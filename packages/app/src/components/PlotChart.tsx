/**
 * Draws a ```plot block as a line chart.
 *
 * The text is a model's, so it is read as data (see lib/plot) and a block
 * that does not parse falls back to its text with a plain message, never to a
 * wrong picture. Up to three series, in a fixed colour order that was checked
 * for colour-blind separation on both the light and the dark surface; the
 * names ride the line ends and a legend, and "Show the numbers" opens the same
 * values as a table, so nothing depends on telling two colours apart.
 */
import { useId, useMemo, useRef, useState } from 'react';

import {
  formatNumber,
  nearestPoint,
  niceTicks,
  parsePlotSpec,
  type PlotSpec,
} from '../lib/plot';

const WIDTH = 560;
const HEIGHT = 300;
const MARGIN = { top: 14, right: 96, bottom: 40, left: 52 };
const INNER_W = WIDTH - MARGIN.left - MARGIN.right;
const INNER_H = HEIGHT - MARGIN.top - MARGIN.bottom;

const SERIES_VARS = ['var(--plot-series-1)', 'var(--plot-series-2)', 'var(--plot-series-3)'];

export interface PlotChartProps {
  /** The text of a ```plot block. */
  source?: string;
  /** Or values already in hand (an ```explore block samples its own). */
  spec?: PlotSpec;
  /** Hold the y-axis still at this range instead of fitting it to the data. */
  yDomain?: [number, number];
  /** The "Show the numbers" table; off when the numbers change as a slider moves. */
  numbers?: boolean;
}

