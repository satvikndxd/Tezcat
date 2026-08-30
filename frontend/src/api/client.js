// Tezcat REST client. All endpoints under /api (same origin, proxied in dev).
// Every helper is defensive: network failures reject with ApiError, and the
// connection status is broadcast so the status bar can show ONLINE / OFFLINE.

const configuredBase = (import.meta.env.VITE_API_BASE_URL || '').trim().replace(/\/+$/, '');
const BASE = configuredBase
  ? (configuredBase.endsWith('/api') ? configuredBase : `${configuredBase}/api`)
  : '/api';

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status || 0;
  }
}

// ---- connection status pub/sub -------------------------------------------

let connectionOk = null; // null = unknown, true = online, false = offline
const listeners = new Set();

export function getConnectionStatus() {
  return connectionOk;
}

export function onConnectionChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function setConnection(ok) {
  if (connectionOk !== ok) {
    connectionOk = ok;
    listeners.forEach((fn) => {
      try {
        fn(ok);
      } catch {
        /* listener errors are not our problem */
      }
    });
  }
}

// ---- core fetch ----------------------------------------------------------

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(BASE + path, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
  } catch (err) {
    setConnection(false);
    throw new ApiError(`network error: ${err.message}`, 0);
  }
  setConnection(true);
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    const detail = body && body.detail ? body.detail : `HTTP ${res.status}`;
    throw new ApiError(detail, res.status);
  }
  return body;
}

export function apiGet(path) {
  return request(path, { method: 'GET' });
}

export function apiPost(path, body) {
  return request(path, {
    method: 'POST',
    body: JSON.stringify(body == null ? {} : body),
  });
}

// ---- endpoint helpers (all tolerate missing fields downstream) -----------

