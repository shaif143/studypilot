/**
 * The source list shown beneath an answer.
 *
 * Shows every passage the answer was built from, whether or not the model cited
 * it inline, because a student checking an answer needs to see the evidence
 * that was actually used.
 */
import type { Citation } from '@studypilot/shared';
import { Badge } from '@studypilot/ui';

export function CitationList({
  citations,
  onSelect,
  compact = false,
}: {
  citations: Citation[];
  onSelect?: (citation: Citation) => void;
  compact?: boolean;
}) {
  if (citations.length === 0) return null;

  return (
    <ul className="mt-3 space-y-1.5">
      {citations.map((citation) => (
        <li key={`${citation.marker}-${citation.chunk_id}`}>
          <button
            type="button"
            onClick={() => onSelect?.(citation)}
            disabled={!onSelect}
            className={
              'group flex w-full items-start gap-2.5 rounded-[var(--radius-control)] ' +
              'border border-[var(--border-subtle)] bg-[var(--surface-sunken)] px-2.5 py-2 ' +
              'text-left transition-colors enabled:hover:border-[var(--color-accent-300)] ' +
              'enabled:hover:bg-[var(--surface-hover)] disabled:cursor-default'
            }
          >
            <span className="sp-citation mt-0.5 shrink-0 cursor-inherit">{citation.marker}</span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-1.5">
                <span className="text-[0.75rem] font-medium text-[var(--text-primary)]">
                  Page {citation.page_number}
                </span>
                {citation.section ? (
                  <span className="truncate text-[0.75rem] text-[var(--text-secondary)]">
                    {citation.section}
                  </span>
                ) : null}
                {citation.cited_inline === false ? (
                  <Badge tone="caution" title="The answer did not reference this passage inline.">
                    not cited inline
                  </Badge>
                ) : null}
              </span>
              {!compact && citation.quote ? (
                <span className="mt-1 block text-[0.75rem] leading-relaxed text-[var(--text-secondary)]">
                  &ldquo;{citation.quote}&rdquo;
                </span>
              ) : null}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
