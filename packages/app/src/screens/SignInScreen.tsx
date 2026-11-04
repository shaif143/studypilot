/**
 * Sign-in and registration, shown only by hosted deployments.
 *
 * The desktop application never reaches this screen: its backend does not
 * mount the account routes at all, and `useAuthStatus` reports that no
 * authentication is required.
 */
import { Button, Callout, Card, Field, Input, Tabs } from '@studypilot/ui';
import { AlertTriangle, BrainCircuit, Lock } from 'lucide-react';
import type { AuthProvider } from '@studypilot/shared';
import { type FormEvent, useState } from 'react';

import { describeError, useLogin, useRegister } from '../hooks/queries';
import { useNeonSignIn, useNeonSignUp } from '../hooks/useNeonAuth';

const MIN_PASSWORD_LENGTH = 10;

export function SignInScreen({
  registrationOpen,
  provider = 'builtin',
}: {
  registrationOpen: boolean;
  provider?: AuthProvider;
}) {
  // Both providers expose the same shape, so the form below is shared and only
  // the mutation behind it changes.
  const usesNeon = provider === 'neon';
  const builtinLogin = useLogin();
  const builtinRegister = useRegister();
  const neonLogin = useNeonSignIn();
  const neonRegister = useNeonSignUp();

  const login = usesNeon ? neonLogin : builtinLogin;
  const register = usesNeon ? neonRegister : builtinRegister;

  const [mode, setMode] = useState<'signin' | 'register'>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');

  const pending = login.isPending || register.isPending;
  const error = login.error ?? register.error;

  const tooShort = mode === 'register' && password.length > 0 && password.length < MIN_PASSWORD_LENGTH;
  const canSubmit = email.trim().length > 3 && password.length > 0 && !tooShort && !pending;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    // The auth status query is invalidated by the mutation clearing the cache,
    // which re-renders the gate and drops us into the application.
    if (mode === 'register') {
      await register
        .mutateAsync({ email, password, displayName, name: displayName })
        .catch(() => undefined);
    } else {
      await login.mutateAsync({ email, password }).catch(() => undefined);
    }
  };

  return (
    <div className="flex min-h-full items-center justify-center bg-[var(--surface-page)] px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <BrainCircuit size={28} className="text-[var(--text-accent)]" />
          <h1 className="mt-2 text-lg font-semibold text-[var(--text-primary)]">StudyPilot</h1>
          <p className="mt-1 text-[0.8125rem] leading-relaxed text-[var(--text-secondary)]">
            Your lecture material, explained and examined — with citations you can check.
          </p>
        </div>

        <Card>
          {registrationOpen ? (
            <Tabs
              className="mb-4 w-full"
              value={mode}
              onChange={(next) => {
                setMode(next);
                login.reset();
                register.reset();
              }}
              options={[
                { value: 'signin' as const, label: 'Sign in' },
                { value: 'register' as const, label: 'Create account' },
              ]}
            />
          ) : null}

          <form onSubmit={submit} className="space-y-3">
            {error ? (
              <Callout tone="danger" icon={<AlertTriangle size={14} />}>
                {describeError(error)}
              </Callout>
            ) : null}

            <Field label="Email">
              <Input
                type="email"
                autoComplete="email"
                autoFocus
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </Field>

            {mode === 'register' ? (
              <Field label="Name" hint="Optional.">
                <Input
                  type="text"
                  autoComplete="name"
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                />
              </Field>
            ) : null}

            <Field
              label="Password"
              hint={
                mode === 'register'
                  ? `At least ${MIN_PASSWORD_LENGTH} characters. A short phrase works well.`
                  : undefined
              }
              error={tooShort ? `At least ${MIN_PASSWORD_LENGTH} characters.` : undefined}
            >
              <Input
                type="password"
                autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </Field>

            <Button
              type="submit"
              variant="primary"
              className="w-full"
              loading={pending}
              disabled={!canSubmit}
            >
              {mode === 'register' ? 'Create account' : 'Sign in'}
            </Button>
          </form>

          {!registrationOpen && mode === 'signin' ? (
            <p className="mt-3 text-center text-[0.75rem] text-[var(--text-muted)]">
              Registration is closed on this instance.
            </p>
          ) : null}
        </Card>

        <Callout tone="neutral" className="mt-4" icon={<Lock size={14} />}>
          Your uploaded material is private to your account. Nothing is sent to an AI provider
          unless you turn that on in Settings.
        </Callout>

        {usesNeon ? (
          <p className="mt-3 text-center text-[0.6875rem] leading-relaxed text-[var(--text-muted)]">
            Sign-in is handled by Neon Auth. Your password is never seen or stored by StudyPilot.
          </p>
        ) : null}
      </div>
    </div>
  );
}
