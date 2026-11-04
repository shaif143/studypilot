/**
 * The interactive slide reader: the screen students will spend their time in.
 *
 * Left: the real PDF page, with citation highlights drawn over it.
 * Right: the tutor, which answers only from this course's material.
 *
 * Reading position is written back to the session on every page change, which
 * is what makes "quit and reopen" land exactly where the student left off. The
 * checkpoint prompt appears after the configured number of pages, offering a
 * quiz over the pages just read rather than the whole deck.
 */
import type { Citation } from '@studypilot/shared';
import { Badge, Button, Callout, KeyHint, Select, Skeleton, Spinner } from '@studypilot/ui';
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  FileQuestion,
  Minus,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  Sigma,
  Table as TableIcon,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';

import { ErrorPanel } from '../components/AppShell';
import { type HighlightBox, PdfPageView } from '../components/PdfPageView';
import { TutorPanel } from '../components/TutorPanel';
import {
  describeError,
  useDocument,
  useDocumentTopics,
  usePage,
  usePages,
  useResumeSession,
  useSettings,
  useUpdateSession,
} from '../hooks/queries';

const MIN_PANEL = 26;
const MAX_PANEL = 68;

export function ReaderScreen() {
  const { documentId } = useParams<{ documentId: string }>();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();

  const documentQuery = useDocument(documentId);
  const pages = usePages(documentId);
  const topics = useDocumentTopics(documentId);
  const session = useResumeSession(documentId);
  const settings = useSettings();
  const updateSession = useUpdateSession();

  const requestedPage = Number.parseInt(params.get('page') ?? '', 10);
  const [pageNumber, setPageNumber] = useState<number>(
    Number.isFinite(requestedPage) && requestedPage > 0 ? requestedPage : 1,
  );
  const [zoom, setZoom] = useState(1);
  const [panelWidth, setPanelWidth] = useState(42);
  const [panelOpen, setPanelOpen] = useState(true);
  const [highlights, setHighlights] = useState<HighlightBox[]>([]);
  const [pagesRead, setPagesRead] = useState(0);
  const [checkpointDismissed, setCheckpointDismissed] = useState(false);

  const page = usePage(documentId, pageNumber);
  const pageCount = documentQuery.data?.page_count ?? pages.data?.length ?? 0;
  const checkpointEvery = settings.data?.quiz_checkpoint_slides ?? 5;

  // Adopt the saved reading position the first time the session loads, unless
  // the URL asked for a specific page.
  //
  // Done during render rather than in an effect: this is React's documented
  // pattern for seeding state from newly arrived data, and it avoids the extra
  // paint at the old page that an effect would cause.
  const [adoptedSessionId, setAdoptedSessionId] = useState<string | null>(null);
  if (session.data && adoptedSessionId !== session.data.id) {
    setAdoptedSessionId(session.data.id);
    if (!Number.isFinite(requestedPage) || requestedPage <= 0) {
      setPageNumber(Math.max(1, session.data.current_page));
    }
    setPagesRead(session.data.slides_since_checkpoint ?? 0);
  }

  const goToPage = useCallback(
    (next: number, options: { fromCitation?: boolean } = {}) => {
      const clamped = Math.max(1, Math.min(pageCount || next, next));
      setPageNumber((current) => {
        if (current === clamped) return current;
        if (!options.fromCitation) {
          setHighlights([]);
          setPagesRead((read) => read + 1);
        }
        return clamped;
      });
      setParams(
        (current) => {
          const copy = new URLSearchParams(current);
          copy.set('page', String(clamped));
          return copy;
        },
        { replace: true },
      );
    },
    [pageCount, setParams],
  );

  // Persist the position (debounced) so reopening resumes here.
  const sessionId = session.data?.id;
  const currentHeading = page.data?.heading ?? '';
  useEffect(() => {
    if (!sessionId) return;
    const handle = window.setTimeout(() => {
      updateSession.mutate({
        id: sessionId,
        patch: {
          current_page: pageNumber,
          current_topic: currentHeading,
          slides_since_checkpoint: pagesRead,
        },
      });
    }, 700);
    return () => window.clearTimeout(handle);
    // `updateSession` is a stable react-query mutation object.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pageNumber, pagesRead, currentHeading, sessionId]);

  // Keyboard navigation, ignored while typing.
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable)
      ) {
        return;
      }
      if (event.key === 'ArrowRight' || event.key === 'j' || event.key === ' ') {
        event.preventDefault();
        goToPage(pageNumber + 1);
      } else if (event.key === 'ArrowLeft' || event.key === 'k') {
        event.preventDefault();
        goToPage(pageNumber - 1);
      } else if (event.key === '=' || event.key === '+') {
        setZoom((value) => Math.min(3, value + 0.15));
      } else if (event.key === '-') {
        setZoom((value) => Math.max(0.5, value - 0.15));
      } else if (event.key === '0') {
        setZoom(1);
      } else if (event.key === 'f') {
        setPanelOpen((open) => !open);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [goToPage, pageNumber]);

  const onCitationClick = useCallback(
    (citation: Citation) => {
      if (citation.document_id && citation.document_id !== documentId) {
        navigate(`/read/${citation.document_id}?page=${citation.page_number}`);
        return;
      }
      goToPage(citation.page_number, { fromCitation: true });
      setHighlights(
        citation.bbox && citation.bbox.length === 4
          ? [{ bbox: citation.bbox, marker: citation.marker }]
          : [],
      );
    },
    [documentId, goToPage, navigate],
  );

  const currentTopic = useMemo(
    () =>
      topics.data?.find(
        (topic) => pageNumber >= topic.first_page && pageNumber <= topic.last_page,
      ),
    [pageNumber, topics.data],
  );

  const showCheckpoint = !checkpointDismissed && pagesRead >= checkpointEvery && pageCount > 0;

  // Drag-to-resize the split.
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);
  useEffect(() => {
    const move = (event: MouseEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const delta = ((drag.startX - event.clientX) / window.innerWidth) * 100;
      setPanelWidth(Math.max(MIN_PANEL, Math.min(MAX_PANEL, drag.startWidth + delta)));
    };
    const up = () => {
      dragRef.current = null;
      document.body.style.cursor = '';
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
    return () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    };
  }, []);

  if (documentQuery.isError) {
    return (
      <div className="p-6">
        <ErrorPanel
          title="Could not open this document"
          message={describeError(documentQuery.error)}
          onRetry={() => void documentQuery.refetch()}
        />
        <Link
          to="/library"
          className="mt-3 inline-block text-[0.8125rem] text-[var(--text-accent)]"
        >
          Back to the library
        </Link>
      </div>
    );
  }

  const firstCheckpointPage = Math.max(1, pageNumber - pagesRead);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border-subtle)] px-4 py-2">
        <Link
          to="/library"
          className="text-[0.75rem] font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
        >
          ← Library
        </Link>

        <div className="mx-1 h-4 w-px bg-[var(--border-subtle)]" />

        <div className="min-w-0 flex-1">
          {documentQuery.data ? (
            <p className="truncate text-[0.8125rem] font-semibold text-[var(--text-primary)]">
              {documentQuery.data.title}
            </p>
          ) : (
            <Skeleton className="h-4 w-48" />
          )}
          {currentTopic ? (
            <p className="truncate text-[0.6875rem] text-[var(--text-secondary)]">
              {currentTopic.name}
            </p>
          ) : null}
        </div>

        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            aria-label="Previous page"
            disabled={pageNumber <= 1}
            onClick={() => goToPage(pageNumber - 1)}
          >
            <ChevronLeft size={15} />
          </Button>
          <Select
            aria-label="Page"
            className="h-7 w-[5.5rem] text-[0.75rem]"
            value={pageNumber}
            onChange={(event) => goToPage(Number(event.target.value))}
          >
            {Array.from({ length: pageCount }, (_, index) => (
              <option key={index + 1} value={index + 1}>
                Page {index + 1}
              </option>
            ))}
          </Select>
          <span className="text-[0.6875rem] tabular-nums text-[var(--text-muted)]">
            / {pageCount}
          </span>
          <Button
            size="sm"
            variant="ghost"
            aria-label="Next page"
            disabled={pageCount > 0 && pageNumber >= pageCount}
            onClick={() => goToPage(pageNumber + 1)}
          >
            <ChevronRight size={15} />
          </Button>
        </div>

        <div className="mx-1 h-4 w-px bg-[var(--border-subtle)]" />

        <div className="flex items-center gap-0.5">
          <Button
            size="sm"
            variant="ghost"
            aria-label="Zoom out"
            onClick={() => setZoom((value) => Math.max(0.5, value - 0.15))}
          >
            <Minus size={14} />
          </Button>
          <button
            type="button"
            onClick={() => setZoom(1)}
            className="min-w-[2.75rem] text-[0.6875rem] tabular-nums text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            title="Reset zoom"
          >
            {Math.round(zoom * 100)}%
          </button>
          <Button
            size="sm"
            variant="ghost"
            aria-label="Zoom in"
            onClick={() => setZoom((value) => Math.min(3, value + 0.15))}
          >
            <Plus size={14} />
          </Button>
        </div>

        <Button
          size="sm"
          variant="ghost"
          aria-label={panelOpen ? 'Hide the tutor panel' : 'Show the tutor panel'}
          title="Toggle the tutor panel (f)"
          onClick={() => setPanelOpen((open) => !open)}
        >
          {panelOpen ? <PanelRightClose size={15} /> : <PanelRightOpen size={15} />}
        </Button>
      </div>

      {documentQuery.data?.status === 'partial' || documentQuery.data?.status === 'failed' ? (
        <div className="px-4 pt-3">
          <Callout
            tone={documentQuery.data.status === 'failed' ? 'danger' : 'caution'}
            icon={<AlertTriangle size={14} />}
            title={
              documentQuery.data.status === 'failed'
                ? 'This document could not be indexed'
                : 'This document was only partly indexed'
            }
          >
            {documentQuery.data.status_detail ||
              'Some pages produced no text, so the tutor cannot answer about them.'}
          </Callout>
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1">
        <div className="sp-scroll min-w-0 flex-1 overflow-y-auto bg-[var(--surface-sunken)] p-4">
          {page.isError ? (
            <ErrorPanel message={describeError(page.error)} onRetry={() => void page.refetch()} />
          ) : null}

          <div className="mx-auto max-w-5xl">
            {documentId ? (
              <PdfPageView
                documentId={documentId}
                pageNumber={pageNumber}
                page={page.data}
                highlights={highlights}
                zoom={zoom}
              />
            ) : null}

            {page.data ? (
              <div className="mt-3 flex flex-wrap items-center gap-1.5">
                {page.data.heading ? <Badge tone="accent">{page.data.heading}</Badge> : null}
                {page.data.has_equations ? (
                  <Badge tone="neutral" icon={<Sigma size={11} />}>
                    equations
                  </Badge>
                ) : null}
                {page.data.has_tables ? (
                  <Badge tone="neutral" icon={<TableIcon size={11} />}>
                    table
                  </Badge>
                ) : null}
                {page.data.has_figures ? <Badge tone="neutral">figure</Badge> : null}
                {!page.data.text.trim() ? (
                  <Badge tone="caution" icon={<AlertTriangle size={11} />}>
                    no extractable text on this page
                  </Badge>
                ) : null}
                {highlights.length > 0 ? (
                  <Button size="sm" variant="ghost" onClick={() => setHighlights([])}>
                    Clear highlight
                  </Button>
                ) : null}
              </div>
            ) : null}

            <p className="mt-3 flex flex-wrap items-center gap-2 text-[0.6875rem] text-[var(--text-muted)]">
              <span className="flex items-center gap-1">
                <KeyHint>←</KeyHint>
                <KeyHint>→</KeyHint> pages
              </span>
              <span className="flex items-center gap-1">
                <KeyHint>−</KeyHint>
                <KeyHint>+</KeyHint>
                <KeyHint>0</KeyHint> zoom
              </span>
              <span className="flex items-center gap-1">
                <KeyHint>f</KeyHint> focus mode
              </span>
            </p>
          </div>
        </div>

        {panelOpen ? (
          <>
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize the tutor panel"
              onMouseDown={(event) => {
                dragRef.current = { startX: event.clientX, startWidth: panelWidth };
                document.body.style.cursor = 'col-resize';
              }}
              className="w-1 shrink-0 cursor-col-resize bg-[var(--border-subtle)] transition-colors hover:bg-[var(--color-accent-400)]"
            />
            <div
              className="flex min-h-0 shrink-0 flex-col border-l border-[var(--border-subtle)] bg-[var(--surface-raised)]"
              style={{ width: `${panelWidth}%` }}
            >
              {showCheckpoint ? (
                <div className="border-b border-[var(--border-subtle)] p-3">
                  <Callout
                    tone="accent"
                    icon={<FileQuestion size={14} />}
                    title={`You have read ${pagesRead} pages`}
                    action={
                      <div className="flex gap-1.5">
                        <Button
                          size="sm"
                          variant="primary"
                          onClick={() =>
                            navigate(
                              `/quizzes?course=${documentQuery.data?.course_id ?? ''}` +
                                `&document=${documentId ?? ''}` +
                                `&from=${firstCheckpointPage}&to=${pageNumber}&auto=1`,
                            )
                          }
                        >
                          Quiz me
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            setCheckpointDismissed(true);
                            setPagesRead(0);
                          }}
                        >
                          Later
                        </Button>
                      </div>
                    }
                  >
                    A short quiz on pages {firstCheckpointPage}–{pageNumber} will tell you what
                    actually stuck.
                  </Callout>
                </div>
              ) : null}

              {session.isPending ? (
                <div className="flex flex-1 items-center justify-center gap-2 text-[0.8125rem] text-[var(--text-muted)]">
                  <Spinner size={12} /> Restoring your session…
                </div>
              ) : (
                <TutorPanel
                  className="flex-1"
                  sessionId={session.data?.id}
                  courseId={documentQuery.data?.course_id}
                  documentId={documentId}
                  pageNumber={pageNumber}
                  onCitationClick={onCitationClick}
                />
              )}
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
