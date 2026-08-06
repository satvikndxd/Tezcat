import React, { useEffect, useState } from 'react';
import { api, asArray, fmtNum, fmtInt, fmtPct } from '../api/client';
import { Panel, StatTile, Signed, EmptyRow } from '../components/ui.jsx';

const REGIME_BAR_COLORS = {
  stable: '#3a3a3a',
  crisis: 'rgba(255,67,61,0.65)',
  recovery: 'rgba(74,246,195,0.55)',
};

export default function Report({ id }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);
  const [exporting, setExporting] = useState(false);
  const [exportResult, setExportResult] = useState(null);
  const [exportErr, setExportErr] = useState(null);

  useEffect(() => {
    let alive = true;
    api.report(id).then(
      (r) => alive && setReport(r || null),
      (err) => {
        if (!alive) return;
        setError(
          err.status === 404
            ? 'REPORT NOT AVAILABLE — RUN NOT COMPLETED YET'
            : `report fetch failed: ${err.message}`
        );
      }
    );
    return () => {
      alive = false;
    };
  }, [id]);

  const doExport = async () => {
    setExporting(true);
    setExportErr(null);
    try {
      const res = await api.exportRun(id);
      setExportResult(res || {});
    } catch (err) {
      setExportErr(err.message);
    } finally {
      setExporting(false);
    }
  };

  const pnl =
    report && report.agent_pnl_by_type && typeof report.agent_pnl_by_type === 'object'
      ? Object.entries(report.agent_pnl_by_type)
      : [];
  const share =
    report && report.regime_step_share && typeof report.regime_step_share === 'object'
      ? Object.entries(report.regime_step_share).filter(
          ([, v]) => typeof v === 'number' && v > 0
        )
      : [];

  return (
    <div>
      <div className="label" style={{ marginBottom: 8 }}>
        <a href="#/">HOME</a> / <a href={`#/run/${id}`}>RUN</a> / REPORT
      </div>

      {!report ? (
        <div className="panel">
          <div className="msg">{error || 'LOADING…'}</div>
        </div>
      ) : (
        <>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'baseline',
              marginBottom: 10,
            }}
          >
            <span className="val big accent">
              RUN REPORT <span className="dim small">{report.report_id || id}</span>
            </span>
            <button className="btn" disabled={exporting} onClick={doExport}>
              {exporting ? 'EXPORTING…' : 'EXPORT'}
            </button>
          </div>

          {exportErr && <div className="err" style={{ marginBottom: 10 }}>export failed: {exportErr}</div>}
          {exportResult && (
            <Panel
              title="EXPORT ARTIFACTS"
              sub={exportResult.export_id || null}
              className="section-gap"
            >
              {asArray(exportResult.files).length === 0 ? (
                <span className="dim small">NO FILES RETURNED</span>
              ) : (
                <div className="small">
                  {asArray(exportResult.files).map((f, i) => (
                    <div key={i}>{String(f)}</div>
                  ))}
                  {exportResult.s3_prefix && (
                    <div className="dim" style={{ marginTop: 6 }}>
                      s3: {String(exportResult.s3_prefix)}
                    </div>
                  )}
                </div>
              )}
            </Panel>
          )}

          <div className="tiles section-gap">
            <StatTile label="Final Price" value={fmtNum(report.final_price, 2)} />
            <StatTile
              label="Total Return"
              value={<Signed v={report.total_return} fmt={(v) => fmtPct(v)} />}
            />
            <StatTile
              label="Realized Volatility"
              value={fmtNum(report.realized_volatility, 4)}
            />
            <StatTile
              label="Max Drawdown"
              value={
                typeof report.max_drawdown === 'number' ? (
                  <span className={report.max_drawdown > 0.05 ? 'neg' : ''}>
                    {fmtPct(report.max_drawdown)}
                  </span>
                ) : (
                  '—'
                )
              }
            />
            <StatTile label="Average Spread" value={fmtNum(report.average_spread, 3)} />
            <StatTile label="Total Volume" value={fmtInt(report.total_volume)} />
            <StatTile label="Total Trades" value={fmtInt(report.total_trades)} />
            <StatTile
              label="Crash Detected"
              value={report.crash_detected ? 'YES' : 'NO'}
              tone={report.crash_detected ? 'neg' : 'pos'}
            />
            <StatTile
              label="Liquidity Crisis"
              value={report.liquidity_crisis_detected ? 'YES' : 'NO'}
              tone={report.liquidity_crisis_detected ? 'neg' : 'pos'}
            />
            <StatTile label="Shocks Fired" value={fmtInt(report.shock_count)} />
            <StatTile
              label="Regime Transitions"
              value={fmtInt(report.regime_transition_count)}
            />
            <StatTile label="Initial Price" value={fmtNum(report.initial_price, 2)} />
          </div>

          <div className="grid cols-2 section-gap">
            <Panel title="AGENT PNL BY TYPE" sub={`${pnl.length} types`} tight>
              <table className="t">
                <thead>
                  <tr>
                    <th className="l">Type</th>
                    <th>Agents</th>
                    <th>Realized</th>
                    <th>Unrealized</th>
                    <th>Total</th>
                    <th>Final Cash</th>
                    <th>Inventory</th>
                  </tr>
                </thead>
                <tbody>
                  {pnl.length === 0 ? (
                    <EmptyRow cols={7} text="NO PNL DATA" />
                  ) : (
                    pnl.map(([type, p]) => (
                      <tr key={type}>
                        <td className="l accent">{type}</td>
                        <td>{fmtInt(p?.agents)}</td>
                        <td>
                          <Signed v={p?.realized_pnl} fmt={(v) => fmtNum(v, 1)} />
                        </td>
                        <td>
                          <Signed v={p?.unrealized_pnl} fmt={(v) => fmtNum(v, 1)} />
                        </td>
                        <td>
                          <Signed v={p?.total_pnl} fmt={(v) => fmtNum(v, 1)} />
                        </td>
                        <td>{fmtNum(p?.final_cash, 1)}</td>
                        <td>{fmtInt(p?.final_inventory)}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </Panel>

            <Panel title="REGIME STEP SHARE">
              {share.length === 0 ? (
                <span className="dim small">NO REGIME DATA</span>
              ) : (
                <>
                  <div className="share-bar">
                    {share.map(([regime, v]) => (
                      <div
                        key={regime}
                        title={`${regime} ${fmtPct(v, 1)}`}
                        style={{
                          width: `${Math.max(0.5, v * 100)}%`,
                          background: REGIME_BAR_COLORS[regime] || '#555',
                        }}
                      />
                    ))}
                  </div>
                  <div className="share-legend">
                    {share.map(([regime, v]) => (
                      <span key={regime}>
                        <span
                          className="sw"
                          style={{ background: REGIME_BAR_COLORS[regime] || '#555' }}
                        />
                        {regime} {fmtPct(v, 1).replace('+', '')}
                      </span>
                    ))}
                  </div>
                </>
              )}
            </Panel>
          </div>
        </>
      )}
      <div className="footer-links">
        <a href={`#/run/${id}`}>← BACK TO RUN</a>
      </div>
    </div>
  );
}
