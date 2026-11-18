/**
 * Connectors and plugins for the Claude chat.
 *
 * A connector is a remote MCP server: somewhere Claude can fetch from or act
 * in. Anthropic reaches it on the student's behalf; this page only keeps its
 * address and, if it needs one, its access token. A plugin is an archive of
 * skills and commands: instructions Claude reads when a task calls for them.
 */
import type {
  AssistantConnector,
  AssistantConnectorTest,
  AssistantPlugin,
} from '@studypilot/shared';
import { Button, Dialog, EmptyState, Field, Input, Spinner, Toggle } from '@studypilot/ui';
import { Check, Plug, Plus, Puzzle, Trash2, TriangleAlert, Upload } from 'lucide-react';
import { type FormEvent, type ReactNode, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import {
  describeError,
  useAssistantExtensionActions,
  useAssistantExtensions,
} from '../../hooks/queries';

/** What a sign-in came back with, read once from the address and then taken out of it. */
function useSignInOutcome(): { outcome: string; connector: string; reason: string } | null {
  const [params, setParams] = useSearchParams();
  const [seen] = useState(() => {
    const outcome = params.get('signin');
    if (!outcome) return null;
    return {
      outcome,
      connector: params.get('connector') ?? 'the connector',
      reason: params.get('reason') ?? '',
    };
  });
  useEffect(() => {
    if (params.has('signin')) setParams({}, { replace: true });
  }, [params, setParams]);
  return seen;
}

export function ExtensionsView() {
  const extensions = useAssistantExtensions();
  const returned = useSignInOutcome();

  if (extensions.isPending) {
    return (
      <div className="flex justify-center py-16 text-[var(--text-muted)]">
        <Spinner size={16} />
      </div>
    );
  }
  if (extensions.isError) {
    return (
      <p className="px-6 py-10 text-center text-[0.8125rem] text-[var(--signal-danger)]">
        {describeError(extensions.error)}
      </p>
    );
  }
  const data = extensions.data;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-6 py-8">
      {returned ? (
        <p
          role="status"
          className={[
            'flex items-start gap-2 rounded-lg border px-3 py-2 text-[0.8125rem] text-[var(--text-primary)]',
            returned.outcome === 'connected'
              ? 'border-[var(--signal-grounded)]/30 bg-[var(--signal-grounded-bg)]'
              : 'border-[var(--signal-danger)]/30 bg-[var(--signal-danger-bg)]',
          ].join(' ')}
        >
          {returned.outcome === 'connected' ? (
            <Check size={14} className="mt-0.5 shrink-0 text-[var(--signal-grounded)]" />
          ) : (
            <TriangleAlert size={14} className="mt-0.5 shrink-0 text-[var(--signal-danger)]" />
          )}
          <span>
            {returned.outcome === 'connected'
              ? `You are signed in to ${returned.connector}. Press Test to see what it offers.`
              : returned.outcome === 'cancelled'
                ? `The sign-in to ${returned.connector} was not completed.`
                : `The sign-in to ${returned.connector} did not work. ${returned.reason}`}
          </span>
        </p>
      ) : null}
      <Connectors connectors={data.connectors} max={data.max_connectors} plugins={data.plugins} />
      <Plugins plugins={data.plugins} />
    </div>
  );
}

