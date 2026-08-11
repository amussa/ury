import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';

const currentDirectory = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  base: '/assets/ury/waiter/',
  plugins: [react()],
  resolve: {
    alias: {
      '@': resolve(currentDirectory, './src'),
    },
    dedupe: ['react', 'react-dom'],
  },
  server: {
    fs: {
      allow: ['..'],
    },
  },
  build: {
    outDir: '../ury/public/waiter',
    emptyOutDir: true,
  },
});
