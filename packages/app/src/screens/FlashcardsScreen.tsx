/**
 * Flashcards with spaced repetition.
 *
 * Each card shows where it came from. A card lifted from a slide is labelled as
 * such; nothing is ever presented as a past-paper question unless the student
 * uploaded a past paper and the card actually came from it.
 */
import { formatRelativeTime, humaniseSlug, provenanceLabel } from '@studypilot/shared';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  SectionHeading,
  Select,
  Skeleton,
  Tabs,
} from '@studypilot/ui';
import { BookMarked, Eye, RotateCcw, Sparkles } from 'lucide-react';
import { useMemo, useState } from 'react';

import { ErrorPanel, PageBody, PageHeader } from '../components/AppShell';
import {
  describeError,
  useCourses,
  useDocuments,
  useFlashcards,
  useGenerateFlashcards,
  useProviders,
  useReviewFlashcard,
  useSettings,
} from '../hooks/queries';

const RATINGS = [
  { value: 0, label: 'Again', hint: 'Back in 10 minutes' },
  { value: 1, label: 'Hard', hint: 'Shorter interval' },
  { value: 2, label: 'Good', hint: 'Normal interval' },
  { value: 3, label: 'Easy', hint: 'Longer interval' },
];

export function FlashcardsScreen() {
  const courses = useCourses();
  const settings = useSettings();
  const providers = useProviders();
  const generate = useGenerateFlashcards();
  const review = useReviewFlashcard();

  const [courseId, setCourseId] = useState('');
  const [documentId, setDocumentId] = useState('');
  const [count, setCount] = useState(10);
  const [view, setView] = useState<'review' | 'browse'>('review');

  const documents = useDocuments(courseId || undefined);
  const cards = useFlashcards(courseId || undefined, view === 'review');
  const effectiveCourse = courseId || courses.data?.[0]?.id || '';

  const [index, setIndex] = useState(0);
  const [revealed, setRevealed] = useState(false);

  const queue = useMemo(() => cards.data ?? [], [cards.data]);
  const current = queue[Math.min(index, Math.max(0, queue.length - 1))];

  const cloudAllowed = settings.data?.allow_cloud_processing ?? false;
  const activeProvider = providers.data?.llm.find((p) => p.key === providers.data?.active_llm);
  const needsCloudOptIn = Boolean(activeProvider?.sends_data_off_device);

  const byTopic = useMemo(() => {
    const groups = new Map<string, typeof queue>();
    for (const card of queue) {
      const key = card.topic_slug || 'other';
      groups.set(key, [...(groups.get(key) ?? []), card]);
    }
    return [...groups.entries()];
  }, [queue]);

  const rate = async (rating: number) => {
    if (!current) return;
    await review.mutateAsync({ id: current.id, rating });
    setRevealed(false);
    setIndex((value) => value + 1);
  };

  return (
    <>
      <PageHeader
        title="Flashcards"
        description="Active recall on a spacing schedule. Cards you find hard come back sooner."
        actions={
          <Tabs
            value={view}
            onChange={(next) => {
              setView(next);
              setIndex(0);
              setRevealed(false);
            }}
            options={[
              { value: 'review', label: 'Review due' },
              { value: 'browse', label: 'Browse all' },
            ]}
          />
        }
      />
      <PageBody>
        <div className="space-y-4">
          <Card>
            <SectionHeading
              title="Make cards from your material"
              description="Generated from indexed pages, each card keeping the page it came from."
            />
            {generate.error ? (
              <ErrorPanel message={describeError(generate.error)} />
            ) : null}
            <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Field label="Course">
                <Select
                  value={effectiveCourse}
                  onChange={(event) => {
                    setCourseId(event.target.value);
                    setDocumentId('');
                  }}
                >
                  {courses.data?.length === 0 ? <option value="">No courses</option> : null}
                  {courses.data?.map((course) => (
                    <option key={course.id} value={course.id}>
                      {course.title}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Document">
                <Select
                  value={documentId}
                  onChange={(event) => setDocumentId(event.target.value)}
                >
                  <option value="">All documents</option>
                  {documents.data?.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.title}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="How many">
                <Input
                  type="number"
                  min={1}
                  max={60}
                  value={count}
                  onChange={(event) =>
                    setCount(Math.max(1, Math.min(60, Number.parseInt(event.target.value, 10) || 1)))
                  }
                />
              </Field>
              <div className="flex items-end">
                <Button
                  variant="primary"
                  className="w-full"
                  icon={<Sparkles size={15} />}
                  loading={generate.isPending}
                  disabled={!effectiveCourse}
                  onClick={() =>
                    void generate.mutateAsync({
                      course_id: effectiveCourse,
                      document_id: documentId || null,
                      count,
                      allow_cloud: cloudAllowed && needsCloudOptIn,
                    })
                  }
                >
                  Generate
                </Button>
              </div>
            </div>
          </Card>

          {cards.isError ? (
            <ErrorPanel message={describeError(cards.error)} onRetry={() => void cards.refetch()} />
          ) : null}

          {cards.isPending ? (
            <Skeleton className="h-56" />
          ) : view === 'review' ? (
            queue.length === 0 || index >= queue.length ? (
              <EmptyState
                icon={<BookMarked size={22} />}
                title={queue.length === 0 ? 'Nothing due right now' : 'Session complete'}
                description={
                  queue.length === 0
                    ? 'Generate some cards above, or come back when the schedule brings them round.'
                    : `You reviewed ${queue.length} card${queue.length === 1 ? '' : 's'}.`
                }
                action={
                  index >= queue.length && queue.length > 0 ? (
                    <Button
                      icon={<RotateCcw size={14} />}
                      onClick={() => {
                        setIndex(0);
                        void cards.refetch();
                      }}
                    >
                      Check for more
                    </Button>
                  ) : null
                }
              />
            ) : current ? (
              <Card className="mx-auto max-w-2xl">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[0.75rem] tabular-nums text-[var(--text-secondary)]">
                    Card {index + 1} of {queue.length}
                  </span>
                  <div className="flex items-center gap-1.5">
                    <Badge tone="neutral">{provenanceLabel(current.provenance)}</Badge>
                    {current.source_page ? (
                      <Badge tone="neutral">page {current.source_page}</Badge>
                    ) : null}
                    {current.topic_slug ? (
                      <Badge tone="accent">{humaniseSlug(current.topic_slug)}</Badge>
                    ) : null}
                  </div>
                </div>

                <p className="mt-5 whitespace-pre-wrap text-center text-[0.9375rem] leading-relaxed text-[var(--text-primary)]">
                  {current.front}
                </p>

                <div className="mt-5 border-t border-[var(--border-subtle)] pt-5">
                  {revealed ? (
                    <p className="whitespace-pre-wrap text-center text-[0.9375rem] font-medium leading-relaxed text-[var(--text-accent)]">
                      {current.back}
                    </p>
                  ) : (
                    <div className="text-center">
                      <Button icon={<Eye size={14} />} onClick={() => setRevealed(true)}>
                        Show answer
                      </Button>
                    </div>
                  )}
                </div>

                {revealed ? (
                  <div className="mt-5 grid grid-cols-4 gap-2">
                    {RATINGS.map((rating) => (
                      <Button
                        key={rating.value}
                        size="sm"
                        variant={rating.value === 2 ? 'primary' : 'secondary'}
                        title={rating.hint}
                        loading={review.isPending}
                        onClick={() => void rate(rating.value)}
                      >
                        {rating.label}
                      </Button>
                    ))}
                  </div>
                ) : null}
              </Card>
            ) : null
          ) : queue.length === 0 ? (
            <EmptyState
              icon={<BookMarked size={22} />}
              title="No cards yet"
              description="Generate cards from an indexed document above."
            />
          ) : (
            <div className="space-y-4">
              {byTopic.map(([slug, group]) => (
                <Card key={slug}>
                  <SectionHeading
                    title={humaniseSlug(slug)}
                    description={`${group.length} card${group.length === 1 ? '' : 's'}`}
                  />
                  <ul className="mt-3 divide-y divide-[var(--border-subtle)]">
                    {group.map((card) => (
                      <li key={card.id} className="flex items-start gap-4 py-2.5">
                        <div className="min-w-0 flex-1">
                          <p className="text-[0.8125rem] text-[var(--text-primary)]">
                            {card.front}
                          </p>
                          <p className="mt-0.5 text-[0.8125rem] text-[var(--text-secondary)]">
                            {card.back}
                          </p>
                        </div>
                        <div className="flex shrink-0 flex-col items-end gap-1">
                          <Badge tone="neutral">{provenanceLabel(card.provenance)}</Badge>
                          <span className="text-[0.6875rem] text-[var(--text-muted)]">
                            due {formatRelativeTime(card.due_at)}
                          </span>
                        </div>
                      </li>
                    ))}
                  </ul>
                </Card>
              ))}
            </div>
          )}
        </div>
      </PageBody>
    </>
  );
}
