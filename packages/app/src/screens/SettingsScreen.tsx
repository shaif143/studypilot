/**
 * Settings: model providers, learning preferences, privacy and data controls.
 *
 * The provider list is reported by the backend, including whether each one is
 * actually available on this machine and whether it transmits course material
 * off the device. Nothing here claims a capability the installation does not
 * have: an uninstalled local model says so, and says how to install it.
 */
import type { AssignmentPolicy, ExplanationLevel, Theme } from '@studypilot/shared';
import {
  Badge,
  Button,
  Callout,
  Card,
  Dialog,
  Field,
  Input,
  SectionHeading,
  Select,
  Skeleton,
  Tabs,
  Toggle,
} from '@studypilot/ui';
import {
  AlertTriangle,
  CheckCircle2,
  Cloud,
  CloudOff,
  Download,
  Info,
  KeyRound,
  LogOut,
  Trash2,
  XCircle,
} from 'lucide-react';
import { useState } from 'react';

import { ErrorPanel, PageBody, PageHeader } from '../components/AppShell';
import { useApp } from '../context';
import { useNeonSignOut } from '../hooks/useNeonAuth';
import {
  describeError,
  useAuthStatus,
  useDeleteAllData,
  useHealth,
  useLogout,
  useDeleteProviderKey,
  useProviderKeys,
  useProviders,
  useSetProviderKey,
  useSettings,
  useUpdateSettings,
} from '../hooks/queries';

