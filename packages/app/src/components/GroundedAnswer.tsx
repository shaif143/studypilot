/**
 * Renders a tutor answer with working citations.
 *
 * Inline `[n]` markers in the model's text are turned into clickable chips that
 * jump the reader to the cited page. A marker is only ever rendered as a chip
 * when the backend actually resolved it to a retrieved passage — the verify
 * step strips fabricated markers before they reach here, and anything that
 * still has no matching citation is left as plain text rather than rendered as
 * a link that goes nowhere.
 */
import type { Citation } from '@studypilot/shared';
import type { Root, Text } from 'mdast';
import { memo, useMemo } from 'react';
import Markdown from 'react-markdown';
import rehypeKatex from 'rehype-katex';
import remarkMath from 'remark-math';
import { visit } from 'unist-util-visit';

import { MermaidDiagram } from './MermaidDiagram';

const CITATION_PATTERN = /\[(\d{1,2})\]/g;
const CITE_PREFIX = '#sp-cite-';

/**
 * remark plugin: split text nodes on `[n]` and emit link nodes the renderer
 * below recognises. Done in the AST rather than with string replacement so a
 * marker inside a code block or a URL is left alone.
 */
function remarkCitations() {
  return (tree: Root) => {
    visit(tree, 'text', (node: Text, index, parent) => {
      if (!parent || index === undefined) return;
      if (parent.type === 'link' || parent.type === 'linkReference') return;

      const value = node.value;
      CITATION_PATTERN.lastIndex = 0;
      if (!CITATION_PATTERN.test(value)) return;
      CITATION_PATTERN.lastIndex = 0;

      const children: Array<Text | Record<string, unknown>> = [];
      let cursor = 0;
      let match: RegExpExecArray | null;
      while ((match = CITATION_PATTERN.exec(value)) !== null) {
        if (match.index > cursor) {
          children.push({ type: 'text', value: value.slice(cursor, match.index) } as Text);
        }
        children.push({
          type: 'link',
          url: `${CITE_PREFIX}${match[1]}`,
          children: [{ type: 'text', value: match[1] }],
        });
        cursor = match.index + match[0].length;
      }
      if (cursor < value.length) {
        children.push({ type: 'text', value: value.slice(cursor) } as Text);
      }
      parent.children.splice(index, 1, ...(children as Text[]));
      return index + children.length;
    });
  };
}

export interface GroundedAnswerProps {
  markdown: string;
  citations: Citation[];
  onCitationClick?: (citation: Citation) => void;
  className?: string;
}

export const GroundedAnswer = memo(function GroundedAnswer({
  markdown,
  citations,
  onCitationClick,
  className,
}: GroundedAnswerProps) {
  const byMarker = useMemo(
    () => new Map(citations.map((citation) => [citation.marker, citation])),
    [citations],
  );

  return (
    <div className={className ? `sp-prose ${className}` : 'sp-prose'}>
      <Markdown
        remarkPlugins={[remarkMath, remarkCitations]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false }]]}
        components={{
          code({ className: codeClass, children, ...rest }) {
            // A ```mermaid fence becomes a drawn diagram; every other code
            // block is left as code. Mermaid runs in strict mode (see the
            // component), so the model's text can only ever produce a picture.
            if (codeClass === 'language-mermaid') {
              return <MermaidDiagram code={String(children).trim()} title="Diagram" />;
            }
            return (
              <code className={codeClass} {...rest}>
                {children}
              </code>
            );
          },
          a({ href, children, ...rest }) {
            if (typeof href === 'string' && href.startsWith(CITE_PREFIX)) {
              const marker = Number.parseInt(href.slice(CITE_PREFIX.length), 10);
              const citation = byMarker.get(marker);
              if (!citation) {
                // No resolved source: show the number, not a dead link.
                return <span>[{marker}]</span>;
              }
              return (
                <button
                  type="button"
                  className="sp-citation"
                  title={
                    `Page ${citation.page_number}` +
                    (citation.section ? ` — ${citation.section}` : '') +
                    (citation.quote ? `\n\n"${citation.quote}"` : '')
                  }
                  onClick={() => onCitationClick?.(citation)}
                >
                  {marker}
                </button>
              );
            }
            // External links open in a new tab and cannot reach back into the app.
            return (
              <a href={href} target="_blank" rel="noreferrer noopener" {...rest}>
                {children}
              </a>
            );
          },
        }}
      >
        {markdown}
      </Markdown>
    </div>
  );
});
