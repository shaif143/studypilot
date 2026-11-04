import { defineConfig } from 'vite';

import { baseConfig } from '../../vite.base';

/**
 * Tauri expects a fixed dev-server port and must fail loudly if it is taken,
 * otherwise the shell would load a different app than the one just started.
 */
export default defineConfig({
  ...baseConfig({ port: 5174, strictPort: true }),
  // The webview talks to the sidecar directly using the token from the shell,
  // so no dev proxy is involved; clearing it avoids a misleading second path.
  server: {
    ...baseConfig({ port: 5174, strictPort: true }).server,
    proxy: undefined,
  },
  clearScreen: false,
});
