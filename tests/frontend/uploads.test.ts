/**
 * Folder uploads: what is taken, what is left, and that nothing disappears
 * silently.
 */
import { describe, expect, it } from 'vitest';

import { partitionFiles, relativePathOf, titleFor } from '../../packages/app/src/lib/uploads';

function file(name: string, type = '', size = 10, path?: string): File {
  const made = new File([new Uint8Array(size)], name, { type });
  if (path) Object.defineProperty(made, 'webkitRelativePath', { value: path });
  return made;
}

describe('partitionFiles', () => {
  it('takes the supported files from a folder and reports the rest with a reason', () => {
    const { accepted, rejected } = partitionFiles([
      file('week-1.pdf', 'application/pdf', 10, 'Lectures/week-1.pdf'),
      file('week-2.pptx', '', 10, 'Lectures/week-2.pptx'),
      file('.DS_Store', '', 10, 'Lectures/.DS_Store'),
      file('notes.docx', '', 10, 'Lectures/notes.docx'),
      file('old.ppt', '', 10, 'Lectures/old.ppt'),
      file('empty.pdf', 'application/pdf', 0, 'Lectures/empty.pdf'),
      file('figure.png', 'image/png', 10, 'Lectures/figures/figure.png'),
    ]);
    expect(accepted.map(relativePathOf)).toEqual([
      'Lectures/figures/figure.png',
      'Lectures/old.ppt',
      'Lectures/week-1.pdf',
      'Lectures/week-2.pptx',
    ]);
    expect(rejected.map((r) => [r.file.name, r.reason])).toEqual([
      ['notes.docx', 'Not a supported type.'],
      ['empty.pdf', 'Empty file.'],
    ]);
  });

  it('drops a file chosen twice and keeps the same name in different folders', () => {
    const { accepted } = partitionFiles([
      file('a.pdf', 'application/pdf', 10, 'x/a.pdf'),
      file('a.pdf', 'application/pdf', 10, 'x/a.pdf'),
      file('a.pdf', 'application/pdf', 10, 'y/a.pdf'),
    ]);
    expect(accepted.map(relativePathOf)).toEqual(['x/a.pdf', 'y/a.pdf']);
  });

  it('judges a file with no declared type by its name', () => {
    const { accepted, rejected } = partitionFiles([file('deck.pptx'), file('deck.key')]);
    expect(accepted.map((f) => f.name)).toEqual(['deck.pptx']);
    expect(rejected.map((f) => f.file.name)).toEqual(['deck.key']);
  });
});

describe('titleFor', () => {
  it('strips one extension and nothing else', () => {
    expect(titleFor(file('Week 3 - Pooling.v2.pdf'))).toBe('Week 3 - Pooling.v2');
    expect(titleFor(file('README'))).toBe('README');
  });
});
