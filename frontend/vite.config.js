import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// VITE_API_BASE_URL is read by src/api.js via import.meta.env and inlined into the
// bundle at BUILD time. Changing it therefore requires a Vercel rebuild, not just a
// new environment variable.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, host: '127.0.0.1' },
  preview: { port: 4173 },
  build: { outDir: 'dist', sourcemap: false, chunkSizeWarningLimit: 1200 },
})
