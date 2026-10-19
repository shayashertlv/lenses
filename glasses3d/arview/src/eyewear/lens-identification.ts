/** Lens identification (glasses3d/plan/archive/20261019/WP1_DESIGN.md §2, PLAN.md WP1.3): which glTF uses are lens and which are frame.
 *
 *  Pure and three-free. The rule order is the one in `glasses3d/g3d_host/lens_id.py`, and both sides run the shared
 *  vectors `glasses3d/tests/fixtures/lens_id_cases.json`, so they cannot drift apart. Per use (node x primitive):
 *    1. extras on the material, then the mesh, then the node: `partRole` equal to "lens" (case-insensitive, as the
 *       Python side lowercases), or legacy `part_role` starting with "lens". Any other explicit role (frame, temple,
 *       hardware, temple_left, ...) means "not a lens" and stops rules 2-3. A disagreeing lower-priority role is a
 *       warning.
 *    2. a whole-word token `lens`, `lenses` or `glass` (never "glasses"; camelCase and digits split) in the material
 *       or node name, unless either name has a token starting with etch, letter, inscri or print.
 *    3. only when rules 1-2 found no lens anywhere in the file: transmissionFactor > 0, or alphaMode BLEND with
 *       baseColorFactor alpha < 1, with a warning (a crystal acetate frame transmits too).
 *  Otherwise the use is frame ("no lens signal").
 *
 *  The class of a material is decided once from these uses; a material used by both lens and frame uses is "mixed"
 *  (a §2 Components failure). Nothing here reads three's merged userData or a material's live transmission. */

export type PartClass = 'lens' | 'frame';
export type MaterialClass = PartClass | 'mixed';
/** The step that decided: rule 1 (`partRole` / legacy `part_role`), rule 2 (`name_token`), rule 3 (`transmission` /
 *  `alpha`), or no rule (`none`, frame by default). */
export type LensSignalKind = 'partRole' | 'part_role_legacy' | 'name_token' | 'transmission' | 'alpha' | 'none';
export type SignalSite = 'material' | 'mesh' | 'node';
type Extras = Readonly<Record<string, unknown>>;

export const LENS_TOKENS: ReadonlySet<string> = new Set(['lens', 'lenses', 'glass']);
export const EXCLUDE_STEMS: readonly string[] = Object.freeze(['etch', 'letter', 'inscri', 'print']);
export const LENS_ID_RULE_VERSION = 'lens-id-v1';

export interface LensDecision {
  readonly isLens: boolean;
  /** 1, 2 or 3; 0 when no rule matched (frame by default). */
  readonly rule: 0 | 1 | 2 | 3;
  /** Human-readable deciding signal, worded as the Python side words it, e.g. "material.extras.partRole=lens". */
  readonly signal: string;
  readonly kind: LensSignalKind;
  readonly site: SignalSite | null;
  /** The deciding value: the role string, the name token, the transmission factor or the alpha. */
  readonly value: string | number | null;
}

/** What identification needs to know about one use (format-neutral, so tests need no GLB). `material` is the glTF
 *  JSON material object (for rule 3), or null for the glTF default material. */
export interface UseSignals {
  readonly key: string;
  readonly nodeName?: string | null;
  readonly nodeExtras?: unknown;
  readonly meshExtras?: unknown;
  readonly materialName?: string | null;
  readonly materialExtras?: unknown;
  readonly material?: unknown;
}

const isRecord = (value: unknown): value is Extras => typeof value === 'object' && value !== null && !Array.isArray(value);

/** Lower-case word tokens; splits on non-letters (digits and separators), camelCase and ACRONYMCase boundaries
 *  ('LENSRight' -> lens, right; 'LensRight.001' -> lens, right). */
export function nameTokens(name: string | null | undefined): string[] {
  if (!name) return [];
  return name.replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2').replace(/([a-z])([A-Z])/g, '$1 $2')
    .toLowerCase().split(/[^A-Za-z]+/).filter(token => token.length > 0);
}

