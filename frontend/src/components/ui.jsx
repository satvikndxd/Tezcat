import React from 'react';

export function Panel({ title, sub, children, tight = false, className = '' }) {
  return (
    <div className={`panel ${className}`}>
      {title != null && (
        <div className="panel-head">
          <span className="title">{title}</span>
          {sub != null && <span className="sub">{sub}</span>}
        </div>
      )}
      <div className={`panel-body${tight ? ' tight' : ''}`}>{children}</div>
    </div>
  );
}

export function StatusBadge({ status }) {
  const s = String(status || 'unknown').toLowerCase();
  return <span className={`badge ${s}`}>{s}</span>;
}

export function RegimeBadge({ regime }) {
  const r = String(regime || 'stable').toLowerCase();
  return <span className={`badge regime-${r}`}>{r}</span>;
}

export function ProgressBar({ value, max }) {
  const pct =
    typeof value === 'number' && typeof max === 'number' && max > 0
      ? Math.min(100, Math.max(0, (value / max) * 100))
      : 0;
  return (
    <div className="progress">
      <div style={{ width: `${pct}%` }} />
    </div>
  );
}

export function StatTile({ label, value, tone }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className={`val ${tone || ''}`}>{value}</div>
    </div>
  );
}

// Signed value colored green/red — direction is also carried by the sign
// itself so identity never rests on color alone.
export function Signed({ v, fmt }) {
  if (v == null || typeof v !== 'number' || !isFinite(v)) {
    return <span className="neu">—</span>;
  }
  const cls = v > 0 ? 'pos' : v < 0 ? 'neg' : 'neu';
  return <span className={cls}>{fmt ? fmt(v) : v}</span>;
}

export function EmptyRow({ cols, text = 'NO DATA' }) {
  return (
    <tr>
      <td className="empty" colSpan={cols}>
        {text}
      </td>
    </tr>
  );
}

export function navigate(hash) {
  window.location.hash = hash;
}
