/**
 * An ```explore block: a formula drawn as a curve, with a slider for each of
 * its parameters.
 *
 * A static graph shows one case; moving a slider shows what the parameter
 * does, which is usually the point of the slide. The curves are computed
 * here from expressions parsed as data (see lib/expression), the y-axis is
 * held still while a slider moves so that the curve is what changes, and a
 * block that does not validate falls back to its text.
 */
import { RotateCcw } from 'lucide-react';
import { useId, useMemo, useState } from 'react';

import { exploreYDomain, parseExploreSpec, sampleExplore } from '../lib/explore';
import { formatNumber } from '../lib/plot';
import { PlotChart } from './PlotChart';

export function ExploreChart({ source }: { source: string }) {
  const spec = useMemo(() => parseExploreSpec(source), [source]);
  const baseId = useId();
  const [moved, setMoved] = useState<Record<string, number>>({});

  const values = useMemo(() => {
    const current: Record<string, number> = {};
    for (const param of spec?.params ?? []) current[param.name] = moved[param.name] ?? param.value;
    return current;
  }, [moved, spec]);
  const yDomain = useMemo(() => (spec ? exploreYDomain(spec) : null), [spec]);
  const sampled = useMemo(() => {
    if (!spec) return null;
    const plot = sampleExplore(spec, values);
    const series = plot.series.filter((item) => item.points.length >= 2);
    return series.length > 0 ? { ...plot, series } : null;
  }, [spec, values]);

  if (!spec || !yDomain) {
    return (
      <div data-testid="explore-fallback">
        <p className="text-xs text-[var(--text-muted)]">
          This interactive graph could not be drawn, so here is its description instead.
        </p>
        <pre className="mt-1 overflow-x-auto rounded-md bg-[var(--surface-sunken)] p-2 text-xs">
          {source}
        </pre>
      </div>
    );
  }

  const changed = Object.keys(moved).length > 0;

  return (
    <figure
      className="my-3 rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-page)] p-3"
      data-testid="explore-chart"
    >
      <figcaption className="mb-2 flex flex-wrap items-center gap-2">
        <span className="rounded-full bg-[var(--surface-selected)] px-2 py-0.5 text-[0.6875rem] font-medium text-[var(--text-accent)]">
          Try it
        </span>
        <span className="min-w-0 flex-1 text-[0.8125rem] font-medium text-[var(--text-primary)]">
          {spec.title || 'Move the sliders'}
        </span>
        {changed ? (
          <button
            type="button"
            onClick={() => setMoved({})}
            className="flex items-center gap-1 rounded-[var(--radius-control)] px-1.5 py-0.5 text-[0.6875rem] text-[var(--text-secondary)] hover:bg-[var(--surface-hover)]"
          >
            <RotateCcw size={11} /> Reset
          </button>
        ) : null}
      </figcaption>

      {spec.params.length > 0 ? (
        <div className="mb-2 grid grid-cols-1 gap-x-5 gap-y-2 @2xl:grid-cols-2">
          {spec.params.map((param) => {
            const id = `${baseId}-${param.name}`;
            const value = values[param.name] ?? param.value;
            return (
              <div key={param.name} className="min-w-0">
                <label
                  htmlFor={id}
                  className="flex items-baseline justify-between gap-2 text-[0.75rem] text-[var(--text-secondary)]"
                >
                  <span className="truncate">{param.label}</span>
                  <strong className="font-semibold tabular-nums text-[var(--text-primary)]">
                    {formatNumber(value)}
                  </strong>
                </label>
                <input
                  id={id}
                  type="range"
                  min={param.min}
                  max={param.max}
                  step={param.step}
                  value={value}
                  onChange={(event) =>
                    setMoved((current) => ({ ...current, [param.name]: Number(event.target.value) }))
                  }
                  className="sp-range mt-1 w-full"
                />
              </div>
            );
          })}
        </div>
      ) : null}

      {sampled ? (
        <PlotChart spec={sampled} yDomain={yDomain} numbers={false} />
      ) : (
        <p className="py-6 text-center text-[0.75rem] text-[var(--text-muted)]">
          Nothing to draw at these values. Move a slider.
        </p>
      )}

      {spec.note ? (
        <p className="mt-1 text-[0.75rem] leading-relaxed text-[var(--text-secondary)]">
          {spec.note}
        </p>
      ) : null}
      <p className="mt-1 font-mono text-[0.6875rem] text-[var(--text-muted)]">
        {spec.curves.map((curve) => `${curve.name} = ${curve.expr}`).join('   ·   ')}
      </p>
    </figure>
  );
}