/** True when a name marks an etch, lettering, inscription or print (never a lens by name). */
export function excludedName(name: string | null | undefined): boolean {
  return nameTokens(name).some(token => EXCLUDE_STEMS.some(stem => token.startsWith(stem)));
}

/** The explicit role in an extras object: `partRole` first, then legacy `part_role`; non-string or blank values are
 *  absent. */
export function explicitRole(extras: unknown): {role: string; key: 'partRole' | 'part_role'} | null {
  if (!isRecord(extras)) return null;
  for (const key of ['partRole', 'part_role'] as const) {
    const value = extras[key];
    if (typeof value === 'string' && value.trim()) return {role: value.trim(), key};
  }
  return null;
}

/** partRole: equal to "lens" after trimming, in any case. part_role (legacy): starts with "lens", in any case. */
export function roleSaysLens(role: string, key: 'partRole' | 'part_role'): boolean {
  const lower = role.trim().toLowerCase();
  return key === 'partRole' ? lower === 'lens' : lower.startsWith('lens');
}

/** Rule 1 over `[site, extras]` sources in priority order (material, mesh, node). */
export function decideExplicit(sources: readonly (readonly [SignalSite, unknown])[]): {decision: LensDecision | null; warnings: string[]} {
  const found: {site: SignalSite; role: string; key: 'partRole' | 'part_role'}[] = [];
  for (const [site, extras] of sources) {
    const explicit = explicitRole(extras);
    if (explicit) found.push({site, ...explicit});
  }
  const first = found[0];
  if (!first) return {decision: null, warnings: []};
  const lens = roleSaysLens(first.role, first.key);
  const warnings = found.slice(1).filter(other => roleSaysLens(other.role, other.key) !== lens)
    .map(other => `${other.site}.extras.${other.key}='${other.role}' disagrees with ${first.site}.extras.${first.key}='${first.role}' (using ${first.site})`);
  return {decision: {isLens: lens, rule: 1, signal: `${first.site}.extras.${first.key}=${first.role}`,
    kind: first.key === 'partRole' ? 'partRole' : 'part_role_legacy', site: first.site, value: first.role}, warnings};
}

/** Rule 2. */
export function decideName(materialName: string | null | undefined, nodeName: string | null | undefined): LensDecision | null {
  if (excludedName(materialName) || excludedName(nodeName)) return null;
  for (const [site, name] of [['material', materialName], ['node', nodeName]] as const) {
    const hits = [...new Set(nameTokens(name).filter(token => LENS_TOKENS.has(token)))].sort();
    const token = hits[0];
    if (token !== undefined) {
      return {isLens: true, rule: 2, signal: `${site} name token '${token}' in '${name}'`, kind: 'name_token', site, value: token};
    }
  }
  return null;
}

/** Python's float() on a JSON value (numbers, booleans and numeric strings, inf and nan included), or `fallback` where
 *  Python raises, so both sides read odd files alike. */
const pythonFloat = (value: unknown, fallback: number): number => {
  if (typeof value === 'number') return value;
  if (typeof value === 'boolean') return value ? 1 : 0;
  if (typeof value !== 'string') return fallback;
  const text = value.trim().replaceAll('_', '');
  const special = /^([+-]?)(inf|infinity|nan)$/i.exec(text);
  if (special) return special[2]!.toLowerCase() === 'nan' ? NaN : special[1] === '-' ? -Infinity : Infinity;
  return /^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(text) ? Number(text) : fallback;
};

/** The glTF material's transmission factor (0 when absent) and base-colour alpha (1 when absent). */
export function seeThroughProperties(material: unknown): {transmission: number; alpha: number; alphaMode: string} {
  if (!isRecord(material)) return {transmission: 0, alpha: 1, alphaMode: 'OPAQUE'};
  const extensions = isRecord(material.extensions) ? material.extensions : {};
  const transmission = isRecord(extensions.KHR_materials_transmission) ? extensions.KHR_materials_transmission : {};
  const pbr = isRecord(material.pbrMetallicRoughness) ? material.pbrMetallicRoughness : {};
  const factor = Array.isArray(pbr.baseColorFactor) ? pbr.baseColorFactor : [1, 1, 1, 1];
  return {transmission: pythonFloat(transmission.transmissionFactor ?? 0, 0), alpha: pythonFloat(factor[3] ?? 1, 1),
    alphaMode: typeof material.alphaMode === 'string' ? material.alphaMode : 'OPAQUE'};
}

