"""Tezcat research CLI (Phase F10).

Five verbs — the complete research loop, nothing else:

    tezcat run <spec.json>        register an experiment spec and run its batch
    tezcat analyze <ref>          statistical analysis of a completed batch
    tezcat report <ref>           render the markdown research report
    tezcat reproduce <ref>        re-execute and verify against stored hashes
    tezcat list                   registered experiments

Plus one namespaced group (Phase S3) for external event-market
intelligence — read-only ingestion that feeds the same five-verb loop:

    tezcat markets providers | datasets | import | show | signature |
                   propose | research | compare

``<ref>`` is a version id (``expv_…``), a full research hash, or an
unambiguous hash prefix (≥8 chars). The data directory defaults to
``$TEZCAT_DATA_DIR`` or ``./data``; override with ``--data-dir``.

Spec file format (JSON)::

    {
      "experiment_id": "exp_margin",
      "name": "margin-spiral-ab",
      "root_seed": 42,                 // optional; default_seed otherwise
      "config": { ... ExperimentConfig ... },
      "design": { ... DesignSpec ... }
    }

See examples/ for ready-to-run specs and docs/cli.md for the full guide.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

OK = "✓"
BAD = "✗"


def _data_dir(args) -> str:
    return args.data_dir or os.environ.get("TEZCAT_DATA_DIR", "data")


def _registry(args):
    from tezcat.experiments.registry import Registry
    return Registry(_data_dir(args))


def _resolve(registry, ref: str) -> str:
    from tezcat.experiments.reproduce import resolve_reference
    return resolve_reference(registry, ref)


def _f(x, nd: int = 5) -> str:
    """None-safe float formatting for CLI tables."""
    return f"{x:.{nd}f}" if isinstance(x, (int, float)) else "—"


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------
def cmd_run(args) -> int:
    from pydantic import ValidationError
    from tezcat.core.config import ExperimentConfig
    from tezcat.experiments.batch import BatchRunner
    from tezcat.experiments.schema import DesignSpec, ExperimentVersion

    spec_path = Path(args.spec)
    if not spec_path.exists():
        print(f"{BAD} spec file not found: {spec_path}", file=sys.stderr)
        return 1
    try:
        spec = json.loads(spec_path.read_text())
    except json.JSONDecodeError as exc:
        print(f"{BAD} spec is not valid JSON: {exc}", file=sys.stderr)
        return 1

    try:
        config = ExperimentConfig.model_validate(spec["config"])
        design = DesignSpec.model_validate(spec["design"])
        version = ExperimentVersion(
            experiment_id=spec.get("experiment_id", spec_path.stem),
            name=spec.get("name", spec_path.stem),
            config=config, design=design,
            root_seed=spec.get("root_seed"))
    except (KeyError, ValidationError, ValueError) as exc:
        print(f"{BAD} invalid experiment spec:\n{exc}", file=sys.stderr)
        return 1

    registry = _registry(args)
    vid = registry.register(version)
    print(f"{OK} experiment registered")
    print(f"  version:       {vid}")
    print(f"  research hash: {version.research_hash}")
    print(f"  design:        {design.design_type} · "
          f"{len(version.cell_configs)} cells × {design.replications} "
          f"replications = {design.planned_runs()} runs")
    if args.validate_only:
        print(f"{OK} validate-only: nothing executed")
        return 0

    print("running batch (resumable; re-run this command to continue)…")
    batch = BatchRunner(registry).run(vid, max_runs=args.max_runs,
                                  workers=args.workers)
    mark = OK if batch["status"] == "completed" else "…"
    print(f"{mark} batch {batch['status']}: "
          f"{batch['completed']}/{batch['planned']} runs "
          f"({batch['executed_this_call']} executed this call)")
    if batch["pending_keys"]:
        print(f"  pending: {len(batch['pending_keys'])} runs "
              f"(budget cap — run again to continue)")
    print(f"\nnext: tezcat analyze {vid}")
    return 0


def cmd_analyze(args) -> int:
    from tezcat.analysis import AnalysisError, analyze

    registry = _registry(args)
    vid = _resolve(registry, args.ref)
    try:
        a = analyze(registry, vid, seed=args.seed,
                    allow_partial=args.allow_partial)
    except AnalysisError as exc:
        print(f"{BAD} {exc}", file=sys.stderr)
        return 1
    print(f"{OK} analysis {a['analysis_id']} on {vid} "
          f"(seed {a['analysis_seed']}, n_boot {a['n_boot']})")
    pm = a["primary_metric"]
    for cell, desc in a["descriptives"].items():
        d = desc.get(pm, {})
        if d.get("n"):
            print(f"  {cell:<24} n={d['n']:<4} {pm} "
                  f"mean={_f(d.get('mean'))} q05={_f(d.get('q05'))} "
                  f"q95={_f(d.get('q95'))}")
        else:
            print(f"  {cell:<24} n=0    (no completed runs)")
    for comp in a["comparisons"]:
        diff = comp["diff"]
        print(f"  {comp['control']} vs {comp['treatment']}: "
              f"Δ={_f(diff.get('estimate'))} "
              f"CI=[{_f(diff.get('ci_low'))}, {_f(diff.get('ci_high'))}] "
              f"p_holm={_f(comp['permutation'].get('p_holm'), 4)}")
    if a.get("interaction") and a["interaction"].get("estimate") is not None:
        it = a["interaction"]
        print(f"  interaction: {_f(it['estimate'])} "
              f"CI=[{_f(it['ci_low'])}, {_f(it['ci_high'])}]")
    for w in a["warnings"]:
        print(f"  ⚠ {w}")
    print(f"\nnext: tezcat report {vid}")
    return 0


def cmd_report(args) -> int:
    from tezcat.analysis import build_report

    registry = _registry(args)
    vid = _resolve(registry, args.ref)
    try:
        markdown = build_report(registry, vid)
    except ValueError as exc:
        print(f"{BAD} {exc}", file=sys.stderr)
        return 1
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown)
        print(f"{OK} report written to {args.output}")
    else:
        print(markdown)
    return 0


def cmd_reproduce(args) -> int:
    from tezcat.experiments.reproduce import ReproductionError, reproduce

    registry = _registry(args)
    try:
        result = reproduce(registry, args.ref,
                           sample=None if args.full else args.sample)
    except ReproductionError as exc:
        print(f"{BAD} {exc}", file=sys.stderr)
        return 1
    for c in result["checks"]:
        mark = OK if c["ok"] else BAD
        detail = f"  ({c['detail']})" if c["detail"] else ""
        print(f"{mark} {c['check']}{detail}")
    print()
    if result["success"]:
        print(f"{OK} REPRODUCTION SUCCESSFUL — "
              f"{result['runs_verified']}/{result['runs_sampled']} sampled "
              f"runs match all stored hashes "
              f"({result['total_rows']} rows total)")
        return 0
    print(f"{BAD} REPRODUCTION FAILED — "
          f"{result['runs_failed']} run(s) diverged", file=sys.stderr)
    return 1


def cmd_list(args) -> int:
    registry = _registry(args)
    rows = registry.list()
    if not rows:
        print(f"no experiments registered in {_data_dir(args)!r} "
              f"(try: tezcat run examples/margin_spiral_ab.json)")
        return 0
    print(f"{'version':<18} {'design':<10} {'runs':>5}  {'primary metric':<22} name")
    for r in rows:
        print(f"{r['version_id']:<18} {r['design_type']:<10} "
              f"{r['planned_runs']:>5}  {r['primary_metric']:<22} {r['name']}")
    return 0


# ---------------------------------------------------------------------------
# Markets verb group (Phase S3): external event-market intelligence
# ---------------------------------------------------------------------------
def _markets_service(args):
    from tezcat.external.service import MarketsService
    return MarketsService(_data_dir(args))


def cmd_markets_providers(args) -> int:
    for p in _markets_service(args).providers():
        live = "live enabled" if p["live_enabled"] else "offline (fixtures only)"
        print(f"{p['provider_id']:<12} {p['adapter_version']:<26} read-only · {live}")
        print(f"{'':<12} auth: {p['auth']}")
    return 0


def cmd_markets_datasets(args) -> int:
    rows = _markets_service(args).list_datasets()
    if not rows:
        print("no external datasets registered "
              "(try: tezcat markets import kalshi SYN-MKT-YES "
              "--fixture tests/fixtures/external/kalshi/synthetic_event.json)")
        return 0
    print(f"{'dataset':<18} {'provider':<12} {'src':<18} {'v':>2} {'obs':>5}  market")
    for r in rows:
        print(f"{r['dataset_id']:<18} {r['provider']:<12} "
              f"{r['source_kind']:<18} {r['version']:>2} "
              f"{r['n_observations']:>5}  {', '.join(r['market_ids'])}")
    return 0


def cmd_markets_import(args) -> int:
    svc = _markets_service(args)
    try:
        m = svc.import_market(args.provider, args.market_id,
                              fixture_path=args.fixture,
                              start_ts=args.start_ts, end_ts=args.end_ts,
                              period_minutes=args.period,
                              supersedes=args.supersedes)
    except Exception as exc:  # noqa: BLE001 — surface the exact reason
        print(f"{BAD} import failed: {exc}", file=sys.stderr)
        return 1
    print(f"{OK} dataset registered (immutable)")
    print(f"  dataset:  {m.dataset_id} (v{m.version})")
    print(f"  hash:     {m.dataset_hash}")
    print(f"  source:   {m.provider} · {m.source_kind} · {m.adapter_version}")
    print(f"  window:   {m.time_window['start']} → {m.time_window['end']}")
    print(f"  license:  {m.license}")
    print(f"\nnext: tezcat markets signature {m.dataset_id}")
    return 0


def cmd_markets_show(args) -> int:
    svc = _markets_service(args)
    try:
        m = svc.dataset(args.dataset_id)
        obs = svc.observations(args.dataset_id)
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD} {exc}", file=sys.stderr)
        return 1
    label = ("SYNTHETIC FIXTURE DATA" if m.source_kind == "synthetic_fixture"
             else "OBSERVED EXTERNAL MARKET DATA")
    print(f"[{label}]")
    for k, v in m.lineage().items():
        print(f"  {k}: {v}")
    print(f"\n  {'timestamp':<28} {'p (implied)':>11} {'spread':>8} {'volume':>8}")
    for o in obs[: args.limit]:
        print(f"  {o.timestamp:<28} {_f(o.implied_probability, 4):>11} "
              f"{_f(o.spread, 4):>8} {_f(o.volume, 1):>8}")
    if len(obs) > args.limit:
        print(f"  … {len(obs) - args.limit} more observations")
    return 0


def cmd_markets_signature(args) -> int:
    svc = _markets_service(args)
    try:
        sig = svc.signature(args.dataset_id, t0_index=args.t0,
                            pre_window=args.pre, post_window=args.post)
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD} {exc}", file=sys.stderr)
        return 1
    print(f"{OK} event signature (INFERRED from observed data; "
          f"v{sig.signature_version})")
    print(f"  hash:    {sig.signature_hash()}")
    print(f"  window:  t0={sig.t0_index} [-{sig.pre_window}, +{sig.post_window}] "
          f"({sig.window_start} → {sig.window_end})")
    print(f"  p:       {_f(sig.pre_event_probability, 3)} → "
          f"{_f(sig.post_event_probability, 3)} "
          f"(Δ {_f(sig.delta_probability, 3)}, peak {_f(sig.peak_probability, 3)}"
          f" after {sig.time_to_peak} obs)")
    print(f"  spread:  {_f(sig.pre_event_spread, 4)} → {_f(sig.post_event_spread, 4)}")
    print(f"  volume:  ×{_f(sig.volume_change, 2)}   depth: {_f(sig.depth_change, 2)}")
    print(f"  note:    {sig.transform_notes}")
    print(f"\nnext: tezcat markets research {args.dataset_id} "
          f"--mechanisms herding,mm_withdrawal")
    return 0


def cmd_markets_propose(args) -> int:
    svc = _markets_service(args)
    try:
        prop = svc.propose(args.dataset_id, t0_index=args.t0,
                           pre_window=args.pre, post_window=args.post)
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD} {exc}", file=sys.stderr)
        return 1
    print("candidate mechanisms (hypotheses to test — not explanations):")
    for c in prop["candidates"]:
        print(f"  · {c['mechanism']:<24} {c['rationale']}")
    print(f"\n{prop['disclaimer']}")
    return 0


def cmd_markets_research(args) -> int:
    svc = _markets_service(args)
    mechanisms = [m.strip() for m in args.mechanisms.split(",") if m.strip()]
    try:
        result = svc.create_research(
            args.dataset_id, mechanisms=mechanisms,
            name=args.name or f"event-{args.dataset_id}",
            replications=args.replications, t0_index=args.t0,
            pre_window=args.pre, post_window=args.post,
            t0_step=args.t0_step, total_steps=args.steps)
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD} {exc}", file=sys.stderr)
        return 1
    rm = result["research_manifest"]
    print(f"{OK} synthetic experiment registered from observed signature")
    print(f"  version:           {result['version_id']}")
    print(f"  research hash:     {result['research_hash']}")
    print(f"  dataset hash:      {rm['dataset_hash']}")
    print(f"  research identity: {rm['research_identity']}")
    print(f"  planned runs:      {result['planned_runs']}")
    if args.run:
        from tezcat.experiments.batch import BatchRunner
        print("running batch…")
        batch = BatchRunner(svc.registry).run(result["version_id"])
        mark = OK if batch["status"] == "completed" else "…"
        print(f"{mark} batch {batch['status']}: "
              f"{batch['completed']}/{batch['planned']} runs")
        print(f"\nnext: tezcat analyze {result['version_id']}")
    else:
        print(f"\nnext: tezcat run — or — tezcat markets research … --run\n"
              f"      then: tezcat analyze {result['version_id']}")
    return 0


def cmd_markets_compare(args) -> int:
    svc = _markets_service(args)
    try:
        result = svc.compare_datasets(args.dataset_a, args.dataset_b,
                                      threshold=args.threshold,
                                      max_lag=args.max_lag)
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD} {exc}", file=sys.stderr)
        return 1
    d = result["divergence"]
    print("cross-provider probability divergence "
          "(equivalence NOT verified):")
    print(f"  aligned observations: {d['n_aligned']}")
    print(f"  mean |divergence|:    {_f(d['mean_abs_divergence'], 4)}")
    print(f"  peak |divergence|:    {_f(d['peak_abs_divergence'], 4)} "
          f"at {d['peak_at']}")
    print(f"  longest run ≥{d['threshold']}:  {d['longest_divergent_run']} obs")
    print(f"  converged at:         {d['converged_at'] or '—'}")
    ll = result["lead_lag"]
    if "unavailable" in ll:
        print(f"  lead/lag: unavailable ({ll['unavailable']})")
    else:
        print(f"  lead/lag observation: best lag {ll['best_lag']} "
              f"(corr {_f(ll['best_correlation'], 3)}) — no causal claim")
    return 0


def _add_markets_parser(sub) -> None:
    mk = sub.add_parser("markets",
                        help="external event-market intelligence (read-only)")
    msub = mk.add_subparsers(dest="markets_command", required=True)

    mp = msub.add_parser("providers", help="list provider adapters")
    mp.set_defaults(func=cmd_markets_providers)

    md = msub.add_parser("datasets", help="list registered external datasets")
    md.set_defaults(func=cmd_markets_datasets)

    mi = msub.add_parser("import", help="import market history as an "
                                        "immutable dataset")
    mi.add_argument("provider", choices=["kalshi", "polymarket"])
    mi.add_argument("market_id")
    mi.add_argument("--fixture", default=None,
                    help="labeled fixture bundle (offline import)")
    mi.add_argument("--start-ts", type=int, default=0)
    mi.add_argument("--end-ts", type=int, default=None)
    mi.add_argument("--period", type=int, default=60,
                    help="sampling period in minutes (default 60)")
    mi.add_argument("--supersedes", default=None,
                    help="dataset id this import supersedes (new version)")
    mi.set_defaults(func=cmd_markets_import)

    ms = msub.add_parser("show", help="dataset lineage + observations")
    ms.add_argument("dataset_id")
    ms.add_argument("--limit", type=int, default=10)
    ms.set_defaults(func=cmd_markets_show)

    for name, fn, hlp in (
            ("signature", cmd_markets_signature,
             "extract the documented event signature"),
            ("propose", cmd_markets_propose,
             "propose candidate mechanisms (hypotheses)")):
        p = msub.add_parser(name, help=hlp)
        p.add_argument("dataset_id")
        p.add_argument("--t0", type=int, default=None,
                       help="event-anchor observation index (default: "
                            "largest |Δp|, recorded in the signature)")
        p.add_argument("--pre", type=int, default=None)
        p.add_argument("--post", type=int, default=None)
        p.set_defaults(func=fn)

    mr = msub.add_parser("research", help="compile the signature into an "
                                          "ordinary experiment version")
    mr.add_argument("dataset_id")
    mr.add_argument("--mechanisms", required=True,
                    help="comma-separated, e.g. herding,mm_withdrawal")
    mr.add_argument("--name", default=None)
    mr.add_argument("--replications", type=int, default=5)
    mr.add_argument("--t0", type=int, default=None)
    mr.add_argument("--pre", type=int, default=None)
    mr.add_argument("--post", type=int, default=None)
    mr.add_argument("--t0-step", type=int, default=400)
    mr.add_argument("--steps", type=int, default=1200)
    mr.add_argument("--run", action="store_true",
                    help="also execute the batch now")
    mr.set_defaults(func=cmd_markets_research)

    mc = msub.add_parser("compare", help="cross-provider divergence + "
                                         "lead/lag for two datasets")
    mc.add_argument("dataset_a")
    mc.add_argument("dataset_b")
    mc.add_argument("--threshold", type=float, default=0.02)
    mc.add_argument("--max-lag", type=int, default=10)
    mc.set_defaults(func=cmd_markets_compare)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tezcat",
        description="Tezcat research CLI: run, analyze, report, reproduce.")
    p.add_argument("--data-dir", default=None,
                   help="registry directory (default: $TEZCAT_DATA_DIR or ./data)")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="register an experiment spec and run its batch")
    run.add_argument("spec", help="path to a JSON experiment spec")
    run.add_argument("--max-runs", type=int, default=None,
                     help="budget cap for this call (resumable)")
    run.add_argument("--workers", type=int, default=1,
                     help="worker processes for independent replications "
                          "(results identical for any worker count)")
    run.add_argument("--validate-only", action="store_true",
                     help="validate and register without executing")
    run.set_defaults(func=cmd_run)

    an = sub.add_parser("analyze", help="analyze a completed batch")
    an.add_argument("ref", help="version id, research hash, or hash prefix")
    an.add_argument("--seed", type=int, default=0)
    an.add_argument("--allow-partial", action="store_true")
    an.set_defaults(func=cmd_analyze)

    rep = sub.add_parser("report", help="render the markdown research report")
    rep.add_argument("ref")
    rep.add_argument("-o", "--output", default=None, help="write to file")
    rep.set_defaults(func=cmd_report)

    rp = sub.add_parser("reproduce", help="re-execute and verify stored hashes")
    rp.add_argument("ref")
    rp.add_argument("--sample", type=int, default=3,
                    help="number of runs to re-execute (default 3)")
    rp.add_argument("--full", action="store_true", help="re-execute every run")
    rp.set_defaults(func=cmd_reproduce)

    ls = sub.add_parser("list", help="list registered experiments")
    ls.set_defaults(func=cmd_list)

    _add_markets_parser(sub)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
