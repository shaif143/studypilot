/**
 * Studying one slide.
 *
 * Two thirds of the screen is what was written for the slide: one or more
 * teaching slides that explain it, unpack its equations, draw what is clearer
 * drawn, and point at lecture videos. The other third is the lecturer's
 * original, so the explanation can always be checked against what was
 * actually shown. Along the bottom is a prompt for whatever is still unclear;
 * its answers come from the course material, with citations, and join the
 * explanation above.
 *
 * A slide is explained the first time it is opened and kept, so going back
 * to it is instant. Flicking past a slide does not write it: the request
 * waits for a short dwell.
 */
import type { Citation, ExplanationLevel, GuideCard, SlideGuide } from '@studypilot/shared';
import { Badge, Button, Callout, Select, Skeleton, Spinner, Textarea } from '@studypilot/ui';
import { useIsMutating } from '@tanstack/react-query';
import {
  AlertTriangle,
  BookOpen,
  ChartLine,
  ChevronLeft,
  ChevronRight,
  CornerDownLeft,
  ExternalLink,
  Library,
  Lightbulb,
  PencilLine,
  RefreshCw,
  Sigma,
  Sparkles,
  SquarePlay,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';

import { ErrorPanel } from '../components/AppShell';
import { GroundedAnswer } from '../components/GroundedAnswer';
import { PdfPageView } from '../components/PdfPageView';
import { TurnView } from '../components/TutorPanel';
import { useApp } from '../context';
import {
  describeError,
  slideGuideMutationKey,
  useAsk,
  useDocument,
  useMessages,
  usePage,
  useProviders,
  useResumeSession,
  useSettings,
  useSlideGuide,
  useWriteSlideGuide,
} from '../hooks/queries';

/** How long a slide must stay open before it is sent to be explained. */
const DWELL_MS = 600;

const KINDS: Record<string, { label: string; icon: React.ReactNode }> = {
  overview: { label: 'Overview', icon: <BookOpen size={11} /> },
  explanation: { label: 'Explanation', icon: <Lightbulb size={11} /> },
  equation: { label: 'Equation', icon: <Sigma size={11} /> },
  visual: { label: 'Visual', icon: <ChartLine size={11} /> },
  example: { label: 'Worked example', icon: <PencilLine size={11} /> },
  background: { label: 'Background', icon: <Library size={11} /> },
};

export function SlideStudyScreen() {
  const { documentId } = useParams<{ documentId: string }>();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { platform } = useApp();

  const documentQuery = useDocument(documentId);
  const settings = useSettings();
  const providers = useProviders();
  const session = useResumeSession(documentId);
  const messages = useMessages(session.data?.id);
  const ask = useAsk();

  const pageCount = documentQuery.data?.page_count ?? 0;
  const requested = Number.parseInt(params.get('page') ?? '', 10);
  const pageNumber = Math.max(
    1,
    Math.min(pageCount || Number.MAX_SAFE_INTEGER, Number.isFinite(requested) ? requested : 1),
  );

  const page = usePage(documentId, pageNumber);
  const guide = useSlideGuide(documentId, pageNumber);
  const write = useWriteSlideGuide(documentId, pageNumber);
  const writing =
    useIsMutating({ mutationKey: slideGuideMutationKey(documentId ?? '', pageNumber) }) > 0;

  // The two-part consent gate, exactly as the tutor applies it.
  const activeProvider = providers.data?.llm.find((p) => p.key === providers.data?.active_llm);
  const allowCloud =
    Boolean(settings.data?.allow_cloud_processing) &&
    Boolean(activeProvider?.sends_data_off_device);
  const consentKnown = Boolean(settings.data && providers.data);

  const [level, setLevel] = useState<ExplanationLevel | ''>('');
  const [draft, setDraft] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);

  const goToPage = useCallback(
    (next: number) => {
      const clamped = Math.max(1, Math.min(pageCount || next, next));
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

  // Explain a slide the first time it is opened — once, and only after a dwell.
  const attempted = useRef(new Set<string>());
  const nothingWritten = guide.data !== undefined && guide.data.guide === null;
  const { mutate: writeGuide } = write;
  useEffect(() => {
    if (!documentId || !consentKnown || !nothingWritten) return;
    const key = `${documentId}:${pageNumber}`;
    if (attempted.current.has(key)) return;
    const handle = window.setTimeout(() => {
      attempted.current.add(key);
      writeGuide({ allow_cloud: allowCloud });
    }, DWELL_MS);
    return () => window.clearTimeout(handle);
  }, [allowCloud, consentKnown, documentId, nothingWritten, pageNumber, writeGuide]);

  // Arrow keys turn slides, unless something is being typed.
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
      if (event.key === 'ArrowRight') {
        event.preventDefault();
        goToPage(pageNumber + 1);
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault();
        goToPage(pageNumber - 1);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [goToPage, pageNumber]);

  // The questions asked on this slide, not the whole deck's conversation.
  const turns = useMemo(
    () => (messages.data ?? []).filter((message) => message.meta?.page === pageNumber),
    [messages.data, pageNumber],
  );

  // A new slide starts at its top; a new answer is scrolled into view.
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [documentId, pageNumber]);
  const turnCount = turns.length;
  const asking = ask.isPending;
  useEffect(() => {
    const element = scrollRef.current;
    if (element && (turnCount > 0 || asking)) element.scrollTop = element.scrollHeight;
  }, [turnCount, asking]);

  const onCitationClick = useCallback(
    (citation: Citation) => {
      navigate(`/study/${citation.document_id || documentId}?page=${citation.page_number}`);
    },
    [documentId, navigate],
  );

  const submit = async () => {
    const question = draft.trim();
    if (!question || ask.isPending || !documentId) return;
    setDraft('');
    try {
      await ask.mutateAsync({
        question,
        session_id: session.data?.id ?? null,
        course_id: documentQuery.data?.course_id ?? null,
        document_id: documentId,
        page_number: pageNumber,
        mode: 'question',
        explanation_level: level || null,
        allow_cloud: allowCloud,
      });
    } catch {
      // Shown from ask.error; the question is put back to try again.
      setDraft(question);
    }
  };

  if (documentQuery.isError) {
    return (
      <div className="p-6">
        <ErrorPanel
          title="Could not open this lecture"
          message={describeError(documentQuery.error)}
          onRetry={() => void documentQuery.refetch()}
        />
        <Link to="/courses" className="mt-3 inline-block text-[0.8125rem] text-[var(--text-accent)]">
          Back to My Courses
        </Link>
      </div>
    );
  }

  const current = guide.data?.guide ?? null;
  const courseId = documentQuery.data?.course_id;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* ---- top bar ---------------------------------------------------- */}
      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border-subtle)] px-4 py-2">
        <Link
          to={courseId ? `/courses/${courseId}` : '/courses'}
          className="text-[0.75rem] font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
        >
          ← All slides
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
          {page.data?.heading ? (
            <p className="truncate text-[0.6875rem] text-[var(--text-secondary)]">
              {page.data.heading}
            </p>
          ) : null}
        </div>
        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            aria-label="Previous slide"
            disabled={pageNumber <= 1}
            onClick={() => goToPage(pageNumber - 1)}
          >
            <ChevronLeft size={15} />
          </Button>
          <Select
            aria-label="Slide"
            className="h-7 w-[5.75rem] text-[0.75rem]"
            value={pageNumber}
            onChange={(event) => goToPage(Number(event.target.value))}
          >
            {Array.from({ length: Math.max(pageCount, 1) }, (_, index) => (
              <option key={index + 1} value={index + 1}>
                Slide {index + 1}
              </option>
            ))}
          </Select>
          <span className="whitespace-nowrap text-[0.6875rem] tabular-nums text-[var(--text-muted)]">
            / {pageCount || '…'}
          </span>
          <Button
            size="sm"
            variant="ghost"
            aria-label="Next slide"
            disabled={pageCount > 0 && pageNumber >= pageCount}
            onClick={() => goToPage(pageNumber + 1)}
          >
            <ChevronRight size={15} />
          </Button>
        </div>
      </div>

      {/* ---- two thirds explanation, one third original ------------------ */}
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <section
          ref={scrollRef}
          aria-label="Explanation of this slide"
          className="sp-scroll order-2 min-h-0 flex-1 overflow-y-auto px-5 py-4 lg:order-1 lg:basis-2/3"
        >
          <div className="mx-auto max-w-3xl space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="flex items-center gap-1.5 text-[0.8125rem] font-semibold text-[var(--text-primary)]">
                <Sparkles size={14} className="text-[var(--text-accent)]" />
                Explained for you
              </h2>
              <span className="flex-1" />
              <div className="w-[11.5rem] shrink-0">
                <Select
                  aria-label="Explanation depth"
                  className="h-7 text-[0.75rem]"
                  value={level}
                  onChange={(event) => setLevel(event.target.value as ExplanationLevel | '')}
                >
                  <option value="">
                    Depth: {current?.level || settings.data?.explanation_level || 'intermediate'}
                  </option>
                  <option value="simple">Simple</option>
                  <option value="intermediate">Intermediate</option>
                  <option value="advanced">Advanced</option>
                </Select>
              </div>
              <Button
                size="sm"
                icon={<RefreshCw size={13} />}
                loading={writing}
                disabled={!documentId || !consentKnown}
                onClick={() =>
                  write.mutate({ allow_cloud: allowCloud, regenerate: true, level: level || null })
                }
              >
                Explain again
              </Button>
            </div>

            {guide.isError ? (
              <ErrorPanel message={describeError(guide.error)} onRetry={() => void guide.refetch()} />
            ) : null}
            {write.error ? (
              <ErrorPanel
                title="This slide could not be explained"
                message={describeError(write.error)}
                onRetry={() => write.mutate({ allow_cloud: allowCloud })}
              />
            ) : null}

            {current ? (
              <GuideView
                guide={current}
                rewriting={writing}
                openVideo={(url) => void platform.openExternal(url)}
              />
            ) : guide.isError || write.error ? null : (
              <WritingPlaceholder generative={Boolean(activeProvider?.generative) && allowCloud} />
            )}

            {turns.length > 0 || ask.isPending || ask.error ? (
              <div className="space-y-4 border-t border-[var(--border-subtle)] pt-4">
                <h2 className="text-[0.8125rem] font-semibold text-[var(--text-primary)]">
                  Your questions on this slide
                </h2>
                {turns.map((message) => (
                  <TurnView key={message.id} message={message} onCitationClick={onCitationClick} />
                ))}
                {ask.isPending ? (
                  <div className="flex items-center gap-2 text-[0.8125rem] text-[var(--text-secondary)]">
                    <Spinner size={12} /> Searching your material…
                  </div>
                ) : null}
                {ask.error ? (
                  <Callout tone="danger" icon={<AlertTriangle size={14} />} title="Could not answer">
                    {describeError(ask.error)}
                  </Callout>
                ) : null}
              </div>
            ) : null}
          </div>
        </section>

        <aside
          aria-label="Original slide"
          className="sp-scroll order-1 max-h-[36vh] shrink-0 overflow-y-auto border-b border-[var(--border-subtle)] bg-[var(--surface-sunken)] p-3 lg:order-2 lg:max-h-none lg:basis-1/3 lg:border-b-0 lg:border-l"
        >
          <p className="mb-2 flex items-center justify-between text-[0.6875rem] font-medium uppercase tracking-wide text-[var(--text-muted)]">
            Original slide
            <Link
              to={`/read/${documentId ?? ''}?page=${pageNumber}`}
              className="normal-case tracking-normal text-[var(--text-accent)] hover:underline"
            >
              Open in the reader
            </Link>
          </p>
          {documentId ? (
            <PdfPageView
              documentId={documentId}
              pageNumber={pageNumber}
              page={page.data}
              zoom={1}
            />
          ) : null}
          {page.data && !page.data.text.trim() ? (
            <Badge tone="caution" icon={<AlertTriangle size={11} />} className="mt-2">
              no extractable text on this slide
            </Badge>
          ) : null}
        </aside>
      </div>

      {/* ---- ask ---------------------------------------------------------- */}
      <div className="border-t border-[var(--border-subtle)] bg-[var(--surface-sunken)] px-4 py-3">
        <div className="mx-auto flex max-w-5xl items-end gap-2">
          <Textarea
            rows={1}
            value={draft}
            aria-label="Ask about this slide"
            placeholder="Ask me anything about this slide…"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                void submit();
              }
            }}
            className="min-h-[2.5rem] flex-1 bg-[var(--surface-raised)]"
          />
          <Button
            variant="primary"
            icon={<CornerDownLeft size={14} />}
            loading={ask.isPending}
            disabled={draft.trim().length === 0 || !documentId}
            onClick={() => void submit()}
          >
            Ask
          </Button>
        </div>
      </div>
    </div>
  );
}

