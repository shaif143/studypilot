/**
 * The platform adapter.
 *
 * This is the only interface that differs between the website and the macOS
 * application, which is what lets both hosts run the identical application
 * code in `@studypilot/app`. The web host talks to a hosted (or dev-proxied)
 * API and never sees the local token; the desktop host asks the Tauri shell
 * for the sidecar's address and token.
 */
/**
 * The result shape every `@neon/auth` call returns.
 *
 * Declared structurally rather than imported, so `packages/app` carries no
 * dependency on `@neon/auth` and the desktop bundle never pulls in a browser
 * auth client it cannot use.
 */
export interface NeonAuthResult<T = unknown> {
  data?: T | null;
  error?: { message?: string; code?: string; status?: number } | null;
}

export interface NeonAuthUser {
  id: string;
  email: string;
  name?: string;
  emailVerified?: boolean;
}

/** The subset of the Neon Auth client this application uses. */
export interface NeonAuthClient {
  signUp: {
    email(input: {
      email: string;
      password: string;
      name?: string;
    }): Promise<NeonAuthResult<{ user?: NeonAuthUser }>>;
  };
  signIn: {
    email(input: {
      email: string;
      password: string;
    }): Promise<NeonAuthResult<{ user?: NeonAuthUser }>>;
  };
  signOut(): Promise<unknown>;
  getSession(): Promise<NeonAuthResult<{ user?: NeonAuthUser } | null>>;
  /** Mints a short-lived (15 minute) EdDSA JWT for the backend. */
  token(): Promise<NeonAuthResult<{ token?: string }>>;
}

export interface Platform {
  /** 'web' | 'desktop' — used only for host-specific affordances and copy. */
  readonly kind: 'web' | 'desktop';
  /** Base URL for API calls. Relative ('' ) means same-origin via a proxy. */
  readonly apiBaseUrl: string;
  /**
   * Resolve the API token, if this host supplies one.
   *
   * The web host returns `null`: its dev server and its production reverse
   * proxy attach credentials server-side, so no secret is ever present in the
   * browser bundle. The desktop host returns the token the Rust shell
   * generated for the sidecar.
   */
  getApiToken(): Promise<string | null>;
  /**
   * A bearer token for the current request, when identity is delegated.
   *
   * Called per request rather than cached: Neon Auth tokens expire after 15
   * minutes, and the auth client does its own caching and refresh behind this
   * call. Hosts with no delegated identity simply omit it.
   */
  getBearerToken?(): Promise<string | null>;

  /**
   * The Neon Auth client, when this host signs users in through Neon.
   *
   * Supplied by the host rather than constructed in the shared application,
   * because it is a browser-only concern: the desktop app has one local
   * profile and nothing to sign in to.
   */
  readonly neonAuth?: NeonAuthClient;

  /** Open an external link in the user's real browser rather than in-app. */
  openExternal(url: string): Promise<void>;
  /** True when the host can store secrets in an OS keychain. */
  readonly hasSecureStorage: boolean;
  /**
   * A native folder picker, on hosts that have the file system at hand.
   * Returns the absolute path, or null when the user cancels. The web host
   * has no file system and omits it.
   */
  pickFolder?(): Promise<string | null>;
}

export const webPlatformDefaults = {
  kind: 'web',
  hasSecureStorage: false,
} as const;
