/**
 * The blocks that make an explanation something to do: ```explore, ```steps
 * and ```quiz, and the callouts.
 *
 * All of them are a model's text. The subject is therefore what is refused:
 * an expression is a small arithmetic language and nothing else, so anything
 * that is not arithmetic must fail to compile rather than reach the engine.
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { GroundedAnswer } from '@studypilot/app';
import { calloutTone, parseQuiz, parseSteps } from '../../packages/app/src/lib/blocks';
import {
  exploreYDomain,
  parseExploreSpec,
  sampleExplore,
} from '../../packages/app/src/lib/explore';
import { compileExpression } from '../../packages/app/src/lib/expression';

const run = (source: string, scope: Record<string, number> = {}) =>
  compileExpression(source, Object.keys(scope))?.(scope);

describe('the expression language', () => {
  it('does arithmetic with the usual precedence', () => {
    expect(run('1 + 2 * 3')).toBe(7);
    expect(run('(1 + 2) * 3')).toBe(9);
    expect(run('2 ^ 3 ^ 2')).toBe(512); // right-associative
    expect(run('-2 ^ 2')).toBe(-4); // the sign applies after the power
    expect(run('2 ^ -1')).toBe(0.5);
    expect(run('10 / 4 - 1')).toBe(1.5);
    expect(run('1.5e2 + .5')).toBe(150.5);
  });

  it('knows variables, constants and its fixed list of functions', () => {
    expect(run('A * sin(2 * pi * f * x)', { A: 2, f: 1, x: 0.25 })).toBeCloseTo(2, 10);
    expect(run('exp(ln(e))')).toBeCloseTo(Math.E, 10);
    expect(run('max(1, x, 3) + min(4, 2)', { x: 7 })).toBe(9);
    expect(run('sinc(0)')).toBe(1);
    expect(run('rect(0.4) + rect(0.6) + step(-1) + step(0)')).toBe(2);
    expect(run('log10(1000) + log2(8)')).toBe(6);
  });

  it('refuses anything that is not arithmetic', () => {
    for (const source of [
      '',
      'alert(1)',
      'x.constructor',
      'constructor("return 1")()',
      'window',
      'x; 1',
      'x = 2',
      '"a"',
      '`a`',
      'x[0]',
      '(() => 1)()',
      'sin',
      'sin()',
      'sin(1, 2)',
      'pow(2)',
      '2 x',
      '(1 + 2',
      '1 +',
      'y + 1',
      '__proto__',
      'toString()',
      'hasOwnProperty(1)',
      '1'.repeat(400),
    ]) {
      expect(compileExpression(source, ['x']), source).toBeNull();
    }
  });

  it('gives a gap, not an exception, where a formula has no value', () => {
    expect(run('1 / x', { x: 0 })).toBe(Number.POSITIVE_INFINITY);
    expect(Number.isNaN(run('sqrt(x)', { x: -1 }) as number)).toBe(true);
  });
});

const EXPLORE = {
  title: 'A sine wave',
  x: { label: 't (ms)', min: 0, max: 20 },
  y: { label: 'amplitude' },
  params: [
    { name: 'f', label: 'frequency (Hz)', min: 50, max: 500, value: 100, step: 10 },
    { name: 'A', label: 'amplitude', min: 0, max: 2, value: 1 },
  ],
  curves: [{ name: 'signal', expr: 'A*sin(2*pi*f*x/1000)' }],
  note: 'Raise the frequency and count the cycles.',
};

describe('parseExploreSpec', () => {
  it('reads a formula with sliders', () => {
    const spec = parseExploreSpec(JSON.stringify(EXPLORE));
    expect(spec?.params.map((param) => [param.name, param.value, param.step])).toEqual([
      ['f', 100, 10],
      ['A', 1, 0.02],
    ]);
    const sampled = sampleExplore(spec!, { f: 100, A: 1 });
    expect(sampled.series[0]?.points).toHaveLength(241);
    // 100 Hz over 20 ms is two cycles: a peak a quarter of the way through the first.
    const peak = sampled.series[0]?.points.find(([x]) => Math.abs(x - 2.5) < 1e-9);
    expect(peak?.[1]).toBeCloseTo(1, 6);
  });

  it('holds one y-range for every slider position', () => {
    const spec = parseExploreSpec(JSON.stringify(EXPLORE))!;
    const [low, high] = exploreYDomain(spec)!;
    // The amplitude slider goes to 2, so the frame must hold +/-2, not just +/-1.
    expect(low).toBeLessThan(-1.9);
    expect(high).toBeGreaterThan(1.9);
  });

  it('refuses a block it cannot trust or draw', () => {
    const bad = (change: Record<string, unknown>) =>
      parseExploreSpec(JSON.stringify({ ...EXPLORE, ...change }));
    expect(parseExploreSpec('not json')).toBeNull();
    expect(bad({ curves: [] })).toBeNull();
    expect(bad({ curves: [{ name: 'x', expr: 'fetch("/api")' }] })).toBeNull();
    expect(bad({ curves: [{ name: 'x', expr: 'A*g*x' }] })).toBeNull(); // g is not declared
    expect(bad({ x: { min: 5, max: 5 } })).toBeNull();
    expect(bad({ params: [{ name: 'sin', min: 0, max: 1 }] })).toBeNull();
    expect(bad({ params: [{ name: 'x', min: 0, max: 1 }] })).toBeNull();
    expect(bad({ params: [{ name: 'a b', min: 0, max: 1 }] })).toBeNull();
    expect(bad({ params: [{ name: 'k', min: 2, max: 1 }] })).toBeNull();
    expect(bad({ params: Array.from({ length: 4 }, (_, i) => ({ name: `p${i}`, min: 0, max: 1 })) })).toBeNull();
  });
});

describe('steps, self-checks and callouts', () => {
  it('splits a derivation into its steps', () => {
    const steps = parseSteps('## Step 1: Write the sum\n$a + b$\n\n## Simplify\nCollect terms.\n');
    expect(steps).toEqual([
      { title: 'Write the sum', body: '$a + b$' },
      { title: 'Simplify', body: 'Collect terms.' },
    ]);
    expect(parseSteps('no headings here')).toEqual([]);
  });

  it('pairs questions with their answers', () => {
    const items = parseQuiz('Q: Why pad?\nA: To keep the size,\nand the borders.\n\n2. Q: And stride?\nA: Step size.');
    expect(items).toEqual([
      { question: 'Why pad?', answer: 'To keep the size,\nand the borders.' },
      { question: 'And stride?', answer: 'Step size.' },
    ]);
    expect(parseQuiz('Q: a question with no answer')).toEqual([]);
  });

  it('recognises a callout by its label', () => {
    expect(calloutTone('Key idea: sample fast enough')).toBe('key');
    expect(calloutTone('Common mistake: forgetting the factor of two')).toBe('mistake');
    expect(calloutTone('Exam tip: state the theorem')).toBe('exam');
    expect(calloutTone('In class: the lecturer drew this')).toBe('class');
    expect(calloutTone('As Shannon wrote')).toBeNull();
  });
});

describe('in an answer', () => {
  it('draws an explore block with a slider per parameter, and redraws when one moves', async () => {
    const { container } = render(
      <GroundedAnswer
        markdown={`Try it:\n\n\`\`\`explore\n${JSON.stringify(EXPLORE)}\n\`\`\`\n`}
        citations={[]}
      />,
    );
    expect(screen.getByTestId('explore-chart')).toBeInTheDocument();
    expect(container.querySelector('pre')).toBeNull(); // not shown as code
    const slider = screen.getByLabelText(/frequency \(Hz\)/);
    expect(slider).toHaveAttribute('max', '500');
    const before = container.querySelector('polyline')?.getAttribute('points');

    // A range input has no typing; set it the way the browser would.
    const { fireEvent } = await import('@testing-library/react');
    fireEvent.change(slider, { target: { value: '300' } });
    expect(container.querySelector('polyline')?.getAttribute('points')).not.toBe(before);
    expect(screen.getByText('300')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Reset/ }));
    expect(container.querySelector('polyline')?.getAttribute('points')).toBe(before);
  });

  it('shows the text of an explore block it will not draw', () => {
    render(
      <GroundedAnswer
        markdown={'```explore\n{"x":{"min":0,"max":1},"curves":[{"expr":"document.cookie"}]}\n```'}
        citations={[]}
      />,
    );
    expect(screen.getByTestId('explore-fallback')).toHaveTextContent('document.cookie');
    expect(screen.queryByTestId('explore-chart')).toBeNull();
  });

  it('reveals a derivation one step at a time', async () => {
    render(
      <GroundedAnswer
        markdown={'```steps\n## Start\nWrite $x$.\n## Middle\nDouble it.\n## End\nDone.\n```'}
        citations={[]}
      />,
    );
    expect(screen.getByText('Start')).toBeInTheDocument();
    expect(screen.queryByText('Double it.')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /Next: Middle/ }));
    expect(screen.getByText('Double it.')).toBeInTheDocument();
    expect(screen.queryByText('Done.')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Show all' }));
    expect(screen.getByText('Done.')).toBeInTheDocument();
    expect(screen.getByText('That is every step.')).toBeInTheDocument();
  });

  it('keeps a self-check answer hidden until it is asked for', async () => {
    render(
      <GroundedAnswer markdown={'```quiz\nQ: Why pad?\nA: To keep the size.\n```'} citations={[]} />,
    );
    expect(screen.getByText('Why pad?')).toBeInTheDocument();
    expect(screen.queryByText('To keep the size.')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /show the answer/ }));
    expect(screen.getByText('To keep the size.')).toBeInTheDocument();
  });

  it('styles a labelled quote line as a callout and leaves a real quotation alone', () => {
    const { container } = render(
      <GroundedAnswer
        markdown={'> **Key idea:** sample at twice the bandwidth.\n\nText.\n\n> A plain quotation.'}
        citations={[]}
      />,
    );
    const quotes = container.querySelectorAll('blockquote');
    expect(quotes[0]).toHaveClass('sp-callout', 'sp-callout-key');
    expect(quotes[1]).not.toHaveClass('sp-callout');
  });
});
