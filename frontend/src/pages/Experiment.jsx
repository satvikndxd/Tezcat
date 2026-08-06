import React, { useEffect, useState } from 'react';
import { api, asArray, fmtDate, truncHash, fmtInt } from '../api/client';
import { Panel, StatusBadge, EmptyRow, navigate } from '../components/ui.jsx';

// The full ExperimentConfig shape is backend-defined; extract what we can,
// tolerating any missing pieces.
function extractAgents(config) {
  if (!config || typeof config !== 'object') return [];
  const src = config.agents ?? config.agent_populations ?? config.populations;
  if (Array.isArray(src)) {
    return src.map((a, i) => ({
      type: a?.type ?? a?.agent_type ?? `agent_${i}`,
      count: a?.count ?? a?.n ?? null,
      cash: a?.cash ?? a?.initial_cash ?? a?.starting_cash ?? null,
      params: a?.params ?? a?.parameters ?? null,
      raw: a,
    }));
  }
  if (src && typeof src === 'object') {
    return Object.entries(src).map(([type, a]) => ({
      type,
      count: typeof a === 'number' ? a : a?.count ?? null,
      cash: a?.cash ?? a?.initial_cash ?? null,
      params: a?.params ?? null,
      raw: a,
    }));
  }
  return [];
}

function summarizeParams(agent) {
  const p = agent.params ?? agent.raw;
  if (!p || typeof p !== 'object') return '—';
  const parts = [];
  for (const [k, v] of Object.entries(p)) {
    if (['type', 'agent_type', 'count', 'n', 'cash', 'initial_cash', 'params'].includes(k)) continue;
    if (v == null || typeof v === 'object') continue;
    parts.push(`${k}=${v}`);
    if (parts.length >= 4) break;
  }
  return parts.length > 0 ? parts.join(' ') : '—';
}

function extractRegimePolicy(config) {
  if (!config || typeof config !== 'object') return [];
  const src = config.regime_policy ?? config.regimes ?? config.regime ?? null;
  if (!src || typeof src !== 'object') return [];
  const rows = [];
  const walk = (obj, prefix) => {
    for (const [k, v] of Object.entries(obj)) {
      const key = prefix ? `${prefix}.${k}` : k;
      if (v != null && typeof v === 'object' && !Array.isArray(v)) {
        walk(v, key);
      } else {
        rows.push([key, Array.isArray(v) ? v.join(', ') : String(v)]);
      }
      if (rows.length >= 16) return;
    }
  };
  walk(src, '');
  return rows;
}

