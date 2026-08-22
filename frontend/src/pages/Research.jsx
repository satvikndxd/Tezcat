import React, { useCallback, useEffect, useState } from 'react';
import { api, asArray, fmtNum, truncHash } from '../api/client';
import { EmptyRow, Panel, navigate } from '../components/ui.jsx';

// The research loop, as a page: experiments → batch → analysis → report →
// reproduction. Every number shown here is read from persisted registry
// artifacts via the research API — nothing is computed client-side.

function Check({ ok }) {
  return <span className={ok ? 'pos' : 'neg'}>{ok ? '✓' : '✗'}</span>;
}

// ---- list view -----------------------------------------------------------

function ResearchList() {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    api.researchList().then(setRows).catch((e) => setErr(e.message));
  }, []);

  return (
    <Panel title="RESEARCH EXPERIMENTS" sub="immutable, content-addressed, reproducible">
      {err && <div className="neg small">{err}</div>}
      <table className="table">
        <thead>
          <tr>
            <th>VERSION</th><th>NAME</th><th>DESIGN</th><th>RUNS</th>
            <th>PRIMARY METRIC</th><th>RESEARCH HASH</th>
          </tr>
        </thead>
        <tbody>
          {rows === null && <EmptyRow cols={6} text="LOADING…" />}
          {rows !== null && rows.length === 0 && (
            <EmptyRow cols={6} text="NO EXPERIMENTS — register one via `tezcat run <spec.json>` or POST /api/research/experiments" />
          )}
          {asArray(rows).map((r) => (
            <tr key={r.version_id} className="clickable"
                onClick={() => navigate(`#/research/${r.version_id}`)}>
              <td className="accent">{r.version_id}</td>
              <td>{r.name}</td>
              <td>{r.design_type}</td>
              <td>{r.planned_runs}</td>
              <td>{r.primary_metric}</td>
              <td className="dim">{truncHash(r.research_hash, 14)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

// ---- detail view ---------------------------------------------------------

function CellTable({ analysis, primaryMetric }) {
  const cells = Object.entries(analysis.descriptives || {});
  return (
    <table className="table">
      <thead>
        <tr><th>CELL</th><th>N</th><th>MEAN</th><th>STD</th><th>Q05</th><th>MEDIAN</th><th>Q95</th></tr>
      </thead>
      <tbody>
        {cells.map(([cell, desc]) => {
          const d = desc[primaryMetric] || {};
          return (
            <tr key={cell}>
              <td>{cell}</td><td>{d.n ?? 0}</td>
              <td className="val">{fmtNum(d.mean, 5)}</td>
              <td>{fmtNum(d.std, 5)}</td><td>{fmtNum(d.q05, 5)}</td>
              <td>{fmtNum(d.median, 5)}</td><td>{fmtNum(d.q95, 5)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Comparisons({ analysis }) {
  const comps = asArray(analysis.comparisons);
  if (!comps.length) return null;
  return (
    <table className="table">
      <thead>
        <tr><th>CONTROL</th><th>TREATMENT</th><th>Δ MEAN</th><th>95% CI</th><th>p (HOLM)</th></tr>
      </thead>
      <tbody>
        {comps.map((c, i) => (
          <tr key={i}>
            <td>{c.control}</td><td>{c.treatment}</td>
            <td className="val">{fmtNum(c.diff?.estimate, 5)}</td>
            <td>[{fmtNum(c.diff?.ci_low, 5)}, {fmtNum(c.diff?.ci_high, 5)}]</td>
            <td className={c.permutation?.p_holm < 0.05 ? 'pos' : ''}>
              {fmtNum(c.permutation?.p_holm, 4)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ResearchDetail({ id }) {
  const [record, setRecord] = useState(null);
  const [summary, setSummary] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [report, setReport] = useState(null);
  const [repro, setRepro] = useState(null);
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState(null);

  const refresh = useCallback(() => {
    api.researchGet(id).then(setRecord).catch((e) => setErr(e.message));
    api.researchSummary(id).then(setSummary).catch(() => {});
    api.researchAnalysis(id).then(setAnalysis).catch(() => {});
  }, [id]);

  useEffect(refresh, [refresh]);

  // Poll summary while a batch is executing.
  useEffect(() => {
    if (busy !== 'batch') return undefined;
    const t = setInterval(async () => {
      try {
        const s = await api.researchSummary(id);
        setSummary(s);
        if (s.complete) {
          setBusy('');
        }
      } catch { /* keep polling */ }
    }, 1000);
    return () => clearInterval(t);
  }, [busy, id]);

  const act = async (name, fn) => {
    setBusy(name);
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(e.message);
    } finally {
      if (name !== 'batch') setBusy('');
    }
  };

  const runBatch = () => act('batch', () => api.researchBatch(id));
  const runAnalyze = () =>
    act('analyze', async () => setAnalysis(await api.researchAnalyze(id)));
  const loadReport = () =>
    act('report', async () => setReport((await api.researchReport(id)).markdown));
  const runReproduce = () =>
    act('reproduce', async () => setRepro(await api.researchReproduce(id)));

  if (!record) {
    return <Panel title="RESEARCH EXPERIMENT">{err ? <div className="neg">{err}</div> : 'LOADING…'}</Panel>;
  }
  const design = record.design || {};
  const complete = summary?.complete;

  return (
    <>
      <Panel title={record.name} sub={record.version_id}>
        <div className="kv">
          <span className="k">QUESTION</span><span className="v">{design.question}</span>
          <span className="k">HYPOTHESIS</span><span className="v">{design.hypothesis}</span>
          <span className="k">DESIGN</span>
          <span className="v">{design.design_type} · {design.replications} replications/cell · {record.planned_runs} runs</span>
          <span className="k">PRIMARY METRIC</span><span className="v accent">{design.primary_metric}</span>
          <span className="k">RESEARCH HASH</span><span className="v dim">{record.research_hash}</span>
          <span className="k">PROGRESS</span>
          <span className="v">{summary ? `${summary.completed_runs}/${summary.planned_runs} runs` : '—'}
            {busy === 'batch' && <span className="accent"> (RUNNING…)</span>}
          </span>
        </div>
        <div className="section-gap" />
        <div className="actions">
          <button className="btn" onClick={runBatch} disabled={busy !== '' || complete}>
            {complete ? 'BATCH COMPLETE' : busy === 'batch' ? 'RUNNING…' : 'RUN BATCH'}
          </button>
          <button className="btn" onClick={runAnalyze} disabled={busy !== '' || !complete}>ANALYZE</button>
          <button className="btn" onClick={loadReport} disabled={busy !== '' || !analysis}>REPORT</button>
          <button className="btn" onClick={runReproduce} disabled={busy !== '' || !complete}>
            {busy === 'reproduce' ? 'REPRODUCING…' : 'REPRODUCE'}
          </button>
        </div>
        {err && <div className="neg small section-gap">{err}</div>}
      </Panel>

      {analysis && (
        <Panel title={`ANALYSIS ${analysis.analysis_id}`}
               sub={`primary metric: ${analysis.primary_metric} · seed ${analysis.analysis_seed} · n_boot ${analysis.n_boot}`}>
          <CellTable analysis={analysis} primaryMetric={analysis.primary_metric} />
          <Comparisons analysis={analysis} />
          {asArray(analysis.warnings).map((w, i) => (
            <div key={i} className="accent small">⚠ {w}</div>
          ))}
        </Panel>
      )}

      {repro && (
        <Panel title="REPRODUCTION"
               sub={repro.success ? 'ALL SAMPLED RUNS MATCH STORED HASHES' : 'DIVERGENCE DETECTED'}>
          <table className="table">
            <tbody>
              {asArray(repro.checks).map((c, i) => (
                <tr key={i}>
                  <td><Check ok={c.ok} /></td>
                  <td>{c.check}</td>
                  <td className="dim">{c.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className={repro.success ? 'pos' : 'neg'}>
            {repro.success
              ? `✓ REPRODUCTION SUCCESSFUL — ${repro.runs_verified}/${repro.runs_sampled} sampled runs (${repro.total_rows} total)`
              : `✗ REPRODUCTION FAILED — ${repro.runs_failed} run(s) diverged`}
          </div>
        </Panel>
      )}

      {report && (
        <Panel title="RESEARCH REPORT" sub="rendered from persisted artifacts only">
          <pre className="report-md">{report}</pre>
        </Panel>
      )}
    </>
  );
}

export default function Research({ id }) {
  return (
    <div className="grid">
      {id ? <ResearchDetail id={id} /> : <ResearchList />}
    </div>
  );
}
