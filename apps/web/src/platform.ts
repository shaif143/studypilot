import type { Platform } from '@studypilot/shared';

import { currentBearerToken, neonAuthClient, neonAuthConfigured } from './neon-auth';

/**
 * The website's platform adapter.
 *
 * `apiBaseUrl` is empty: requests go to the same origin, where the Vite dev
 * server (in development) or a reverse proxy (in production) forwards them to
 * the API and attaches the credential server-side. `getApiToken` therefore
 * returns null, and no secret is ever present in the browser bundle.
 *
 * `VITE_API_BASE_URL` can point the site at an API on another origin during
 * development; that path has no token either, so the API must be configured to
 * accept the request another way.
 */
export const webPlatform: Platform = {
  kind: 'web',
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL ?? '',
  hasSecureStorage: false,
  async getApiToken() {
    return null;
  },
  async openExternal(url: string) {
    window.open(url, '_blank', 'noopener,noreferrer');
  },
  // Only present when this build points at a Neon Auth branch; otherwise the
  // backend's own account system handles identity and these stay undefined.
  ...(neonAuthConfigured
    ? { neonAuth: neonAuthClient, getBearerToken: currentBearerToken }
    : {}),
};
