import {fileURLToPath} from 'node:url';
import {defineConfig} from 'vite';
import type {UserConfig} from 'vite';
import baseline from '../../vite.config.ts';
import {acceptedReferencePlugin} from '../hair-live-preview/accepted-reference.ts';
import {hairModelAssetsPlugin} from '../hair-live-preview/vite.config.ts';
import {MOBILE_BASE, mobileAddressPlugin, mobilePackagePlugin, mobileRelease} from './mobile-build.ts';

export default defineConfig(async (): Promise<UserConfig> => {
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const release = await mobileRelease(root);
  return {...baseline, base: MOBILE_BASE, cacheDir: 'node_modules/.vite-mobile',
    define: {'import.meta.env.VITE_AR_BUILD_ID': JSON.stringify(release.sourceFingerprint),
      'import.meta.env.VITE_AR_BUILD_AT': JSON.stringify(release.createdAt)},
    plugins: [await acceptedReferencePlugin(), hairModelAssetsPlugin(), mobileAddressPlugin(), mobilePackagePlugin(root, release)],
    worker: {format: 'es', plugins: () => [mobileAddressPlugin()]},
    build: {outDir: 'mobile-site', emptyOutDir: true, copyPublicDir: false, sourcemap: false,
      rolldownOptions: {
        input: {arTesting: fileURLToPath(new URL('./live.html', import.meta.url))}}},
  };
});
