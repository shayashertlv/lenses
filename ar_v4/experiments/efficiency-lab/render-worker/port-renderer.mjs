/** Mechanical worker-only port. Never writes an accepted source file. */
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../../..');
const files = [
  'experiments/speed-lab/renderer.ts',
  'experiments/speed-lab/temples/renderer.ts',
  'experiments/speed-lab/native/renderer.ts',
  'experiments/performance-candidate/temples/branch-renderer.ts',
  'experiments/speed-lab/speed-options.ts',
  'experiments/speed-lab/native/source-camera.ts',
  'experiments/performance-stage2/native/native-pixels.ts',
  'experiments/performance-candidate/native/temple-visibility.ts',
  'references/perfect-temples/src/render/temple-clip.ts',
];
const mapped = new Map(files.map(file => [path.resolve(root, file), path.join(here, 'adapted', file)]));
export async function portedSources() {
  return Promise.all(files.map(async file => {
    const from = path.resolve(root, file), to = mapped.get(from);
    let value = await readFile(from, 'utf8');
    value = value.replaceAll('HTMLCanvasElement', 'OffscreenCanvas').replaceAll('CanvasRenderingContext2D', 'OffscreenCanvasRenderingContext2D')
      .replaceAll(': CanvasTexture', ': CanvasTexture<OffscreenCanvas>')
      .replaceAll('as CanvasTexture', 'as CanvasTexture<OffscreenCanvas>')
      .replaceAll("document.createElement('canvas')", 'new OffscreenCanvas(300, 150)');
    value = value.replace(/(from\s+['"])(\.\.?\/[^'"]+)(['"])/g, (_, a, spec, b) => {
      const absolute = path.resolve(path.dirname(from), spec);
      const relative = path.relative(path.dirname(to), mapped.get(absolute) ?? absolute).replaceAll('\\', '/');
      return a + (relative.startsWith('.') ? relative : './' + relative) + b;
    });
    if (value.includes(".toDataURL('image/png')")) {
      const pngModule = path.relative(path.dirname(to), path.join(here, 'png.ts')).replaceAll('\\', '/');
      value = `import {canvasPng} from '${pngModule}';\n` + value;
      value = value.replaceAll('exportDiagnostic(): Record<string, unknown> | null {', 'async exportDiagnostic(): Promise<Record<string, unknown> | null> {')
        .replaceAll('const png = (value: OwnedPixels): string => {', 'const png = async (value: OwnedPixels): Promise<string> => {')
        .replace(/([\w.]+)\.toDataURL\('image\/png'\)/g, 'await canvasPng($1)')
        .replaceAll('acceptedPngDataUrl: png(before)', 'acceptedPngDataUrl: await png(before)')
        .replaceAll('hairPngDataUrl: png(after)', 'hairPngDataUrl: await png(after)')
        .replaceAll('this.background ? png(this.background)', 'this.background ? await png(this.background)');
    }
    if (file === 'experiments/speed-lab/renderer.ts') value = value.replace('  private publish(): void {',
      `  /** Worker transport owns copies; renderer storage remains private for Hold. */\n  copyCompletedPixels(): {accepted: ImageData; hair: ImageData} | null {\n    if (this.pending || this.disposed || !this.before || !this.after) return null;\n    return {accepted: structuredClone(this.before), hair: structuredClone(this.after)};\n  }\n\n  private publish(): void {`);
    return {to, value};
  }));
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  for (const {to, value} of await portedSources()) {await mkdir(path.dirname(to), {recursive:true}); await writeFile(to, value);}
}
