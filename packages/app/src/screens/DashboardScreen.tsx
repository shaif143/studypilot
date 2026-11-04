/**
 * The home screen.
 *
 * Every number here is read from the backend. When there is no data yet the
 * screen says so and offers the next step, rather than showing a chart of
 * invented progress.
 */
import { actionLabel, formatRelativeTime, masteryLabel } from '@studypilot/shared';
import { Badge, Button, Card, EmptyState, ProgressBar, SectionHeading, Skeleton } from '@studypilot/ui';
import { BookMarked, FileText, GraduationCap, Layers, Upload } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';

import { ErrorPanel, PageBody, PageHeader } from '../components/AppShell';
import { ProviderNotice } from '../components/ProviderNotice';
import { describeError, useDashboard } from '../hooks/queries';

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

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard
                icon={<GraduationCap size={15} />}
                label="Courses"
                value={data.course_count}
              />
              <StatCard
                icon={<FileText size={15} />}
                label="Documents"
                value={data.document_count}
                detail={`${data.page_count} pages`}
              />
              <StatCard
                icon={<Layers size={15} />}
                label="Indexed passages"
                value={data.indexed_chunk_count}
                detail="searchable"
              />
              <StatCard
                icon={<BookMarked size={15} />}
                label="Cards due"
                value={data.due_flashcards}
                detail={`${data.upcoming_reviews} within 7 days`}
                to="/flashcards"
              />
            </div>

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
                  <ul className="mt-3 space-y-2">
                    {data.recommendations.map((item) => (
                      <li
                        key={`${item.action}-${item.topic_slug}`}
                        className="flex items-start justify-between gap-3 rounded-[var(--radius-control)] border border-[var(--border-subtle)] bg-[var(--surface-sunken)] px-3 py-2"
                      >
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
                  </ul>
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

function StatCard({
  icon,
  label,
  value,
  detail,
  to,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  detail?: string;
  to?: string;
}) {
  const content = (
    <Card className="h-full">
      <div className="flex items-center gap-1.5 text-[var(--text-secondary)]">
        {icon}
        <span className="text-[0.75rem] font-medium">{label}</span>
      </div>
      <p className="mt-1.5 text-2xl font-semibold tabular-nums tracking-[-0.02em] text-[var(--text-primary)]">
        {value}
      </p>
      {detail ? (
        <p className="mt-0.5 text-[0.75rem] text-[var(--text-muted)]">{detail}</p>
      ) : null}
    </Card>
  );
  return to ? (
    <Link to={to} className="block transition-transform hover:-translate-y-px">
      {content}
    </Link>
  ) : (
    content
  );
}
