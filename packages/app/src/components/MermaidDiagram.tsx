/**
 * Renders Mermaid text as an SVG.
 *
 * Mermaid is loaded on first use (it is a large library and most sessions
 * never draw a diagram) and runs with `securityLevel: 'strict'`, so a diagram
 * is a picture: no click handlers, no HTML labels, no scripts. The text comes
 * either from code that built it out of stored topics (the concept map) or
 * from a model's answer; in both cases a diagram that does not parse falls
 * back to the text itself with a plain message, never to a broken page.
 */
import { useEffect, useId, useRef, useState } from 'react';

type MermaidModule = (typeof import('mermaid'))['default'];

let loader: Promise<MermaidModule> | null = null;

function loadMermaid(): Promise<MermaidModule> {
  if (!loader) {
    loader = import('mermaid').then((module) => {
      const mermaid = module.default;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'base',
        fontFamily: 'inherit',
        themeVariables: {
          primaryColor: '#eef0f3',
          primaryBorderColor: '#8a94a6',
          primaryTextColor: '#1f2937',
          lineColor: '#64748b',
          fontSize: '14px',
        },
        // Natural size, scrolled by the container: a nine-topic chain squeezed
        // to the card's width is unreadable.
        flowchart: { useMaxWidth: false, htmlLabels: false, curve: 'basis' },
      });
      return mermaid;
    });
  }
  return loader;
}

export interface MermaidDiagramProps {
  code: string;
  /** Accessible description of what the diagram shows. */
  title?: string;
  className?: string;
}

interface RenderOutcome {
  code: string;
  status: 'ready' | 'failed';
  error: string;
}

export function MermaidDiagram({ code, title, className }: MermaidDiagramProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  // Keyed by the code it describes, so a new `code` is "loading" without a
  // synchronous state reset inside the effect.
  const [outcome, setOutcome] = useState<RenderOutcome | null>(null);
  const reactId = useId().replace(/[^a-zA-Z0-9]/g, '');
  const state = outcome && outcome.code === code ? outcome.status : 'loading';
  const error = outcome && outcome.code === code ? outcome.error : '';

  useEffect(() => {
    let cancelled = false;
    loadMermaid()
      .then(async (mermaid) => {
        // parse() throws on bad syntax; render() would otherwise inject an error SVG.
        await mermaid.parse(code);
        const { svg } = await mermaid.render(`mermaid-${reactId}`, code);
        if (cancelled || !containerRef.current) return;
        containerRef.current.innerHTML = svg;
        const svgElement = containerRef.current.querySelector('svg');
        if (svgElement) {
          svgElement.setAttribute('role', 'img');
          if (title) svgElement.setAttribute('aria-label', title);
        }
        setOutcome({ code, status: 'ready', error: '' });
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setOutcome({
          code,
          status: 'failed',
          error: cause instanceof Error ? cause.message : String(cause),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [code, reactId, title]);

  if (state === 'failed') {
    return (
      <div className={className} data-testid="mermaid-fallback">
        <p className="text-xs text-[var(--text-muted)]">
          This diagram could not be drawn, so here is its description instead.
          {error ? ` (${error.split('\n')[0]})` : ''}
        </p>
        <pre className="mt-1 overflow-x-auto rounded-md bg-[var(--surface-sunken)] p-2 text-xs">
          {code}
        </pre>
      </div>
    );
  }

  return (
    <div className={className}>
      {state === 'loading' ? (
        <p className="text-xs text-[var(--text-muted)]" data-testid="mermaid-loading">
          Drawing the diagram…
        </p>
      ) : null}
      <div ref={containerRef} className="overflow-x-auto" data-testid="mermaid-diagram" />
    </div>
  );
}