/** Rule 3 signal for a glTF JSON material, or null. */
export function seeThroughSignal(material: unknown): LensDecision | null {
  if (!isRecord(material) || Object.keys(material).length === 0) return null;
  const {transmission, alpha, alphaMode} = seeThroughProperties(material);
  if (transmission > 0) {
    return {isLens: true, rule: 3, signal: `transmissionFactor=${transmission}`, kind: 'transmission', site: 'material', value: transmission};
  }
  if (alphaMode === 'BLEND' && alpha < 1) {
    return {isLens: true, rule: 3, signal: `alphaMode=BLEND alpha=${alpha}`, kind: 'alpha', site: 'material', value: alpha};
  }
  return null;
}

const NO_SIGNAL: LensDecision = Object.freeze({isLens: false, rule: 0, signal: 'no lens signal', kind: 'none', site: null, value: null});

/** Classify every use. Returns ({key: decision}, warnings), exactly as `lens_id.classify`; `label` names a use in
 *  warnings (default: its key). */
export function classifyUses(uses: readonly UseSignals[], label: (use: UseSignals) => string = use => use.key):
  {decisions: Map<string, LensDecision>; warnings: string[]} {
  const decisions = new Map<string, LensDecision>(), warnings: string[] = [], undecided: UseSignals[] = [];
  for (const use of uses) {
    const explicit = decideExplicit([['material', use.materialExtras], ['mesh', use.meshExtras], ['node', use.nodeExtras]]);
    warnings.push(...explicit.warnings.map(warning => `${label(use)}: ${warning}`));
    const decision = explicit.decision ?? decideName(use.materialName, use.nodeName);
    if (decision) decisions.set(use.key, decision); else undecided.push(use);
  }
  const anyLens = [...decisions.values()].some(decision => decision.isLens);
  for (const use of undecided) {
    const signal = anyLens ? null : seeThroughSignal(use.material);
    if (signal) {
      decisions.set(use.key, signal);
      warnings.push(`${label(use)}: identified as lens only by ${signal.signal} (last resort: no partRole or lens name `
        + 'anywhere in the file; a crystal frame would be misread)');
    } else decisions.set(use.key, NO_SIGNAL);
  }
  return {decisions, warnings};
}

/* ------------------------------------------------------------------------------------------------ glTF JSON uses */

/** One use of a material in the default scene: node x primitive (all primitive modes, as the Python side). */
export interface GltfUse {
  readonly key: string;
  readonly node: number;
  readonly nodeName: string;
  readonly mesh: number;
  readonly primitive: number;
  /** glTF material index, or null for the glTF default material. */
  readonly material: number | null;
  readonly mode: number;
  readonly triangles: number;
}

/** The key of a use: node index and primitive index. */
export const useKey = (node: number, primitive: number): string => `${node}:${primitive}`;

/** Every node x primitive of the default scene (`scenes[scene ?? 0]`, or the root nodes when there are no scenes),
 *  depth first, as `g3d_host/glb.py` enumerates them. A node reachable twice is enumerated once. */
