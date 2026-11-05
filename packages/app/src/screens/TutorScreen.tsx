/**
 * The standalone tutor.
 *
 * The same panel the reader uses, scoped to a whole course rather than one
 * document, with the list of saved sessions alongside so a conversation can be
 * picked up where it was left.
 */
import { formatRelativeTime } from '@studypilot/shared';
import { Button, EmptyState, SectionHeading, Select, Skeleton } from '@studypilot/ui';
import { MessagesSquare, Plus } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { PageHeader } from '../components/AppShell';
import { TutorPanel } from '../components/TutorPanel';
import { useCourses, useSessions } from '../hooks/queries';

export function TutorScreen() {
  const navigate = useNavigate();
  const courses = useCourses();
  const sessions = useSessions(15);

  const [courseId, setCourseId] = useState('');
  const [sessionId, setSessionId] = useState<string | undefined>();

  const effectiveCourse = courseId || courses.data?.[0]?.id || '';
  const activeSession = sessions.data?.find((item) => item.id === sessionId);

  if (!courses.isPending && courses.data?.length === 0) {
    return (
      <>
        <PageHeader title="AI Tutor" />
        <div className="px-6 py-5">
          <EmptyState
            icon={<MessagesSquare size={22} />}
            title="Nothing to tutor from yet"
            description="The tutor answers only from material you have uploaded. Create a course and add a lecture PDF first."
            action={
              <Button variant="primary" onClick={() => navigate('/courses')}>
                Create a course
              </Button>
            }
          />
        </div>
      </>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="AI Tutor"
        description="Grounded in your own course material, with citations you can open."
        actions={
          <>
            <Select
              aria-label="Course"
              className="w-56"
              value={effectiveCourse}
              onChange={(event) => {
                setCourseId(event.target.value);
                setSessionId(undefined);
              }}
            >
              {courses.data?.map((course) => (
                <option key={course.id} value={course.id}>
                  {course.title}
                </option>
              ))}
            </Select>
            <Button icon={<Plus size={14} />} onClick={() => setSessionId(undefined)}>
              New conversation
            </Button>
          </>
        }
      />

      <div className="flex min-h-0 flex-1">
        <div className="sp-scroll w-64 shrink-0 overflow-y-auto border-r border-[var(--border-subtle)] px-3 py-3">
          <SectionHeading title="Saved sessions" className="px-1 pb-2" />
          {sessions.isPending ? (
            <div className="space-y-2">
              {Array.from({ length: 3 }, (_, i) => (
                <Skeleton key={i} className="h-12" />
              ))}
            </div>
          ) : sessions.data?.length === 0 ? (
            <p className="px-1 text-[0.75rem] leading-relaxed text-[var(--text-secondary)]">
              Sessions appear here once you start asking questions.
            </p>
          ) : (
            <ul className="space-y-0.5">
              {sessions.data?.map((session) => (
                <li key={session.id}>
                  <button
                    type="button"
                    onClick={() => setSessionId(session.id)}
                    className={[
                      'w-full rounded-[var(--radius-control)] px-2.5 py-2 text-left transition-colors',
                      session.id === sessionId
                        ? 'bg-[var(--surface-selected)]'
                        : 'hover:bg-[var(--surface-hover)]',
                    ].join(' ')}
                  >
                    <span className="block truncate text-[0.8125rem] font-medium text-[var(--text-primary)]">
                      {session.document_title || session.course_title || 'Conversation'}
                    </span>
                    <span className="block truncate text-[0.6875rem] text-[var(--text-secondary)]">
                      {formatRelativeTime(session.last_active_at)}
                      {session.document_id ? ` · page ${session.current_page}` : ''}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex min-h-0 flex-1 flex-col">
          {activeSession?.document_id ? (
            <div className="border-b border-[var(--border-subtle)] bg-[var(--surface-sunken)] px-4 py-2 text-[0.75rem] text-[var(--text-secondary)]">
              This session is tied to{' '}
              <Link
                to={`/read/${activeSession.document_id}?page=${activeSession.current_page}`}
                className="font-medium text-[var(--text-accent)] hover:underline"
              >
                {activeSession.document_title || 'a document'}
              </Link>
              . Open the reader to see the cited pages alongside the answers.
            </div>
          ) : null}
          {/* Keyed by the session only. Keying by the course too remounted the
              panel when the course list finished loading, wiping a question the
              student had already started typing. Found by the end-to-end test. */}
          <TutorPanel
            key={sessionId ?? 'new'}
            className="flex-1"
            sessionId={sessionId}
            onSessionCreated={setSessionId}
            onCitationClick={(citation) =>
              navigate(`/read/${citation.document_id}?page=${citation.page_number}`)
            }
            courseId={activeSession?.course_id ?? effectiveCourse}
            documentId={activeSession?.document_id ?? undefined}
            availableModes={['question', 'assignment']}
          />
        </div>
      </div>
    </div>
  );
}
