import { defineConfig } from 'vite'

// Use dynamic import for the React plugin to avoid ESM/CJS loading issues inside some
// Docker build environments where the plugin resolves to an ESM-only file.
export default defineConfig(async () => {
  const reactPlugin = (await import('@vitejs/plugin-react')).default
  return {
    plugins: [reactPlugin()],
    server: { host: true, port: 3000 },
  }
})
