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
  LogOut,
  Trash2,
  XCircle,
} from 'lucide-react';
import { useState } from 'react';

import { ErrorPanel, PageBody, PageHeader } from '../components/AppShell';
import { useApp } from '../context';
import {
  describeError,
  useAuthStatus,
  useDeleteAllData,
  useHealth,
  useLogout,
  useProviders,
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
  const logout = useLogout();

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
                  description="Your material is private to this account."
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
                    </li>
                  );
                })}
              </ul>
              <Callout tone="info" className="mt-3" icon={<Info size={14} />}>
                The provider is chosen by backend configuration, not in the browser, so API keys
                never reach the app bundle. Set <code>STUDYPILOT_LLM_PROVIDER</code> and the
                matching key in the backend environment. See <code>docs/ARCHITECTURE.md</code>.
              </Callout>
            </Card>

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
                  {health.data.runtime.vector_count} passages indexed, keyword search via{' '}
                  {health.data.runtime.sparse_backend}.
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
