import React, { useEffect, useState } from 'react';
import { api, asArray } from '../api/client';
import { EmptyRow, Panel, StatTile, navigate } from '../components/ui.jsx';

// TradeOps (Phase S2): the operational layer around experiment execution.
// Research asks the question; this page runs the laboratory. Every number
// is real runtime state from /api/ops — nothing is fabricated.

const STATE_CLASS = {
  QUEUED: 'accent', RUNNING: 'pos', COMPLETED: '', FAILED: 'neg', CANCELLED: 'dim',
};

function pct(job) {
  if (!job.planned) return '—';
  return `${Math.round(100 * (job.completed || 0) / job.planned)}%`;
}

export default function Ops() {
  const [status, setStatus] = useState(null);
  const [workers, setWorkers] = useState([]);
  const [jobs, setJobs] = useState(null);
  const [err, setErr] = useState(null);
  const [notice, setNotice] = useState(null);

  const refresh = async () => {
    try {
      const [st, wk, jb] = await Promise.all([
        api.opsStatus(), api.opsWorkers(), api.opsJobs()]);
      setStatus(st); setWorkers(wk); setJobs(jb); setErr(null);
    } catch (e) { setErr(e.message); }
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 1500);
    return () => clearInterval(t);
  }, []);

  const act = async (fn) => {
    setNotice(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      // Duplicate guard and limits arrive as structured 409/429 details.
      const detail = e.message && typeof e.message === 'object' ? e.message : null;
      setNotice(detail ? JSON.stringify(detail) : String(e.message));
    }
  };

  return (
    <div className="grid">
      <Panel title="TRADEOPS — SYSTEM STATUS"
             sub={status ? `data dir: ${status.data_dir} · chunk size: ${status.chunk_size}` : ''}>
        {err && <div className="neg small">{err}</div>}
        {status && (
          <div className="grid cols-4">
            <StatTile label="WORKERS" value={`${status.workers_busy} / ${status.workers_total}`} />
            <StatTile label="CAPACITY" value={`${status.capacity_pct}%`} />
            <StatTile label="QUEUED" value={String(status.queued)} />
            <StatTile label="RUNNING" value={String(status.running)} />
            <StatTile label="COMPLETED" value={String(status.completed)} />
            <StatTile label="FAILED" value={String(status.failed)}
                      tone={status.failed ? 'neg' : undefined} />
            <StatTile label="CANCELLED" value={String(status.cancelled)} />
          </div>
        )}
        {status && (
          <div className="dim small section-gap">
            Operator limits: max queue {status.limits.max_queue} · max
            replications {status.limits.max_replications} · max steps{' '}
            {status.limits.max_steps} · max planned runs{' '}
            {status.limits.max_planned_runs}. Limits act at the submission
            boundary only — experiment semantics are never modified.
          </div>
        )}
        {notice && <div className="accent small section-gap">{notice}</div>}
      </Panel>

      <Panel title="WORKERS" tight>
        <table className="table">
          <thead>
            <tr><th>WORKER</th><th>STATE</th><th>CURRENT JOB</th><th>PROGRESS</th><th>STARTED</th></tr>
          </thead>
          <tbody>
            {workers.length === 0 && <EmptyRow cols={5} text="NO WORKERS" />}
            {workers.map((w) => (
              <tr key={w.worker_id}>
                <td className="accent">{w.worker_id}</td>
                <td className={w.state === 'busy' ? 'pos' : 'dim'}>{w.state.toUpperCase()}</td>
                <td>{w.job_id || '—'}</td>
                <td>{w.progress ? `${w.progress.completed} / ${w.progress.planned}` : '—'}</td>
                <td className="dim small">{w.started_at || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <Panel title="JOB QUEUE" sub="queued · running · completed · failed · cancelled" tight>
        <table className="table">
          <thead>
            <tr>
              <th>JOB</th><th>EXPERIMENT</th><th>STATE</th><th>RUNS</th>
              <th>%</th><th>WORKER</th><th>SUBMITTED</th><th />
            </tr>
          </thead>
          <tbody>
            {jobs === null && <EmptyRow cols={8} text="LOADING…" />}
            {jobs !== null && jobs.length === 0 && (
              <EmptyRow cols={8}
                        text="QUEUE EMPTY — submit an experiment from a RESEARCH page or the scenario builder" />
            )}
            {asArray(jobs).map((j) => (
              <tr key={j.job_id}>
                <td>{j.job_id}</td>
                <td className="accent clickable"
                    onClick={() => navigate(`#/research/${j.version_id}`)}>
                  {j.name || j.version_id}
                </td>
                <td className={STATE_CLASS[j.state] || ''}>{j.state}</td>
                <td>{j.completed} / {j.planned}</td>
                <td>{pct(j)}</td>
                <td className="dim small">{j.worker_id || '—'}</td>
                <td className="dim small">{j.submitted_at}</td>
                <td>
                  {(j.state === 'QUEUED' || j.state === 'RUNNING') && (
                    <button className="btn ghost"
                            onClick={() => act(() => api.opsCancel(j.job_id))}>CANCEL</button>
                  )}
                  {(j.state === 'FAILED' || j.state === 'CANCELLED') && (
                    <button className="btn ghost"
                            onClick={() => act(() => api.opsRetry(j.job_id))}>RETRY</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {asArray(jobs).filter((j) => j.state === 'FAILED').map((j) => (
          <div key={j.job_id} className="neg small section-gap">
            ✗ {j.job_id} FAILED — {j.error || 'unknown error'} · rows persisted:{' '}
            {j.completed}/{j.planned} · RESUMABLE (retry continues from
            persisted rows with identical seeds)
          </div>
        ))}
      </Panel>
    </div>
  );
}
