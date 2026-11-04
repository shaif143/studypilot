/**
 * Learning analytics.
 *
 * The honesty rule here matters more than the chart: a topic with fewer than
 * three observations is shown as "not enough evidence", not as a low score.
 * The uncertainty band is drawn so the student can see how much the estimate is
 * actually worth.
 */
import { actionLabel, masteryLabel } from '@studypilot/shared';
import type { MasteryEstimate } from '@studypilot/shared';
import {
  Badge,
  Button,
  Callout,
  Card,
  EmptyState,
  ProgressBar,
  SectionHeading,
  Select,
  Skeleton,
  type Tone,
} from '@studypilot/ui';
import { ChartNoAxesColumn, Info } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { ErrorPanel, PageBody, PageHeader } from '../components/AppShell';
import {
  describeError,
  useCourses,
  useDashboard,
  useMastery,
  useRecommendations,
} from '../hooks/queries';

const LABEL_TONE: Record<string, Tone> = {
  unknown: 'neutral',
  weak: 'danger',
  developing: 'caution',
  strong: 'grounded',
};

export function AnalyticsScreen() {
  const courses = useCourses();
  const [courseId, setCourseId] = useState('');
  const mastery = useMastery(courseId || undefined);
  const recommendations = useRecommendations(courseId || undefined);
  const dashboard = useDashboard();

  const estimates = mastery.data ?? [];
  const confident = estimates.filter((item) => item.confident);
  const averageMastery =
    confident.length > 0
      ? confident.reduce((sum, item) => sum + item.probability, 0) / confident.length
      : null;

  return (
    <>
      <PageHeader
        title="Learning Analytics"
        description="What your answers actually show, including where there is not enough evidence to say."
        actions={
          (courses.data?.length ?? 0) > 1 ? (
            <Select
              aria-label="Course"
              className="w-48"
              value={courseId}
              onChange={(event) => setCourseId(event.target.value)}
            >
              <option value="">All courses</option>
              {courses.data?.map((course) => (
                <option key={course.id} value={course.id}>
                  {course.title}
                </option>
              ))}
            </Select>
          ) : null
        }
      />
      <PageBody>
        <div className="space-y-4">
          {mastery.isError ? (
            <ErrorPanel
              message={describeError(mastery.error)}
              onRetry={() => void mastery.refetch()}
            />
          ) : null}

          <Callout tone="info" icon={<Info size={14} />}>
            Mastery is estimated with Bayesian Knowledge Tracing from the questions you have
            answered. One wrong answer moves an estimate a little, never to zero, and a topic stays
            marked uncertain until there are at least three observations.
          </Callout>

          {mastery.isPending ? (
            <Skeleton className="h-64" />
          ) : estimates.length === 0 ? (
            <EmptyState
              icon={<ChartNoAxesColumn size={22} />}
              title="No estimates yet"
              description="Answer a quiz or review some flashcards and this will start filling in."
              action={
                <Link to="/quizzes">
                  <Button variant="primary">Take a quiz</Button>
                </Link>
              }
            />
          ) : (
            <>
              <div className="grid gap-3 sm:grid-cols-4">
                <SummaryTile
                  label="Topics tracked"
                  value={String(estimates.length)}
                  detail={`${confident.length} with enough evidence`}
                />
                <SummaryTile
                  label="Average mastery"
                  value={averageMastery !== null ? `${Math.round(averageMastery * 100)}%` : '—'}
                  detail={
                    averageMastery !== null
                      ? 'across confident estimates only'
                      : 'not enough evidence yet'
                  }
                />
                <SummaryTile
                  label="Needs work"
                  value={String(estimates.filter((item) => item.label === 'weak').length)}
                  detail="below 60% with evidence"
                />
                <SummaryTile
                  label="Cards due"
                  value={String(dashboard.data?.due_flashcards ?? 0)}
                  detail={`${dashboard.data?.upcoming_reviews ?? 0} within 7 days`}
                />
              </div>

              <Card>
                <SectionHeading
                  title="Topic mastery"
                  description="The lighter band shows how uncertain each estimate is."
                />
                <ul className="mt-4 space-y-3.5">
                  {estimates.map((estimate) => (
                    <MasteryRow key={estimate.topic_slug} estimate={estimate} />
                  ))}
                </ul>
              </Card>

              <Card>
                <SectionHeading
                  title="Recommended next actions"
                  description="Rule-based and explainable: the reason shown is the rule that fired."
                />
                {recommendations.data?.length === 0 ? (
                  <p className="mt-3 text-[0.8125rem] text-[var(--text-secondary)]">
                    Nothing to recommend yet.
                  </p>
                ) : (
                  <ol className="mt-3 space-y-2">
                    {recommendations.data?.map((item, position) => (
                      <li
                        key={`${item.action}-${item.topic_slug}`}
                        className="flex items-start gap-3 rounded-[var(--radius-control)] border border-[var(--border-subtle)] bg-[var(--surface-sunken)] px-3 py-2"
                      >
                        <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--surface-raised)] text-[0.6875rem] font-semibold tabular-nums text-[var(--text-secondary)]">
                          {position + 1}
                        </span>
                        <div className="min-w-0">
                          <p className="text-[0.8125rem] font-medium text-[var(--text-primary)]">
                            {actionLabel(item.action)}
                            {item.topic_name ? (
                              <span className="font-normal text-[var(--text-secondary)]">
                                {' '}
                                — {item.topic_name}
                              </span>
                            ) : null}
                          </p>
                          <p className="mt-0.5 text-[0.75rem] leading-relaxed text-[var(--text-secondary)]">
                            {item.reason}
                          </p>
                        </div>
                      </li>
                    ))}
                  </ol>
                )}
              </Card>
            </>
          )}
        </div>
      </PageBody>
    </>
  );
}

