"""Observed event → synthetic experiment bridge (Phase S3-D).

The workflow this module implements::

    External observation → EventSignature → mechanism proposal
        → ordinary ExperimentVersion → BatchRunner → comparison
        → research manifest (experiment hash + dataset hash)

Architectural rule (mandatory): external data enters **experiment design
and calibration targets only** — never the simulation kernel. The synthetic
market remains synthetic; nothing here feeds an observed probability into
the price engine.

Language rules (mandatory): mechanism proposals are **candidates**, worded
as hypotheses to test. Comparison output says "consistent with" /
"reproduces" / "does not reproduce" — never that a mechanism *caused* the
real-world move. A synthetic run is a *synthetic analogue*, and the
comparison layer only uses **dimensionless episode descriptors** computed
identically on both sides (see :func:`dynamics_features`) — comparing a
bounded probability against a synthetic price level directly would be a
category error, so it is not offered.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Dict, List, Optional, Sequence

from tezcat.analysis.stats import describe
from tezcat.analysis.stylized_facts import acf
from tezcat.core.config import (
    AgentGroupConfig, AgentType, ExperimentConfig, MarketConfig,
    ShockConfig, ShockTrigger, ShockType, canonical_json,
)
from tezcat.experiments.schema import Arm, DesignSpec, ExperimentVersion
from tezcat.external.datasets import DatasetManifest
from tezcat.external.signature import EventSignature

BRIDGE_VERSION = 1


class BridgeError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Mechanism templates (explicit — no LLM invention)
# ---------------------------------------------------------------------------
# Agent order in bridge_base_config is FIXED and load-bearing for the
# override paths below: 0 noise, 1 retail, 2 momentum, 3 mean-reversion,
# 4 market maker. Shock index 0 is the information shock.
MECHANISMS: Dict[str, Dict[str, Any]] = {
    "information_shock": {
        "description": "Exogenous information arrival modeled as a whale "
                       "order program at the event step.",
        "overrides": {"shocks.0.magnitude": 900.0},
        "signature_cue": "large delta_probability or max_jump",
    },
    "herding": {
        "description": "Retail imitation strength amplifies moves once "
                       "they start.",
        "overrides": {"agents.1.params.herding": 0.9},
        "signature_cue": "rising activity (volume_change > 1.5) during the move",
    },
    "momentum_amplification": {
        "description": "Trend followers activate on smaller price changes, "
                       "adding flow in the direction of the move.",
        "overrides": {"agents.2.params.threshold": 0.0008},
        "signature_cue": "volatility clustering in the observed increments",
    },
    "mm_withdrawal": {
        "description": "Market makers pull quotes at the event, thinning "
                       "the book while flow arrives.",
        "overrides": {"shocks.1.enabled": True},
        "signature_cue": "spread expansion (spread_change > 0)",
    },
    "thin_liquidity": {
        "description": "Structurally smaller market-maker quotes: the same "
                       "flow moves the price further.",
        "overrides": {"agents.4.params.quote_size": 6},
        "signature_cue": "depth loss (depth_change < 0)",
    },
}


def bridge_base_config(*, t0_step: int = 400, total_steps: int = 1200,
                       shock_side: str = "sell") -> ExperimentConfig:
    """Base synthetic ecology for observed-event experiments.

    Agent group order is part of this module's contract (see MECHANISMS).
    The information shock at ``t0_step`` is the synthetic analogue of the
    observed event anchor; the MM-withdrawal shock exists but is disabled
    in the base config, so `shocks.1.enabled` can switch the mechanism on.
    """
    if not 0 < t0_step < total_steps:
        raise BridgeError(f"t0_step {t0_step} must lie inside (0, {total_steps})")
    return ExperimentConfig(
        market=MarketConfig(),
        total_steps=total_steps,
        step_delay_ms=0,
        agents=[
            AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=18,
                             cash=10_000, inventory=100,
                             trading_frequency=0.5, risk_tolerance=0.5),
            AgentGroupConfig(agent_type=AgentType.RETAIL_TRADER, count=12,
                             cash=8_000, inventory=90, trading_frequency=0.45,
                             risk_tolerance=0.5, params={"herding": 0.5}),
            AgentGroupConfig(agent_type=AgentType.MOMENTUM_TRADER, count=6,
                             cash=15_000, inventory=120,
                             trading_frequency=0.5, risk_tolerance=0.55,
                             params={"threshold": 0.0015}),
            AgentGroupConfig(agent_type=AgentType.MEAN_REVERSION_TRADER,
                             count=6, cash=15_000, inventory=120,
                             trading_frequency=0.5, risk_tolerance=0.55,
                             params={"band": 0.01}),
            AgentGroupConfig(agent_type=AgentType.MARKET_MAKER, count=4,
                             cash=50_000, inventory=500, trading_frequency=1.0,
                             risk_tolerance=0.7,
                             params={"half_spread": 2.0, "quote_size": 12}),
        ],
        shocks=[
            ShockConfig(shock_id="info_shock",
                        shock_type=ShockType.WHALE_ORDER,
                        trigger=ShockTrigger(kind="scheduled", step=t0_step),
                        side=shock_side, magnitude=600.0, duration=8,
                        description="Synthetic analogue of the observed "
                                    "information arrival"),
            ShockConfig(shock_id="mm_withdrawal",
                        shock_type=ShockType.MM_WITHDRAWAL,
                        trigger=ShockTrigger(kind="scheduled", step=t0_step),
                        magnitude=0.8, duration=60, enabled=False,
                        description="Optional mechanism: quote withdrawal "
                                    "at the event"),
        ],
    )


# ---------------------------------------------------------------------------
# Mechanism proposal (rule-based, hypotheses only)
# ---------------------------------------------------------------------------
def propose_mechanisms(signature: EventSignature) -> Dict[str, Any]:
    """Structured experiment proposal from an observed signature.

    Every candidate is worded as a hypothesis; the proposal never asserts
    that a mechanism produced the observed move.
    """
    candidates: List[Dict[str, str]] = []

    def add(name: str, why: str) -> None:
        candidates.append({"mechanism": name,
                           "rationale": f"observed {why} — consistent with "
                                        f"{name.replace('_', ' ')} as a "
                                        "candidate mechanism (hypothesis, "
                                        "not an explanation)"})

    dp = signature.delta_probability
    feats = signature.features or {}
    if dp is not None and abs(dp) >= 0.05:
        add("information_shock", f"probability move of {dp:+.2f}")
    if (feats.get("max_jump") or 0) >= (feats.get("jump_threshold") or 0.05):
        if not any(c["mechanism"] == "information_shock" for c in candidates):
            add("information_shock", f"max jump {feats['max_jump']:.2f}")
    if signature.volume_change is not None and signature.volume_change > 1.5:
        add("herding", f"activity ratio {signature.volume_change:.1f}× during "
                       "the move")
    if (feats.get("volatility_clustering") or 0) > 0.1:
        add("momentum_amplification",
            f"volatility clustering {feats['volatility_clustering']:.2f}")
    if signature.spread_change is not None and signature.spread_change > 0:
        add("mm_withdrawal", f"spread expansion {signature.spread_change:+.3f}")
    if signature.depth_change is not None and signature.depth_change < -0.2:
        add("thin_liquidity", f"depth change {signature.depth_change:+.0%}")
    if not candidates:
        add("information_shock", "no strong cues; the default single-shock "
                                 "baseline is the minimal starting hypothesis")

    return {
        "bridge_version": BRIDGE_VERSION,
        "signature_hash": signature.signature_hash(),
        "dataset_id": signature.dataset_id,
        "market_id": signature.market_id,
        "observed": {
            "delta_probability": signature.delta_probability,
            "peak_probability": signature.peak_probability,
            "spread_change": signature.spread_change,
            "volume_change": signature.volume_change,
            "depth_change": signature.depth_change,
        },
        "candidates": candidates,
        "suggested_design": "ab: control = information shock alone; one "
                            "treatment per additional candidate mechanism",
        "disclaimer": "Candidate mechanisms are hypotheses for controlled "
                      "synthetic experiments. None is claimed to explain "
                      "the real-world observation.",
    }


# ---------------------------------------------------------------------------
# Signature → ExperimentVersion (the ordinary machinery, nothing bespoke)
# ---------------------------------------------------------------------------
def experiment_from_signature(signature: EventSignature, *,
                              mechanisms: Sequence[str],
                              name: str,
                              experiment_id: str = "exp_external_event",
                              replications: int = 5,
                              t0_step: int = 400,
                              total_steps: int = 1200,
                              root_seed: Optional[int] = None,
                              question: Optional[str] = None,
                              hypothesis: Optional[str] = None
                              ) -> ExperimentVersion:
    """Compile an observed signature into a normal ``ExperimentVersion``.

    Control arm: information shock alone (the minimal event analogue).
    One treatment arm per requested mechanism. The result is an ordinary
    research object — same registry, batch runner, analysis, report, and
    reproduction machinery as every other Tezcat experiment. No separate
    research-identity system.
    """
    unknown = [m for m in mechanisms if m not in MECHANISMS]
    if unknown:
        raise BridgeError(f"unknown mechanisms {unknown}; "
                          f"available: {sorted(MECHANISMS)}")
    mechs = [m for m in mechanisms if m != "information_shock"]
    if not mechs:
        raise BridgeError(
            "select at least one mechanism besides the information-shock "
            "control — an experiment needs a comparison")

    dp = signature.delta_probability
    side = "buy" if (dp or 0) >= 0 else "sell"
    config = bridge_base_config(t0_step=t0_step, total_steps=total_steps,
                                shock_side=side)

    treatments = [Arm(name=m, overrides=dict(MECHANISMS[m]["overrides"]))
                  for m in mechs]
    design = DesignSpec(
        design_type="ab",
        question=question or (
            f"Which candidate mechanisms produce post-shock dynamics "
            f"consistent with the episode observed in dataset "
            f"{signature.dataset_id} (market {signature.market_id}, "
            f"Δp={dp:+.2f})?" if dp is not None else
            f"Which candidate mechanisms produce post-shock dynamics "
            f"consistent with the episode observed in dataset "
            f"{signature.dataset_id}?"),
        hypothesis=hypothesis or (
            "At least one candidate mechanism, added to a pure information "
            "shock, shifts the synthetic episode descriptors toward the "
            "observed signature. (A null result — no mechanism helps — is "
            "a valid outcome.)"),
        independent_variables=[f"mechanism:{m}" for m in mechs],
        dependent_variables=["total_return", "realized_volatility",
                             "max_drawdown", "average_spread", "total_volume"],
        primary_metric="total_return",
        control=Arm(name="information_shock_only"),
        treatments=treatments,
        replications=replications,
    )
    return ExperimentVersion(experiment_id=experiment_id, name=name,
                             config=config, design=design,
                             root_seed=root_seed)


# ---------------------------------------------------------------------------
# Research manifest: experiment identity × dataset identity
# ---------------------------------------------------------------------------
def research_manifest(version: ExperimentVersion,
                      dataset: DatasetManifest,
                      signature: EventSignature) -> Dict[str, Any]:
    """The reproducibility contract for external-data research.

    Reproducing this experiment means re-executing the research hash
    *against this exact dataset version* — both identities are recorded,
    and the combination is itself hashed.
    """
    payload = {
        "experiment_hash": version.research_hash,
        "dataset_hash": dataset.dataset_hash,
        "signature_hash": signature.signature_hash(),
    }
    return {
        **payload,
        "research_identity": hashlib.sha256(
            canonical_json(payload).encode()).hexdigest(),
        "bridge_version": BRIDGE_VERSION,
        "version_id": version.version_id,
        "dataset_id": dataset.dataset_id,
        "provider": dataset.provider,
        "event_id": dataset.event_id,
        "market_ids": dataset.market_ids,
        "time_window": dataset.time_window,
        "adapter_version": dataset.adapter_version,
        "external_schema_version": dataset.external_schema_version,
        "signature_window": {"t0_index": signature.t0_index,
                             "pre": signature.pre_window,
                             "post": signature.post_window},
        "source_kind": dataset.source_kind,
        "license": dataset.license,
    }


# ---------------------------------------------------------------------------
# Dimensionless episode descriptors (the only comparison currency)
# ---------------------------------------------------------------------------
def dynamics_features(values: Sequence[float], t0_index: int
                      ) -> Dict[str, Optional[float]]:
    """Scale-free descriptors of an episode, identical for both domains.

    Works on any ordered series (observed probabilities OR synthetic
    prices) because every feature is dimensionless: increments are
    standardized by their own standard deviation, times are fractions of
    the post-window, and displacements are ratios. This is what makes an
    observed-vs-synthetic comparison mathematically justified; comparing
    raw levels across domains would not be.
    """
    n = len(values)
    if not 0 < t0_index < n - 1:
        raise BridgeError(f"t0_index {t0_index} must be interior to the "
                          f"series (n={n})")
    incr = [b - a for a, b in zip(values, values[1:])]
    std = (sum((v - sum(incr) / len(incr)) ** 2 for v in incr)
           / max(len(incr) - 1, 1)) ** 0.5
    feats: Dict[str, Optional[float]] = {}

    pre_level = values[t0_index]
    post = values[t0_index:]
    end = values[-1]
    move = end - pre_level
    disp = [(v - pre_level) for v in post]
    direction = 1.0 if move >= 0 else -1.0
    peak_idx = max(range(len(disp)), key=lambda i: direction * disp[i])
    peak = disp[peak_idx]

    feats["move_direction"] = direction
    feats["time_to_peak_fraction"] = peak_idx / max(len(post) - 1, 1)
    feats["overshoot"] = ((peak - move) / abs(peak)
                          if abs(peak) > 1e-12 else None)
    if std > 0:
        z = [abs(v) / std for v in incr]
        feats["jump_frequency_2sigma"] = sum(v > 2.0 for v in z) / len(z)
        clustering = [acf([abs(v) for v in incr], lag) for lag in (1, 2, 5)]
        clustering = [c for c in clustering if c is not None]
        feats["volatility_clustering"] = (sum(clustering) / len(clustering)
                                          if clustering else None)
        pre_incr = incr[:t0_index]
        post_incr = incr[t0_index:]

        def _std(xs: Sequence[float]) -> Optional[float]:
            if len(xs) < 3:
                return None
            m = sum(xs) / len(xs)
            return (sum((v - m) ** 2 for v in xs) / (len(xs) - 1)) ** 0.5

        s_pre, s_post = _std(pre_incr), _std(post_incr)
        feats["post_pre_vol_ratio"] = (s_post / s_pre
                                       if s_pre and s_post is not None else None)
    else:
        feats["jump_frequency_2sigma"] = None
        feats["volatility_clustering"] = None
        feats["post_pre_vol_ratio"] = None
    return feats


def compare_episode(observed_values: Sequence[float], observed_t0: int,
                    synthetic_paths: Sequence[Sequence[float]],
                    synthetic_t0: int) -> Dict[str, Any]:
    """OBSERVED episode vs SYNTHETIC ensemble on dimensionless descriptors.

    The synthetic side is an ensemble (>= 10 paths — a band from fewer is
    decoration, same F9 rule). Output rows carry the q05–q95 band and an
    inside/outside verdict per feature. A partial match is the expected
    outcome; the table exists to say which dynamics the mechanism
    reproduces and which it does not.
    """
    if len(synthetic_paths) < 10:
        raise BridgeError(f"synthetic ensemble has {len(synthetic_paths)} "
                          "paths; >= 10 replications are required")
    observed = dynamics_features(observed_values, observed_t0)
    ensemble = [dynamics_features(p, synthetic_t0) for p in synthetic_paths]

    rows: Dict[str, Any] = {}
    inside = total = 0
    for name, obs_val in observed.items():
        if obs_val is None:
            continue
        vals = [e.get(name) for e in ensemble]
        vals = [v for v in vals if v is not None]
        if len(vals) < 10:
            rows[name] = {"observed": obs_val,
                          "warning": "insufficient ensemble values"}
            continue
        d = describe(vals)
        in_band = d["q05"] <= obs_val <= d["q95"]
        rows[name] = {"observed": obs_val, "ensemble_mean": d["mean"],
                      "band_q05": d["q05"], "band_q95": d["q95"],
                      "inside_band": in_band,
                      "z_distance": ((obs_val - d["mean"]) / d["std"])
                                    if d["std"] > 0 else None}
        total += 1
        inside += in_band
    return {
        "labels": {"observed": "OBSERVED external market data",
                   "synthetic": "SYNTHETIC Tezcat ensemble"},
        "comparison_currency": "dimensionless episode descriptors "
                               "(standardized increments; see "
                               "dynamics_features)",
        "features": rows,
        "n_features": total,
        "n_inside_band": inside,
        "coverage": inside / total if total else None,
        "n_ensemble": len(synthetic_paths),
        "interpretation": "coverage reports which observed dynamics the "
                          "synthetic ensemble reproduces under controlled "
                          "assumptions — no claim about the real-world "
                          "cause is made or implied",
    }
