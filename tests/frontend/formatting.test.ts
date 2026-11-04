/**
 * Shared formatting and labelling.
 *
 * `provenanceLabel` is load-bearing rather than cosmetic: it is what tells a
 * student whether a question came from their own slides, from a model, or from
 * a genuine past paper.
 */
import {
  actionLabel,
  formatBytes,
  formatDuration,
  formatPercent,
  formatRelativeTime,
  humaniseSlug,
  masteryLabel,
  provenanceLabel,
} from '@studypilot/shared';
import { describe, expect, it } from 'vitest';

describe('formatBytes', () => {
  it('scales units and keeps the number readable', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(1024)).toBe('1.0 KB');
    expect(formatBytes(1024 * 1024 * 3.5)).toBe('3.5 MB');
    expect(formatBytes(1024 * 1024 * 48)).toBe('48 MB');
    expect(formatBytes(1024 ** 3 * 2)).toBe('2.0 GB');
  });
});

describe('formatRelativeTime', () => {
  const now = new Date('2026-11-04T12:00:00Z');

  it('describes recent times in human terms', () => {
    expect(formatRelativeTime('2026-11-04T11:59:40Z', now)).toBe('just now');
    expect(formatRelativeTime('2026-11-04T11:45:00Z', now)).toBe('15 min ago');
    expect(formatRelativeTime('2026-11-04T09:00:00Z', now)).toBe('3 hours ago');
    expect(formatRelativeTime('2026-11-03T12:00:00Z', now)).toBe('1 day ago');
  });

  it('falls back to a date beyond a week', () => {
    const result = formatRelativeTime('2026-09-01T12:00:00Z', now);
    expect(result).toMatch(/2026/);
    expect(result).not.toContain('ago');
  });

  it('returns an empty string for an unparseable value rather than NaN', () => {
    expect(formatRelativeTime('not a date', now)).toBe('');
  });
});

describe('formatDuration', () => {
  it('renders a countdown', () => {
    expect(formatDuration(0)).toBe('0:00');
    expect(formatDuration(65)).toBe('1:05');
    expect(formatDuration(3600)).toBe('60:00');
  });

  it('never renders a negative clock', () => {
    expect(formatDuration(-10)).toBe('0:00');
  });
});

describe('labels', () => {
  it('distinguishes where a question actually came from', () => {
    expect(provenanceLabel('source_derived')).toBe('From your slides');
    expect(provenanceLabel('ai_generated')).toBe('AI generated');
    expect(provenanceLabel('past_paper')).toBe('Past paper');
    expect(provenanceLabel('past_paper_variation')).toBe('Past-paper variation');
    // An unrecognised value is shown as-is rather than guessed at.
    expect(provenanceLabel('something_new')).toBe('something_new');
  });

  it('never claims a mastery level without evidence', () => {
    expect(masteryLabel('unknown')).toBe('Not enough evidence');
    expect(masteryLabel('weak')).toBe('Needs work');
    expect(masteryLabel('strong')).toBe('Solid');
  });

  it('describes recommended actions in plain language', () => {
    expect(actionLabel('revisit_prerequisite')).toBe('Revisit a prerequisite');
    expect(actionLabel('review_flashcards')).toBe('Review due flashcards');
    // An action with no mapping is humanised rather than shown as a raw slug.
    expect(actionLabel('unmapped_action')).toBe('Unmapped action');
  });

  it('humanises topic slugs', () => {
    expect(humaniseSlug('backpropagation-through-convolution')).toBe(
      'Backpropagation through convolution',
    );
    expect(humaniseSlug('')).toBe('Untitled topic');
  });

  it('formats percentages', () => {
    expect(formatPercent(0.734)).toBe('73%');
    expect(formatPercent(0.734, 1)).toBe('73.4%');
  });
});
