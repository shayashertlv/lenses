/** The model report without drawing: the load front-end (load.ts) and the normalisation pass (normalize.ts), exactly
 *  as TryOnRenderer.create runs them, minus everything that draws. For the capture CLI's preflight, the QA temple
 *  cutoff and tests. A refusal is returned as a report with status 'refused', never thrown; a file this environment
 *  cannot check (Draco geometry without a Web Worker) as a report with status 'unchecked' (no verdict). With `renderer: null`
 *  KTX2 textures cannot be transcoded: they are left out and reported as a 'textures' fallback (never a refusal);
 *  pass the WebGLRenderer to decode them as the renderer does. */
import type {Group} from 'three';
import type {GltfSource} from '../eyewear/optical-material.ts';
import {loadModel} from './load.ts';
import type {LoadModelOptions} from './load.ts';
import {normalizeModel} from './normalize.ts';
import {ModelRefusal, ModelReportBuilder, ModelUnchecked} from './report.ts';
import type {ModelReport} from './report.ts';

export interface CheckedModel {
  readonly report: ModelReport;
  /** The normalised scene and its glTF source; null when the model was refused. */
  readonly scene: Group | null;
  readonly source: GltfSource | null;
}

export async function checkModel(bytes: ArrayBuffer, options: LoadModelOptions & {readonly displayName?: string; readonly definitionWarnings?: readonly string[]}):
  Promise<CheckedModel> {
  const report = new ModelReportBuilder(options.url, options.displayName ?? options.url, options.definitionWarnings ?? []);
  try {
    const loaded = await loadModel(bytes, report, options);
    normalizeModel(loaded.gltf.scene, report, loaded.source);
    return {report: report.snapshot('rendered'), scene: loaded.gltf.scene, source: loaded.source};
  } catch (error) {
    if (error instanceof ModelRefusal || error instanceof ModelUnchecked) return {report: error.report, scene: null, source: null};
    throw error;
  }
}
