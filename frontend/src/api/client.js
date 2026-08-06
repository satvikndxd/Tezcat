// Tezcat REST client. All endpoints under /api (same origin, proxied in dev).
// Every helper is defensive: network failures reject with ApiError, and the
// connection status is broadcast so the status bar can show ONLINE / OFFLINE.

const BASE = '/api';

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
