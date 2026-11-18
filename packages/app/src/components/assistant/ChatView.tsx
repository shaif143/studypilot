/**
 * One conversation with Claude: what has been said, the reply being written,
 * and the box a message is typed into.
 *
 * A new chat has no conversation behind it until something is sent or
 * attached; it is created at that moment, and the address changes to the new
 * conversation's without this component being rebuilt, so a half-typed
 * message and its attachments survive the change.
 */
import type {
  AssistantFile,
  AssistantItem,
  AssistantStatus,
  AssistantThread,
} from '@studypilot/shared';
import { Spinner } from '@studypilot/ui';
import { useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowUp,
  Check,
  Copy,
  FileText,
  FolderKanban,
  Paperclip,
  Square,
  X,
} from 'lucide-react';
import {
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { Link } from 'react-router-dom';

import { useClient } from '../../context';
import {
  describeError,
  keys,
  useAssistantExtensions,
  useAssistantProject,
  useAssistantThread,
} from '../../hooks/queries';
import {
  EMPTY_REPLY,
  type LiveReply,
  applyEvent,
  connectorTool,
  formatBytes,
  formatCost,
  formatTokens,
  matchingCommands,
  replyText,
  resultDetail,
  resultSummary,
  toolLabel,
} from '../../lib/assistant';
import { GroundedAnswer } from '../GroundedAnswer';

const MODEL_KEY = 'studypilot.assistant.model';
const MAX_FILES = 10;

interface PendingFile {
  key: string;
  name: string;
  size: number;
  state: 'uploading' | 'ready' | 'failed';
  remote?: AssistantFile;
  error?: string;
}

function rememberedModel(status: AssistantStatus): string {
  try {
    const stored = window.localStorage.getItem(MODEL_KEY);
    if (stored && status.models.some((model) => model.id === stored)) return stored;
  } catch {
    // Not remembered; the default is used.
  }
  return status.default_model;
}

export function ChatView({
  routeThreadId,
  projectId,
  status,
  setup,
  onCreated,
}: {
  routeThreadId: string | null;
  /** The project a new chat is started in, when it is started from one. */
  projectId: string | null;
  status: AssistantStatus;
  /** Shown in place of the message box until the chat can be used. */
  setup: ReactNode;
  onCreated: (threadId: string) => void;
}) {
  const client = useClient();
  const queryClient = useQueryClient();
  const [threadId, setThreadId] = useState<string | null>(routeThreadId);
  const thread = useAssistantThread(threadId);
  const inProject = thread.data?.project_id ?? (threadId ? null : projectId);
  const project = useAssistantProject(inProject);

  const extensions = useAssistantExtensions();
  const [text, setText] = useState('');
  const [files, setFiles] = useState<PendingFile[]>([]);
  const [model, setModel] = useState(() => rememberedModel(status));
  const [live, setLive] = useState<LiveReply | null>(null);
  const [pendingUser, setPendingUser] = useState<AssistantItem | null>(null);
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  /** What the last reply ended with, kept on screen until the next message. */
  const [afterword, setAfterword] = useState<{ notices: string[]; error: string | null }>({
    notices: [],
    error: null,
  });
  const [dragging, setDragging] = useState(false);

  const liveRef = useRef<LiveReply>(EMPTY_REPLY);
  const abortRef = useRef<AbortController | null>(null);
  const creatingRef = useRef<Promise<string> | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // An existing chat opens on the model it last used.
  const adoptedModel = useRef(false);
  useEffect(() => {
    if (adoptedModel.current || !routeThreadId || !thread.data) return;
    adoptedModel.current = true;
    if (status.models.some((item) => item.id === thread.data.model)) setModel(thread.data.model);
  }, [routeThreadId, thread.data, status.models]);

  const chooseModel = (next: string) => {
    setModel(next);
    try {
      window.localStorage.setItem(MODEL_KEY, next);
    } catch {
      // Used for this chat all the same.
    }
  };

  const ensureThread = useCallback(async (): Promise<string> => {
    if (threadId) return threadId;
    // Two files dropped at once must not make two conversations.
    creatingRef.current ??= client
      .createAssistantThread({ project_id: projectId, model })
      .then((created: AssistantThread) => {
        queryClient.setQueryData(keys.assistantThread(created.id), created);
        setThreadId(created.id);
        onCreated(created.id);
        return created.id;
      })
      .finally(() => {
        creatingRef.current = null;
      });
    return creatingRef.current;
  }, [client, model, onCreated, projectId, queryClient, threadId]);

  // --- attachments -----------------------------------------------------
  const addFiles = useCallback(
    async (picked: File[]) => {
      const room = MAX_FILES - files.length;
      const accepted = picked.slice(0, Math.max(0, room));
      if (accepted.length === 0) return;
      const entries: PendingFile[] = accepted.map((file) => ({
        key: `${file.name}-${file.size}-${Math.random().toString(36).slice(2)}`,
        name: file.name || 'pasted image',
        size: file.size,
        state: 'uploading',
      }));
      setFiles((current) => [...current, ...entries]);
      const update = (key: string, change: Partial<PendingFile>) =>
        setFiles((current) =>
          current.map((entry) => (entry.key === key ? { ...entry, ...change } : entry)),
        );
      let id: string;
      try {
        id = await ensureThread();
      } catch (error) {
        entries.forEach((entry) =>
          update(entry.key, { state: 'failed', error: describeError(error) }),
        );
        return;
      }
      await Promise.all(
        accepted.map(async (file, index) => {
          const entry = entries[index];
          if (!entry) return;
          try {
            const remote = await client.addAssistantAttachment(id, file);
            update(entry.key, { state: 'ready', remote, name: remote.filename });
          } catch (error) {
            update(entry.key, { state: 'failed', error: describeError(error) });
          }
        }),
      );
    },
    [client, ensureThread, files.length],
  );

  const removeFile = (entry: PendingFile) => {
    setFiles((current) => current.filter((item) => item.key !== entry.key));
    if (entry.remote) void client.deleteAssistantAttachment(entry.remote.id).catch(() => undefined);
  };

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const pasted = Array.from(event.clipboardData.files);
    if (pasted.length === 0) return;
    event.preventDefault();
    void addFiles(pasted);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (!status.ready) return;
    void addFiles(Array.from(event.dataTransfer.files));
  };

  // --- sending ---------------------------------------------------------
  const uploading = files.some((entry) => entry.state === 'uploading');
  const ready = files.filter((entry) => entry.state === 'ready' && entry.remote);
  const canSend = status.ready && !sending && !uploading && (text.trim() !== '' || ready.length > 0);

  const send = async () => {
    if (!canSend) return;
    const message = text.trim();
    const attached = ready.map((entry) => entry.remote as AssistantFile);
    const kept = files;
    setAfterword({ notices: [], error: null });
    setSending(true);
    setStopping(false);
    setText('');
    setFiles([]);
    setPendingUser({ role: 'user', text: message, attachments: attached, blocks: [] });
    liveRef.current = EMPTY_REPLY;
    setLive(EMPTY_REPLY);
    stickRef.current = true;

    let id: string | null = null;
    let finished = false;
    let listed = false;
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      id = await ensureThread();
      const target = id;
      await client.sendAssistantMessage(
        target,
        { text: message, attachment_ids: attached.map((file) => file.id), model },
        (event) => {
          if (!listed) {
            // The chat has its name as soon as the message is taken: list it.
            listed = true;
            void queryClient.invalidateQueries({ queryKey: keys.assistantThreads });
          }
          const next = applyEvent(liveRef.current, event);
          liveRef.current = next;
          if (event.type === 'done') {
            finished = true;
            queryClient.setQueryData(keys.assistantThread(target), event.thread);
            setAfterword({ notices: next.notices, error: next.error });
            setPendingUser(null);
            setLive(null);
          } else {
            setLive(next);
          }
        },
        controller.signal,
      );
    } catch (error) {
      const aborted = error instanceof DOMException && error.name === 'AbortError';
      if (!aborted) {
        setAfterword({ notices: [], error: describeError(error) });
        // Nothing was sent, so give the message back to be sent again.
        if (!finished && liveRef.current.blocks.length === 0) {
          setText(message);
          setFiles(kept);
        }
      }
    } finally {
      abortRef.current = null;
      setSending(false);
      setStopping(false);
      setPendingUser(null);
      setLive(null);
      if (id && !finished) {
        // The stream ended without its last line: read what was kept.
        void queryClient.invalidateQueries({ queryKey: keys.assistantThread(id) });
      }
      void queryClient.invalidateQueries({ queryKey: keys.assistantThreads });
      void queryClient.invalidateQueries({ queryKey: keys.assistantProjects });
      if (liveRef.current.acted) {
        // A step changed courses, files or notes; every other screen re-reads.
        void queryClient.invalidateQueries({
          predicate: (query) => query.queryKey[0] !== 'assistant',
        });
      }
      inputRef.current?.focus();
    }
  };

  const stop = () => {
    if (!threadId || stopping) return;
    setStopping(true);
    void client.stopAssistant(threadId).catch(() => undefined);
    // The backend stops at the next thing Claude sends. If nothing comes for
    // a while, stop listening anyway; what was finished is already kept.
    window.setTimeout(() => abortRef.current?.abort(), 5000);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void send();
    }
  };

  // --- layout ----------------------------------------------------------
  const items = thread.data?.items ?? [];
  const shown: AssistantItem[] = pendingUser ? [...items, pendingUser] : items;
  const empty = shown.length === 0 && !live;

  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
  }, [text]);

  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (element && stickRef.current) element.scrollTop = element.scrollHeight;
  }, [shown.length, live, afterword]);

  const onScroll = () => {
    const element = scrollRef.current;
    if (!element) return;
    stickRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
  };

  // Until the first reply is in, the chat is named by what was just asked.
  const savedTitle = thread.data?.title;
  const heading =
    savedTitle && savedTitle !== 'New chat'
      ? savedTitle
      : pendingUser?.text.split('\n', 1)[0]?.slice(0, 60) || savedTitle || 'New chat';

  const suggestions = matchingCommands(text, extensions.data?.commands ?? []);

  const usage = thread.data?.usage;
  const composer = status.ready ? (
    <div
      className={[
        'rounded-2xl border bg-[var(--surface-raised)] shadow-[var(--shadow-card)] transition-colors',
        dragging ? 'border-[var(--border-focus)]' : 'border-[var(--border-strong)]',
      ].join(' ')}
    >
      {files.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5 px-3 pt-3">
          {files.map((entry) => (
            <li
              key={entry.key}
              className={[
                'flex max-w-[16rem] items-center gap-1.5 rounded-lg border px-2 py-1 text-[0.75rem]',
                entry.state === 'failed'
                  ? 'border-[var(--signal-danger)]/40 text-[var(--signal-danger)]'
                  : 'border-[var(--border-subtle)] text-[var(--text-secondary)]',
              ].join(' ')}
              title={entry.error ?? entry.name}
            >
              {entry.state === 'uploading' ? <Spinner size={11} /> : <FileText size={12} />}
              <span className="truncate">{entry.state === 'failed' ? entry.error : entry.name}</span>
              <button
                type="button"
                onClick={() => removeFile(entry)}
                aria-label={`Remove ${entry.name}`}
                className="shrink-0 rounded p-0.5 hover:bg-[var(--surface-hover)]"
              >
                <X size={11} />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {suggestions.length > 0 ? (
        <ul
          aria-label="Commands"
          className="mx-3 mt-3 overflow-hidden rounded-lg border border-[var(--border-subtle)]"
        >
          {suggestions.map((command) => (
            <li key={command.name}>
              <button
                type="button"
                onClick={() => {
                  setText(`/${command.name} `);
                  inputRef.current?.focus();
                }}
                className="flex w-full items-baseline gap-2 px-3 py-1.5 text-left text-[0.8125rem] hover:bg-[var(--surface-hover)]"
              >
                <span className="shrink-0 font-[family-name:var(--font-mono)] text-[var(--text-primary)]">
                  /{command.name}
                </span>
                <span className="min-w-0 truncate text-[var(--text-muted)]">
                  {command.description || command.plugin}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <textarea
        ref={inputRef}
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        rows={1}
        placeholder={empty ? 'How can Claude help you today?' : 'Reply to Claude'}
        aria-label="Message"
        className="block w-full resize-none bg-transparent px-4 pb-1 pt-3 text-[0.9375rem] leading-relaxed text-[var(--text-primary)] outline-none placeholder:text-[var(--text-muted)]"
      />
      <div className="flex items-center gap-2 px-2.5 pb-2.5 pt-1">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          hidden
          onChange={(event) => {
            void addFiles(Array.from(event.target.files ?? []));
            event.target.value = '';
          }}
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={files.length >= MAX_FILES}
          aria-label="Attach files or photos"
          title="Attach files or photos"
          className="flex h-8 w-8 items-center justify-center rounded-lg text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)] disabled:opacity-40"
        >
          <Paperclip size={16} />
        </button>
        <div className="flex-1" />
        <select
          value={model}
          onChange={(event) => chooseModel(event.target.value)}
          disabled={sending}
          aria-label="Model"
          title={status.models.find((item) => item.id === model)?.detail}
          className="h-8 rounded-lg border border-transparent bg-transparent px-2 text-[0.8125rem] text-[var(--text-secondary)] hover:bg-[var(--surface-hover)]"
        >
          {status.models.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
        {sending ? (
          <button
            type="button"
            onClick={stop}
            disabled={stopping}
            aria-label="Stop the reply"
            title="Stop the reply"
            className="flex h-8 w-8 items-center justify-center rounded-lg bg-[var(--text-primary)] text-[var(--surface-raised)] disabled:opacity-50"
          >
            {stopping ? <Spinner size={13} /> : <Square size={12} fill="currentColor" />}
          </button>
        ) : (
          <button
            type="button"
            onClick={() => void send()}
            disabled={!canSend}
            aria-label="Send"
            title="Send"
            className="flex h-8 w-8 items-center justify-center rounded-lg bg-[var(--color-accent-600)] text-[var(--text-inverse)] hover:bg-[var(--color-accent-700)] disabled:opacity-35"
          >
            <ArrowUp size={16} />
          </button>
        )}
      </div>
    </div>
  ) : (
    setup
  );

  const projectChip = project.data ? (
    <Link
      to={`/claude/projects/${project.data.id}`}
      className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border-subtle)] px-2.5 py-0.5 text-[0.75rem] text-[var(--text-secondary)] hover:bg-[var(--surface-hover)]"
    >
      <FolderKanban size={12} />
      {project.data.name}
    </Link>
  ) : null;

  if (routeThreadId && thread.isError) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center">
        <div>
          <p className="text-sm font-semibold text-[var(--text-primary)]">
            This chat could not be opened
          </p>
          <p className="mt-1 text-[0.8125rem] text-[var(--text-secondary)]">
            {describeError(thread.error)}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div
      className="flex h-full min-h-0 flex-col"
      onDragOver={(event) => {
        if (event.dataTransfer.types.includes('Files')) {
          event.preventDefault();
          setDragging(true);
        }
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      {empty ? (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto px-6 py-8">
          <div className="w-full max-w-2xl">
            {routeThreadId && thread.isPending ? (
              <div className="flex justify-center py-10 text-[var(--text-muted)]">
                <Spinner size={16} />
              </div>
            ) : (
              <>
                <h1 className="text-center font-[family-name:var(--font-serif)] text-[1.75rem] text-[var(--text-primary)]">
                  {project.data ? project.data.name : 'What are we working on?'}
                </h1>
                <p className="mb-6 mt-1.5 text-center text-[0.8125rem] text-[var(--text-secondary)]">
                  {project.data
                    ? 'A new chat in this project. It follows the project’s instructions and can see its files.'
                    : 'Ask anything, attach files or photos, or have your courses tidied up.'}
                </p>
              </>
            )}
            {composer}
            {afterword.error ? <Afterword notices={[]} error={afterword.error} /> : null}
          </div>
        </div>
      ) : (
        <>
          <header className="flex shrink-0 items-center gap-3 border-b border-[var(--border-subtle)] px-6 py-2.5">
            <h1 className="min-w-0 flex-1 truncate text-sm font-semibold text-[var(--text-primary)]">
              {heading}
            </h1>
            {projectChip}
            {usage && usage.input + usage.output > 0 ? (
              <span
                className="shrink-0 text-[0.75rem] text-[var(--text-muted)]"
                title="Tokens sent and received in this chat, and an estimate of their cost at list prices. Your bill is in the Claude Console."
              >
                {formatTokens(usage.input + usage.output)} tokens · about{' '}
                {formatCost(usage.cost_usd)}
              </span>
            ) : null}
          </header>
          <div
            ref={scrollRef}
            onScroll={onScroll}
            className="sp-scroll min-h-0 flex-1 overflow-y-auto"
          >
            <div className="mx-auto w-full max-w-3xl space-y-5 px-6 py-6">
              {shown.map((item, index) =>
                item.role === 'user' ? (
                  <UserMessage key={index} item={item} />
                ) : (
                  <AssistantMessage key={index} blocks={item.blocks} />
                ),
              )}
              {live ? (
                <div aria-live="polite">
                  <AssistantMessage blocks={live.blocks} streaming />
                  {live.thinking || live.blocks.length === 0 ? (
                    <Thinking thought={live.thought} />
                  ) : null}
                </div>
              ) : null}
              {!live ? <Afterword notices={afterword.notices} error={afterword.error} /> : null}
            </div>
          </div>
          <div className="shrink-0 px-6 pb-4">
            <div className="mx-auto w-full max-w-3xl">
              {composer}
              <p className="mt-1.5 text-center text-[0.6875rem] text-[var(--text-muted)]">
                Claude can make mistakes. Check anything that matters.
              </p>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Afterword({ notices, error }: { notices: string[]; error: string | null }) {
  if (notices.length === 0 && !error) return null;
  return (
    <div className="mt-3 space-y-1.5">
      {notices.map((notice, index) => (
        <p key={index} className="text-[0.8125rem] text-[var(--text-muted)]">
          {notice}
        </p>
      ))}
      {error ? (
        <p
          role="alert"
          className="flex items-start gap-2 rounded-lg border border-[var(--signal-danger)]/30 bg-[var(--signal-danger-bg)] px-3 py-2 text-[0.8125rem] text-[var(--text-primary)]"
        >
          <AlertTriangle size={14} className="mt-0.5 shrink-0 text-[var(--signal-danger)]" />
          {error}
        </p>
      ) : null}
    </div>
  );
}

function Thinking({ thought }: { thought: string }) {
  const summary = thought.trim();
  return (
    <div className="mt-2 text-[0.8125rem] text-[var(--text-muted)]">
      <span className="flex items-center gap-2">
        <Spinner size={12} />
        Thinking
      </span>
      {summary ? (
        <p className="mt-1.5 line-clamp-3 border-l-2 border-[var(--border-subtle)] pl-3 leading-relaxed">
          {summary.slice(-400)}
        </p>
      ) : null}
    </div>
  );
}

function UserMessage({ item }: { item: AssistantItem }) {
  return (
    <div className="flex flex-col items-end gap-1.5">
      {item.attachments.length > 0 ? (
        <ul className="flex max-w-[85%] flex-wrap justify-end gap-1.5">
          {item.attachments.map((file) => (
            <li key={file.id}>
              <AttachmentChip file={file} />
            </li>
          ))}
        </ul>
      ) : null}
      {item.text ? (
        <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl bg-[var(--surface-sunken)] px-4 py-2.5 text-[0.9375rem] leading-relaxed text-[var(--text-primary)]">
          {item.text}
        </div>
      ) : null}
    </div>
  );
}

function AttachmentChip({ file }: { file: AssistantFile }) {
  const client = useClient();
  const [url, setUrl] = useState<string | null>(null);
  const showImage = file.kind === 'image' && file.available !== false;

  useEffect(() => {
    if (!showImage) return;
    let objectUrl: string | null = null;
    let cancelled = false;
    client
      .fetchBlobUrl(client.assistantAttachmentPath(file.id))
      .then((made) => {
        if (cancelled) URL.revokeObjectURL(made);
        else {
          objectUrl = made;
          setUrl(made);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [client, file.id, showImage]);

  if (showImage && url) {
    return (
      <img
        src={url}
        alt={file.filename}
        className="max-h-44 max-w-[14rem] rounded-xl border border-[var(--border-subtle)] object-cover"
      />
    );
  }
  return (
    <span className="flex items-center gap-2 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-3 py-2 text-[0.8125rem] text-[var(--text-secondary)]">
      <FileText size={14} className="shrink-0" />
      <span className="max-w-[12rem] truncate text-[var(--text-primary)]">{file.filename}</span>
      <span className="shrink-0 text-[0.6875rem] text-[var(--text-muted)]">
        {formatBytes(file.byte_size)}
      </span>
    </span>
  );
}

function AssistantMessage({
  blocks,
  streaming = false,
}: {
  blocks: AssistantItem['blocks'];
  streaming?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const words = replyText(blocks);
  if (blocks.length === 0) return null;

  const copy = () => {
    void navigator.clipboard?.writeText(words).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="group space-y-2.5">
      {blocks.map((block, index) =>
        block.type === 'text' ? (
          <GroundedAnswer
            key={index}
            markdown={block.text}
            citations={[]}
            className="text-[0.9375rem]"
          />
        ) : (
          <Step key={block.id || index} block={block} />
        ),
      )}
      {!streaming && words ? (
        <button
          type="button"
          onClick={copy}
          className="flex items-center gap-1.5 rounded-md px-1.5 py-1 text-[0.75rem] text-[var(--text-muted)] opacity-0 transition-opacity hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)] focus-visible:opacity-100 group-hover:opacity-100"
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      ) : null}
    </div>
  );
}

function Step({ block }: { block: Extract<AssistantItem['blocks'][number], { type: 'tool' }> }) {
  // A connector's result is whatever its server sent, not a line written to
  // be read as a headline, so the step is named and the result sits under it.
  const external = connectorTool(block.name) !== null;
  const summary = block.done && !external ? resultSummary(block.result) : '';
  const detail = !block.done ? '' : external ? block.result.trim() : resultDetail(block.result);
  const label = summary || toolLabel(block.name, block.done);
  const icon = !block.done ? (
    <Spinner size={12} />
  ) : block.is_error ? (
    <AlertTriangle size={13} className="text-[var(--signal-caution)]" />
  ) : (
    <Check size={13} className="text-[var(--signal-grounded)]" />
  );
  const row = (
    <span className="flex items-start gap-2 text-[0.8125rem] leading-relaxed text-[var(--text-secondary)]">
      <span className="mt-[0.2rem] shrink-0">{icon}</span>
      <span className="min-w-0 break-words">{label}</span>
    </span>
  );
  if (!detail) {
    return (
      <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-sunken)] px-3 py-1.5">
        {row}
      </div>
    );
  }
  return (
    <details className="rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-sunken)] px-3 py-1.5">
      <summary className="cursor-pointer list-none">{row}</summary>
      <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words border-t border-[var(--border-subtle)] pt-2 font-[family-name:var(--font-mono)] text-[0.6875rem] leading-relaxed text-[var(--text-muted)]">
        {detail}
      </pre>
    </details>
  );
}
