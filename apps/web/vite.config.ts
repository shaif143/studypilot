import { defineConfig } from 'vite';

import { baseConfig } from '../../vite.base';

export default defineConfig(baseConfig({ port: 5173 }));
