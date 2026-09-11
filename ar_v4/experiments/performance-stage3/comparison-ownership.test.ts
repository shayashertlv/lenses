import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {registerHooks} from 'node:module';
import {test} from 'node:test';
import type {TestContext} from 'node:test';
import {fileURLToPath} from 'node:url';
import {transformSync} from 'rolldown/utils';
import type {Detection} from '../../references/perfect-temples/src/runtime/detector.ts';
import type {HairMask, PairIdentity, HairModelContract, HeldHairInput} from '../hair-live-preview/renderer.ts';
import type {LiveVariant} from '../hair-live-preview/ownership.ts';
import type {ComparisonRenderer as Comparison, Pipeline} from './comparison-renderer.ts';

// Real public comparison orchestration, with only expensive renderer factories
// stubbed. DOM replacement models one connected #mirror; no WebGL visual claim.
const adapterUrl = new URL('./comparison-renderer.ts', import.meta.url).href;
const factoryUrls = new Map<string, Pipeline>([
  [new URL('../hair-live-preview/renderer.ts', import.meta.url).href, 'current'],
  [new URL('../performance-candidate/renderer.ts', import.meta.url).href, 'test'],
  [new URL('../performance-stage2/renderer.ts', import.meta.url).href, 'test2'],
  [new URL('./renderer.ts', import.meta.url).href, 'test3'],
]);
const eyewearUrl = new URL('../../references/perfect-temples/src/render/eyewear.ts', import.meta.url).href;
const factorySymbol = Symbol.for('performance-stage3-comparison-ownership-factories');
const hooks = registerHooks({
  resolve(specifier, context, nextResolve) {
    const url = new URL(specifier, context.parentURL ?? import.meta.url).href;
    return factoryUrls.has(url) ? {url, shortCircuit: true} : nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url === adapterUrl) {
      const compiled = transformSync(fileURLToPath(url), readFileSync(new URL(url), 'utf8'));
      if (compiled.errors.length) throw new Error(JSON.stringify(compiled.errors));
      return {format: 'module', shortCircuit: true, source: compiled.code};
    }
    const pipeline = factoryUrls.get(url);
    if (pipeline) return {format: 'module', shortCircuit: true, source:
      'export class LiveHairRenderer { static create(...args) { return globalThis[Symbol.for("performance-stage3-comparison-ownership-factories")].' + pipeline + '(...args); } }'};
    if (url === eyewearUrl) return {format: 'module', shortCircuit: true, source: "export const DEFAULT_EYEWEAR_ID = 'amber-horizon';"};
    return nextLoad(url, context);
  },
});
const {ComparisonRenderer} = await import(adapterUrl) as typeof import('./comparison-renderer.ts');
hooks.deregister();