export default function Experiment({ id }) {
  const [exp, setExp] = useState(null);
  const [shocks, setShocks] = useState([]);
  const [error, setError] = useState(null);
  const [seed, setSeed] = useState('');
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    let alive = true;
    api.experiment(id).then(
      (e) => alive && setExp(e || null),
      (err) => alive && setError(`load failed: ${err.message}`)
    );
    api.experimentShocks(id).then(
      (s) => alive && setShocks(asArray(s)),
      () => {}
    );
    return () => {
      alive = false;
    };
  }, [id]);

  const startRun = async () => {
    setStarting(true);
    setError(null);
    try {
      const body = {};
      if (seed.trim() !== '' && !isNaN(Number(seed))) body.seed = Number(seed);
      const run = await api.startRun(id, body);
      if (run && run.run_id) {
        navigate(`#/run/${run.run_id}`);
      } else {
        setError('run started but no id returned');
      }
    } catch (err) {
      setError(`start failed: ${err.message}`);
    } finally {
      setStarting(false);
    }
  };

  const config = exp?.config;
  const agents = extractAgents(config);
  const policy = extractRegimePolicy(config);
  const cfgShocks =
    shocks.length > 0
      ? shocks
      : asArray(config?.shocks).map((s, i) => ({
          shock_id: s?.shock_id ?? s?.id ?? `shock_${i}`,
          shock_type: s?.shock_type ?? s?.type,
          trigger: s?.trigger,
          magnitude: s?.magnitude,
          duration: s?.duration,
          enabled: s?.enabled,
          description: s?.description,
        }));

  return (
    <div>
      <div className="label" style={{ marginBottom: 8 }}>
        <a href="#/">HOME</a> / EXPERIMENT
      </div>
      {error && <div className="err" style={{ marginBottom: 10 }}>{error}</div>}
      {!exp ? (
        <div className="panel"><div className="msg">{error ? 'EXPERIMENT UNAVAILABLE' : 'LOADING…'}</div></div>
      ) : (
        <>
          <div className="run-header">
            <div className="cell">
              <span className="label">Experiment</span>
              <span className="val big accent">{exp.name || exp.experiment_id}</span>
            </div>
            <div className="cell">
              <span className="label">ID</span>
              <span className="val">{exp.experiment_id}</span>
            </div>
            <div className="cell">
              <span className="label">Config Hash</span>
              <span className="val">{truncHash(exp.config_hash, 12)}</span>
            </div>
            <div className="cell">
              <span className="label">Status</span>
              <StatusBadge status={exp.status} />
            </div>
            <div className="cell">
              <span className="label">Created</span>
              <span className="val small">{fmtDate(exp.created_at)}</span>
            </div>
            <div className="cell">
              <span className="label">Version</span>
              <span className="val small">{exp.version || '—'}</span>
            </div>
            <div className="cell" style={{ marginLeft: 'auto' }}>
              <span className="label">Seed (optional)</span>
              <div className="toolbar">
                <input
                  className="inp"
                  style={{ width: 110 }}
                  placeholder="random"
                  value={seed}
                  inputMode="numeric"
                  onChange={(e) => setSeed(e.target.value)}
                />
                <button className="btn big" disabled={starting} onClick={startRun}>
                  {starting ? 'STARTING…' : 'START RUN'}
                </button>
              </div>
            </div>
          </div>

          {exp.hypothesis && (
            <div className="panel section-gap">
              <div className="panel-body small">
                <span className="label" style={{ marginRight: 10 }}>Hypothesis</span>
                {String(exp.hypothesis)}
              </div>
            </div>
          )}

          <div className="grid cols-2 section-gap">
            <Panel title="AGENT POPULATION" sub={`${agents.length} types`} tight>
              <table className="t">
                <thead>
                  <tr>
                    <th className="l">Type</th>
                    <th>Count</th>
                    <th>Cash</th>
                    <th className="l">Params</th>
                  </tr>
                </thead>
                <tbody>
                  {agents.length === 0 ? (
                    <EmptyRow cols={4} text="NO AGENT CONFIG" />
                  ) : (
                    agents.map((a, i) => (
                      <tr key={i}>
                        <td className="l accent">{String(a.type)}</td>
                        <td>{fmtInt(a.count)}</td>
                        <td>{a.cash != null ? fmtInt(a.cash) : '—'}</td>
                        <td className="l dim small">{summarizeParams(a)}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </Panel>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <Panel title="SCHEDULED SHOCKS" sub={`${cfgShocks.length}`} tight>
                <table className="t">
                  <thead>
                    <tr>
                      <th className="l">ID</th>
                      <th className="l">Type</th>
                      <th>Step</th>
                      <th>Magnitude</th>
                      <th>Duration</th>
                      <th className="l">Enabled</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cfgShocks.length === 0 ? (
                      <EmptyRow cols={6} text="NO SCHEDULED SHOCKS" />
                    ) : (
                      cfgShocks.map((s, i) => (
                        <tr key={s.shock_id || i}>
                          <td className="l">{s.shock_id || '—'}</td>
                          <td className="l accent">{s.shock_type || '—'}</td>
                          <td>{fmtInt(s.trigger?.step)}</td>
                          <td>{s.magnitude != null ? String(s.magnitude) : '—'}</td>
                          <td>{s.duration != null ? String(s.duration) : '—'}</td>
                          <td className={`l ${s.enabled === false ? 'neg' : 'pos'}`}>
                            {s.enabled === false ? 'no' : 'yes'}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </Panel>

              <Panel title="REGIME POLICY">
                {policy.length === 0 ? (
                  <span className="dim small">NO REGIME POLICY IN CONFIG</span>
                ) : (
                  <div className="kv">
                    {policy.map(([k, v]) => (
                      <React.Fragment key={k}>
                        <span className="k">{k}</span>
                        <span className="v">{v}</span>
                      </React.Fragment>
                    ))}
                  </div>
                )}
              </Panel>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