export const api = {
  presets: () => apiGet('/presets'),
  preset: (id) => apiGet(`/presets/${encodeURIComponent(id)}`),
  createExperimentFromPreset: (id, body) =>
    apiPost(`/presets/${encodeURIComponent(id)}/experiments`, body || {}),

  experiments: () => apiGet('/experiments'),
  experiment: (id) => apiGet(`/experiments/${encodeURIComponent(id)}`),
  experimentShocks: (id) => apiGet(`/experiments/${encodeURIComponent(id)}/shocks`),
  startRun: (experimentId, body) =>
    apiPost(`/experiments/${encodeURIComponent(experimentId)}/runs`, body || {}),

  runs: () => apiGet('/runs'),
  run: (id) => apiGet(`/runs/${encodeURIComponent(id)}`),
  pauseRun: (id) => apiPost(`/runs/${encodeURIComponent(id)}/pause`),
  resumeRun: (id) => apiPost(`/runs/${encodeURIComponent(id)}/resume`),
  cancelRun: (id) => apiPost(`/runs/${encodeURIComponent(id)}/cancel`),

  injectShock: (id, body) => apiPost(`/runs/${encodeURIComponent(id)}/shocks`, body),
  runShocks: (id) => apiGet(`/runs/${encodeURIComponent(id)}/shocks`),
  runRegimes: (id) => apiGet(`/runs/${encodeURIComponent(id)}/regimes`),

  market: (id) => apiGet(`/runs/${encodeURIComponent(id)}/market`),
  history: (id, start = 0, limit = 5000) =>
    apiGet(`/runs/${encodeURIComponent(id)}/history?start=${start}&limit=${limit}`),
  trades: (id, limit = 100) =>
    apiGet(`/runs/${encodeURIComponent(id)}/trades?limit=${limit}`),
  metrics: (id, start = 0, limit = 5000) =>
    apiGet(`/runs/${encodeURIComponent(id)}/metrics?start=${start}&limit=${limit}`),
  report: (id) => apiGet(`/runs/${encodeURIComponent(id)}/report`),
  exportRun: (id) => apiPost(`/runs/${encodeURIComponent(id)}/export`),

  // Research API (Phase F10): register -> batch -> analyze -> report -> reproduce
  researchList: () => apiGet('/research/experiments'),
  researchGet: (ref) => apiGet(`/research/experiments/${encodeURIComponent(ref)}`),
  researchBatch: (ref, body) =>
    apiPost(`/research/experiments/${encodeURIComponent(ref)}/batch`, body || {}),
  researchBatchStatus: (batchId) => apiGet(`/research/batches/${encodeURIComponent(batchId)}`),
  researchSummary: (ref) => apiGet(`/research/experiments/${encodeURIComponent(ref)}/summary`),
  researchAnalyze: (ref, body) =>
    apiPost(`/research/experiments/${encodeURIComponent(ref)}/analyze`, body || {}),
  researchAnalysis: (ref) => apiGet(`/research/experiments/${encodeURIComponent(ref)}/analysis`),
  researchReport: (ref) => apiGet(`/research/experiments/${encodeURIComponent(ref)}/report`),
  researchReproduce: (ref, body) =>
    apiPost(`/research/experiments/${encodeURIComponent(ref)}/reproduce`, body || {}),

  // Scenarios (Phase S2): mutable drafts -> canonical experiment path
  scenarioTemplates: () => apiGet('/scenarios/templates'),
  scenarioFromPreset: (presetId) => apiGet(`/scenarios/from-preset/${encodeURIComponent(presetId)}`),
  scenarioValidate: (spec) => apiPost('/scenarios/validate', { spec }),
  scenarioRegister: (spec) => apiPost('/scenarios/register', { spec }),
  scenarios: () => apiGet('/scenarios'),
  scenarioSave: (name, spec) => apiPost('/scenarios', { name, spec }),
  scenarioGet: (id) => apiGet(`/scenarios/${encodeURIComponent(id)}`),
  scenarioUpdate: (id, name, spec) =>
    request(`/scenarios/${encodeURIComponent(id)}`, {
      method: 'PUT', body: JSON.stringify({ name, spec }),
    }),
  scenarioDelete: (id) =>
    request(`/scenarios/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  scenarioDuplicate: (id) => apiPost(`/scenarios/${encodeURIComponent(id)}/duplicate`),

  // TradeOps (Phase S2)
  opsStatus: () => apiGet('/ops/status'),
  opsWorkers: () => apiGet('/ops/workers'),
  opsJobs: (state) => apiGet(`/ops/jobs${state ? `?state=${state}` : ''}`),
  opsJob: (id) => apiGet(`/ops/jobs/${encodeURIComponent(id)}`),
  opsSubmit: (versionRef) => apiPost('/ops/jobs', { version_ref: versionRef }),
  opsCancel: (id) => apiPost(`/ops/jobs/${encodeURIComponent(id)}/cancel`),
  opsRetry: (id) => apiPost(`/ops/jobs/${encodeURIComponent(id)}/retry`),

  // External event-market intelligence (Phase S3)
  marketsProviders: () => apiGet('/markets/providers'),
  marketsDatasets: () => apiGet('/markets/datasets'),
  marketsDataset: (id) => apiGet(`/markets/datasets/${encodeURIComponent(id)}`),
  marketsObservations: (id, start = 0, limit = 5000) =>
    apiGet(`/markets/datasets/${encodeURIComponent(id)}/observations?start=${start}&limit=${limit}`),
  marketsSignature: (id, params = {}) => {
    const q = Object.entries(params)
      .filter(([, v]) => v !== '' && v != null)
      .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
      .join('&');
    return apiGet(`/markets/datasets/${encodeURIComponent(id)}/signature${q ? `?${q}` : ''}`);
  },
  marketsPropose: (id) => apiGet(`/markets/datasets/${encodeURIComponent(id)}/propose`),
  marketsMechanisms: () => apiGet('/markets/mechanisms'),
  marketsResearch: (body) => apiPost('/markets/research', body),
  marketsCompare: (body) => apiPost('/markets/compare', body),
  marketsImport: (body) => apiPost('/markets/import', body),

  // Strategy Lab (Phase S4): market worlds × Nautilus backtests
  labStrategies: () => apiGet('/lab/strategies'),
  labWorlds: () => apiGet('/lab/worlds'),
  labWorld: (id) => apiGet(`/lab/worlds/${encodeURIComponent(id)}`),
  labWorldQuotes: (id, start = 0, limit = 5000) =>
    apiGet(`/lab/worlds/${encodeURIComponent(id)}/quotes?start=${start}&limit=${limit}`),
  labBuildWorld: (body) => apiPost('/lab/worlds', body),
  labRunBacktest: (body) => apiPost('/lab/backtests', body),
  labResults: () => apiGet('/lab/results'),
  labResult: (id) => apiGet(`/lab/results/${encodeURIComponent(id)}`),
  labCompare: (resultIds) => apiPost('/lab/results/compare', { result_ids: resultIds }),
  labReproduce: (id) => apiPost(`/lab/results/${encodeURIComponent(id)}/reproduce`),

  // Finance (Phase S6): valuation & transaction analysis
  financeCases: () => apiGet('/finance/cases'),
  financeCase: (id) => apiGet(`/finance/cases/${encodeURIComponent(id)}`),
  financeReport: (id) => apiGet(`/finance/cases/${encodeURIComponent(id)}/report`),
  financeRun: (spec) => apiPost('/finance/cases', { spec }),
  financeReproduce: (id) => apiPost(`/finance/cases/${encodeURIComponent(id)}/reproduce`),
};

// ---- small formatting helpers shared by pages ----------------------------

export function asArray(v) {
  return Array.isArray(v) ? v : [];
}

export function fmtNum(v, digits = 2) {
  if (v == null || typeof v !== 'number' || !isFinite(v)) return '—';
  return v.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtInt(v) {
  if (v == null || typeof v !== 'number' || !isFinite(v)) return '—';
  return Math.round(v).toLocaleString('en-US');
}

export function fmtPct(v, digits = 2) {
  if (v == null || typeof v !== 'number' || !isFinite(v)) return '—';
  const s = (v * 100).toFixed(digits);
  return `${v > 0 ? '+' : ''}${s}%`;
}

export function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  return d.toISOString().replace('T', ' ').slice(0, 19) + 'Z';
}

export function truncHash(h, n = 10) {
  if (!h || typeof h !== 'string') return '—';
  return h.length > n ? h.slice(0, n) + '…' : h;
}
