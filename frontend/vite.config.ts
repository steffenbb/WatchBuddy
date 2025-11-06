import { defineConfig } from 'vite'

// Use dynamic import for the React plugin to avoid ESM/CJS loading issues inside some
// Docker build environments where the plugin resolves to an ESM-only file.
export default defineConfig(async () => {
  const reactPlugin = (await import('@vitejs/plugin-react')).default
  return {
    plugins: [reactPlugin()],
    server: {
      host: true,
      port: 3000,
      proxy: {
        '/api': {
          target: 'http://backend:8000',
          changeOrigin: true,
          secure: false,
          ws: true,
          configure: (proxy, options) => {
            proxy.on('error', (err, _req, _res) => {
              console.log('proxy error', err);
            });
            proxy.on('proxyReq', (proxyReq, req, _res) => {
              console.log('Sending Request:', req.method, req.url);
            });
            proxy.on('proxyRes', (proxyRes, req, _res) => {
              console.log('Received Response:', proxyRes.statusCode, req.url);
            });
          }
        }
      }
    },
  }
})