interface Mount {current: StubCanvas; replacements: number;}
class StubCanvas extends EventTarget {
  width = 2; height = 2; id = 'mirror'; className = 'camera-output'; hidden = false;
  pixels = new Uint8ClampedArray(16).fill(37);
  mount: Mount | null = null;
  readonly clones: StubCanvas[] = [];
  get isConnected(): boolean {return this.mount?.current === this;}
  cloneNode(_deep = false): StubCanvas {
    const copy = new StubCanvas(); copy.width=this.width; copy.height=this.height;
    copy.id=this.id; copy.className=this.className; copy.hidden=this.hidden;
    this.clones.push(copy); return copy;
  }
  replaceWith(next: StubCanvas): void {
    assert.ok(this.mount && this.mount.current===this, 'Only the connected output can be replaced.');
    const slot=this.mount; this.mount=null; next.mount=slot; slot.current=next; slot.replacements++;
  }
  clone(): StubCanvas {const copy=new StubCanvas();copy.width=this.width;copy.height=this.height;copy.pixels=this.pixels.slice();return copy;}
}
interface OwnedInput {source: StubCanvas; detection: Detection; pair: PairIdentity; expectedModel: HairModelContract;}
class StubRenderer {
  prepares = 0; finishes = 0; publishes = 0; disposals = 0;
  failPrepare = false; failFinish = false; failExport = false;
  readonly nativeSamples = 4;
  readonly eyewear = {id: 'amber-horizon'};
  readonly receivedMasks: (HairMask | null)[] = [];
  readonly issuedSources: StubCanvas[] = [];
  readonly preparedInputs: OwnedInput[] = [];
  readonly hairPreparationRequests: boolean[] = [];
  owned: OwnedInput | null = null;
  pending = false;
  private disposed = false;
  private completed = false;
  private selected: LiveVariant = 'hair';
  private retainedMask: HairMask | null = null;
  private readonly display: StubCanvas;
  private readonly marker: number;
  constructor(display: StubCanvas, marker: number) { this.display = display; this.marker = marker; }
  get stats() { return this.disposed || this.pending || !this.completed ? null : {hasFace: this.owned?.detection.matrix !== null,
    hasMask: this.retainedMask !== null, maskOutputMode: this.retainedMask?.outputMode ?? null}; }
  get captureSnapshot() { return this.stats?.hasFace ? {eyewearMatrix: this.owned!.detection.matrix} : null; }
  setPreparationHairEnabled(enabled: boolean): void {this.hairPreparationRequests.push(enabled);}
  prepare(frame: HTMLCanvasElement, detection: Detection, pair: PairIdentity, model: HairModelContract): boolean {
    this.prepares++; this.pending = true; this.completed = false; this.retainedMask = null;
    if (this.failPrepare) { this.pending = false; this.owned = null; this.display.pixels.fill(0); throw new Error('injected prepare failure'); }
    this.owned = {source: (frame as unknown as StubCanvas).clone(), detection: structuredClone(detection), pair: {...pair}, expectedModel: structuredClone(model)};
    this.preparedInputs.push(this.owned);
    return detection.matrix !== null;
  }
  finish(mask: HairMask | null): boolean {
    assert.equal(this.pending, true); this.finishes++; this.pending = false;
    if (this.failFinish) { this.owned = null; this.display.pixels.fill(0); throw new Error('injected finish failure'); }
    this.receivedMasks.push(mask); this.retainedMask = mask ? structuredClone(mask) : null; this.completed = true;
    this.publish(); return this.owned!.detection.matrix !== null;
  }
  present(frame: HTMLCanvasElement, detection: Detection, mask: HairMask | null, pair: PairIdentity, model: HairModelContract): boolean {
    this.prepare(frame, detection, pair, model); return this.finish(mask);
  }
  selectVariant(variant: LiveVariant): void { this.selected = variant; this.publish(); }
  copyHeldInput(): HeldHairInput | null {
    if (!this.stats?.hasFace || !this.owned) return null;
    const source = this.owned.source.clone(); this.issuedSources.push(source);
    return {source: source as unknown as HTMLCanvasElement, detection: structuredClone(this.owned.detection),
      pair: {...this.owned.pair}, expectedModel: structuredClone(this.owned.expectedModel)};
  }
  exportDiagnostic(): Record<string, unknown> | null {
    if (this.failExport) throw new Error('injected export failure');
    if (!this.stats?.hasFace || !this.owned) return null;
    return {pair: {...this.owned.pair}, sourcePixels: Array.from(this.owned.source.pixels),
      detection: structuredClone(this.owned.detection), mask: structuredClone(this.retainedMask), variant: this.selected};
  }
  private publish(): void {
    if (this.disposed || this.pending || !this.completed || !this.owned) return;
    this.display.pixels = this.owned.source.pixels.slice(); this.display.pixels[0] = this.marker; this.publishes++;
  }
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true; this.disposals++; this.pending = false; this.owned = null; this.retainedMask = null; this.display.pixels.fill(0);
  }
}


