import React, { useEffect, useState } from 'react';
import { api, asArray, fmtNum, truncHash } from '../api/client';
import { EmptyRow, Panel, StatTile } from '../components/ui.jsx';

// FINANCE (Phase S6): fundamental valuation & transaction analysis.
//
// Everything shown here reads from persisted, hash-linked artifacts:
// the case spec (facts + assumptions), the valuation output (derived),
// and the report. Labels distinguish sourced facts, analyst
// assumptions, and derived values. Analytical research — not
// investment advice; the bundled example uses synthetic fixture data.

function pct(v, nd = 1) {
  if (v == null || !isFinite(v)) return '—';
  return `${(v * 100).toFixed(nd)}%`;
}
function spct(v, nd = 1) {
  if (v == null || !isFinite(v)) return '—';
  return `${v >= 0 ? '+' : ''}${(v * 100).toFixed(nd)}%`;
}

function RunCasePanel({ onRegistered }) {
  const [specText, setSpecText] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);

  const run = () => {
    let spec;
    try {
      spec = JSON.parse(specText);
    } catch (e) {
      setErr(`spec is not valid JSON: ${e.message}`);
      return;
    }
    setBusy(true); setErr(null); setMsg(null);
    api.financeRun(spec)
      .then((r) => { setMsg(`case ${r.version_id} registered as ${r.case_artifact}`); onRegistered(); })
      .catch((e) => setErr(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <Panel title="REGISTER VALUATION CASE"
           sub="paste a case spec (see examples/finance/meridian_case.json) — executed deterministically, persisted immutably">
      <textarea className="inp" rows={4} style={{ width: '100%', fontFamily: 'inherit' }}
                placeholder='{"case_id": "...", "target": {...}, "forecast": {...}, "dcf": {...}}'
                value={specText} onChange={(e) => setSpecText(e.target.value)} />
      <div className="section-gap">
        <button className="btn" disabled={busy || !specText.trim()} onClick={run}>
          {busy ? 'EXECUTING…' : 'RUN CASE'}
        </button>
      </div>
      {msg && <div className="pos small section-gap">{msg}</div>}
      {err && <div className="neg small section-gap">{err}</div>}
    </Panel>
  );
}

function Overview({ detail }) {
  const r = detail.results;
  const s = r.target_summary;
  const tri = r.triangulation;
  return (
    <Panel title="OVERVIEW" sub={detail.spec.name}>
      <div className="small">
        <span className="badge inferred">DERIVED FROM PERSISTED ARTIFACTS</span>{' '}
        <span className="dim">{detail.data_label} · not investment advice</span>
      </div>
      <div className="grid cols-4 section-gap">
        <StatTile label="revenue (latest)" value={fmtNum(s.revenue, 0)} />
        <StatTile label="EBITDA / margin"
                  value={`${fmtNum(s.ebitda, 0)} · ${pct(s.ebitda_margin)}`} />
        <StatTile label="net debt / EBITDA"
                  value={`${fmtNum(s.leverage_net_debt_ebitda, 2)}x`} />
        <StatTile label="market cap" value={fmtNum(s.market_cap, 0)} />
      </div>
      <table className="table section-gap">
        <thead><tr><th>METHODOLOGY</th><th>IMPLIED VALUE / SHARE</th></tr></thead>
        <tbody>
          {tri.methods.map((m) => (
            <tr key={m.method}>
              <td>{m.method}</td>
              <td className="val">{fmtNum(m.implied_value_per_share, 2)}</td>
            </tr>
          ))}
          <tr>
            <td className="accent">RANGE</td>
            <td className="accent">{fmtNum(tri.low, 2)} – {fmtNum(tri.high, 2)}
              {' '}<span className="dim">(current {fmtNum(tri.current_share_price, 2)})</span></td>
          </tr>
        </tbody>
      </table>
      <div className="dim small">{tri.note}</div>
    </Panel>
  );
}

function Financials({ detail }) {
  const target = detail.spec.target;
  const fc = detail.results.forecast;
  return (
    <Panel title="FINANCIALS" sub="historical sourced facts · forecast from declared assumptions">
      <div className="small"><span className="badge observed">SOURCED FACTS</span>{' '}
        <span className="dim">{target.periods[target.periods.length - 1].provenance.source}</span></div>
      <table className="table section-gap">
        <thead><tr><th>PERIOD</th><th>REVENUE</th><th>EBITDA*</th><th>NET DEBT*</th></tr></thead>
        <tbody>
          {target.periods.map((p) => (
            <tr key={p.label}>
              <td>{p.label}</td>
              <td>{fmtNum(p.revenue, 0)}</td>
              <td>{fmtNum(p.revenue - p.cogs - p.opex, 0)}</td>
              <td>{fmtNum(p.debt - p.cash, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="dim small">*derived from sourced line items</div>
      <div className="small section-gap"><span className="badge inferred">FORECAST (assumptions → derived)</span>{' '}
        <span className="dim">{fc.assumptions.rationale}</span></div>
      <table className="table section-gap">
        <thead><tr><th>YEAR</th><th>REVENUE</th><th>EBITDA</th><th>UFCF</th></tr></thead>
        <tbody>
          {fc.projections.map((p) => (
            <tr key={p.year}>
              <td>{p.year}</td><td>{fmtNum(p.revenue, 0)}</td>
              <td>{fmtNum(p.ebitda, 0)}</td>
              <td>{fmtNum(p.unlevered_fcf, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function Valuation({ detail }) {
  const r = detail.results;
  const dcf = r.dcf;
  return (
    <Panel title="VALUATION" sub="DCF · trading comps · precedent transactions">
      <div className="grid cols-4">
        <StatTile label="WACC" value={pct(dcf.assumptions.wacc)} />
        <StatTile label="terminal" value={dcf.assumptions.terminal_method === 'gordon_growth'
          ? `g = ${pct(dcf.assumptions.terminal_growth)}` : `${dcf.assumptions.exit_multiple}x exit`} />
        <StatTile label="enterprise value" value={fmtNum(dcf.enterprise_value, 0)} />
        <StatTile label="TV % of EV" value={pct(dcf.terminal_value_pct_of_ev, 0)}
                  tone={dcf.terminal_value_pct_of_ev > 0.7 ? 'neg' : ''} />
      </div>
      {r.comps && (
        <div className="section-gap small">
          comps: {r.comps.n_peers} peers · {r.comps.selection.statistic}{' '}
          {r.comps.selection.multiple} = {fmtNum(r.comps.selected_multiple_value, 2)}x →{' '}
          <span className="accent">{fmtNum(r.comps.implied_value_per_share, 2)}/share</span>{' '}
          <span className="dim">(peer q25–q75: {fmtNum(r.comps.per_share_at_peer_q25, 2)}–
            {fmtNum(r.comps.per_share_at_peer_q75, 2)})</span>
        </div>
      )}
      {r.precedents && (
        <div className="small">
          precedents: {r.precedents.n_transactions} transactions ·{' '}
          {r.precedents.selection.statistic} {r.precedents.selection.multiple} ={' '}
          {fmtNum(r.precedents.selected_multiple_value, 2)}x →{' '}
          <span className="accent">{fmtNum(r.precedents.implied_value_per_share, 2)}/share</span>
        </div>
      )}
      <div className="dim small section-gap">{dcf.disclosure}</div>
    </Panel>
  );
}

function Transaction({ detail }) {
  const r = detail.results;
  if (!r.transaction) return null;
  const t = r.transaction;
  const pf = r.pro_forma;
  return (
    <Panel title="M&A ANALYSIS" sub="hypothetical transaction — declared assumptions, reconciled funding">
      <div className="grid cols-4">
        <StatTile label="offer / premium"
                  value={`${fmtNum(t.offer_price_per_share, 2)} · ${spct(t.premium_to_unaffected, 0)}`} />
        <StatTile label="purchase EV"
                  value={`${fmtNum(t.purchase_enterprise_value, 0)}${t.implied_ev_ebitda ? ` (${fmtNum(t.implied_ev_ebitda, 1)}x)` : ''}`} />
        <StatTile label="ownership (acq / tgt)"
                  value={`${pct(t.ownership.acquirer_shareholders)} / ${pct(t.ownership.target_shareholders)}`} />
        <StatTile label="pro forma debt" value={fmtNum(t.pro_forma_debt, 0)} />
      </div>
      <table className="table section-gap">
        <thead><tr><th>SOURCES</th><th></th><th>USES</th><th></th></tr></thead>
        <tbody>
          {Object.entries(t.sources).map(([k, v], i) => {
            const uses = Object.entries(t.uses);
            return (
              <tr key={k}>
                <td>{k.replace(/_/g, ' ')}</td><td>{fmtNum(v, 0)}</td>
                <td>{uses[i] ? uses[i][0].replace(/_/g, ' ') : ''}</td>
                <td>{uses[i] ? fmtNum(uses[i][1], 0) : ''}</td>
              </tr>
            );
          })}
          <tr className="accent">
            <td>TOTAL</td><td>{fmtNum(t.total_sources, 0)}</td>
            <td>TOTAL</td><td>{fmtNum(t.total_uses, 0)}</td>
          </tr>
        </tbody>
      </table>
      <table className="table section-gap">
        <thead><tr><th>YEAR</th><th>STANDALONE EPS</th><th>PRO FORMA EPS</th>
          <th>IMPACT</th><th>VERDICT</th></tr></thead>
        <tbody>
          {pf.years.map((y) => (
            <tr key={y.year}>
              <td>{y.year}</td>
              <td>{fmtNum(y.standalone_eps, 3)}</td>
              <td>{fmtNum(y.pro_forma_eps, 3)}</td>
              <td className={y.accretion_dilution_pct >= 0 ? 'pos' : 'neg'}>
                {spct(y.accretion_dilution_pct, 2)}</td>
              <td>{y.verdict}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="dim small">{pf.disclosure}</div>
    </Panel>
  );
}

function Scenarios({ detail }) {
  const r = detail.results;
  return (
    <Panel title="SCENARIOS & SENSITIVITY" sub="declared assumption deltas · full model re-run per cell">
      {r.scenario_analysis && (
        <table className="table">
          <thead><tr><th>SCENARIO</th><th>DCF / SHARE</th><th>TV % EV</th>
            <th>Y1 EPS IMPACT</th></tr></thead>
          <tbody>
            {r.scenario_analysis.scenarios.map((s) => (
              <tr key={s.scenario}>
                <td className="accent">{s.scenario}</td>
                <td>{fmtNum(s.dcf_value_per_share, 2)}</td>
                <td>{pct(s.terminal_value_pct_of_ev, 0)}</td>
                <td>{s.year_1_accretion != null ? spct(s.year_1_accretion, 2) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {asArray(r.sensitivities).map((s) => (
        <div key={s.name} className="section-gap">
          <div className="small accent">{s.name} — {s.metric}</div>
          <table className="table">
            <thead>
              <tr><th>{s.axis1.label || s.axis1.path}</th>
                {s.axis2.values.map((v) => <th key={v}>{v}</th>)}</tr>
            </thead>
            <tbody>
              {s.grid.map((row, i) => (
                <tr key={i}>
                  <td className="dim">{s.axis1.values[i]}</td>
                  {row.map((c, j) => (
                    <td key={j}>{typeof c === 'number' ? fmtNum(c, 2) : 'n/m'}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </Panel>
  );
}

function Research({ detail, onReproduce, repro }) {
  const c = detail.results.case;
  return (
    <Panel title="RESEARCH" sub="identity, lineage, reproduction">
      <div className="kv">
        <span className="k">case object</span><span className="v">{c.version_id}</span>
        <span className="k">case hash</span><span className="v">{truncHash(c.case_hash, 24)}</span>
        <span className="k">case artifact</span><span className="v">{detail.case_artifact}</span>
        <span className="k">output artifact</span><span className="v">{detail.output_artifact}</span>
        <span className="k">report artifact</span><span className="v">{detail.report_artifact || '—'}</span>
      </div>
      <div className="section-gap">
        <button className="btn ghost" onClick={onReproduce}>REPRODUCE</button>
      </div>
      {repro && (
        <div className="small section-gap">
          <span className={repro.success ? 'pos' : 'neg'}>
            {repro.success ? '✓ reproduction successful — outputs byte-identical'
              : '✗ reproduction failed'}</span>{' '}
          <span className="dim">
            {asArray(repro.checks).map((k) => `${k.ok ? '✓' : '✗'} ${k.check}`).join(' · ')}
          </span>
        </div>
      )}
    </Panel>
  );
}

export default function Finance() {
  const [cases, setCases] = useState(null);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [repro, setRepro] = useState(null);
  const [err, setErr] = useState(null);

  const load = () => api.financeCases().then((rows) => {
    setCases(rows);
    if (rows.length && !selected) setSelected(rows[rows.length - 1].case_artifact);
  }).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!selected) return;
    setDetail(null); setRepro(null);
    api.financeCase(selected).then(setDetail).catch((e) => setErr(e.message));
  }, [selected]);

  const reproduce = () => {
    setRepro(null);
    api.financeReproduce(selected).then(setRepro).catch((e) => setErr(e.message));
  };

  return (
    <div className="grid">
      {err && <div className="neg small">{err}</div>}
      <Panel title="VALUATION CASES"
             sub="immutable research objects — try `tezcat finance run examples/finance/meridian_case.json`">
        <table className="table">
          <thead><tr><th>ARTIFACT</th><th>CASE</th><th>NAME</th><th>CREATED</th></tr></thead>
          <tbody>
            {cases === null && <EmptyRow cols={4} text="LOADING…" />}
            {cases !== null && cases.length === 0 &&
              <EmptyRow cols={4} text="NO CASES REGISTERED" />}
            {asArray(cases).map((c) => (
              <tr key={c.case_artifact}
                  className={`clickable ${selected === c.case_artifact ? 'selected-row' : ''}`}
                  onClick={() => setSelected(c.case_artifact)}>
                <td className="accent">{c.case_artifact}</td>
                <td className="dim small">{c.case_id}</td>
                <td className="small">{c.name}</td>
                <td className="dim small">{(c.created_at || '').slice(0, 19)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      {detail && (
        <>
          <Overview detail={detail} />
          <Financials detail={detail} />
          <Valuation detail={detail} />
          <Transaction detail={detail} />
          <Scenarios detail={detail} />
          <Research detail={detail} onReproduce={reproduce} repro={repro} />
        </>
      )}
      <RunCasePanel onRegistered={load} />
    </div>
  );
}
