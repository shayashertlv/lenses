/** The external resources a GLB names, read and routed ONE way for `arview serve` and `arview capture`, so the two tools
 *  and spec.py agree on which files a model has (WP1_DESIGN.md §1, the Format row: only a MISSING resource is refused;
 *  relative URIs resolve against the model URL).
 *
 *  - The file: exactly what spec.py reads (g3d_host/glb.py `resolve`: the GLB's folder joined with
 *    urllib.parse.unquote(uri)). A malformed percent escape stays as it is ('100%.png' is the file 100%.png), a URI that
 *    leaves the folder ('../shared/a.bin') is read where it points, a root-relative one ('/x.bin') is taken from the
 *    drive root as pathlib does. A file outside the GLB's folder is served with a warning (the model is not portable as
 *    one folder); it is never a refusal while the file exists.
 *  - The route: the pathname arview's loader requests (load.ts: three's LoaderUtils.resolveURL against the model URL's
 *    directory, then the URL parser's normalisation of '..' and '%2e%2e'), never decoded: the server looks it up by the
 *    raw request pathname, so '100%.png' is served at '/…/100%.png'.
 *  - Refused (not served, so arview refuses the model as missing, which spec.py also does): an absolute URI (a scheme, a
 *    drive letter or '//host': glTF resolves it as a URL, never next to the file; spec.py _missing_uris), and a file that
 *    cannot be read. The route of a file that cannot be read is returned (`missingRoutes`) so
 *    each server answers it 404 explicitly WHATEVER its prefix: a URI that climbs out of the model's folder
 *    ('../../../../package.json') resolves outside /__arview/, where the server would otherwise hand the page one of its
 *    own files (HTTP 200) and arview would get bytes for a missing resource (WP1 review E/F).
 *  - A readable resource whose route is one of the server's own paths (the page, a public binary, the report) cannot be
 *    served faithfully: that is an ERROR of the tool (the page would hand arview the wrong bytes), never a §2 verdict.
 *
 *  Never throws. */
import fsp from 'node:fs/promises';
import path from 'node:path';
import {LoaderUtils} from 'three';
import {isAbsoluteUri} from '../../src/model/glb.ts';
export {isAbsoluteUri} from '../../src/model/glb.ts';

