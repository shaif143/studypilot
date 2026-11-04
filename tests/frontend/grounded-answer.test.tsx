/**
 * Citation rendering.
 *
 * The rule under test is the one that keeps the UI honest: a `[n]` marker
 * becomes a clickable source only when it resolves to a passage that was
 * actually retrieved. An unresolved marker must render as plain text, never as
 * a link that leads nowhere.
 */
import { GroundedAnswer } from '@studypilot/app';
import type { Citation } from '@studypilot/shared';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

function citation(overrides: Partial<Citation> = {}): Citation {
  return {
    marker: 1,
    chunk_id: 'chunk-1',
    document_id: 'doc-1',
    document_title: 'Lecture 7',
    page_number: 4,
    section: 'Padding',
    bbox: [10, 20, 300, 60],
    score: 0.42,
    quote: 'Padding adds a border of extra values.',
    cited_inline: true,
    ...overrides,
  };
}

describe('GroundedAnswer', () => {
  it('renders a resolved marker as a clickable citation', async () => {
    const onCitationClick = vi.fn();
    render(
      <GroundedAnswer
        markdown="Padding keeps the feature map the same size [1]."
        citations={[citation()]}
        onCitationClick={onCitationClick}
      />,
    );

    const chip = screen.getByRole('button', { name: '1' });
    expect(chip).toHaveClass('sp-citation');
    expect(chip.title).toContain('Page 4');
    expect(chip.title).toContain('Padding');

    await userEvent.click(chip);
    expect(onCitationClick).toHaveBeenCalledWith(expect.objectContaining({ page_number: 4 }));
  });

  it('renders an unresolved marker as plain text, not a dead link', () => {
    render(
      <GroundedAnswer
        markdown="This claim cites a source that does not exist [9]."
        citations={[citation()]}
      />,
    );

    expect(screen.queryByRole('button', { name: '9' })).toBeNull();
    expect(screen.getByText(/\[9\]/)).toBeInTheDocument();
  });

  it('handles several markers in one sentence', async () => {
    const onCitationClick = vi.fn();
    render(
      <GroundedAnswer
        markdown="Padding [1] and stride [2] both change the output size."
        citations={[citation(), citation({ marker: 2, chunk_id: 'chunk-2', page_number: 5 })]}
        onCitationClick={onCitationClick}
      />,
    );

    expect(screen.getByRole('button', { name: '1' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '2' }));
    expect(onCitationClick).toHaveBeenCalledWith(expect.objectContaining({ page_number: 5 }));
  });

  it('leaves markdown links alone', () => {
    render(
      <GroundedAnswer
        markdown="See [the docs](https://example.com) for more."
        citations={[citation()]}
      />,
    );

    const link = screen.getByRole('link', { name: 'the docs' });
    expect(link).toHaveAttribute('href', 'https://example.com');
    // External links must not be able to reach back into the app.
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
    expect(link).toHaveAttribute('target', '_blank');
  });

  it('renders markdown structure and mathematics', () => {
    const { container } = render(
      <GroundedAnswer
        markdown={'## Padding\n\n- keeps size\n- treats edges fairly\n\n$W_{out} = W - K + 1$'}
        citations={[]}
      />,
    );

    expect(screen.getByRole('heading', { name: 'Padding' })).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(container.querySelector('.katex')).not.toBeNull();
  });

  it('does not turn a marker inside a code block into a citation', () => {
    render(
      <GroundedAnswer markdown={'```\narray[1]\n```'} citations={[citation()]} />,
    );
    expect(screen.queryByRole('button', { name: '1' })).toBeNull();
  });

  it('renders an empty answer without crashing', () => {
    const { container } = render(<GroundedAnswer markdown="" citations={[]} />);
    expect(container.querySelector('.sp-prose')).not.toBeNull();
  });
});
