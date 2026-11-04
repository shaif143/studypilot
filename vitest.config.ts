import react from '@vitejs/plugin-react';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

/**
 * Frontend unit tests.
 *
 * The workspace packages are consumed as source, so the same aliases the two
 * hosts use are declared here rather than relying on a build step.
 */
const ROOT = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@studypilot/shared': resolve(ROOT, 'packages/shared/src/index.ts'),
      '@studypilot/api-client': resolve(ROOT, 'packages/api-client/src/index.ts'),
      '@studypilot/ui': resolve(ROOT, 'packages/ui/src/index.ts'),
      '@studypilot/app': resolve(ROOT, 'packages/app/src/index.ts'),
    },
  },
  test: {
    environment: 'happy-dom',
    globals: true,
    setupFiles: ['./tests/frontend/setup.ts'],
    include: ['tests/frontend/**/*.test.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      include: ['packages/*/src/**/*.{ts,tsx}'],
      exclude: ['**/*.d.ts', 'packages/ui/src/theme.css'],
    },
  },
});
