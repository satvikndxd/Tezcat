import React from 'react';
import { useMeasuredWidth, NoData, AXIS_TEXT } from './chartUtils.jsx';

const ROW_H = 15;
const TOP = 18;

function cleanLevels(levels) {
  if (!Array.isArray(levels)) return [];
  return levels
    .filter(
      (l) =>
        Array.isArray(l) &&
        typeof l[0] === 'number' &&
        typeof l[1] === 'number' &&
        isFinite(l[0]) &&
        isFinite(l[1])
    )
    .slice(0, 10);
}

// Order book ladder: mirrored horizontal bars around mid.
// Bids (green) grow leftward from center, asks (red) grow rightward.
export default function DepthChart({ bids, asks, mid, height: hProp }) {
  const [ref, width] = useMeasuredWidth(400);
  const b = cleanLevels(bids);
  const a = cleanLevels(asks);
  const rows = Math.max(b.length, a.length);
  const height = hProp || TOP + Math.max(rows, 4) * ROW_H + 8;

  let body = null;
  if (rows === 0) {
    body = <NoData width={width} height={height} />;
  } else {
    const cx = width / 2;
    const halfW = Math.max(20, cx - 64);
    const maxQty = Math.max(
      1,
      ...b.map((l) => l[1]),
      ...a.map((l) => l[1])
    );
    const bidSum = b.reduce((s, l) => s + l[1], 0);
    const askSum = a.reduce((s, l) => s + l[1], 0);

    body = (
      <>
        <text x={cx} y={10} textAnchor="middle" {...AXIS_TEXT} fill="#f2f2f2">
          {typeof mid === 'number' && isFinite(mid) ? `MID ${mid.toFixed(2)}` : 'MID —'}
        </text>
        <text x={8} y={10} {...AXIS_TEXT} fill="var(--up)">
          BID Σ{Math.round(bidSum)}
        </text>
        <text x={width - 8} y={10} textAnchor="end" {...AXIS_TEXT} fill="var(--down)">
          ASK Σ{Math.round(askSum)}
        </text>
        <line
          x1={cx}
          x2={cx}
          y1={TOP - 3}
          y2={TOP + rows * ROW_H}
          stroke="#1f1f1f"
          strokeWidth="1"
        />
        {Array.from({ length: rows }).map((_, i) => {
          const y = TOP + i * ROW_H;
          const bid = b[i];
          const ask = a[i];
          return (
            <g key={i}>
              {bid ? (
                <>
                  <rect
                    x={cx - 4 - (bid[1] / maxQty) * halfW}
                    y={y + 2}
                    width={(bid[1] / maxQty) * halfW}
                    height={ROW_H - 4}
                    fill="rgba(74,246,195,0.22)"
                    stroke="rgba(74,246,195,0.55)"
                    strokeWidth="0.5"
                  />
                  <text
                    x={cx - 8}
                    y={y + ROW_H - 4}
                    textAnchor="end"
                    {...AXIS_TEXT}
                    fill="var(--up)"
                  >
                    {bid[0].toFixed(2)}
                  </text>
                  <text x={8} y={y + ROW_H - 4} {...AXIS_TEXT}>
                    {Math.round(bid[1])}
                  </text>
                </>
              ) : null}
              {ask ? (
                <>
                  <rect
                    x={cx + 4}
                    y={y + 2}
                    width={(ask[1] / maxQty) * halfW}
                    height={ROW_H - 4}
                    fill="rgba(255,67,61,0.20)"
                    stroke="rgba(255,67,61,0.55)"
                    strokeWidth="0.5"
                  />
                  <text x={cx + 8} y={y + ROW_H - 4} {...AXIS_TEXT} fill="var(--down)">
                    {ask[0].toFixed(2)}
                  </text>
                  <text
                    x={width - 8}
                    y={y + ROW_H - 4}
                    textAnchor="end"
                    {...AXIS_TEXT}
                  >
                    {Math.round(ask[1])}
                  </text>
                </>
              ) : null}
            </g>
          );
        })}
      </>
    );
  }

  return (
    <div className="chart-wrap" ref={ref}>
      <svg width={width} height={height}>
        {body}
      </svg>
    </div>
  );
}
