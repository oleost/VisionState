import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// Relative base: the app is served below a dynamic Home Assistant Ingress path.
export default defineConfig({
  base: './',
  plugins: [svelte()],
  server: {
    proxy: { '/api': 'http://127.0.0.1:8099' },
  },
});
