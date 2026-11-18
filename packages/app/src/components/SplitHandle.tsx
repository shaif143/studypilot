/**
 * A bar between two panes that are side by side, dragged to share the width
 * between them differently.
 *
 * The share is the right-hand pane's part of the row, as a percentage. It is
 * kept between a minimum and a maximum so neither pane can be dragged out of
 * reach, and remembered, because how much room a slide needs is a matter of
 * the reader's screen and eyes rather than of the slide. It works from the
 * keyboard as a separator does: the arrow keys move it, Home and End take it
 * to its limits, and Enter or a double click puts it back.
 */
import {
  type KeyboardEvent,
  type PointerEvent,
  type RefObject,
  useCallback,
  useRef,
  useState,
} from 'react';

export interface SplitLimits {
  min: number;
  max: number;
  initial: number;
}

/** The right-hand pane's share, in percent, for a pointer at ``clientX``. */
export function shareAt(
  clientX: number,
  row: { left: number; width: number },
  limits: Pick<SplitLimits, 'min' | 'max'>,
): number {
  if (row.width <= 0) return limits.min;
  const fromRight = ((row.left + row.width - clientX) / row.width) * 100;
  return Math.min(limits.max, Math.max(limits.min, Math.round(fromRight * 10) / 10));
}

/** A remembered share. Storage that is unavailable only means it is not remembered. */
export function useSplitShare(
  storageKey: string,
  limits: SplitLimits,
): [share: number, setShare: (next: number) => void, reset: () => void] {
  const [share, setShareState] = useState(() => {
    try {
      const stored = Number.parseFloat(window.localStorage.getItem(storageKey) ?? '');
      if (Number.isFinite(stored)) return Math.min(limits.max, Math.max(limits.min, stored));
    } catch {
      // Falls through to the starting share.
    }
    return limits.initial;
  });
  const setShare = useCallback(
    (next: number) => {
      const clamped = Math.min(limits.max, Math.max(limits.min, next));
      setShareState(clamped);
      try {
        window.localStorage.setItem(storageKey, String(clamped));
      } catch {
        // Kept for this visit all the same.
      }
    },
    [limits.max, limits.min, storageKey],
  );
  const reset = useCallback(() => setShare(limits.initial), [limits.initial, setShare]);
  return [share, setShare, reset];
}

export function SplitHandle({
  rowRef,
  share,
  limits,
  onChange,
  onReset,
  label,
  className = '',
}: {
  /** The row holding both panes; the share is measured against its width. */
  rowRef: RefObject<HTMLElement | null>;
  share: number;
  limits: SplitLimits;
  onChange: (share: number) => void;
  onReset: () => void;
  label: string;
  className?: string;
}) {
  const dragging = useRef(false);
  const [active, setActive] = useState(false);

  const move = (event: PointerEvent<HTMLDivElement>) => {
    if (!dragging.current || !rowRef.current) return;
    onChange(shareAt(event.clientX, rowRef.current.getBoundingClientRect(), limits));
  };
  const end = (event: PointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    dragging.current = false;
    setActive(false);
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 10 : 2;
    // The handle sits at the left edge of the right-hand pane: moving it left
    // gives that pane more of the row.
    if (event.key === 'ArrowLeft') onChange(share + step);
    else if (event.key === 'ArrowRight') onChange(share - step);
    else if (event.key === 'Home') onChange(limits.max);
    else if (event.key === 'End') onChange(limits.min);
    else if (event.key === 'Enter') onReset();
    else return;
    event.preventDefault();
    // These keys are the bar's while it has the focus: a screen that also
    // listens for the arrows (to turn a page, say) must not act on them too.
    event.stopPropagation();
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuemin={limits.min}
      aria-valuemax={limits.max}
      aria-valuenow={Math.round(share)}
      tabIndex={0}
      title="Drag to resize. Double-click to reset."
      onPointerDown={(event) => {
        dragging.current = true;
        setActive(true);
        event.currentTarget.setPointerCapture?.(event.pointerId);
        event.preventDefault();
      }}
      onPointerMove={move}
      onPointerUp={end}
      onPointerCancel={end}
      onDoubleClick={onReset}
      onKeyDown={onKeyDown}
      className={[
        'group relative w-2 shrink-0 cursor-col-resize touch-none select-none outline-none',
        className,
      ].join(' ')}
    >
      {/* The line that is seen; the element around it is the wider thing that is grabbed. */}
      <span
        className={[
          'absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-[width,background-color]',
          active
            ? 'w-[3px] bg-[var(--border-focus)]'
            : 'bg-[var(--border-subtle)] group-hover:w-[3px] group-hover:bg-[var(--border-focus)] group-focus-visible:w-[3px] group-focus-visible:bg-[var(--border-focus)]',
        ].join(' ')}
      />
      <span
        className={[
          'absolute left-1/2 top-1/2 h-9 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full',
          active
            ? 'bg-[var(--border-focus)]'
            : 'bg-[var(--border-strong)] group-hover:bg-[var(--border-focus)]',
        ].join(' ')}
      />
    </div>
  );
}
