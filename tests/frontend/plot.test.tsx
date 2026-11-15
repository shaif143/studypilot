/**
 * ```plot fences in a model's text.
 *
 * The text is untrusted, so the parser is the subject: exactly a JSON object
 * of number pairs is a chart, and anything else is refused and shown as text.
 * The component is checked for what a reader relies on — the title, the
 * series names, the numbers as a table, and the fallback.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { GroundedAnswer } from '@studypilot/app';
import { PlotChart } from '../../packages/app/src/components/PlotChart';
import {
  MAX_SERIES,
  formatNumber,
  nearestPoint,
  niceTicks,
  parsePlotSpec,
} from '../../packages/app/src/lib/plot';

const SIGMOID = JSON.stringify({
  title: 'Sigmoid and tanh',
  x: 'input',
  y: 'output',
  series: [
    { name: 'sigmoid', points: [[2, 0.88], [-2, 0.12], [0, 0.5]] },
    { name: 'tanh', points: [[-2, -0.96], [0, 0], [2, 0.96]] },
  ],
});

describe('parsePlotSpec', () => {
  it('reads a chart and sorts each series by x', () => {
    const spec = parsePlotSpec(SIGMOID);
    expect(spec?.title).toBe('Sigmoid and tanh');
    expect(spec?.xLabel).toBe('input');
    expect(spec?.series.map((s) => s.name)).toEqual(['sigmoid', 'tanh']);
    expect(spec?.series[0]?.points).toEqual([
      [-2, 0.12],
      [0, 0.5],
      [2, 0.88],
    ]);
  });

  it.each([
    ['not JSON', 'y = x^2'],
    ['an array', '[[1, 2], [3, 4]]'],
    ['no series', '{"title": "t"}'],
    ['an empty series list', '{"series": []}'],
    ['one point', '{"series": [{"points": [[1, 2]]}]}'],
    ['a string for a number', '{"series": [{"points": [[1, "2"], [2, 3]]}]}'],
    ['a triple', '{"series": [{"points": [[1, 2, 3], [2, 3, 4]]}]}'],
    ['an expression', '{"series": [{"points": "Math.sin(x)"}]}'],
  ])('refuses %s', (_name, source) => {
    expect(parsePlotSpec(source)).toBeNull();
  });

  it('keeps at most the series the palette can tell apart, and names a nameless one', () => {
    const many = {
      series: Array.from({ length: 6 }, () => ({ points: [[0, 0], [1, 1]] })),
    };
    const spec = parsePlotSpec(JSON.stringify(many));
    expect(spec?.series).toHaveLength(MAX_SERIES);
    expect(spec?.series[0]?.name).toBe('Series 1');
  });
});

describe('axis helpers', () => {
  it('picks round ticks that cover the data', () => {
    expect(niceTicks(0, 1, 5)).toEqual([0, 0.2, 0.4, 0.6, 0.8, 1]);
    expect(niceTicks(-0.96, 0.96, 5)).toEqual([-1, -0.5, 0, 0.5, 1]);
    const ticks = niceTicks(3, 97, 5);
    expect(ticks[0]).toBeLessThanOrEqual(3);
    expect(ticks[ticks.length - 1]).toBeGreaterThanOrEqual(97);
    // The last tick encloses the top value: a flat line at 32 must not be cut off at 30.
    expect(niceTicks(2, 32, 5)).toEqual([0, 10, 20, 30, 40]);
  });

  it('gives a flat series a range instead of dividing by zero', () => {
    const ticks = niceTicks(5, 5);
    expect(ticks.length).toBeGreaterThan(1);
    expect(ticks.every(Number.isFinite)).toBe(true);
  });

  it('formats numbers without floating-point dust', () => {
    expect(formatNumber(0.30000000000000004)).toBe('0.3');
    expect(formatNumber(0)).toBe('0');
    expect(formatNumber(1500)).toBe('1500');
    expect(formatNumber(1234567)).toBe('1.2e6');
  });

  it('finds the nearest point of a series', () => {
    expect(nearestPoint([[0, 1], [1, 2], [5, 9]], 1.4)).toEqual([1, 2]);
  });
});

describe('PlotChart', () => {
  it('draws the lines, names the series and keeps the numbers readable as a table', () => {
    const { container } = render(<PlotChart source={SIGMOID} />);
    expect(screen.getByTestId('plot-chart')).toBeInTheDocument();
    expect(screen.getByText('Sigmoid and tanh')).toBeInTheDocument();
    expect(container.querySelectorAll('polyline')).toHaveLength(2);
    // Legend and end label: identity is never the colour alone.
    expect(screen.getAllByText('sigmoid').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('Show the numbers')).toBeInTheDocument();
    expect(container.querySelectorAll('tbody tr')).toHaveLength(6);
  });

  it('shows the text when the block is not a chart', () => {
    render(<PlotChart source="plot sin(x) please" />);
    expect(screen.getByTestId('plot-fallback')).toBeInTheDocument();
    expect(screen.getByText('plot sin(x) please')).toBeInTheDocument();
  });

  it('is what a plot fence in an answer becomes', () => {
    const { container } = render(
      <GroundedAnswer markdown={`Here it is:\n\n\`\`\`plot\n${SIGMOID}\n\`\`\`\n`} citations={[]} />,
    );
    expect(screen.getByTestId('plot-chart')).toBeInTheDocument();
    expect(container.querySelector('code.language-plot')).toBeNull();
    // A picture is not a code block: no <pre> around it.
    expect(container.querySelector('pre [data-testid="plot-chart"]')).toBeNull();
  });
});
