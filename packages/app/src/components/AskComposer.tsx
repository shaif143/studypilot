/**
 * The question box, with a "+" menu beside it.
 *
 * A question about a slide is often about something else as well: a photo of
 * the student's own working, a paper, a folder of code, an issue on GitHub.
 * The menu attaches those, and offers the commands that write a question for
 * you. What is attached is read for that one answer and is not added to the
 * course.
 *
 * The box owns its text and its attachments. It clears both when a question
 * is sent and puts both back if the question could not be answered, so
 * nothing typed or attached is lost to a failed request.
 */
import type { AskAttachment } from '@studypilot/shared';
import { Button, Dialog, Field, Input, Textarea } from '@studypilot/ui';
import {
  CircleDot,
  CornerDownLeft,
  FileText,
  FolderOpen,
  Image as ImageIcon,
  Paperclip,
  Plus,
  Puzzle,
  SquareSlash,
  TriangleAlert,
  X,
} from 'lucide-react';
import {
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useNavigate } from 'react-router-dom';

import { useApp } from '../context';
import { describeError, useAssistantExtensions, useImportGitHubIssue } from '../hooks/queries';
import {
  MAX_ASK_FILES,
  type PendingAttachment,
  type StudyCommand,
  allCommands,
  attachmentOnlyQuestion,
  describeRefusals,
  encodeAttachments,
  expandCommand,
  issueAttachment,
  takeFiles,
} from '../lib/askAttachments';
import { formatBytes, matchingCommands } from '../lib/assistant';

export interface AskComposerProps {
  /**
   * Send the question. Resolves true when it was answered; false puts the
   * text and the attachments back in the box to try again.
   */
  onSend: (question: string, attachments: AskAttachment[]) => Promise<boolean>;
  pending: boolean;
  /** Nothing can be asked at all: no document, or help is switched off. */
  disabled?: boolean;
  /** An empty box may be sent (the "explain this page" mode). */
  allowEmpty?: boolean;
  /** What the built-in commands talk about: "this slide", "this material". */
  subject: string;
  label: string;
  placeholder: string;
  sendLabel?: string;
  rows?: number;
}

