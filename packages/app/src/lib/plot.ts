/**
 * Reading a ```plot block.
 *
 * The text comes from a model, so it is parsed as data and nothing else: a
 * JSON object with a few strings and lists of number pairs. Anything that is
 * not exactly that is refused, and the caller shows the text instead of a
 * wrong chart. No expression is ever evaluated.
 */

export interface PlotSeries {
  name: string;
  points: Array<[number, number]>;
}

export interface PlotSpec {
  title: string;
  xLabel: string;
  yLabel: string;
  series: PlotSeries[];
}

/** Three series is what the palette is validated for; a fourth would not be told apart. */
export const MAX_SERIES = 3;
const MAX_POINTS = 400;

function text(value: unknown, limit: number): string {
  return typeof value === 'string' ? value.trim().slice(0, limit) : '';
}

export function parsePlotSpec(source: string): PlotSpec | null {
  let raw: unknown;
  try {
    raw = JSON.parse(source);
  } catch {
    return null;
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const spec = raw as Record<string, unknown>;
  if (!Array.isArray(spec.series)) return null;

  const series: PlotSeries[] = [];
  for (const [index, entry] of spec.series.slice(0, MAX_SERIES).entries()) {
    if (!entry || typeof entry !== 'object') return null;
    const item = entry as Record<string, unknown>;
    if (!Array.isArray(item.points)) return null;
    const points: Array<[number, number]> = [];
    for (const point of item.points.slice(0, MAX_POINTS)) {
      if (!Array.isArray(point) || point.length !== 2) return null;
      const [x, y] = point as unknown[];
      if (typeof x !== 'number' || typeof y !== 'number') return null;
      if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
      points.push([x, y]);
    }
    if (points.length < 2) return null;
    points.sort((a, b) => a[0] - b[0]);
    series.push({ name: text(item.name, 60) || `Series ${index + 1}`, points });
  }
  if (series.length === 0) return null;

  return {
    title: text(spec.title, 120),
    xLabel: text(spec.x, 60),
    yLabel: text(spec.y, 60),
    series,
  };
}

/**
 * Round tick values covering [min, max]: steps of 1, 2 or 5 times a power of
 * ten, so an axis reads 0 / 0.5 / 1 rather than 0 / 0.47 / 0.94.
 */
export function niceTicks(min: number, max: number, target = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) {
    const pad = Math.abs(min) > 0 ? Math.abs(min) * 0.5 : 1;
    return niceTicks(min - pad, max + pad, target);
  }
  const span = max - min;
  const rough = span / Math.max(1, target);
  const power = 10 ** Math.floor(Math.log10(rough));
  const fraction = rough / power;
  const step = (fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10) * power;
  // Outward to the next round value on both sides, so the first and last
  // ticks enclose the data: an axis that stops at 30 would cut off a line at 32.
  const first = Math.floor(min / step + 1e-9);
  const last = Math.ceil(max / step - 1e-9);
  const ticks: number[] = [];
  for (let index = first; index <= last && ticks.length <= 20; index += 1) {
    // Kill floating-point dust such as 0.30000000000000004.
    ticks.push(Number((index * step).toPrecision(12)));
  }
  return ticks;
}

/** A tick or tooltip value, without trailing noise. */
export function formatNumber(value: number): string {
  if (value === 0) return '0';
  const magnitude = Math.abs(value);
  if (magnitude >= 10000 || magnitude < 0.001) return value.toExponential(1).replace('e+', 'e');
  return String(Number(value.toPrecision(4)));
}

/** The point of a series nearest to x; series need not share their x values. */
export function nearestPoint(points: Array<[number, number]>, x: number): [number, number] {
  let best = points[0] as [number, number];
  for (const point of points) {
    if (Math.abs(point[0] - x) < Math.abs(best[0] - x)) best = point;
  }
  return best;
}
