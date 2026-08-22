import React, { useEffect, useState } from 'react';
import { api, asArray, fmtDate, truncHash, fmtInt } from '../api/client';
import { Panel, StatusBadge, EmptyRow, navigate } from '../components/ui.jsx';

function PresetCard({ preset, onCreate, busy }) {
  const agents = preset.agent_summary && typeof preset.agent_summary === 'object'
    ? Object.entries(preset.agent_summary)
    : [];
  const shocks = asArray(preset.shock_summary);
  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span className="card-name">{preset.name || preset.preset_id || 'UNNAMED'}</span>
        <span className="dim small">{fmtInt(preset.total_steps)} STEPS</span>
      </div>
      <div className="desc">{preset.description || '—'}</div>
      {asArray(preset.tags).length > 0 && (
        <div className="tags">
          {asArray(preset.tags).map((t) => (
            <span key={t} className="tag">{String(t)}</span>
          ))}
        </div>
      )}
      <div className="kv">
        <span className="k">Agents</span>
        <span className="v small">
          {agents.length > 0
            ? agents.map(([k, v]) => `${k}×${v}`).join('  ')
            : '—'}
        </span>
        <span className="k">Shocks</span>
        <span className="v small">
          {shocks.length > 0 ? shocks.map(String).join(' · ') : 'none scheduled'}
        </span>
      </div>
      <div style={{ marginTop: 'auto' }}>
        <button
          className="btn"
          disabled={busy}
          onClick={() => onCreate(preset.preset_id)}
        >
          {busy ? 'CREATING…' : 'CREATE EXPERIMENT'}
        </button>
      </div>
    </div>
  );
}

export default function Home() {
  const [presets, setPresets] = useState([]);
  const [experiments, setExperiments] = useState([]);
  const [runs, setRuns] = useState([]);
  const [error, setError] = useState(null);
  const [creating, setCreating] = useState(null);

  useEffect(() => {
    let alive = true;
    Promise.allSettled([api.presets(), api.experiments(), api.runs()]).then(
      ([p, e, r]) => {
        if (!alive) return;
        if (p.status === 'fulfilled') setPresets(asArray(p.value));
        if (e.status === 'fulfilled') setExperiments(asArray(e.value));
        if (r.status === 'fulfilled') setRuns(asArray(r.value));
        if (p.status === 'rejected' && e.status === 'rejected' && r.status === 'rejected') {
          setError('API unreachable — showing empty state');
        }
      }
    );
    return () => {
      alive = false;
    };
  }, []);

  const createExperiment = async (presetId) => {
    if (!presetId) return;
    setCreating(presetId);
    setError(null);
    try {
      const exp = await api.createExperimentFromPreset(presetId, {});
      if (exp && exp.experiment_id) {
        navigate(`#/experiment/${exp.experiment_id}`);
      } else {
        setError('experiment created but no id returned');
      }
    } catch (err) {
      setError(`create failed: ${err.message}`);
    } finally {
      setCreating(null);
    }
  };

  return (
    <div>
      {error && <div className="err" style={{ marginBottom: 10 }}>{error}</div>}

      <div className="hero-split">
        <div className="hero-card primary">
          <div className="label">CUSTOM EXPERIMENT</div>
          <div className="hero-title">Build your own market.</div>
          <div className="desc">
            Design an agent population, shock schedule, and risk configuration,
            then run it as an immutable, reproducible experiment — the same
            machinery behind every preset.
          </div>
          <button className="btn" onClick={() => navigate('#/build')}>
            BUILD SCENARIO
          </button>
        </div>
        <div className="hero-card">
          <div className="label">PRESETS</div>
          <div className="desc">
            Quickly explore known market phenomena. Presets are templates —
            each can be loaded into the builder and modified.
          </div>
        </div>
      </div>

      <div className="label" style={{ marginBottom: 8 }}>PRESET SCENARIOS</div>
      {presets.length === 0 ? (
        <div className="panel"><div className="msg">NO PRESETS AVAILABLE</div></div>
      ) : (
        <div className="cards">
          {presets.map((p) => (
            <PresetCard
              key={p.preset_id || p.name}
              preset={p}
              busy={creating === p.preset_id}
              onCreate={createExperiment}
            />
          ))}
        </div>
      )}

      <div className="grid cols-2 section-gap">
        <Panel title="EXPERIMENTS" sub={`${experiments.length}`} tight>
          <table className="t">
            <thead>
              <tr>
                <th className="l">ID</th>
                <th className="l">Name</th>
                <th className="l">Preset</th>
                <th>Status</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {experiments.length === 0 ? (
                <EmptyRow cols={5} text="NO EXPERIMENTS" />
              ) : (
                experiments.map((e) => (
                  <tr key={e.experiment_id}>
                    <td className="l">
                      <a href={`#/experiment/${e.experiment_id}`}>
                        {truncHash(e.experiment_id, 14)}
                      </a>
                    </td>
                    <td className="l">{e.name || '—'}</td>
                    <td className="l dim">{e.preset_id || '—'}</td>
                    <td><StatusBadge status={e.status} /></td>
                    <td className="dim">{fmtDate(e.created_at)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </Panel>

        <Panel title="RECENT RUNS" sub={`${runs.length}`} tight>
          <table className="t">
            <thead>
              <tr>
                <th className="l">ID</th>
                <th className="l">Experiment</th>
                <th>Status</th>
                <th>Step</th>
                <th className="l">Regime</th>
              </tr>
            </thead>
            <tbody>
              {runs.length === 0 ? (
                <EmptyRow cols={5} text="NO RUNS" />
              ) : (
                runs.slice(0, 20).map((r) => (
                  <tr key={r.run_id}>
                    <td className="l">
                      <a href={`#/run/${r.run_id}`}>{truncHash(r.run_id, 14)}</a>
                    </td>
                    <td className="l">{r.experiment_name || truncHash(r.experiment_id, 12)}</td>
                    <td><StatusBadge status={r.status} /></td>
                    <td>
                      {fmtInt(r.current_step)}<span className="dim">/{fmtInt(r.total_steps)}</span>
                    </td>
                    <td className="l dim">{r.current_regime || '—'}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </Panel>
      </div>
    </div>
  );
}