export function AskComposer({
  onSend,
  pending,
  disabled = false,
  allowEmpty = false,
  subject,
  label,
  placeholder,
  sendLabel = 'Ask',
  rows = 1,
}: AskComposerProps) {
  const navigate = useNavigate();
  const { platform } = useApp();
  const extensions = useAssistantExtensions();

  const [text, setText] = useState('');
  const [waiting, setWaiting] = useState<PendingAttachment[]>([]);
  const [notice, setNotice] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [reading, setReading] = useState(false);

  const inputRef = useRef<HTMLTextAreaElement>(null);
  const filesRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const commands = useMemo(
    () => allCommands(extensions.data?.commands ?? []),
    [extensions.data?.commands],
  );
  const suggestions = matchingCommands(text, commands);
  const busy = pending || reading;
  const full = waiting.length >= MAX_ASK_FILES;

  // The menu closes when anything outside it is pressed, or on Escape.
  useEffect(() => {
    if (!menuOpen) return;
    const onPointer = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false);
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [menuOpen]);

  const add = (files: File[]) => {
    if (files.length === 0) return;
    const { attachments, refused } = takeFiles(waiting, files);
    setWaiting(attachments);
    setNotice(describeRefusals(refused));
  };

  const choose = (command: StudyCommand) => {
    // A built-in command is the question it stands for, there to send or change.
    setText(command.prompt ? command.prompt(subject) : `/${command.name} `);
    inputRef.current?.focus();
  };

  const submit = async () => {
    if (busy || disabled) return;
    const typed = text.trim();
    if (!typed && waiting.length === 0 && !allowEmpty) return;
    const sentText = text;
    const sentFiles = waiting;
    const question =
      typed || waiting.length === 0
        ? expandCommand(typed, commands, subject)
        : attachmentOnlyQuestion(subject);

    setReading(true);
    let attachments: AskAttachment[];
    try {
      attachments = await encodeAttachments(sentFiles);
    } catch {
      setNotice('One of the attachments could not be read. Remove it and attach it again.');
      setReading(false);
      return;
    }
    setText('');
    setWaiting([]);
    setNotice('');
    setReading(false);
    const answered = await onSend(question, attachments);
    if (!answered) {
      setText(sentText);
      setWaiting(sentFiles);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends; Shift+Enter adds a line.
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void submit();
    } else if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'u') {
      event.preventDefault();
      filesRef.current?.click();
    }
  };

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(event.clipboardData.files);
    if (files.length === 0) return;
    event.preventDefault();
    add(files);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    add(Array.from(event.dataTransfer.files));
  };

  const canSend = !disabled && (allowEmpty || text.trim().length > 0 || waiting.length > 0);

  return (
    <div
      onDragOver={(event) => {
        if (!event.dataTransfer.types.includes('Files')) return;
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      className={[
        'min-w-0 rounded-[var(--radius-control)]',
        dragging ? 'outline outline-2 outline-offset-4 outline-[var(--border-focus)]' : '',
      ].join(' ')}
    >
      {notice ? (
        <p
          role="status"
          className="mb-2 flex items-start gap-1.5 text-[0.75rem] leading-relaxed text-[var(--signal-caution)] [overflow-wrap:anywhere]"
        >
          <TriangleAlert size={12} className="mt-0.5 shrink-0" />
          <span className="min-w-0 flex-1">{notice}</span>
          <button
            type="button"
            onClick={() => setNotice('')}
            aria-label="Dismiss"
            className="shrink-0 rounded p-0.5 hover:bg-[var(--surface-hover)]"
          >
            <X size={11} />
          </button>
        </p>
      ) : null}

      {waiting.length > 0 ? (
        <ul aria-label="Attached to this question" className="mb-2 flex flex-wrap gap-1.5">
          {waiting.map((entry) => (
            <li
              key={entry.key}
              title={`${entry.name} (${formatBytes(entry.size)})`}
              className="flex max-w-full items-center gap-1.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-2 py-1 text-[0.75rem] text-[var(--text-secondary)] @2xl:max-w-[16rem]"
            >
              <span className="shrink-0">
                {entry.kind === 'image' ? (
                  <ImageIcon size={12} />
                ) : entry.kind === 'issue' ? (
                  <CircleDot size={12} />
                ) : (
                  <FileText size={12} />
                )}
              </span>
              <span className="min-w-0 truncate">{entry.name}</span>
              <button
                type="button"
                onClick={() => setWaiting((items) => items.filter((it) => it.key !== entry.key))}
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
          className="mb-2 overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-raised)]"
        >
          {suggestions.map((command) => (
            <li key={command.name}>
              <button
                type="button"
                onClick={() => choose(command)}
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

      {/* In a narrow side panel the box takes its own line and the button drops below it. */}
      <div className="flex flex-wrap items-end justify-end gap-2">
        <div ref={menuRef} className="relative shrink-0">
          <button
            type="button"
            aria-label="Add files, photos and more"
            title="Add files, photos and more"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            disabled={disabled}
            onClick={() => setMenuOpen((open) => !open)}
            className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-control)] border border-[var(--border-strong)] bg-[var(--surface-raised)] text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)] disabled:opacity-40"
          >
            <Plus
              size={16}
              className={menuOpen ? 'rotate-45 transition-transform' : 'transition-transform'}
            />
          </button>
          {menuOpen ? (
            <div
              role="menu"
              aria-label="Add to this question"
              className="absolute bottom-full left-0 z-20 mb-2 w-[min(17rem,calc(100vw-2rem))] rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-1 shadow-[var(--shadow-overlay)]"
            >
              <MenuItem
                icon={<Paperclip size={15} />}
                name="Add files or photos"
                hint="⌘U"
                off={full}
                onChoose={() => {
                  setMenuOpen(false);
                  filesRef.current?.click();
                }}
              />
              <MenuItem
                icon={<FolderOpen size={15} />}
                name="Add folder"
                off={full}
                onChoose={() => {
                  setMenuOpen(false);
                  folderRef.current?.click();
                }}
              />
              <MenuItem
                icon={<CircleDot size={15} />}
                name="Import GitHub issue"
                off={full}
                onChoose={() => {
                  setMenuOpen(false);
                  setImporting(true);
                }}
              />
              <div className="my-1 border-t border-[var(--border-subtle)]" />
              <MenuItem
                icon={<SquareSlash size={15} />}
                name="Slash commands"
                onChoose={() => {
                  setMenuOpen(false);
                  setText('/');
                  inputRef.current?.focus();
                }}
              />
              <MenuItem
                icon={<Puzzle size={15} />}
                name="Plugins"
                onChoose={() => {
                  setMenuOpen(false);
                  navigate(platform.kind === 'web' ? '/claude/extensions' : '/plugins');
                }}
              />
            </div>
          ) : null}
        </div>

        <input
          ref={filesRef}
          type="file"
          multiple
          hidden
          aria-label="Files or photos to attach"
          accept="image/*,application/pdf,text/*,.md,.csv,.json,.py,.ipynb,.tex,.js,.ts,.java,.c,.cpp,.h,.m,.r,.sql"
          onChange={(event) => {
            add(Array.from(event.target.files ?? []));
            event.target.value = '';
          }}
        />
        <input
          ref={folderRef}
          type="file"
          multiple
          hidden
          aria-label="Folder to attach"
          // A folder picker: not in React's typings, understood by every
          // browser the app runs in.
          {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
          onChange={(event) => {
            add(Array.from(event.target.files ?? []));
            event.target.value = '';
          }}
        />

        <Textarea
          ref={inputRef}
          rows={rows}
          value={text}
          aria-label={label}
          placeholder={placeholder}
          disabled={disabled}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          className="min-h-[2.5rem] min-w-0 flex-1 basis-[7rem] bg-[var(--surface-raised)]"
        />
        <Button
          variant="primary"
          icon={<CornerDownLeft size={14} />}
          loading={busy}
          disabled={!canSend}
          onClick={() => void submit()}
        >
          {sendLabel}
        </Button>
      </div>

      <Dialog
        open={importing}
        onClose={() => setImporting(false)}
        title="Import a GitHub issue"
        description="The issue or pull request and its comments are fetched and attached to your question as text. Public repositories only."
      >
        {importing ? (
          <ImportIssue
            onDone={(attachment) => {
              setWaiting((items) =>
                items.length >= MAX_ASK_FILES ? items : [...items, attachment],
              );
              setImporting(false);
              // After the dialog has closed and handed focus back.
              window.setTimeout(() => inputRef.current?.focus(), 60);
            }}
            onCancel={() => setImporting(false)}
          />
        ) : null}
      </Dialog>
    </div>
  );
}

function MenuItem({
  icon,
  name,
  hint,
  off = false,
  onChoose,
}: {
  icon: ReactNode;
  name: string;
  hint?: string;
  off?: boolean;
  onChoose: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={off}
      onClick={onChoose}
      className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-[0.8125rem] text-[var(--text-primary)] hover:bg-[var(--surface-hover)] disabled:opacity-40"
    >
      <span className="shrink-0 text-[var(--text-secondary)]">{icon}</span>
      <span className="min-w-0 flex-1 truncate">{name}</span>
      {hint ? (
        <span className="shrink-0 text-[0.6875rem] text-[var(--text-muted)]">{hint}</span>
      ) : null}
    </button>
  );
}

/** Mounted only while the dialog is open, so it starts empty each time. */
function ImportIssue({
  onDone,
  onCancel,
}: {
  onDone: (attachment: PendingAttachment) => void;
  onCancel: () => void;
}) {
  const fetchIssue = useImportGitHubIssue();
  const [reference, setReference] = useState('');

  const run = () => {
    const value = reference.trim();
    if (!value || fetchIssue.isPending) return;
    fetchIssue.mutate(value, {
      onSuccess: (issue) => onDone(issueAttachment(issue)),
    });
  };

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        run();
      }}
      className="space-y-3"
    >
      <Field label="Link or reference" hint="A link to the issue, or owner/repository#123.">
        <Input
          autoFocus
          value={reference}
          onChange={(event) => setReference(event.target.value)}
          placeholder="https://github.com/owner/repository/issues/123"
        />
      </Field>
      {fetchIssue.isError ? (
        <p role="alert" className="text-[0.8125rem] text-[var(--signal-danger)]">
          {describeError(fetchIssue.error)}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          type="submit"
          variant="primary"
          loading={fetchIssue.isPending}
          disabled={reference.trim().length === 0}
        >
          Import
        </Button>
      </div>
    </form>
  );
}
