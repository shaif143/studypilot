/**
 * A deck's slides as a grid of thumbnails.
 *
 * The PDF is fetched once for the whole grid and each slide is drawn only
 * when it scrolls into view, so a sixty-slide deck costs one download and a
 * screenful of small canvases rather than sixty of either. A document read in
 * place (a linked folder, Google Drive) has no stored previews at all, which
 * is why these are drawn here from the file rather than fetched as images.
 * If the file cannot be opened the tiles still list every slide by number
 * and heading; only the pictures are missing.
 */
import type { PageSummary } from '@studypilot/shared';
import { Sparkles } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import { useClient } from '../context';

interface PdfPage {
  getViewport(options: { scale: number }): { width: number; height: number };
  render(options: { canvasContext: CanvasRenderingContext2D; viewport: unknown }): {
    promise: Promise<void>;
    cancel(): void;
  };
}

interface PdfDocument {
  numPages: number;
  getPage(pageNumber: number): Promise<PdfPage>;
}

type DeckState =
  | { documentId: string; status: 'ready'; doc: PdfDocument }
  | { documentId: string; status: 'failed' };

const THUMB_WIDTH = 220;

export function SlideThumbnails({
  documentId,
  pages,
  guided,
  hrefFor,
}: {
  documentId: string;
  pages: PageSummary[];
  /** Page numbers that already have teaching slides. */
  guided: ReadonlySet<number>;
  hrefFor: (pageNumber: number) => string;
}) {
  const client = useClient();
  // Keyed by the document it belongs to, so a new id reads as "loading"
  // without a synchronous reset inside the effect.
  const [deck, setDeck] = useState<DeckState | null>(null);
  const doc = deck && deck.documentId === documentId && deck.status === 'ready' ? deck.doc : null;

  useEffect(() => {
    let cancelled = false;
    let release: (() => void) | null = null;

    const load = async () => {
      try {
        const [{ getDocument, GlobalWorkerOptions }, workerModule] = await Promise.all([
          import('pdfjs-dist'),
          import('pdfjs-dist/build/pdf.worker.min.mjs?url'),
        ]);
        if (!GlobalWorkerOptions.workerSrc) {
          GlobalWorkerOptions.workerSrc = (workerModule as { default: string }).default;
        }
        const objectUrl = await client.fetchBlobUrl(`/api/documents/${documentId}/file`);
        const task = getDocument({ url: objectUrl });
        release = () => {
          void task.destroy();
          URL.revokeObjectURL(objectUrl);
        };
        const loaded = await task.promise;
        if (cancelled) {
          release();
          return;
        }
        setDeck({ documentId, status: 'ready', doc: loaded as unknown as PdfDocument });
      } catch {
        if (!cancelled) setDeck({ documentId, status: 'failed' });
      }
    };
    void load();

    return () => {
      cancelled = true;
      release?.();
    };
  }, [client, documentId]);

  return (
    <ul className="grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-3">
      {pages.map((page) => (
        <li key={page.page_number}>
          <Link
            to={hrefFor(page.page_number)}
            className="group block rounded-[var(--radius-control)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--border-focus)]"
          >
            <SlideThumb doc={doc} pageNumber={page.page_number} heading={page.heading} />
            <span className="mt-1.5 flex items-start gap-1.5">
              <span className="shrink-0 text-[0.75rem] tabular-nums text-[var(--text-muted)]">
                {page.page_number}
              </span>
              <span className="min-w-0 flex-1 truncate text-[0.75rem] text-[var(--text-secondary)] group-hover:text-[var(--text-primary)]">
                {page.heading || 'Untitled slide'}
              </span>
              {guided.has(page.page_number) ? (
                <Sparkles
                  size={12}
                  className="mt-0.5 shrink-0 text-[var(--text-accent)]"
                  aria-label="Already explained"
                />
              ) : null}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function SlideThumb({
  doc,
  pageNumber,
  heading,
}: {
  doc: PdfDocument | null;
  pageNumber: number;
  heading: string;
}) {
  const frameRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [visible, setVisible] = useState(false);
  const [drawn, setDrawn] = useState(false);

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame || visible) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setVisible(true);
      },
      { rootMargin: '300px' },
    );
    observer.observe(frame);
    return () => observer.disconnect();
  }, [visible]);

  useEffect(() => {
    if (!doc || !visible || pageNumber > doc.numPages) return;
    let cancelled = false;
    let task: { cancel(): void } | null = null;

    const draw = async () => {
      try {
        const page = await doc.getPage(pageNumber);
        const canvas = canvasRef.current;
        if (cancelled || !canvas) return;
        const base = page.getViewport({ scale: 1 });
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        const viewport = page.getViewport({ scale: (THUMB_WIDTH / base.width) * ratio });
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        const context = canvas.getContext('2d');
        if (!context) return;
        const rendering = page.render({ canvasContext: context, viewport });
        task = rendering;
        await rendering.promise;
        if (!cancelled) setDrawn(true);
      } catch {
        // A slide that will not draw keeps its numbered placeholder.
      }
    };
    void draw();

    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [doc, pageNumber, visible]);

  return (
    <div
      ref={frameRef}
      className="relative aspect-[4/3] overflow-hidden rounded-[var(--radius-control)] border border-[var(--border-subtle)] bg-[var(--surface-sunken)] shadow-[var(--shadow-card)] transition-shadow group-hover:border-[var(--color-accent-400)] group-hover:shadow-[var(--shadow-raised)]"
    >
      <canvas
        ref={canvasRef}
        aria-label={`Slide ${pageNumber}${heading ? `: ${heading}` : ''}`}
        className={`h-full w-full bg-white object-contain ${drawn ? '' : 'invisible'}`}
      />
      {!drawn ? (
        <span className="absolute inset-0 flex items-center justify-center text-lg font-semibold tabular-nums text-[var(--text-muted)]">
          {pageNumber}
        </span>
      ) : null}
    </div>
  );
}