// --- connectors ------------------------------------------------------------------
function Connectors({
  connectors,
  max,
  plugins,
}: {
  connectors: AssistantConnector[];
  max: number;
  plugins: AssistantPlugin[];
}) {
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<AssistantConnector | null>(null);
  const pluginName = (id: string | null) => plugins.find((plugin) => plugin.id === id)?.name;

  return (
    <section aria-labelledby="connectors-heading">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1
            id="connectors-heading"
            className="font-[family-name:var(--font-serif)] text-[1.5rem] text-[var(--text-primary)]"
          >
            Connectors
          </h1>
          <p className="mt-1 max-w-xl text-[0.8125rem] leading-relaxed text-[var(--text-secondary)]">
            Other services Claude can look things up in or act in for you, through a remote MCP
            server. Up to {max} can be switched on at once.
          </p>
        </div>
        <Button variant="primary" icon={<Plus size={15} />} onClick={() => setAdding(true)}>
          Add connector
        </Button>
      </div>

      {connectors.length === 0 ? (
        <div className="mt-5">
          <EmptyState
            icon={<Plug size={22} />}
            title="No connectors yet"
            description="Add one by its address, then sign in to it if it asks. Many services publish a remote MCP server; its documentation gives the address, which starts with https://."
          />
        </div>
      ) : (
        <ul className="mt-5 space-y-2">
          {connectors.map((connector) => (
            <ConnectorRow
              key={connector.id}
              connector={connector}
              from={pluginName(connector.plugin_id)}
              onEdit={() => setEditing(connector)}
            />
          ))}
        </ul>
      )}
      <p className="mt-3 text-[0.75rem] leading-relaxed text-[var(--text-muted)]">
        Only add a connector you trust. While it is on, Claude can send it parts of your chats,
        and whatever it returns is read by Claude. Anthropic connects to it for you and keeps what
        is exchanged under its usual retention.
      </p>

      <Dialog
        open={adding}
        onClose={() => setAdding(false)}
        title="Add a connector"
        description="The address of a remote MCP server. It must be on the public internet; one running on your own computer cannot be reached."
      >
        {adding ? <ConnectorForm onClose={() => setAdding(false)} /> : null}
      </Dialog>
      <Dialog open={editing !== null} onClose={() => setEditing(null)} title="Edit connector">
        {editing ? <ConnectorForm connector={editing} onClose={() => setEditing(null)} /> : null}
      </Dialog>
    </section>
  );
}

function ConnectorRow({
  connector,
  from,
  onEdit,
}: {
  connector: AssistantConnector;
  from: string | undefined;
  onEdit: () => void;
}) {
  const { updateConnector, deleteConnector, testConnector, signIn, signOut } =
    useAssistantExtensionActions();
  const [result, setResult] = useState<AssistantConnectorTest | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const test = () => {
    setResult(null);
    setProblem(null);
    testConnector.mutate(connector.id, {
      onSuccess: setResult,
      onError: (error) => setProblem(describeError(error)),
    });
  };

  return (
    <li className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-[var(--text-primary)]">
            {connector.label}
          </p>
          <p className="truncate text-[0.75rem] text-[var(--text-muted)]" title={connector.url}>
            {connector.url}
          </p>
          <p className="mt-0.5 text-[0.75rem] text-[var(--text-muted)]">
            {connector.signed_in
              ? 'Signed in'
              : connector.has_token
                ? 'Access token saved'
                : 'Not signed in'}
            {from ? ` · from the plugin ${from}` : ''}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {connector.signed_in ? (
            <Button
              size="sm"
              variant="ghost"
              loading={signOut.isPending}
              onClick={() => signOut.mutate(connector.id)}
            >
              Sign out
            </Button>
          ) : (
            <Button
              size="sm"
              variant={connector.has_token ? 'ghost' : 'secondary'}
              loading={signIn.isPending || signIn.isSuccess}
              onClick={() => {
                setProblem(null);
                signIn.mutate(connector.id, {
                  // The whole page goes to the connector's sign-in and comes
                  // back here when it is done.
                  onSuccess: ({ authorization_url }) => window.location.assign(authorization_url),
                  onError: (error) => setProblem(describeError(error)),
                });
              }}
            >
              Sign in
            </Button>
          )}
          <Button size="sm" variant="ghost" loading={testConnector.isPending} onClick={test}>
            Test
          </Button>
          <Button size="sm" variant="ghost" onClick={onEdit}>
            Edit
          </Button>
          <button
            type="button"
            aria-label={`Remove ${connector.label}`}
            title="Remove"
            onClick={() => deleteConnector.mutate(connector.id)}
            className="rounded p-1.5 text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--signal-danger)]"
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>
      <div className="mt-1">
        <Toggle
          checked={connector.enabled}
          onChange={(next) =>
            updateConnector.mutate(
              { id: connector.id, enabled: next },
              { onError: (error) => setProblem(describeError(error)) },
            )
          }
          label={connector.enabled ? 'On: used in your chats' : 'Off'}
        />
      </div>
      {result?.ok ? (
        <p className="mt-2 flex items-start gap-2 text-[0.8125rem] text-[var(--text-secondary)]">
          <Check size={14} className="mt-0.5 shrink-0 text-[var(--signal-grounded)]" />
          <span>
            It answered
            {result.tools.length > 0
              ? `, and offers ${result.tools.length} ${result.tools.length === 1 ? 'tool' : 'tools'}: ${result.tools.join(', ')}.`
              : ', but listed no tools.'}
          </span>
        </p>
      ) : null}
      {result && !result.ok ? <Problem>{result.detail}</Problem> : null}
      {problem ? <Problem>{problem}</Problem> : null}
    </li>
  );
}

