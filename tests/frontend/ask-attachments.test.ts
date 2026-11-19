/**
 * What can go with a question, decided before anything is read or sent.
 */
import { describe, expect, it } from 'vitest';

import {
  MAX_ASK_FILES,
  STUDY_COMMANDS,
  allCommands,
  attachmentOnlyQuestion,
  bytesToBase64,
  describeRefusals,
  encodeAttachments,
  expandCommand,
  issueAttachment,
  refusalFor,
  takeFiles,
} from '../../packages/app/src/lib/askAttachments';

const file = (name: string, type = '', body = 'x') => new File([body], name, { type });

describe('what a question can carry', () => {
  it('takes pictures, PDFs and text, by name or by type', () => {
    for (const ok of [
      file('working.jpg', 'image/jpeg'),
      file('paper.pdf', 'application/pdf'),
      file('notes.md'),
      file('solver.py'),
      file('pasted', 'image/png'),
      file('readme', 'text/plain'),
    ]) {
      expect(refusalFor(ok), ok.name).toBeNull();
    }
  });

  it('says why a file cannot go with a question', () => {
    expect(refusalFor(file('deck.pptx'))).toMatch(/add it to the course/);
    expect(refusalFor(file('photo.HEIC', 'image/heic'))).toMatch(/JPEG or PNG/);
    expect(refusalFor(file('empty.txt', 'text/plain', ''))).toMatch(/empty/);
  });

  it('drops a folder\'s housekeeping files without comment and names the rest', () => {
    const { attachments, refused } = takeFiles(
      [],
      [file('.DS_Store'), file('notes.md'), file('lecture.mp4', 'video/mp4')],
    );
    expect(attachments.map((item) => item.name)).toEqual(['notes.md']);
    expect(refused.map((item) => item.name)).toEqual(['lecture.mp4']);
    expect(describeRefusals(refused)).toMatch(/^lecture\.mp4 cannot be read/);
  });

  it('does not take the same file twice, or more than the limit', () => {
    const first = takeFiles([], [file('a.txt')]).attachments;
    expect(takeFiles(first, [file('a.txt')]).attachments).toHaveLength(1);

    const many = Array.from({ length: MAX_ASK_FILES + 2 }, (_, index) => file(`n${index}.txt`));
    const { attachments, refused } = takeFiles([], many);
    expect(attachments).toHaveLength(MAX_ASK_FILES);
    expect(refused).toHaveLength(2);
    expect(describeRefusals(refused)).toMatch(/1 more left out too/);
  });

  it('tells a picture from a file, for the chip', () => {
    const { attachments } = takeFiles([], [file('a.png', 'image/png'), file('b.pdf')]);
    expect(attachments.map((item) => item.kind)).toEqual(['image', 'file']);
  });

  it('encodes files and fetched text the same way', async () => {
    const issue = issueAttachment({ filename: 'widgets-12.md', text: 'Crash on résumé' });
    expect(issue.kind).toBe('issue');
    const { attachments } = takeFiles([issue], [file('a.txt', 'text/plain', 'hello')]);
    const sent = await encodeAttachments(attachments);
    expect(sent).toEqual([
      { filename: 'widgets-12.md', data: bytesToBase64(new TextEncoder().encode('Crash on résumé')) },
      { filename: 'a.txt', data: btoa('hello') },
    ]);
  });

  it('encodes more than one chunk of bytes without losing any', () => {
    const bytes = new Uint8Array(100_000).map((_, index) => index % 251);
    const back = Uint8Array.from(atob(bytesToBase64(bytes)), (char) => char.charCodeAt(0));
    expect(back).toEqual(bytes);
  });
});

describe('commands in a question box', () => {
  it('a built-in command typed by name becomes the question it stands for', () => {
    expect(expandCommand('/quiz', STUDY_COMMANDS, 'this slide')).toMatch(
      /^Ask me three short questions that test this slide/,
    );
    expect(expandCommand('/summary focus on padding', STUDY_COMMANDS, 'this slide')).toBe(
      'Summarise this slide in five bullet points. focus on padding',
    );
  });

  it('leaves a plugin\'s command, an unknown one and plain text as typed', () => {
    const commands = allCommands([{ name: 'lab:report', description: '', plugin: 'lab' }]);
    expect(expandCommand('/lab:report week 3', commands, 'this slide')).toBe('/lab:report week 3');
    expect(expandCommand('/nothing', commands, 'this slide')).toBe('/nothing');
    expect(expandCommand('  what is a/b testing?  ', commands, 'this slide')).toBe(
      'what is a/b testing?',
    );
  });

  it('a plugin\'s command wins a name it shares with a built-in one', () => {
    const commands = allCommands([{ name: 'quiz', description: 'mine', plugin: 'lab' }]);
    expect(commands.filter((command) => command.name === 'quiz')).toHaveLength(1);
    expect(expandCommand('/quiz', commands, 'this slide')).toBe('/quiz');
  });

  it('a question with only attachments still says something', () => {
    expect(attachmentOnlyQuestion('this slide')).toMatch(/attached/);
  });
});
