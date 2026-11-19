/**
 * What can ride along with a question: files, photos, a folder's worth of
 * them, a GitHub issue, and the commands that write a question for you.
 *
 * Everything here is decided from a file's name, type and size, before
 * anything is read or sent, so the student hears "that cannot be read with a
 * question" at once and not as a failed answer. The backend decides again
 * from the bytes.
 */
import type { AskAttachment } from '@studypilot/shared';

export const MAX_ASK_FILES = 10;
export const MAX_ASK_BYTES = 20 * 1024 * 1024;

const IMAGE = /\.(png|jpe?g|webp|gif|bmp|tiff?)$/i;
const TEXT =
  /\.(txt|md|markdown|csv|tsv|json|ya?ml|xml|html?|tex|bib|py|ipynb|jsx?|tsx?|java|c|h|cpp|hpp|cs|go|rs|rb|php|swift|kt|r|m|sql|sh|css|toml|ini|log|srt|vtt)$/i;

export type PendingKind = 'image' | 'file' | 'issue';

/** Something waiting to be sent with the next question. */
export interface PendingAttachment {
  key: string;
  name: string;
  size: number;
  kind: PendingKind;
  /** A picked, pasted or dropped file. */
  file?: File;
  /** Text that was fetched rather than picked: a GitHub issue. */
  text?: string;
}

export interface Refusal {
  name: string;
  reason: string;
}

/** Why a file cannot go with a question, or null when it can. */
export function refusalFor(file: { name: string; type: string; size: number }): string | null {
  if (file.size === 0) return 'is empty';
  if (/\.hei[cf]$/i.test(file.name) || /^image\/hei[cf]/i.test(file.type)) {
    return 'is a HEIC photo; export it as JPEG or PNG first';
  }
  const readable =
    IMAGE.test(file.name) ||
    TEXT.test(file.name) ||
    /\.pdf$/i.test(file.name) ||
    file.type === 'application/pdf' ||
    file.type.startsWith('text/') ||
    /^image\/(png|jpeg|webp|gif|bmp|tiff)$/i.test(file.type);
  if (!readable) {
    return 'cannot be read with a question; add it to the course instead, where it is indexed';
  }
  return null;
}

function kindOf(file: { name: string; type: string }): PendingKind {
  return IMAGE.test(file.name) || file.type.startsWith('image/') ? 'image' : 'file';
}

let counter = 0;
const nextKey = () => `a${Date.now().toString(36)}${(counter += 1)}`;

/**
 * Add picked files to what is already waiting.
 *
 * A folder brings its housekeeping files with it (`.DS_Store` and the like);
 * those are dropped without comment. Everything else that is left out is
 * named, with the reason.
 */
export function takeFiles(
  waiting: PendingAttachment[],
  incoming: File[],
): { attachments: PendingAttachment[]; refused: Refusal[] } {
  const attachments = [...waiting];
  const refused: Refusal[] = [];
  let bytes = waiting.reduce((sum, item) => sum + item.size, 0);
  for (const file of incoming) {
    if (file.name.startsWith('.') || file.name === 'Thumbs.db') continue;
    const reason = refusalFor(file);
    if (reason) {
      refused.push({ name: file.name, reason });
      continue;
    }
    if (attachments.some((item) => item.name === file.name && item.size === file.size)) continue;
    if (attachments.length >= MAX_ASK_FILES) {
      refused.push({
        name: file.name,
        reason: `is over the limit of ${MAX_ASK_FILES} files`,
      });
      continue;
    }
    if (bytes + file.size > MAX_ASK_BYTES) {
      refused.push({
        name: file.name,
        reason: 'would take the attachments past 20 MB',
      });
      continue;
    }
    bytes += file.size;
    attachments.push({
      key: nextKey(),
      name: file.name,
      size: file.size,
      kind: kindOf(file),
      file,
    });
  }
  return { attachments, refused };
}

/** A fetched GitHub issue, as something waiting to be sent. */
export function issueAttachment(issue: { filename: string; text: string }): PendingAttachment {
  return {
    key: nextKey(),
    name: issue.filename,
    size: new TextEncoder().encode(issue.text).length,
    kind: 'issue',
    text: issue.text,
  };
}

