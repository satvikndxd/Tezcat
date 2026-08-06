import React from 'react';
import {
  useMeasuredWidth,
  cleanPoints,
  extent,
  makeScale,
  fmtAxis,
  NoData,
  AXIS_TEXT,
  REGIME_COLORS,
} from './chartUtils.jsx';

// Price line chart with:
//  - regime background bands: [{x0, x1, regime}]
//  - shock markers: [{step, label}] (vertical amber dashed lines)
// Auto-scales, labels min / max / last, renders NO DATA when empty.
export default function LineChart({
  points,
  shocks = [],
  regimeBands = [],
  height = 260,
  color = 'var(--accent)',
  yLabel = '',
}) {
  const [ref, width] = useMeasuredWidth(700);
  const data = cleanPoints(points);
  const pad = { top: 16, right: 54, bottom: 18, left: 8 };
  const iw = Math.max(10, width - pad.left - pad.right);
  const ih = Math.max(10, height - pad.top - pad.bottom);

  let body = null;
  if (data.length === 0) {
    body = <NoData width={width} height={height} />;
  } else {
    const xd = extent(data.map((p) => p.x));
    const yd = extent(data.map((p) => p.y), 0.08);
    const sx = makeScale(xd, [pad.left, pad.left + iw]);
    const sy = makeScale(yd, [pad.top + ih, pad.top]);

    const path = data
      .map((p, i) => `${i === 0 ? 'M' : 'L'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`)
      .join('');

    let minP = data[0];
    let maxP = data[0];
    for (const p of data) {
      if (p.y < minP.y) minP = p;
      if (p.y > maxP.y) maxP = p;
    }
    const last = data[data.length - 1];

    const bands = (Array.isArray(regimeBands) ? regimeBands : []).filter(
      (b) =>
        b &&
        typeof b.x0 === 'number' &&
        typeof b.x1 === 'number' &&
        b.x1 >= xd[0] &&
        b.x0 <= xd[1] &&
        REGIME_COLORS[b.regime] &&
        b.regime !== 'stable'
    );

    const marks = (Array.isArray(shocks) ? shocks : []).filter(
      (s) => s && typeof s.step === 'number' && s.step >= xd[0] && s.step <= xd[1]
    );

    body = (
      <>
        {bands.map((b, i) => {
          const x0 = sx(Math.max(b.x0, xd[0]));
          const x1 = sx(Math.min(b.x1, xd[1]));
          return (
            <rect
              key={`b${i}`}
              x={x0}
              y={pad.top}
              width={Math.max(1, x1 - x0)}
              height={ih}
              fill={REGIME_COLORS[b.regime]}
            />
          );
        })}
        {/* recessive horizontal grid */}
        {[0.25, 0.5, 0.75].map((f) => {
          const y = pad.top + ih * f;
          return (
            <line
              key={f}
              x1={pad.left}
              x2={pad.left + iw}
              y1={y}
              y2={y}
              stroke="#141414"
              strokeWidth="1"
            />
          );
        })}
        <path d={path} fill="none" stroke={color} strokeWidth="1.5" />
        {marks.map((s, i) => {
          const x = sx(s.step);
          return (
            <g key={`s${i}`}>
              <line
                x1={x}
                x2={x}
                y1={pad.top}
                y2={pad.top + ih}
                stroke="var(--accent)"
                strokeWidth="1"
                strokeDasharray="3,3"
                opacity="0.8"
              />
              <text
                x={x + 3}
                y={pad.top + 8}
                {...AXIS_TEXT}
                fill="var(--accent)"
              >
                {s.label || 'SHOCK'}
              </text>
            </g>
          );
        })}
        {/* min / max / last labels on the right gutter */}
        <text x={pad.left + iw + 4} y={sy(maxP.y) + 3} {...AXIS_TEXT}>
          {fmtAxis(maxP.y)}
        </text>
        <text x={pad.left + iw + 4} y={sy(minP.y) + 3} {...AXIS_TEXT}>
          {fmtAxis(minP.y)}
        </text>
        <circle cx={sx(last.x)} cy={sy(last.y)} r="2.5" fill={color} />
        <text
          x={pad.left + iw + 4}
          y={sy(last.y) + 3}
          {...AXIS_TEXT}
          fill="#f2f2f2"
        >
          {fmtAxis(last.y)}
        </text>
        {/* x extent labels */}
        <text x={pad.left} y={height - 5} {...AXIS_TEXT}>
          {fmtAxis(xd[0])}
        </text>
        <text x={pad.left + iw} y={height - 5} textAnchor="end" {...AXIS_TEXT}>
          {fmtAxis(xd[1])}
        </text>
        {yLabel ? (
          <text x={pad.left} y={pad.top - 5} {...AXIS_TEXT} fill="#555">
            {yLabel}
          </text>
        ) : null}
      </>
    );
  }

  return (
    <div className="chart-wrap" ref={ref}>
      <svg width={width} height={height}>
        <rect x="0" y="0" width={width} height={height} fill="transparent" />
        {body}
      </svg>
    </div>
  );
}
