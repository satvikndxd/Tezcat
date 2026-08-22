"""Tezcat research CLI (Phase F10).

Five verbs — the complete research loop, nothing else:

    tezcat run <spec.json>        register an experiment spec and run its batch
    tezcat analyze <ref>          statistical analysis of a completed batch
    tezcat report <ref>           render the markdown research report
    tezcat reproduce <ref>        re-execute and verify against stored hashes
    tezcat list                   registered experiments

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
    batch = BatchRunner(registry).run(vid, max_runs=args.max_runs)
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
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
