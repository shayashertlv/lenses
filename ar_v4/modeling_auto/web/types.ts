export const ANGLES = ['front', 'back', 'left', 'right', 'angled'] as const;
export type Angle = typeof ANGLES[number];
export const STAGES = ['generate', 'lenses', 'connections', 'texture', 'finish', 'review', 'complete'] as const;
export const TEST_STAGES = ['generate', 'lenses', 'connections', 'texture', 'finish', 'finish_refine', 'review', 'complete'] as const;
export type Stage = typeof TEST_STAGES[number];
export type Pipeline = 'current' | 'test';
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
  pipeline?: Pipeline;
  pipeline_stages?: Stage[];
  status: string; stage: Stage; message?: string; error?: string | null; auth_failure?: boolean;
  recovery_kind?: 'astra_script' | null;
  notes: string; dimensions: Record<string, number>;
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
export interface JobListItem { id: string; name: string; status: string; stage: Stage; pipeline?: Pipeline }
export const LABELS: Record<Stage, string> = {
  generate: 'Blank model', lenses: 'Smooth & create lenses', connections: 'Lens seating',
  texture: 'Texture', finish: 'Material & finish', finish_refine: 'Material & finish · pass 2', review: 'Your review', complete: 'Accepted',
};
