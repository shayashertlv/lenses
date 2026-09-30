import {isStudioSender, mergeStudioPreview, parseStudioPreview, studioConnection} from './protocol.ts';
import type {StudioPreview} from './protocol.ts';
import type {StudioMaterial} from './materials.ts';

export interface StudioTarget {
  materials(): StudioMaterial[];
  apply(preview: StudioPreview): void;
}
/** Closing AR leaves accepted edits here; reopening replays them after the exact model loads. */
export function createStudioBridge(view: 'ar' | '3d') {
  const connection = studioConnection(location.search, location.origin);
  if (!connection || window.parent === window) return null;
  let target: StudioTarget | null = null, state: StudioPreview = {edits: {}, viewer: {}};
  const send = (type: string, extra: Record<string, unknown> = {}) => window.parent.postMessage({type: `lenses-studio:${type}`,
    channel: connection.channel, model_sha256: connection.modelSha256, view, ...extra}, connection.origin);
  const ready = () => send('ready', {loaded: !!target, materials: target?.materials() ?? []});
  const onMessage = (event: MessageEvent) => {
    if (!isStudioSender(event, window.parent, connection)) return;
    try {
      const preview = parseStudioPreview(event.data, connection.modelSha256), merged = mergeStudioPreview(state, preview);
      target?.apply(preview); state = merged;
      send('applied', {pending: !target, material_ids: Object.keys(preview.edits), viewer: preview.viewer});
    } catch (error) {send('error', {message: error instanceof Error ? error.message : String(error)});}
  };
  window.addEventListener('message', onMessage);
  queueMicrotask(ready);
  return {
    attach(value: StudioTarget) {
      target = value;
      try {target.apply(state);} catch (error) {send('error', {message: error instanceof Error ? error.message : String(error)});}
      ready();
    },
    detach() {target = null; ready();},
    error(message: string) {send('error', {message});},
    dispose() {window.removeEventListener('message', onMessage); target = null;},
  };
}
