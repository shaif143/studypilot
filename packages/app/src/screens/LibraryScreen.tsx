/**
 * Lecture library: upload documents, link Google Drive folders, open the reader.
 *
 * The upload result is reported exactly as the backend returned it — page
 * count, indexed passage count, and any extraction warnings — because a PDF
 * that produced no text is a very different outcome from one that indexed
 * cleanly, and the student needs to know which they got. A folder upload is
 * the same, once per file, with the files the folder held but the app could
 * not take listed by name.
 *
 * A Drive folder is different in kind: the files stay in Drive, the app keeps
 * only its index, and "Sync now" follows the folder. Those documents say so
 * in the list, because opening one needs the connection to be live.
 */
import { formatBytes, formatRelativeTime } from '@studypilot/shared';
import type {
  DocumentStatus,
  DriveFile,
  DriveImportOutcome,
  DriveImportResult,
  DriveImportStatus,
  DriveLink,
  LocalFolderLink,
  UploadResult,
} from '@studypilot/shared';
import {
  Badge,
  Button,
  Callout,
  Card,
  Dialog,
  EmptyState,
  Field,
  Input,
  ProgressBar,
  Select,
  Skeleton,
  Spinner,
  Toggle,
  type Tone,
} from '@studypilot/ui';
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  ChevronRight,
  Cloud,
  FileText,
  Folder,
  FolderOpen,
  HardDrive,
  RefreshCw,
  Trash2,
  Unlink,
  Upload,
  X,
} from 'lucide-react';
import { useCallback, useId, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';

import { ErrorPanel, PageBody, PageHeader } from '../components/AppShell';
import { useApp } from '../context';
import {
  describeError,
  useCourses,
  useDeleteDocument,
  useDocuments,
  useDriveFiles,
  useGoogleDrive,
  useImportFromDrive,
  useLinkLocalFolder,
  useLocalFolders,
  useProviders,
  useRescanLocalFolder,
  useSettings,
  useUnlinkLocalFolder,
  useSyncDriveLink,
  useUnlinkDriveFolder,
  useUploadArchive,
  useUploadDocument,
} from '../hooks/queries';
import {
  ACCEPT_ATTRIBUTE,
  SUPPORTED_DESCRIPTION,
  filesFromDrop,
  isArchive,
  mayNeedModel,
  partitionFiles,
  relativePathOf,
  titleFor,
  type Rejected,
} from '../lib/uploads';

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

/** One file's upload, as it happened. */
interface UploadEntry {
  file: File;
  /** The name shown; for an archive member, "archive.zip/path/inside". */
  label: string;
  result?: UploadResult;
  error?: string;
}

export interface UploadBatch {
  entries: UploadEntry[];
  rejected: Rejected[];
}

export function LibraryScreen() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const courseFilter = params.get('course') ?? undefined;

  const { platform } = useApp();
  const courses = useCourses();
  const documents = useDocuments(courseFilter);
  const deleteDocument = useDeleteDocument();
  const drive = useGoogleDrive();
  const localFolders = useLocalFolders();

  const [uploadFor, setUploadFor] = useState<string | null>(null);
  const [driveFor, setDriveFor] = useState<string | null>(null);
  const [folderFor, setFolderFor] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<{ id: string; title: string } | null>(null);
  const [lastBatch, setLastBatch] = useState<UploadBatch | null>(null);
  const [lastImport, setLastImport] = useState<DriveImportResult | null>(null);

  const hasCourses = (courses.data?.length ?? 0) > 0;
  const defaultCourse = courseFilter ?? courses.data?.[0]?.id ?? null;
  const links = (drive.data?.links ?? []).filter(
    (link) => !courseFilter || link.course_id === courseFilter,
  );
  const canLinkFolders = platform.kind === 'desktop' && localFolders.data?.available !== false;
  const folderLinks = (localFolders.data?.links ?? []).filter(
    (link) => !courseFilter || link.course_id === courseFilter,
  );

  return (
    <>
      <PageHeader
        title="Lecture Library"
        description={
          canLinkFolders
            ? 'Link a folder on this Mac and it is indexed where it is — nothing copied. Uploads and Google Drive folders work too.'
            : 'PDF lectures, past papers and assignment briefs. Uploads stay on this device; a linked Google Drive folder is read from Drive and only its index is kept here.'
        }
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
            {canLinkFolders ? (
              <Button
                variant="primary"
                icon={<HardDrive size={15} />}
                disabled={!hasCourses}
                title={hasCourses ? undefined : 'Create a course first'}
                onClick={() => setFolderFor(defaultCourse)}
              >
                Link a folder
              </Button>
            ) : null}
            <Button
              icon={<Cloud size={15} />}
              disabled={!hasCourses}
              title={hasCourses ? undefined : 'Create a course first'}
              onClick={() => setDriveFor(defaultCourse)}
            >
              Google Drive
            </Button>
            <Button
              variant={canLinkFolders ? 'secondary' : 'primary'}
              icon={<Upload size={15} />}
              disabled={!hasCourses}
              title={hasCourses ? undefined : 'Create a course first'}
              onClick={() => setUploadFor(defaultCourse)}
            >
              Upload
            </Button>
          </>
        }
      />
      <PageBody>
        <div className="space-y-4">
          {lastBatch ? (
            <BatchSummary batch={lastBatch} onDismiss={() => setLastBatch(null)} />
          ) : null}
          {lastImport ? (
            <ImportSummary result={lastImport} onDismiss={() => setLastImport(null)} />
          ) : null}

          {folderLinks.length > 0 ? (
            <LinkedLocalFolders links={folderLinks} courses={courses.data ?? []} />
          ) : null}

          {links.length > 0 ? (
            <LinkedFolders
              links={links}
              courses={courses.data ?? []}
              onSynced={setLastImport}
            />
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
              description="Upload lecture files or a whole folder, or link a Google Drive folder. StudyPilot extracts the text, equations and figures, detects topics, and indexes everything for search."
              action={
                <div className="flex flex-wrap justify-center gap-2">
                  {canLinkFolders ? (
                    <Button
                      variant="primary"
                      icon={<HardDrive size={14} />}
                      onClick={() => setFolderFor(defaultCourse)}
                    >
                      Link a folder on this Mac
                    </Button>
                  ) : null}
                  <Button
                    variant={canLinkFolders ? 'secondary' : 'primary'}
                    onClick={() => setUploadFor(defaultCourse)}
                  >
                    Upload files or a folder
                  </Button>
                  <Button icon={<Cloud size={14} />} onClick={() => setDriveFor(defaultCourse)}>
                    Link a Google Drive folder
                  </Button>
                </div>
              }
            />
          ) : (
            <ul className="space-y-2">
              {documents.data?.map((document) => {
                const course = courses.data?.find((item) => item.id === document.course_id);
                const fromDrive = document.source === 'google_drive';
                const fromFolder = document.source === 'local_folder';
                const drivePath =
                  document.extraction_meta.drive_path ?? document.extraction_meta.folder_path;
                return (
                  <li key={document.id}>
                    <Card className="flex flex-wrap items-center gap-4" padded={false}>
                      <div className="flex min-w-0 flex-1 items-center gap-3 py-3 pl-4">
                        {fromDrive ? (
                          <Cloud size={18} className="shrink-0 text-[var(--text-muted)]" />
                        ) : fromFolder ? (
                          <HardDrive size={18} className="shrink-0 text-[var(--text-muted)]" />
                        ) : (
                          <FileText size={18} className="shrink-0 text-[var(--text-muted)]" />
                        )}
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
                            {fromDrive ? (
                              <Badge
                                tone="info"
                                icon={<Cloud size={11} />}
                                title="The file stays in Google Drive; only its index is kept here."
                              >
                                Google Drive
                              </Badge>
                            ) : null}
                            {fromFolder ? (
                              <Badge
                                tone="info"
                                icon={<HardDrive size={11} />}
                                title={`Indexed in place from ${document.source_ref}; nothing copied.`}
                              >
                                On this Mac
                              </Badge>
                            ) : null}
                          </div>
                          <p className="mt-0.5 truncate text-[0.75rem] text-[var(--text-secondary)]">
                            {course ? `${course.title} · ` : ''}
                            {drivePath ? `${drivePath} · ` : ''}
                            {document.page_count} pages · {formatBytes(document.byte_size)}
                            {fromDrive ? ' in Drive' : fromFolder ? ' in place' : ''} ·{' '}
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
          onUploaded={(batch) => {
            setUploadFor(null);
            setLastImport(null);
            setLastBatch(batch);
          }}
        />

        <LinkFolderDialog
          open={folderFor !== null}
          courseId={folderFor}
          courses={courses.data ?? []}
          onClose={() => setFolderFor(null)}
        />

        <DriveImportDialog
          open={driveFor !== null}
          courseId={driveFor}
          courses={courses.data ?? []}
          onClose={() => setDriveFor(null)}
          onImported={(result) => {
            setDriveFor(null);
            setLastBatch(null);
            setLastImport(result);
          }}
        />

        <Dialog
          open={confirmDelete !== null}
          onClose={() => setConfirmDelete(null)}
          title={`Delete ${confirmDelete?.title ?? 'document'}?`}
          description="The file, its page previews and every index entry built from it are removed from this device. Quizzes already generated from it are kept. A file in Google Drive or in a linked folder is not touched."
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

// --- upload results --------------------------------------------------------------
function resultTone(result: UploadResult): Tone {
  return result.status === 'indexed' ? 'grounded' : result.status === 'failed' ? 'danger' : 'caution';
}

function BatchSummary({ batch, onDismiss }: { batch: UploadBatch; onDismiss: () => void }) {
  const { entries, rejected } = batch;
  const indexed = entries.filter((e) => e.result?.status === 'indexed').length;
  const failed = entries.filter((e) => !e.result || e.result.status === 'failed').length;
  const tone: Tone = failed === 0 && rejected.length === 0 ? 'grounded' : failed === entries.length ? 'danger' : 'caution';

  if (entries.length === 1 && rejected.length === 0) {
    const [entry] = entries;
    if (entry?.result) return <SingleSummary result={entry.result} onDismiss={onDismiss} />;
  }

  return (
    <Callout
      tone={tone}
      icon={tone === 'grounded' ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />}
      title={`${indexed} of ${entries.length} file${entries.length === 1 ? '' : 's'} indexed${
        rejected.length ? `, ${rejected.length} not taken` : ''
      }`}
      action={
        <Button size="sm" variant="ghost" onClick={onDismiss}>
          Dismiss
        </Button>
      }
    >
      <ul className="max-h-56 space-y-0.5 overflow-y-auto text-[0.75rem]">
        {entries.map((entry, index) => (
          <li key={`${entry.label}-${index}`} className="flex flex-wrap items-center gap-1.5">
            <span className="truncate">{entry.label}</span>
            {entry.result ? (
              <>
                <Badge tone={resultTone(entry.result)}>
                  {entry.result.deduplicated ? 'already here' : STATUS_LABEL[entry.result.status]}
                </Badge>
                <span className="text-[var(--text-muted)]">
                  {entry.result.page_count} pages, {entry.result.chunk_count} passages
                </span>
                <Link to={`/read/${entry.result.document.id}`} className="underline">
                  Open
                </Link>
              </>
            ) : (
              <Badge tone="danger">{entry.error ?? 'Failed'}</Badge>
            )}
          </li>
        ))}
        {rejected.map((item) => (
          <li key={relativePathOf(item.file)} className="flex flex-wrap items-center gap-1.5">
            <span className="truncate text-[var(--text-muted)]">{relativePathOf(item.file)}</span>
            <Badge tone="neutral">{item.reason}</Badge>
          </li>
        ))}
      </ul>
    </Callout>
  );
}

function SingleSummary({ result, onDismiss }: { result: UploadResult; onDismiss: () => void }) {
  const tone = resultTone(result);
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

// --- the upload dialog -----------------------------------------------------------
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
  onUploaded: (batch: UploadBatch) => void;
}) {
  const upload = useUploadDocument();
  const uploadArchive = useUploadArchive();
  const settings = useSettings();
  const providers = useProviders();
  const inputId = useId();
  const filesRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);

  const [files, setFiles] = useState<File[]>([]);
  const [rejected, setRejected] = useState<Rejected[]>([]);
  const [title, setTitle] = useState('');
  const [kind, setKind] = useState('lecture');
  const [selectedCourse, setSelectedCourse] = useState(courseId ?? '');
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState<{ done: number; current: string } | null>(null);
  // Consent for a cloud model to read images and recordings in this batch.
  // Starts from the Settings switch; the user can decline for one upload.
  const [allowCloud, setAllowCloud] = useState<boolean | null>(null);

  const effectiveCourse = selectedCourse || courseId || courses[0]?.id || '';
  const busy = progress !== null;
  const cloudAllowedInSettings = Boolean(settings.data?.allow_cloud_processing);
  const cloudForThisUpload = allowCloud ?? cloudAllowedInSettings;
  const needsModel = files.some(mayNeedModel);
  const activeProvider = providers.data?.llm.find((p) => p.key === providers.data?.active_llm);
  const canReadMedia = Boolean(activeProvider?.accepts_images || activeProvider?.accepts_audio);

  const take = useCallback((candidates: Iterable<File>) => {
    const partition = partitionFiles(candidates);
    setFiles((current) => partitionFiles([...current, ...partition.accepted]).accepted);
    setRejected((current) => [...current, ...partition.rejected]);
  }, []);

  const reset = () => {
    setFiles([]);
    setRejected([]);
    setTitle('');
    setKind('lecture');
    setProgress(null);
    upload.reset();
    uploadArchive.reset();
  };

  const close = () => {
    if (busy) return;
    reset();
    onClose();
  };

  const start = async () => {
    if (files.length === 0 || !effectiveCourse) return;
    const entries: UploadEntry[] = [];
    for (const [index, file] of files.entries()) {
      const label = relativePathOf(file);
      setProgress({ done: index, current: label });
      try {
        if (isArchive(file)) {
          // The backend unpacks it and answers once per member.
          const unpacked = await uploadArchive.mutateAsync({
            courseId: effectiveCourse,
            file,
            kind,
            allowCloud: cloudForThisUpload,
          });
          for (const member of unpacked.members) {
            entries.push({
              file,
              label: member.path ? `${label}/${member.path}` : label,
              result: member.result ?? undefined,
              error: member.result ? undefined : member.detail || member.status,
            });
          }
          continue;
        }
        const result = await upload.mutateAsync({
          courseId: effectiveCourse,
          file,
          title: files.length === 1 ? title.trim() || undefined : titleFor(file),
          kind,
          allowCloud: cloudForThisUpload,
        });
        entries.push({ file, label, result });
      } catch (error) {
        entries.push({ file, label, error: describeError(error) });
      }
    }
    setProgress(null);
    const batch = { entries, rejected };
    reset();
    onUploaded(batch);
  };

  const totalBytes = files.reduce((sum, file) => sum + file.size, 0);

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Upload files or a folder"
      description="Everything is stored and indexed on this device. A folder is taken whole, including its subfolders; files of other types are listed, not lost."
      width="lg"
      footer={
        <>
          <Button onClick={close} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={busy}
            disabled={files.length === 0 || !effectiveCourse}
            onClick={() => void start()}
          >
            {busy
              ? `Indexing ${progress.done + 1} of ${files.length}…`
              : files.length > 1
                ? `Upload and index ${files.length} files`
                : 'Upload and index'}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {upload.error && !busy ? (
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
            void filesFromDrop(event.dataTransfer).then(take);
          }}
          className={[
            'rounded-[var(--radius-control)] border-2 border-dashed px-4 py-5 text-center transition-colors',
            dragging
              ? 'border-[var(--color-accent-500)] bg-[var(--color-accent-50)]'
              : 'border-[var(--border-strong)] bg-[var(--surface-sunken)]',
          ].join(' ')}
        >
          <input
            id={inputId}
            ref={filesRef}
            type="file"
            multiple
            accept={ACCEPT_ATTRIBUTE}
            className="sr-only"
            onChange={(event) => {
              take(Array.from(event.target.files ?? []));
              event.target.value = '';
            }}
          />
          <input
            ref={folderRef}
            type="file"
            multiple
            aria-label="Choose a folder"
            className="sr-only"
            // A folder picker: not in React's typings, understood by every
            // browser the app runs in.
            {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
            onChange={(event) => {
              take(Array.from(event.target.files ?? []));
              event.target.value = '';
            }}
          />
          <Upload size={20} className="mx-auto text-[var(--text-muted)]" />
          <p className="mt-2 text-[0.8125rem] text-[var(--text-secondary)]">
            {files.length === 0
              ? 'Drop files or a folder here, or'
              : `${files.length} file${files.length === 1 ? '' : 's'}, ${formatBytes(totalBytes)}`}
          </p>
          <div className="mt-2 flex flex-wrap justify-center gap-2">
            <Button size="sm" disabled={busy} onClick={() => filesRef.current?.click()}>
              Choose files
            </Button>
            <Button
              size="sm"
              disabled={busy}
              icon={<FolderOpen size={13} />}
              onClick={() => folderRef.current?.click()}
            >
              Choose a folder
            </Button>
          </div>
          <p className="mt-2 text-[0.6875rem] text-[var(--text-muted)]">{SUPPORTED_DESCRIPTION}</p>
        </div>

        {busy ? (
          <div>
            <ProgressBar value={progress.done / files.length} label={progress.current} />
          </div>
        ) : null}

        {files.length > 0 ? (
          <ul className="max-h-40 space-y-1 overflow-y-auto rounded-[var(--radius-control)] border border-[var(--border-subtle)] p-2 text-[0.75rem]">
            {files.map((file) => (
              <li key={relativePathOf(file)} className="flex items-center gap-2">
                <FileText size={12} className="shrink-0 text-[var(--text-muted)]" />
                <span className="min-w-0 flex-1 truncate">{relativePathOf(file)}</span>
                <span className="text-[var(--text-muted)]">{formatBytes(file.size)}</span>
                <button
                  type="button"
                  aria-label={`Remove ${file.name}`}
                  disabled={busy}
                  className="text-[var(--text-muted)] hover:text-[var(--text-primary)]"
                  onClick={() => setFiles((current) => current.filter((item) => item !== file))}
                >
                  <X size={12} />
                </button>
              </li>
            ))}
          </ul>
        ) : null}

        {rejected.length > 0 ? (
          <Callout tone="caution" icon={<AlertTriangle size={14} />} title="Not taken">
            <ul className="space-y-0.5">
              {rejected.map((item) => (
                <li key={relativePathOf(item.file)}>
                  {relativePathOf(item.file)} — {item.reason}
                </li>
              ))}
            </ul>
          </Callout>
        ) : null}

        {needsModel ? (
          <div className="rounded-[var(--radius-control)] border border-[var(--border-subtle)] px-3">
            <Toggle
              checked={cloudForThisUpload}
              disabled={busy || !cloudAllowedInSettings}
              onChange={setAllowCloud}
              label="Let a cloud model read the images and recordings"
              description={
                !cloudAllowedInSettings
                  ? 'Images and audio are read by a model. Turn on "Allow cloud AI processing" in Settings (and add a Claude or Gemini key) to include them; otherwise they are skipped with a note.'
                  : canReadMedia
                    ? `${activeProvider?.label ?? 'The active provider'} will read them; the files are sent to it. Off, they are skipped with a note.`
                    : 'The active provider cannot read images or audio. Choose Claude or Gemini in Settings, or these files are skipped with a note.'
              }
            />
          </div>
        ) : null}

        {courses.length > 1 ? (
          <Field label="Course">
            <Select
              value={effectiveCourse}
              disabled={busy}
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

        {files.length === 1 ? (
          <Field label="Title" hint="Defaults to the file name.">
            <Input
              value={title}
              disabled={busy}
              placeholder={files[0] ? titleFor(files[0]) : ''}
              onChange={(event) => setTitle(event.target.value)}
            />
          </Field>
        ) : null}

        <Field
          label={files.length > 1 ? 'What kind of documents are these?' : 'What kind of document is this?'}
          hint="Questions are only ever labelled as coming from a past paper if you upload one and say so."
        >
          <Select value={kind} disabled={busy} onChange={(event) => setKind(event.target.value)}>
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

// --- Google Drive ------------------------------------------------------------------
const IMPORT_TONE: Record<DriveImportStatus, Tone> = {
  indexed: 'grounded',
  partial: 'caution',
  failed: 'danger',
  skipped: 'neutral',
  unchanged: 'neutral',
  removed: 'caution',
};

const IMPORT_LABEL: Record<DriveImportStatus, string> = {
  indexed: 'Indexed',
  partial: 'Partly indexed',
  failed: 'Failed',
  skipped: 'Skipped',
  unchanged: 'Unchanged',
  removed: 'Removed',
};

function countsSentence(counts: DriveImportResult['counts']): string {
  const parts = (Object.keys(IMPORT_LABEL) as DriveImportStatus[])
    .filter((status) => counts[status])
    .map((status) => `${counts[status]} ${IMPORT_LABEL[status].toLowerCase()}`);
  return parts.length ? parts.join(', ') : 'nothing to do';
}

function ImportSummary({
  result,
  onDismiss,
}: {
  result: DriveImportResult;
  onDismiss: () => void;
}) {
  const failed = (result.counts.failed ?? 0) > 0;
  const changed = (result.counts.indexed ?? 0) + (result.counts.partial ?? 0) + (result.counts.removed ?? 0);
  const tone: Tone = failed ? 'caution' : changed > 0 ? 'grounded' : 'info';
  return (
    <Callout
      tone={tone}
      icon={failed ? <AlertTriangle size={14} /> : <Cloud size={14} />}
      title={`Google Drive: ${countsSentence(result.counts)}`}
      action={
        <Button size="sm" variant="ghost" onClick={onDismiss}>
          Dismiss
        </Button>
      }
    >
      <p className="mb-1">
        Only the search index is kept here; the files stay in Drive
        {result.duration_ms ? ` (${(result.duration_ms / 1000).toFixed(1)}s)` : ''}.
      </p>
      <OutcomeList outcomes={result.outcomes} />
    </Callout>
  );
}

function OutcomeList({ outcomes }: { outcomes: DriveImportOutcome[] }) {
  return (
    <ul className="max-h-56 space-y-0.5 overflow-y-auto text-[0.75rem]">
      {outcomes.map((outcome) => (
        <li key={outcome.file_id} className="flex flex-wrap items-center gap-1.5">
          <span className="truncate">
            {outcome.path ? `${outcome.path}/` : ''}
            {outcome.name}
          </span>
          <Badge tone={IMPORT_TONE[outcome.status]}>{IMPORT_LABEL[outcome.status]}</Badge>
          {outcome.detail ? (
            <span className="text-[var(--text-muted)]">{outcome.detail}</span>
          ) : null}
          {outcome.document_id && outcome.status !== 'removed' ? (
            <Link to={`/read/${outcome.document_id}`} className="underline">
              Open
            </Link>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function LinkedFolders({
  links,
  courses,
  onSynced,
}: {
  links: DriveLink[];
  courses: Array<{ id: string; title: string }>;
  onSynced: (result: DriveImportResult) => void;
}) {
  const sync = useSyncDriveLink();
  const unlink = useUnlinkDriveFolder();
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <Cloud size={15} className="text-[var(--text-muted)]" />
        <span className="text-[0.8125rem] font-medium text-[var(--text-primary)]">
          Linked Google Drive folders
        </span>
        <span className="text-[0.75rem] text-[var(--text-muted)]">
          Files stay in Drive; Sync now re-reads what changed.
        </span>
      </div>
      <ul className="mt-2 divide-y divide-[var(--border-subtle)]">
        {links.map((link) => {
          const course = courses.find((item) => item.id === link.course_id);
          const syncing = sync.isPending && sync.variables === link.id;
          return (
            <li key={link.id} className="flex flex-wrap items-center gap-3 py-2">
              <Folder size={14} className="shrink-0 text-[var(--text-muted)]" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-[0.8125rem] text-[var(--text-primary)]">
                  {link.folder_name}
                  {link.recursive ? '' : ' (this folder only)'}
                </p>
                <p className="text-[0.75rem] text-[var(--text-secondary)]">
                  {course ? `${course.title} · ` : ''}
                  {link.last_synced_at
                    ? `synced ${formatRelativeTime(link.last_synced_at)}: ${countsSentence(link.last_summary)}`
                    : 'never synced'}
                </p>
              </div>
              <Button
                size="sm"
                icon={<RefreshCw size={13} />}
                loading={syncing}
                onClick={() => sync.mutate(link.id, { onSuccess: onSynced })}
              >
                Sync now
              </Button>
              <Button
                size="sm"
                variant="ghost"
                icon={<Unlink size={13} />}
                loading={unlink.isPending && unlink.variables === link.id}
                onClick={() => unlink.mutate(link.id)}
                title="Stop syncing. Documents already indexed stay."
              >
                Unlink
              </Button>
            </li>
          );
        })}
      </ul>
      {sync.error ? (
        <Callout tone="danger" className="mt-2" icon={<AlertTriangle size={14} />}>
          {describeError(sync.error)}
        </Callout>
      ) : null}
    </Card>
  );
}

interface Crumb {
  id: string;
  name: string;
}

function DriveImportDialog({
  open,
  courseId,
  courses,
  onClose,
  onImported,
}: {
  open: boolean;
  courseId: string | null;
  courses: Array<{ id: string; title: string }>;
  onClose: () => void;
  onImported: (result: DriveImportResult) => void;
}) {
  const drive = useGoogleDrive();
  const importFromDrive = useImportFromDrive();
  const [selectedCourse, setSelectedCourse] = useState(courseId ?? '');
  const [crumbs, setCrumbs] = useState<Crumb[]>([{ id: 'root', name: 'My Drive' }]);
  const [pasted, setPasted] = useState('');
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [recursive, setRecursive] = useState(true);
  const [keepLinked, setKeepLinked] = useState(true);

  const connected = Boolean(drive.data?.connected);
  const current = crumbs[crumbs.length - 1] ?? { id: 'root', name: 'My Drive' };
  const listing = useDriveFiles(current.id, open && connected);
  const effectiveCourse = selectedCourse || courseId || courses[0]?.id || '';
  const busy = importFromDrive.isPending;

  const openFolder = (file: DriveFile) => {
    setCrumbs((stack) => [...stack, { id: file.id, name: file.name }]);
    setChosen(new Set());
  };

  const goTo = (index: number) => {
    setCrumbs((stack) => stack.slice(0, index + 1));
    setChosen(new Set());
  };

  const openPasted = () => {
    const text = pasted.trim();
    if (!text) return;
    setCrumbs([{ id: text, name: 'Pasted folder' }]);
    setChosen(new Set());
    setPasted('');
  };

  const close = () => {
    if (busy) return;
    importFromDrive.reset();
    onClose();
  };

  const run = async () => {
    if (!effectiveCourse) return;
    const folderId = listing.data?.folder.id ?? current.id;
    try {
      const result = await importFromDrive.mutateAsync({
        course_id: effectiveCourse,
        folder: folderId,
        file_ids: chosen.size ? Array.from(chosen) : [],
        recursive,
        link: chosen.size === 0 && keepLinked,
      });
      setChosen(new Set());
      onImported(result);
    } catch {
      // Shown below through importFromDrive.error.
    }
  };

  const importableCount = listing.data?.files.filter((f) => f.importable).length ?? 0;
  const folderCount = listing.data?.files.filter((f) => f.is_folder).length ?? 0;

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Import from Google Drive"
      description="Pick a folder, or files in it. Nothing is copied: StudyPilot reads each file once to index it, and opens it from Drive when you read it."
      width="lg"
      footer={
        <>
          <Button onClick={close} disabled={busy}>
            {busy ? 'Working…' : 'Close'}
          </Button>
          {connected ? (
            <Button
              variant="primary"
              icon={<Cloud size={14} />}
              loading={busy}
              disabled={!effectiveCourse || !listing.data || (chosen.size === 0 && importableCount + folderCount === 0)}
              onClick={() => void run()}
            >
              {chosen.size > 0
                ? `Index ${chosen.size} selected`
                : `Index everything in ${listing.data?.folder.name ?? 'this folder'}`}
            </Button>
          ) : null}
        </>
      }
    >
      <div className="space-y-3">
        {drive.isPending ? (
          <Skeleton className="h-16" />
        ) : !drive.data?.configured || !connected ? (
          <Callout tone="info" icon={<Cloud size={14} />} title="Connect Google Drive first">
            {drive.data?.configured
              ? 'Your Google account is not connected yet. '
              : 'Google Drive is not set up on this installation yet. '}
            <Link to="/settings" className="underline">
              Open Settings → Google Drive
            </Link>
            .
          </Callout>
        ) : (
          <>
            {courses.length > 1 ? (
              <Field label="Into course">
                <Select
                  value={effectiveCourse}
                  disabled={busy}
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

            <form
              className="flex flex-wrap items-end gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                openPasted();
              }}
            >
              <Field
                label="Open a folder by link"
                hint="Paste the link Drive gives you for a folder, or browse below."
                className="min-w-[16rem] flex-1"
              >
                <Input
                  value={pasted}
                  disabled={busy}
                  placeholder="https://drive.google.com/drive/folders/…"
                  onChange={(event) => setPasted(event.target.value)}
                />
              </Field>
              <Button type="submit" size="sm" disabled={!pasted.trim() || busy}>
                Open
              </Button>
            </form>

            <nav aria-label="Folder path" className="flex flex-wrap items-center gap-1 text-[0.8125rem]">
              {crumbs.map((crumb, index) => (
                <span key={`${crumb.id}-${index}`} className="flex items-center gap-1">
                  {index > 0 ? <ChevronRight size={12} className="text-[var(--text-muted)]" /> : null}
                  <button
                    type="button"
                    disabled={busy || index === crumbs.length - 1}
                    className="rounded px-1 text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:text-[var(--text-primary)] disabled:font-medium"
                    onClick={() => goTo(index)}
                  >
                    {index === crumbs.length - 1 && listing.data ? listing.data.folder.name : crumb.name}
                  </button>
                </span>
              ))}
            </nav>

            <div className="max-h-72 overflow-y-auto rounded-[var(--radius-control)] border border-[var(--border-subtle)]">
              {listing.isPending ? (
                <div className="flex items-center gap-2 p-3 text-[0.8125rem] text-[var(--text-secondary)]">
                  <Spinner size={13} /> Reading the folder…
                </div>
              ) : listing.isError ? (
                <div className="p-3">
                  <ErrorPanel message={describeError(listing.error)} onRetry={() => void listing.refetch()} />
                </div>
              ) : listing.data?.files.length === 0 ? (
                <p className="p-3 text-[0.8125rem] text-[var(--text-secondary)]">This folder is empty.</p>
              ) : (
                <ul className="divide-y divide-[var(--border-subtle)]">
                  {listing.data?.files.map((file) =>
                    file.is_folder ? (
                      <li key={file.id}>
                        <button
                          type="button"
                          disabled={busy}
                          className="flex w-full items-center gap-2 px-3 py-2 text-left text-[0.8125rem] hover:bg-[var(--surface-hover)]"
                          onClick={() => openFolder(file)}
                        >
                          <Folder size={14} className="shrink-0 text-[var(--text-muted)]" />
                          <span className="min-w-0 flex-1 truncate">{file.name}</span>
                          <ChevronRight size={14} className="text-[var(--text-muted)]" />
                        </button>
                      </li>
                    ) : (
                      <li key={file.id}>
                        <label
                          className={[
                            'flex items-center gap-2 px-3 py-2 text-[0.8125rem]',
                            file.importable ? 'cursor-pointer hover:bg-[var(--surface-hover)]' : 'opacity-60',
                          ].join(' ')}
                          title={file.why_skipped || undefined}
                        >
                          <input
                            type="checkbox"
                            className="shrink-0"
                            disabled={!file.importable || busy}
                            checked={chosen.has(file.id)}
                            onChange={(event) =>
                              setChosen((set) => {
                                const next = new Set(set);
                                if (event.target.checked) next.add(file.id);
                                else next.delete(file.id);
                                return next;
                              })
                            }
                          />
                          <FileText size={14} className="shrink-0 text-[var(--text-muted)]" />
                          <span className="min-w-0 flex-1 truncate">{file.name}</span>
                          <span className="text-[0.75rem] text-[var(--text-muted)]">
                            {file.importable
                              ? file.size
                                ? formatBytes(file.size)
                                : 'Google format'
                              : file.why_skipped}
                          </span>
                        </label>
                      </li>
                    ),
                  )}
                </ul>
              )}
            </div>

            {chosen.size === 0 ? (
              <div className="divide-y divide-[var(--border-subtle)]">
                <Toggle
                  checked={recursive}
                  disabled={busy}
                  onChange={setRecursive}
                  label="Include subfolders"
                  description="Everything below this folder, up to 300 files per run."
                />
                <Toggle
                  checked={keepLinked}
                  disabled={busy}
                  onChange={setKeepLinked}
                  label="Keep this folder linked to the course"
                  description="Sync now in the Library re-reads changed files, adds new ones and removes deleted ones."
                />
              </div>
            ) : (
              <p className="text-[0.75rem] text-[var(--text-muted)]">
                Only the selected files are indexed; the folder is not linked. Clear the selection
                to index and link the whole folder.
              </p>
            )}

            {importFromDrive.error ? (
              <ErrorPanel title="Import failed" message={describeError(importFromDrive.error)} />
            ) : null}
          </>
        )}
      </div>
    </Dialog>
  );
}

// --- folders on this Mac -----------------------------------------------------------
function scanSentence(link: LocalFolderLink): string {
  const scan = link.scan;
  if (scan && (scan.state === 'queued' || scan.state === 'running')) {
    if (!scan.total) return 'Reading the folder…';
    const what = scan.phase === 'media' ? 'images and recordings' : 'documents';
    return `Indexing ${Math.min(scan.done + 1, scan.total)} of ${scan.total} (${what})${scan.current ? `: ${scan.current}` : ''}`;
  }
  if (scan?.state === 'failed') return `The last scan failed: ${scan.error}`;
  if (link.last_scanned_at) {
    return `scanned ${formatRelativeTime(link.last_scanned_at)}: ${countsSentence(link.last_summary)}`;
  }
  return 'not scanned yet';
}

function LinkedLocalFolders({
  links,
  courses,
}: {
  links: LocalFolderLink[];
  courses: Array<{ id: string; title: string }>;
}) {
  const rescan = useRescanLocalFolder();
  const unlink = useUnlinkLocalFolder();
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <HardDrive size={15} className="text-[var(--text-muted)]" />
        <span className="text-[0.8125rem] font-medium text-[var(--text-primary)]">
          Folders on this Mac
        </span>
        <span className="text-[0.75rem] text-[var(--text-muted)]">
          Indexed where they are, nothing copied. Rescan picks up what changed.
        </span>
      </div>
      <ul className="mt-2 divide-y divide-[var(--border-subtle)]">
        {links.map((link) => {
          const course = courses.find((item) => item.id === link.course_id);
          const running =
            link.scan !== null && (link.scan.state === 'queued' || link.scan.state === 'running');
          return (
            <li key={link.id} className="py-2">
              <div className="flex flex-wrap items-center gap-3">
                <Folder size={14} className="shrink-0 text-[var(--text-muted)]" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[0.8125rem] text-[var(--text-primary)]" title={link.path}>
                    {link.path.split('/').filter(Boolean).slice(-2).join('/')}
                    {link.recursive ? '' : ' (this folder only)'}
                  </p>
                  <p className="text-[0.75rem] text-[var(--text-secondary)]">
                    {course ? `${course.title} · ` : ''}
                    {scanSentence(link)}
                  </p>
                </div>
                <Button
                  size="sm"
                  icon={<RefreshCw size={13} />}
                  loading={running || (rescan.isPending && rescan.variables === link.id)}
                  onClick={() => rescan.mutate(link.id)}
                >
                  Rescan
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  icon={<Unlink size={13} />}
                  disabled={running}
                  loading={unlink.isPending && unlink.variables === link.id}
                  onClick={() => unlink.mutate(link.id)}
                  title="Stop indexing this folder. Its documents leave the library; the files are untouched."
                >
                  Unlink
                </Button>
              </div>
              {running && link.scan?.total ? (
                <ProgressBar className="mt-2" value={link.scan.done / link.scan.total} />
              ) : null}
            </li>
          );
        })}
      </ul>
      {rescan.error ? (
        <Callout tone="danger" className="mt-2" icon={<AlertTriangle size={14} />}>
          {describeError(rescan.error)}
        </Callout>
      ) : null}
    </Card>
  );
}

function LinkFolderDialog({
  open,
  courseId,
  courses,
  onClose,
}: {
  open: boolean;
  courseId: string | null;
  courses: Array<{ id: string; title: string }>;
  onClose: () => void;
}) {
  const { platform } = useApp();
  const link = useLinkLocalFolder();
  const settings = useSettings();
  const providers = useProviders();
  const [path, setPath] = useState('');
  const [selectedCourse, setSelectedCourse] = useState(courseId ?? '');
  const [recursive, setRecursive] = useState(true);
  const [readMedia, setReadMedia] = useState<boolean | null>(null);

  const effectiveCourse = selectedCourse || courseId || courses[0]?.id || '';
  const cloudAllowed = Boolean(settings.data?.allow_cloud_processing);
  const activeProvider = providers.data?.llm.find((p) => p.key === providers.data?.active_llm);
  const canReadImages = Boolean(activeProvider?.accepts_images);
  const readMediaValue = readMedia ?? cloudAllowed;

  const reset = () => {
    setPath('');
    setRecursive(true);
    setReadMedia(null);
    link.reset();
  };

  const choose = async () => {
    if (!platform.pickFolder) return;
    const chosen = await platform.pickFolder();
    if (chosen) setPath(chosen);
  };

  return (
    <Dialog
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title="Link a folder on this Mac"
      description="The folder is indexed where it is: nothing is copied, and the reader opens the original files. Rescan later and new, changed or deleted files are followed."
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
            icon={<HardDrive size={14} />}
            loading={link.isPending}
            disabled={!path.trim() || !effectiveCourse}
            onClick={() => {
              link.mutate(
                {
                  course_id: effectiveCourse,
                  path: path.trim(),
                  recursive,
                  read_media: readMediaValue,
                },
                {
                  onSuccess: () => {
                    reset();
                    onClose();
                  },
                },
              );
            }}
          >
            Link and index
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {link.error ? <ErrorPanel title="Could not link" message={describeError(link.error)} /> : null}
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Folder" hint="PDF, PowerPoint, images and recordings inside it; archives are left where they are." className="min-w-[16rem] flex-1">
            <Input
              value={path}
              placeholder="/Users/you/University/EEEM030"
              spellCheck={false}
              onChange={(event) => setPath(event.target.value)}
            />
          </Field>
          {platform.pickFolder ? (
            <Button icon={<FolderOpen size={14} />} onClick={() => void choose()}>
              Choose…
            </Button>
          ) : null}
        </div>
        {courses.length > 1 ? (
          <Field label="Course">
            <Select value={effectiveCourse} onChange={(event) => setSelectedCourse(event.target.value)}>
              {courses.map((course) => (
                <option key={course.id} value={course.id}>
                  {course.title}
                </option>
              ))}
            </Select>
          </Field>
        ) : null}
        <div className="divide-y divide-[var(--border-subtle)] rounded-[var(--radius-control)] border border-[var(--border-subtle)] px-3">
          <Toggle
            checked={recursive}
            onChange={setRecursive}
            label="Include subfolders"
            description="Everything below the folder, up to 500 files per scan."
          />
          <Toggle
            checked={readMediaValue}
            disabled={!cloudAllowed}
            onChange={setReadMedia}
            label="Let a cloud model read the images and recordings"
            description={
              !cloudAllowed
                ? 'Turn on "Allow cloud AI processing" in Settings to include images; recordings are transcribed on this Mac either way when the local speech model is installed.'
                : canReadImages
                  ? `${activeProvider?.label ?? 'The active provider'} reads the images; recordings are transcribed on this Mac. Off, images are skipped.`
                  : 'The active provider cannot read images. Choose Claude or Gemini in Settings, or images are skipped.'
            }
          />
        </div>
      </div>
    </Dialog>
  );
}
