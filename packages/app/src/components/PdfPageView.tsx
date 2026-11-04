/**
 * The PDF page canvas, with citation highlighting.
 *
 * Renders the real PDF with pdf.js rather than showing a server-side image, so
 * text stays selectable-sharp at any zoom and the page looks exactly as the
 * lecturer exported it. The server-rendered preview is kept as a fallback for
 * pages pdf.js cannot draw.
 *
 * Highlight geometry: the extractor stores bounding boxes in PDF points with a
 * top-left origin (PyMuPDF's convention), and a pdf.js viewport at scale `s`
 * maps those same points to CSS pixels by multiplying by `s`. Pages with a
 * non-zero crop-box origin or a rotation are therefore offset; such pages fall
 * back to no highlight rather than a wrong one.
 */
import type { PageDetail } from '@studypilot/shared';
import { Button, Spinner } from '@studypilot/ui';
import { AlertTriangle } from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { useClient } from '../context';

export interface HighlightBox {
  bbox: number[];
  /** Marker number shown on the highlight, if it came from a citation. */
  marker?: number;
}

export interface PdfPageViewProps {
  documentId: string;
  pageNumber: number;
  page: PageDetail | undefined;
  highlights?: HighlightBox[];
  zoom: number;
  onRendered?: (info: { width: number; height: number }) => void;
}

interface LoadedDocument {
  // pdf.js's PDFDocumentProxy; typed loosely to avoid coupling to its internals.
  doc: { numPages: number; getPage(pageNumber: number): Promise<unknown> };
  /**
   * The loading task owns the worker. `destroy()` lives here rather than on the
   * document proxy, and is what releases the worker thread.
   */
  task: { destroy(): Promise<void> };
  objectUrl: string;
}