function Problem({ children }: { children: ReactNode }) {
  return (
    <p className="mt-2 flex items-start gap-2 text-[0.8125rem] text-[var(--text-primary)]">
      <TriangleAlert size={14} className="mt-0.5 shrink-0 text-[var(--signal-danger)]" />
      <span className="min-w-0 break-words">{children}</span>
    </p>
  );
}

function ConnectorForm({
  connector,
  onClose,
}: {
  connector?: AssistantConnector;
  onClose: () => void;
}) {
  const { addConnector, updateConnector } = useAssistantExtensionActions();
  const [label, setLabel] = useState(connector?.label ?? '');
  const [url, setUrl] = useState(connector?.url ?? '');
  const [token, setToken] = useState('');
  const [clearToken, setClearToken] = useState(false);
  const action = connector ? updateConnector : addConnector;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!label.trim() || !url.trim()) return;
    if (connector) {
      updateConnector.mutate(
        {
          id: connector.id,
          label: label.trim(),
          url: url.trim(),
          // Absent keeps the saved token; an empty string removes it.
          ...(token.trim() ? { token: token.trim() } : clearToken ? { token: '' } : {}),
        },
        { onSuccess: onClose },
      );
    } else {
      addConnector.mutate(
        { label: label.trim(), url: url.trim(), token: token.trim() },
        { onSuccess: onClose },
      );
    }
  };

  return (
    <form onSubmit={submit} className="space-y-3">
      <Field label="Name">
        <Input
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          placeholder="Notion, GitHub, my notes server…"
          maxLength={120}
          autoFocus
        />
      </Field>
      <Field label="Address" hint="The server's MCP address, starting with https://.">
        <Input
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="https://mcp.example.com/mcp"
          spellCheck={false}
          autoComplete="off"
        />
      </Field>
      <Field
        label={connector?.has_token ? 'New access token' : 'Access token'}
        hint="Usually not needed: add the connector and press Sign in. Paste a token only for a server that gives you one. It is stored encrypted, sent to Anthropic with your chats so it can reach the server, and never shown again."
      >
        <Input
          type="password"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          placeholder={connector?.has_token ? 'Leave empty to keep the saved one' : 'Optional'}
          autoComplete="off"
          spellCheck={false}
        />
      </Field>
      {connector?.has_token ? (
        <label className="flex items-center gap-2 text-[0.8125rem] text-[var(--text-secondary)]">
          <input
            type="checkbox"
            checked={clearToken}
            onChange={(event) => setClearToken(event.target.checked)}
          />
          Remove the saved token
        </label>
      ) : null}
      {action.isError ? (
        <p className="text-[0.8125rem] text-[var(--signal-danger)]">
          {describeError(action.error)}
        </p>
      ) : null}
      <div className="flex justify-end gap-2 pt-1">
        <Button type="button" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button
          type="submit"
          variant="primary"
          loading={action.isPending}
          disabled={!label.trim() || !url.trim()}
        >
          {connector ? 'Save' : 'Add connector'}
        </Button>
      </div>
    </form>
  );
}

