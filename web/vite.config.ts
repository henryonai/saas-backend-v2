import path from 'node:path';
import tailwindcss from '@tailwindcss/vite';
import { tanstackRouter } from '@tanstack/router-plugin/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// The HTTP API allows the app's origin (CorsOrigin in template.yaml), so the
// app calls it directly with VITE_API_URL: no dev proxy needed.
export default defineConfig({
  // Options (routesDirectory, autoCodeSplitting, ...) live in tsr.config.json,
  // shared with the `tsr generate` CLI used before typecheck and build.
  plugins: [tanstackRouter(), react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    // template.yaml's AppUrl default: checkout and invitation emails link here
    port: 5173,
    strictPort: true,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
