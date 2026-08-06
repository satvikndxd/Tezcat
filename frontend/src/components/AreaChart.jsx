import React from 'react';
import {
  useMeasuredWidth,
  cleanPoints,
  extent,
  makeScale,
  fmtAxis,
  NoData,
  AXIS_TEXT,
} from './chartUtils.jsx';

// Small area / bar chart used for spread, rolling volatility and volume.
// mode: "area" (filled line) | "bars" (thin per-step bars).
// Auto-scales from zero, labels min / max / last, NO DATA when empty.
export default function AreaChart({
  points,
  height = 110,
  color = '#8a8a8a',
  fill = 'rgba(138,138,138,0.15)',
  mode = 'area',
  title = '',
}) {
  const [ref, width] = useMeasuredWidth(400);
  const data = cleanPoints(points);
  const pad = { top: 14, right: 46, bottom: 4, left: 4 };
  const iw = Math.max(10, width - pad.left - pad.right);
  const ih = Math.max(10, height - pad.top - pad.bottom);

  let body = null;
  if (data.length === 0) {
    body = <NoData width={width} height={height} />;
  } else {
    const xd = extent(data.map((p) => p.x));
    const ymax = Math.max(...data.map((p) => p.y), 0);
    const yd = [0, ymax === 0 ? 1 : ymax * 1.08];
    const sx = makeScale(xd, [pad.left, pad.left + iw]);
    const sy = makeScale(yd, [pad.top + ih, pad.top]);
    const y0 = sy(0);
    const last = data[data.length - 1];
    let minY = Infinity;
    for (const p of data) if (p.y < minY) minY = p.y;

    let marks;
    if (mode === 'bars') {
      const bw = Math.max(1, Math.min(4, iw / Math.max(1, data.length) - 0.5));
      marks = data.map((p, i) => (
        <rect
          key={i}
          x={sx(p.x) - bw / 2}
          y={sy(p.y)}
          width={bw}
          height={Math.max(0, y0 - sy(p.y))}
          fill={color}
          opacity="0.75"
        />
      ));
    } else {
      const line = data
        .map((p, i) => `${i === 0 ? 'M' : 'L'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`)
        .join('');
      const area =
        `M${sx(data[0].x).toFixed(1)},${y0.toFixed(1)}` +
        data.map((p) => `L${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join('') +
        `L${sx(last.x).toFixed(1)},${y0.toFixed(1)}Z`;
      marks = (
        <>
          <path d={area} fill={fill} stroke="none" />
          <path d={line} fill="none" stroke={color} strokeWidth="1" />
        </>
      );
    }

    body = (
      <>
        <line
          x1={pad.left}
          x2={pad.left + iw}
          y1={y0}
          y2={y0}
          stroke="#1f1f1f"
          strokeWidth="1"
        />
        {marks}
        <text x={pad.left + iw + 4} y={pad.top + 4} {...AXIS_TEXT}>
          {fmtAxis(ymax)}
        </text>
        <text x={pad.left + iw + 4} y={y0} {...AXIS_TEXT}>
          {fmtAxis(Math.max(0, minY))}
        </text>
        <text
          x={pad.left + iw + 4}
          y={Math.min(y0 - 8, Math.max(pad.top + 14, sy(last.y) + 3))}
          {...AXIS_TEXT}
          fill="#f2f2f2"
        >
          {fmtAxis(last.y)}
        </text>
      </>
    );
  }

  return (
    <div className="chart-wrap" ref={ref}>
      <svg width={width} height={height}>
        {title ? (
          <text x={4} y={9} {...AXIS_TEXT} fill="#555">
            {title}
          </text>
        ) : null}
        {body}
      </svg>
    </div>
  );
}