// --- plugins ---------------------------------------------------------------------
function Plugins({ plugins }: { plugins: AssistantPlugin[] }) {
  const { addPlugin } = useAssistantExtensionActions();
  const input = useRef<HTMLInputElement>(null);

  return (
    <section aria-labelledby="plugins-heading">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2
            id="plugins-heading"
            className="font-[family-name:var(--font-serif)] text-[1.5rem] text-[var(--text-primary)]"
          >
            Plugins
          </h2>
          <p className="mt-1 max-w-xl text-[0.8125rem] leading-relaxed text-[var(--text-secondary)]">
            Skills and commands from a plugin archive. A skill is a set of instructions Claude
            loads when a task calls for it; a command is run by typing its name after a slash.
          </p>
        </div>
        <Button
          variant="primary"
          icon={addPlugin.isPending ? undefined : <Upload size={15} />}
          loading={addPlugin.isPending}
          onClick={() => input.current?.click()}
        >
          Add plugin
        </Button>
        <input
          ref={input}
          type="file"
          accept=".zip,.plugin,.skill,application/zip"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) addPlugin.mutate(file);
            event.target.value = '';
          }}
        />
      </div>
      {addPlugin.isError ? <Problem>{describeError(addPlugin.error)}</Problem> : null}

      {plugins.length === 0 ? (
        <div className="mt-5">
          <EmptyState
            icon={<Puzzle size={22} />}
            title="No plugins yet"
            description="Add a .zip, .plugin or .skill file. Its skills and commands are read from it; nothing in it is run."
          />
        </div>
      ) : (
        <ul className="mt-5 space-y-2">
          {plugins.map((plugin) => (
            <PluginRow key={plugin.id} plugin={plugin} />
          ))}
        </ul>
      )}
    </section>
  );
}

function PluginRow({ plugin }: { plugin: AssistantPlugin }) {
  const { updatePlugin, deletePlugin } = useAssistantExtensionActions();
  const [removing, setRemoving] = useState(false);

  return (
    <li className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-4 py-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-[var(--text-primary)]">
            {plugin.name}
            {plugin.version ? (
              <span className="ml-2 text-[0.75rem] font-normal text-[var(--text-muted)]">
                {plugin.version}
              </span>
            ) : null}
          </p>
          {plugin.description ? (
            <p className="mt-0.5 text-[0.8125rem] leading-relaxed text-[var(--text-secondary)]">
              {plugin.description}
            </p>
          ) : null}
        </div>
        <button
          type="button"
          aria-label={`Remove ${plugin.name}`}
          title="Remove"
          onClick={() => setRemoving(true)}
          className="shrink-0 rounded p-1.5 text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--signal-danger)]"
        >
          <Trash2 size={14} />
        </button>
      </div>

      {plugin.skills.length > 0 ? (
        <Listing title={plugin.skills.length === 1 ? '1 skill' : `${plugin.skills.length} skills`}>
          {plugin.skills.map((skill) => (
            <li key={skill.name}>
              <code className="text-[var(--text-primary)]">{skill.name}</code>
              {skill.description ? ` — ${skill.description}` : ''}
            </li>
          ))}
        </Listing>
      ) : null}
      {plugin.commands.length > 0 ? (
        <Listing
          title={plugin.commands.length === 1 ? '1 command' : `${plugin.commands.length} commands`}
        >
          {plugin.commands.map((command) => (
            <li key={command.name}>
              <code className="text-[var(--text-primary)]">/{command.name}</code>
              {command.description ? ` — ${command.description}` : ''}
            </li>
          ))}
        </Listing>
      ) : null}
      {plugin.notes.length > 0 ? (
        <ul className="mt-2 space-y-0.5 text-[0.75rem] leading-relaxed text-[var(--text-muted)]">
          {plugin.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}

      <div className="mt-1">
        <Toggle
          checked={plugin.enabled}
          onChange={(next) => updatePlugin.mutate({ id: plugin.id, enabled: next })}
          label={plugin.enabled ? 'On: its skills and commands are available' : 'Off'}
        />
      </div>

      <Dialog
        open={removing}
        onClose={() => setRemoving(false)}
        title={`Remove “${plugin.name}”?`}
        description="Its skills and commands go, and so do the connectors that came with it. Your chats are kept."
        footer={
          <>
            <Button variant="ghost" onClick={() => setRemoving(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={deletePlugin.isPending}
              onClick={() => deletePlugin.mutate(plugin.id)}
            >
              Remove plugin
            </Button>
          </>
        }
      />
    </li>
  );
}

function Listing({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-[0.8125rem] font-medium text-[var(--text-secondary)]">
        {title}
      </summary>
      <ul className="mt-1.5 space-y-1 pl-4 text-[0.75rem] leading-relaxed text-[var(--text-secondary)]">
        {children}
      </ul>
    </details>
  );
}
