import React, { useEffect, useMemo, useState } from 'react';
import { api, asArray, fmtNum, truncHash } from '../api/client';
import { Panel, navigate } from '../components/ui.jsx';

// Custom Scenario Builder (Phase S2).
// You are designing an experiment, not configuring a game: every field maps
// 1:1 onto the canonical ExperimentConfig/DesignSpec schema, and the review
// step shows the exact research identity the spec would mint (computed
// server-side through the real ExperimentVersion machinery).

const STEPS = ['MARKET', 'AGENTS', 'BEHAVIOR', 'RISK', 'SHOCKS', 'SIMULATION', 'REVIEW'];

const AGENT_TYPES = [
  'noise_trader', 'retail_trader', 'momentum_trader',
  'mean_reversion_trader', 'market_maker',
];

// Behavioral parameters that actually exist in the engine (traders.py /
// config.py). Nothing here is decorative.
const BEHAVIOR_PARAMS = {
  retail_trader: [
    { key: 'herding', label: 'Herding strength', min: 0, max: 1, step: 0.05, def: 0.5,
      tip: 'Weight on crowd order-imbalance in retail buy/sell propensity.' },
    { key: 'base_size', label: 'Base order size', min: 1, max: 50, step: 1, def: 6,
      tip: 'Typical order size before risk/fear scaling.' },
  ],
  noise_trader: [
    { key: 'base_size', label: 'Base order size', min: 1, max: 50, step: 1, def: 5,
      tip: 'Typical order size before risk/fear scaling.' },
  ],
  momentum_trader: [
    { key: 'threshold', label: 'Signal threshold', min: 0.0005, max: 0.01, step: 0.0005, def: 0.0015,
      tip: 'Minimum trend signal before the agent trades; lower = more aggressive.' },
    { key: 'base_size', label: 'Base order size', min: 1, max: 50, step: 1, def: 6,
      tip: 'Typical order size before signal-strength scaling.' },
  ],
  mean_reversion_trader: [
    { key: 'fundamental_weight', label: 'Fundamental anchor weight', min: 0, max: 1, step: 0.05, def: 0.5,
      tip: 'Blend between the fundamental price and the agent’s value belief.' },
    { key: 'band', label: 'Deviation band', min: 0.002, max: 0.05, step: 0.002, def: 0.01,
      tip: 'Fractional deviation from anchor before the agent leans against it.' },
    { key: 'base_size', label: 'Base order size', min: 1, max: 50, step: 1, def: 5, tip: '' },
  ],
  market_maker: [
    { key: 'half_spread', label: 'Half-spread (ticks)', min: 1, max: 10, step: 0.5, def: 2,
      tip: 'Quoted half-spread in ticks before volatility/fear widening.' },
    { key: 'quote_size', label: 'Quote size', min: 1, max: 60, step: 1, def: 12,
      tip: 'Size quoted on each side.' },
    { key: 'skew_per_unit', label: 'Inventory skew / unit', min: 0, max: 0.01, step: 0.001, def: 0.002,
      tip: 'How strongly quotes shade against accumulated inventory.' },
  ],
};

const METRICS = ['max_drawdown', 'total_return', 'realized_volatility', 'total_trades'];
const RISK_METRICS = ['risk_forced_volume', 'risk_liquidation_slices',
  'risk_margin_calls', 'risk_max_leverage'];

function defaultState() {
  return {
    name: 'custom-market',
    market: { initial_price: 100.0, tick_size: 0.05, max_order_size: 500, max_order_age: 40 },
    agents: [
      { agent_type: 'noise_trader', count: 10, cash: 10000, inventory: 100, trading_frequency: 0.5, params: {} },
      { agent_type: 'retail_trader', count: 10, cash: 10000, inventory: 100, trading_frequency: 0.5, params: { herding: 0.5 } },
      { agent_type: 'market_maker', count: 3, cash: 10000, inventory: 100, trading_frequency: 0.5, params: {} },
    ],
    risk: { enabled: false, initial_margin: 0.5, maintenance_margin: 0.25, liquidation_delay: 2, liquidation_fraction: 0.25 },
    shocks: [],
    sim: { total_steps: 800, replications: 10, root_seed: 42, primary_metric: 'max_drawdown' },
    question: 'What does this custom market ecology produce?',
    hypothesis: 'State the outcome you expect before running.',
  };
}

