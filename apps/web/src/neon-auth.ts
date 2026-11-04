/**
 * The Neon Auth client for the website.
 *
 * Constructed here rather than in `packages/app` so the shared application —
 * and therefore the macOS build — never imports a browser auth library it has
 * no use for. The desktop host simply does not supply one.
 *
 * `VITE_NEON_AUTH_URL` is the branch's Auth URL. `neon env pull` writes
 * `NEON_AUTH_BASE_URL`; the `VITE_` alias has to be set separately, because
 * only `VITE_`-prefixed variables are exposed to browser code.
 */
import type { NeonAuthClient } from '@studypilot/shared';

const AUTH_URL = import.meta.env.VITE_NEON_AUTH_URL ?? '';

let client: NeonAuthClient | null = null;
let loading: Promise<NeonAuthClient | null> | null = null;

/**
 * Load the client on demand.
 *
 * Imported dynamically so a deployment using the built-in accounts never
 * downloads the auth library at all.
 */
async function load(): Promise<NeonAuthClient | null> {
  if (!AUTH_URL) return null;
  if (client) return client;
  loading ??= (async () => {
    const [{ createAuthClient }, { BetterAuthReactAdapter }] = await Promise.all([
      import('@neon/auth'),
      import('@neon/auth/react/adapters'),
    ]);
    client = createAuthClient(AUTH_URL, {
      adapter: BetterAuthReactAdapter(),
    }) as unknown as NeonAuthClient;
    return client;
  })();
  return loading;
}

/** True when this build is configured to sign in through Neon Auth. */
export const neonAuthConfigured = Boolean(AUTH_URL);

/**
 * A synchronous facade over the lazily-loaded client.
 *
 * The platform adapter is constructed before any user interaction, so each
 * method awaits the import at call time rather than at module load.
 */
export const neonAuthClient: NeonAuthClient = {
  signUp: {
    async email(input) {
      const instance = await load();
      if (!instance) throw new Error('Neon Auth is not configured for this site.');
      return instance.signUp.email(input);
    },
  },
  signIn: {
    async email(input) {
      const instance = await load();
      if (!instance) throw new Error('Neon Auth is not configured for this site.');
      return instance.signIn.email(input);
    },
  },
  async signOut() {
    const instance = await load();
    return instance ? instance.signOut() : undefined;
  },
  async getSession() {
    const instance = await load();
    return instance ? instance.getSession() : { data: null };
  },
  async token() {
    const instance = await load();
    return instance ? instance.token() : { data: null };
  },
};

/**
 * The current bearer token, or null when nobody is signed in.
 *
 * Neon Auth tokens last 15 minutes; the client refreshes them, so this is
 * called per request rather than cached by the API client.
 */
export async function currentBearerToken(): Promise<string | null> {
  if (!AUTH_URL) return null;
  try {
    const result = await neonAuthClient.token();
    return result.data?.token ?? null;
  } catch {
    // Not signed in, or the service is unreachable. The backend answers 401
    // and the app shows its sign-in screen, which is the right outcome.
    return null;
  }
}
