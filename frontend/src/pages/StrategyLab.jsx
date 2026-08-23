import React, { useEffect, useMemo, useState } from 'react';
import { api, asArray, fmtNum, truncHash } from '../api/client';
import { EmptyRow, Panel, StatTile, navigate } from '../components/ui.jsx';
import LineChart from '../components/LineChart.jsx';

// STRATEGY LAB (Phase S4): the Tezcat ↔ NautilusTrader bridge.
//
// Two-layer laboratory: Tezcat generates deterministic SYNTHETIC market
// worlds (with hashed ecology fingerprints); Nautilus replays a fixed,
// hashed strategy against them. Everything here is backtest/research —
// there is no live-trading surface. Every result shows its full
// provenance chain: research hash → world hash → strategy hash.

const SYNTH_LABEL = 'SYNTHETIC TEZCAT MARKET WORLD — not real market data';

function pct(v) {
  if (v == null || !isFinite(v)) return '—';
  return `${(v * 100).toFixed(3)}%`;
}

// ---- world building ------------------------------------------------------

function BuildWorldPanel({ onBuilt }) {
  const [experiments, setExperiments] = useState([]);
  const [ref, setRef] = useState('');
  const [cell, setCell] = useState('');
  const [replication, setReplication] = useState('0');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [msg, setMsg] = useState(null);

  useEffect(() => {
    api.researchList().then(setExperiments).catch((e) => setErr(e.message));
  }, []);

  const build = () => {
    setBusy(true); setErr(null); setMsg(null);
    api.labBuildWorld({ ref, cell: cell.trim() || null,
                        replication: Number(replication) || 0 })
      .then((w) => {
        setMsg(`world ${w.world_id} built (deterministic, immutable)`);
        onBuilt();
      })
      .catch((e) => setErr(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <Panel title="BUILD MARKET WORLD"
           sub="one deterministic realization of a registered experiment cell">
      <div className="grid cols-4">
        <label className="kv">
          <span className="k">experiment</span>
          <select className="inp" value={ref} onChange={(e) => setRef(e.target.value)}>
            <option value="">—</option>
            {asArray(experiments).map((x) => (
              <option key={x.version_id} value={x.version_id}>
                {x.version_id} · {x.name}
              </option>
            ))}
          </select>
        </label>
        <label className="kv">
          <span className="k">cell (blank = first)</span>
          <input className="inp" value={cell} onChange={(e) => setCell(e.target.value)} />
        </label>
        <label className="kv">
          <span className="k">replication</span>
          <input className="inp" value={replication}
                 onChange={(e) => setReplication(e.target.value)} />
        </label>
        <div style={{ alignSelf: 'end' }}>
          <button className="btn" disabled={busy || !ref} onClick={build}>
            {busy ? 'SIMULATING…' : 'BUILD WORLD'}
          </button>
        </div>
      </div>
      {msg && <div className="pos small section-gap">{msg}</div>}
      {err && <div className="neg small section-gap">{err}</div>}
    </Panel>
  );
}

// ---- world detail --------------------------------------------------------

function FingerprintPanel({ worldId }) {
  const [detail, setDetail] = useState(null);
  const [quotes, setQuotes] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (!worldId) return;
    setDetail(null); setQuotes(null); setErr(null);
    api.labWorld(worldId).then(setDetail).catch((e) => setErr(e.message));
    api.labWorldQuotes(worldId).then(setQuotes).catch(() => {});
  }, [worldId]);

  const points = useMemo(() => asArray(quotes && quotes.quotes)
    .map((q, i) => ({ x: i, y: (q.bid + q.ask) / 2 })), [quotes]);
  if (!worldId) return null;
  const fp = detail && detail.manifest.fingerprint;

  return (
    <Panel title={`WORLD ${worldId}`} sub="ecology fingerprint — a market-environment identity">
      {err && <div className="neg small">{err}</div>}
      {detail && (
        <>
          <div className="small">
            <span className="badge synthetic">SYNTHETIC</span>{' '}
            <span className="dim">{SYNTH_LABEL}</span>
          </div>
          <div className="section-gap">
            <LineChart points={points} height={170} yLabel="mid" />
          </div>
          <div className="grid cols-4 section-gap">
            <StatTile label="rel. spread" value={fmtNum(fp.mean_relative_spread, 5)} />
            <StatTile label="return vol" value={fmtNum(fp.return_volatility, 5)} />
            <StatTile label="vol clustering" value={fmtNum(fp.volatility_clustering, 3)} />
            <StatTile label="hill tail α" value={fmtNum(fp.hill_tail_alpha, 2)} />
            <StatTile label="depth q25/q50/q75"
              value={`${fmtNum(fp.depth_q25, 0)} / ${fmtNum(fp.depth_q50, 0)} / ${fmtNum(fp.depth_q75, 0)}`} />
            <StatTile label="max drawdown" value={pct(fp.max_drawdown)} />
            <StatTile label="crash" value={fp.crash_detected ? 'YES' : 'no'}
                      tone={fp.crash_detected ? 'neg' : ''} />
            <StatTile label="trades / step" value={fmtNum(fp.trades_per_step, 1)} />
          </div>
          <div className="kv section-gap">
            <span className="k">regime occupancy</span>
            <span className="v">{Object.entries(fp.regime_occupancy)
              .map(([k, v]) => `${k} ${(v * 100).toFixed(0)}%`).join(' · ')}</span>
            <span className="k">agent composition</span>
            <span className="v">{Object.entries(fp.agent_composition)
              .map(([k, v]) => `${k.replace('_trader', '')} ${(v * 100).toFixed(0)}%`).join(' · ')}</span>
            <span className="k">world hash</span>
            <span className="v">{truncHash(detail.manifest.world_hash, 20)}</span>
            <span className="k">research hash</span>
            <span className="v">{truncHash(detail.manifest.research_hash, 20)}</span>
            <span className="k">experiment</span>
            <span className="v clickable accent"
                  onClick={() => navigate(`#/research/${detail.manifest.version_id}`)}>
              {detail.manifest.version_id} · {detail.manifest.cell} · rep {detail.manifest.replication}
            </span>
          </div>
        </>
      )}
    </Panel>
  );
}

