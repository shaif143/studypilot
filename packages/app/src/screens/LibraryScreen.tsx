/**
 * Lecture library: upload documents and open them in the reader.
 *
 * The upload result is reported exactly as the backend returned it — page
 * count, indexed passage count, and any extraction warnings — because a PDF
 * that produced no text is a very different outcome from one that indexed
 * cleanly, and the student needs to know which they got.
 */
import { formatBytes, formatRelativeTime } from '@studypilot/shared';
import type { DocumentStatus, UploadResult } from '@studypilot/shared';
import {
  Badge,
  Button,
  Callout,
  Card,
  Dialog,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
  type Tone,
} from '@studypilot/ui';
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  FileText,
  Trash2,
  Upload,
} from 'lucide-react';
import { useCallback, useId, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';

import { ErrorPanel, PageBody, PageHeader } from '../components/AppShell';
import {
  describeError,
  useCourses,
  useDeleteDocument,
  useDocuments,
  useUploadDocument,
} from '../hooks/queries';

const STATUS_TONE: Record<DocumentStatus, Tone> = {
  indexed: 'grounded',
  partial: 'caution',
  failed: 'danger',
  processing: 'info',
  pending: 'neutral',
};

const STATUS_LABEL: Record<DocumentStatus, string> = {
  indexed: 'Indexed',
  partial: 'Partly indexed',
  failed: 'Failed',
  processing: 'Processing',
  pending: 'Queued',
};

const DOCUMENT_KINDS = [
  { value: 'lecture', label: 'Lecture slides or notes' },
  { value: 'past_paper', label: 'Past examination paper' },
  { value: 'assignment', label: 'Assignment brief' },
  { value: 'textbook', label: 'Textbook chapter' },
];

export function LibraryScreen() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const courseFilter = params.get('course') ?? undefined;

  const courses = useCourses();
  const documents = useDocuments(courseFilter);
  const deleteDocument = useDeleteDocument();

  const [uploadFor, setUploadFor] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<{ id: string; title: string } | null>(null);
  const [lastResult, setLastResult] = useState<UploadResult | null>(null);

  const hasCourses = (courses.data?.length ?? 0) > 0;

  return (
    <>
      <PageHeader
        title="Lecture Library"
        description="PDF lectures, past papers and assignment briefs. Everything stays on this device."
        actions={
          <>
            {hasCourses ? (
              <Select
                aria-label="Filter by course"
                className="w-48"
                value={courseFilter ?? ''}
                onChange={(event) => {
                  const next = event.target.value;
                  setParams(next ? { course: next } : {});
                }}
              >
                <option value="">All courses</option>
                {courses.data?.map((course) => (
                  <option key={course.id} value={course.id}>
                    {course.title}
                  </option>
                ))}
              </Select>
            ) : null}
            <Button
              variant="primary"
              icon={<Upload size={15} />}
              disabled={!hasCourses}
              title={hasCourses ? undefined : 'Create a course first'}
              onClick={() => setUploadFor(courseFilter ?? courses.data?.[0]?.id ?? null)}
            >
              Upload PDF
            </Button>
          </>
        }
      />
      <PageBody>
        <div className="space-y-4">
          {lastResult ? (
            <UploadSummary result={lastResult} onDismiss={() => setLastResult(null)} />
          ) : null}

          {documents.isError ? (
            <ErrorPanel
              message={describeError(documents.error)}
              onRetry={() => void documents.refetch()}
            />
          ) : null}

          {!hasCourses && !courses.isPending ? (
            <EmptyState
              icon={<BookOpen size={22} />}
              title="Create a course first"
              description="Documents belong to a course, which is what scopes the tutor's retrieval."
              action={
                <Button variant="primary" onClick={() => navigate('/courses')}>
                  Go to courses
                </Button>
              }
            />
          ) : documents.isPending ? (
            <div className="space-y-2">
              {Array.from({ length: 3 }, (_, i) => (
                <Skeleton key={i} className="h-16" />
              ))}
            </div>
          ) : documents.data?.length === 0 ? (
            <EmptyState
              icon={<Upload size={22} />}
              title="No documents in this view"
              description="Upload a lecture PDF. StudyPilot will extract its text, equations and figures, detect its topics, and index it for search."
              action={
                <Button
                  variant="primary"
                  onClick={() => setUploadFor(courseFilter ?? courses.data?.[0]?.id ?? null)}
                >
                  Upload a PDF
                </Button>
              }
            />
          ) : (
            <ul className="space-y-2">
              {documents.data?.map((document) => {
                const course = courses.data?.find((item) => item.id === document.course_id);
                return (
                  <li key={document.id}>
                    <Card className="flex flex-wrap items-center gap-4" padded={false}>
                      <div className="flex min-w-0 flex-1 items-center gap-3 py-3 pl-4">
                        <FileText size={18} className="shrink-0 text-[var(--text-muted)]" />
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <Link
                              to={`/read/${document.id}`}
                              className="truncate text-[0.875rem] font-medium text-[var(--text-primary)] hover:text-[var(--text-accent)] hover:underline"
                            >
                              {document.title}
                            </Link>
                            <Badge
                              tone={STATUS_TONE[document.status]}
                              title={document.status_detail || undefined}
                            >
                              {STATUS_LABEL[document.status]}
                            </Badge>
                            {document.kind !== 'lecture' ? (
                              <Badge tone="accent">{document.kind.replace('_', ' ')}</Badge>
                            ) : null}
                          </div>
                          <p className="mt-0.5 truncate text-[0.75rem] text-[var(--text-secondary)]">
                            {course ? `${course.title} · ` : ''}
                            {document.page_count} pages · {formatBytes(document.byte_size)} ·{' '}
                            {formatRelativeTime(document.created_at)}
                          </p>
                          {document.status_detail ? (
                            <p className="mt-0.5 line-clamp-2 text-[0.75rem] text-[var(--signal-caution)]">
                              {document.status_detail}
                            </p>
                          ) : null}
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2 py-3 pr-4">
                        <Link to={`/read/${document.id}`}>
                          <Button size="sm">Open reader</Button>
                        </Link>
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={`Delete ${document.title}`}
                          onClick={() =>
                            setConfirmDelete({ id: document.id, title: document.title })
                          }
                        >
                          <Trash2 size={14} />
                        </Button>
                      </div>
                    </Card>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <UploadDialog
          open={uploadFor !== null}
          courseId={uploadFor}
          courses={courses.data ?? []}
          onClose={() => setUploadFor(null)}
          onUploaded={(result) => {
            setUploadFor(null);
            setLastResult(result);
          }}
        />

        <Dialog
          open={confirmDelete !== null}
          onClose={() => setConfirmDelete(null)}
          title={`Delete ${confirmDelete?.title ?? 'document'}?`}
          description="The file, its page previews and every index entry built from it are removed from this device. Quizzes already generated from it are kept."
          footer={
            <>
              <Button onClick={() => setConfirmDelete(null)}>Cancel</Button>
              <Button
                variant="danger"
                loading={deleteDocument.isPending}
                onClick={async () => {
                  if (confirmDelete) await deleteDocument.mutateAsync(confirmDelete.id);
                  setConfirmDelete(null);
                }}
              >
                Delete
              </Button>
            </>
          }
        />
      </PageBody>
    </>
  );
}

function UploadSummary({
  result,
  onDismiss,
}: {
  result: UploadResult;
  onDismiss: () => void;
}) {
  const tone: Tone =
    result.status === 'indexed' ? 'grounded' : result.status === 'failed' ? 'danger' : 'caution';
  return (
    <Callout
      tone={tone}
      icon={
        result.status === 'indexed' ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />
      }
      title={
        result.deduplicated
          ? 'That file was already in this course'
          : result.status === 'indexed'
            ? `Indexed ${result.document.title}`
            : result.status === 'failed'
              ? `Could not index ${result.document.title}`
              : `Partly indexed ${result.document.title}`
      }
      action={
        <div className="flex items-center gap-2">
          <Link to={`/read/${result.document.id}`}>
            <Button size="sm" variant="primary">
              Open
            </Button>
          </Link>
          <Button size="sm" variant="ghost" onClick={onDismiss}>
            Dismiss
          </Button>
        </div>
      }
    >
      <p>
        {result.page_count} pages, {result.chunk_count} searchable passages,{' '}
        {result.topic_count} topics detected
        {result.duration_ms ? ` in ${(result.duration_ms / 1000).toFixed(1)}s` : ''}.
      </p>
      {result.warnings.length > 0 ? (
        <ul className="mt-1 list-disc space-y-0.5 pl-4">
          {result.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
    </Callout>
  );
}

function UploadDialog({
  open,
  courseId,
  courses,
  onClose,
  onUploaded,
}: {
  open: boolean;
  courseId: string | null;
  courses: Array<{ id: string; title: string }>;
  onClose: () => void;
  onUploaded: (result: UploadResult) => void;
}) {
  const upload = useUploadDocument();
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [kind, setKind] = useState('lecture');
  const [selectedCourse, setSelectedCourse] = useState(courseId ?? '');
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const effectiveCourse = selectedCourse || courseId || courses[0]?.id || '';

  const accept = useCallback((candidate: File | undefined) => {
    if (!candidate) return;
    const name = candidate.name.toLowerCase();
    const ok =
      candidate.type === 'application/pdf' ||
      /\.(pdf|pptx|png|jpe?g|webp|wav|mp3|m4a)$/.test(name) ||
      candidate.type.startsWith('image/') ||
      candidate.type.startsWith('audio/');
    if (!ok) {
      setLocalError(
        'Supported files: PDF, PowerPoint (.pptx), PNG/JPEG/WebP images, and WAV/MP3/M4A audio. An old .ppt file needs saving as .pptx first.',
      );
      return;
    }
    setLocalError(null);
    setFile(candidate);
    if (!title) setTitle(candidate.name.replace(/\.pdf$/i, ''));
  }, [title]);

  const reset = () => {
    setFile(null);
    setTitle('');
    setKind('lecture');
    setLocalError(null);
    upload.reset();
  };

  return (
    <Dialog
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title="Upload a document"
      description="PDF only for now. The file is stored on this device and indexed locally."
      footer={
        <>
          <Button
            onClick={() => {
              reset();
              onClose();
            }}
          >
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={upload.isPending}
            disabled={!file || !effectiveCourse}
            onClick={async () => {
              if (!file || !effectiveCourse) return;
              try {
                const result = await upload.mutateAsync({
                  courseId: effectiveCourse,
                  file,
                  title: title.trim() || undefined,
                  kind,
                });
                reset();
                onUploaded(result);
              } catch {
                // Surfaced below via upload.error.
              }
            }}
          >
            {upload.isPending ? 'Indexing…' : 'Upload and index'}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {localError ? <ErrorPanel title="Wrong file type" message={localError} /> : null}
        {upload.error ? (
          <ErrorPanel title="Upload failed" message={describeError(upload.error)} />
        ) : null}

        <div
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            accept(event.dataTransfer.files[0]);
          }}
          className={[
            'rounded-[var(--radius-control)] border-2 border-dashed px-4 py-6 text-center transition-colors',
            dragging
              ? 'border-[var(--color-accent-500)] bg-[var(--color-accent-50)]'
              : 'border-[var(--border-strong)] bg-[var(--surface-sunken)]',
          ].join(' ')}
        >
          <input
            id={inputId}
            ref={inputRef}
            type="file"
            accept="application/pdf,.pdf,application/vnd.openxmlformats-officedocument.presentationml.presentation,.pptx,image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp,audio/wav,audio/mpeg,audio/mp4,.wav,.mp3,.m4a"
            className="sr-only"
            onChange={(event) => accept(event.target.files?.[0])}
          />
          <Upload size={20} className="mx-auto text-[var(--text-muted)]" />
          {file ? (
            <p className="mt-2 text-[0.8125rem] font-medium text-[var(--text-primary)]">
              {file.name} <span className="font-normal">({formatBytes(file.size)})</span>
            </p>
          ) : (
            <p className="mt-2 text-[0.8125rem] text-[var(--text-secondary)]">
              Drop a PDF here, or
            </p>
          )}
          <Button size="sm" className="mt-2" onClick={() => inputRef.current?.click()}>
            {file ? 'Choose a different file' : 'Choose a file'}
          </Button>
        </div>

        {courses.length > 1 ? (
          <Field label="Course">
            <Select
              value={effectiveCourse}
              onChange={(event) => setSelectedCourse(event.target.value)}
            >
              {courses.map((course) => (
                <option key={course.id} value={course.id}>
                  {course.title}
                </option>
              ))}
            </Select>
          </Field>
        ) : null}

        <Field label="Title" hint="Defaults to the file name.">
          <Input value={title} onChange={(event) => setTitle(event.target.value)} />
        </Field>

        <Field
          label="What kind of document is this?"
          hint="Questions are only ever labelled as coming from a past paper if you upload one and say so."
        >
          <Select value={kind} onChange={(event) => setKind(event.target.value)}>
            {DOCUMENT_KINDS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </Field>
      </div>
    </Dialog>
  );
}