export function PlotChart({ source = '', spec: given, yDomain, numbers = true }: PlotChartProps) {
  const parsed = useMemo(() => (given ? null : parsePlotSpec(source)), [given, source]);
  const spec = given ?? parsed;
  const titleId = useId();
  const clipId = useId();
  const svgRef = useRef<SVGSVGElement>(null);
  const [hoverX, setHoverX] = useState<number | null>(null);

  const geometry = useMemo(() => {
    if (!spec) return null;
    const xs = spec.series.flatMap((s) => s.points.map((p) => p[0]));
    const ys = spec.series.flatMap((s) => s.points.map((p) => p[1]));
    const xTicks = niceTicks(Math.min(...xs), Math.max(...xs), 6);
    const yTicks = yDomain
      ? niceTicks(yDomain[0], yDomain[1], 5)
      : niceTicks(Math.min(...ys), Math.max(...ys), 5);
    const xMin = Math.min(Math.min(...xs), xTicks[0] ?? 0);
    const xMax = Math.max(Math.max(...xs), xTicks[xTicks.length - 1] ?? 1);
    const yMin = yTicks[0] ?? Math.min(...ys);
    const yMax = yTicks[yTicks.length - 1] ?? Math.max(...ys);
    const sx = (x: number) => MARGIN.left + ((x - xMin) / (xMax - xMin || 1)) * INNER_W;
    const sy = (y: number) => MARGIN.top + INNER_H - ((y - yMin) / (yMax - yMin || 1)) * INNER_H;
    return {
      xMin,
      xMax,
      sx,
      sy,
      xTicks: xTicks.filter((t) => t >= xMin && t <= xMax),
      yTicks,
    };
  }, [spec, yDomain]);

  if (!spec || !geometry) {
    return (
      <div data-testid="plot-fallback">
        <p className="text-xs text-[var(--text-muted)]">
          This graph could not be drawn, so here is its description instead.
        </p>
        <pre className="mt-1 overflow-x-auto rounded-md bg-[var(--surface-sunken)] p-2 text-xs">
          {source}
        </pre>
      </div>
    );
  }

  const { sx, sy, xTicks, yTicks, xMin, xMax } = geometry;
  const multi = spec.series.length > 1;

  // End labels, nudged apart so two lines ending together stay readable.
  const ends = spec.series
    .map((series, index) => {
      const last = series.points[series.points.length - 1] as [number, number];
      return { index, name: series.name, x: sx(last[0]), y: sy(last[1]) };
    })
    .sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i += 1) {
    const previous = ends[i - 1];
    const current = ends[i];
    if (previous && current && current.y - previous.y < 13) current.y = previous.y + 13;
  }

  const onMove = (event: React.PointerEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) return;
    const box = svg.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * WIDTH;
    if (px < MARGIN.left || px > MARGIN.left + INNER_W) {
      setHoverX(null);
      return;
    }
    setHoverX(xMin + ((px - MARGIN.left) / INNER_W) * (xMax - xMin));
  };

  // The crosshair snaps to the nearest real x of the first series.
  const snappedX =
    hoverX === null
      ? null
      : nearestPoint((spec.series[0] as { points: Array<[number, number]> }).points, hoverX)[0];
  const readout =
    snappedX === null
      ? []
      : spec.series.map((series, index) => ({
          index,
          name: series.name,
          point: nearestPoint(series.points, snappedX),
        }));
  const tooltipLeft = snappedX === null ? 0 : (sx(snappedX) / WIDTH) * 100;

  return (
    <figure className="sp-plot my-3" data-testid="plot-chart">
      {spec.title ? (
        <figcaption
          id={titleId}
          className="mb-1 text-[0.8125rem] font-medium text-[var(--text-primary)]"
        >
          {spec.title}
        </figcaption>
      ) : null}

      {multi ? (
        <ul className="mb-1 flex flex-wrap gap-x-4 gap-y-1 text-[0.75rem] text-[var(--text-secondary)]">
          {spec.series.map((series, index) => (
            <li key={series.name + index} className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="inline-block h-0.5 w-4 rounded-full"
                style={{ background: SERIES_VARS[index] }}
              />
              {series.name}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="relative">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="img"
          aria-labelledby={spec.title ? titleId : undefined}
          aria-label={spec.title ? undefined : 'Line graph'}
          className="block h-auto w-full max-w-[640px] touch-none"
          onPointerMove={onMove}
          onPointerLeave={() => setHoverX(null)}
        >
          {yTicks.map((tick) => (
            <g key={`y${tick}`}>
              <line
                x1={MARGIN.left}
                x2={MARGIN.left + INNER_W}
                y1={sy(tick)}
                y2={sy(tick)}
                stroke="var(--border-subtle)"
                strokeWidth={1}
              />
              <text
                x={MARGIN.left - 8}
                y={sy(tick)}
                textAnchor="end"
                dominantBaseline="central"
                fontSize={11}
                fill="var(--text-muted)"
              >
                {formatNumber(tick)}
              </text>
            </g>
          ))}
          {xTicks.map((tick) => (
            <text
              key={`x${tick}`}
              x={sx(tick)}
              y={MARGIN.top + INNER_H + 16}
              textAnchor="middle"
              fontSize={11}
              fill="var(--text-muted)"
            >
              {formatNumber(tick)}
            </text>
          ))}
          {spec.xLabel ? (
            <text
              x={MARGIN.left + INNER_W / 2}
              y={HEIGHT - 6}
              textAnchor="middle"
              fontSize={11}
              fill="var(--text-secondary)"
            >
              {spec.xLabel}
            </text>
          ) : null}
          {spec.yLabel ? (
            <text
              transform={`translate(12 ${MARGIN.top + INNER_H / 2}) rotate(-90)`}
              textAnchor="middle"
              fontSize={11}
              fill="var(--text-secondary)"
            >
              {spec.yLabel}
            </text>
          ) : null}

          <clipPath id={clipId}>
            <rect x={MARGIN.left} y={MARGIN.top - 2} width={INNER_W} height={INNER_H + 4} />
          </clipPath>
          {spec.series.map((series, index) => (
            <polyline
              key={series.name + index}
              fill="none"
              stroke={SERIES_VARS[index]}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
              // With a fixed range a curve can leave the frame; it is cut at the edge.
              clipPath={yDomain ? `url(#${clipId})` : undefined}
              points={series.points.map(([x, y]) => `${sx(x)},${sy(y)}`).join(' ')}
            />
          ))}

          {ends.map((end) => {
            const series = spec.series[end.index] as { points: Array<[number, number]> };
            const last = series.points[series.points.length - 1] as [number, number];
            const y = sy(last[1]);
            if (y < MARGIN.top - 2 || y > MARGIN.top + INNER_H + 2) return null;
            return (
              <g key={`end${end.index}`}>
                <circle
                  cx={sx(last[0])}
                  cy={sy(last[1])}
                  r={4}
                  fill={SERIES_VARS[end.index]}
                  stroke="var(--surface-raised)"
                  strokeWidth={2}
                />
                {multi ? (
                  <text
                    x={end.x + 9}
                    y={end.y}
                    dominantBaseline="central"
                    fontSize={11}
                    fill="var(--text-secondary)"
                  >
                    {end.name.length > 14 ? `${end.name.slice(0, 13)}…` : end.name}
                  </text>
                ) : null}
              </g>
            );
          })}

          {snappedX !== null ? (
            <g pointerEvents="none">
              <line
                x1={sx(snappedX)}
                x2={sx(snappedX)}
                y1={MARGIN.top}
                y2={MARGIN.top + INNER_H}
                stroke="var(--text-muted)"
                strokeWidth={1}
              />
              {readout.map((row) => (
                <circle
                  key={`dot${row.index}`}
                  cx={sx(row.point[0])}
                  cy={sy(row.point[1])}
                  r={4}
                  fill={SERIES_VARS[row.index]}
                  stroke="var(--surface-raised)"
                  strokeWidth={2}
                />
              ))}
            </g>
          ) : null}
        </svg>

        {snappedX !== null ? (
          <div
            role="status"
            className="pointer-events-none absolute top-1 z-10 rounded-[var(--radius-control)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-2 py-1.5 text-[0.75rem] shadow-[var(--shadow-raised)]"
            style={
              tooltipLeft > 55
                ? { right: `${100 - tooltipLeft + 2}%` }
                : { left: `${tooltipLeft + 2}%` }
            }
          >
            <p className="text-[var(--text-muted)]">
              {spec.xLabel || 'x'} = {formatNumber(snappedX)}
            </p>
            {readout.map((row) => (
              <p key={`row${row.index}`} className="flex items-center gap-1.5 whitespace-nowrap">
                <span
                  aria-hidden
                  className="inline-block h-0.5 w-3 rounded-full"
                  style={{ background: SERIES_VARS[row.index] }}
                />
                <strong className="font-semibold tabular-nums text-[var(--text-primary)]">
                  {formatNumber(row.point[1])}
                </strong>
                {multi ? <span className="text-[var(--text-secondary)]">{row.name}</span> : null}
              </p>
            ))}
          </div>
        ) : null}
      </div>

      {numbers ? (
      <details className="mt-1 text-[0.75rem] text-[var(--text-secondary)]">
        <summary className="cursor-pointer select-none text-[var(--text-muted)]">
          Show the numbers
        </summary>
        <div className="mt-1 overflow-x-auto">
          <table className="border-collapse tabular-nums">
            <thead>
              <tr>
                <th className="pr-4 text-left font-medium">Series</th>
                <th className="pr-4 text-right font-medium">{spec.xLabel || 'x'}</th>
                <th className="text-right font-medium">{spec.yLabel || 'y'}</th>
              </tr>
            </thead>
            <tbody>
              {spec.series.flatMap((series, index) =>
                series.points.map(([x, y], row) => (
                  <tr key={`${index}-${row}`}>
                    <td className="pr-4">{row === 0 ? series.name : ''}</td>
                    <td className="pr-4 text-right">{formatNumber(x)}</td>
                    <td className="text-right">{formatNumber(y)}</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
      </details>
      ) : null}
    </figure>
  );
}