// ---- backtest form -------------------------------------------------------

function RunPanel({ worldId, onRan }) {
  const [strategies, setStrategies] = useState([]);
  const [strategyId, setStrategyId] = useState('ema_cross');
  const [params, setParams] = useState({});
  const [cash, setCash] = useState('1000000');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [result, setResult] = useState(null);

  useEffect(() => {
    api.labStrategies().then((rows) => {
      setStrategies(rows);
      const first = rows.find((r) => r.strategy_id === 'ema_cross') || rows[0];
      if (first) { setStrategyId(first.strategy_id); setParams({ ...first.default_params }); }
    }).catch((e) => setErr(e.message));
  }, []);

  const spec = strategies.find((s) => s.strategy_id === strategyId);
  const pick = (sid) => {
    setStrategyId(sid);
    const s = strategies.find((x) => x.strategy_id === sid);
    if (s) setParams({ ...s.default_params });
  };

  const run = () => {
    setBusy(true); setErr(null); setResult(null);
    api.labRunBacktest({ world_id: worldId, strategy_id: strategyId,
                         params, starting_cash: Number(cash) || 1000000 })
      .then((r) => { setResult(r); onRan(); })
      .catch((e) => setErr(e.message))
      .finally(() => setBusy(false));
  };

  const m = result && result.metrics;
  return (
    <Panel title="RUN NAUTILUS BACKTEST"
           sub="fixed, hashed strategy vs the selected world — backtest only, no live trading">
      <div className="grid cols-4">
        <label className="kv">
          <span className="k">strategy</span>
          <select className="inp" value={strategyId} onChange={(e) => pick(e.target.value)}>
            {strategies.map((s) => (
              <option key={s.strategy_id} value={s.strategy_id}>{s.strategy_id}</option>
            ))}
          </select>
        </label>
        {spec && Object.keys(spec.default_params).map((k) => (
          <label key={k} className="kv">
            <span className="k">{k}</span>
            <input className="inp" value={params[k] ?? ''}
                   onChange={(e) => setParams({ ...params, [k]: e.target.value })} />
          </label>
        ))}
        <label className="kv">
          <span className="k">starting cash</span>
          <input className="inp" value={cash} onChange={(e) => setCash(e.target.value)} />
        </label>
        <div style={{ alignSelf: 'end' }}>
          <button className="btn" disabled={busy || !worldId} onClick={run}>
            {busy ? 'RUNNING…' : 'RUN BACKTEST'}
          </button>
        </div>
      </div>
      {spec && <div className="dim small section-gap">{spec.description}</div>}
      {err && <div className="neg small section-gap">{err}</div>}
      {result && (
        <div className="section-gap">
          <div className="pos small">result {result.result_id}</div>
          <div className="grid cols-4 section-gap">
            <StatTile label="total return" value={pct(m.total_return)}
                      tone={m.total_return >= 0 ? 'pos' : 'neg'} />
            <StatTile label="max drawdown" value={pct(m.max_drawdown)} />
            <StatTile label="fills / submitted"
                      value={`${m.n_fills} / ${m.n_orders_submitted}`} />
            <StatTile label="mean slippage vs mid"
                      value={fmtNum(m.mean_slippage_vs_mid, 4)} />
          </div>
          <table className="table section-gap">
            <thead>
              <tr><th>REGIME</th><th>PNL</th><th>FILLS</th><th>TURNOVER</th><th>SLIPPAGE</th></tr>
            </thead>
            <tbody>
              {Object.entries(m.regime_breakdown).map(([regime, row]) => (
                <tr key={regime}>
                  <td className="accent">{regime}</td>
                  <td className={row.pnl >= 0 ? 'pos' : 'neg'}>{fmtNum(row.pnl, 2)}</td>
                  <td>{row.n_fills}</td>
                  <td>{fmtNum(row.turnover, 0)}</td>
                  <td>{fmtNum(row.mean_slippage_vs_mid, 4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="dim small section-gap">
            provenance: research {truncHash(result.world.research_hash, 14)} → world{' '}
            {truncHash(result.world.world_hash, 14)} → strategy{' '}
            {truncHash(result.strategy.strategy_hash, 14)} · nautilus{' '}
            {result.environment.nautilus_trader_version}
          </div>
        </div>
      )}
    </Panel>
  );
}

// ---- results + comparison ------------------------------------------------

function ResultsPanel({ refreshToken }) {
  const [rows, setRows] = useState(null);
  const [selected, setSelected] = useState([]);
  const [comparison, setComparison] = useState(null);
  const [repro, setRepro] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    api.labResults().then(setRows).catch((e) => setErr(e.message));
  }, [refreshToken]);

  const toggle = (rid) => setSelected((cur) =>
    cur.includes(rid) ? cur.filter((x) => x !== rid) : [...cur, rid]);

  const compare = () => {
    setErr(null); setComparison(null);
    api.labCompare(selected).then(setComparison).catch((e) => setErr(e.message));
  };
  const reproduce = (rid) => {
    setErr(null); setRepro(null);
    api.labReproduce(rid).then((r) => setRepro({ rid, ...r }))
      .catch((e) => setErr(e.message));
  };

  return (
    <Panel title="LAB RESULTS"
           sub="content-addressed artifacts; select ≥2 to compare across worlds">
      {err && <div className="neg small">{err}</div>}
      <table className="table">
        <thead>
          <tr><th></th><th>RESULT</th><th>WORLD</th><th>CELL</th><th>STRATEGY</th>
              <th>RETURN</th><th>DRAWDOWN</th><th>FILLS</th><th></th></tr>
        </thead>
        <tbody>
          {rows === null && <EmptyRow cols={9} text="LOADING…" />}
          {rows !== null && rows.length === 0 && (
            <EmptyRow cols={9} text="NO RESULTS — build a world and run a backtest" />
          )}
          {asArray(rows).map((r) => (
            <tr key={r.result_id}>
              <td><input type="checkbox" checked={selected.includes(r.result_id)}
                         onChange={() => toggle(r.result_id)} /></td>
              <td className="accent">{r.result_id}</td>
              <td className="dim small">{r.world_id}</td>
              <td className="dim small">{r.cell}</td>
              <td>{r.strategy_id}</td>
              <td className={r.total_return >= 0 ? 'pos' : 'neg'}>{pct(r.total_return)}</td>
              <td>{pct(r.max_drawdown)}</td>
              <td>{r.n_fills}</td>
              <td><button className="btn ghost" onClick={() => reproduce(r.result_id)}>
                REPRODUCE</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      {selected.length >= 2 && (
        <div className="section-gap">
          <button className="btn" onClick={compare}>COMPARE {selected.length} RESULTS</button>
        </div>
      )}
      {repro && (
        <div className="section-gap small">
          <span className={repro.success ? 'pos' : 'neg'}>
            {repro.success ? '✓ reproduction successful' : '✗ reproduction failed'}
          </span>{' '}
          <span className="dim">{repro.rid}:{' '}
            {asArray(repro.checks).map((c) => `${c.ok ? '✓' : '✗'} ${c.check}`).join(' · ')}
            {repro.reason ? ` — ${repro.reason}` : ''}</span>
        </div>
      )}
      {comparison && (
        <div className="section-gap">
          <div className="small accent">{comparison.mode.toUpperCase()}</div>
          <table className="table">
            <thead>
              <tr><th>WORLD</th><th>CELL</th><th>STRATEGY</th><th>RETURN</th>
                  <th>DRAWDOWN</th><th>FILL RATE</th><th>SLIPPAGE</th><th>REGIME PNL</th></tr>
            </thead>
            <tbody>
              {comparison.rows.map((row) => (
                <tr key={row.result_id}>
                  <td className="dim small">{row.world_id}</td>
                  <td>{row.cell}</td>
                  <td>{row.strategy_id}</td>
                  <td className={row.total_return >= 0 ? 'pos' : 'neg'}>{pct(row.total_return)}</td>
                  <td>{pct(row.max_drawdown)}</td>
                  <td>{fmtNum(row.fill_rate, 2)}</td>
                  <td>{fmtNum(row.mean_slippage_vs_mid, 4)}</td>
                  <td className="dim small">{Object.entries(row.regime_pnl)
                    .map(([k, v]) => `${k}: ${fmtNum(v, 1)}`).join(' · ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="dim small">{comparison.note}</div>
        </div>
      )}
    </Panel>
  );
}

// ---- page ----------------------------------------------------------------

export default function StrategyLab() {
  const [worlds, setWorlds] = useState(null);
  const [selectedWorld, setSelectedWorld] = useState(null);
  const [resultsToken, setResultsToken] = useState(0);
  const [err, setErr] = useState(null);

  const loadWorlds = () =>
    api.labWorlds().then(setWorlds).catch((e) => setErr(e.message));
  useEffect(() => { loadWorlds(); }, []);

  return (
    <div className="grid">
      {err && <div className="neg small">{err}</div>}
      <BuildWorldPanel onBuilt={loadWorlds} />
      <Panel title="MARKET WORLDS"
             sub="deterministic, immutable, content-addressed — the bridge input">
        <table className="table">
          <thead>
            <tr><th>WORLD</th><th>EXPERIMENT</th><th>CELL</th><th>REP</th>
                <th>QUOTES</th><th>TRADES</th><th>CRASH</th><th>REGIMES</th></tr>
          </thead>
          <tbody>
            {worlds === null && <EmptyRow cols={8} text="LOADING…" />}
            {worlds !== null && worlds.length === 0 && (
              <EmptyRow cols={8}
                text="NO WORLDS — build one from a registered experiment above" />
            )}
            {asArray(worlds).map((w) => (
              <tr key={w.world_id}
                  className={`clickable ${selectedWorld === w.world_id ? 'selected-row' : ''}`}
                  onClick={() => setSelectedWorld(w.world_id)}>
                <td className="accent">{w.world_id}</td>
                <td className="dim small">{w.version_id}</td>
                <td>{w.cell}</td>
                <td>{w.replication}</td>
                <td>{w.n_quotes}</td>
                <td>{w.n_trades}</td>
                <td className={w.crash_detected ? 'neg' : 'dim'}>
                  {w.crash_detected ? 'YES' : 'no'}</td>
                <td className="dim small">{Object.entries(w.regime_occupancy || {})
                  .map(([k, v]) => `${k} ${(v * 100).toFixed(0)}%`).join(' · ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <FingerprintPanel worldId={selectedWorld} />
      {selectedWorld && (
        <RunPanel worldId={selectedWorld}
                  onRan={() => setResultsToken((t) => t + 1)} />
      )}
      <ResultsPanel refreshToken={resultsToken} />
    </div>
  );
}
