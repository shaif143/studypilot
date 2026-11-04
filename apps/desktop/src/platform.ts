import type { Platform } from '@studypilot/shared';
import { invoke } from '@tauri-apps/api/core';
import { openUrl } from '@tauri-apps/plugin-opener';

/**
 * The macOS application's platform adapter.
 *
 * The Rust shell generates a random token, passes it to the Python sidecar as
 * an environment variable, and hands it to the webview on request. The token
 * therefore never touches disk in a location the webview can read, and the
 * local HTTP service is not usable by any other process that has not been given
 * the secret.
 */
interface BackendInfo {
  base_url: string;
  token: string;
}

let cached: Promise<BackendInfo> | null = null;

function backendInfo(): Promise<BackendInfo> {
  cached ??= invoke<BackendInfo>('backend_info');
  return cached;
}

export const desktopPlatform: Platform = {
  kind: 'desktop',
  // Resolved lazily below; the client only ever reads this after the first
  // getApiToken() call, which populates it.
  get apiBaseUrl() {
    return resolvedBaseUrl;
  },
  hasSecureStorage: true,
  async getApiToken() {
    const info = await backendInfo();
    resolvedBaseUrl = info.base_url;
    return info.token;
  },
  async openExternal(url: string) {
    await openUrl(url);
  },
};

let resolvedBaseUrl = 'http://127.0.0.1:8765';

/** Resolve the backend address before the app renders, so the first request works. */
export async function prepareDesktopPlatform(): Promise<void> {
  const info = await backendInfo();
  resolvedBaseUrl = info.base_url;
}
