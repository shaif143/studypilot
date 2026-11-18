/**
 * The bar between the explanation and the original slide.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { useRef } from 'react';
import { beforeEach, describe, expect, it } from 'vitest';

import {
  SplitHandle,
  type SplitLimits,
  shareAt,
  useSplitShare,
} from '../../packages/app/src/components/SplitHandle';

const LIMITS: SplitLimits = { min: 18, max: 78, initial: 33.3 };

describe('where the bar is', () => {
  const row = { left: 100, width: 1000 };

  it('gives the right-hand pane what lies to the right of the pointer', () => {
    expect(shareAt(600, row, LIMITS)).toBe(50);
    expect(shareAt(766.5, row, LIMITS)).toBe(33.4);
  });

  it('never lets either pane be dragged out of reach', () => {
    expect(shareAt(1095, row, LIMITS)).toBe(18);
    expect(shareAt(120, row, LIMITS)).toBe(78);
    expect(shareAt(500, { left: 0, width: 0 }, LIMITS)).toBe(18);
  });
});

function Harness() {
  const row = useRef<HTMLDivElement>(null);
  const [share, setShare, reset] = useSplitShare('test.split', LIMITS);
  return (
    <div ref={row}>
      <SplitHandle
        rowRef={row}
        share={share}
        limits={LIMITS}
        onChange={setShare}
        onReset={reset}
        label="Resize"
      />
      <output>{share}</output>
    </div>
  );
}

describe('the bar from the keyboard', () => {
  beforeEach(() => window.localStorage.clear());

  it('moves with the arrows, stops at its limits and resets', () => {
    render(<Harness />);
    const bar = screen.getByRole('separator', { name: 'Resize' });
    const share = () => Number(screen.getByRole('status').textContent);

    expect(share()).toBe(33.3);
    fireEvent.keyDown(bar, { key: 'ArrowLeft' });
    expect(share()).toBeCloseTo(35.3);
    fireEvent.keyDown(bar, { key: 'ArrowRight', shiftKey: true });
    expect(share()).toBeCloseTo(25.3);
    fireEvent.keyDown(bar, { key: 'Home' });
    expect(share()).toBe(78);
    fireEvent.keyDown(bar, { key: 'ArrowLeft' });
    expect(share()).toBe(78);
    fireEvent.keyDown(bar, { key: 'End' });
    expect(share()).toBe(18);
    expect(bar).toHaveAttribute('aria-valuenow', '18');
    fireEvent.keyDown(bar, { key: 'Enter' });
    expect(share()).toBe(33.3);
  });

  it('is remembered, and a stored value out of range is brought back in', () => {
    const first = render(<Harness />);
    fireEvent.keyDown(screen.getByRole('separator'), { key: 'Home' });
    first.unmount();
    render(<Harness />);
    expect(screen.getByRole('status').textContent).toBe('78');

    window.localStorage.setItem('test.split', '500');
    const { container } = render(<Harness />);
    expect(container.querySelector('output')?.textContent).toBe('78');
  });
});
