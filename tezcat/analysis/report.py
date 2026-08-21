"""Research-report generation (Phase F6).

Reports are rendered **exclusively from persisted artifacts** — the version
record, the batch summary, and the analysis result. No number in a report is
computed here beyond formatting; every figure traces to an artifact a reader
can open. Narrative claims are template-bounded and hedged: the report
states estimates with uncertainty, never causal conclusions about real
markets.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from tezcat.core.config import canonical_json
from tezcat.experiments.aggregation import aggregate
from tezcat.experiments.registry import Registry


def _fmt(x: Any, nd: int = 4) -> str:
    if x is None:
        return "—"
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def build_report(registry: Registry, version_id: str) -> str:
    """Render the markdown report for an analyzed experiment version."""
    record = registry.get(version_id)
    analysis = registry.get_analysis(version_id)
    if analysis is None:
        raise ValueError(
            f"no analysis found for {version_id}; run analysis first — "
            "reports are generated from artifacts only")
    summary = registry.get_batch_summary(analysis["batch_id"])
    if summary is None:
        summary = aggregate(registry, version_id)

    design = record["design"]
    lines: List[str] = []
    add = lines.append

    add(f"# {record['name']}")
    add("")
    add(f"**Question.** {design['question']}")
    add("")
    add(f"**Hypothesis.** {design['hypothesis']}")
    add("")
    add("## Design")
    add("")
    add(f"| Field | Value |\n| --- | --- |")
    add(f"| Design type | {design['design_type']} |")
    add(f"| Independent variables | {', '.join(design['independent_variables']) or '—'} |")
    add(f"| Dependent variables | {', '.join(design['dependent_variables'])} |")
    add(f"| Primary metric | **{design['primary_metric']}** |")
    add(f"| Replications per cell | {design['replications']} |")
    add(f"| Planned runs | {record['planned_runs']} |")
    add(f"| Root seed | {record['root_seed']} |")
    add("")

    add("## Data")
    add("")
    add(f"Batch `{summary['batch_id']}`: {summary['completed_runs']}/"
        f"{summary['planned_runs']} runs complete.")
    if summary["missing_keys"]:
        add("")
        add(f"**MISSING RUNS ({len(summary['missing_keys'])}):** "
            f"`{', '.join(summary['missing_keys'])}` — interpret with caution.")
    add("")

    pm = design["primary_metric"]
    add(f"### Per-cell results: {pm}")
    add("")
    add("| Cell | n | mean | std | q05 | median | q95 |")
    add("| --- | --- | --- | --- | --- | --- | --- |")
    for cell, desc in analysis["descriptives"].items():
        d = desc.get(pm, {})
        add(f"| {cell} | {d.get('n', 0)} | {_fmt(d.get('mean'))} | "
            f"{_fmt(d.get('std'))} | {_fmt(d.get('q05'))} | "
            f"{_fmt(d.get('median'))} | {_fmt(d.get('q95'))} |")
    add("")

    if analysis["comparisons"]:
        add("## Comparisons (primary metric)")
        add("")
        add("| Control | Treatment | Δ mean | 95% CI | Cohen's d | Cliff's δ | p (perm) | p (Holm) |")
        add("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for c in analysis["comparisons"]:
            diff = c["diff"]
            ci = (f"[{_fmt(diff.get('ci_low'))}, {_fmt(diff.get('ci_high'))}]"
                  if diff.get("ci_low") is not None else "—")
            add(f"| {c['control']} | {c['treatment']} | "
                f"{_fmt(diff.get('estimate'))} | {ci} | {_fmt(c['cohens_d'])} | "
                f"{_fmt(c['cliffs_delta'])} | "
                f"{_fmt(c['permutation'].get('p_value'))} | "
                f"{_fmt(c['permutation'].get('p_holm'))} |")
        add("")

    if analysis.get("interaction"):
        it = analysis["interaction"]
        add("## 2×2 interaction (primary metric)")
        add("")
        if it.get("estimate") is None:
            add(f"Not estimable: {it.get('warning', 'insufficient data')}.")
        else:
            add(f"| Quantity | Estimate |\n| --- | --- |")
            add(f"| Interaction | {_fmt(it['estimate'])} "
                f"(95% CI [{_fmt(it['ci_low'])}, {_fmt(it['ci_high'])}]) |")
            add(f"| Main effect (factor A) | {_fmt(it['main_effect_a'])} |")
            add(f"| Main effect (factor B) | {_fmt(it['main_effect_b'])} |")
        add("")

    if analysis.get("trend"):
        tr = analysis["trend"]
        add("## Sweep trend (primary metric)")
        add("")
        add(f"Level-order/mean correlation: {_fmt(tr['level_mean_correlation'])} "
            f"(permutation p = {_fmt(tr['p_value'])}, "
            f"{tr['levels_used']} levels).")
        add("")

    add("## Methods and assumptions")
    add("")
    m = analysis["methods"]
    add(f"- Uncertainty: {m['uncertainty']} (n_boot = {analysis['n_boot']})")
    add(f"- Test: {m['test']}")
    add(f"- Effect sizes: {m['effect_sizes']}")
    add(f"- Multiple comparisons: {m['multiple_comparisons']}")
    for a in m["assumptions"]:
        add(f"- Assumes: {a}")
    add("")

    if analysis["warnings"]:
        add("## Warnings")
        add("")
        for w in analysis["warnings"]:
            add(f"- ⚠ {w}")
        add("")

    card = record["model_card"]
    add("## Model card (excerpt)")
    add("")
    add(f"{card['purpose']}")
    add("")
    add(f"- Calibration: {card['calibration_status']}")
    add(f"- Validation: {card['validation_status']}")
    add("- Claims **not** supported: " + "; ".join(card["claims_not_supported"]))
    add("- Limitations: " + "; ".join(card["limitations"]))
    add("")

    add("## Reproducibility")
    add("")
    add(f"- Research hash: `{record['research_hash']}`")
    add(f"- Version: `{record['version_id']}` (schema v{record['schema_version']}, "
        f"code {record['code_version']}, seed allocator "
        f"v{record['seed_allocator_version']})")
    add(f"- Batch: `{summary['batch_id']}` · Analysis: `{analysis['analysis_id']}` "
        f"(seed {analysis['analysis_seed']})")
    add(f"- Reproduce: register this version from its stored record, run "
        f"`BatchRunner(registry).run(\"{version_id}\")`, then "
        f"`analyze(registry, \"{version_id}\", seed={analysis['analysis_seed']})`.")
    add("")
    add("*All numbers in this report are read from persisted artifacts "
        "(batch summary and analysis result); none are computed at render "
        "time. Results describe the specified synthetic model only.*")

    markdown = "\n".join(lines)
    meta = {
        "version_id": version_id,
        "research_hash": record["research_hash"],
        "analysis_id": analysis["analysis_id"],
        "batch_id": summary["batch_id"],
        "report_hash": hashlib.sha256(markdown.encode()).hexdigest(),
        "sources": ["version record", "batch summary", "analysis result"],
    }
    registry.save_report(version_id, markdown, meta)
    return markdown
