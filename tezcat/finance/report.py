"""Professional financial-analysis report (S6).

Rendered exclusively from the persisted case-spec and valuation-output
payloads (the exact content registered in the artifact graph) — never
from mutable state. Sections follow standard sell-side/research memo
structure, with Tezcat's honesty rules: facts, assumptions, and derived
values are labeled; every number traces to an artifact; the
methodology section carries the reproduction command.
"""

from __future__ import annotations

from typing import Any, Dict, List

from tezcat.finance.case import ValuationCase


def _money(v: Any, nd: int = 1) -> str:
    return f"{v:,.{nd}f}" if isinstance(v, (int, float)) else "—"


def _pct(v: Any, nd: int = 1) -> str:
    return f"{v * 100:+.{nd}f}%" if isinstance(v, (int, float)) else "—"


def render_report(case: ValuationCase, results: Dict[str, Any],
                  case_art, output_art) -> str:
    L: List[str] = []
    add = L.append
    target = case.target
    summary = results["target_summary"]
    dcf = results["dcf"]
    tri = results["triangulation"]
    units = f"{target.currency} {target.units}"

    add(f"# {case.name}")
    add("")
    add(f"*Research object `{case.version_id}` · finance model "
        f"v{results['finance_model_version']} · all values in {units} "
        "unless noted. Facts, assumptions, and derived values are labeled "
        "throughout. This is an analytical research artifact, not "
        "investment advice.*")

    # -- Executive summary ---------------------------------------------
    add("")
    add("## Executive summary")
    add("")
    add(f"- **Company:** {target.name}"
        + (f" ({target.ticker})" if target.ticker else "")
        + (f", {target.sector}" if target.sector else ""))
    add(f"- **Valuation conclusion (derived):** triangulated equity value "
        f"of {_money(tri['low'], 2)}–{_money(tri['high'], 2)} per share "
        f"across {len(tri['methods'])} methodologies (midpoint "
        f"{_money(tri['midpoint'], 2)}); current price "
        f"{_money(tri['current_share_price'], 2)}.")
    if "pro_forma" in results:
        pf = results["pro_forma"]
        txn = results["transaction"]
        add(f"- **Transaction conclusion (derived):** at "
            f"{_money(txn['offer_price_per_share'], 2)} per share "
            f"({_pct(txn['premium_to_unaffected'])} premium), the deal is "
            f"**{pf['year_1_verdict']}** in year 1 "
            f"({_pct(pf['years'][0]['accretion_dilution_pct'], 2)} EPS "
            "impact).")
    add(f"- **Key assumptions:** WACC {_pct(dcf['assumptions']['wacc'])}"
        + (f", terminal growth "
           f"{_pct(dcf['assumptions']['terminal_growth'])}"
           if dcf["assumptions"]["terminal_growth"] is not None else "")
        + f"; forecast horizon {dcf['horizon_years']} years"
        + (f"; synergies "
           f"{_money(results['transaction']['assumptions']['synergies']['run_rate_pretax'], 0)} run-rate pre-tax"
           if "transaction" in results else "") + ".")
    add(f"- **Major risk disclosures:** terminal value is "
        f"{results['dcf']['terminal_value_pct_of_ev'] * 100:.0f}% of "
        "enterprise value; see Risk analysis.")
    if case.analyst_note:
        add(f"- **Analyst note:** {case.analyst_note}")

    # -- Company overview ----------------------------------------------
    add("")
    add("## Company overview")
    add("")
    if target.description:
        add(target.description)
        add("")
    add("| Metric (derived from sourced facts) | Value |")
    add("| --- | ---: |")
    add(f"| Latest period | {summary['latest_period']} |")
    add(f"| Revenue | {_money(summary['revenue'])} |")
    add(f"| Revenue CAGR (historical) | {_pct(summary['revenue_cagr'])} |")
    add(f"| EBITDA | {_money(summary['ebitda'])} "
        f"({summary['ebitda_margin'] * 100:.1f}% margin) |")
    add(f"| Net income | {_money(summary['net_income'])} |")
    add(f"| Net debt | {_money(summary['net_debt'])} |")
    add(f"| Net debt / EBITDA | "
        f"{_money(summary['leverage_net_debt_ebitda'], 2)}x |")
    add(f"| Diluted shares | {_money(summary['diluted_shares'])} |")
    add(f"| Market capitalization | {_money(summary['market_cap'])} |")

    # -- Historical financials -----------------------------------------
    add("")
    add("## Historical financial analysis (sourced facts + derived margins)")
    add("")
    add("| Period | Revenue | EBITDA | Margin | Net income | Net debt |")
    add("| --- | ---: | ---: | ---: | ---: | ---: |")
    for p in target.periods:
        add(f"| {p.label} | {_money(p.revenue)} | {_money(p.ebitda)} | "
            f"{p.ebitda_margin * 100:.1f}% | {_money(p.net_income)} | "
            f"{_money(p.net_debt)} |")
    add("")
    add(f"*Provenance: {target.periods[-1].provenance.source}.*")

    # -- Forecast -------------------------------------------------------
    add("")
    add("## Operating forecast (analyst assumptions → derived projections)")
    add("")
    fa = results["forecast"]["assumptions"]
    add(f"Assumptions ({fa['rationale'] or 'declared in the case spec'}): "
        f"revenue growth {[f'{g:.1%}' for g in fa['revenue_growth']]}, "
        f"EBITDA margin {[f'{m:.1%}' for m in fa['ebitda_margin']]}, "
        f"tax rate {fa['tax_rate']:.1%}.")
    add("")
    add("| Year | Revenue | EBITDA | EBIT | Capex | ΔNWC | Unlevered FCF |")
    add("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for p in results["forecast"]["projections"]:
        add(f"| {p['year']} | {_money(p['revenue'])} | {_money(p['ebitda'])} "
            f"| {_money(p['ebit'])} | {_money(p['capex'])} | "
            f"{_money(p['change_in_nwc'])} | {_money(p['unlevered_fcf'])} |")

    # -- Valuation ------------------------------------------------------
    add("")
    add("## Valuation")
    add("")
    add("### Discounted cash flow (derived)")
    add("")
    add(f"- PV of forecast FCF: {_money(dcf['pv_forecast_fcf'])}")
    add(f"- PV of terminal value ({dcf['assumptions']['terminal_method']}):"
        f" {_money(dcf['pv_terminal_value'])} "
        f"({dcf['terminal_value_pct_of_ev'] * 100:.0f}% of EV)")
    add(f"- Enterprise value: **{_money(dcf['enterprise_value'])}**")
    add(f"- Less net debt: {_money(dcf['net_debt'])}")
    add(f"- Equity value: **{_money(dcf['equity_value'])}** → "
        f"**{_money(dcf['implied_value_per_share'], 2)} per share**")
    if "comps" in results:
        c = results["comps"]
        sel = c["selection"]
        add("")
        add("### Trading comparables (derived)")
        add("")
        add(f"- {c['n_peers']} peers; selected {sel['statistic']} "
            f"{sel['multiple']} = {c['selected_multiple_value']:.2f}x")
        add(f"- Implied per share: "
            f"**{_money(c['implied_value_per_share'], 2)}** "
            f"(peer q25–q75 dispersion: {_money(c['per_share_at_peer_q25'], 2)}"
            f"–{_money(c['per_share_at_peer_q75'], 2)})")
    if "precedents" in results:
        pr = results["precedents"]
        add("")
        add("### Precedent transactions (derived)")
        add("")
        add(f"- {pr['n_transactions']} transactions; selected "
            f"{pr['selection']['statistic']} {pr['selection']['multiple']} "
            f"= {pr['selected_multiple_value']:.2f}x → implied "
            f"**{_money(pr['implied_value_per_share'], 2)}** per share")
    add("")
    add("### Triangulation")
    add("")
    add("| Methodology | Implied value per share |")
    add("| --- | ---: |")
    for row in tri["methods"]:
        add(f"| {row['method']} | "
            f"{_money(row['implied_value_per_share'], 2)} |")
    add(f"| **Range** | **{_money(tri['low'], 2)} – "
        f"{_money(tri['high'], 2)}** |")
    add("")
    add(f"*{tri['note']}.*")

    # -- Transaction analysis ------------------------------------------
    if "transaction" in results:
        txn = results["transaction"]
        pf = results["pro_forma"]
        add("")
        add("## Transaction analysis (derived from declared assumptions)")
        add("")
        add(f"Hypothetical acquisition of {target.name} by "
            f"{case.acquirer.name}. Offer "
            f"{_money(txn['offer_price_per_share'], 2)}/share "
            f"({_pct(txn['premium_to_unaffected'])} premium), equity "
            f"purchase price {_money(txn['equity_purchase_price'])}, "
            f"purchase EV {_money(txn['purchase_enterprise_value'])} "
            + (f"({txn['implied_ev_ebitda']:.1f}x EBITDA)."
               if txn["implied_ev_ebitda"] else "."))
        add("")
        add("| Sources | | Uses | |")
        add("| --- | ---: | --- | ---: |")
        src = list(txn["sources"].items())
        use = list(txn["uses"].items())
        for i in range(max(len(src), len(use))):
            s = (f"{src[i][0].replace('_', ' ')} | {_money(src[i][1])}"
                 if i < len(src) else " | ")
            u = (f"{use[i][0].replace('_', ' ')} | {_money(use[i][1])}"
                 if i < len(use) else " | ")
            add(f"| {s} | {u} |")
        add(f"| **Total** | **{_money(txn['total_sources'])}** | **Total** "
            f"| **{_money(txn['total_uses'])}** |")
        add("")
        add(f"Ownership: acquirer shareholders "
            f"{txn['ownership']['acquirer_shareholders'] * 100:.1f}%, "
            f"target shareholders "
            f"{txn['ownership']['target_shareholders'] * 100:.1f}%. "
            f"Pro forma debt {_money(txn['pro_forma_debt'])}.")
        add("")
        add("### Accretion / dilution (pro forma EPS bridge)")
        add("")
        add("| Year | Standalone EPS | Pro forma EPS | Impact | Verdict |")
        add("| --- | ---: | ---: | ---: | --- |")
        for y in pf["years"]:
            add(f"| {y['year']} | {y['standalone_eps']:.3f} | "
                f"{y['pro_forma_eps']:.3f} | "
                f"{_pct(y['accretion_dilution_pct'], 2)} | {y['verdict']} |")
        y1 = pf["years"][0]["bridge"]
        add("")
        add("Year-1 net-income bridge (components sum exactly to pro forma "
            "net income):")
        add("")
        for k, v in y1.items():
            add(f"- {k.replace('_', ' ')}: {_money(v)}")
        add("")
        add(f"*One-time advisory fees of "
            f"{_money(pf['one_time_advisory_fees_excluded_from_recurring_eps'])} "
            "are excluded from recurring EPS and disclosed here.*")

    # -- Scenarios ------------------------------------------------------
    if "scenario_analysis" in results:
        sa = results["scenario_analysis"]
        add("")
        add("## Scenario analysis (declared assumption deltas)")
        add("")
        add("| Scenario | DCF value/share | TV % of EV | Year-1 EPS impact |")
        add("| --- | ---: | ---: | ---: |")
        for r in sa["scenarios"]:
            add(f"| {r['scenario']} | "
                f"{_money(r['dcf_value_per_share'], 2)} | "
                f"{r['terminal_value_pct_of_ev'] * 100:.0f}% | "
                f"{_pct(r.get('year_1_accretion'), 2) if r.get('year_1_accretion') is not None else '—'} |")
        add("")
        add(f"Value-per-share spread across scenarios: "
            f"{_money(sa['spread'], 2)} "
            f"({_money(sa['value_per_share_low'], 2)} – "
            f"{_money(sa['value_per_share_high'], 2)}).")

    # -- Sensitivities --------------------------------------------------
    if "sensitivities" in results:
        add("")
        add("## Sensitivity analysis (full model re-run per cell)")
        for s in results["sensitivities"]:
            add("")
            add(f"### {s['name']} — {s['metric']}")
            add("")
            header = " | ".join(str(v) for v in s["axis2"]["values"])
            add(f"| {s['axis1']['path']} \\\\ {s['axis2']['path']} | "
                f"{header} |")
            add("| --- |" + " ---: |" * len(s["axis2"]["values"]))
            for v1, row in zip(s["axis1"]["values"], s["grid"]):
                cells = " | ".join(
                    _money(c, 2) if isinstance(c, (int, float)) else "n/m"
                    for c in row)
                add(f"| {v1} | {cells} |")
            if s["n_invalid_cells"]:
                add("")
                add(f"*{s['n_invalid_cells']} cell(s) are not meaningful "
                    "(invalid assumption combinations, e.g. terminal growth "
                    "≥ WACC) and are marked n/m rather than interpolated.*")

    # -- Risk analysis --------------------------------------------------
    add("")
    add("## Risk analysis")
    add("")
    add(f"- **Terminal-value dependence:** "
        f"{dcf['terminal_value_pct_of_ev'] * 100:.0f}% of DCF enterprise "
        "value sits in the terminal value; conclusions are sensitive to "
        "terminal assumptions (see sensitivity tables).")
    if "comps" in results:
        c = results["comps"]
        add(f"- **Peer dispersion:** the comps point estimate moves from "
            f"{_money(c['per_share_at_peer_q25'], 2)} to "
            f"{_money(c['per_share_at_peer_q75'], 2)} per share across the "
            "peer interquartile range.")
    if "transaction" in results:
        txn = results["transaction"]
        add(f"- **Financing risk:** pro forma debt of "
            f"{_money(txn['pro_forma_debt'])} against combined earnings; "
            "accretion depends on the cost of new debt and synergy "
            "realization (see scenarios).")
        add("- **Execution risk:** synergy phase-in is an assumption; the "
            "downside scenario shows the deal economics without full "
            "realization.")
    if "scenario_analysis" in results:
        add(f"- **Scenario spread:** "
            f"{_money(results['scenario_analysis']['spread'], 2)} per share "
            "between downside and upside cases — the model does not "
            "support more precision than this spread implies.")
    add("- **Model limitations:** single-segment operating model; "
        "constant-rate driver assumptions; no purchase accounting "
        "(goodwill/step-up D&A) in this version; debt principal held "
        "constant over the pro forma horizon. Historical inputs in the "
        "bundled example are synthetic fixtures, not real company data.")

    # -- Methodology / reproducibility ---------------------------------
    add("")
    add("## Methodology and reproducibility")
    add("")
    add(f"- Research object: `{case.version_id}` (case hash "
        f"`{case.case_hash()[:16]}…`)")
    add(f"- Case artifact: `{case_art.artifact_id}` · output artifact: "
        f"`{output_art.artifact_id}`")
    add(f"- Finance model version: {FINANCE_MODEL_VERSION_STR}")
    add(f"- Source provenance: "
        f"{target.periods[-1].provenance.source}")
    add("- All derived values are deterministic functions of the persisted "
        "case specification; the report renders only from persisted "
        "artifact payloads.")
    add("")
    add("Reproduce:")
    add("")
    add("```")
    add(f"tezcat finance reproduce {case_art.artifact_id}")
    add("```")
    return "\n".join(L)


from tezcat.finance.statements import FINANCE_MODEL_VERSION  # noqa: E402

FINANCE_MODEL_VERSION_STR = str(FINANCE_MODEL_VERSION)