function SummaryTile({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <Card>
      <p className="text-[0.75rem] font-medium text-[var(--text-secondary)]">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums tracking-[-0.02em] text-[var(--text-primary)]">
        {value}
      </p>
      <p className="mt-0.5 text-[0.6875rem] text-[var(--text-muted)]">{detail}</p>
    </Card>
  );
}

function MasteryRow({ estimate }: { estimate: MasteryEstimate }) {
  // Half-width credible band around the point estimate, clamped to [0, 1].
  const half = Math.min(0.5, estimate.uncertainty / 2);
  const lower = Math.max(0, estimate.probability - half);
  const upper = Math.min(1, estimate.probability + half);

  return (
    <li>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="min-w-0 truncate text-[0.8125rem] font-medium text-[var(--text-primary)]">
          {estimate.topic_name}
        </span>
        <span className="flex shrink-0 items-center gap-2">
          <Badge tone={LABEL_TONE[estimate.label] ?? 'neutral'}>
            {masteryLabel(estimate.label)}
          </Badge>
          <span className="text-[0.75rem] tabular-nums text-[var(--text-secondary)]">
            {estimate.correct_observations}/{estimate.observations} correct
          </span>
        </span>
      </div>

      <div className="relative mt-1.5 h-2 w-full overflow-hidden rounded-full bg-[var(--surface-sunken)]">
        {/* Uncertainty band */}
        <div
          className="absolute top-0 h-full rounded-full bg-[var(--border-strong)] opacity-40"
          style={{ left: `${lower * 100}%`, width: `${Math.max(1, (upper - lower) * 100)}%` }}
        />
        {/* Point estimate */}
        <div
          className="absolute top-0 h-full w-[2px] bg-[var(--text-primary)]"
          style={{ left: `${estimate.probability * 100}%` }}
        />
      </div>

      <p className="mt-1 text-[0.6875rem] text-[var(--text-muted)]">
        {estimate.confident
          ? `Estimated ${Math.round(estimate.probability * 100)}% (between ${Math.round(
              lower * 100,
            )}% and ${Math.round(upper * 100)}%)`
          : `Only ${estimate.observations} answer${estimate.observations === 1 ? '' : 's'} so far — not enough to judge this topic`}
      </p>
      <ProgressBar className="sr-only" value={estimate.probability} label={estimate.topic_name} />
    </li>
  );
}