export function enumerateGltfUses(json: unknown): GltfUse[] {
  if (!isRecord(json)) return [];
  const nodes = Array.isArray(json.nodes) ? json.nodes : [], meshes = Array.isArray(json.meshes) ? json.meshes : [];
  const accessors = Array.isArray(json.accessors) ? json.accessors : [], materials = Array.isArray(json.materials) ? json.materials : [];
  const validMaterial = (value: unknown): boolean => typeof value === 'number' && Number.isInteger(value) && value >= 0 && value < materials.length
    && isRecord(materials[value]);
  let roots: unknown[];
  const scenes = Array.isArray(json.scenes) ? json.scenes : null;
  if (scenes && scenes.length) {
    const scene = scenes[typeof json.scene === 'number' ? json.scene : 0];
    roots = isRecord(scene) && Array.isArray(scene.nodes) ? scene.nodes : [];
  } else {
    const children = new Set<unknown>();
    for (const node of nodes) if (isRecord(node) && Array.isArray(node.children)) for (const child of node.children) children.add(child);
    roots = nodes.map((_, index) => index).filter(index => !children.has(index));
  }
  const count = (accessor: unknown): number => {
    const value = typeof accessor === 'number' ? accessors[accessor] : undefined;
    return isRecord(value) && typeof value.count === 'number' && Number.isFinite(value.count) ? value.count : 0;
  };
  const uses: GltfUse[] = [], seen = new Set<number>();
  const walk = (index: unknown): void => {
    if (typeof index !== 'number' || !Number.isInteger(index) || seen.has(index)) return;
    seen.add(index);
    const node = nodes[index];
    if (!isRecord(node)) return;
    if (typeof node.mesh === 'number' && isRecord(meshes[node.mesh])) {
      const mesh = meshes[node.mesh] as Extras;
      const primitives = Array.isArray(mesh.primitives) ? mesh.primitives : [];
      primitives.forEach((primitive: unknown, primitiveIndex) => {
        const row = isRecord(primitive) ? primitive : {};
        const mode = typeof row.mode === 'number' ? row.mode : 4;
        const attributes = isRecord(row.attributes) ? row.attributes : {};
        const vertices = row.indices !== undefined ? count(row.indices) : count(attributes.POSITION);
        uses.push({key: useKey(index, primitiveIndex), node: index,
          nodeName: typeof node.name === 'string' ? node.name : `node${index}`, mesh: node.mesh as number,
          // A material index that names no material object is the glTF default material: what three draws for it (load
          // leaves the broken reference out, glb.ts defaultSceneDocument) and what spec.py's glb.material() returns.
          primitive: primitiveIndex, material: validMaterial(row.material) ? row.material as number : null, mode,
          triangles: mode === 4 ? Math.floor(vertices / 3) : mode === 5 || mode === 6 ? Math.max(0, vertices - 2) : 0});
      });
    }
    if (Array.isArray(node.children)) for (const child of node.children) walk(child);
  };
  for (const root of roots) walk(root);
  return uses;
}

/** UseSignals for a glTF use, read from the JSON (node, mesh and material extras and names). */
export function gltfUseSignals(json: unknown, use: GltfUse): UseSignals {
  const doc = isRecord(json) ? json : {};
  const node = Array.isArray(doc.nodes) && isRecord(doc.nodes[use.node]) ? doc.nodes[use.node] as Extras : {};
  const mesh = Array.isArray(doc.meshes) && isRecord(doc.meshes[use.mesh]) ? doc.meshes[use.mesh] as Extras : {};
  const material = use.material !== null && Array.isArray(doc.materials) && isRecord(doc.materials[use.material])
    ? doc.materials[use.material] as Extras : null;
  return {key: use.key, nodeName: use.nodeName, nodeExtras: node.extras, meshExtras: mesh.extras,
    materialName: material && typeof material.name === 'string' ? material.name : null,
    materialExtras: material?.extras, material};
}

/* ------------------------------------------------------------------------------------------------------- report */

/** One use as the report lists it. `node`, `mesh` and `primitive` are glTF indices (null for a scene that was not
 *  loaded from glTF JSON). */
export interface ReportUse {
  readonly node: number | null;
  readonly node_name: string;
  readonly mesh: number | null;
  readonly primitive: number | null;
  readonly mode?: number;
  readonly triangles?: number;
  readonly class: PartClass;
  readonly rule: 0 | 1 | 2 | 3;
  readonly signal: string;
  readonly site: SignalSite | null;
}

