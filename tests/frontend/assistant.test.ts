/**
 * The Claude chat on the client side: how a streamed reply is put together,
 * and how the stream itself is read.
 */
import { StudyPilotClient } from '@studypilot/api-client';
import type { AssistantEvent, Platform } from '@studypilot/shared';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  EMPTY_REPLY,
  applyEvent,
  connectorTool,
  formatCost,
  formatTokens,
  matchingCommands,
  recencyGroup,
  replyText,
  resultDetail,
  resultSummary,
  toolLabel,
} from '../../packages/app/src/lib/assistant';

function play(events: AssistantEvent[]) {
  return events.reduce(applyEvent, EMPTY_REPLY);
}

describe('a reply as it streams', () => {
  it('joins text into one paragraph and shows thinking until words arrive', () => {
    const thinking = play([
      { type: 'thinking', text: '' },
      { type: 'thinking', text: 'Weighing it.' },
    ]);
    expect(thinking.thinking).toBe(true);
    expect(thinking.thought).toBe('Weighing it.');

    const reply = applyEvent(applyEvent(thinking, { type: 'text', text: 'Hello ' }), {
      type: 'text',
      text: 'there',
    });
    expect(reply.thinking).toBe(false);
    expect(reply.blocks).toEqual([{ type: 'text', text: 'Hello there' }]);
  });

  it('shows a step when it starts and fills it in when it has run', () => {
    const started = play([
      { type: 'text', text: 'Making it.' },
      { type: 'tool_pending', id: 't1', name: 'create_folder' },
      { type: 'tool_start', id: 't1', name: 'create_folder' },
    ]);
    expect(started.blocks).toHaveLength(2);
    expect(started.blocks[1]).toMatchObject({ type: 'tool', id: 't1', done: false });
    expect(started.acted).toBe(false);

    const finished = applyEvent(started, {
      type: 'tool_end',
      id: 't1',
      name: 'create_folder',
      result: 'Created the folder "Week 3".\n{"folder_id":"f1"}',
      is_error: false,
    });
    expect(finished.blocks[1]).toMatchObject({ done: true, is_error: false });
    expect(finished.acted).toBe(true);

    const after = applyEvent(finished, { type: 'text', text: 'Done.' });
    expect(after.blocks.map((block) => block.type)).toEqual(['text', 'tool', 'text']);
  });

  it('a failed step does not count as having changed anything', () => {
    const reply = play([
      { type: 'tool_start', id: 't1', name: 'move_file' },
      { type: 'tool_end', id: 't1', name: 'move_file', result: 'No file.', is_error: true },
    ]);
    expect(reply.acted).toBe(false);
  });

  it('a reset drops only what came after the last finished step', () => {
    const reply = play([
      { type: 'text', text: 'First.' },
      { type: 'tool_start', id: 't1', name: 'list_courses' },
      { type: 'tool_end', id: 't1', name: 'list_courses', result: 'Found 1.', is_error: false },
      { type: 'text', text: 'Half a sen' },
      { type: 'tool_pending', id: 't2', name: 'create_folder' },
      { type: 'reset' },
    ]);
    expect(reply.blocks.map((block) => block.type)).toEqual(['text', 'tool']);
    expect(play([{ type: 'text', text: 'declined' }, { type: 'reset' }]).blocks).toEqual([]);
  });

  it('keeps notices and the error for after the reply', () => {
    const reply = play([
      { type: 'notice', text: 'Stopped.' },
      { type: 'error', message: 'Anthropic is limiting this key.' },
    ]);
    expect(reply.notices).toEqual(['Stopped.']);
    expect(reply.error).toBe('Anthropic is limiting this key.');
  });
});

describe('what a step says', () => {
  it('splits a result into its headline and the rest', () => {
    const result = 'Found 2 courses.\n[{"course_id":"a"},{"course_id":"b"}]';
    expect(resultSummary(result)).toBe('Found 2 courses.');
    expect(resultDetail(result)).toBe('[{"course_id":"a"},{"course_id":"b"}]');
    expect(resultDetail('Renamed it.')).toBe('');
  });

  it('names a step while it runs, and any step it does not know', () => {
    expect(toolLabel('search_materials', false)).toBe('Searching your course material');
    expect(toolLabel('search_materials', true)).toBe('Searched your course material');
    expect(toolLabel('something_new', false)).toBe('Working');
  });

  it('copies the words of a reply without its steps', () => {
    expect(
      replyText([
        { type: 'text', text: 'One. ' },
        { type: 'tool', id: 't', name: 'x', result: 'r', is_error: false, done: true },
        { type: 'text', text: 'Two.' },
      ]),
    ).toBe('One.\n\nTwo.');
  });
});

