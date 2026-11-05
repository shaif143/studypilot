/**
 * Shared Vite configuration for both hosts.
 *
 * Two things are worth explaining.
 *
 * **Workspace aliases.** The internal packages are consumed as source rather
 * than as built artefacts. There is no build-order to orchestrate, changes
 * hot-reload across package boundaries, and a single `tsc --build` typechecks
 * the lot.
 *
 * **The dev proxy injects the API token.** The local backend requires a shared
 * secret. Rather than shipping it to the browser, the dev server reads the
 * token file itself and attaches the header as it forwards each request, so the
 * credential stays on the server side of the proxy — the same arrangement a
 * production deployment would use with a reverse proxy.
 */
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Plugin, ProxyOptions, UserConfig } from 'vite';

const ROOT = dirname(fileURLToPath(import.meta.url));

const TOKEN_HEADER = 'x-studypilot-token';

/** Where the backend writes its token, mirroring `app.config.default_data_dir`. */
function tokenPath(): string {
  const override = process.env.STUDYPILOT_DATA_DIR;
  if (override) return resolve(override, 'api_token');
  if (process.platform === 'darwin') {
    return resolve(homedir(), 'Library', 'Application Support', 'StudyPilot', 'api_token');
  }
  const xdg = process.env.XDG_DATA_HOME;
  return xdg
    ? resolve(xdg, 'StudyPilot', 'api_token')
    : resolve(homedir(), '.local', 'share', 'StudyPilot', 'api_token');
}

function readToken(): string | null {
  // Read on every request rather than caching: the backend may not have been
  // started yet when the dev server boots, and restarting it can rotate the file.
  if (process.env.STUDYPILOT_API_TOKEN) return process.env.STUDYPILOT_API_TOKEN;
  const path = tokenPath();
  if (!existsSync(path)) return null;
  const token = readFileSync(path, 'utf8').trim();
  return token || null;
}

function apiProxy(target: string): Record<string, ProxyOptions> {
  const options: ProxyOptions = {
    target,
    changeOrigin: false,
    configure(proxy) {
      proxy.on('proxyReq', (proxyRequest) => {
        const token = readToken();
        if (token) proxyRequest.setHeader(TOKEN_HEADER, token);
      });
    },
  };
  return { '/api': options, '/healthz': options, '/openapi.json': options };
}

/** Warns once, clearly, when the backend has not been started. */
function backendHintPlugin(): Plugin {
  return {
    name: 'studypilot-backend-hint',
    configureServer(server) {
      if (readToken()) return;
      server.config.logger.warn(
        '\n  StudyPilot: no backend token found yet.\n' +
          '  Start the API in another terminal:  npm run dev:api\n' +
          '  The dev server reads the token on each request, so no restart is needed.\n',
      );
    },
  };
}

export interface BaseConfigOptions {
  /** Dev-server port. */
  port: number;
  /** Where the local FastAPI backend is listening. */
  apiTarget?: string;
  /** Build output directory, relative to the host app. */
  outDir?: string;
  /** Tauri needs a fixed port and must fail rather than pick another. */
  strictPort?: boolean;
}

export function baseConfig({
  port,
  // The same variable `scripts/dev-api.sh` honours, so a backend started on
  // another port (the end-to-end tests do this) is proxied without editing.
  apiTarget = `http://127.0.0.1:${process.env.STUDYPILOT_PORT ?? '8765'}`,
  outDir = 'dist',
  strictPort = false,
}: BaseConfigOptions): UserConfig {
  return {
    // `neon env pull` and `vercel env pull` both write to the repository root,
    // so that is where VITE_* variables live. Vite otherwise looks only in the
    // host app's own directory and would silently find nothing.
    envDir: ROOT,
    plugins: [react(), tailwindcss(), backendHintPlugin()],
    resolve: {
      alias: {
        '@studypilot/shared': resolve(ROOT, 'packages/shared/src/index.ts'),
        '@studypilot/api-client': resolve(ROOT, 'packages/api-client/src/index.ts'),
        '@studypilot/ui/theme.css': resolve(ROOT, 'packages/ui/src/theme.css'),
        '@studypilot/ui': resolve(ROOT, 'packages/ui/src/index.ts'),
        '@studypilot/app': resolve(ROOT, 'packages/app/src/index.ts'),
      },
    },
    server: {
      port,
      strictPort,
      host: '127.0.0.1',
      proxy: apiProxy(apiTarget),
      fs: {
        // The dev server must be able to read the workspace packages.
        allow: [ROOT],
      },
    },
    preview: { port: port + 1, proxy: apiProxy(apiTarget) },
    build: {
      outDir,
      emptyOutDir: true,
      target: 'es2022',
      sourcemap: true,
      rollupOptions: {
        output: {
          // Split the two heaviest dependency groups out of the entry chunk:
          // pdf.js is only needed in the reader, and the Markdown/KaTeX stack
          // only once an answer or a note is rendered.
          manualChunks(id: string) {
            if (id.includes('pdfjs-dist')) return 'pdf';
            if (
              id.includes('react-markdown') ||
              id.includes('remark-') ||
              id.includes('rehype-') ||
              id.includes('katex') ||
              id.includes('mdast') ||
              id.includes('micromark') ||
              id.includes('unist-')
            ) {
              return 'markdown';
            }
            return undefined;
          },
        },
      },
    },
    optimizeDeps: {
      // The worker is loaded as an asset URL, not pre-bundled.
      exclude: ['pdfjs-dist/build/pdf.worker.min.mjs'],
    },
  };
}
