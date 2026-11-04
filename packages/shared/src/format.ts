/** Formatting helpers shared by both hosts. */

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

export function formatRelativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return '';
  const seconds = Math.round((now.getTime() - then.getTime()) / 1000);
  if (seconds < 45) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days} day${days === 1 ? '' : 's'} ago`;
  return then.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}:${String(rest).padStart(2, '0')}`;
}

export function formatPercent(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`;
}

/** Human label for a topic slug when no name is available. */
export function humaniseSlug(slug: string): string {
  if (!slug) return 'Untitled topic';
  const words = slug.replace(/-/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const PROVENANCE_LABELS: Record<string, string> = {
  source_derived: 'From your slides',
  ai_generated: 'AI generated',
  past_paper: 'Past paper',
  past_paper_variation: 'Past-paper variation',
  user: 'Written by you',
};

/**
 * Describe where a question or card came from.
 *
 * The distinction matters to a student revising: a question lifted from their
 * own slides is a different thing from one a model invented, and claiming a
 * past paper that does not exist would be a lie.
 */
export function provenanceLabel(provenance: string): string {
  return PROVENANCE_LABELS[provenance] ?? provenance;
}

const ACTION_LABELS: Record<string, string> = {
  review_flashcards: 'Review due flashcards',
  revisit_prerequisite: 'Revisit a prerequisite',
  targeted_explanation: 'Get a focused explanation',
  checkpoint_quiz: 'Take a checkpoint quiz',
  additional_practice: 'Practise a little more',
  practise_harder_questions: 'Try harder questions',
  advance: 'Move on to the next topic',
  upload_material: 'Upload the relevant lecture',
  review_source: 'Read the cited pages',
  next_hint: 'Try the step, then ask for the next hint',
  continue_reading: 'Keep reading',
};

export function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? humaniseSlug(action.replace(/_/g, '-'));
}

const MASTERY_LABELS: Record<string, string> = {
  unknown: 'Not enough evidence',
  weak: 'Needs work',
  developing: 'Getting there',
  strong: 'Solid',
};

export function masteryLabel(label: string): string {
  return MASTERY_LABELS[label] ?? label;
}
