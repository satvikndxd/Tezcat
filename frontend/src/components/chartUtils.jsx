import { useEffect, useRef, useState } from 'react';

// Measure the rendered width of a container so SVG charts stay crisp
// (no viewBox stretching of text) while remaining responsive.
export function useMeasuredWidth(fallback = 600) {
  const ref = useRef(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const update = () => {
      const w = el.clientWidth;
      if (w > 0) setWidth(w);
    };
    update();
    let ro;
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(update);
      ro.observe(el);
    } else {
      window.addEventListener('resize', update);
    }
    return () => {
      if (ro) ro.disconnect();
      else window.removeEventListener('resize', update);
    };
  }, []);
  return [ref, width];
}

// Clean numeric filter for [{x, y}] points.
export function cleanPoints(points) {
  if (!Array.isArray(points)) return [];
  return points.filter(
    (p) =>
      p &&
      typeof p.x === 'number' &&
      typeof p.y === 'number' &&
      isFinite(p.x) &&
      isFinite(p.y)
  );
}

export function extent(values, pad = 0) {
  let min = Infinity;
  let max = -Infinity;
  for (const v of values) {
    if (v < min) min = v;
    if (v > max) max = v;
  }
  if (!isFinite(min) || !isFinite(max)) return [0, 1];
  if (min === max) {
    const d = Math.abs(min) * 0.05 || 1;
    return [min - d, max + d];
  }
  const span = max - min;
  return [min - span * pad, max + span * pad];
}

export function makeScale([d0, d1], [r0, r1]) {
  const span = d1 - d0 || 1;
  return (v) => r0 + ((v - d0) / span) * (r1 - r0);
}

export function fmtAxis(v) {
  if (v == null || !isFinite(v)) return '';
  const a = Math.abs(v);
  if (a >= 1e6) return (v / 1e6).toFixed(1) + 'M';
  if (a >= 1e4) return (v / 1e3).toFixed(1) + 'K';
  if (a >= 100) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  if (a === 0) return '0';
  return v.toFixed(4);
}

export function NoData({ width, height }) {
  return (
    <text
      x={width / 2}
      y={height / 2}
      textAnchor="middle"
      dominantBaseline="middle"
      className="chart-nodata"
    >
      NO DATA
    </text>
  );
}

export const AXIS_TEXT = {
  fontSize: 9,
  fill: '#8a8a8a',
  fontFamily: 'inherit',
};

export const REGIME_COLORS = {
  stable: 'transparent',
  crisis: 'rgba(255, 67, 61, 0.08)',
  recovery: 'rgba(74, 246, 195, 0.07)',
};
