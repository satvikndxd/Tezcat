"""AWS persistence: DynamoDB for metadata, S3 for artifacts.

Drop-in replacement for LocalStore, selected via TEZCAT_STORE=aws.
Required environment:
  TEZCAT_S3_BUCKET          artifact bucket
  TEZCAT_TABLE_EXPERIMENTS  DynamoDB experiments table (pk: experiment_id)
  TEZCAT_TABLE_RUNS         DynamoDB runs table (pk: run_id)
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any, Dict, List, Optional


def _dynamo_safe(obj: Any) -> Any:
    """DynamoDB rejects floats; round-trip through Decimal."""
    return json.loads(json.dumps(obj, default=str), parse_float=Decimal)


def _plain(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


class AwsStore:
    def __init__(self) -> None:
        import boto3  # imported lazily so the core engine has no AWS dependency

        self._s3 = boto3.client("s3")
        ddb = boto3.resource("dynamodb")
        self.bucket = os.environ["TEZCAT_S3_BUCKET"]
        self._experiments = ddb.Table(os.environ.get("TEZCAT_TABLE_EXPERIMENTS", "TezcatExperiments"))
        self._runs = ddb.Table(os.environ.get("TEZCAT_TABLE_RUNS", "TezcatRuns"))

    # -- experiments ---------------------------------------------------
    def save_experiment(self, exp: Dict[str, Any]) -> None:
        item = dict(exp)
        config = item.pop("config", None)
        self._experiments.put_item(Item=_dynamo_safe(item))
        if config is not None:
            self._put_json(f"experiments/{exp['experiment_id']}/config/config.json",
                           {"experiment_id": exp["experiment_id"], "config": config})

    def load_experiment(self, experiment_id: str) -> Optional[Dict[str, Any]]:
        resp = self._experiments.get_item(Key={"experiment_id": experiment_id})
        item = resp.get("Item")
        if item is None:
            return None
        exp = _plain(item)
        cfg = self._get_json(f"experiments/{experiment_id}/config/config.json")
        if cfg:
            exp["config"] = cfg["config"]
        return exp

    def list_experiments(self) -> List[Dict[str, Any]]:
        items = [_plain(i) for i in self._experiments.scan().get("Items", [])]
        items.sort(key=lambda d: d.get("created_at", ""), reverse=True)
        return items

    # -- runs ----------------------------------------------------------
    def save_run(self, run: Dict[str, Any]) -> None:
        self._runs.put_item(Item=_dynamo_safe(run))

    def load_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        item = self._runs.get_item(Key={"run_id": run_id}).get("Item")
        return _plain(item) if item else None

    def list_runs(self) -> List[Dict[str, Any]]:
        items = [_plain(i) for i in self._runs.scan().get("Items", [])]
        items.sort(key=lambda d: d.get("started_at") or "", reverse=True)
        return items

    # -- artifacts -----------------------------------------------------
    def save_artifact(self, s3_prefix: str, name: str, obj: Any) -> str:
        key = f"{s3_prefix}/{name}"
        self._put_json(key, obj)
        return f"s3://{self.bucket}/{key}"

    def load_artifact(self, s3_prefix: str, name: str) -> Optional[Any]:
        return self._get_json(f"{s3_prefix}/{name}")

    def presigned_url(self, s3_prefix: str, name: str, expires: int = 3600) -> str:
        return self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": f"{s3_prefix}/{name}"},
            ExpiresIn=expires,
        )

    # -- helpers -------------------------------------------------------
    def _put_json(self, key: str, obj: Any) -> None:
        self._s3.put_object(Bucket=self.bucket, Key=key,
                            Body=json.dumps(obj, default=str).encode(),
                            ContentType="application/json")

    def _get_json(self, key: str) -> Optional[Any]:
        try:
            resp = self._s3.get_object(Bucket=self.bucket, Key=key)
        except self._s3.exceptions.NoSuchKey:
            return None
        return json.loads(resp["Body"].read())


def get_store():
    """Store factory honoring TEZCAT_STORE (local|aws)."""
    if os.environ.get("TEZCAT_STORE", "local").lower() == "aws":
        return AwsStore()
    from tezcat.persistence.local import LocalStore

    return LocalStore(os.environ.get("TEZCAT_DATA_DIR", "data"))
