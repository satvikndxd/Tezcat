"""Local JSON persistence mirroring the S3/DynamoDB layout.

The artifact directory structure matches the planned S3 bucket layout so the
AWS store (tezcat/persistence/aws.py) is a drop-in replacement.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional


class LocalStore:
    def __init__(self, root: str = "data"):
        self.root = Path(root)
        (self.root / "experiments").mkdir(parents=True, exist_ok=True)
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
        (self.root / "artifacts").mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # -- generic -------------------------------------------------------
    def _write(self, path: Path, obj: Any) -> None:
        # Writer-unique tmp name: concurrent same-key writes (e.g. the run
        # manager's periodic persistence racing an API save) must not share
        # a tmp file — see the F11 fault-injection tests.
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(
            f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        with open(tmp, "w") as f:
            json.dump(obj, f, indent=1, default=str)
        os.replace(tmp, path)

    def _read(self, path: Path) -> Optional[Any]:
        if not path.exists():
            return None
        with open(path) as f:
            return json.load(f)

    # -- experiments ---------------------------------------------------
    def save_experiment(self, exp: Dict[str, Any]) -> None:
        with self._lock:
            self._write(self.root / "experiments" / f"{exp['experiment_id']}.json", exp)

    def load_experiment(self, experiment_id: str) -> Optional[Dict[str, Any]]:
        return self._read(self.root / "experiments" / f"{experiment_id}.json")

    def list_experiments(self) -> List[Dict[str, Any]]:
        out = []
        for p in sorted((self.root / "experiments").glob("*.json")):
            d = self._read(p)
            if d:
                d.pop("config", None)  # keep listings light
                out.append(d)
        out.sort(key=lambda d: d.get("created_at", ""), reverse=True)
        return out

    # -- runs ----------------------------------------------------------
    def save_run(self, run: Dict[str, Any]) -> None:
        with self._lock:
            self._write(self.root / "runs" / f"{run['run_id']}.json", run)

    def load_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._read(self.root / "runs" / f"{run_id}.json")

    def list_runs(self) -> List[Dict[str, Any]]:
        out = [d for p in (self.root / "runs").glob("*.json") if (d := self._read(p))]
        out.sort(key=lambda d: d.get("started_at") or "", reverse=True)
        return out

    # -- artifacts (S3-layout mirror) ----------------------------------
    def save_artifact(self, s3_prefix: str, name: str, obj: Any) -> str:
        path = self.root / "artifacts" / s3_prefix / name
        with self._lock:
            self._write(path, obj)
        return str(path)

    def load_artifact(self, s3_prefix: str, name: str) -> Optional[Any]:
        return self._read(self.root / "artifacts" / s3_prefix / name)
