/**
 * The home screen.
 *
 * Every number here is read from the backend. When there is no data yet the
 * screen says so and offers the next step, rather than showing a chart of
 * invented progress.
 */
import {
  actionLabel,
  formatRelativeTime,
  masteryLabel,
  recommendationUrgency,
  urgencyLabel,
  type Urgency,
} from '@studypilot/shared';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ProgressBar,
  SectionHeading,
  Skeleton,
  type Tone,
} from '@studypilot/ui';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  GraduationCap,
  Repeat,
  Upload,
} from 'lucide-react';
import { useId, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { ErrorPanel, PageBody, PageHeader } from '../components/AppShell';
import { ProviderNotice } from '../components/ProviderNotice';
import { describeError, useCourses, useDashboard } from '../hooks/queries';

export function DashboardScreen() {
  const navigate = useNavigate();
  const { data, isPending, isError, error, refetch } = useDashboard();

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Where you are, and what is worth doing next."
        actions={
          <Button variant="primary" icon={<Upload size={15} />} onClick={() => navigate('/library')}>
            Upload a lecture
          </Button>
        }
      />
      <PageBody>
        {isError ? (
          <ErrorPanel
            title="No backend is connected"
            message={
              describeError(error) +
              ' This site serves the StudyPilot interface, but the API it talks to has not ' +
              'been configured yet. See docs/DEPLOYMENT.md for the three services it needs.'
            }
            onRetry={() => void refetch()}
          />
        ) : null}

        {isPending ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }, (_, index) => (
              <Skeleton key={index} className="h-24" />
            ))}
          </div>
        ) : data ? (
          <div className="space-y-5">
            <ProviderNotice />

            <CoursesAccordion count={data.course_count} />

            {data.document_count === 0 ? (
              <EmptyState
                icon={<Upload size={22} />}
                title="Nothing uploaded yet"
                description="Add a lecture PDF and StudyPilot will index it, explain it page by page, and build quizzes from it."
                action={
                  <Button variant="primary" onClick={() => navigate('/library')}>
                    Upload your first lecture
                  </Button>
                }
              />
            ) : null}

            <div className="grid gap-4 lg:grid-cols-5">
              <Card className="lg:col-span-3">
                <SectionHeading
                  title="What to do next"
                  description="Based on your answers so far, not on a guess."
                />
                {data.recommendations.length === 0 ? (
                  <p className="mt-3 text-[0.8125rem] text-[var(--text-secondary)]">
                    Answer a quiz and recommendations will appear here.
                  </p>
                ) : (
                  <>
                    <UrgencyLegend
                      present={new Set(
                        data.recommendations.map((item) => recommendationUrgency(item.priority)),
                      )}
                    />
                    <ul className="mt-3 space-y-2">
                      {data.recommendations.map((item) => {
                        const urgency = recommendationUrgency(item.priority);
                        const style = URGENCY_STYLE[urgency];
                        return (
                          <li
                            key={`${item.action}-${item.topic_slug}`}
                            data-urgency={urgency}
                            className="flex items-start gap-3 rounded-[var(--radius-control)] border px-3 py-2"
                            style={{
                              background: style.background,
                              borderColor: style.border,
                              borderLeftWidth: 3,
                              borderLeftColor: style.signal,
                            }}
                          >
                            <div className="min-w-0 flex-1">
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
                            <Badge tone={style.tone} icon={style.icon} className="shrink-0">
                              {urgencyLabel(urgency)}
                            </Badge>
                          </li>
                        );
                      })}
                    </ul>
                  </>
                )}
              </Card>

              <Card className="lg:col-span-2">
                <SectionHeading title="Continue studying" />
                {data.recent_sessions.length === 0 ? (
                  <p className="mt-3 text-[0.8125rem] text-[var(--text-secondary)]">
                    No saved sessions yet.
                  </p>
                ) : (
                  <ul className="mt-3 space-y-1.5">
                    {data.recent_sessions.map((session) => (
                      <li key={session.id}>
                        <Link
                          to={
                            session.document_id
                              ? `/read/${session.document_id}?page=${session.current_page}`
                              : '/tutor'
                          }
                          className="block rounded-[var(--radius-control)] px-2.5 py-2 transition-colors hover:bg-[var(--surface-hover)]"
                        >
                          <p className="truncate text-[0.8125rem] font-medium text-[var(--text-primary)]">
                            {session.document_title || session.course_title || 'Study session'}
                          </p>
                          <p className="text-[0.75rem] text-[var(--text-secondary)]">
                            Page {session.current_page}
                            {session.current_topic ? ` · ${session.current_topic}` : ''} ·{' '}
                            {formatRelativeTime(session.last_active_at)}
                          </p>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <SectionHeading
                  title="Topic mastery"
                  description="Estimated from the questions you have actually answered."
                  action={
                    <Link
                      to="/analytics"
                      className="text-[0.75rem] font-medium text-[var(--text-accent)] hover:underline"
                    >
                      All topics
                    </Link>
                  }
                />
                {data.mastery.length === 0 ? (
                  <p className="mt-3 text-[0.8125rem] text-[var(--text-secondary)]">
                    No estimates yet. Take a quiz to start building one.
                  </p>
                ) : (
                  <ul className="mt-3 space-y-2.5">
                    {data.mastery.slice(0, 6).map((estimate) => (
                      <li key={estimate.topic_slug}>
                        <div className="flex items-baseline justify-between gap-2">
                          <span className="truncate text-[0.8125rem] text-[var(--text-primary)]">
                            {estimate.topic_name}
                          </span>
                          <span className="shrink-0 text-[0.75rem] tabular-nums text-[var(--text-secondary)]">
                            {estimate.confident
                              ? `${Math.round(estimate.probability * 100)}%`
                              : masteryLabel('unknown')}
                          </span>
                        </div>
                        <ProgressBar
                          className="mt-1"
                          value={estimate.probability}
                          tone={
                            !estimate.confident
                              ? 'neutral'
                              : estimate.label === 'weak'
                                ? 'danger'
                                : estimate.label === 'developing'
                                  ? 'caution'
                                  : 'grounded'
                          }
                          label={`${estimate.topic_name} mastery`}
                        />
                      </li>
                    ))}
                  </ul>
                )}
              </Card>

              <Card>
                <SectionHeading title="Recent results" />
                {data.recent_attempts.length === 0 ? (
                  <p className="mt-3 text-[0.8125rem] text-[var(--text-secondary)]">
                    No quizzes submitted yet.
                  </p>
                ) : (
                  <ul className="mt-3 space-y-2">
                    {data.recent_attempts.slice(0, 6).map((attempt) => (
                      <li
                        key={attempt.attempt_id}
                        className="flex items-center justify-between gap-3 text-[0.8125rem]"
                      >
                        <span className="text-[var(--text-secondary)]">
                          {attempt.submitted_at
                            ? formatRelativeTime(attempt.submitted_at)
                            : 'In progress'}
                        </span>
                        <span className="flex items-center gap-2">
                          <span className="tabular-nums text-[var(--text-primary)]">
                            {attempt.score}/{attempt.max_score}
                          </span>
                          <Badge
                            tone={
                              attempt.percentage >= 80
                                ? 'grounded'
                                : attempt.percentage >= 50
                                  ? 'caution'
                                  : 'danger'
                            }
                          >
                            {attempt.percentage}%
                          </Badge>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>
          </div>
        ) : null}
      </PageBody>
    </>
  );
}

/**
 * Colour for how pressing a step is. Tints, not saturated fills: an urgent row
 * is reddish, not red, and the label and icon say it too, so the meaning never
 * rests on colour alone.
 */
const URGENCY_STYLE: Record<
  Urgency,
  { tone: Tone; signal: string; background: string; border: string; icon: React.ReactNode }
> = {
  urgent: {
    tone: 'danger',
    signal: 'var(--signal-danger)',
    background: 'var(--signal-danger-bg)',
    border: 'color-mix(in oklch, var(--signal-danger) 28%, transparent)',
    icon: <AlertTriangle size={11} />,
  },
  soon: {
    tone: 'caution',
    signal: 'var(--signal-caution)',
    background: 'var(--signal-caution-bg)',
    border: 'color-mix(in oklch, var(--signal-caution) 28%, transparent)',
    icon: <Clock size={11} />,
  },
  routine: {
    tone: 'info',
    signal: 'var(--signal-info)',
    background: 'var(--signal-info-bg)',
    border: 'color-mix(in oklch, var(--signal-info) 28%, transparent)',
    icon: <Repeat size={11} />,
  },
  on_track: {
    tone: 'grounded',
    signal: 'var(--signal-grounded)',
    background: 'var(--signal-grounded-bg)',
    border: 'color-mix(in oklch, var(--signal-grounded) 28%, transparent)',
    icon: <Check size={11} />,
  },
};

const URGENCY_ORDER: Urgency[] = ['urgent', 'soon', 'routine', 'on_track'];

function UrgencyLegend({ present }: { present: Set<Urgency> }) {
  return (
    <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.6875rem] text-[var(--text-muted)]">
      {URGENCY_ORDER.filter((urgency) => present.has(urgency)).map((urgency) => (
        <span key={urgency} className="flex items-center gap-1">
          <span
            aria-hidden
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: URGENCY_STYLE[urgency].signal }}
          />
          {urgencyLabel(urgency)}
        </span>
      ))}
    </p>
  );
}

/**
 * The courses, folded away until asked for.
 *
 * Closed it is one line with the count; open it lists every course, and each
 * row opens that course's slides.
 */
function CoursesAccordion({ count }: { count: number }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const courses = useCourses();

  return (
    <Card padded={false}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-3 rounded-[var(--radius-card)] px-4 py-3 text-left transition-colors hover:bg-[var(--surface-hover)]"
      >
        <GraduationCap size={16} className="shrink-0 text-[var(--text-secondary)]" />
        <span className="text-[0.875rem] font-semibold text-[var(--text-primary)]">Courses</span>
        <span className="text-[0.8125rem] tabular-nums text-[var(--text-secondary)]">{count}</span>
        <span className="ml-auto text-[0.75rem] text-[var(--text-muted)]">
          {open ? 'Hide' : 'Show'}
        </span>
        <ChevronDown
          size={16}
          className={`shrink-0 text-[var(--text-secondary)] transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open ? (
        <div id={panelId} className="border-t border-[var(--border-subtle)] px-2 py-2">
          {courses.isPending ? (
            <Skeleton className="m-2 h-10" />
          ) : courses.isError ? (
            <p className="px-2 py-2 text-[0.8125rem] text-[var(--text-secondary)]">
              {describeError(courses.error)}
            </p>
          ) : courses.data.length === 0 ? (
            <p className="px-2 py-2 text-[0.8125rem] text-[var(--text-secondary)]">
              No courses yet.{' '}
              <Link to="/courses" className="text-[var(--text-accent)] hover:underline">
                Create one
              </Link>
              .
            </p>
          ) : (
            <ul>
              {courses.data.map((course) => (
                <li key={course.id}>
                  <Link
                    to={`/courses/${course.id}`}
                    className="flex items-center gap-3 rounded-[var(--radius-control)] px-2.5 py-2 transition-colors hover:bg-[var(--surface-hover)]"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[0.8125rem] font-medium text-[var(--text-primary)]">
                        {course.title}
                      </span>
                      <span className="block truncate text-[0.75rem] text-[var(--text-secondary)]">
                        {[course.code, `${course.document_count} lectures`, `${course.page_count} slides`]
                          .filter(Boolean)
                          .join(' · ')}
                      </span>
                    </span>
                    <ChevronRight size={15} className="shrink-0 text-[var(--text-muted)]" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </Card>
  );
}
