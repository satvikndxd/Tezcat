import React, { useEffect, useMemo, useState } from 'react';
import { api, asArray, fmtNum, fmtDate, truncHash } from '../api/client';
import { EmptyRow, Panel, StatTile, navigate } from '../components/ui.jsx';
import LineChart from '../components/LineChart.jsx';

// MARKETS (Phase S3): external event-market intelligence.
//
// Everything on this page is observation and research design — Tezcat has
// no trading surface. Source honesty is a hard requirement: every chart
// and panel carries its provenance label (OBSERVED external data vs
// SYNTHETIC FIXTURE vs INFERRED features), probabilities are always
// "market-implied probability proxies", and cross-provider comparisons
// repeat the equivalence caveat verbatim from the API.

function DataLabel({ label }) {
  const text = String(label || '');
  const cls = text.startsWith('SYNTHETIC')
    ? 'synthetic'
    : text.startsWith('INFERRED')
      ? 'inferred'
      : 'observed';
  return <span className={`badge ${cls}`}>{text}</span>;
}

const PROB_CAPTION = 'market-implied probability proxy — not a forecast, not financial advice';

// ---- list view -----------------------------------------------------------

function ProvidersPanel() {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    api.marketsProviders().then(setRows).catch((e) => setErr(e.message));
  }, []);
  return (
    <Panel title="PROVIDERS" sub="read-only adapters — no trading, no credentials for public data">
      {err && <div className="neg small">{err}</div>}
      <table className="table">
        <thead>
          <tr><th>PROVIDER</th><th>ADAPTER</th><th>MODE</th><th>AUTH</th></tr>
        </thead>
        <tbody>
          {rows === null && <EmptyRow cols={4} text="LOADING…" />}
          {asArray(rows).map((p) => (
            <tr key={p.provider_id}>
              <td className="accent">{p.provider_id}</td>
              <td className="dim">{p.adapter_version}</td>
              <td>{p.live_enabled ? 'LIVE ENABLED' : 'OFFLINE (fixtures only)'}</td>
              <td className="dim small">{p.auth}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function ImportPanel({ onImported }) {
  const [provider, setProvider] = useState('kalshi');
  const [marketId, setMarketId] = useState('');
  const [fixture, setFixture] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);

  const doImport = () => {
    setBusy(true); setMsg(null); setErr(null);
    api.marketsImport({
      provider,
      market_id: marketId.trim(),
      fixture_path: fixture.trim() || null,
    })
      .then((m) => {
        setMsg(`dataset ${m.dataset_id} registered (immutable, v${m.version})`);
        onImported();
      })
      .catch((e) => setErr(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <Panel title="IMPORT MARKET DATA" sub="raw response retained · normalized · checksummed · immutable">
      <div className="grid cols-4">
        <label className="kv">
          <span className="k">provider</span>
          <select className="inp" value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="kalshi">kalshi</option>
            <option value="polymarket">polymarket</option>
          </select>
        </label>
        <label className="kv">
          <span className="k">market id</span>
          <input className="inp" value={marketId} placeholder="e.g. SYN-MKT-YES"
                 onChange={(e) => setMarketId(e.target.value)} />
        </label>
        <label className="kv">
          <span className="k">fixture bundle (offline)</span>
          <input className="inp" value={fixture}
                 placeholder="tests/fixtures/external/kalshi/synthetic_event.json"
                 onChange={(e) => setFixture(e.target.value)} />
        </label>
        <div style={{ alignSelf: 'end' }}>
          <button className="btn" disabled={busy || !marketId.trim()} onClick={doImport}>
            {busy ? 'IMPORTING…' : 'IMPORT'}
          </button>
        </div>
      </div>
      <div className="dim small section-gap">
        Live provider access requires TEZCAT_EXTERNAL_LIVE=1 server-side; without it,
        only labeled fixture bundles import. Provider terms are recorded on every dataset.
      </div>
      {msg && <div className="pos small section-gap">{msg}</div>}
      {err && <div className="neg small section-gap">{err}</div>}
    </Panel>
  );
}

function DatasetsPanel({ rows }) {
  return (
    <Panel title="EXTERNAL DATASETS" sub="immutable after registration — new data means a new version">
      <table className="table">
        <thead>
          <tr>
            <th>DATASET</th><th>PROVIDER</th><th>SOURCE</th><th>V</th>
            <th>OBS</th><th>MARKET</th><th>RETRIEVED</th>
          </tr>
        </thead>
        <tbody>
          {rows === null && <EmptyRow cols={7} text="LOADING…" />}
          {rows !== null && rows.length === 0 && (
            <EmptyRow cols={7}
              text="NO DATASETS — import one above or via `tezcat markets import`" />
          )}
          {asArray(rows).map((r) => (
            <tr key={r.dataset_id} className="clickable"
                onClick={() => navigate(`#/markets/${r.dataset_id}`)}>
              <td className="accent">{r.dataset_id}</td>
              <td>{r.provider}</td>
              <td>
                <DataLabel label={r.source_kind === 'synthetic_fixture'
                  ? 'SYNTHETIC FIXTURE' : 'OBSERVED'} />
              </td>
              <td>{r.version}</td>
              <td>{r.n_observations}</td>
              <td className="dim small">{asArray(r.market_ids).join(', ')}</td>
              <td className="dim small">{fmtDate(r.retrieved_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function ComparePanel({ rows }) {
  const ids = asArray(rows).map((r) => r.dataset_id);
  const [a, setA] = useState('');
  const [b, setB] = useState('');
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);

  const doCompare = () => {
    setBusy(true); setErr(null); setResult(null);
    api.marketsCompare({ dataset_a: a, dataset_b: b })
      .then(setResult)
      .catch((e) => setErr(e.message))
      .finally(() => setBusy(false));
  };

  const d = result && result.divergence;
  return (
    <Panel title="CROSS-PROVIDER COMPARISON"
           sub="probability divergence + lead/lag observation — equivalence is never assumed">
      <div className="grid cols-4">
        <label className="kv">
          <span className="k">dataset A</span>
          <select className="inp" value={a} onChange={(e) => setA(e.target.value)}>
            <option value="">—</option>
            {ids.map((id) => <option key={id} value={id}>{id}</option>)}
          </select>
        </label>
        <label className="kv">
          <span className="k">dataset B</span>
          <select className="inp" value={b} onChange={(e) => setB(e.target.value)}>
            <option value="">—</option>
            {ids.map((id) => <option key={id} value={id}>{id}</option>)}
          </select>
        </label>
        <div style={{ alignSelf: 'end' }}>
          <button className="btn" disabled={busy || !a || !b || a === b} onClick={doCompare}>
            {busy ? 'COMPARING…' : 'COMPARE'}
          </button>
        </div>
      </div>
      {err && <div className="neg small section-gap">{err}</div>}
      {result && (
        <>
          <div className="accent small section-gap">{result.equivalence}</div>
          <div className="grid cols-4 section-gap">
            <StatTile label="aligned obs" value={d.n_aligned} />
            <StatTile label="mean |divergence|" value={fmtNum(d.mean_abs_divergence, 4)} />
            <StatTile label={`peak |divergence|`} value={fmtNum(d.peak_abs_divergence, 4)} />
            <StatTile label="longest divergent run" value={d.longest_divergent_run} />
          </div>
          <div className="dim small section-gap">{d.label}</div>
          {result.lead_lag && !result.lead_lag.unavailable && (
            <div className="small section-gap">
              lead/lag observation: best lag {result.lead_lag.best_lag} (corr{' '}
              {fmtNum(result.lead_lag.best_correlation, 3)}) — {result.lead_lag.label}
            </div>
          )}
          {result.lead_lag && result.lead_lag.unavailable && (
            <div className="dim small section-gap">
              lead/lag unavailable: {result.lead_lag.unavailable}
            </div>
          )}
          <div className="section-gap">
            <div className="small"><DataLabel label="OBSERVED" /> A − B signed divergence</div>
            <LineChart
              points={asArray(d.series).map((p, i) => ({ x: i, y: p.signed }))}
              height={160} yLabel="Δp (A−B)" />
          </div>
        </>
      )}
    </Panel>
  );
}

function MarketsList() {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState(null);
  const load = () => api.marketsDatasets().then(setRows).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  return (
    <div className="grid">
      {err && <div className="neg small">{err}</div>}
      <ProvidersPanel />
      <ImportPanel onImported={load} />
      <DatasetsPanel rows={rows} />
      <ComparePanel rows={rows} />
    </div>
  );
}

// ---- detail view ---------------------------------------------------------

function LineagePanel({ detail }) {
  const m = detail.manifest;
  return (
    <Panel title="DATA LINEAGE" sub="raw → normalized → features; every step named and checksummed">
      <div className="kv">
        <span className="k">provider</span><span className="v">{m.provider}</span>
        <span className="k">adapter</span><span className="v">{m.adapter_version}</span>
        <span className="k">schema</span><span className="v">external v{m.external_schema_version}</span>
        <span className="k">source kind</span><span className="v">{m.source_kind}</span>
        <span className="k">event</span><span className="v">{m.event_id || '—'}</span>
        <span className="k">markets</span><span className="v">{asArray(m.market_ids).join(', ')}</span>
        <span className="k">window</span>
        <span className="v">{m.time_window.start} → {m.time_window.end}</span>
        <span className="k">sampling</span><span className="v">{m.sampling}</span>
        <span className="k">retrieved</span><span className="v">{fmtDate(m.retrieved_at)}</span>
        <span className="k">dataset hash</span><span className="v">{truncHash(m.dataset_hash, 20)}</span>
        <span className="k">raw checksum</span>
        <span className="v">{m.raw_checksum ? truncHash(m.raw_checksum, 20) : 'raw not retained'}</span>
        <span className="k">normalized checksum</span>
        <span className="v">{truncHash(m.normalized_checksum, 20)}</span>
        <span className="k">license / terms</span><span className="v">{m.license}</span>
        <span className="k">permitted use</span><span className="v">{m.permitted_use}</span>
        {m.supersedes && (
          <>
            <span className="k">supersedes</span>
            <span className="v clickable accent"
                  onClick={() => navigate(`#/markets/${m.supersedes}`)}>{m.supersedes}</span>
          </>
        )}
        {m.notes && (<><span className="k">notes</span><span className="v">{m.notes}</span></>)}
      </div>
    </Panel>
  );
}

function SignaturePanel({ id, sig, setSig }) {
  const [t0, setT0] = useState('');
  const [pre, setPre] = useState('');
  const [post, setPost] = useState('');
  const [err, setErr] = useState(null);
  const [proposal, setProposal] = useState(null);

  const fetchSig = () => {
    setErr(null);
    api.marketsSignature(id, { t0_index: t0, pre_window: pre, post_window: post })
      .then(setSig)
      .catch((e) => setErr(e.message));
    api.marketsPropose(id).then(setProposal).catch(() => {});
  };
  useEffect(fetchSig, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  const s = sig && sig.signature;
  return (
    <Panel title="EVENT SIGNATURE"
           sub="versioned + hashable description of the observed episode — no mechanism claim">
      <div className="grid cols-4">
        <label className="kv"><span className="k">t0 index (blank = largest |Δp|)</span>
          <input className="inp" value={t0} onChange={(e) => setT0(e.target.value)} /></label>
        <label className="kv"><span className="k">pre window</span>
          <input className="inp" value={pre} onChange={(e) => setPre(e.target.value)} /></label>
        <label className="kv"><span className="k">post window</span>
          <input className="inp" value={post} onChange={(e) => setPost(e.target.value)} /></label>
        <div style={{ alignSelf: 'end' }}>
          <button className="btn ghost" onClick={fetchSig}>EXTRACT</button>
        </div>
      </div>
      {err && <div className="neg small section-gap">{err}</div>}
      {s && (
        <>
          <div className="section-gap">
            <DataLabel label={sig.data_label} />{' '}
            <span className="dim small">signature {truncHash(sig.signature_hash, 16)}</span>
          </div>
          <div className="grid cols-4 section-gap">
            <StatTile label="p (pre → post)"
              value={`${fmtNum(s.pre_event_probability, 3)} → ${fmtNum(s.post_event_probability, 3)}`} />
            <StatTile label="Δ probability" value={fmtNum(s.delta_probability, 3)} />
            <StatTile label="peak / time-to-peak"
              value={`${fmtNum(s.peak_probability, 3)} @ ${s.time_to_peak ?? '—'}`} />
            <StatTile label="volume ×" value={fmtNum(s.volume_change, 2)} />
            <StatTile label="spread (pre → post)"
              value={`${fmtNum(s.pre_event_spread, 4)} → ${fmtNum(s.post_event_spread, 4)}`} />
            <StatTile label="depth change" value={fmtNum(s.depth_change, 2)} />
            <StatTile label="window"
              value={`t0=${s.t0_index} [-${s.pre_window}, +${s.post_window}]`} />
            <StatTile label="jump freq"
              value={fmtNum(s.features && s.features.jump_frequency, 3)} />
          </div>
          <div className="dim small section-gap">{s.transform_notes}</div>
        </>
      )}
      {proposal && (
        <div className="section-gap">
          <div className="small">CANDIDATE MECHANISMS (hypotheses to test — not explanations)</div>
          {asArray(proposal.candidates).map((c) => (
            <div key={c.mechanism} className="small section-gap">
              <span className="accent">{c.mechanism}</span>{' '}
              <span className="dim">{c.rationale}</span>
            </div>
          ))}
          <div className="dim small section-gap">{proposal.disclaimer}</div>
        </div>
      )}
    </Panel>
  );
}

function ResearchPanel({ id }) {
  const [mechs, setMechs] = useState(null);
  const [selected, setSelected] = useState([]);
  const [name, setName] = useState(`event-${id}`);
  const [replications, setReplications] = useState(5);
  const [steps, setSteps] = useState(1200);
  const [t0Step, setT0Step] = useState(400);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [result, setResult] = useState(null);

  useEffect(() => {
    api.marketsMechanisms().then(setMechs).catch((e) => setErr(e.message));
  }, []);

  const toggle = (m) => setSelected((cur) =>
    cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m]);

  const create = () => {
    setBusy(true); setErr(null);
    api.marketsResearch({
      dataset_id: id, mechanisms: selected, name,
      replications: Number(replications) || 5,
      total_steps: Number(steps) || 1200,
      t0_step: Number(t0Step) || 400,
    })
      .then(setResult)
      .catch((e) => setErr(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <Panel title="RESEARCH THIS EVENT"
           sub="observed signature → ordinary ExperimentVersion — same registry, batches, reports, reproduction">
      {mechs && (
        <div className="grid cols-2">
          {Object.entries(mechs).map(([m, info]) => (
            <label key={m} className="small clickable" style={{ display: 'block' }}>
              <input type="checkbox" checked={selected.includes(m)}
                     onChange={() => toggle(m)} disabled={m === 'information_shock'} />{' '}
              <span className="accent">{m}</span>
              {m === 'information_shock' && <span className="dim"> (always the control arm)</span>}
              <div className="dim small">{info.description}</div>
            </label>
          ))}
        </div>
      )}
      <div className="grid cols-4 section-gap">
        <label className="kv"><span className="k">name</span>
          <input className="inp" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="kv"><span className="k">replications</span>
          <input className="inp" value={replications}
                 onChange={(e) => setReplications(e.target.value)} /></label>
        <label className="kv"><span className="k">total steps</span>
          <input className="inp" value={steps} onChange={(e) => setSteps(e.target.value)} /></label>
        <label className="kv"><span className="k">shock step (t0)</span>
          <input className="inp" value={t0Step} onChange={(e) => setT0Step(e.target.value)} /></label>
      </div>
      <div className="section-gap">
        <button className="btn" disabled={busy || selected.length === 0} onClick={create}>
          {busy ? 'REGISTERING…' : 'CREATE SYNTHETIC EXPERIMENT'}
        </button>
      </div>
      {err && <div className="neg small section-gap">{err}</div>}
      {result && (
        <div className="section-gap">
          <div className="pos small">
            experiment {result.version_id} registered · {result.planned_runs} planned runs
          </div>
          <div className="kv section-gap">
            <span className="k">research hash</span>
            <span className="v">{truncHash(result.research_hash, 20)}</span>
            <span className="k">dataset hash</span>
            <span className="v">{truncHash(result.research_manifest.dataset_hash, 20)}</span>
            <span className="k">research identity</span>
            <span className="v">{truncHash(result.research_manifest.research_identity, 20)}</span>
          </div>
          <div className="section-gap">
            <button className="btn ghost"
                    onClick={() => navigate(`#/research/${result.version_id}`)}>
              OPEN IN RESEARCH →
            </button>
          </div>
          <div className="dim small section-gap">
            The synthetic experiment is a SYNTHETIC Tezcat simulation. It never becomes
            the observed market; results are compared against the signature, not merged with it.
          </div>
        </div>
      )}
    </Panel>
  );
}

export default function Markets({ id }) {
  const [detail, setDetail] = useState(null);
  const [obs, setObs] = useState(null);
  const [sig, setSig] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (!id) return;
    setDetail(null); setObs(null); setErr(null);
    api.marketsDataset(id).then(setDetail).catch((e) => setErr(e.message));
    api.marketsObservations(id).then(setObs).catch((e) => setErr(e.message));
  }, [id]);

  const points = useMemo(() => asArray(obs && obs.observations)
    .map((o, i) => ({ x: i, y: o.implied_probability }))
    .filter((p) => typeof p.y === 'number'), [obs]);

  if (!id) return <MarketsList />;

  const t0 = sig && sig.signature ? sig.signature.t0_index : null;
  return (
    <div className="grid">
      {err && <div className="neg small">{err}</div>}
      <Panel
        title={`DATASET ${id}`}
        sub={detail ? `${detail.manifest.provider} · ${asArray(detail.manifest.market_ids).join(', ')}` : ''}
      >
        <div>
          {detail && <DataLabel label={detail.data_label} />}{' '}
          <span className="clickable dim small" onClick={() => navigate('#/markets')}>
            ← all datasets
          </span>
        </div>
        <div className="section-gap">
          <LineChart points={points}
                     shocks={t0 != null ? [{ step: t0, label: 'signature t0' }] : []}
                     yLabel="implied p" />
          <div className="dim small">{PROB_CAPTION}</div>
          {obs && <div className="dim small">{obs.note}</div>}
        </div>
      </Panel>
      {detail && <LineagePanel detail={detail} />}
      <SignaturePanel id={id} sig={sig} setSig={setSig} />
      <ResearchPanel id={id} />
    </div>
  );
}