describe('connectors and commands', () => {
  it('names a step taken through a connector by its server and tool', () => {
    expect(connectorTool('mcp__deep-wiki__ask_question')).toEqual({
      server: 'deep-wiki',
      tool: 'ask_question',
    });
    expect(connectorTool('create_folder')).toBeNull();
    expect(toolLabel('mcp__deep-wiki__ask_question', false)).toBe(
      'Using the connector deep-wiki: ask_question',
    );
    expect(toolLabel('mcp__deep-wiki__ask_question', true)).toBe(
      'Used the connector deep-wiki: ask_question',
    );
    expect(toolLabel('load_skill', true)).toBe('Loaded a skill');
  });

  it('offers commands only while the name is still being typed', () => {
    const commands = [
      { name: 'study-kit:quiz-me' },
      { name: 'study-kit:summarise' },
      { name: 'other:quick' },
    ];
    expect(matchingCommands('/', commands)).toHaveLength(3);
    expect(matchingCommands('/qu', commands).map((c) => c.name)).toEqual([
      'study-kit:quiz-me',
      'other:quick',
    ]);
    expect(matchingCommands('/study-kit:s', commands).map((c) => c.name)).toEqual([
      'study-kit:summarise',
    ]);
    expect(matchingCommands('/quiz-me backprop', commands)).toEqual([]);
    expect(matchingCommands('what is /quiz', commands)).toEqual([]);
  });
});

describe('small formats', () => {
  it('shortens token counts and never shows a cost as nothing when it is not', () => {
    expect(formatTokens(950)).toBe('950');
    expect(formatTokens(8500)).toBe('8.5k');
    expect(formatTokens(15_200)).toBe('15k');
    expect(formatCost(0)).toBe('$0');
    expect(formatCost(0.004)).toBe('under $0.01');
    expect(formatCost(0.257)).toBe('$0.26');
  });

  it('groups chats by how recent they are', () => {
    const now = new Date(2026, 10, 17, 15, 0, 0);
    const at = (daysAgo: number) =>
      new Date(2026, 10, 17 - daysAgo, 9, 0, 0).toISOString();
    expect(recencyGroup(at(0), now)).toBe('Today');
    expect(recencyGroup(at(1), now)).toBe('Yesterday');
    expect(recencyGroup(at(5), now)).toBe('Previous 7 days');
    expect(recencyGroup(at(20), now)).toBe('Previous 30 days');
    expect(recencyGroup(at(90), now)).toBe('Older');
  });
});

describe('reading the stream', () => {
  const platform: Platform = {
    kind: 'web',
    apiBaseUrl: '',
    hasSecureStorage: false,
    getApiToken: async () => null,
    openExternal: async () => {},
  };

  afterEach(() => vi.unstubAllGlobals());

  function streamOf(chunks: string[], status = 200): Response {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
        controller.close();
      },
    });
    return new Response(body, { status });
  }

  it('hands over each event, however the lines are cut into chunks', async () => {
    const lines = [
      '{"type":"text","text":"Hel',
      'lo"}\n{"type":"text","text":" wörld"}\n{"type":"no',
      'tice","text":"Stopped."}',
    ];
    vi.stubGlobal('fetch', () => Promise.resolve(streamOf(lines)));
    const seen: AssistantEvent[] = [];
    await new StudyPilotClient(platform).sendAssistantMessage(
      't1',
      { text: 'hi', attachment_ids: [], model: 'claude-opus-5-5' },
      (event) => seen.push(event),
    );
    expect(seen).toEqual([
      { type: 'text', text: 'Hello' },
      { type: 'text', text: ' wörld' },
      { type: 'notice', text: 'Stopped.' },
    ]);
  });

  it('turns a refusal before the stream into an error with its reason', async () => {
    vi.stubGlobal('fetch', () =>
      Promise.resolve(
        new Response(JSON.stringify({ detail: 'Save a Claude API key first.' }), { status: 409 }),
      ),
    );
    await expect(
      new StudyPilotClient(platform).sendAssistantMessage(
        't1',
        { text: 'hi', attachment_ids: [], model: 'claude-opus-5-5' },
        () => {},
      ),
    ).rejects.toMatchObject({ message: 'Save a Claude API key first.', status: 409 });
  });
});
