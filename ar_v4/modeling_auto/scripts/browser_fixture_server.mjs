// Browser-only synthetic download endpoint. It is not part of the production app.
// A real HTTP response is needed to verify the browser's completed download bytes.
import { createServer } from 'vite';

const bytes = Buffer.from('BLENDER-SYNTHETIC-EXACT-ACCEPTED-ARTIFACT');
const server = await createServer({
  configFile: 'vite.config.ts',
  // Private model outputs and Python dependencies are not frontend sources.
  // Keep growing test/job directories out of discovery and file watching.
  optimizeDeps: { entries: ['index.html'] },
  server: { watch: { ignored: ['**/data/**', '**/.venv/**'] } },
  plugins: [{
    name: 'synthetic-browser-download',
    configureServer(vite) {
      vite.middlewares.use((request, response, next) => {
        if (request.method !== 'GET' || request.url !== '/api/jobs/synthetic-ui-job/files/accepted-exact-blend') { next(); return; }
        response.writeHead(200, {
          'Content-Type': 'application/octet-stream',
          'Content-Disposition': 'attachment; filename="synthetic-accepted.blend"',
          'Content-Length': bytes.length,
        });
        response.end(bytes);
      });
    },
  }],
});
await server.listen();
server.printUrls();
