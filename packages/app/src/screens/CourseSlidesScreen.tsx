/**
 * A course, slide by slide.
 *
 * Opening a course lands here: every lecture in it as a folded section.
 * Opening a section shows its slides as thumbnails, and a slide opens the
 * study view, where it is explained. A lecture can have every slide
 * explained ahead of time from its header, and carries the transcript of the
 * class it was shown in, which the explanations then draw on. Only an open
 * section fetches its file, so a course of twenty decks opens at once.
 */
import type { DocumentSummary, GuidedPages } from '@studypilot/shared';
import { Badge, Button, Card, Dialog, EmptyState, ProgressBar, Skeleton } from '@studypilot/ui';
import { useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ClipboardList,
  FileText,
  FolderPlus,
  GraduationCap,
  MessagesSquare,
  Mic,
  Presentation,
  Sparkles,
  Square,
  Trash2,
  Upload,
  Waypoints,
} from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { ErrorPanel, PageBody, PageHeader } from '../components/AppShell';
import { SlideThumbnails } from '../components/SlideThumbnails';
import {
  describeError,
  guideJobActive,
  useCourse,
  useDeleteTranscript,
  useDocuments,
  useExplainAll,
  useGuidedPages,
  usePages,
  useProviders,
  useSettings,
  useStopExplaining,
  useTranscript,
  useUploadTranscript,
} from '../hooks/queries';

/** What is read slide by slide. Past papers and briefs have screens of their own. */
const LECTURE_KINDS = new Set(['lecture', 'textbook', '']);

export function CourseSlidesScreen() {
  const { courseId } = useParams<{ courseId: string }>();
  const course = useCourse(courseId);
  const documents = useDocuments(courseId);

  // Only what can be read slide by slide: something indexed, with pages.
  const lectures = useMemo(
    () =>
      (documents.data ?? [])
        .filter(
          (document) =>
            document.page_count > 0 &&
            document.status !== 'failed' &&
            LECTURE_KINDS.has(document.kind ?? ''),
        )
        .sort((a, b) => a.title.localeCompare(b.title, undefined, { numeric: true })),
    [documents.data],
  );
  const slideCount = lectures.reduce((total, lecture) => total + lecture.page_count, 0);
  const id = courseId ?? '';

  return (
    <>
      <PageHeader
        title={course.data?.title ?? 'Course'}
        description={
          lectures.length > 0
            ? `${lectures.length} lecture${lectures.length === 1 ? '' : 's'}, ${slideCount} slides. Open a lecture, then a slide, to have it explained.`
            : 'Every lecture in this course, slide by slide.'
        }
        actions={
          <>
            <Link to={`/tutor?course=${id}`}>
              <Button icon={<MessagesSquare size={15} />}>Ask about this course</Button>
            </Link>
            <Link to={`/courses/${id}/exams`}>
              <Button icon={<GraduationCap size={15} />}>Past exams</Button>
            </Link>
            <Link to={`/library?course=${id}`}>
              <Button variant="ghost" icon={<FolderPlus size={15} />}>
                Add files
              </Button>
            </Link>
            <Link to={`/courses/${id}/assignments`}>
              <Button variant="ghost" icon={<ClipboardList size={15} />}>
                Assignments
              </Button>
            </Link>
            <Link to={`/courses/${id}/map`}>
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
              <Link to={`/library?course=${id}`}>
                <Button variant="primary">Add lectures</Button>
              </Link>
            }
          />
        ) : (
          <div className="space-y-3">
            {lectures.map((lecture) => (
              <LectureSection key={lecture.id} lecture={lecture} />
            ))}
          </div>
        )}
      </PageBody>
    </>
  );
}

