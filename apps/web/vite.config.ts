import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', 'VITE_');
  return {
    server: {
      allowedHosts: (env.VITE_ALLOWED_HOSTS || '').split(',').map(host => host.trim()).filter(Boolean),
      proxy: { '/api': env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000' },
    },
  };
});
