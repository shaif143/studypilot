/**
 * The platform adapter.
 *
 * This is the only interface that differs between the website and the macOS
 * application, which is what lets both hosts run the identical application
 * code in `@studypilot/app`. The web host talks to a hosted (or dev-proxied)
 * API and never sees the local token; the desktop host asks the Tauri shell
 * for the sidecar's address and token.
 */
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
  /** Open an external link in the user's real browser rather than in-app. */
  openExternal(url: string): Promise<void>;
  /** True when the host can store secrets in an OS keychain. */
  readonly hasSecureStorage: boolean;
}

export const webPlatformDefaults = {
  kind: 'web',
  hasSecureStorage: false,
} as const;