/** One report entry per glTF material (plus the glTF default material when a primitive uses it). */
export interface MaterialLensEntry {
  /** glTF materials[] index; null = the glTF default material (or a material of a scene not loaded from glTF). */
  readonly index: number | null;
  readonly name: string;
  readonly class: MaterialClass;
  /** The deciding step of the representative use; 'unused' for a material no primitive of the default scene uses. */
  readonly signal: LensSignalKind | 'unused';
  readonly site: SignalSite | null;
  readonly value: string | number | null;
  readonly uses: readonly ReportUse[];
  /** Material properties rule 3 reads (corroboration, never the deciding signal unless signal says so). */
  readonly transmission: number;
  readonly alpha: number;
  readonly alpha_mode: string;
  readonly notes: readonly string[];
}

export interface LensIdentificationReport {
  readonly rule_version: typeof LENS_ID_RULE_VERSION;
  /** Where the extras came from: the glTF JSON (`gltf-json`, the load path) or a synthetic three scene's userData
   *  (`scene-userData`, tests and hand-built scenes only). */
  readonly source: 'gltf-json' | 'scene-userData';
  readonly order: readonly string[];
  readonly last_resort_used: boolean;
  readonly lens_material_count: number;
  readonly frame_material_count: number;
  readonly mixed_material_count: number;
  readonly decided_by: Readonly<Record<LensSignalKind, number>>;
  /** The §2 Components row as arview checks it (the same reasons `g3d_host/spec.py` gives). */
  readonly components: {readonly pass: boolean; readonly reasons: readonly string[]};
  readonly materials: readonly MaterialLensEntry[];
  readonly warnings: readonly string[];
}

export const LENS_ID_ORDER: readonly string[] = Object.freeze([
  'partRole=lens | part_role=lens* (material > mesh > node extras; any other explicit role is frame)',
  'name token lens|lenses|glass (material, then node name; not etch|letter|inscri|print)',
  'transmissionFactor > 0 | BLEND alpha < 1 (only when nothing else names a lens: last resort, warned)',
]);

/** A material as the report builder sees it. `key` identifies it among `materials`. */
export interface ReportMaterial {
  readonly key: string;
  readonly index: number | null;
  readonly name: string;
  readonly json: unknown;
}

/** A classified use as the report builder sees it. */
export interface ReportUseInput extends ReportUse {readonly key: string; readonly materialKey: string; readonly decision: LensDecision;}

const RULE_STRENGTH = [3, 0, 1, 2] as const;   // rule 1 first, then 2, then 3, then no rule

/** Build the report block from classified uses. `warnings` are the classification's own warnings; the Components
 *  failures are appended to them. */
