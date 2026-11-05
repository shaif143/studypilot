/**
 * Mermaid fences in an answer become diagrams; other code stays code; a diagram
 * that does not parse falls back to its text. Mermaid itself is mocked: the
 * subject is the routing and the fallback, not Mermaid's renderer.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('mermaid', () => ({
  default: {
    initialize: vi.fn(),
    parse: vi.fn(async (code: string) => {
      if (code.includes('bogus')) throw new Error('Parse error on line 1');
      return true;
    }),
    render: vi.fn(async () => ({
      svg: '<svg data-rendered="yes"><g></g></svg>',
    })),
  },
}));

import { GroundedAnswer } from '@studypilot/app';
import { MermaidDiagram } from '../../packages/app/src/components/MermaidDiagram';

describe('MermaidDiagram', () => {
  it('draws a valid diagram as an SVG in strict mode', async () => {
    const { container } = render(<MermaidDiagram code="flowchart LR\n a --> b" title="Flow" />);
    await waitFor(() => expect(container.querySelector('svg')).not.toBeNull());
    const mermaid = (await import('mermaid')).default;
    expect(mermaid.initialize).toHaveBeenCalledWith(
      expect.objectContaining({ securityLevel: 'strict', startOnLoad: false }),
    );
    expect(container.querySelector('svg')?.getAttribute('aria-label')).toBe('Flow');
  });

  it('falls back to the text when the diagram does not parse', async () => {
    render(<MermaidDiagram code="flowchart LR\n bogus [[[" />);
    await waitFor(() => expect(screen.getByTestId('mermaid-fallback')).toBeInTheDocument());
    expect(screen.getByText(/could not be drawn/)).toBeInTheDocument();
    expect(screen.getByText(/bogus/)).toBeInTheDocument();
  });
});

describe('GroundedAnswer with diagrams', () => {
  it('routes a ```mermaid fence to the diagram and leaves other code alone', async () => {
    const markdown = [
      'Convolution feeds pooling [1].',
      '',
      '```mermaid',
      'flowchart LR',
      '  a[Convolution] --> b[Pooling]',
      '```',
      '',
      '```python',
      'print("hi")',
      '```',
    ].join('\n');
    const { container } = render(
      <GroundedAnswer
        markdown={markdown}
        citations={[
          {
            marker: 1,
            document_id: 'd',
            document_title: 'Lecture',
            page_number: 3,
            chunk_id: 'c',
            quote: '',
            section: '',
            score: 1,
          } as never,
        ]}
      />,
    );
    await waitFor(() => expect(container.querySelector('svg')).not.toBeNull());
    expect(screen.getByText('print("hi")')).toBeInTheDocument();
    expect(container.querySelectorAll('code.language-mermaid')).toHaveLength(0);
  });
});