const pipelines: Pipeline[] = ['current','test','test2','test3'];
function harness(t: TestContext) {
  const display=new StubCanvas(), source=new StubCanvas(), mount:Mount={current:display,replacements:0};display.mount=mount;
  const renderers={} as Record<Pipeline,StubRenderer>, factoryCanvases={} as Record<Pipeline,StubCanvas>, calls:Pipeline[]=[];
  const detection:Detection={matrix:Array.from({length:16},(_,i)=>Number(i%5===0)),inferenceMs:0,landmarks:Array.from({length:478},()=>({x:.5,y:.5,z:0}))};
  const pair:PairIdentity={sourceSHA256:'1'.repeat(64),detectionSHA256:'2'.repeat(64),eyewearModel:'amber-horizon'};
  const model={id:'hair-only',sha256:'3'.repeat(64),labels:['background','hair'],hairIndex:1};
  const mask:HairMask={...pair,model:model.id,modelSHA256:model.sha256,labels:[...model.labels],hairIndex:1,categorySHA256:'4'.repeat(64),outputMode:'category-only',width:2,height:2,category:new Uint8Array([0,1,1,0])};
  let renderer:Comparison|undefined;
  t.after(()=>renderer?.dispose());
  const create=async(options:{signal?:AbortSignal;fail?:Pipeline;gate?:{pipeline:Pipeline;entered:()=>void;wait:Promise<void>}}={})=>{
    const previous=Object.getOwnPropertyDescriptor(globalThis,factorySymbol);
    const factories=Object.fromEntries(pipelines.map((pipeline,index)=>[pipeline,async(canvas:HTMLCanvasElement)=>{
      calls.push(pipeline);factoryCanvases[pipeline]=canvas as unknown as StubCanvas;
      if(options.fail===pipeline)throw new Error('injected '+pipeline+' factory failure');
      if(options.gate?.pipeline===pipeline){options.gate.entered();await options.gate.wait;}
      return renderers[pipeline]=new StubRenderer(canvas as unknown as StubCanvas,(index+1)*11);
    }]));
    Object.defineProperty(globalThis,factorySymbol,{configurable:true,value:factories});
    try {renderer=await ComparisonRenderer.create(display as unknown as HTMLCanvasElement,options.signal??new AbortController().signal);return renderer;}
    finally {if(previous)Object.defineProperty(globalThis,factorySymbol,previous);else Reflect.deleteProperty(globalThis,factorySymbol);}
  };
  return {create,renderers,factoryCanvases,calls,display,source,mount,detection,pair,model,mask,canvas:source as unknown as HTMLCanvasElement};
}

test('four public factories preserve the legacy canvas; live output swaps only after a complete owned finish', async t => {
  const f=harness(t), renderer=await f.create();
  assert.deepEqual(f.calls,pipelines);
  for(const pipeline of ['current','test','test2'] as const)assert.equal(f.factoryCanvases[pipeline],f.display);
  const gpu=f.factoryCanvases.test3;assert.notEqual(gpu,f.display);assert.equal(gpu.isConnected,false);
  assert.equal(gpu.id,'mirror');assert.equal(gpu.className,f.display.className);
  renderer.present(f.canvas,f.detection,f.mask,f.pair,f.model);
  renderer.selectPipeline('test3');assert.equal(renderer.pipeline,'current');assert.equal(renderer.canvas,f.display as unknown as HTMLCanvasElement);
  renderer.prepare(f.canvas,f.detection,f.pair,f.model);
  assert.equal(renderer.pipeline,'test3');assert.equal(f.mount.current,f.display);assert.equal(f.display.pixels[0],11);
  assert.equal(renderer.stats,null);assert.equal(renderer.copyHeldInput(),null);
  assert.throws(()=>renderer.setHeld(),/Finish the owned pair/);
  assert.throws(()=>renderer.prepare(f.canvas,f.detection,f.pair,f.model),/already pending/);
  renderer.selectPipeline('test2');renderer.finish(f.mask);
  assert.equal(f.mount.current,gpu);assert.equal(renderer.canvas,gpu as unknown as HTMLCanvasElement);assert.equal(gpu.pixels[0],44);
  assert.equal(f.display.hidden,true);assert.equal(gpu.hidden,false);assert.equal(f.renderers.test2.prepares,0);
  renderer.prepare(f.canvas,f.detection,f.pair,f.model);
  assert.equal(renderer.pipeline,'test2');assert.equal(f.mount.current,gpu,'Pending legacy work must not expose its stale canvas.');
  renderer.finish(f.mask);assert.equal(f.mount.current,f.display);assert.equal(f.display.pixels[0],33);
  assert.equal(f.display.hidden,false);assert.equal(gpu.hidden,true);
  assert.equal(f.renderers.current.prepares,1);assert.equal(f.renderers.test.prepares,0);assert.equal(f.renderers.test3.prepares,1);
});

