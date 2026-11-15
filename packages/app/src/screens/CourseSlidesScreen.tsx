/**
 * A course, slide by slide.
 *
 * Opening a course lands here: every lecture in it as a section of slide
 * thumbnails. Clicking a slide opens the study view, where that slide is
 * explained. Lectures fold, and only an open one fetches its file, so a
 * course of twenty decks opens as fast as its first.
 */
import type { DocumentSummary } from '@studypilot/shared';
import { Badge, Button, Card, EmptyState, Skeleton } from '@studypilot/ui';
import { ChevronDown, ClipboardList, FolderOpen, Presentation, Waypoints } from 'lucide-react';
import { useId, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { ErrorPanel, PageBody, PageHeader } from '../components/AppShell';
import { SlideThumbnails } from '../components/SlideThumbnails';
import {
  describeError,
  useCourse,
  useDocuments,
  useGuidedPages,
  usePages,
} from '../hooks/queries';

export function CourseSlidesScreen() {
  const { courseId } = useParams<{ courseId: string }>();
  const course = useCourse(courseId);
  const documents = useDocuments(courseId);

  // Only what can be read slide by slide: something indexed, with pages.
  const lectures = useMemo(
    () =>
      (documents.data ?? [])
        .filter((document) => document.page_count > 0 && document.status !== 'failed')
        .sort((a, b) => a.title.localeCompare(b.title, undefined, { numeric: true })),
    [documents.data],
  );
  const slideCount = lectures.reduce((total, lecture) => total + lecture.page_count, 0);

  return (
    <>
      <PageHeader
        title={course.data?.title ?? 'Course'}
        description={
          lectures.length > 0
            ? `${lectures.length} lecture${lectures.length === 1 ? '' : 's'}, ${slideCount} slides. Open a slide to have it explained.`
            : 'Every lecture in this course, slide by slide.'
        }
        actions={
          <>
            <Link to={`/library?course=${courseId ?? ''}`}>
              <Button icon={<FolderOpen size={15} />}>Files</Button>
            </Link>
            <Link to={`/courses/${courseId ?? ''}/assignments`}>
              <Button variant="ghost" icon={<ClipboardList size={15} />}>
                Assignments
              </Button>
            </Link>
            <Link to={`/courses/${courseId ?? ''}/map`}>
              <Button variant="ghost" icon={<Waypoints size={15} />}>
                Map
              </Button>
            </Link>
          </>
        }
      />
      <PageBody>
        <Link
          to="/courses"
          className="mb-3 inline-block text-[0.75rem] font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
        >
          ← My Courses
        </Link>

        {course.isError || documents.isError ? (
          <ErrorPanel
            message={describeError(course.error ?? documents.error)}
            onRetry={() => {
              void course.refetch();
              void documents.refetch();
            }}
          />
        ) : documents.isPending ? (
          <div className="space-y-3">
            <Skeleton className="h-14" />
            <Skeleton className="h-14" />
          </div>
        ) : lectures.length === 0 ? (
          <EmptyState
            icon={<Presentation size={22} />}
            title="No lecture slides in this course yet"
            description="Add a lecture PDF or PowerPoint, or link a folder, and its slides appear here."
            action={
              <Link to={`/library?course=${courseId ?? ''}`}>
                <Button variant="primary">Add lectures</Button>
              </Link>
            }
          />
        ) : (
          <div className="space-y-3">
            {lectures.map((lecture, index) => (
              <LectureSection key={lecture.id} lecture={lecture} defaultOpen={index === 0} />
            ))}
          </div>
        )}
      </PageBody>
    </>
  );
}

function LectureSection({
  lecture,
  defaultOpen,
}: {
  lecture: DocumentSummary;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();
  const pages = usePages(open ? lecture.id : undefined);
  const guides = useGuidedPages(lecture.id, open);
  const guided = useMemo(() => new Set(guides.data?.pages ?? []), [guides.data]);

  return (
    <Card padded={false}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-3 rounded-[var(--radius-card)] px-4 py-3 text-left transition-colors hover:bg-[var(--surface-hover)]"
      >
        <Presentation size={16} className="shrink-0 text-[var(--text-secondary)]" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[0.875rem] font-semibold text-[var(--text-primary)]">
            {lecture.title}
          </span>
          <span className="block text-[0.75rem] text-[var(--text-secondary)]">
            {lecture.page_count} slide{lecture.page_count === 1 ? '' : 's'}
            {guided.size > 0 ? ` · ${guided.size} explained` : ''}
          </span>
        </span>
        {lecture.status === 'partial' ? <Badge tone="caution">Partly indexed</Badge> : null}
        <ChevronDown
          size={16}
          className={`shrink-0 text-[var(--text-secondary)] transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open ? (
        <div id={panelId} className="border-t border-[var(--border-subtle)] p-4">
          {pages.isPending ? (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-3">
              {Array.from({ length: Math.min(8, lecture.page_count) }, (_, i) => (
                <Skeleton key={i} className="aspect-[4/3]" />
              ))}
            </div>
          ) : pages.isError ? (
            <ErrorPanel message={describeError(pages.error)} onRetry={() => void pages.refetch()} />
          ) : (
            <SlideThumbnails
              documentId={lecture.id}
              pages={pages.data}
              guided={guided}
              hrefFor={(page) => `/study/${lecture.id}?page=${page}`}
            />
          )}
        </div>
      ) : null}
    </Card>
  );
}
