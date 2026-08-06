"""AWS Lambda entry points.

control_handler — serves the FastAPI app through API Gateway (via Mangum).
worker_handler  — executes a run in chunks triggered by EventBridge; each
                  invocation steps up to CHUNK_STEPS, checkpoints run metadata
                  to DynamoDB, and re-emits RunChunkRequested until done.

The chunked model keeps each invocation well under the Lambda timeout; because
the engine is deterministic, a chunk restart replays from step 0 cheaply (the
engine runs thousands of steps per second) — steps already persisted are simply
overwritten idempotently.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

CHUNK_STEPS = int(os.environ.get("TEZCAT_CHUNK_STEPS", "5000"))


def control_handler(event: Dict[str, Any], context: Any):
    from mangum import Mangum

    from tezcat.api.app import app

    return Mangum(app)(event, context)


def worker_handler(event: Dict[str, Any], context: Any):
    import boto3

    from tezcat.core.config import ExperimentConfig
    from tezcat.engine.ecology import EcologyEngine
    from tezcat.persistence.aws import AwsStore

    detail = event.get("detail", {})
    run_id = detail["run_id"]
    store = AwsStore()
    run = store.load_run(run_id)
    exp = store.load_experiment(run["experiment_id"])
    config = ExperimentConfig.model_validate(exp["config"])

    engine = EcologyEngine(run_id, config, run["seed"])
    target = min(config.total_steps, detail.get("resume_from", 0) + CHUNK_STEPS)
    while not engine.done and engine.step_num < target:
        engine.step()

    run["steps_completed"] = engine.step_num
    run["current_regime"] = engine.regimes.current.value

    if engine.done:
        engine.check_invariants()
        prefix = run["s3_prefix"]
        store.save_artifact(prefix, "report/report.json", engine.build_report())
        store.save_artifact(prefix, "trades/trades.json", engine.trades)
        store.save_artifact(prefix, "snapshots/snapshots.json", engine.snapshots)
        store.save_artifact(prefix, "metrics/step_metrics.json", engine.metrics.step_metrics)
        store.save_artifact(prefix, "shocks/shock_events.json",
                            [e.to_dict() for e in engine.shocks.events])
        store.save_artifact(prefix, "regimes/regime_events.json",
                            [e.to_dict() for e in engine.regimes.events])
        run["status"] = "completed"
    else:
        boto3.client("events").put_events(Entries=[{
            "Source": "tezcat",
            "DetailType": "RunChunkRequested",
            "Detail": json.dumps({"run_id": run_id, "resume_from": engine.step_num}),
        }])

    store.save_run(run)
    return {"run_id": run_id, "steps_completed": engine.step_num, "done": engine.done}