export function buildSpec(s) {
  const dvs = [...METRICS, ...(s.risk.enabled ? RISK_METRICS : [])];
  return {
    experiment_id: `exp_${s.name.replace(/[^a-z0-9]+/gi, '_').toLowerCase()}`,
    name: s.name,
    root_seed: Number(s.sim.root_seed),
    config: {
      market: {
        initial_price: Number(s.market.initial_price),
        tick_size: Number(s.market.tick_size),
        max_order_size: Number(s.market.max_order_size),
        max_order_age: Number(s.market.max_order_age),
      },
      agents: s.agents.map((a) => ({
        agent_type: a.agent_type,
        count: Number(a.count),
        cash: Number(a.cash),
        inventory: Number(a.inventory),
        trading_frequency: Number(a.trading_frequency),
        params: Object.fromEntries(
          Object.entries(a.params || {}).map(([k, v]) => [k, Number(v)])),
      })),
      shocks: s.shocks.map((sh, i) => ({
        shock_id: `shock_${i + 1}`,
        shock_type: sh.shock_type,
        trigger: { kind: 'scheduled', step: Number(sh.step) },
        side: sh.shock_type === 'mm_withdrawal' ? null : sh.side,
        magnitude: Number(sh.magnitude),
        duration: Number(sh.duration),
      })),
      risk: s.risk.enabled ? {
        enabled: true,
        initial_margin: Number(s.risk.initial_margin),
        maintenance_margin: Number(s.risk.maintenance_margin),
        liquidation_delay: Number(s.risk.liquidation_delay),
        liquidation_fraction: Number(s.risk.liquidation_fraction),
      } : { enabled: false },
      total_steps: Number(s.sim.total_steps),
    },
    design: {
      design_type: 'baseline',
      question: s.question,
      hypothesis: s.hypothesis,
      dependent_variables: dvs,
      primary_metric: s.sim.primary_metric,
      replications: Number(s.sim.replications),
    },
  };
}

// Best-effort inverse: load a spec (template/import/draft) into builder state.
export function stateFromSpec(spec, fallbackName) {
  const d = defaultState();
  const cfg = spec.config || {};
  return {
    ...d,
    name: spec.name || fallbackName || d.name,
    market: { ...d.market, ...(cfg.market || {}) },
    agents: (cfg.agents || d.agents).map((a) => ({
      agent_type: a.agent_type, count: a.count, cash: a.cash ?? 10000,
      inventory: a.inventory ?? 100, trading_frequency: a.trading_frequency ?? 0.5,
      params: { ...(a.params || {}) },
    })),
    risk: { ...d.risk, ...(cfg.risk || {}) },
    shocks: (cfg.shocks || []).map((sh) => ({
      shock_type: sh.shock_type, step: sh.trigger?.step ?? 0,
      side: sh.side || 'sell', magnitude: sh.magnitude, duration: sh.duration,
    })),
    sim: {
      total_steps: cfg.total_steps ?? d.sim.total_steps,
      replications: spec.design?.replications ?? d.sim.replications,
      root_seed: spec.root_seed ?? d.sim.root_seed,
      primary_metric: spec.design?.primary_metric ?? d.sim.primary_metric,
    },
    question: spec.design?.question || d.question,
    hypothesis: spec.design?.hypothesis || d.hypothesis,
  };
}

// ---- field helpers --------------------------------------------------------

function Field({ label, tip, children }) {
  return (
    <label className="field" title={tip || ''}>
      <span className="label">{label}</span>
      {children}
    </label>
  );
}

