/**
 * Reading an ```explore block: a formula the student drives with sliders.
 *
 * Like a ```plot block it is a model's text read as data. The curves are
 * expressions in the small language of lib/expression, compiled there and
 * never evaluated as code. Anything that does not validate is refused, and
 * the caller shows the text instead of a wrong picture.
 */
import { compileExpression, FUNCTION_NAMES, type Compiled } from './expression';
import type { PlotSpec } from './plot';

export interface ExploreParam {
  name: string;
  label: string;
  min: number;
  max: number;
  value: number;
  step: number;
}

export interface ExploreCurve {
  name: string;
  expr: string;
  evaluate: Compiled;
}

export interface ExploreSpec {
  title: string;
  note: string;
  xLabel: string;
  yLabel: string;
  xMin: number;
  xMax: number;
  params: ExploreParam[];
  curves: ExploreCurve[];
}

export const MAX_PARAMS = 3;
export const MAX_CURVES = 3;
const SAMPLES = 240;
const NAME = /^[A-Za-z][A-Za-z0-9_]{0,11}$/;

const text = (value: unknown, limit: number) =>
  typeof value === 'string' ? value.trim().slice(0, limit) : '';
const finite = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

export function parseExploreSpec(source: string): ExploreSpec | null {
  let raw: unknown;
  try {
    raw = JSON.parse(source);
  } catch {
    return null;
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const spec = raw as Record<string, unknown>;
  const x = (spec.x && typeof spec.x === 'object' ? spec.x : {}) as Record<string, unknown>;
  const y = (spec.y && typeof spec.y === 'object' ? spec.y : {}) as Record<string, unknown>;
  if (!finite(x.min) || !finite(x.max) || x.max <= x.min) return null;

  const params: ExploreParam[] = [];
  const rawParams = Array.isArray(spec.params) ? spec.params : [];
  if (rawParams.length > MAX_PARAMS) return null;
  for (const entry of rawParams) {
    if (!entry || typeof entry !== 'object') return null;
    const item = entry as Record<string, unknown>;
    const name = text(item.name, 12);
    if (!NAME.test(name) || name === 'x' || name === 'pi' || name === 'e') return null;
    if (FUNCTION_NAMES.has(name) || params.some((param) => param.name === name)) return null;
    if (!finite(item.min) || !finite(item.max) || item.max <= item.min) return null;
    const span = item.max - item.min;
    const step = finite(item.step) && item.step > 0 && item.step <= span ? item.step : span / 100;
    const value = finite(item.value) ? Math.min(item.max, Math.max(item.min, item.value)) : item.min;
    params.push({
      name,
      label: text(item.label, 60) || name,
      min: item.min,
      max: item.max,
      value,
      step,
    });
  }

  const variables = ['x', ...params.map((param) => param.name)];
  const curves: ExploreCurve[] = [];
  const rawCurves = Array.isArray(spec.curves) ? spec.curves : [];
  if (rawCurves.length === 0 || rawCurves.length > MAX_CURVES) return null;
  for (const [index, entry] of rawCurves.entries()) {
    if (!entry || typeof entry !== 'object') return null;
    const item = entry as Record<string, unknown>;
    const expr = text(item.expr, 300);
    const evaluate = compileExpression(expr, variables);
    if (!evaluate) return null;
    curves.push({ name: text(item.name, 60) || `Curve ${index + 1}`, expr, evaluate });
  }

  return {
    title: text(spec.title, 120),
    note: text(spec.note, 300),
    xLabel: text(x.label, 60),
    yLabel: text(y.label, 60),
    xMin: x.min,
    xMax: x.max,
    params,
    curves,
  };
}

/** The curves at the given slider positions, as something PlotChart can draw. */
export function sampleExplore(spec: ExploreSpec, values: Record<string, number>): PlotSpec {
  const series = spec.curves.map((curve) => {
    const points: Array<[number, number]> = [];
    for (let index = 0; index <= SAMPLES; index += 1) {
      const x = spec.xMin + ((spec.xMax - spec.xMin) * index) / SAMPLES;
      const y = curve.evaluate({ ...values, x });
      // A pole or a log of zero is a gap in the line, not a spike to infinity.
      if (Number.isFinite(y) && Math.abs(y) < 1e9) points.push([x, y]);
    }
    return { name: curve.name, points };
  });
  return { title: '', xLabel: spec.xLabel, yLabel: spec.yLabel, series };
}

/**
 * One y-range that holds the curves wherever the sliders are, so the axis
 * stays still while a slider moves and the curve is what is seen to change.
 */
export function exploreYDomain(spec: ExploreSpec): [number, number] | null {
  const stops = spec.params.map((param) => [param.min, (param.min + param.max) / 2, param.max]);
  const combos: Array<Record<string, number>> = [{}];
  for (const [index, param] of spec.params.entries()) {
    const next: Array<Record<string, number>> = [];
    for (const combo of combos) {
      for (const stop of stops[index] ?? []) next.push({ ...combo, [param.name]: stop });
    }
    combos.splice(0, combos.length, ...next);
  }
  combos.push(Object.fromEntries(spec.params.map((param) => [param.name, param.value])));

  const values: number[] = [];
  for (const combo of combos) {
    for (const series of sampleExplore(spec, combo).series) {
      for (const point of series.points) values.push(point[1]);
    }
  }
  if (values.length < 2) return null;
  values.sort((a, b) => a - b);
  // Trim the extreme 1% each side: one near-pole would flatten everything else.
  const low = values[Math.floor(values.length * 0.01)] as number;
  const high = values[Math.ceil(values.length * 0.99) - 1] as number;
  if (!(high > low)) return [low - 1, high + 1];
  const pad = (high - low) * 0.06;
  return [low - pad, high + pad];
}
