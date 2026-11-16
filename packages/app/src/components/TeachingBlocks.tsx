/**
 * Two blocks that make an explanation something to do rather than only read.
 *
 * `StepThrough` reveals a derivation or a procedure one step at a time, so
 * the reader can try the next step before seeing it. `SelfCheck` asks short
 * questions with the answers hidden until asked for. Both take the Markdown
 * renderer from their caller, which keeps maths and citations working inside
 * them without this file importing the renderer that imports it.
 */
import { Check, ChevronRight, Eye, ListOrdered } from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';

import { parseQuiz, parseSteps } from '../lib/blocks';

type RenderMarkdown = (markdown: string) => ReactNode;

function Fallback({ source, what }: { source: string; what: string }) {
  return (
    <pre
      data-testid={`${what}-fallback`}
      className="overflow-x-auto rounded-md bg-[var(--surface-sunken)] p-2 text-xs"
    >
      {source}
    </pre>
  );
}

export function StepThrough({ source, render }: { source: string; render: RenderMarkdown }) {
  const steps = useMemo(() => parseSteps(source), [source]);
  const [shown, setShown] = useState(1);
  if (steps.length < 2) return <Fallback source={source} what="steps" />;

  const visible = Math.min(shown, steps.length);
  const finished = visible >= steps.length;

  return (
    <section
      data-testid="step-through"
      className="my-3 rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-page)] p-3"
    >
      <header className="mb-2 flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1 rounded-full bg-[var(--surface-selected)] px-2 py-0.5 text-[0.6875rem] font-medium text-[var(--text-accent)]">
          <ListOrdered size={11} /> Step by step
        </span>
        <span className="text-[0.6875rem] tabular-nums text-[var(--text-muted)]">
          {visible} of {steps.length}
        </span>
        <span className="flex-1" />
        {!finished ? (
          <button
            type="button"
            onClick={() => setShown(steps.length)}
            className="rounded-[var(--radius-control)] px-1.5 py-0.5 text-[0.6875rem] text-[var(--text-secondary)] hover:bg-[var(--surface-hover)]"
          >
            Show all
          </button>
        ) : (
          <button
            type="button"
            onClick={() => setShown(1)}
            className="rounded-[var(--radius-control)] px-1.5 py-0.5 text-[0.6875rem] text-[var(--text-secondary)] hover:bg-[var(--surface-hover)]"
          >
            Start again
          </button>
        )}
      </header>

      <ol className="!list-none space-y-2 !pl-0">
        {steps.slice(0, visible).map((step, index) => (
          <li key={`${index}-${step.title}`} className="flex gap-3">
            <span
              aria-hidden
              className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--color-accent-600)] text-[0.6875rem] font-semibold text-white"
            >
              {index + 1}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[0.875rem] font-semibold text-[var(--text-primary)]">
                {step.title}
              </p>
              {step.body ? <div className="mt-1">{render(step.body)}</div> : null}
            </div>
          </li>
        ))}
      </ol>

      {!finished ? (
        <button
          type="button"
          onClick={() => setShown(visible + 1)}
          className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-[var(--radius-control)] border border-dashed border-[var(--border-strong)] px-3 py-2 text-[0.8125rem] font-medium text-[var(--text-accent)] transition-colors hover:bg-[var(--surface-hover)]"
        >
          Next: {steps[visible]?.title}
          <ChevronRight size={14} />
        </button>
      ) : (
        <p className="mt-3 flex items-center gap-1.5 text-[0.75rem] text-[var(--signal-grounded)]">
          <Check size={13} /> That is every step.
        </p>
      )}
    </section>
  );
}

export function SelfCheck({ source, render }: { source: string; render: RenderMarkdown }) {
  const items = useMemo(() => parseQuiz(source), [source]);
  const [open, setOpen] = useState<Set<number>>(() => new Set());
  if (items.length === 0) return <Fallback source={source} what="quiz" />;

  return (
    <section data-testid="self-check" className="my-3 space-y-2">
      {items.map((item, index) => {
        const revealed = open.has(index);
        return (
          <div
            key={`${index}-${item.question.slice(0, 24)}`}
            className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-page)] p-3"
          >
            <div className="flex gap-2">
              <span className="mt-0.5 shrink-0 text-[0.75rem] font-semibold tabular-nums text-[var(--text-accent)]">
                Q{index + 1}
              </span>
              <div className="min-w-0 flex-1">{render(item.question)}</div>
            </div>
            {revealed ? (
              <div className="mt-2 border-t border-[var(--border-subtle)] pt-2">
                {render(item.answer)}
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setOpen((current) => new Set(current).add(index))}
                className="mt-2 flex items-center gap-1.5 rounded-[var(--radius-control)] px-2 py-1 text-[0.75rem] font-medium text-[var(--text-accent)] hover:bg-[var(--surface-hover)]"
              >
                <Eye size={13} /> Think first, then show the answer
              </button>
            )}
          </div>
        );
      })}
    </section>
  );
}
