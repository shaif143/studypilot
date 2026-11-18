/**
 * The reply being written, built up from the events the backend streams.
 *
 * Kept as a pure function of the events so the screen has nothing to get
 * wrong: text is appended to the paragraph in progress, a tool call appears
 * when Claude starts writing it and is filled in when it has run, and a
 * "reset" throws away the part of the reply that is being asked for again.
 */
import type { AssistantBlock, AssistantEvent, AssistantToolBlock } from '@studypilot/shared';

export interface LiveReply {
  blocks: AssistantBlock[];
  /** Claude is thinking and has not started the next piece of the answer. */
  thinking: boolean;
  /** The summary of that thinking so far; empty when none was sent. */
  thought: string;
  notices: string[];
  error: string | null;
  /** A tool ran, so courses, files or notes may have changed. */
  acted: boolean;
}

export const EMPTY_REPLY: LiveReply = {
  blocks: [],
  thinking: false,
  thought: '',
  notices: [],
  error: null,
  acted: false,
};

function withTool(
  blocks: AssistantBlock[],
  id: string,
  name: string,
  change: (tool: AssistantToolBlock) => AssistantToolBlock,
): AssistantBlock[] {
  const fresh: AssistantToolBlock = {
    type: 'tool',
    id,
    name,
    result: '',
    is_error: false,
    done: false,
  };
  const found = blocks.some((block) => block.type === 'tool' && block.id === id);
  if (!found) return [...blocks, change(fresh)];
  return blocks.map((block) => (block.type === 'tool' && block.id === id ? change(block) : block));
}

export function applyEvent(reply: LiveReply, event: AssistantEvent): LiveReply {
  switch (event.type) {
    case 'thinking':
      return { ...reply, thinking: true, thought: reply.thinking ? reply.thought + event.text : event.text };
    case 'text': {
      const last = reply.blocks[reply.blocks.length - 1];
      const blocks: AssistantBlock[] =
        last && last.type === 'text'
          ? [...reply.blocks.slice(0, -1), { type: 'text', text: last.text + event.text }]
          : [...reply.blocks, { type: 'text', text: event.text }];
      return { ...reply, blocks, thinking: false };
    }
    case 'tool_pending':
    case 'tool_start':
      return {
        ...reply,
        thinking: false,
        blocks: withTool(reply.blocks, event.id, event.name, (tool) => tool),
      };
    case 'tool_end':
      return {
        ...reply,
        acted: reply.acted || !event.is_error,
        blocks: withTool(reply.blocks, event.id, event.name, (tool) => ({
          ...tool,
          result: event.result,
          is_error: event.is_error,
          done: true,
        })),
      };
    case 'notice':
      return { ...reply, thinking: false, notices: [...reply.notices, event.text] };
    case 'reset': {
      // Everything after the last step that finished belongs to the request
      // being asked for again.
      let keep = 0;
      reply.blocks.forEach((block, index) => {
        if (block.type === 'tool' && block.done) keep = index + 1;
      });
      return { ...reply, blocks: reply.blocks.slice(0, keep), thinking: false, thought: '' };
    }
    case 'error':
      return { ...reply, thinking: false, error: event.message };
    case 'done':
      return { ...reply, thinking: false };
  }
}

const TOOL_LABELS: Record<string, [doing: string, done: string]> = {
  list_courses: ['Looking at your courses', 'Looked at your courses'],
  create_course: ['Creating a course', 'Created a course'],
  list_files: ['Looking at the files', 'Looked at the files'],
  create_folder: ['Creating a folder', 'Created a folder'],
  rename_folder: ['Renaming a folder', 'Renamed a folder'],
  delete_folder: ['Removing a folder', 'Removed a folder'],
  move_file: ['Moving a file', 'Moved a file'],
  rename_file: ['Renaming a file', 'Renamed a file'],
  save_attachment: ['Adding the file to a course', 'Added the file to a course'],
  search_materials: ['Searching your course material', 'Searched your course material'],
  read_file: ['Reading a file', 'Read a file'],
  write_note: ['Saving a note', 'Saved a note'],
  load_skill: ['Loading a skill', 'Loaded a skill'],
  read_skill_file: ['Reading a skill file', 'Read a skill file'],
};

/** A connector's tool, taken apart: `mcp__server__tool`. */
export function connectorTool(name: string): { server: string; tool: string } | null {
  if (!name.startsWith('mcp__')) return null;
  const rest = name.slice('mcp__'.length);
  const cut = rest.indexOf('__');
  if (cut < 0) return { server: rest, tool: '' };
  return { server: rest.slice(0, cut), tool: rest.slice(cut + 2) };
}

/** What a step is doing, in words, before its result is known. */
export function toolLabel(name: string, done: boolean): string {
  const connector = connectorTool(name);
  if (connector) {
    const what = connector.tool ? `${connector.server}: ${connector.tool}` : connector.server;
    return done ? `Used the connector ${what}` : `Using the connector ${what}`;
  }
  const labels = TOOL_LABELS[name];
  if (!labels) return done ? 'Finished a step' : 'Working';
  return done ? labels[1] : labels[0];
}

/** The line of a tool result that says what happened. */
export function resultSummary(result: string): string {
  return result.split('\n', 1)[0]?.trim() ?? '';
}

/** Whatever follows that line: the listing or text Claude was given. */
export function resultDetail(result: string): string {
  const cut = result.indexOf('\n');
  return cut < 0 ? '' : result.slice(cut + 1).trim();
}

/** The words of a reply, for copying: its text without the steps. */
export function replyText(blocks: AssistantBlock[]): string {
  return blocks
    .filter((block): block is { type: 'text'; text: string } => block.type === 'text')
    .map((block) => block.text.trim())
    .filter(Boolean)
    .join('\n\n');
}

export function formatTokens(count: number): string {
  if (count < 1000) return String(count);
  if (count < 1_000_000) return `${(count / 1000).toFixed(count < 10_000 ? 1 : 0)}k`;
  return `${(count / 1_000_000).toFixed(2)}M`;
}

export function formatCost(usd: number): string {
  if (usd <= 0) return '$0';
  if (usd < 0.01) return 'under $0.01';
  return `$${usd.toFixed(2)}`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** "Today", "Yesterday", "Earlier this week" and so on, for grouping recent chats. */
export function recencyGroup(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const day = 24 * 60 * 60 * 1000;
  const at = then.getTime();
  if (at >= startOfToday) return 'Today';
  if (at >= startOfToday - day) return 'Yesterday';
  if (at >= startOfToday - 7 * day) return 'Previous 7 days';
  if (at >= startOfToday - 30 * day) return 'Previous 30 days';
  return 'Older';
}

/**
 * The commands a half-typed "/name" could mean, best first.
 *
 * Offered only while the message is still just the command's name: once there
 * is a space, the student is writing what the command should work on.
 */
export function matchingCommands<T extends { name: string }>(text: string, commands: T[]): T[] {
  if (!text.startsWith('/') || /\s/.test(text)) return [];
  const typed = text.slice(1).toLowerCase();
  const short = (name: string) => name.split(':').pop() ?? name;
  return commands
    .filter((command) => {
      const name = command.name.toLowerCase();
      return name.startsWith(typed) || short(name).startsWith(typed);
    })
    .slice(0, 8);
}
