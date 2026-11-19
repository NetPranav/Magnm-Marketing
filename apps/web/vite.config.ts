import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

const designDir = fileURLToPath(new URL('../../packages/magnm-design', import.meta.url));
const designReactEntry = fileURLToPath(
  new URL('../../packages/magnm-design/react/index.ts', import.meta.url),
);
const reactDir = fileURLToPath(new URL('./node_modules/react', import.meta.url));
const reactDomDir = fileURLToPath(new URL('./node_modules/react-dom', import.meta.url));
const engineUrl = process.env.MAGNM_ENGINE_URL || 'http://127.0.0.1:8787';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: [
      { find: '@magnm/design/react', replacement: designReactEntry },
      { find: '@magnm/design', replacement: designDir },
      { find: 'react/jsx-dev-runtime', replacement: `${reactDir}/jsx-dev-runtime.js` },
      { find: 'react/jsx-runtime', replacement: `${reactDir}/jsx-runtime.js` },
      { find: 'react', replacement: reactDir },
      { find: 'react-dom', replacement: reactDomDir },
    ],
  },
  server: {
    fs: {
      allow: ['.', designDir],
    },
    proxy: {
      '/api': {
        target: engineUrl,
        changeOrigin: true,
      },
      '/pitch': {
        target: engineUrl,
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: false,
  },
});
