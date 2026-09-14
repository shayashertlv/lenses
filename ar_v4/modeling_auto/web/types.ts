export const ANGLES = ['front', 'back', 'left', 'right', 'angled'] as const;
export type Angle = typeof ANGLES[number];
export const LEGACY_STAGES = ['generate', 'lenses', 'connections', 'texture', 'finish', 'review', 'complete'] as const;
export const STANDARD_STAGES = ['generate', 'lenses', 'connections', 'texture', 'finish', 'finish_refine', 'review', 'complete'] as const;
export type Stage = typeof STANDARD_STAGES[number];
export type Pipeline = 'standard' | 'legacy';
export const DEFAULT_PIPELINE: Pipeline = 'standard';
/** Accepts the canonical plan names and the names the plans first shipped under. */
export function parsePipeline(value: string | null | undefined): Pipeline | null {
  if (value === 'standard' || value === 'test') return 'standard';
  if (value === 'legacy' || value === 'current') return 'legacy';
  return null;
}
export interface Proof { angle: string; url: string }
export interface Revision {
  id: string;
  stage?: Stage;
  blend_url: string;
  model_url: string;
  proofs?: Proof[];
  closeup_url?: string;
  inspection?: Record<string, unknown>;
}
export interface Job {
  id: string; name: string; version: number;
  pipeline?: Pipeline | string;
  pipeline_stages?: Stage[];
  status: string; stage: Stage; message?: string; error?: string | null; auth_failure?: boolean;
  recovery_kind?: 'astra_script' | null;
  notes: string; edit_instructions?: string | null; dimensions: Record<string, number>;
  references: Proof[];
  current: Revision | null;
  revisions: Revision[];
  proposal: null | {
    id: string; stage: Stage; provider: string; title: string; description: string;
    image_count: number; images: { label: string; url: string }[];
    settings: Record<string, unknown>; input_sha256: string;
  };
  calls: { astra: number; meshy: number };
  allowed_actions: string[];
  accepted: { sha256: string; url: string } | null;
}
export interface Health {
  status?: string;
  ready?: boolean;
  runtime_ready?: boolean;
  blender_available?: boolean;
  active_job_id?: string | null;
  keys_present?: { openai: boolean; meshy: boolean };
  openai_configured?: boolean;
  meshy_configured?: boolean;
  [key: string]: unknown;
}
export interface JobListItem { id: string; name: string; status: string; stage: Stage; pipeline?: Pipeline | string }
export const LABELS: Record<Stage, string> = {
  generate: 'Blank model', lenses: 'Smooth & create lenses', connections: 'Lens seating',
  texture: 'Texture', finish: 'Material & finish', finish_refine: 'Material & finish · pass 2', review: 'Your review', complete: 'Accepted',
};