test('held four-way switching reuses exact source/pose/mask and invalidates every revision on full-mask upgrade', async t => {
  const f=harness(t),renderer=await f.create();
  renderer.present(f.canvas,f.detection,f.mask,f.pair,f.model);renderer.setHeld();
  const pair={...f.pair},detection=structuredClone(f.detection);
  f.source.pixels.fill(99);f.detection.landmarks[0]!.x=.9;f.pair.sourceSHA256='9'.repeat(64);
  for(const pipeline of ['test','test2','test3'] as const){
    renderer.selectPipeline(pipeline);const input=f.renderers[pipeline].preparedInputs[0]!;
    assert.equal(input.source.pixels[0],37);assert.deepEqual(input.pair,pair);assert.deepEqual(input.detection,detection);
    assert.equal(f.renderers[pipeline].receivedMasks[0],f.mask);
  }
  assert.equal(f.mount.current,f.factoryCanvases.test3);
  for(const pipeline of pipelines)renderer.selectPipeline(pipeline);
  for(const stub of Object.values(f.renderers))assert.equal(stub.prepares,1);
  const held=renderer.copyHeldInput()!,full:HairMask={...f.mask,outputMode:'full',confidence:new Float32Array([.1,.9,.9,.1]),confidenceSHA256:'5'.repeat(64)};
  renderer.present(held.source,held.detection,full,held.pair,held.expectedModel);
  const selectedCanvas=renderer.canvas,output=renderer.exportDiagnostic()!;
  assert.equal(output.schema,'ar-performance-stage3-comparison-v1');assert.equal(output.selectedPipeline,'test3');
  assert.equal(output.candidateAccepted,false);assert.equal(renderer.canvas,selectedCanvas);assert.equal(f.mount.current,f.factoryCanvases.test3);
  for(const pipeline of pipelines){assert.deepEqual(output[pipeline],output.current);assert.equal(f.renderers[pipeline].prepares,2);assert.equal(f.renderers[pipeline].receivedMasks[1],full);}
  assert.equal(held.source.width,2,'The explicit caller-owned held source remains usable until its caller releases it.');
  held.source.width=held.source.height=0;
  assert.ok(Object.values(f.renderers).flatMap(stub=>stub.issuedSources).every(source=>source.width===0&&source.height===0));
});

test('accepted-only Test3 skips new hair, but an exact held mask upgrade and inactive exports request it', async t => {
  const f=harness(t),renderer=await f.create();renderer.selectPipeline('test3');renderer.selectVariant('accepted');
  renderer.present(f.canvas,f.detection,null,f.pair,f.model);renderer.setHeld();
  assert.deepEqual(f.renderers.test3.hairPreparationRequests,[false]);
  const held=renderer.copyHeldInput()!,full:HairMask={...f.mask,outputMode:'full',confidence:new Float32Array([.1,.9,.9,.1]),confidenceSHA256:'5'.repeat(64)};
  renderer.present(held.source,held.detection,full,held.pair,held.expectedModel);renderer.exportDiagnostic();
  assert.deepEqual(f.renderers.test3.hairPreparationRequests,[false,true]);
  for(const pipeline of ['current','test','test2'] as const)assert.deepEqual(f.renderers[pipeline].hairPreparationRequests,[true]);
  assert.equal(renderer.pipeline,'test3');assert.equal(renderer.variant,'accepted');assert.equal(f.mount.current,f.factoryCanvases.test3);
  held.source.width=held.source.height=0;
});

test('inactive held prepare/finish/export failures restore both selected canvas and pixels in either direction', async t => {
  for(const selected of ['current','test3'] as const)for(const target of pipelines.filter(p=>p!==selected))for(const failure of ['failPrepare','failFinish','failExport'] as const){
    const f=harness(t),renderer=await f.create();renderer.selectPipeline(selected);
    renderer.present(f.canvas,f.detection,f.mask,f.pair,f.model);renderer.setHeld();
    const mounted=f.mount.current,pixels=mounted.pixels.slice();f.renderers[target][failure]=true;
    assert.throws(()=>renderer.exportDiagnostic(),/injected/);
    assert.equal(renderer.pipeline,selected);assert.equal(renderer.requestedPipeline,selected);
    assert.equal(renderer.canvas,mounted as unknown as HTMLCanvasElement);assert.equal(f.mount.current,mounted);assert.deepEqual(mounted.pixels,pixels);
    assert.ok(Object.values(f.renderers).flatMap(stub=>stub.issuedSources).every(source=>source.width===0&&source.height===0));
    f.renderers[target][failure]=false;const output=renderer.exportDiagnostic()!;
    for(const pipeline of pipelines)assert.deepEqual(output[pipeline],output.current);
    assert.equal(f.mount.current,mounted);assert.deepEqual(mounted.pixels,pixels);
    renderer.dispose();
  }
});

