"""First vertical slice (Phase S5-D):

    Forecast → Portfolio → Tezcat Worlds → Nautilus → Risk → Report

One function, :func:`run_full_slice`, executes the whole chain and
registers every stage as a linked node in the artifact graph:

    market_world (history) ─► external_dataset ─► forecast ─► portfolio
                                                                 │
    market_world (stress cell) ────────────────────────────────► backtest
                                                    (one per stress cell)
                                                                 │
                                                                 ▼
                                                            risk_report
                                                                 │
                                                                 ▼
                                                          research_report

Semantics of the seam (declared, §7/§13):

* The forecast is fitted on the *history world's* mid-price series (a
  synthetic data-generating process — no real-market claim).
* The portfolio converts the forecast distribution into a risky weight
  with explicit constraints and costs (gross vs net separated).
* The weight sizes the position of the fixed execution strategy
  (``trade_size = weight × capital / last price``) — the forecast never
  becomes an order; it informs *research sizing* of a backtest.
* Risk aggregates per-world execution results into the **edge-decay
  pipeline** (§33): every row is measured from the chain's own
  artifacts, never fabricated.

Reproduction (:func:`reproduce_slice`) re-runs the entire chain from the
stored slice configuration and compares every artifact hash; an
environment mismatch (nautilus/skfolio versions) fails loudly.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from tezcat.core.config import canonical_json
from tezcat.experiments.registry import Registry
from tezcat.plane.artifacts import ArtifactGraph, PlaneError, ResearchArtifact
from tezcat.plane.forecasting import get_model, make_forecast_artifact
from tezcat.plane.portfolio import (
    CostModel, PortfolioConstraints, PortfolioView, get_optimizer,
    make_portfolio_artifact,
)

SLICE_VERSION = 1


def _world_artifact(graph: ArtifactGraph, world) -> ResearchArtifact:
    """Wrap an S4 Market World as a graph node (identity carried verbatim)."""
    return graph.register(
        "market_world",
        {"world_id": world.world_id, "world_hash": world.world_hash,
         "research_hash": world.manifest["research_hash"],
         "version_id": world.manifest["version_id"],
         "cell": world.manifest["cell"],
         "replication": world.manifest["replication"],
         "seed": world.manifest["seed"],
         "fingerprint": world.manifest["fingerprint"],
         "n_quotes": world.manifest["n_quotes"],
         "n_trades": world.manifest["n_trades"]},
        config={"version_id": world.manifest["version_id"],
                "cell": world.manifest["cell"],
                "replication": world.manifest["replication"]},
        external_identity=world.world_hash)


def run_full_slice(data_dir: str, *, history_ref: str,
                   history_cell: Optional[str] = None,
                   stress_cells: Optional[List[str]] = None,
                   model_id: str = "bootstrap_reference",
                   model_params: Optional[Dict[str, Any]] = None,
                   horizon: int = 60, n_paths: int = 200, seed: int = 7,
                   optimizer_id: str = "reference_cvar",
                   optimizer_params: Optional[Dict[str, Any]] = None,
                   constraints: Optional[PortfolioConstraints] = None,
                   costs: Optional[CostModel] = None,
                   strategy_id: str = "buy_hold",
                   capital: float = 1_000_000.0) -> Dict[str, Any]:
    """Execute the full chain; returns every artifact id plus the report."""
    from tezcat.lab.backtest import run_backtest
    from tezcat.lab.results import LabResultStore
    from tezcat.worlds import WorldStore, build_world

    graph = ArtifactGraph(data_dir)
    registry = Registry(data_dir)
    world_store = WorldStore(data_dir)
    result_store = LabResultStore(data_dir)
    constraints = constraints or PortfolioConstraints(max_weight=0.8)
    costs = costs or CostModel()

    slice_config = {
        "slice_version": SLICE_VERSION,
        "history_ref": history_ref, "history_cell": history_cell,
        "stress_cells": stress_cells,
        "model_id": model_id, "model_params": model_params or {},
        "horizon": horizon, "n_paths": n_paths, "seed": seed,
        "optimizer_id": optimizer_id,
        "optimizer_params": optimizer_params or {},
        "constraints": constraints.model_dump(mode="json"),
        "costs": costs.model_dump(mode="json"),
        "strategy_id": strategy_id, "capital": capital,
    }

    # 1. history world → dataset ---------------------------------------
    history_world = build_world(registry, history_ref, cell=history_cell)
    world_store.save(history_world)
    history_art = _world_artifact(graph, history_world)
    mids = [(q["bid"] + q["ask"]) / 2 for q in history_world.quotes]
    instrument = history_world.manifest["instrument_symbol"]
    dataset_art = graph.register(
        "external_dataset",
        {"source": "tezcat_market_world", "world_id": history_world.world_id,
         "world_hash": history_world.world_hash, "instrument": instrument,
         "prices": [round(m, 6) for m in mids],
         "sampling": "world quote mids, one per step",
         "label": "SYNTHETIC series from a Tezcat world — not real market "
                  "data"},
        config={"source": "tezcat_market_world",
                "world_id": history_world.world_id},
        parents=[history_art], external_identity=history_world.world_hash)

    # 2. forecast -------------------------------------------------------
    model = get_model(model_id, model_params)
    forecast_art = make_forecast_artifact(
        graph, dataset_artifact=dataset_art, prices=mids,
        instrument=instrument, model=model, horizon=horizon,
        n_paths=n_paths, seed=seed)
    forecast_payload = graph.payload(forecast_art.artifact_id)

    # 3. portfolio ------------------------------------------------------
    view = PortfolioView.from_forecast(forecast_payload,
                                       forecast_art.artifact_hash)
    optimizer = get_optimizer(optimizer_id, optimizer_params)
    portfolio_art = make_portfolio_artifact(
        graph, forecast_artifact=forecast_art, view=view,
        optimizer=optimizer, constraints=constraints, costs=costs)
    portfolio_payload = graph.payload(portfolio_art.artifact_id)
    weight = portfolio_payload["risky_weight"]

    # 4. stress worlds + execution --------------------------------------
    version = registry.load(
        __import__("tezcat.experiments.reproduce",
                   fromlist=["resolve_reference"])
        .resolve_reference(registry, history_ref))
    cells = stress_cells or [c["cell"] for c in version.cell_configs]
    last_price = mids[-1]
    trade_size = max(1, int(weight * capital / last_price)) if weight > 0 else 0
    backtest_arts: List[ResearchArtifact] = []
    per_world: List[Dict[str, Any]] = []
    for cell in cells:
        world = build_world(registry, history_ref, cell=cell)
        world_store.save(world)
        world_art = _world_artifact(graph, world)
        if trade_size == 0:
            # explicit zero-allocation: no backtest is fabricated
            per_world.append({"cell": cell, "world_id": world.world_id,
                              "skipped": "portfolio allocated zero weight"})
            continue
        result = run_backtest(world, strategy_id,
                              {"trade_size": trade_size},
                              starting_cash=capital)
        rid = result_store.save(result)
        bt_art = graph.register(
            "backtest",
            {"lab_result_id": rid, "world_id": world.world_id,
             "world_hash": world.world_hash,
             "strategy": result["strategy"], "metrics": result["metrics"],
             "environment": result["environment"]},
            config={"strategy_id": strategy_id, "trade_size": trade_size,
                    "capital": capital, "cell": cell},
            parents=[portfolio_art, world_art], external_identity=rid)
        backtest_arts.append(bt_art)
        m = result["metrics"]
        per_world.append({
            "cell": cell, "world_id": world.world_id,
            "backtest_artifact": bt_art.artifact_id,
            "crash_world": world.manifest["fingerprint"]["crash_detected"],
            "total_return": m["total_return"],
            "max_drawdown": m["max_drawdown"],
            "mean_slippage_vs_mid": m["mean_slippage_vs_mid"],
            "total_slippage_cost": m["total_slippage_cost"],
            "fill_rate": m["fill_rate"],
            "regime_pnl": {k: v["pnl"]
                           for k, v in m["regime_breakdown"].items()},
        })

    # 5. risk: measured edge-decay pipeline -----------------------------
    executed = [w for w in per_world if "total_return" in w]
    returns = sorted(w["total_return"] for w in executed)
    n = len(returns)
    model_edge = forecast_payload["terminal"]["mean_return"]
    edge_decay = {
        "model_edge_expected": model_edge,
        "portfolio_edge_gross": portfolio_payload["gross_expected_return"],
        "portfolio_edge_net_of_declared_costs":
            portfolio_payload["net_expected_return"],
        "execution_realized_median": (returns[n // 2] if n else None),
        "execution_realized_q05": (returns[max(0, int(n * 0.05) - 1)]
                                   if n >= 2 else
                                   (returns[0] if n else None)),
        "execution_realized_worst": (returns[0] if n else None),
        "execution_realized_best": (returns[-1] if n else None),
        "note": "expected rows come from forecast/portfolio artifacts; "
                "realized rows are measured backtest outcomes across "
                "stress worlds — nothing is fabricated, and expected vs "
                "realized are different kinds of quantity",
    }
    risk_art = graph.register(
        "risk_report",
        {"per_world": per_world, "edge_decay": edge_decay,
         "n_worlds": len(per_world), "n_executed": len(executed),
         "risky_weight": weight, "trade_size": trade_size},
        config={"slice_config": slice_config},
        parents=backtest_arts or [portfolio_art])

    # 6. report (from artifacts only) -----------------------------------
    markdown = _render_report(graph, slice_config, history_art, dataset_art,
                              forecast_art, portfolio_art, risk_art)
    report_art = graph.register(
        "research_report",
        {"markdown": markdown,
         "artifact_ids": {
             "history_world": history_art.artifact_id,
             "dataset": dataset_art.artifact_id,
             "forecast": forecast_art.artifact_id,
             "portfolio": portfolio_art.artifact_id,
             "backtests": [a.artifact_id for a in backtest_arts],
             "risk": risk_art.artifact_id,
         }},
        config={"slice_config": slice_config},
        parents=[risk_art])
    return {
        "report_artifact": report_art.artifact_id,
        "risk_artifact": risk_art.artifact_id,
        "forecast_artifact": forecast_art.artifact_id,
        "portfolio_artifact": portfolio_art.artifact_id,
        "backtest_artifacts": [a.artifact_id for a in backtest_arts],
        "risky_weight": weight,
        "edge_decay": edge_decay,
        "markdown": markdown,
        "slice_config": slice_config,
    }


def _render_report(graph: ArtifactGraph, slice_config: Dict[str, Any],
                   history_art, dataset_art, forecast_art, portfolio_art,
                   risk_art) -> str:
    forecast = graph.payload(forecast_art.artifact_id)
    portfolio = graph.payload(portfolio_art.artifact_id)
    risk = graph.payload(risk_art.artifact_id)
    t = forecast["terminal"]
    lines = [
        "# Full-stack research slice: forecast → portfolio → worlds → "
        "execution → risk",
        "",
        "All numbers below are read from registered, hashed artifacts; "
        "none are computed at render time. Every stage links to its "
        "parents in the research graph.",
        "",
        "## Lineage",
        "",
        "| Stage | Artifact | Hash |",
        "| --- | --- | --- |",
    ]
    for name, art in (("history world", history_art), ("dataset", dataset_art),
                      ("forecast", forecast_art), ("portfolio", portfolio_art),
                      ("risk", risk_art)):
        lines.append(f"| {name} | `{art.artifact_id}` | "
                     f"`{art.artifact_hash[:16]}…` |")
    lines += [
        "",
        "## Forecast (probabilistic research observation — not an order)",
        "",
        f"- model: `{forecast['model_id']}` v{forecast['model_version']} — "
        f"{forecast['disclaimer']}",
        f"- horizon {forecast['horizon']} steps · {forecast['n_paths']} paths "
        f"· seed {forecast['seed']}",
        f"- terminal return: mean {t['mean_return']:+.4%}, dispersion "
        f"{t['dispersion']:.4%}, q05 {t['q05']:+.4%}, q95 {t['q95']:+.4%}, "
        f"P(up) {t['prob_up']:.2f}",
        "",
        "## Portfolio decision trace (deterministic)",
        "",
    ]
    for row in portfolio["decision_trace"]:
        lines.append(f"1. **{row['step']}** — {row['detail']}")
    lines += [
        "",
        "## Execution across stress worlds (SYNTHETIC Tezcat ecologies)",
        "",
        "| Cell | World | Crash | Return | Drawdown | Slippage cost | "
        "Fill rate |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for w in risk["per_world"]:
        if "skipped" in w:
            lines.append(f"| {w['cell']} | `{w['world_id']}` | — | "
                         f"*{w['skipped']}* | | | |")
            continue
        lines.append(
            f"| {w['cell']} | `{w['world_id']}` | "
            f"{'yes' if w['crash_world'] else 'no'} | "
            f"{w['total_return']:+.4%} | {w['max_drawdown']:.4%} | "
            f"{w['total_slippage_cost']:.2f} | {w['fill_rate']:.2f} |")
    ed = risk["edge_decay"]
    lines += [
        "",
        "## Edge-decay pipeline (measured)",
        "",
        "| Stage | Value | Kind |",
        "| --- | ---: | --- |",
        f"| model edge (forecast E[r]) | {ed['model_edge_expected']:+.4%} | "
        "expected |",
        f"| portfolio edge, gross | {ed['portfolio_edge_gross']:+.4%} | "
        "expected |",
        f"| portfolio edge, net of declared costs | "
        f"{ed['portfolio_edge_net_of_declared_costs']:+.4%} | expected |",
    ]
    for key, label in (("execution_realized_median", "stress-world median"),
                       ("execution_realized_q05", "stress-world q05"),
                       ("execution_realized_worst", "stress-world worst")):
        value = ed[key]
        lines.append(f"| {label} | "
                     + (f"{value:+.4%}" if value is not None else "—")
                     + " | realized |")
    lines += [
        "",
        f"*{ed['note']}*",
        "",
        "## Limitations",
        "",
        "- The forecast model resamples a synthetic history; no claim about "
        "real markets is made or implied.",
        "- Worlds are synthetic Tezcat ecologies; results describe strategy "
        "behavior under specified model assumptions.",
        "- Expected and realized rows in the edge-decay table are different "
        "kinds of quantity and are labeled as such.",
        "",
        "## Reproduction",
        "",
        "Re-run the slice configuration and compare every artifact hash:",
        "```",
        f"tezcat plane reproduce <report-artifact-id>",
        "```",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Whole-chain reproduction
# ---------------------------------------------------------------------------
def reproduce_slice(data_dir: str, report_artifact_id: str) -> Dict[str, Any]:
    """Re-run the stored slice config; compare every artifact hash.

    Environment mismatch (optional-dependency versions recorded on the
    stored artifacts vs the current environment) fails loudly before
    comparison — a changed dependency yields "non-identical", never
    "close enough".
    """
    from tezcat.plane.artifacts import environment_fingerprint

    graph = ArtifactGraph(data_dir)
    stored_report = graph.get(report_artifact_id)
    if stored_report.artifact_type != "research_report":
        raise PlaneError(f"{report_artifact_id} is not a research_report")
    slice_config = stored_report.config.get("slice_config")
    if not slice_config:
        raise PlaneError("stored report carries no slice_config")

    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        checks.append({"check": name, "ok": ok, "detail": detail})
        return ok

    current_env = environment_fingerprint()
    stored_env = stored_report.environment
    for key in ("nautilus_trader_version", "skfolio_version",
                "tezcat_version"):
        ok = stored_env.get(key) == current_env.get(key)
        check(f"environment: {key}", ok,
              f"stored {stored_env.get(key)} vs current {current_env.get(key)}")
        if not ok:
            return {"report_artifact": report_artifact_id, "success": False,
                    "checks": checks,
                    "reason": "environment mismatch — result declared "
                              "non-identical rather than silently accepted"}

    constraints = PortfolioConstraints.model_validate(slice_config["constraints"])
    costs = CostModel.model_validate(slice_config["costs"])
    rerun = run_full_slice(
        data_dir, history_ref=slice_config["history_ref"],
        history_cell=slice_config["history_cell"],
        stress_cells=slice_config["stress_cells"],
        model_id=slice_config["model_id"],
        model_params=slice_config["model_params"],
        horizon=slice_config["horizon"], n_paths=slice_config["n_paths"],
        seed=slice_config["seed"], optimizer_id=slice_config["optimizer_id"],
        optimizer_params=slice_config["optimizer_params"],
        constraints=constraints, costs=costs,
        strategy_id=slice_config["strategy_id"],
        capital=slice_config["capital"])

    stored_payload = graph.payload(report_artifact_id)
    rerun_report = graph.get(rerun["report_artifact"])
    ok = rerun_report.artifact_hash == stored_report.artifact_hash
    check("report artifact hash reproduces", ok,
          f"{stored_report.artifact_hash[:16]}… vs "
          f"{rerun_report.artifact_hash[:16]}…")
    # compare the full stored lineage hash-by-hash
    stored_ids = stored_payload["artifact_ids"]
    rerun_payload = graph.payload(rerun["report_artifact"])
    for stage in ("dataset", "forecast", "portfolio", "risk"):
        same = stored_ids[stage] == rerun_payload["artifact_ids"][stage]
        check(f"{stage} artifact reproduces", same,
              f"{stored_ids[stage]} vs {rerun_payload['artifact_ids'][stage]}")
    same_bt = stored_ids["backtests"] == rerun_payload["artifact_ids"]["backtests"]
    check("backtest artifacts reproduce", same_bt,
          f"{len(stored_ids['backtests'])} artifacts")
    success = all(c["ok"] for c in checks)
    return {"report_artifact": report_artifact_id, "success": success,
            "checks": checks,
            "identical": json.loads(canonical_json(stored_payload))
            == json.loads(canonical_json(rerun_payload))}