function WritingPlaceholder({ generative }: { generative: boolean }) {
  return (
    <div className="space-y-3" aria-live="polite">
      <p className="flex items-center gap-2 text-[0.8125rem] text-[var(--text-secondary)]">
        <Spinner size={12} />
        {generative
          ? 'Writing the explanation for this slide. The first time takes up to a minute; after that it opens at once.'
          : 'Preparing this slide…'}
      </p>
      <Skeleton className="h-40" />
      <Skeleton className="h-28" />
    </div>
  );
}

function GuideView({
  guide,
  rewriting,
  openVideo,
}: {
  guide: SlideGuide;
  rewriting: boolean;
  openVideo: (url: string) => void;
}) {
  const written = guide.generated_by.startsWith('llm:');
  return (
    <div className={`space-y-4 ${rewriting ? 'opacity-60' : ''}`}>
      {guide.warnings.map((warning) => (
        <Callout key={warning} tone="caution" icon={<AlertTriangle size={14} />}>
          {warning}{' '}
          {!written ? (
            <Link to="/settings" className="underline">
              Open Settings
            </Link>
          ) : null}
        </Callout>
      ))}

      {guide.cards.map((card, index) => (
        <GuideCardView
          key={`${index}-${card.title}`}
          card={card}
          index={index}
          total={guide.cards.length}
        />
      ))}

      {guide.videos.length > 0 ? (
        <section className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4 shadow-[var(--shadow-card)]">
          <h3 className="flex items-center gap-1.5 text-[0.8125rem] font-semibold text-[var(--text-primary)]">
            <SquarePlay size={14} className="text-[var(--text-secondary)]" />
            Watch a lecture on this
          </h3>
          <p className="mt-0.5 text-[0.75rem] text-[var(--text-secondary)]">
            Each opens a YouTube search, so the results are real videos rather than a link an AI
            made up.
          </p>
          <ul className="mt-2 space-y-1">
            {guide.videos.map((video) => (
              <li key={video.url}>
                <button
                  type="button"
                  onClick={() => openVideo(video.url)}
                  className="flex w-full items-center gap-2 rounded-[var(--radius-control)] px-2 py-1.5 text-left text-[0.8125rem] text-[var(--text-accent)] transition-colors hover:bg-[var(--surface-hover)]"
                >
                  <ExternalLink size={13} className="shrink-0" />
                  <span className="min-w-0 flex-1 truncate">{video.title}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {written ? (
        <p className="text-[0.6875rem] text-[var(--text-muted)]">
          Written by {guide.model || guide.generated_by.slice(4)}
          {guide.saw_image ? ', which was shown the slide itself' : ' from the slide’s text'}. Check
          anything that matters against the original.
        </p>
      ) : null}
    </div>
  );
}

function GuideCardView({ card, index, total }: { card: GuideCard; index: number; total: number }) {
  const kind = KINDS[card.kind] ?? KINDS.explanation;
  return (
    <article className="overflow-hidden rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] shadow-[var(--shadow-card)]">
      <header className="flex flex-wrap items-center gap-2 border-b border-[var(--border-subtle)] bg-[var(--surface-sunken)] px-4 py-2">
        <Badge tone="accent" icon={kind?.icon}>
          {kind?.label}
        </Badge>
        {card.beyond_slide ? (
          <Badge
            tone="info"
            title="Added to help you understand the slide. It is not on the lecturer's slide."
          >
            Beyond the slide
          </Badge>
        ) : null}
        <span className="flex-1" />
        {total > 1 ? (
          <span className="text-[0.6875rem] tabular-nums text-[var(--text-muted)]">
            {index + 1} / {total}
          </span>
        ) : null}
      </header>
      <div className="px-5 py-4">
        <h3 className="text-base font-semibold tracking-[-0.01em] text-[var(--text-primary)]">
          {card.title}
        </h3>
        <GroundedAnswer className="mt-2" markdown={card.markdown} citations={[]} />
      </div>
    </article>
  );
}