/** One sentence saying what was left out, for the line above the box. */
export function describeRefusals(refused: Refusal[]): string {
  if (refused.length === 0) return '';
  const [first, ...rest] = refused;
  if (!first) return '';
  const more = rest.length > 0 ? ` ${rest.length} more left out too.` : '';
  return `${first.name} ${first.reason}.${more}`;
}

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  const step = 0x8000;
  for (let index = 0; index < bytes.length; index += step) {
    binary += String.fromCharCode(...bytes.subarray(index, index + step));
  }
  return btoa(binary);
}

/** Read what is waiting into the shape the question is sent in. */
export async function encodeAttachments(waiting: PendingAttachment[]): Promise<AskAttachment[]> {
  return Promise.all(
    waiting.map(async (item) => {
      const bytes = item.file
        ? new Uint8Array(await item.file.arrayBuffer())
        : new TextEncoder().encode(item.text ?? '');
      return { filename: item.name, data: bytesToBase64(bytes) };
    }),
  );
}

/** What a question says when only attachments were sent. */
export function attachmentOnlyQuestion(subject: string): string {
  return `Look at what I attached and explain how it relates to ${subject}.`;
}

export interface StudyCommand {
  name: string;
  description: string;
  /** Where the command comes from: built in, or the plugin's name. */
  plugin?: string;
  /** Built-in commands write the question themselves. */
  prompt?: (subject: string) => string;
}

/**
 * The commands every question box has. Each one writes a question the
 * student could have typed; choosing it puts that question in the box to
 * send or change.
 */
export const STUDY_COMMANDS: StudyCommand[] = [
  {
    name: 'explain',
    description: 'Step by step, from the beginning',
    prompt: (subject) =>
      `Explain ${subject} step by step, as if I am seeing it for the first time.`,
  },
  {
    name: 'simplify',
    description: 'The simplest words, with an everyday comparison',
    prompt: (subject) =>
      `Explain ${subject} in the simplest words you can, with an everyday comparison.`,
  },
  {
    name: 'terms',
    description: 'Every term and symbol, defined in a table',
    prompt: (subject) =>
      `Define every term and symbol in ${subject} in a table. For a symbol, give its name first.`,
  },
  {
    name: 'example',
    description: 'A worked example',
    prompt: (subject) => `Give me a worked example that uses what ${subject} teaches.`,
  },
  {
    name: 'quiz',
    description: 'Three questions to test yourself',
    prompt: (subject) =>
      `Ask me three short questions that test ${subject}, then give the answers below them.`,
  },
  {
    name: 'summary',
    description: 'Five bullet points',
    prompt: (subject) => `Summarise ${subject} in five bullet points.`,
  },
  {
    name: 'exam',
    description: 'What an exam would ask, and how to answer',
    prompt: (subject) =>
      `What would an exam most likely ask about ${subject}, and how should I answer it?`,
  },
];

/** Built-in commands, then the installed plugins' commands that do not share a name with one. */
export function allCommands(
  plugins: Array<{ name: string; description: string; plugin: string }>,
): StudyCommand[] {
  const taken = new Set(plugins.map((command) => command.name.toLowerCase()));
  // A plugin's command wins a shared name: the student installed it on purpose.
  return [...STUDY_COMMANDS.filter((command) => !taken.has(command.name)), ...plugins];
}

/**
 * The question to send for what was typed.
 *
 * A built-in command typed by name becomes the question it stands for, with
 * anything written after it kept. A plugin's command is sent as typed: the
 * backend holds the plugin and knows what the command means.
 */
export function expandCommand(text: string, commands: StudyCommand[], subject: string): string {
  const match = /^\/([\w:-]+)(?:\s+([\s\S]*))?$/.exec(text.trim());
  if (!match) return text.trim();
  const command = commands.find((item) => item.name.toLowerCase() === match[1]?.toLowerCase());
  if (!command?.prompt) return text.trim();
  const rest = (match[2] ?? '').trim();
  return rest ? `${command.prompt(subject)} ${rest}` : command.prompt(subject);
}
