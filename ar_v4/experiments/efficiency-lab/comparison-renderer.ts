import {LiveHairRenderer as BaseRenderer} from '../speed-lab/renderer.ts';
import {PROFILES as G_PROFILES} from '../speed-lab/profiles.ts';
import {LiveHairRenderer as CandidateRenderer} from './renderer.ts';
import {PIPELINES, PROFILES, usesBaseRenderer, G_COMMIT} from './profiles.ts';
import type {Pipeline} from './profiles.ts';
import {createOwnedSourceFrame} from './speed-options.ts';
import type {OwnedSourceFrame} from './speed-options.ts';
import type {HairMask, PairIdentity, HairModelContract, HeldHairInput, LiveHairStats} from '../hair-live-preview/renderer.ts';
import type {LiveVariant} from '../hair-live-preview/ownership.ts';
import type {Detection} from '../../references/perfect-temples/src/runtime/detector.ts';
import {DEFAULT_EYEWEAR_ID} from '../../references/perfect-temples/src/render/eyewear.ts';
import type {EyewearId} from '../../references/perfect-temples/src/render/eyewear.ts';
export type {Pipeline, HairMask};

interface HeldOutput {diagnostic: Record<string, unknown>; accepted: ImageData; hair: ImageData;
  snapshot: BaseRenderer['captureSnapshot'] | CandidateRenderer['captureSnapshot'];}
/** Two renderer instances: unchanged G and one separately configured test.
 * Async preparation stays private. The chosen profile is frozen for each pair. */