test('no-face clears owned metadata; disposal cancels pending work and restores legacy canvas for restart', async t => {
  const f=harness(t),renderer=await f.create();renderer.selectPipeline('test3');
  renderer.present(f.canvas,f.detection,f.mask,f.pair,f.model);
  assert.equal(renderer.present(f.canvas,{...f.detection,matrix:null,landmarks:[]},null,f.pair,f.model),false);
  assert.equal(renderer.stats!.hasFace,false);assert.equal(renderer.captureSnapshot,null);assert.equal(renderer.copyHeldInput(),null);assert.equal(renderer.exportDiagnostic(),null);
  renderer.prepare(f.canvas,f.detection,f.pair,f.model);const published=f.renderers.test3.publishes;
  renderer.dispose();renderer.dispose();
  assert.equal(f.mount.current,f.display);assert.equal(renderer.canvas,f.display as unknown as HTMLCanvasElement);
  for(const stub of Object.values(f.renderers))assert.equal(stub.disposals,1);
  assert.equal(renderer.finish(f.mask),false);assert.equal(renderer.prepare(f.canvas,f.detection,f.pair,f.model),false);
  renderer.selectPipeline('current');renderer.selectVariant('hair');assert.equal(f.renderers.test3.publishes,published);
  assert.equal(renderer.stats,null);assert.equal(renderer.exportDiagnostic(),null);
  const restarted=await f.create();assert.equal(restarted.canvas,f.display as unknown as HTMLCanvasElement);
  for(const pipeline of ['current','test','test2'] as const)assert.equal(f.factoryCanvases[pipeline],f.display);
});

test('GPU context loss forwards to the owned legacy session once and disposal removes forwarding', async t => {
  const f=harness(t),renderer=await f.create();renderer.selectPipeline('test3');renderer.present(f.canvas,f.detection,f.mask,f.pair,f.model);
  let forwarded=0;f.display.addEventListener('webglcontextlost',event=>{assert.equal(event.cancelable,true);forwarded++;renderer.dispose();});
  const lost=new Event('webglcontextlost',{cancelable:true});f.factoryCanvases.test3.dispatchEvent(lost);
  assert.equal(lost.defaultPrevented,true);assert.equal(forwarded,1);assert.equal(f.mount.current,f.display);
  f.factoryCanvases.test3.dispatchEvent(new Event('webglcontextlost',{cancelable:true}));assert.equal(forwarded,1);
});

test('cancellation during fourth factory creation disposes late Test3 and all initialized renderers without swapping', async t => {
  const f=harness(t),abort=new AbortController();let release!:()=>void,entered!:()=>void;
  const wait=new Promise<void>(resolve=>{release=resolve;}),started=new Promise<void>(resolve=>{entered=resolve;});
  const pending=f.create({signal:abort.signal,gate:{pipeline:'test3',entered,wait}});
  await started;assert.equal(f.mount.current,f.display);abort.abort();release();
  await assert.rejects(pending,{name:'AbortError'});
  for(const stub of Object.values(f.renderers)){assert.equal(stub.disposals,1);assert.equal(stub.prepares,0);}
  assert.equal(f.factoryCanvases.test3.width,0);assert.equal(f.factoryCanvases.test3.height,0);assert.equal(f.mount.replacements,0);
});

test('failure at any factory releases earlier renderers and leaves the original DOM canvas mounted', async t => {
  for(const [index,pipeline] of pipelines.entries()){
    const f=harness(t);await assert.rejects(f.create({fail:pipeline}),/injected .* factory failure/);
    assert.deepEqual(f.calls,pipelines.slice(0,index+1));
    for(const previous of pipelines.slice(0,index))assert.equal(f.renderers[previous].disposals,1);
    assert.equal(f.mount.current,f.display);assert.equal(f.mount.replacements,0);
    assert.equal(f.display.clones[0]!.width,0);assert.equal(f.display.clones[0]!.height,0);
  }
});
