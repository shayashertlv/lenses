import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import type {IncomingMessage, ServerResponse} from 'node:http';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {defineConfig, mergeConfig} from 'vite';
import type {Plugin} from 'vite';
import baseline from '../temple-sagittal/vite.config.ts';
import {acceptedReferencePlugin} from './accepted-reference.ts';
import {HAIR_MODEL_LIST} from './models.ts';
import type {HairModelId} from './models.ts';

const directory = fileURLToPath(new URL('../../public/models/hair/', import.meta.url));
const models = new Map(HAIR_MODEL_LIST.map(model => [model.id, model]));

async function verifiedModel(modelDirectory: string, id: string): Promise<Buffer> {
  const model = models.get(id as HairModelId);
  if (!model) throw new Error('Unknown hair model.');
  const bytes = await readFile(path.join(modelDirectory, `${model.id}.tflite`));
  if (bytes.length !== model.bytes || createHash('sha256').update(bytes).digest('hex') !== model.sha256)
    throw new Error(`Pinned hair model mismatch: ${model.id}`);
  return bytes;
}

function serveModels(modelDirectory: string) {
  return (request: IncomingMessage, response: ServerResponse, next: () => void): void => {
    const pathname = new URL(request.url ?? '/', 'http://127.0.0.1').pathname;
    if (!pathname.startsWith('/hair-preview-models/')) { next(); return; }
    const match = /^\/hair-preview-models\/(hair-only|selfie-multiclass)\.tflite$/.exec(pathname);
    if (!match || !['GET', 'HEAD'].includes(request.method ?? '')) {
      response.statusCode = 404; response.end('Unknown hair model.'); return;
    }
    void verifiedModel(modelDirectory, match[1]!).then(bytes => {
      response.statusCode = 200;
      response.setHeader('Content-Type', 'application/octet-stream');
      response.setHeader('Content-Length', bytes.length);
      response.setHeader('Cache-Control', 'no-store');
      response.setHeader('Cross-Origin-Resource-Policy', 'same-origin');
      response.end(request.method === 'HEAD' ? undefined : bytes);
    }).catch(() => {
      response.statusCode = 503;
      response.end('The verified local hair model is unavailable. Accepted rendering remains available.');
    });
  };
}

/** Preserve worker URLs in dev, preview and a standalone static build. */
export function hairModelAssetsPlugin(): Plugin {
  let isBuild = false;
  return {
    name: 'pinned-hair-model-assets',
    configResolved(config) { isBuild = config.command === 'build'; },
    async buildStart() {
      for (const model of HAIR_MODEL_LIST) {
        const source = await verifiedModel(directory, model.id);
        // Vite also copies the attributed public/models/hair originals to dist.
        if (isBuild) this.emitFile({type: 'asset', fileName: model.urlPath.slice(1), source});
      }
    },
    configureServer(server) { server.middlewares.use(serveModels(directory)); },
    configurePreviewServer(server) {
      const output = path.resolve(server.config.root, server.config.build.outDir, 'hair-preview-models');
      server.middlewares.use(serveModels(output));
    },
  };
}

export default defineConfig(async () => mergeConfig(baseline, {
  cacheDir: 'node_modules/.vite-hair',
  server: {host: '127.0.0.1', port: 8064, strictPort: true},
  plugins: [await acceptedReferencePlugin(), hairModelAssetsPlugin()],
}));