export function SettingsScreen() {
  const { theme, setTheme, platform, client } = useApp();
  const settings = useSettings();
  const providers = useProviders();
  const health = useHealth();
  const update = useUpdateSettings();
  const deleteAll = useDeleteAllData();
  const auth = useAuthStatus();
  const builtinLogout = useLogout();
  const neonLogout = useNeonSignOut();
  // Sign-out has to go through whichever provider holds the session.
  const logout = auth.data?.provider === 'neon' ? neonLogout : builtinLogout;

  const [confirmDelete, setConfirmDelete] = useState(false);
  const [exporting, setExporting] = useState(false);

  const value = settings.data;

  const exportData = async () => {
    setExporting(true);
    try {
      const payload = await client.exportData();
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `studypilot-export-${new Date().toISOString().slice(0, 10)}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Settings"
        description="How StudyPilot works, what it is allowed to send anywhere, and what it stores."
      />
      <PageBody>
        {settings.isPending ? (
          <div className="space-y-4">
            {Array.from({ length: 3 }, (_, i) => (
              <Skeleton key={i} className="h-40" />
            ))}
          </div>
        ) : !value ? (
          <ErrorPanel
            message={describeError(settings.error)}
            onRetry={() => void settings.refetch()}
          />
        ) : (
          <div className="max-w-3xl space-y-4">
            {update.error ? <ErrorPanel message={describeError(update.error)} /> : null}

            {/* --- Account (hosted deployments only) ---------------------- */}
            {auth.data?.requires_authentication ? (
              <Card>
                <SectionHeading
                  title="Account"
                  description={
                    auth.data.provider === 'neon'
                      ? 'Signed in through Neon Auth. Your material is private to this account.'
                      : 'Your material is private to this account.'
                  }
                  action={
                    <Button
                      size="sm"
                      icon={<LogOut size={14} />}
                      loading={logout.isPending}
                      onClick={() => void logout.mutateAsync().catch(() => undefined)}
                    >
                      Sign out
                    </Button>
                  }
                />
                {auth.data.account ? (
                  <p className="mt-2 text-[0.8125rem] text-[var(--text-secondary)]">
                    Signed in as{' '}
                    <span className="font-medium text-[var(--text-primary)]">
                      {auth.data.account.email}
                    </span>
                  </p>
                ) : null}
              </Card>
            ) : null}

            {/* --- AI providers ------------------------------------------- */}
            <Card>
              <SectionHeading
                title="AI model providers"
                description="What this installation can actually run, and what leaves this device."
              />
              <ul className="mt-3 space-y-2">
                {providers.data?.llm.map((provider) => {
                  const active = provider.key === providers.data?.active_llm;
                  return (
                    <li
                      key={provider.key}
                      className={[
                        'rounded-[var(--radius-control)] border px-3 py-2.5',
                        active
                          ? 'border-[var(--color-accent-400)] bg-[var(--surface-selected)]'
                          : 'border-[var(--border-subtle)]',
                      ].join(' ')}
                    >
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-[0.8125rem] font-medium text-[var(--text-primary)]">
                          {provider.label}
                        </span>
                        {active ? <Badge tone="accent">active</Badge> : null}
                        {provider.available ? (
                          <Badge tone="grounded" icon={<CheckCircle2 size={11} />}>
                            available
                          </Badge>
                        ) : (
                          <Badge tone="neutral" icon={<XCircle size={11} />}>
                            not set up
                          </Badge>
                        )}
                        {provider.sends_data_off_device ? (
                          <Badge tone="caution" icon={<Cloud size={11} />}>
                            sends data off this device
                          </Badge>
                        ) : (
                          <Badge tone="grounded" icon={<CloudOff size={11} />}>
                            stays on this device
                          </Badge>
                        )}
                        {!provider.generative ? (
                          <Badge tone="neutral">no generation</Badge>
                        ) : null}
                      </div>
                      <p className="mt-1 text-[0.75rem] leading-relaxed text-[var(--text-secondary)]">
                        {provider.detail}
                      </p>
                      {provider.available && !active ? (
                        <Button
                          size="sm"
                          className="mt-2"
                          loading={update.isPending}
                          onClick={() => update.mutate({ preferred_llm_provider: provider.key })}
                        >
                          Use this provider
                        </Button>
                      ) : null}
                      {active && settings.data?.preferred_llm_provider ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          className="mt-2"
                          loading={update.isPending}
                          onClick={() => update.mutate({ preferred_llm_provider: '' })}
                        >
                          Back to the default
                        </Button>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
              <Callout tone="info" className="mt-3" icon={<Info size={14} />}>
                Pick the provider that answers for you here. Cloud providers also need{' '}
                <em>Allow cloud AI processing</em> below.
              </Callout>
            </Card>

            <ProviderKeysCard />

            {/* --- Embeddings -------------------------------------------- */}
            <Card>
              <SectionHeading
                title="Search quality"
                description="Which embedding model powers semantic search."
              />
              <ul className="mt-3 space-y-2">
                {providers.data?.embedding.map((provider) => (
                  <li key={provider.key} className="flex items-start gap-2.5">
                    {provider.available ? (
                      <CheckCircle2 size={14} className="mt-0.5 text-[var(--signal-grounded)]" />
                    ) : (
                      <XCircle size={14} className="mt-0.5 text-[var(--text-muted)]" />
                    )}
                    <div className="min-w-0">
                      <p className="text-[0.8125rem] font-medium text-[var(--text-primary)]">
                        {provider.label}
                      </p>
                      <p className="text-[0.75rem] leading-relaxed text-[var(--text-secondary)]">
                        {provider.detail}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
              {health.data ? (
                <p className="mt-3 text-[0.75rem] text-[var(--text-muted)]">
                  Currently indexing with <code>{health.data.runtime.embedding_model}</code> (
                  {health.data.runtime.embedding_dimensions} dimensions),{' '}
                  {health.data.runtime.vector_count ?? 'an unknown number of'} passages indexed,
                  keyword search via {health.data.runtime.sparse_backend}.
                  {health.data.runtime.database_error ? (
                    <>
                      {' '}
                      The passage count could not be read from the database:{' '}
                      <code>{health.data.runtime.database_error}</code>
                    </>
                  ) : null}
                </p>
              ) : null}
            </Card>

            {/* --- Learning ---------------------------------------------- */}
            <Card>
              <SectionHeading title="Learning preferences" />
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <Field label="Appearance">
                  <Tabs
                    value={theme}
                    onChange={(next: Theme) => {
                      setTheme(next);
                      update.mutate({ theme: next });
                    }}
                    options={[
                      { value: 'system' as Theme, label: 'System' },
                      { value: 'light' as Theme, label: 'Light' },
                      { value: 'dark' as Theme, label: 'Dark' },
                    ]}
                  />
                </Field>
                <Field label="Default explanation depth">
                  <Select
                    value={value.explanation_level}
                    onChange={(event) =>
                      update.mutate({
                        explanation_level: event.target.value as ExplanationLevel,
                      })
                    }
                  >
                    <option value="simple">Simple</option>
                    <option value="intermediate">Intermediate</option>
                    <option value="advanced">Advanced</option>
                  </Select>
                </Field>
                <Field
                  label="Assignment guidance policy"
                  hint="Socratic gives one hint at a time and never writes the answer."
                >
                  <Select
                    value={value.assignment_policy}
                    onChange={(event) =>
                      update.mutate({
                        assignment_policy: event.target.value as AssignmentPolicy,
                      })
                    }
                  >
                    <option value="socratic">Socratic hints only</option>
                    <option value="explain">Full explanations</option>
                    <option value="off">Disabled</option>
                  </Select>
                </Field>
                <Field
                  label="Quiz checkpoint"
                  hint="Pages read before the reader offers a quiz."
                >
                  <Input
                    type="number"
                    min={1}
                    max={100}
                    value={value.quiz_checkpoint_slides}
                    onChange={(event) => {
                      const next = Number.parseInt(event.target.value, 10);
                      if (Number.isFinite(next) && next >= 1) {
                        update.mutate({ quiz_checkpoint_slides: next });
                      }
                    }}
                  />
                </Field>
              </div>
            </Card>

            {/* --- Privacy ----------------------------------------------- */}
            <Card>
              <SectionHeading
                title="Privacy"
                description="Every option here is off until you turn it on."
              />
              <div className="mt-1 divide-y divide-[var(--border-subtle)]">
                <Toggle
                  checked={value.allow_cloud_processing}
                  onChange={(next) => update.mutate({ allow_cloud_processing: next })}
                  label="Allow cloud AI processing"
                  description="Required before any hosted provider can be used. Each request must also opt in, so turning this on does not start sending anything by itself."
                />
                <Toggle
                  checked={value.allow_cloud_vision}
                  onChange={(next) => update.mutate({ allow_cloud_vision: next })}
                  label="Allow sending images of pages to a vision model"
                  description="For diagrams that cannot be read as text. Not used in this release."
                />
                <Toggle
                  checked={value.allow_external_search}
                  onChange={(next) => update.mutate({ allow_external_search: next })}
                  label="Allow looking beyond your material"
                  description="When your documents do not cover a question. Externally sourced claims are labelled. Not used in this release."
                />
                <Toggle
                  checked={value.allow_attention_tracking}
                  onChange={(next) => update.mutate({ allow_attention_tracking: next })}
                  label="Attention estimation (experimental)"
                  description="Camera-based gaze estimation is NOT implemented in this release; only simulated test data is recorded. Dwell time is not comprehension."
                />
                <Toggle
                  checked={value.allow_telemetry}
                  onChange={(next) => update.mutate({ allow_telemetry: next })}
                  label="Share anonymous usage statistics"
                  description="No telemetry is collected or transmitted in this release."
                />
                <Toggle
                  checked={value.allow_federated_participation}
                  onChange={(next) => update.mutate({ allow_federated_participation: next })}
                  label="Join federated model improvement (research)"
                  description="Not implemented. When it exists it will train on this device only and share model updates, never your documents — and that will not make it anonymous."
                />
              </div>
            </Card>

            {/* --- Data -------------------------------------------------- */}
            <Card>
              <SectionHeading
                title="Your data"
                description={
                  platform.kind === 'desktop'
                    ? 'Stored on this Mac, in your application support directory.'
                    : 'Stored by the backend this website is configured to talk to.'
                }
              />
              {health.data ? (
                <p className="mt-2 break-all text-[0.75rem] text-[var(--text-muted)]">
                  <code>{health.data.runtime.data_dir}</code>
                </p>
              ) : null}
              <div className="mt-3 flex flex-wrap gap-2">
                <Button icon={<Download size={14} />} loading={exporting} onClick={() => void exportData()}>
                  Export everything as JSON
                </Button>
                <Button
                  variant="danger"
                  icon={<Trash2 size={14} />}
                  onClick={() => setConfirmDelete(true)}
                >
                  Delete all my data
                </Button>
              </div>
            </Card>
          </div>
        )}

        <Dialog
          open={confirmDelete}
          onClose={() => setConfirmDelete(false)}
          title="Delete everything?"
          description="Your profile, courses, uploaded files, search indexes, conversations, flashcards, quizzes, notes and progress are all permanently removed from this device. This cannot be undone."
          footer={
            <>
              <Button onClick={() => setConfirmDelete(false)}>Cancel</Button>
              <Button
                variant="danger"
                loading={deleteAll.isPending}
                onClick={async () => {
                  await deleteAll.mutateAsync();
                  setConfirmDelete(false);
                }}
              >
                Delete everything
              </Button>
            </>
          }
        >
          <Callout tone="danger" icon={<AlertTriangle size={14} />}>
            Export your data first if you might want it later.
          </Callout>
        </Dialog>
      </PageBody>
    </>
  );
}

const KEY_LABELS: Record<string, { label: string; hint: string; placeholder: string }> = {
  anthropic: {
    label: 'Claude (Anthropic) API key',
    hint: 'From console.anthropic.com → API keys.',
    placeholder: 'sk-ant-…',
  },
  gemini: {
    label: 'Gemini (Google) API key',
    hint: 'From aistudio.google.com → Get API key.',
    placeholder: 'AIza…',
  },
};

/**
 * Keys for cloud providers, on the desktop only.
 *
 * The engine runs on this Mac, so a cloud key has to be here too. It goes
 * into the macOS Keychain — the API reports where — and is never shown back:
 * the card only says whether one is set. On a hosted site the API answers
 * `editable: false` and this card does not render; keys there belong to
 * whoever operates it.
 */
function ProviderKeysCard() {
  const keys = useProviderKeys();
  const setKey = useSetProviderKey();
  const removeKey = useDeleteProviderKey();
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  if (!keys.data?.editable) return null;
  const storage =
    keys.data.storage === 'keychain'
      ? 'the macOS Keychain — encrypted, unlocked with your login, never a plain file'
      : keys.data.storage === 'file'
        ? 'a file only your account can read (no system keychain was available)'
        : `your system's secret store (${keys.data.storage})`;

  return (
    <Card>
      <SectionHeading
        title="Keys for cloud providers"
        description={`The engine runs on this Mac, so a cloud provider's key has to be here too. It is kept in ${storage}.`}
      />
      <ul className="mt-3 space-y-3">
        {keys.data.keys.map((entry) => {
          const copy = KEY_LABELS[entry.provider] ?? {
            label: `${entry.provider} API key`,
            hint: '',
            placeholder: '',
          };
          const draft = drafts[entry.provider] ?? '';
          return (
            <li
              key={entry.provider}
              className="rounded-[var(--radius-control)] border border-[var(--border-subtle)] px-3 py-2.5"
            >
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-[0.8125rem] font-medium text-[var(--text-primary)]">
                  {copy.label}
                </span>
                {entry.configured ? (
                  <Badge tone="grounded" icon={<KeyRound size={11} />}>
                    {entry.from_environment ? 'set by the environment' : 'set'}
                  </Badge>
                ) : (
                  <Badge tone="neutral">not set</Badge>
                )}
              </div>
              {entry.from_environment ? (
                <p className="mt-1 text-[0.75rem] text-[var(--text-secondary)]">
                  This key was set outside the app, so it cannot be changed here.
                </p>
              ) : (
                <form
                  className="mt-2 flex flex-wrap items-end gap-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    if (!draft.trim()) return;
                    setKey.mutate(
                      { provider: entry.provider, key: draft.trim() },
                      {
                        onSuccess: () =>
                          setDrafts((current) => ({ ...current, [entry.provider]: '' })),
                      },
                    );
                  }}
                >
                  <Field label={entry.configured ? 'Replace the key' : 'Paste the key'} hint={copy.hint} className="min-w-[16rem] flex-1">
                    <Input
                      type="password"
                      autoComplete="off"
                      spellCheck={false}
                      value={draft}
                      placeholder={copy.placeholder}
                      onChange={(event) =>
                        setDrafts((current) => ({ ...current, [entry.provider]: event.target.value }))
                      }
                    />
                  </Field>
                  <Button
                    type="submit"
                    size="sm"
                    variant="primary"
                    disabled={!draft.trim()}
                    loading={setKey.isPending && setKey.variables?.provider === entry.provider}
                  >
                    Save
                  </Button>
                  {entry.configured ? (
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      loading={removeKey.isPending && removeKey.variables === entry.provider}
                      onClick={() => removeKey.mutate(entry.provider)}
                    >
                      Remove
                    </Button>
                  ) : null}
                </form>
              )}
            </li>
          );
        })}
      </ul>
      {setKey.error ? (
        <Callout tone="danger" className="mt-3" icon={<AlertTriangle size={14} />}>
          {describeError(setKey.error)}
        </Callout>
      ) : null}
      {removeKey.error ? (
        <Callout tone="danger" className="mt-3" icon={<AlertTriangle size={14} />}>
          {describeError(removeKey.error)}
        </Callout>
      ) : null}
      <Callout tone="info" className="mt-3" icon={<Info size={14} />}>
        After saving, the provider shows as available above — choose <em>Use this provider</em>{' '}
        and turn on <em>Allow cloud AI processing</em> below. The key is only ever sent to that
        provider, with the text you ask about.
      </Callout>
    </Card>
  );
}
