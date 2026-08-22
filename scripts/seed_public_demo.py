"""Seed the public demo's existing margin-spiral research artifacts.

This script only composes the existing documented research interfaces. It does
not alter the engine, experiment schema, batch runner, or reproducibility code.
It is safe to run repeatedly: registration and batch execution are resumable.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tezcat.analysis import analyze
from tezcat.experiments.batch import BatchRunner
from tezcat.experiments.registry import Registry, RegistryError
from tezcat.experiments.schema import DesignSpec, ExperimentVersion
from tezcat.core.config import ExperimentConfig


SPEC_PATH = ROOT / "examples" / "margin_spiral_ab.json"


def load_version() -> ExperimentVersion:
    with SPEC_PATH.open() as handle:
        spec = json.load(handle)
    return ExperimentVersion(
        experiment_id=spec["experiment_id"],
        name=spec["name"],
        config=ExperimentConfig.model_validate(spec["config"]),
        design=DesignSpec.model_validate(spec["design"]),
        root_seed=spec.get("root_seed"),
    )


def main() -> None:
    data_dir = os.environ.get("TEZCAT_DATA_DIR", "/data")
    registry = Registry(data_dir)
    version = load_version()

    try:
        registry.get(version.version_id)
    except RegistryError:
        registry.register(version)

    batch = BatchRunner(registry).run(version.version_id, max_runs=60)
    if batch["status"] != "completed":
        raise RuntimeError(
            f"public demo batch is incomplete: {batch['completed']}/{batch['planned']}"
        )

    analyze(registry, version.version_id, seed=0, allow_partial=False)
    print(
        f"seeded {version.version_id} ({batch['completed']} runs) in {data_dir}",
        flush=True,
    )


if __name__ == "__main__":
    main()
