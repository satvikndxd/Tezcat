import React, { useEffect, useMemo, useRef, useState } from 'react';
import { api, asArray, fmtNum, fmtInt } from '../api/client';
import {
  Panel,
  StatusBadge,
  RegimeBadge,
  ProgressBar,
  EmptyRow,
} from '../components/ui.jsx';
import LineChart from '../components/LineChart.jsx';
import AreaChart from '../components/AreaChart.jsx';
import DepthChart from '../components/DepthChart.jsx';

const TERMINAL = ['completed', 'failed', 'cancelled'];
const POLL_MS = 1000;
const VOL_WINDOW = 20;
const MAX_RENDER_POINTS = 1500;

function downsample(points) {
  if (points.length <= MAX_RENDER_POINTS) return points;
  const stride = Math.ceil(points.length / MAX_RENDER_POINTS);
  const out = [];
  for (let i = 0; i < points.length; i += stride) out.push(points[i]);
  if (out[out.length - 1] !== points[points.length - 1]) {
    out.push(points[points.length - 1]);
  }
  return out;
}

function ShockPanel({ runId, disabled, onFired }) {
  const [shockType, setShockType] = useState('whale_order');
  const [side, setSide] = useState('sell');
  const [magnitude, setMagnitude] = useState('1.0');
  const [duration, setDuration] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  const sideRelevant = shockType === 'whale_order';

  const fire = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const body = {
        shock_type: shockType,
        side: sideRelevant ? side : null,
        magnitude: Number(magnitude) || 0,
        duration: duration.trim() !== '' && !isNaN(Number(duration)) ? Number(duration) : null,
      };
      await api.injectShock(runId, body);
      setMsg('QUEUED');
      if (onFired) onFired();
      setTimeout(() => setMsg(null), 2500);
    } catch (err) {
      setMsg(`ERR: ${err.message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="INJECT SHOCK" sub={msg}>
      <div className="toolbar">
        <div className="field">
          <span className="label">Type</span>
          <select className="inp" value={shockType} onChange={(e) => setShockType(e.target.value)}>
            <option value="whale_order">whale_order</option>
            <option value="mm_withdrawal">mm_withdrawal</option>
            <option value="sentiment_shock">sentiment_shock</option>
          </select>
        </div>
        <div className="field">
          <span className="label">Side</span>
          <select
            className="inp"
            value={side}
            disabled={!sideRelevant}
            onChange={(e) => setSide(e.target.value)}
          >
            <option value="sell">sell</option>
            <option value="buy">buy</option>
          </select>
        </div>
        <div className="field">
          <span className="label">Magnitude</span>
          <input
            className="inp"
            style={{ width: 90 }}
            value={magnitude}
            inputMode="decimal"
            onChange={(e) => setMagnitude(e.target.value)}
          />
        </div>
        <div className="field">
          <span className="label">Duration</span>
          <input
            className="inp"
            style={{ width: 80 }}
            value={duration}
            placeholder="auto"
            inputMode="numeric"
            onChange={(e) => setDuration(e.target.value)}
          />
        </div>
        <button className="btn danger" disabled={disabled || busy} onClick={fire}>
          {busy ? 'FIRING…' : 'FIRE'}
        </button>
      </div>
    </Panel>
  );
}

export default function Run({ id }) {
  const [run, setRun] = useState(null);
  const [snapshots, setSnapshots] = useState([]);
  const [trades, setTrades] = useState([]);
  const [shockEvents, setShockEvents] = useState([]);
  const [regimeEvents, setRegimeEvents] = useState([]);
  const [market, setMarket] = useState(null);
  const [error, setError] = useState(null);
  const [actionErr, setActionErr] = useState(null);

  const nextStartRef = useRef(0);
  const stoppedRef = useRef(false);

  useEffect(() => {
    nextStartRef.current = 0;
    stoppedRef.current = false;
    let alive = true;
    let timer = null;

    const tick = async () => {
      if (!alive || stoppedRef.current) return;
      let currentRun = null;
      try {
        currentRun = await api.run(id);
        if (!alive) return;
        if (currentRun) {
          setRun(currentRun);
          setError(null);
        }
      } catch (err) {
        if (!alive) return;
        setError(err.status === 404 ? 'RUN NOT FOUND' : `run fetch failed: ${err.message}`);
        if (err.status === 404) {
          stoppedRef.current = true;
          return;
        }
      }

      const results = await Promise.allSettled([
        api.history(id, nextStartRef.current),
        api.trades(id, 100),
        api.runShocks(id),
        api.runRegimes(id),
        api.market(id),
      ]);
      if (!alive) return;
      const [h, t, s, rg, m] = results;
      if (h.status === 'fulfilled' && h.value) {
        const snaps = asArray(h.value.snapshots);
        if (snaps.length > 0) {
          setSnapshots((prev) => prev.concat(snaps));
        }
        if (typeof h.value.next_start === 'number') {
          nextStartRef.current = h.value.next_start;
        } else {
          nextStartRef.current += snaps.length;
        }
      }
      if (t.status === 'fulfilled' && t.value) setTrades(asArray(t.value.trades));
      if (s.status === 'fulfilled') setShockEvents(asArray(s.value));
      if (rg.status === 'fulfilled') setRegimeEvents(asArray(rg.value));
      if (m.status === 'fulfilled' && m.value) setMarket(m.value);

      // Stop polling once terminal (this tick already fetched the final state).
      if (currentRun && TERMINAL.includes(currentRun.status)) {
        stoppedRef.current = true;
      }
    };

    const loop = async () => {
      await tick();
      if (alive && !stoppedRef.current) {
        timer = setTimeout(loop, POLL_MS);
      }
    };
    loop();

    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [id]);

  // Resume polling after a control action changes state.
  const applyRunUpdate = (updated) => {
    if (updated) setRun(updated);
    if (updated && !TERMINAL.includes(updated.status) && stoppedRef.current) {
      // status went back to non-terminal — reload page state via hash refresh
      stoppedRef.current = false;
    }
  };

  const doAction = async (fn) => {
    setActionErr(null);
    try {
      const updated = await fn();
      applyRunUpdate(updated);
    } catch (err) {
      setActionErr(err.message);
    }
  };

  // ---- derived series ----------------------------------------------------

  const series = useMemo(() => {
    const price = [];
    const spread = [];
    const volume = [];
    const vol = [];
    const logrets = [];
    let prevPrice = null;
    for (const s of snapshots) {
      if (!s || typeof s.step !== 'number') continue;
      const p = s.last_price;
      if (typeof p === 'number' && isFinite(p)) {
        price.push({ x: s.step, y: p });
        if (prevPrice != null && prevPrice > 0 && p > 0) {
          logrets.push(Math.log(p / prevPrice));
        } else {
          logrets.push(0);
        }
        if (logrets.length >= VOL_WINDOW) {
          const win = logrets.slice(-VOL_WINDOW);
          const mean = win.reduce((a, b) => a + b, 0) / win.length;
          const varc = win.reduce((a, b) => a + (b - mean) * (b - mean), 0) / win.length;
          vol.push({ x: s.step, y: Math.sqrt(varc) });
        }
        prevPrice = p;
      }
      if (typeof s.spread === 'number' && isFinite(s.spread)) {
        spread.push({ x: s.step, y: s.spread });
      }
      if (typeof s.volume === 'number' && isFinite(s.volume)) {
        volume.push({ x: s.step, y: s.volume });
      }
    }
    return {
      price: downsample(price),
      spread: downsample(spread),
      volume: downsample(volume),
      vol: downsample(vol),
    };
  }, [snapshots]);

  const regimeBands = useMemo(() => {
    const bands = [];
    let cur = null;
    for (const s of snapshots) {
      if (!s || typeof s.step !== 'number') continue;
      const r = s.regime || 'stable';
      if (cur && cur.regime === r) {
        cur.x1 = s.step;
      } else {
        if (cur) bands.push(cur);
        cur = { regime: r, x0: s.step, x1: s.step };
      }
    }
    if (cur) bands.push(cur);
    return bands.filter((b) => b.regime !== 'stable');
  }, [snapshots]);

  const shockMarkers = useMemo(
    () =>
      shockEvents
        .filter((e) => e && typeof e.step === 'number')
        .map((e) => ({
          step: e.step,
          label: String(e.shock_type || 'shock').toUpperCase().slice(0, 10),
        })),
    [shockEvents]
  );

  const lastPrice =
    market?.snapshot?.last_price ??
    (series.price.length > 0 ? series.price[series.price.length - 1].y : null);
  const prevPrice =
    series.price.length > 1 ? series.price[series.price.length - 2].y : null;
  const priceTone =
    lastPrice != null && prevPrice != null
      ? lastPrice > prevPrice
        ? 'pos'
        : lastPrice < prevPrice
        ? 'neg'
        : ''
      : '';

  const status = run?.status || 'unknown';
  const live = ['pending', 'running', 'paused'].includes(status);
  const tape = trades.slice(-30).reverse();

  return (
    <div>
      <div className="label" style={{ marginBottom: 8 }}>
        <a href="#/">HOME</a>
        {run?.experiment_id ? (
          <>
            {' / '}
            <a href={`#/experiment/${run.experiment_id}`}>EXPERIMENT</a>
          </>
        ) : null}
        {' / RUN'}
      </div>
      {error && <div className="err" style={{ marginBottom: 10 }}>{error}</div>}

      {/* header */}
      <div className="run-header">
        <div className="cell">
          <span className="label">Run</span>
          <span className="val">{id}</span>
          <span className="dim small">{run?.experiment_name || '—'}</span>
        </div>
        <div className="cell">
          <span className="label">Status</span>
          <StatusBadge status={status} />
        </div>
        <div className="cell">
          <span className="label">Regime</span>
          <RegimeBadge regime={run?.current_regime} />
        </div>
        <div className="cell" style={{ minWidth: 180 }}>
          <span className="label">
            Step {fmtInt(run?.current_step)} / {fmtInt(run?.total_steps)}
          </span>
          <ProgressBar value={run?.current_step} max={run?.total_steps} />
        </div>
        <div className="cell">
          <span className="label">Seed</span>
          <span className="val">{run?.seed != null ? String(run.seed) : '—'}</span>
        </div>
        <div className="cell" style={{ marginLeft: 'auto', textAlign: 'right' }}>
          <span className="label">Last Price</span>
          <span className={`val huge ${priceTone}`}>
            {lastPrice != null ? fmtNum(lastPrice, 2) : '—'}
          </span>
        </div>
      </div>

      {/* controls */}
      <div className="grid cols-2 section-gap" style={{ alignItems: 'stretch' }}>
        <Panel title="CONTROLS" sub={actionErr ? `ERR: ${actionErr}` : null}>
          <div className="toolbar">
            <button
              className="btn"
              disabled={status !== 'running'}
              onClick={() => doAction(() => api.pauseRun(id))}
            >
              PAUSE
            </button>
            <button
              className="btn"
              disabled={status !== 'paused'}
              onClick={() => doAction(() => api.resumeRun(id))}
            >
              RESUME
            </button>
            <button
              className="btn danger"
              disabled={!live}
              onClick={() => doAction(() => api.cancelRun(id))}
            >
              CANCEL
            </button>
            {status === 'completed' && (
              <a href={`#/run/${id}/report`} style={{ marginLeft: 'auto' }}>
                <button className="btn big">VIEW REPORT →</button>
              </a>
            )}
          </div>
        </Panel>
        <ShockPanel runId={id} disabled={!live} />
      </div>

      {/* main price chart */}
      <div className="section-gap">
        <Panel
          title="PRICE"
          sub={`${series.price.length} pts · shocks ${shockMarkers.length}`}
          tight
        >
          <LineChart
            points={series.price}
            shocks={shockMarkers}
            regimeBands={regimeBands}
            height={280}
            yLabel="LAST PRICE"
          />
        </Panel>
      </div>

      {/* small charts */}
      <div className="grid cols-3 section-gap">
        <Panel title="SPREAD" tight>
          <AreaChart
            points={series.spread}
            color="#fb8b1e"
            fill="rgba(251,139,30,0.12)"
          />
        </Panel>
        <Panel title="ROLLING VOLATILITY" sub={`w=${VOL_WINDOW}`} tight>
          <AreaChart
            points={series.vol}
            color="#ff433d"
            fill="rgba(255,67,61,0.10)"
          />
        </Panel>
        <Panel title="VOLUME / STEP" tight>
          <AreaChart points={series.volume} mode="bars" color="#4a6a8a" />
        </Panel>
      </div>

      {/* depth + tape */}
      <div className="grid cols-2 section-gap">
        <Panel
          title="ORDER BOOK DEPTH"
          sub={
            market?.snapshot
              ? `bid ${fmtNum(market.snapshot.best_bid)} / ask ${fmtNum(market.snapshot.best_ask)} · spr ${fmtNum(market.snapshot.spread)}`
              : null
          }
          tight
        >
          <DepthChart
            bids={market?.bids}
            asks={market?.asks}
            mid={market?.snapshot?.mid_price}
          />
        </Panel>

        <Panel title="TRADE TAPE" sub={`last ${tape.length}`} tight>
          <div style={{ maxHeight: 260, overflowY: 'auto' }}>
            <table className="t">
              <thead>
                <tr>
                  <th>Step</th>
                  <th>Price</th>
                  <th>Qty</th>
                  <th className="l">Buyer</th>
                  <th className="l">Seller</th>
                </tr>
              </thead>
              <tbody>
                {tape.length === 0 ? (
                  <EmptyRow cols={5} text="NO TRADES" />
                ) : (
                  tape.map((tr, i) => {
                    // tape is reversed (newest first); previous trade is i+1
                    const prev = tape[i + 1];
                    const tone =
                      prev && typeof tr.price === 'number' && typeof prev.price === 'number'
                        ? tr.price > prev.price
                          ? 'pos'
                          : tr.price < prev.price
                          ? 'neg'
                          : ''
                        : '';
                    return (
                      <tr key={tr.trade_id || i}>
                        <td className="dim">{fmtInt(tr.step)}</td>
                        <td className={tone}>{fmtNum(tr.price, 2)}</td>
                        <td>{fmtInt(tr.quantity)}</td>
                        <td className="l dim">{tr.buy_agent_type || tr.buy_agent_id || '—'}</td>
                        <td className="l dim">{tr.sell_agent_type || tr.sell_agent_id || '—'}</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      {/* events */}
      <div className="grid cols-2 section-gap">
        <Panel title="SHOCK EVENTS" sub={`${shockEvents.length}`} tight>
          <table className="t">
            <thead>
              <tr>
                <th>Step</th>
                <th className="l">Type</th>
                <th className="l">Trigger</th>
                <th className="l">Payload</th>
                <th>Px Before</th>
                <th>Px After</th>
              </tr>
            </thead>
            <tbody>
              {shockEvents.length === 0 ? (
                <EmptyRow cols={6} text="NO SHOCKS FIRED" />
              ) : (
                shockEvents.map((e, i) => (
                  <tr key={e.event_id || i}>
                    <td>{fmtInt(e.step)}</td>
                    <td className="l accent">{e.shock_type || '—'}</td>
                    <td className="l dim">{e.trigger_reason || '—'}</td>
                    <td className="l dim small">
                      {e.payload && typeof e.payload === 'object'
                        ? Object.entries(e.payload)
                            .map(([k, v]) => `${k}=${v}`)
                            .join(' ')
                        : '—'}
                    </td>
                    <td>{fmtNum(e.market_before?.last_price)}</td>
                    <td
                      className={
                        typeof e.market_after?.last_price === 'number' &&
                        typeof e.market_before?.last_price === 'number'
                          ? e.market_after.last_price < e.market_before.last_price
                            ? 'neg'
                            : 'pos'
                          : ''
                      }
                    >
                      {fmtNum(e.market_after?.last_price)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </Panel>

        <Panel title="REGIME TRANSITIONS" sub={`${regimeEvents.length}`} tight>
          <table className="t">
            <thead>
              <tr>
                <th>Step</th>
                <th className="l">Transition</th>
                <th className="l">Reason</th>
              </tr>
            </thead>
            <tbody>
              {regimeEvents.length === 0 ? (
                <EmptyRow cols={3} text="NO TRANSITIONS" />
              ) : (
                regimeEvents.map((e, i) => (
                  <tr key={e.event_id || i}>
                    <td>{fmtInt(e.step)}</td>
                    <td className="l">
                      <span className="dim">{e.previous_regime || '?'}</span>
                      {' → '}
                      <span
                        className={
                          e.new_regime === 'crisis'
                            ? 'neg'
                            : e.new_regime === 'recovery'
                            ? 'pos'
                            : ''
                        }
                      >
                        {e.new_regime || '?'}
                      </span>
                    </td>
                    <td className="l dim small">{e.reason || '—'}</td>
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