function Num({ value, onChange, min, max, step }) {
  return (
    <input type="number" value={value} min={min} max={max} step={step || 'any'}
           onChange={(e) => onChange(e.target.value)} />
  );
}

// ---- steps ----------------------------------------------------------------

function MarketStep({ s, set }) {
  const m = s.market;
  const setM = (k, v) => set({ ...s, market: { ...m, [k]: v } });
  return (
    <div className="grid cols-2">
      <Field label="INITIAL PRICE" tip="Fundamental anchor and opening price.">
        <Num value={m.initial_price} min={1} onChange={(v) => setM('initial_price', v)} />
      </Field>
      <Field label="TICK SIZE" tip="Minimum price increment; all quotes snap to it.">
        <Num value={m.tick_size} min={0.01} step={0.01} onChange={(v) => setM('tick_size', v)} />
      </Field>
      <Field label="MAX ORDER SIZE" tip="Per-order size cap enforced at validation.">
        <Num value={m.max_order_size} min={1} onChange={(v) => setM('max_order_size', v)} />
      </Field>
      <Field label="MAX ORDER AGE (STEPS)" tip="Resting orders expire after this many steps.">
        <Num value={m.max_order_age} min={1} onChange={(v) => setM('max_order_age', v)} />
      </Field>
      <div className="dim small" style={{ gridColumn: '1 / -1' }}>
        Liquidity is not a dial: it emerges from the market-maker population
        you configure in the next step.
      </div>
    </div>
  );
}

