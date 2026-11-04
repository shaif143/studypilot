/**
 * Neon Auth session state for the shared application.
 *
 * The auth client itself is supplied by the host through the platform
 * adapter, so this hook — and everything that uses it — stays free of any
 * dependency on `@neon/auth`. On the desktop, where no client is supplied,
 * every operation reports that Neon Auth is unavailable instead of throwing.
 */
import type { NeonAuthUser } from '@studypilot/shared';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { useApp } from '../context';
import { resetSessionCache } from './queries';

/** Turn a Neon `{ data, error }` result into a thrown Error, or return data. */
function unwrap<T>(result: { data?: T | null; error?: { message?: string } | null }): T {
  if (result.error) {
    throw new Error(result.error.message || 'Neon Auth rejected that request.');
  }
  if (result.data === undefined || result.data === null) {
    throw new Error('Neon Auth returned no data.');
  }
  return result.data;
}

export function useNeonAuthAvailable(): boolean {
  return Boolean(useApp().platform.neonAuth);
}

export function useNeonSignIn() {
  const { platform } = useApp();
  const queryClient = useQueryClient();
  return useMutation<{ user?: NeonAuthUser }, Error, { email: string; password: string }>({
    mutationFn: async ({ email, password }) => {
      const client = platform.neonAuth;
      if (!client) throw new Error('Neon Auth is not configured for this app.');
      return unwrap(await client.signIn.email({ email, password }));
    },
    // Everything cached belonged to the signed-out state. The status refetch
    // is awaited so the gate re-evaluates before this resolves — without it
    // the sign-in succeeds but the screen never changes.
    onSuccess: () => resetSessionCache(queryClient),
  });
}

export function useNeonSignUp() {
  const { platform } = useApp();
  const queryClient = useQueryClient();
  return useMutation<
    { user?: NeonAuthUser },
    Error,
    { email: string; password: string; name?: string }
  >({
    mutationFn: async ({ email, password, name }) => {
      const client = platform.neonAuth;
      if (!client) throw new Error('Neon Auth is not configured for this app.');
      return unwrap(await client.signUp.email({ email, password, name }));
    },
    onSuccess: () => resetSessionCache(queryClient),
  });
}

export function useNeonSignOut() {
  const { platform } = useApp();
  const queryClient = useQueryClient();
  return useMutation<void, Error, void>({
    mutationFn: async () => {
      const client = platform.neonAuth;
      if (!client) throw new Error('Neon Auth is not configured for this app.');
      await client.signOut();
    },
    // On settle, not just success: a failed sign-out must still drop the
    // local cache rather than leave another account's data on screen.
    onSettled: () => resetSessionCache(queryClient),
  });
}
