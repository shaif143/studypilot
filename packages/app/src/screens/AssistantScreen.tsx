/**
 * The Claude chat (website only).
 *
 * A chat application inside StudyPilot, running on the Claude API key the
 * account saves here: its own list of recent chats and projects on the left,
 * and a chat, the list of projects, or one project on the right.
 *
 * The four addresses under /claude are read here rather than declared as
 * four routes, so the chat on screen keeps its state when a new chat is given
 * its permanent address.
 */
import type { AssistantStatus, AssistantThreadSummary } from '@studypilot/shared';
import { Button, Dialog, Field, Input, Spinner } from '@studypilot/ui';
import { useQueryClient } from '@tanstack/react-query';
import { FolderKanban, KeyRound, Pencil, Plug, Plus, Trash2 } from 'lucide-react';
import { type FormEvent, type ReactNode, useMemo, useState } from 'react';
import { Link, NavLink, useLocation, useMatch, useNavigate } from 'react-router-dom';

import { ChatView } from '../components/assistant/ChatView';
import { ExtensionsView } from '../components/assistant/ExtensionsView';
import { ProjectPage, ProjectsList } from '../components/assistant/ProjectViews';
import {
  describeError,
  keys,
  useAssistantActions,
  useAssistantKey,
  useAssistantStatus,
  useAssistantThreads,
  useUpdateSettings,
} from '../hooks/queries';
import { recencyGroup } from '../lib/assistant';

export function AssistantScreen() {
  const status = useAssistantStatus();
  const location = useLocation();
  const navigate = useNavigate();
  const chatMatch = useMatch('/claude/chat/:threadId');
  const projectMatch = useMatch('/claude/projects/:projectId');
  const projectsMatch = useMatch('/claude/projects');
  const extensionsMatch = useMatch('/claude/extensions');
  const [keyOpen, setKeyOpen] = useState(false);

  const routeThreadId = chatMatch?.params.threadId ?? null;
  const newChatProject = useMemo(
    () => new URLSearchParams(location.search).get('project'),
    [location.search],
  );

  // A new chat keeps its place on screen when it is given an address: the
  // chat it became is recognised and drawn under the key it already had.
  const [adopted, setAdopted] = useState<{ id: string; key: string } | null>(null);
  const newKey = `new:${location.key}`;
  const chatKey =
    routeThreadId === null
      ? newKey
      : adopted?.id === routeThreadId
        ? adopted.key
        : routeThreadId;

  if (status.isPending) {
    return (
      <div className="flex h-full items-center justify-center text-[var(--text-muted)]">
        <Spinner size={16} />
      </div>
    );
  }
  if (status.isError) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center">
        <div>
          <p className="text-sm font-semibold text-[var(--text-primary)]">
            The chat could not be loaded
          </p>
          <p className="mt-1 text-[0.8125rem] text-[var(--text-secondary)]">
            {describeError(status.error)}
          </p>
          <Button className="mt-3" onClick={() => void status.refetch()}>
            Try again
          </Button>
        </div>
      </div>
    );
  }

  const main = projectMatch?.params.projectId ? (
    <div className="sp-scroll h-full overflow-y-auto">
      <ProjectPage projectId={projectMatch.params.projectId} />
    </div>
  ) : projectsMatch ? (
    <div className="sp-scroll h-full overflow-y-auto">
      <ProjectsList />
    </div>
  ) : extensionsMatch ? (
    <div className="sp-scroll h-full overflow-y-auto">
      <ExtensionsView />
    </div>
  ) : (
    <ChatView
      key={chatKey}
      routeThreadId={routeThreadId}
      projectId={routeThreadId ? null : newChatProject}
      status={status.data}
      setup={<Setup status={status.data} onAddKey={() => setKeyOpen(true)} />}
      onCreated={(id) => {
        setAdopted({ id, key: chatKey });
        navigate(`/claude/chat/${id}`, { replace: true });
      }}
    />
  );

  return (
    <div className="flex h-full min-h-0">
      <ChatList status={status.data} activeId={routeThreadId} onKey={() => setKeyOpen(true)} />
      <div className="min-w-0 flex-1">{main}</div>
      <KeyDialog open={keyOpen} onClose={() => setKeyOpen(false)} status={status.data} />
    </div>
  );
}

