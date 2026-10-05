/** Where the page's assets live. Vite's base URL ('/' in development and for the local preview, '/ar/' on the
 *  published site) is applied to every model, weight and runtime address, in the page and in its workers alike, so
 *  the same bundle works at the root and under a prefix. Outside Vite (unit tests) the base is '/'. */
const configured = (import.meta as ImportMeta & {env?: {BASE_URL?: string}}).env?.BASE_URL;
export const ASSET_BASE: string = typeof configured === 'string' && configured ? (configured.endsWith('/') ? configured : configured + '/') : '/';

/** The site-absolute address of an asset path such as 'models/face_landmarker.task'. */
export function assetPath(path: string): string {
  return ASSET_BASE + path.replace(/^\/+/, '');
}