function AgentsStep({ s, set }) {
  const total = s.agents.reduce((n, a) => n + Number(a.count || 0), 0);
  const update = (i, patch) => {
    const agents = s.agents.map((a, j) => (j === i ? { ...a, ...patch } : a));
    set({ ...s, agents });
  };
  const remove = (i) => set({ ...s, agents: s.agents.filter((_, j) => j !== i) });
  const duplicate = (i) => set({ ...s, agents: [...s.agents, { ...s.agents[i], params: { ...s.agents[i].params } }] });
  const add = () => set({
    ...s,
    agents: [...s.agents, { agent_type: 'noise_trader', count: 5, cash: 10000, inventory: 100, trading_frequency: 0.5, params: {} }],
  });
  return (
    <>
      <table className="table">
        <thead>
          <tr><th>TYPE</th><th>COUNT</th><th>CASH</th><th>INVENTORY</th><th>FREQ</th><th /></tr>
        </thead>
        <tbody>
          {s.agents.map((a, i) => (
            <tr key={i}>
              <td>
                <select value={a.agent_type}
                        onChange={(e) => update(i, { agent_type: e.target.value, params: {} })}>
                  {AGENT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </td>
              <td><Num value={a.count} min={1} max={500} onChange={(v) => update(i, { count: v })} /></td>
              <td><Num value={a.cash} min={0} onChange={(v) => update(i, { cash: v })} /></td>
              <td><Num value={a.inventory} min={0} onChange={(v) => update(i, { inventory: v })} /></td>
              <td><Num value={a.trading_frequency} min={0} max={1} step={0.05}
                       onChange={(v) => update(i, { trading_frequency: v })} /></td>
              <td>
                <button className="btn ghost" onClick={() => duplicate(i)}>DUP</button>{' '}
                <button className="btn ghost" onClick={() => remove(i)}
                        disabled={s.agents.length <= 1}>✗</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="actions">
        <button className="btn" onClick={add}>+ ADD AGENT GROUP</button>
        <button className="btn ghost" onClick={() => set({ ...s, agents: defaultState().agents })}>RESET</button>
      </div>
      <div className="section-gap" />
      <div className="kv">
        <span className="k">TOTAL AGENTS</span><span className="v big">{total}</span>
        {s.agents.map((a, i) => (
          <React.Fragment key={i}>
            <span className="k">{a.agent_type}</span>
            <span className="v">{a.count} ({total ? Math.round(100 * a.count / total) : 0}%)</span>
          </React.Fragment>
        ))}
      </div>
    </>
  );
}

function BehaviorStep({ s, set }) {
  const update = (i, key, v) => {
    const agents = s.agents.map((a, j) =>
      j === i ? { ...a, params: { ...a.params, [key]: v } } : a);
    set({ ...s, agents });
  };
  return (
    <>
      <div className="dim small">
        Every parameter below is a real engine parameter (nothing decorative).
        Unset parameters use the strategy defaults shown.
      </div>
      <div className="section-gap" />
      {s.agents.map((a, i) => {
        const params = BEHAVIOR_PARAMS[a.agent_type] || [];
        if (!params.length) return null;
        return (
          <div key={i} className="section-gap">
            <div className="label accent">{a.agent_type} × {a.count}</div>
            <div className="grid cols-3">
              {params.map((p) => (
                <Field key={p.key} label={p.label.toUpperCase()} tip={p.tip}>
                  <Num value={a.params[p.key] ?? p.def} min={p.min} max={p.max}
                       step={p.step} onChange={(v) => update(i, p.key, v)} />
                </Field>
              ))}
            </div>
          </div>
        );
      })}
    </>
  );
}

function RiskStep({ s, set }) {
  const r = s.risk;
  const setR = (k, v) => set({ ...s, risk: { ...r, [k]: v } });
  const lev = r.initial_margin > 0 ? (1 / r.initial_margin).toFixed(1) : '—';
  return (
    <>
      <div className="dim small">
        Synthetic market risk configuration — this parameterizes an
        experiment; nothing here touches real trading of any kind.
      </div>
      <div className="section-gap" />
      <Field label="MARGIN MECHANICS" tip="Off = strict cash accounting (legacy semantics).">
        <select value={r.enabled ? 'on' : 'off'}
                onChange={(e) => setR('enabled', e.target.value === 'on')}>
          <option value="off">DISABLED (no leverage)</option>
          <option value="on">ENABLED (margin buying + forced liquidation)</option>
        </select>
      </Field>
      {r.enabled && (
        <div className="grid cols-2 section-gap">
          <Field label={`INITIAL MARGIN (≈ ${lev}× LEVERAGE CAP)`}
                 tip="Minimum equity/position to admit new buys.">
            <Num value={r.initial_margin} min={0.05} max={1} step={0.05}
                 onChange={(v) => setR('initial_margin', v)} />
          </Field>
          <Field label="MAINTENANCE MARGIN" tip="Breach below this starts the margin-call clock.">
            <Num value={r.maintenance_margin} min={0.01} max={0.95} step={0.01}
                 onChange={(v) => setR('maintenance_margin', v)} />
          </Field>
          <Field label="LIQUIDATION DELAY (STEPS)" tip="Breach persistence before forced selling.">
            <Num value={r.liquidation_delay} min={0} max={20}
                 onChange={(v) => setR('liquidation_delay', v)} />
          </Field>
          <Field label="LIQUIDATION FRACTION" tip="Fraction of inventory force-sold per slice.">
            <Num value={r.liquidation_fraction} min={0.05} max={1} step={0.05}
                 onChange={(v) => setR('liquidation_fraction', v)} />
          </Field>
        </div>
      )}
    </>
  );
}

function ShocksStep({ s, set }) {
  const [draft, setDraft] = useState({ shock_type: 'whale_order', step: 400, side: 'sell', magnitude: 1000, duration: 10 });
  const add = () => set({ ...s, shocks: [...s.shocks, { ...draft }] });
  const remove = (i) => set({ ...s, shocks: s.shocks.filter((_, j) => j !== i) });
  const sorted = [...s.shocks].sort((a, b) => a.step - b.step);
  const T = Number(s.sim.total_steps) || 1;
  return (
    <>
      <div className="label">SHOCK SCHEDULE — STEP 0 ─ {s.sim.total_steps}</div>
      <div className="timeline">
        {sorted.length === 0 && <div className="dim small">No scheduled shocks. The market runs unperturbed.</div>}
        {sorted.map((sh, i) => (
          <div key={i} className="timeline-row">
            <span className="dim small" style={{ width: `${Math.min(92, 100 * sh.step / T)}%` }} />
            <span className="accent">▼ {sh.shock_type} @ t={sh.step}
              {sh.shock_type !== 'mm_withdrawal' ? ` · ${sh.side}` : ''} · mag {sh.magnitude} · {sh.duration} steps</span>
            <button className="btn ghost" onClick={() => remove(s.shocks.indexOf(sh))}>✗</button>
          </div>
        ))}
      </div>
      <div className="section-gap" />
      <div className="grid cols-4">
        <Field label="TYPE">
          <select value={draft.shock_type}
                  onChange={(e) => setDraft({ ...draft, shock_type: e.target.value })}>
            <option value="whale_order">whale_order</option>
            <option value="sentiment_shock">sentiment_shock</option>
            <option value="mm_withdrawal">mm_withdrawal</option>
          </select>
        </Field>
        <Field label="STEP"><Num value={draft.step} min={1} onChange={(v) => setDraft({ ...draft, step: v })} /></Field>
        {draft.shock_type !== 'mm_withdrawal' && (
          <Field label="DIRECTION">
            <select value={draft.side} onChange={(e) => setDraft({ ...draft, side: e.target.value })}>
              <option value="sell">sell</option><option value="buy">buy</option>
            </select>
          </Field>
        )}
        <Field label={draft.shock_type === 'sentiment_shock' ? 'MAGNITUDE (0–1)' : 'MAGNITUDE (SHARES)'}>
          <Num value={draft.magnitude} min={0.01} onChange={(v) => setDraft({ ...draft, magnitude: v })} />
        </Field>
        <Field label="DURATION (STEPS)"><Num value={draft.duration} min={1} onChange={(v) => setDraft({ ...draft, duration: v })} /></Field>
      </div>
      <button className="btn" onClick={add}>+ ADD SHOCK</button>
    </>
  );
}

function SimulationStep({ s, set }) {
  const sim = s.sim;
  const setSim = (k, v) => set({ ...s, sim: { ...sim, [k]: v } });
  const metrics = [...METRICS, ...(s.risk.enabled ? RISK_METRICS : [])];
  return (
    <div className="grid cols-2">
      <Field label="TOTAL STEPS" tip="Simulation horizon per replication.">
        <Num value={sim.total_steps} min={10} max={100000} onChange={(v) => setSim('total_steps', v)} />
      </Field>
      <Field label="REPLICATIONS" tip="Independent seeded runs; distributions, not anecdotes.">
        <Num value={sim.replications} min={1} max={500} onChange={(v) => setSim('replications', v)} />
      </Field>
      <Field label="ROOT SEED" tip="Deterministic per-replication seeds derive from this.">
        <Num value={sim.root_seed} min={0} onChange={(v) => setSim('root_seed', v)} />
      </Field>
      <Field label="PRIMARY METRIC" tip="Declared before running; analysis targets this first.">
        <select value={sim.primary_metric} onChange={(e) => setSim('primary_metric', e.target.value)}>
          {metrics.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </Field>
      <Field label="RESEARCH QUESTION" tip="Required: what are you asking?">
        <input value={s.question} onChange={(e) => set({ ...s, question: e.target.value })} />
      </Field>
      <Field label="HYPOTHESIS" tip="Required: what do you expect, stated in advance?">
        <input value={s.hypothesis} onChange={(e) => set({ ...s, hypothesis: e.target.value })} />
      </Field>
      <Field label="SCENARIO NAME">
        <input value={s.name} onChange={(e) => set({ ...s, name: e.target.value })} />
      </Field>
    </div>
  );
}

function ReviewStep({ s, preview, error }) {
  const spec = buildSpec(s);
  const total = s.agents.reduce((n, a) => n + Number(a.count || 0), 0);
  return (
    <>
      <div className="kv">
        <span className="k">SCENARIO</span><span className="v">{s.name}</span>
        <span className="k">MARKET</span>
        <span className="v">price {fmtNum(Number(s.market.initial_price), 2)} · tick {s.market.tick_size} · {s.sim.total_steps} steps</span>
        <span className="k">AGENTS ({total})</span>
        <span className="v">{s.agents.map((a) => `${a.agent_type}×${a.count}`).join(' · ')}</span>
        <span className="k">RISK</span>
        <span className="v">{s.risk.enabled
          ? `margin on · ≈${(1 / s.risk.initial_margin).toFixed(1)}× leverage cap · maintenance ${s.risk.maintenance_margin}`
          : 'strict cash (no leverage)'}</span>
        <span className="k">SHOCKS</span>
        <span className="v">{s.shocks.length
          ? [...s.shocks].sort((a, b) => a.step - b.step)
              .map((sh) => `${sh.shock_type}@${sh.step}`).join(' · ')
          : 'none'}</span>
        <span className="k">REPLICATIONS</span><span className="v">{s.sim.replications}</span>
        <span className="k">PRIMARY METRIC</span><span className="v accent">{s.sim.primary_metric}</span>
        <span className="k">QUESTION</span><span className="v">{s.question}</span>
        <span className="k">HYPOTHESIS</span><span className="v">{s.hypothesis}</span>
      </div>
      <div className="section-gap" />
      {error && <div className="neg small">VALIDATION FAILED: {error}</div>}
      {preview && (
        <div className="kv">
          <span className="k">RESEARCH IDENTITY</span>
          <span className="v accent">{preview.research_hash}</span>
          <span className="k">VERSION</span><span className="v">{preview.version_id}</span>
          <span className="k">PLANNED RUNS</span><span className="v">{preview.planned_runs}</span>
        </div>
      )}
      <details className="section-gap">
        <summary className="dim small">Canonical specification (exportable JSON)</summary>
        <pre className="report-md">{JSON.stringify(spec, null, 1)}</pre>
      </details>
    </>
  );
}

// ---- the wizard -----------------------------------------------------------

export default function Build() {
  const [s, set] = useState(defaultState);
  const [step, setStep] = useState(0);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState('');
  const [templates, setTemplates] = useState([]);
  const [drafts, setDrafts] = useState([]);
  const [savedId, setSavedId] = useState(null);
  const spec = useMemo(() => buildSpec(s), [s]);

  useEffect(() => {
    api.scenarioTemplates().then(setTemplates).catch(() => {});
    api.scenarios().then(setDrafts).catch(() => {});
  }, []);

  // Re-validate whenever the review step is shown.
  useEffect(() => {
    if (STEPS[step] !== 'REVIEW') return;
    setPreview(null); setError(null);
    api.scenarioValidate(spec)
      .then(setPreview)
      .catch((e) => setError(e.message));
  }, [step, spec]);

  const act = async (name, fn) => {
    setBusy(name); setError(null);
    try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(''); }
  };

  const save = () => act('save', async () => {
    const rec = savedId
      ? await api.scenarioUpdate(savedId, s.name, spec)
      : await api.scenarioSave(s.name, spec);
    setSavedId(rec.scenario_id);
    setDrafts(await api.scenarios());
  });

  const exportSpec = () => {
    const blob = new Blob([JSON.stringify(spec, null, 1)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${s.name.replace(/[^a-z0-9]+/gi, '_')}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const importSpec = (file) => {
    const reader = new FileReader();
    reader.onload = () => act('import', async () => {
      const parsed = JSON.parse(reader.result);
      await api.scenarioValidate(parsed);           // exact failures surface
      set(stateFromSpec(parsed, file.name.replace(/\.json$/, '')));
      setSavedId(null);
      setStep(STEPS.length - 1);
    });
    reader.readAsText(file);
  };

  const createExperiment = () => act('create', async () => {
    const record = await api.scenarioRegister(spec);
    navigate(`#/research/${record.version_id}`);
  });

  const loadTemplate = (t) => {
    set(stateFromSpec(t.spec, t.name));
    setSavedId(null);
    setStep(STEPS.length - 1);
  };

  const loadDraft = (d) => act('load', async () => {
    const rec = await api.scenarioGet(d.scenario_id);
    set(stateFromSpec(rec.spec, rec.name));
    setSavedId(rec.scenario_id);
    setStep(0);
  });

  const stepName = STEPS[step];
  return (
    <div className="grid">
      <Panel title="BUILD CUSTOM SCENARIO"
             sub="you are designing an experiment — every field maps onto the canonical schema">
        <div className="wizard-steps">
          {STEPS.map((name, i) => (
            <button key={name}
                    className={`wizstep ${i === step ? 'active' : ''} ${i < step ? 'done' : ''}`}
                    onClick={() => setStep(i)}>
              {i + 1}. {name}
            </button>
          ))}
        </div>
        <div className="section-gap" />
        {stepName === 'MARKET' && <MarketStep s={s} set={set} />}
        {stepName === 'AGENTS' && <AgentsStep s={s} set={set} />}
        {stepName === 'BEHAVIOR' && <BehaviorStep s={s} set={set} />}
        {stepName === 'RISK' && <RiskStep s={s} set={set} />}
        {stepName === 'SHOCKS' && <ShocksStep s={s} set={set} />}
        {stepName === 'SIMULATION' && <SimulationStep s={s} set={set} />}
        {stepName === 'REVIEW' && <ReviewStep s={s} preview={preview} error={error} />}
        <div className="section-gap" />
        <div className="actions">
          <button className="btn ghost" disabled={step === 0} onClick={() => setStep(step - 1)}>← BACK</button>
          {step < STEPS.length - 1 && (
            <button className="btn" onClick={() => setStep(step + 1)}>NEXT →</button>
          )}
          {stepName === 'REVIEW' && (
            <>
              <button className="btn ghost" disabled={busy !== ''} onClick={save}>
                {busy === 'save' ? 'SAVING…' : savedId ? 'UPDATE DRAFT' : 'SAVE DRAFT'}
              </button>
              <button className="btn ghost" onClick={exportSpec}>EXPORT JSON</button>
              <button className="btn" disabled={busy !== '' || !preview}
                      onClick={createExperiment}>
                {busy === 'create' ? 'CREATING…' : 'CREATE EXPERIMENT'}
              </button>
            </>
          )}
        </div>
        {error && stepName !== 'REVIEW' && <div className="neg small section-gap">{error}</div>}
      </Panel>

      <Panel title="TRY AN IDEA" sub="templates — full canonical specs, editable before you run" tight>
        <div className="grid cols-2">
          {templates.map((t) => (
            <div key={t.template_id} className="card">
              <span className="card-name">{t.name}</span>
              <div className="desc">{t.description}</div>
              <button className="btn" onClick={() => loadTemplate(t)}>LOAD</button>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="SAVED DRAFTS & IMPORT" tight>
        <div className="actions">
          <label className="btn ghost">
            IMPORT SPEC JSON
            <input type="file" accept=".json" style={{ display: 'none' }}
                   onChange={(e) => e.target.files[0] && importSpec(e.target.files[0])} />
          </label>
        </div>
        <table className="table">
          <tbody>
            {drafts.map((d) => (
              <tr key={d.scenario_id}>
                <td className="accent">{d.scenario_id}</td>
                <td>{d.name}</td>
                <td className="dim small">{d.updated_at}</td>
                <td>
                  <button className="btn ghost" onClick={() => loadDraft(d)}>LOAD</button>{' '}
                  <button className="btn ghost" onClick={() => act('dup', async () => {
                    await api.scenarioDuplicate(d.scenario_id);
                    setDrafts(await api.scenarios());
                  })}>DUP</button>{' '}
                  <button className="btn ghost" onClick={() => act('del', async () => {
                    await api.scenarioDelete(d.scenario_id);
                    setDrafts(await api.scenarios());
                  })}>✗</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}