// --- the list on the left ------------------------------------------------------
function ChatList({
  status,
  activeId,
  onKey,
}: {
  status: AssistantStatus;
  activeId: string | null;
  onKey: () => void;
}) {
  const threads = useAssistantThreads();
  const navigate = useNavigate();
  const { updateThread, deleteThread } = useAssistantActions();
  const [renaming, setRenaming] = useState<AssistantThreadSummary | null>(null);
  const [removing, setRemoving] = useState<AssistantThreadSummary | null>(null);
  const [title, setTitle] = useState('');

  const groups = useMemo(() => {
    const out: Array<{ label: string; threads: AssistantThreadSummary[] }> = [];
    for (const thread of threads.data ?? []) {
      const label = recencyGroup(thread.updated_at);
      const last = out[out.length - 1];
      if (last && last.label === label) last.threads.push(thread);
      else out.push({ label, threads: [thread] });
    }
    return out;
  }, [threads.data]);

  const submitRename = (event: FormEvent) => {
    event.preventDefault();
    if (!renaming || !title.trim()) return;
    updateThread.mutate(
      { id: renaming.id, title: title.trim() },
      { onSuccess: () => setRenaming(null) },
    );
  };

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    [
      'flex items-center gap-2 rounded-[var(--radius-control)] px-2.5 py-1.5 text-[0.8125rem] font-medium',
      isActive
        ? 'bg-[var(--surface-selected)] text-[var(--text-primary)]'
        : 'text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]',
    ].join(' ');

  return (
    <aside
      aria-label="Chats"
      className="hidden w-[15.5rem] shrink-0 flex-col border-r border-[var(--border-subtle)] bg-[var(--surface-page)] md:flex"
    >
      <div className="space-y-1 px-2 pb-2 pt-3">
        <NavLink to="/claude" end className={linkClass}>
          <Plus size={15} />
          New chat
        </NavLink>
        <NavLink to="/claude/projects" className={linkClass}>
          <FolderKanban size={15} />
          Projects
        </NavLink>
        <NavLink to="/claude/extensions" className={linkClass}>
          <Plug size={15} />
          Connectors and plugins
        </NavLink>
      </div>

      <div className="sp-scroll min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {threads.isPending ? (
          <div className="flex justify-center py-6 text-[var(--text-muted)]">
            <Spinner size={13} />
          </div>
        ) : groups.length === 0 ? (
          <p className="px-2.5 pt-3 text-[0.75rem] leading-relaxed text-[var(--text-muted)]">
            Your chats will be listed here.
          </p>
        ) : (
          groups.map((group) => (
            <div key={group.label}>
              <p className="px-2.5 pb-1 pt-3 text-[0.6875rem] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                {group.label}
              </p>
              <ul className="space-y-px">
                {group.threads.map((thread) => (
                  <li key={thread.id} className="group relative">
                    <Link
                      to={`/claude/chat/${thread.id}`}
                      aria-current={thread.id === activeId ? 'page' : undefined}
                      className={[
                        'block truncate rounded-[var(--radius-control)] py-1.5 pl-2.5 pr-12 text-[0.8125rem]',
                        thread.id === activeId
                          ? 'bg-[var(--surface-selected)] text-[var(--text-primary)]'
                          : 'text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]',
                      ].join(' ')}
                    >
                      {thread.title}
                    </Link>
                    <span className="absolute right-1 top-1/2 hidden -translate-y-1/2 gap-0.5 group-focus-within:flex group-hover:flex">
                      <button
                        type="button"
                        aria-label={`Rename ${thread.title}`}
                        title="Rename"
                        onClick={() => {
                          setTitle(thread.title);
                          setRenaming(thread);
                        }}
                        className="rounded p-1 text-[var(--text-muted)] hover:bg-[var(--surface-raised)] hover:text-[var(--text-primary)]"
                      >
                        <Pencil size={12} />
                      </button>
                      <button
                        type="button"
                        aria-label={`Delete ${thread.title}`}
                        title="Delete"
                        onClick={() => setRemoving(thread)}
                        className="rounded p-1 text-[var(--text-muted)] hover:bg-[var(--surface-raised)] hover:text-[var(--signal-danger)]"
                      >
                        <Trash2 size={12} />
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))
        )}
      </div>

      <button
        type="button"
        onClick={onKey}
        className="flex items-center gap-2 border-t border-[var(--border-subtle)] px-4 py-2.5 text-left text-[0.75rem] text-[var(--text-secondary)] hover:bg-[var(--surface-hover)]"
      >
        <KeyRound size={13} className="shrink-0" />
        <span className="min-w-0 truncate">
          {status.key.configured
            ? status.key.source === 'account'
              ? `Claude API key ending ${status.key.hint}`
              : 'Using this installation’s Claude key'
            : 'Add your Claude API key'}
        </span>
      </button>

      <Dialog open={renaming !== null} onClose={() => setRenaming(null)} title="Rename chat">
        {renaming ? (
          <form onSubmit={submitRename} className="space-y-3">
            <Input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              maxLength={200}
              aria-label="Chat name"
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setRenaming(null)}>
                Cancel
              </Button>
              <Button
                type="submit"
                variant="primary"
                loading={updateThread.isPending}
                disabled={!title.trim()}
              >
                Rename
              </Button>
            </div>
          </form>
        ) : null}
      </Dialog>

      <Dialog
        open={removing !== null}
        onClose={() => setRemoving(null)}
        title="Delete this chat?"
        description={`“${removing?.title ?? ''}” and the files attached in it are removed for good.`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRemoving(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={deleteThread.isPending}
              onClick={() => {
                if (!removing) return;
                const id = removing.id;
                deleteThread.mutate(id, {
                  onSuccess: () => {
                    setRemoving(null);
                    if (id === activeId) navigate('/claude');
                  },
                });
              }}
            >
              Delete
            </Button>
          </>
        }
      />
    </aside>
  );
}

// --- what is still needed before a message can be sent ---------------------------
function Setup({ status, onAddKey }: { status: AssistantStatus; onAddKey: () => void }) {
  const update = useUpdateSettings();
  const queryClient = useQueryClient();

  const steps: Array<{ done: boolean; title: string; body: ReactNode }> = [
    {
      done: status.installation_allows_cloud,
      title: 'Cloud AI is switched on for this site',
      body: (
        <>
          The person who runs this site has to allow it once, by setting{' '}
          <code>STUDYPILOT_ALLOW_CLOUD_PROCESSING</code> to <code>true</code> on the server and
          restarting it.
        </>
      ),
    },
    {
      done: status.account_allows_cloud,
      title: 'You allow cloud AI for your account',
      body: (
        <>
          <span className="block">
            What you type and attach here, and any course material Claude looks up for you, is
            sent to Anthropic to be answered.
          </span>
          <Button
            size="sm"
            variant="primary"
            className="mt-2"
            loading={update.isPending}
            onClick={() =>
              update.mutate(
                { allow_cloud_processing: true },
                { onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.assistant }) },
              )
            }
          >
            Allow it
          </Button>
        </>
      ),
    },
    {
      done: status.key.configured,
      title: 'A Claude API key is saved',
      body: (
        <>
          <span className="block">
            The chat runs on your own key from the Claude Console, and Anthropic bills that key
            for what you use.
          </span>
          <Button size="sm" variant="primary" className="mt-2" onClick={onAddKey}>
            Add your key
          </Button>
        </>
      ),
    },
  ];
  const next = steps.findIndex((step) => !step.done);

  return (
    <div className="rounded-2xl border border-[var(--border-strong)] bg-[var(--surface-raised)] p-4">
      <p className="text-sm font-semibold text-[var(--text-primary)]">
        Three things before you can chat
      </p>
      <ol className="mt-3 space-y-3">
        {steps.map((step, index) => (
          <li key={step.title} className="flex gap-3">
            <span
              className={[
                'mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[0.6875rem] font-semibold',
                step.done
                  ? 'bg-[var(--signal-grounded-bg)] text-[var(--signal-grounded)]'
                  : 'bg-[var(--surface-sunken)] text-[var(--text-secondary)]',
              ].join(' ')}
            >
              {step.done ? '✓' : index + 1}
            </span>
            <div className="min-w-0 text-[0.8125rem] leading-relaxed">
              <p
                className={
                  step.done ? 'text-[var(--text-muted)]' : 'font-medium text-[var(--text-primary)]'
                }
              >
                {step.title}
              </p>
              {!step.done && index === next ? (
                <div className="mt-1 text-[var(--text-secondary)]">{step.body}</div>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
      {update.isError ? (
        <p className="mt-2 text-[0.75rem] text-[var(--signal-danger)]">
          {describeError(update.error)}
        </p>
      ) : null}
    </div>
  );
}

function KeyDialog({
  open,
  onClose,
  status,
}: {
  open: boolean;
  onClose: () => void;
  status: AssistantStatus;
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Claude API key"
      description="The chat runs on your own key. It is checked with Anthropic, stored encrypted, and never shown again."
    >
      {/* Built afresh each time it opens, so a key typed earlier is not left in it. */}
      {open ? <KeyForm status={status} onClose={onClose} /> : null}
    </Dialog>
  );
}

function KeyForm({ status, onClose }: { status: AssistantStatus; onClose: () => void }) {
  const { save, remove } = useAssistantKey();
  const [value, setValue] = useState('');

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!value.trim()) return;
    save.mutate(value.trim(), { onSuccess: onClose });
  };
  const own = status.key.configured && status.key.source === 'account';

  return (
    <form onSubmit={submit} className="space-y-3">
      {own ? (
        <p className="rounded-lg bg-[var(--surface-sunken)] px-3 py-2 text-[0.8125rem] text-[var(--text-secondary)]">
          A key ending <strong className="text-[var(--text-primary)]">{status.key.hint}</strong> is
          saved. Paste another to replace it.
        </p>
      ) : null}
      <Field
        label={own ? 'New key' : 'Key'}
        hint="From the Claude Console at platform.claude.com, under API keys. It starts with sk-ant-."
      >
        <Input
          type="password"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="sk-ant-…"
          autoComplete="off"
          spellCheck={false}
          autoFocus
        />
      </Field>
      {save.isError ? (
        <p className="text-[0.8125rem] text-[var(--signal-danger)]">{describeError(save.error)}</p>
      ) : null}
      <div className="flex items-center justify-between gap-2 pt-1">
        {own ? (
          <Button
            type="button"
            variant="danger"
            size="sm"
            loading={remove.isPending}
            onClick={() => remove.mutate(undefined, { onSuccess: onClose })}
          >
            Remove key
          </Button>
        ) : (
          <span />
        )}
        <span className="flex gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={save.isPending} disabled={!value.trim()}>
            Save key
          </Button>
        </span>
      </div>
    </form>
  );
}