function LectureSection({ lecture }: { lecture: DocumentSummary }) {
  // Folded until asked for: a course is a list of lectures first.
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const queryClient = useQueryClient();
  const pages = usePages(open ? lecture.id : undefined);
  const guides = useGuidedPages(lecture.id);
  const guided = useMemo(() => new Set(guides.data?.pages ?? []), [guides.data]);

  // Slides the job writes were "not explained" in the cache a moment ago.
  const written = guides.data?.job?.written ?? 0;
  const seenWritten = useRef(0);
  useEffect(() => {
    if (written > seenWritten.current) {
      void queryClient.invalidateQueries({
        predicate: (query) =>
          query.queryKey[0] === 'documents' &&
          query.queryKey[1] === lecture.id &&
          query.queryKey[4] === 'guide',
      });
    }
    seenWritten.current = written;
  }, [lecture.id, queryClient, written]);

  return (
    <Card padded={false}>
      <div className="flex items-center gap-1 pr-2">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((value) => !value)}
          className="flex min-w-0 flex-1 items-center gap-3 rounded-[var(--radius-card)] px-4 py-3 text-left transition-colors hover:bg-[var(--surface-hover)]"
        >
          <Presentation size={16} className="shrink-0 text-[var(--text-secondary)]" />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[0.875rem] font-semibold text-[var(--text-primary)]">
              {lecture.title}
            </span>
            <span className="block text-[0.75rem] text-[var(--text-secondary)]">
              {lecture.page_count} slide{lecture.page_count === 1 ? '' : 's'}
              {guided.size > 0 ? ` · ${guided.size} explained` : ''}
              {guides.data?.has_transcript ? ' · class transcript added' : ''}
            </span>
          </span>
          {lecture.status === 'partial' ? <Badge tone="caution">Partly indexed</Badge> : null}
        </button>

        <ExplainAllControl lecture={lecture} guides={guides.data} />

        <button
          type="button"
          aria-label={open ? `Close ${lecture.title}` : `Open ${lecture.title}`}
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((value) => !value)}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-control)] text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-hover)]"
        >
          <ChevronDown size={16} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
        </button>
      </div>

      {open ? (
        <div id={panelId} className="space-y-4 border-t border-[var(--border-subtle)] p-4">
          <TranscriptRow lecture={lecture} explained={guided.size} />
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

/**
 * "Explain all slides", beside the fold arrow.
 *
 * One click would be fifty model calls, so it asks first and says what it
 * will cost in calls and time. While it runs it shows how far it has got and
 * can be stopped; what was written by then is kept.
 */
function ExplainAllControl({
  lecture,
  guides,
}: {
  lecture: DocumentSummary;
  guides: GuidedPages | undefined;
}) {
  const settings = useSettings();
  const providers = useProviders();
  const explain = useExplainAll(lecture.id);
  const stop = useStopExplaining(lecture.id);
  const [asking, setAsking] = useState(false);
  const [rewrite, setRewrite] = useState(false);

  const activeProvider = providers.data?.llm.find((p) => p.key === providers.data?.active_llm);
  const allowCloud =
    Boolean(settings.data?.allow_cloud_processing) &&
    Boolean(activeProvider?.sends_data_off_device);

  const job = guides?.job ?? null;
  const explained = guides?.pages.length ?? 0;
  const missing = Math.max(0, lecture.page_count - explained);
  const count = rewrite ? lecture.page_count : missing;
  const minutes = Math.max(1, Math.round((count * 40) / 3 / 60));

  if (guideJobActive(guides) && job) {
    return (
      <div className="flex shrink-0 items-center gap-2" aria-live="polite">
        <div className="w-28">
          <p className="mb-1 text-right text-[0.6875rem] tabular-nums text-[var(--text-secondary)]">
            {job.stopping
              ? 'Stopping…'
              : job.total > 0
                ? `Explaining ${job.done} / ${job.total}`
                : 'Starting…'}
          </p>
          <ProgressBar
            value={job.total > 0 ? job.done / job.total : 0}
            label={`Explaining ${lecture.title}`}
          />
        </div>
        <Button
          size="sm"
          variant="ghost"
          icon={<Square size={11} />}
          loading={stop.isPending}
          disabled={job.stopping}
          onClick={() => stop.mutate()}
          title="Stop. Slides already explained are kept."
        >
          Stop
        </Button>
      </div>
    );
  }

  return (
    <>
      {job && job.state === 'failed' ? (
        <Badge tone="caution" icon={<AlertTriangle size={11} />} title={job.error}>
          Stopped: {job.written} explained
        </Badge>
      ) : missing === 0 && guides ? (
        <Badge tone="grounded" icon={<Check size={11} />}>
          All explained
        </Badge>
      ) : null}
      <Button
        size="sm"
        icon={<Sparkles size={13} />}
        disabled={!guides}
        onClick={() => {
          setRewrite(missing === 0);
          setAsking(true);
        }}
        title="Have every slide of this lecture explained now, so none has to be waited for later."
      >
        {missing === 0 ? 'Explain again' : 'Explain all slides'}
      </Button>

      <Dialog
        open={asking}
        onClose={() => setAsking(false)}
        title={`Explain every slide of ${lecture.title}?`}
        description={
          count === 0
            ? 'Every slide is already explained.'
            : `${count} slide${count === 1 ? '' : 's'} will be explained, one AI call each. It takes about ${minutes} minute${minutes === 1 ? '' : 's'} and runs in the background, so you can keep working.`
        }
        footer={
          <>
            <Button variant="ghost" onClick={() => setAsking(false)}>
              Not now
            </Button>
            <Button
              variant="primary"
              icon={<Sparkles size={14} />}
              loading={explain.isPending}
              disabled={count === 0}
              onClick={() =>
                explain.mutate(
                  { allow_cloud: allowCloud, regenerate: rewrite },
                  { onSuccess: () => setAsking(false) },
                )
              }
            >
              Explain {count} slide{count === 1 ? '' : 's'}
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-[0.8125rem] text-[var(--text-secondary)]">
          <p>
            Model: {providers.data?.active_model || providers.data?.active_llm || 'none chosen'}.
            {guides?.has_transcript
              ? ' The class transcript you added will be used.'
              : ' Add the class transcript first if you have one: explanations then follow how the lecturer taught it.'}
          </p>
          {explained > 0 ? (
            <label className="flex items-start gap-2">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={rewrite}
                onChange={(event) => setRewrite(event.target.checked)}
              />
              <span>
                Also rewrite the {explained} slide{explained === 1 ? '' : 's'} already explained
              </span>
            </label>
          ) : null}
          {explain.error ? (
            <p className="text-[var(--signal-danger)]">{describeError(explain.error)}</p>
          ) : null}
        </div>
      </Dialog>
    </>
  );
}

/** The transcript of the class this lecture was shown in: add, replace or remove it. */
function TranscriptRow({ lecture, explained }: { lecture: DocumentSummary; explained: number }) {
  const transcript = useTranscript(lecture.id);
  const upload = useUploadTranscript(lecture.id);
  const remove = useDeleteTranscript(lecture.id);
  const inputRef = useRef<HTMLInputElement>(null);
  const current = transcript.data?.transcript ?? null;

  return (
    <div className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-sunken)] px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Mic size={14} className="shrink-0 text-[var(--text-secondary)]" />
        <div className="min-w-0 flex-1">
          <p className="text-[0.8125rem] font-medium text-[var(--text-primary)]">
            Class video transcription
          </p>
          <p className="truncate text-[0.75rem] text-[var(--text-secondary)]">
            {transcript.isPending ? (
              'Checking…'
            ) : current ? (
              <>
                <FileText size={11} className="mr-1 inline" />
                {current.filename} · {current.word_count.toLocaleString()} words. Slides are
                explained the way the lecturer taught them.
              </>
            ) : (
              'Add what was said in this class and each slide is explained the way your lecturer taught it.'
            )}
          </p>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".txt,.md,.vtt,.srt,.docx,.pdf"
          className="hidden"
          aria-label={`Class transcript for ${lecture.title}`}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (file) upload.mutate(file);
          }}
        />
        <Button
          size="sm"
          icon={<Upload size={13} />}
          loading={upload.isPending}
          onClick={() => inputRef.current?.click()}
        >
          {current ? 'Replace' : 'Add transcript'}
        </Button>
        {current ? (
          <Button
            size="sm"
            variant="ghost"
            aria-label="Remove the transcript"
            loading={remove.isPending}
            onClick={() => remove.mutate()}
          >
            <Trash2 size={13} />
          </Button>
        ) : null}
      </div>
      {upload.error ? (
        <p className="mt-1.5 text-[0.75rem] text-[var(--signal-danger)]">
          {describeError(upload.error)}
        </p>
      ) : null}
      {upload.isSuccess && explained > 0 ? (
        <p className="mt-1.5 text-[0.75rem] text-[var(--text-secondary)]">
          Added. The {explained} slide{explained === 1 ? '' : 's'} already explained keep their
          text; use “Explain again” above to rewrite them with the transcript.
        </p>
      ) : null}
    </div>
  );
}