export function PdfPageView({
  documentId,
  pageNumber,
  page,
  highlights = [],
  zoom,
  onRendered,
}: PdfPageViewProps) {
  const client = useClient();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const loadedRef = useRef<LoadedDocument | null>(null);
  const renderTaskRef = useRef<{ cancel(): void } | null>(null);

  const [status, setStatus] = useState<'loading' | 'ready' | 'fallback'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);
  const [containerWidth, setContainerWidth] = useState(0);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  // Track the available width so the page can be fitted to it.
  useLayoutEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setContainerWidth(entry.contentRect.width);
    });
    observer.observe(element);
    setContainerWidth(element.clientWidth);
    return () => observer.disconnect();
  }, []);

  // Reset load state when the document changes. Done during render — React's
  // documented pattern for adjusting state when a prop changes — so the
  // previous document's canvas is never shown under the new document's id.
  const [loadingDocumentId, setLoadingDocumentId] = useState<string | null>(null);
  if (loadingDocumentId !== documentId) {
    setLoadingDocumentId(documentId);
    setStatus('loading');
    setError(null);
  }

  // Load the PDF once per document.
  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const [{ getDocument, GlobalWorkerOptions }, workerModule] = await Promise.all([
          import('pdfjs-dist'),
          import('pdfjs-dist/build/pdf.worker.min.mjs?url'),
        ]);
        if (!GlobalWorkerOptions.workerSrc) {
          GlobalWorkerOptions.workerSrc = (workerModule as { default: string }).default;
        }
        // The file endpoint needs the API token, which <embed>/pdf.js cannot
        // attach, so it is fetched here and handed over as an object URL.
        const objectUrl = await client.fetchBlobUrl(`/api/documents/${documentId}/file`);
        if (cancelled) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        const task = getDocument({ url: objectUrl });
        const doc = await task.promise;
        if (cancelled) {
          URL.revokeObjectURL(objectUrl);
          void task.destroy();
          return;
        }
        loadedRef.current = {
          doc: doc as unknown as LoadedDocument['doc'],
          task,
          objectUrl,
        };
        setStatus('ready');
      } catch (cause) {
        if (cancelled) return;
        setError(cause instanceof Error ? cause.message : 'Could not open this PDF.');
        setStatus('fallback');
      }
    };

    void load();

    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel();
      renderTaskRef.current = null;
      const loaded = loadedRef.current;
      loadedRef.current = null;
      if (loaded) {
        // Destroying the task tears down the worker and the document together.
        void loaded.task.destroy();
        URL.revokeObjectURL(loaded.objectUrl);
      }
    };
  }, [client, documentId]);

  // Fall back to the server-rendered image when pdf.js cannot be used.
  useEffect(() => {
    if (status !== 'fallback') return;
    let cancelled = false;
    let created: string | null = null;
    client
      .fetchBlobUrl(`/api/documents/${documentId}/pages/${pageNumber}/preview`)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        created = url;
        setPreviewUrl(url);
      })
      .catch(() => setPreviewUrl(null));
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [client, documentId, pageNumber, status]);

  const render = useCallback(async () => {
    const loaded = loadedRef.current;
    const canvas = canvasRef.current;
    if (!loaded || !canvas || status !== 'ready' || containerWidth <= 0) return;
    if (pageNumber < 1 || pageNumber > loaded.doc.numPages) return;

    renderTaskRef.current?.cancel();

    try {
      const pdfPage = (await loaded.doc.getPage(pageNumber)) as {
        getViewport(options: { scale: number }): { width: number; height: number };
        render(options: unknown): { promise: Promise<void>; cancel(): void };
      };

      const unscaled = pdfPage.getViewport({ scale: 1 });
      // Fit to the container, then apply the user's zoom on top.
      const fitScale = (containerWidth - 2) / unscaled.width;
      const scale = Math.max(0.1, fitScale * zoom);
      const viewport = pdfPage.getViewport({ scale });

      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.floor(viewport.width * ratio);
      canvas.height = Math.floor(viewport.height * ratio);
      canvas.style.width = `${Math.floor(viewport.width)}px`;
      canvas.style.height = `${Math.floor(viewport.height)}px`;

      const context = canvas.getContext('2d');
      if (!context) return;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);

      const task = pdfPage.render({ canvasContext: context, viewport });
      renderTaskRef.current = task;
      await task.promise;
      renderTaskRef.current = null;

      const next = { width: Math.floor(viewport.width), height: Math.floor(viewport.height) };
      setSize(next);
      onRendered?.(next);
    } catch (cause) {
      // A cancelled render is normal when the page or zoom changes quickly.
      const name = (cause as { name?: string })?.name;
      if (name === 'RenderingCancelledException') return;
      setError(cause instanceof Error ? cause.message : 'Could not draw this page.');
      setStatus('fallback');
    }
  }, [containerWidth, onRendered, pageNumber, status, zoom]);

  useEffect(() => {
    void render();
  }, [render]);

  // Scale factor from PDF points to rendered CSS pixels, for the overlay.
  const highlightScale = size && page?.width ? size.width / page.width : null;

  return (
    <div ref={containerRef} className="relative w-full">
      {status === 'loading' ? (
        <div className="flex h-72 items-center justify-center gap-2 text-[var(--text-muted)]">
          <Spinner /> <span className="text-sm">Opening document…</span>
        </div>
      ) : null}

      {status === 'ready' ? (
        <div className="relative inline-block">
          <canvas
            ref={canvasRef}
            className="block rounded-[var(--radius-control)] border border-[var(--border-subtle)] bg-white shadow-[var(--shadow-card)]"
            aria-label={`Page ${pageNumber}${page?.heading ? `: ${page.heading}` : ''}`}
          />
          {highlightScale
            ? highlights.map((highlight, index) => {
                const [x0, y0, x1, y1] = highlight.bbox as [number, number, number, number];
                if (
                  !Number.isFinite(x0) ||
                  !Number.isFinite(y0) ||
                  !Number.isFinite(x1) ||
                  !Number.isFinite(y1)
                ) {
                  return null;
                }
                return (
                  <div
                    key={`${index}-${x0}-${y0}`}
                    aria-hidden
                    className="pointer-events-none absolute rounded-[3px] border-2 border-[var(--color-accent-500)] bg-[var(--color-accent-400)]/18"
                    style={{
                      left: x0 * highlightScale,
                      top: y0 * highlightScale,
                      width: Math.max(2, (x1 - x0) * highlightScale),
                      height: Math.max(2, (y1 - y0) * highlightScale),
                    }}
                  >
                    {highlight.marker ? (
                      <span className="absolute -left-2 -top-2.5 flex h-5 w-5 items-center justify-center rounded-full bg-[var(--color-accent-600)] text-[0.625rem] font-semibold text-white shadow">
                        {highlight.marker}
                      </span>
                    ) : null}
                  </div>
                );
              })
            : null}
        </div>
      ) : null}

      {status === 'fallback' ? (
        <div className="space-y-2">
          <div className="flex items-start gap-2 rounded-[var(--radius-control)] border border-[var(--signal-caution)]/25 bg-[var(--signal-caution-bg)] px-3 py-2 text-[0.8125rem] text-[var(--signal-caution)]">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            <div>
              <p className="font-medium">Showing a rendered image of this page.</p>
              <p className="mt-0.5">
                The PDF could not be drawn in the app
                {error ? `: ${error}` : ''}. Text selection and highlighting are unavailable.
              </p>
            </div>
          </div>
          {previewUrl ? (
            <img
              src={previewUrl}
              alt={`Page ${pageNumber}`}
              className="block w-full rounded-[var(--radius-control)] border border-[var(--border-subtle)] bg-white"
            />
          ) : (
            <div className="flex h-64 items-center justify-center text-sm text-[var(--text-muted)]">
              No preview is available for this page.
            </div>
          )}
        </div>
      ) : null}

      {status === 'ready' && error ? (
        <div className="mt-2 text-[0.75rem] text-[var(--signal-danger)]">
          {error} <Button size="sm" variant="ghost" onClick={() => void render()}>Retry</Button>
        </div>
      ) : null}
    </div>
  );
}