/** Media types of glTF external resources (buffers and images); anything else is application/octet-stream. */
export const RESOURCE_CONTENT_TYPES: Readonly<Record<string, string>> = Object.freeze({'.bin': 'application/octet-stream', '.png': 'image/png',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.avif': 'image/avif', '.ktx2': 'image/ktx2', '.glb': 'model/gltf-binary'});

const LOOPBACK = 'http://127.0.0.1';

/** Python's urllib.parse.unquote (UTF-8, errors='replace'), which spec.py applies to every URI: inside each ASCII run a
 *  valid %XX escape becomes its byte and a malformed one is kept literally ('100%.png' stays '100%.png'); the bytes are
 *  decoded as UTF-8 with U+FFFD for an invalid sequence; non-ASCII text is left as it is. Never throws (unlike
 *  decodeURIComponent, which threw URIError on '100%.png' and crashed arview capture). */
export function pythonUnquote(text: string): string {
  if (!text.includes('%')) return text;
  const decoder = new TextDecoder('utf-8');
  return text.replace(/[\x00-\x7f]+/g, run => {
    if (!run.includes('%')) return run;
    const bytes: number[] = [];
    for (let i = 0; i < run.length; i++) {
      const hex = run.slice(i + 1, i + 3);
      if (run[i] === '%' && /^[0-9A-Fa-f]{2}$/.test(hex)) {bytes.push(Number.parseInt(hex, 16)); i += 2;}
      else bytes.push(run.charCodeAt(i));
    }
    return decoder.decode(Uint8Array.from(bytes));
  });
}

/** The pathname arview's loader requests for `uri` when the model is served at `modelPath` (load.ts: resourceBase, then
 *  LoaderUtils.resolveURL; the request's URL parser normalises '..'). Raw (percent escapes kept). Throws for a URI the
 *  URL parser rejects. */
export function resourceRoute(uri: string, modelPath: string): string {
  const base = new URL('.', LOOPBACK + modelPath).href;
  return new URL(LoaderUtils.resolveURL(uri, base)).pathname;
}

/** The file spec.py reads for `uri` (glb.py: Path(glb).parent / unquote(uri); path.resolve joins like pathlib: a rooted
 *  right side replaces the folder, keeping the drive on Windows). */
export function resourceFile(glbPath: string, uri: string): string {
  return path.resolve(path.dirname(path.resolve(glbPath)), pythonUnquote(uri));
}

/** Whether two resolved paths name the same file path (case-insensitively on Windows, as the file system compares). */
const samePath = (a: string, b: string): boolean => process.platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b;

/** Whether `child` lies inside `parent` (both resolved; never the folder itself). path.relative compares
 *  case-insensitively on Windows. */
function isInside(parent: string, child: string): boolean {
  const relative = path.relative(parent, child);
  return relative !== '' && relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

/** A readable resource, served at `route`. */
export interface ServedResource {
  readonly uri: string;
  readonly file: string;
  readonly route: string;
  readonly bytes: Buffer;
  readonly type: string;
  /** The file lies outside the GLB's folder (by its path or through a link): served, with a warning. */
  readonly outside: boolean;
}

export interface ExternalResources {
  /** What to route: one entry per URL (the first URI naming it). */
  readonly resources: ServedResource[];
  /** Every URI that is served, in file order (an alias of a routed URL and the GLB naming itself included). */
  readonly served: string[];
  /** The served URIs whose file lies outside the GLB's folder (each with a warning). */
  readonly outside: string[];
  /** Not served because it is missing, unreadable or an absolute URL: answered 404, so arview refuses the model as
   *  missing (§1 Format), as spec.py fails the row. */
  readonly unreadable: string[];
  /** The route arview requests for each unreadable URI that resolves to a URL (an absolute URL has none): every server
   *  registers it as an explicit 404, looked up before its own files, whatever its prefix. */
  readonly missingRoutes: {readonly uri: string; readonly route: string}[];
  /** Readable, but its route is one of the server's own paths: not served (see `errors`). */
  readonly unservable: string[];
  readonly warnings: string[];
  /** The tool's own failures (a route collision): never a §2 verdict on the model. */
  readonly errors: string[];
}

export interface ResolveOptions {
  /** The model's served path ('/__arview/models/<sha256>/<name>'). */
  readonly modelPath: string;
  /** Whether a route is one of the server's own paths (the page's files, a public binary, the report). It receives the
   *  raw route and its decoded form (null when it does not decode). The model's own path is always reserved. */
  readonly reserved?: (route: string, decoded: string | null) => boolean;
  /** The tool's name in the sentences ('arview serve', 'the capture'). */
  readonly tool: string;
}

/** Read and route every URI of `uris` (the GLB's external buffers and images, capture/glb-input.ts). */
export async function resolveExternalResources(glbPath: string, uris: readonly string[], options: ResolveOptions): Promise<ExternalResources> {
  const glb = path.resolve(glbPath), folder = path.dirname(glb);
  const realFolder = await fsp.realpath(folder).catch(() => folder);
  const realGlb = await fsp.realpath(glb).catch(() => glb);
  const result: ExternalResources = {resources: [], served: [], outside: [], unreadable: [], unservable: [], missingRoutes: [], warnings: [], errors: []};
  const missing = (uri: string, why: string, route: string | null = null): void => {
    result.unreadable.push(uri);
    if (route !== options.modelPath) {
      result.warnings.push(`external resource "${uri}" ${why}; ${route ? `${options.tool} answers it 404 (at ${route})` : 'arview never fetches it'}, so arview refuses the model `
        + '(§1 Format: a missing external resource)');
    }
    if (route === null) return;
    if (route === options.modelPath) {
      // The model's own URL (a served name that differs from the file's): never shadowed by a 404, so arview reads the
      // model's bytes for the missing resource and the capture's render-anyway check refuses the run.
      result.errors.push(`external resource "${uri}" is missing, and the URL arview requests for it (${route}) is the model's own URL: `
        + `${options.tool} keeps serving the model there, so arview gets the model's bytes for it (the model is refused as missing either way, §1 Format)`);
      return;
    }
    result.missingRoutes.push({uri, route});
    let decoded: string | null;
    try {decoded = decodeURIComponent(route);} catch {decoded = null;}
    if (options.reserved?.(route, decoded) === true) {
      result.errors.push(`external resource "${uri}" is missing, and the URL arview requests for it (${route}) is one of ${options.tool}'s own paths: `
        + `${options.tool} answers that URL 404 for this run, which can break its own page (the model is refused as missing either way, §1 Format). `
        + 'Keep external resources next to the GLB.');
    }
  };
  for (const uri of uris) {
    const file = resourceFile(glb, uri);
    if (isAbsoluteUri(uri)) {
      // spec.py fails it too (its _missing_uris: an absolute URI is resolved as a URL, never next to the file), even when
      // such a path exists on this machine.
      const exists = await fsp.stat(file).then(stat => stat.isFile(), () => false);
      missing(uri, `is an absolute URI, which glTF resolves as a URL (never next to the file) and the page cannot fetch from the loopback server`
        + `${exists ? ` (the path ${file} exists on this machine, but it is not a resource next to the GLB; spec.py fails it as well)` : ''}`);
      continue;
    }
    let route: string;
    try {route = resourceRoute(uri, options.modelPath);}
    catch (error) {missing(uri, `cannot be resolved to a URL (${error instanceof Error ? error.message : String(error)})`); continue;}
    let bytes: Buffer, real: string;
    try {
      real = await fsp.realpath(file);
      bytes = await fsp.readFile(real);
    } catch (error) {
      missing(uri, `(${file}) cannot be read (${(error as NodeJS.ErrnoException).code ?? (error instanceof Error ? error.message : String(error))})`, route);
      continue;
    }
    let decoded: string | null;
    try {decoded = decodeURIComponent(route);} catch {decoded = null;}
    const taken = result.resources.find(entry => entry.route === route);
    const isModel = route === options.modelPath;
    if (taken ? !taken.bytes.equals(bytes) : isModel ? !samePath(real, realGlb) : options.reserved?.(route, decoded) === true) {
      result.unservable.push(uri);
      result.errors.push(`external resource "${uri}" (${file}) exists, but the URL arview requests for it (${route}) is ${taken ? `also the URL of "${taken.uri}" (${taken.file}), a different file`
        : isModel ? 'the model\'s own URL' : `one of ${options.tool}'s own paths`}: ${options.tool} cannot serve both, so whatever arview concludes about this model `
        + 'here is void (this is a limit of the tool, not a §2 verdict; spec.py reads the file). Keep external resources next to the GLB.');
      continue;
    }
    const outside = !isInside(folder, file) || !isInside(realFolder, real);
    if (outside) {
      result.warnings.push(`external resource "${uri}" is ${samePath(real, file) ? file : `${file} (a link to ${real})`}, outside the GLB's folder ${folder}: served (spec.py reads `
        + 'the same file), but the model is not portable as one folder');
      result.outside.push(uri);
    }
    result.served.push(uri);
    // The same bytes at a URL already routed ('a.bin' and './a.bin'), or the GLB naming itself at the model's URL: served,
    // nothing more to route.
    if (taken || isModel) continue;
    result.resources.push({uri, file, route, bytes, type: RESOURCE_CONTENT_TYPES[path.extname(file).toLowerCase()] ?? 'application/octet-stream', outside});
  }
  return result;
}