export class ComparisonRenderer {
  private requested: Pipeline = 'g';
  private heldGeneration=0;
  private active: Pipeline = 'g';
  private pending = false;
  private held = false;
  private disposed = false;
  private selectedVariant: LiveVariant = 'hair';
  private mask: HairMask | null = null;
  private outputs = new Map<Pipeline,HeldOutput>();
  private heldInput: HeldHairInput | null = null;
  private cachedDiagnostic: Record<string,unknown> | null = null;
  private constructor(private display: HTMLCanvasElement, private base: BaseRenderer, private candidate: CandidateRenderer) {}
  static async create(display: HTMLCanvasElement, signal: AbortSignal, id: EyewearId = DEFAULT_EYEWEAR_ID,
    onStartupStage?: (stage: 'g-renderer' | 'candidate-renderer') => void): Promise<ComparisonRenderer> {
    onStartupStage?.('g-renderer');
    const base = await BaseRenderer.create(display,signal,id);
    try {
      if(signal.aborted) throw new DOMException('Startup cancelled.','AbortError');
      onStartupStage?.('candidate-renderer');
      const candidate = await CandidateRenderer.create(display,signal,id);
      if(signal.aborted) {candidate.dispose();throw new DOMException('Startup cancelled.','AbortError');}
      return new ComparisonRenderer(display,base,candidate);
    } catch(error) {base.dispose();throw error;}
  }
  private get renderer() {return usesBaseRenderer(this.active) ? this.base : this.candidate;}
  get pipeline(): Pipeline {return this.active;}
  get requestedPipeline(): Pipeline {return this.requested;}
  get stats(): LiveHairStats | null {
    if(this.disposed || this.pending) return null;
    const heldStats=this.outputs.get(this.active)?.diagnostic.stats as LiveHairStats | undefined;
    return this.held ? heldStats ? {...structuredClone(heldStats),selectedVariant:this.selectedVariant} : null : this.renderer.stats;
  }
  get captureSnapshot() {
    if(this.pending||this.disposed)return null;
    if(!this.held)return this.renderer.captureSnapshot;
    const output=this.outputs.get(this.active), snapshot=output?.snapshot;
    if(!snapshot)return null;
    return {...structuredClone(snapshot),hairPreview:{...structuredClone(snapshot.hairPreview),
      variant:this.selectedVariant,applied:this.selectedVariant==='hair'&&snapshot.hairPreview.applied}};
  }
  get nativeSamples(): number {return this.renderer.nativeSamples;}
  get eyewear() {return this.renderer.eyewear;}
  get variant(): LiveVariant {return this.selectedVariant;}
  selectPipeline(id: Pipeline): void {
    if(!PIPELINES.includes(id))throw new Error('Unknown experiment.');
    this.requested=id;
    if(this.held && !this.pending) {this.active=id;this.showHeld();}
  }
  selectVariant(value: LiveVariant): void {
    this.selectedVariant=value;
    if(this.held) this.showHeld(); else if(!this.disposed) this.renderer.selectVariant(value);
  }
  async prepare(frame: HTMLCanvasElement, detection: Detection, pair: PairIdentity, model: HairModelContract,
    needsHair=this.selectedVariant==='hair', source?: OwnedSourceFrame, onNativeSubmitted?:()=>void): Promise<boolean> {
    if(this.disposed)throw new DOMException('Renderer closed.','AbortError');
    if(this.pending)throw new Error('A comparison pair is already pending.');
    this.active=this.requested;this.pending=true;this.mask=null;this.cachedDiagnostic=null;
    const target=this.renderer;
    try {
      target.setPreparationHairEnabled(needsHair);
      const visible=usesBaseRenderer(this.active) ? await this.base.prepare(frame,detection,pair,model,{options:G_PROFILES.combined.options,source})
        : await this.candidate.prepare(frame,detection,pair,model,{options:PROFILES[this.active].options,source,onNativeSubmitted});
      if(this.disposed)throw new DOMException('Renderer closed.','AbortError');
      target.selectVariant(this.selectedVariant);return visible;
    } catch(error) {this.pending=false;throw error;}
  }
  finish(mask: HairMask | null): boolean {
    if(this.disposed || !this.pending)throw new Error('No owned comparison pair.');
    try {const visible=this.renderer.finish(mask);this.mask=mask;return visible;}
    finally {this.pending=false;}
  }
  async present(frame: HTMLCanvasElement, detection: Detection, mask: HairMask|null,pair:PairIdentity,model:HairModelContract):Promise<boolean> {
    const ownedMask=mask?structuredClone(mask):null;
    const context=frame.getContext('2d',{willReadFrequently:true,colorSpace:'srgb'});
    if(!context)throw new Error('Missing held source context.');
    const source=createOwnedSourceFrame(frame,context.getImageData(0,0,frame.width,frame.height),{
      sourceSHA256:pair.sourceSHA256,generation:++this.heldGeneration,sessionId:'held',isCurrent:()=>!this.disposed});
    await this.prepare(frame,detection,pair,model,ownedMask!==null || this.selectedVariant==='hair',source);return this.finish(ownedMask);
  }
  copyHeldInput():HeldHairInput|null {return this.disposed || this.pending ? null : this.renderer.copyHeldInput();}
  private pixels():ImageData {
    const context=this.display.getContext('2d',{willReadFrequently:true,colorSpace:'srgb'});
    if(!context)throw new Error('Missing comparison display.');
    return context.getImageData(0,0,this.display.width,this.display.height);
  }
  async setHeld(pipelines: readonly Pipeline[] = PIPELINES):Promise<void> {
    if(this.pending || this.disposed)throw new Error('Finish the owned pair before Hold.');
    if(!Array.isArray(pipelines) || pipelines.length===0 || pipelines.length>PIPELINES.length)
      throw new Error('Invalid held comparison choices.');
    const choices=[...pipelines];
    if(choices.some(id=>!PIPELINES.includes(id)) || new Set(choices).size!==choices.length
      || !choices.includes('g') || !choices.includes(this.active))
      throw new Error('Held comparison choices must be unique known profiles and include G and the selected profile.');
    // Own the requested scope before asynchronous work; canonical order renders
    // G first so its input-only aliases always share a complete owned output.
    const heldPipelines=Object.freeze(PIPELINES.filter(id=>choices.includes(id)));
    const input=this.copyHeldInput();if(!input)throw new Error('The held pair is unavailable.');
    const previousInput=this.heldInput, previousOutputs=this.outputs, previousDiagnostic=this.cachedDiagnostic;
    const previousPixels=this.pixels(), nextOutputs=new Map<Pipeline,HeldOutput>();
    const selected=this.active, variant=this.selectedVariant, mask=this.mask;
    this.held=false;
    try {
      for(const profile of heldPipelines) {
        if(this.disposed)return;
        if(profile!=='g' && usesBaseRenderer(profile)) {nextOutputs.set(profile,nextOutputs.get('g')!);continue;}
        this.requested=profile;
        await this.present(input.source,input.detection,mask,input.pair,input.expectedModel);
        const target=this.renderer, diagnostic=target.exportDiagnostic();
        if(!diagnostic)throw new Error(`Missing held ${profile} diagnostic.`);
        target.selectVariant('accepted');const accepted=this.pixels();
        target.selectVariant('hair');const hair=this.pixels();
        nextOutputs.set(profile,{diagnostic,accepted,hair,snapshot:target.captureSnapshot});
      }
      this.cachedDiagnostic={schema:'ar-efficiency-comparison-v1',baseCommit:G_COMMIT,
        candidateAccepted:false,comparedPipelines:[...heldPipelines],
        inputPolicy:heldPipelines.length===2 && heldPipelines.includes('mask-bytes')
          ? 'G and V share the exact held source, detection, pose and SDK full mask. V reuses the owned G held pixels because both use the same renderer. This shared SDK full mask comparison does not independently test V live mask extraction or temporal effects; live extraction requires separate matching-mask evidence.'
          : 'Every output uses the same exact held source, detection and mask. Input-only mode I, rate modes M/N/O, extraction modes P/V, publication mode Q, statistics mode T and scheduling mode U share G held pixels. Renderer candidates including R/S/W/X render their own held outputs. The held diagnostic upgrades to the SDK full mask for all choices; it does not independently test live extraction or temporal effects. P/V extraction is checked separately against the installed SDK on matching masks.',
        ...Object.fromEntries([...nextOutputs].map(([id,value])=>[id,value.diagnostic]))};
      this.outputs=nextOutputs;this.heldInput=input;this.held=true;
      if(previousInput)previousInput.source.width=previousInput.source.height=0;
    } catch(error) {
      this.outputs=previousOutputs;this.cachedDiagnostic=previousDiagnostic;
      this.held=previousDiagnostic!==null;
      input.source.width=input.source.height=0;
      if(!this.disposed){this.display.width=previousPixels.width;this.display.height=previousPixels.height;this.display.getContext('2d')!.putImageData(previousPixels,0,0);}
      throw error;
    } finally {
      if(this.disposed && this.heldInput!==input)input.source.width=input.source.height=0;
      this.active=this.requested=selected;this.selectedVariant=variant;
      if(!this.disposed)this.showHeld();
    }
  }
  private showHeld():void {
    if(this.disposed)return;const output=this.outputs.get(this.active);if(!output)return;
    const pixels=this.selectedVariant==='hair'?output.hair:output.accepted;
    if(this.display.width!==pixels.width||this.display.height!==pixels.height){this.display.width=pixels.width;this.display.height=pixels.height;}
    this.display.getContext('2d')!.putImageData(pixels,0,0);
  }
  exportDiagnostic():Record<string,unknown>|null {
    return this.disposed || this.pending || !this.held || !this.cachedDiagnostic ? null
      : structuredClone({...this.cachedDiagnostic,selectedPipeline:this.active});
  }
  dispose():void {
    if(this.disposed)return;this.disposed=true;this.pending=false;this.mask=null;this.outputs.clear();
    if(this.heldInput)this.heldInput.source.width=this.heldInput.source.height=0;
    this.heldInput=null;this.cachedDiagnostic=null;this.candidate.dispose();this.base.dispose();
  }
}
