"""Experiment and Run entities."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from tezcat import __version__
from tezcat.core.config import SCHEMA_VERSION, ExperimentConfig, config_hash


class ConfigHashMismatch(ValueError):
    """A stored config hash does not match the config's current content."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _short_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@dataclass
class Experiment:
    experiment_id: str
    name: str
    hypothesis: Optional[str]
    preset_id: Optional[str]
    config: ExperimentConfig
    config_hash: str
    version: str
    created_at: str
    status: str = "ready"  # draft | ready | running | completed | failed

    @classmethod
    def create(cls, name: str, config: ExperimentConfig, hypothesis: Optional[str] = None,
               preset_id: Optional[str] = None) -> "Experiment":
        return cls(
            experiment_id=_short_id("exp"),
            name=name,
            hypothesis=hypothesis,
            preset_id=preset_id,
            config=config,
            config_hash=config_hash(config),
            version=__version__,
            created_at=_now(),
        )

    def verify_hash(self) -> None:
        """Fail loudly if the stored hash no longer matches the config.

        The config models are frozen, so a mismatch means either external
        mutation of nested containers, a hand-edited stored record, or a
        schema-version change since the experiment was created. In every case
        the experiment must not be executed under its stored identity.
        """
        current = config_hash(self.config)
        if current != self.config_hash:
            raise ConfigHashMismatch(
                f"experiment {self.experiment_id}: stored config_hash "
                f"{self.config_hash[:12]}… does not match current config "
                f"{current[:12]}… (schema_version={SCHEMA_VERSION}); "
                "re-create the experiment instead of mutating it"
            )

    def to_dict(self, include_config: bool = True) -> Dict[str, Any]:
        d = {
            "experiment_id": self.experiment_id,
            "name": self.name,
            "hypothesis": self.hypothesis,
            "preset_id": self.preset_id,
            "config_hash": self.config_hash,
            "schema_version": SCHEMA_VERSION,
            "version": self.version,
            "created_at": self.created_at,
            "status": self.status,
        }
        if include_config:
            d["config"] = self.config.model_dump(mode="json")
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Experiment":
        return cls(
            experiment_id=d["experiment_id"], name=d["name"], hypothesis=d.get("hypothesis"),
            preset_id=d.get("preset_id"), config=ExperimentConfig.model_validate(d["config"]),
            config_hash=d["config_hash"], version=d.get("version", "?"),
            created_at=d["created_at"], status=d.get("status", "ready"),
        )


@dataclass
class Run:
    run_id: str
    experiment_id: str
    experiment_name: str
    seed: int
    config_hash: str
    total_steps: int
    status: str = "pending"  # pending | running | paused | completed | failed | cancelled
    steps_completed: int = 0
    current_regime: str = "stable"
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    s3_prefix: Optional[str] = None
    step_delay_ms: int = 15
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, experiment: Experiment, seed: int, step_delay_ms: int) -> "Run":
        run_id = _short_id("run")
        return cls(
            run_id=run_id,
            experiment_id=experiment.experiment_id,
            experiment_name=experiment.name,
            seed=seed,
            config_hash=experiment.config_hash,
            total_steps=experiment.config.total_steps,
            step_delay_ms=step_delay_ms,
            s3_prefix=f"experiments/{experiment.experiment_id}/runs/{run_id}",
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "experiment_id": self.experiment_id,
            "experiment_name": self.experiment_name,
            "seed": self.seed,
            "config_hash": self.config_hash,
            "status": self.status,
            "steps_completed": self.steps_completed,
            "current_step": self.steps_completed,
            "total_steps": self.total_steps,
            "current_regime": self.current_regime,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "s3_prefix": self.s3_prefix,
            "step_delay_ms": self.step_delay_ms,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Run":
        return cls(
            run_id=d["run_id"], experiment_id=d["experiment_id"],
            experiment_name=d.get("experiment_name", "?"), seed=d["seed"],
            config_hash=d["config_hash"], total_steps=d["total_steps"],
            status=d.get("status", "pending"), steps_completed=d.get("steps_completed", 0),
            current_regime=d.get("current_regime", "stable"), started_at=d.get("started_at"),
            completed_at=d.get("completed_at"), error=d.get("error"),
            s3_prefix=d.get("s3_prefix"), step_delay_ms=d.get("step_delay_ms", 15),
        )
