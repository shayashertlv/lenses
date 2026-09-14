import {fileURLToPath} from 'node:url';
import {defineConfig, mergeConfig} from 'vite';
import speed from '../../vite.speed.config.ts';
export default defineConfig(async environment => {
  const config = typeof speed === 'function' ? await speed(environment) : await speed;
  return mergeConfig(config, {
    cacheDir:'node_modules/.vite-speed-lab',
    server:{host:'127.0.0.1',port:8082,strictPort:true},
    preview:{host:'127.0.0.1',port:8083,strictPort:true},
    build:{outDir:'experiments/speed-lab/dist',emptyOutDir:true,
      rollupOptions:{input:{speedLab:fileURLToPath(new URL('./live.html',import.meta.url))}}},
  });
});