export function buildLensReport(source: LensIdentificationReport['source'], materials: readonly ReportMaterial[],
  uses: readonly ReportUseInput[], warnings: readonly string[]): LensIdentificationReport {
  const entries: MaterialLensEntry[] = [], reasons: string[] = [], allWarnings = [...warnings];
  const decidedBy: Record<LensSignalKind, number> = {partRole: 0, part_role_legacy: 0, name_token: 0, transmission: 0, alpha: 0, none: 0};
  let lens = 0, frame = 0, mixed = 0;
  for (const material of materials) {
    const own = uses.filter(use => use.materialKey === material.key);
    const properties = seeThroughProperties(material.json);
    const base = {index: material.index, name: material.name, transmission: properties.transmission, alpha: properties.alpha,
      alpha_mode: properties.alphaMode};
    const reportUses = own.map(({key: _key, materialKey: _material, decision: _decision, ...row}) => row);
    if (!own.length) {
      entries.push({...base, class: 'frame', signal: 'unused', site: null, value: null, uses: [], notes: ['no primitive of the default scene uses this material']});
      continue;
    }
    const classes = new Set(own.map(use => use.class));
    const materialClass: MaterialClass = classes.size > 1 ? 'mixed' : own[0]!.class;
    // The representative use: for a mixed material its first lens use (why it is lens anywhere), otherwise the use
    // decided by the earliest rule.
    const candidates = materialClass === 'mixed' ? own.filter(use => use.class === 'lens') : own;
    const representative = candidates.reduce((best, use) => RULE_STRENGTH[use.rule] < RULE_STRENGTH[best.rule] ? use : best);
    decidedBy[representative.decision.kind]++;
    const notes: string[] = [];
    if (materialClass === 'frame' && (properties.transmission > 0 || properties.alphaMode === 'BLEND' && properties.alpha < 1)) {
      notes.push(representative.rule === 1 ? 'see-through, frame by its explicit role (crystal or clear part)'
        : 'see-through but frame: another part is identified as lens by role or name, so the last resort does not apply');
    }
    if (materialClass === 'mixed') {
      mixed++;
      const describe = (wanted: PartClass) => own.filter(use => use.class === wanted).map(use => `"${use.node_name}"`).join(', ');
      const reason = `material '${material.name}' is used by both lens and non-lens primitives`;
      reasons.push(reason);
      notes.push('cloned per class at load so both render');
      allWarnings.push(`Material "${material.name}"${material.index === null ? '' : ` (index ${material.index})`} is used by lens `
        + `primitives (${describe('lens')}) and frame primitives (${describe('frame')}); it was cloned per class so both render, `
        + 'and the §2 Components row fails.');
    } else if (materialClass === 'lens') lens++; else frame++;
    entries.push({...base, class: materialClass, signal: representative.decision.kind, site: representative.decision.site,
      value: representative.decision.value, uses: reportUses, notes});
  }
  const lensUses = uses.filter(use => use.class === 'lens'), frameUses = uses.filter(use => use.class === 'frame');
  if (!lensUses.length) reasons.push("no lens identified (partRole 'lens', a lens/glass name token, or transmission)");
  if (uses.length && !frameUses.length) {
    reasons.push('no frame/temple primitives (everything was identified as lens)');
    allWarnings.push('No frame or temple primitive: every primitive was identified as lens, and the §2 Components row fails.');
  }
  for (const use of lensUses) {
    const material = materials.find(row => row.key === use.materialKey);
    if (material && material.index === null && source === 'gltf-json') {
      reasons.push(`lens primitive '${use.node_name}' has no material, so it is not distinguished by material`);
      allWarnings.push(`Lens primitive "${use.node_name}" uses the glTF default material, so it is not distinguished by material; `
        + 'the §2 Components row fails.');
    }
  }
  return {rule_version: LENS_ID_RULE_VERSION, source, order: LENS_ID_ORDER,
    last_resort_used: uses.some(use => use.rule === 3), lens_material_count: lens, frame_material_count: frame,
    mixed_material_count: mixed, decided_by: decidedBy, components: {pass: reasons.length === 0, reasons}, materials: entries,
    warnings: allWarnings};
}

/** Identify every use of a glTF document (its JSON only). The report lists every glTF material. */
export function identifyGltfJson(json: unknown): {uses: GltfUse[]; decisions: Map<string, LensDecision>; report: LensIdentificationReport} {
  const uses = enumerateGltfUses(json);
  const signals = uses.map(use => gltfUseSignals(json, use));
  const labels = new Map(uses.map(use => [use.key, `node "${use.nodeName}" primitive ${use.primitive}`]));
  const {decisions, warnings} = classifyUses(signals, use => labels.get(use.key) ?? use.key);
  const doc = isRecord(json) ? json : {};
  const jsonMaterials = Array.isArray(doc.materials) ? doc.materials : [];
  const materials: ReportMaterial[] = jsonMaterials.map((material, index) => ({key: String(index), index,
    name: isRecord(material) && typeof material.name === 'string' ? material.name : `material${index}`, json: material}));
  if (uses.some(use => use.material === null)) materials.push({key: 'default', index: null, name: '(glTF default material)', json: null});
  const rows: ReportUseInput[] = uses.map(use => {
    const decision = decisions.get(use.key)!;
    return {key: use.key, materialKey: use.material === null ? 'default' : String(use.material), decision,
      node: use.node, node_name: use.nodeName, mesh: use.mesh, primitive: use.primitive, mode: use.mode, triangles: use.triangles,
      class: decision.isLens ? 'lens' : 'frame', rule: decision.rule, signal: decision.signal, site: decision.site};
  });
  return {uses, decisions, report: buildLensReport('gltf-json', materials, rows, warnings)};
}
