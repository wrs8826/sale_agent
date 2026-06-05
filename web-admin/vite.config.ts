import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/auth':          { target: 'http://127.0.0.1:5002', changeOrigin: true },
      '/users':         { target: 'http://127.0.0.1:5002', changeOrigin: true },
      '/agent':         { target: 'http://127.0.0.1:5002', changeOrigin: true },
      '/feedback':      { target: 'http://127.0.0.1:5002', changeOrigin: true },
      '/files':         { target: 'http://127.0.0.1:5002', changeOrigin: true },
      '/upload':        { target: 'http://127.0.0.1:5002', changeOrigin: true },
      '/ingest':        { target: 'http://127.0.0.1:5002', changeOrigin: true },
      '/query':         { target: 'http://127.0.0.1:5002', changeOrigin: true },
      '/vectordb':      { target: 'http://127.0.0.1:5002', changeOrigin: true },
      '/settings':      { target: 'http://127.0.0.1:5002', changeOrigin: true },
      '/conversations': { target: 'http://127.0.0.1:5002', changeOrigin: true },
      '/lark':          { target: 'http://127.0.0.1:5002', changeOrigin: true },
    }
  }
})
