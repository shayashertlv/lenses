import test from 'node:test';
import assert from 'node:assert/strict';
import {inside} from '../hair-arm-preview/compose.ts';
import type {HairArmInput} from '../hair-arm-preview/compose.ts';
import type {LiveHairArmInput} from '../hair-live-preview/live-mask.ts';
import {composeHairArmsFast,checkHairProtection} from '../performance-candidate/fast-compose.ts';
import {findDetachedTemplePixels} from '../hair-live-preview/continuity.ts';
import type {ContinuityInput} from '../hair-live-preview/continuity.ts';
import {HAIR_FLAGS,buildGpuRegionMembership,validateGpuHairInput,analyzePackedHairFlags,
  findDetachedTemplePixelsFromFlags,changedPixelsAfterContinuity,successfulGpuProtectionChecks,readPackedHairFlag} from './gpu-rules.ts';
import type {PackedHairFlags} from './gpu-rules.ts';

function fixture(seed: number, width = 23, height = 17): HairArmInput {
  let state = seed;
  const random = (): number => { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state / 2 ** 32; };
  const before = new Uint8ClampedArray(width * height * 4), background = before.slice();
  const labels = seed % 2 ? ['background', 'hair'] : ['background', 'hair', 'body-skin', 'face-skin', 'clothes', 'others'];
  const pair = {sourceSHA256: '1'.repeat(64), detectionSHA256: '2'.repeat(64), eyewearModel: seed % 2 ? 'tom-ford-clear' : 'amber-horizon'};
  const maskWidth = Math.max(1, Math.floor(width * ([.4, 1, 1.7][seed % 3]!))), maskHeight = Math.max(1, Math.floor(height * .7));
  const category = new Uint8Array(maskWidth * maskHeight), confidence = new Float32Array(category.length);
  for (let index = 0; index < category.length; index++) {
    category[index] = random() < .75 ? 1 : Math.floor(random() * labels.length); confidence[index] = random();
  }
  const input: HairArmInput = {width, height, before, background, pair, geometryPair: {...pair},
    expectedModel: {id: 'test-pinned-model', sha256: '3'.repeat(64), labels, hairIndex: 1},
    mask: {...pair, model: 'test-pinned-model', modelSHA256: '3'.repeat(64), categorySHA256: '4'.repeat(64), confidenceSHA256: '5'.repeat(64),
      labels, hairIndex: 1, width: maskWidth, height: maskHeight, category, confidence},
    protection: {method: 'temple-optics-copy-v1', width, height, marginPx: 4,
      protectedRects: [{x0: 2, y0: 2, x1: width - 5, y1: 5}, {x0: 5, y0: 4, x1: 8, y1: 9}],
      editableRects: [{x0: 1, y0: 0, x1: width, y1: height}, {x0: 0, y0: 3, x1: width - 2, y1: height - 1}]},
    noseRoi: {x0: 5, y0: 5, x1: 9, y1: 10}};
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const offset = (y * width + x) * 4;
    const bounded = input.protection.protectedRects.some(rect => inside(rect, x, y))
      || input.protection.editableRects.some(rect => inside(rect, x, y)) || inside(input.noseRoi, x, y);
    for (let channel = 0; channel < 4; channel++) {
      const value = Math.floor(random() * 256); background[offset + channel] = before[offset + channel] = value;
      if (bounded && random() < .55) before[offset + channel] = Math.floor(random() * 256);
    }
  }
  return input;
}

function continuityFixture(reversed = false): ContinuityInput {
  const width = 64, height = 18, background = new Uint8ClampedArray(width * height * 4).fill(20);
  for (let i = 3; i < background.length; i += 4) background[i] = 255;
  const before = background.slice();
  for (let y = 8; y <= 10; y++) for (let x = 8; x <= 50; x++) for (let c = 0; c < 3; c++) before[(y * width + x) * 4 + c] = 200;
  const points = reversed ? [{x: 54.5, y: 9.5, radiusPx: 2, progressPx: 0}, {x: 7.5, y: 9.5, radiusPx: 2, progressPx: 47}]
    : [{x: 7.5, y: 9.5, radiusPx: 2, progressPx: 0}, {x: 54.5, y: 9.5, radiusPx: 2, progressPx: 47}];
  return {width, height, before, background, after: before.slice(),
    paths: [{side: 0, points, lengthPx: 47}, {side: 1, points: points.map(point => ({...point, y: 30})), lengthPx: 47}],
    protection: {method: 'temple-optics-copy-v1', width, height, marginPx: 4,
      protectedRects: [{x0: 0, y0: 0, x1: 5, y1: 4}, {x0: 24, y0: 0, x1: 28, y1: 3}],
      editableRects: [{x0: 5, y0: 6, x1: 58, y1: 14}]}, noseRoi: {x0: 29, y0: 0, x1: 32, y1: 4}};
}
function hideBand(input: ContinuityInput, x0 = 28, x1 = 34, baselineToo = false): void {
  for (let y = 8; y <= 10; y++) for (let x = x0; x <= x1; x++) {
    const index = (y * input.width + x) * 4;
    input.after.set(input.background.subarray(index, index + 4), index);
    if (baselineToo) input.before.set(input.background.subarray(index, index + 4), index);
  }
}

const different=(a:Uint8ClampedArray,b:Uint8ClampedArray,offset:number,channels=4):boolean=>{
  for(let channel=0;channel<channels;channel++) if(a[offset+channel]!==b[offset+channel]) return true;
  return false;
};
function pack(width:number,height:number,values:Uint8Array,rowOrder:PackedHairFlags['rowOrder']):PackedHairFlags {
  const rowBytes=Math.ceil(width/4)*4,bytes=new Uint8Array(rowBytes*height);
  for(let y=0;y<height;y++) bytes.set(values.subarray(y*width,(y+1)*width),(rowOrder==='bottom-up'?height-1-y:y)*rowBytes);
  return {width,height,bytes,rowOrder};
}
/** Independent integer formulation of the proposed shader, compared below to the
 * existing floating-point compositor. It does not claim browser shader execution. */
function integerComposition(input:LiveHairArmInput,rowOrder:PackedHairFlags['rowOrder']='bottom-up') {
  const {width,height,before,background,mask}=input,regions=buildGpuRegionMembership(input);
  const after=before.slice(),values=new Uint8Array(width*height);
  const hair=(x:number,y:number):boolean=>{
    const mx=Math.min(mask.width-1,Math.floor(((2*x+1)*mask.width)/(2*width)));
    const my=Math.min(mask.height-1,Math.floor(((2*y+1)*mask.height)/(2*height)));
    return mask.category[my*mask.width+mx]===mask.hairIndex;
  };
  let referenceMaxDelta=0;
  for(let y=0;y<height;y++) for(let x=0;x<width;x++) {
    const index=y*width+x,offset=index*4,region=regions[index]!,residual=different(before,background,offset);
    const eligible=residual && (region&4)!==0 && (region&3)===0,h=hair(x,y);
    let half=false;
    if(eligible && h) {
      half=x===0 || y===0 || x===width-1 || y===height-1 || !hair(x-1,y) || !hair(x+1,y) || !hair(x,y-1) || !hair(x,y+1);
      for(let channel=0;channel<3;channel++) after[offset+channel]=half
        ? Math.floor((before[offset+channel]!+background[offset+channel]!+1)/2):background[offset+channel]!;
    }
    values[index]=(residual?1:0)|(eligible?2:0)|(different(after,background,offset)?4:0)
      |(different(after,before,offset)?8:0)|(h?16:0)|(half?32:0)|(different(before,background,offset,3)?64:0);
    if(region===0) for(let channel=0;channel<4;channel++) referenceMaxDelta=Math.max(referenceMaxDelta,Math.abs(before[offset+channel]!-background[offset+channel]!));
  }
  return {after,flags:pack(width,height,values,rowOrder),regions,referenceMaxDelta};
}
function continuityFlags(input:ContinuityInput,rowOrder:PackedHairFlags['rowOrder']='bottom-up'):PackedHairFlags {
  const values=new Uint8Array(input.width*input.height);
  const inside=(rect:ContinuityInput['noseRoi'],x:number,y:number)=>x>=rect.x0&&x<rect.x1&&y>=rect.y0&&y<rect.y1;
  for(let index=0;index<values.length;index++) {
    const x=index%input.width,y=Math.floor(index/input.width),offset=index*4;
    const residual=different(input.before,input.background,offset);
    const eligible=residual && !inside(input.noseRoi,x,y) && !input.protection.protectedRects.some(rect=>inside(rect,x,y))
      && input.protection.editableRects.some(rect=>inside(rect,x,y));
    values[index]=(residual?1:0)|(eligible?2:0)|(different(input.after,input.background,offset)?4:0)
      |(different(input.after,input.before,offset)?8:0)|(different(input.before,input.background,offset,3)?64:0);
  }
  return pack(input.width,input.height,values,rowOrder);
}
function equalContinuity(input:ContinuityInput):void {
  const {before:_before,background:_background,after:_after,...metadata}=input;
  const original=findDetachedTemplePixels(input);
  for(const rowOrder of ['bottom-up','top-down'] as const) {
    const candidate=findDetachedTemplePixelsFromFlags({...metadata,flags:continuityFlags(input,rowOrder)});
    assert.deepEqual(candidate,original);
  }
}

test('integer category sampling/blending, compact flags, every statistic and passing guard counts equal Test1',()=>{
  for(let seed=1;seed<=48;seed++) for(const rowOrder of ['bottom-up','top-down'] as const) {
    const input=fixture(seed,seed%3===0?127:29,seed%3===0?85:19);
    const cpu=composeHairArmsFast(input,{collectEligibleResidualIndices:true});
    const gpu=integerComposition(input,rowOrder),analysis=analyzePackedHairFlags(input,gpu.flags,gpu.referenceMaxDelta,gpu.regions);
    assert.equal(cpu.fallbackReason,null);assert.deepEqual(gpu.after,cpu.pixels);assert.deepEqual(gpu.regions,cpu.regions);
    assert.deepEqual(analysis.statistics,cpu.statistics);assert.deepEqual(analysis.backgroundReferenceCheck,cpu.backgroundReferenceCheck);
    assert.deepEqual(analysis.eligibleResidualIndices,cpu.eligibleResidualIndices);assert.equal(analysis.fallbackReason,null);
    assert.deepEqual(successfulGpuProtectionChecks(analysis.protectionTested,[0,0,0,0]),checkHairProtection(input,gpu.after,gpu.regions));
  }
});

test('all byte half-weight ties round upward and alpha-only residuals remain visible without changing RGB',()=>{
  for(let b=0;b<=255;b++) for(let c=0;c<=255;c++) assert.equal(Math.floor((b+c+1)/2),Math.round(b*.5+c*.5));
  const input=fixture(1),index=11*input.width+2,offset=index*4;input.mask.category.fill(1);
  input.before.set(input.background.subarray(offset,offset+4),offset);input.before[offset+3]=input.background[offset+3]===1?2:1;
  const gpu=integerComposition(input),flag=readPackedHairFlag(gpu.flags,index);
  assert.ok(flag&HAIR_FLAGS.RESIDUAL);assert.ok(flag&HAIR_FLAGS.ELIGIBLE);assert.ok(flag&HAIR_FLAGS.VISIBLE);
  assert.equal(flag&HAIR_FLAGS.RGB_RESIDUAL,0);assert.equal(flag&HAIR_FLAGS.CHANGED,0);
  assert.equal(gpu.after[offset+3],input.before[offset+3]);assert.equal(changedPixelsAfterContinuity(gpu.flags,0,new Uint32Array([index])),0);
});

test('category-only storage has the same rules without manufactured confidence; membership reuse clears old rectangles',()=>{
  const original=fixture(4),{confidence:_confidence,confidenceSHA256:_confidenceHash,...mask}=original.mask;
  const input:LiveHairArmInput={...original,mask:{...mask,outputMode:'category-only'}};
  const gpu=integerComposition(input),cpu=composeHairArmsFast(input);
  assert.deepEqual(gpu.after,cpu.pixels);assert.deepEqual(analyzePackedHairFlags(input,gpu.flags,0).statistics,cpu.statistics);
  const storage=buildGpuRegionMembership(input);storage.fill(255);
  assert.equal(buildGpuRegionMembership(input,storage),storage);assert.deepEqual(storage,cpu.regions);
});

test('reference mismatch retains original counts/maxDelta, resets composition statistics, and cannot publish passing checks',()=>{
  const input=fixture(1);input.before[0]=input.background[0]===0?255:0;
  const gpu=integerComposition(input),cpu=composeHairArmsFast(input),analysis=analyzePackedHairFlags(input,gpu.flags,gpu.referenceMaxDelta);
  assert.equal(analysis.fallbackReason,cpu.fallbackReason);assert.deepEqual(analysis.backgroundReferenceCheck,cpu.backgroundReferenceCheck);
  assert.deepEqual(analysis.statistics,cpu.statistics);assert.equal(analysis.eligibleResidualIndices,null);
  assert.throws(()=>analyzePackedHairFlags(input,gpu.flags,0),/reduction disagrees/);
  for(const deltas of [[0,0,0,1],[0,0,0], [0,0,NaN,0],[-1,0,0,0]]) assert.throws(()=>successfulGpuProtectionChecks(analysis.protectionTested,deltas),/protection check failed/);
});

test('mask, identity, model and protection validation preserve original rejection messages',()=>{
  const changes:((input:HairArmInput)=>void)[]=[
    input=>{input.pair.sourceSHA256='bad';},input=>{input.geometryPair.detectionSHA256='9'.repeat(64);},
    input=>{input.mask.sourceSHA256='9'.repeat(64);},input=>{input.mask.model='another';},
    input=>{input.mask.categorySHA256='bad';},input=>{input.mask.labels=['face','hair'];},
    input=>{input.mask.category[0]=255;},input=>{input.mask.confidence[0]=NaN;},
    input=>{input.mask.confidence=new Float32Array(0);},
    input=>{input.noseRoi={x0:-1,y0:1,x1:3,y1:3};},
    input=>{input.protection={...input.protection,method:'invalid' as 'temple-optics-copy-v1'};},
  ];
  for(const change of changes) {
    const input=fixture(1);change(input);const expected=composeHairArmsFast(input).fallbackReason;
    assert.ok(expected);assert.throws(()=>validateGpuHairInput(input),error=>error instanceof Error&&error.message===expected);
  }
});

test('malformed compact storage, padding, reserved flags and region contradictions fail closed',()=>{
  const input=fixture(1),gpu=integerComposition(input);
  assert.throws(()=>analyzePackedHairFlags(input,{...gpu.flags,bytes:new Uint8Array(0)},0),/storage\/dimensions/);
  const corrupt={...gpu.flags,bytes:gpu.flags.bytes.slice()};corrupt.bytes[0]=corrupt.bytes[0]!|128;
  assert.throws(()=>analyzePackedHairFlags(input,corrupt,0),/predicates contradict/);
  corrupt.bytes=gpu.flags.bytes.slice();corrupt.bytes[input.width]=1;
  assert.throws(()=>analyzePackedHairFlags(input,corrupt,0),/padding/);
  corrupt.bytes=gpu.flags.bytes.slice();corrupt.bytes[0]=corrupt.bytes[0]!^2;
  assert.throws(()=>analyzePackedHairFlags(input,corrupt,0),/predicates contradict/);
});

test('compact continuity exactly matches original components/order for both directions, existing gaps, partial visibility and ambiguity',()=>{
  for(const reversed of [false,true]) for(const mode of ['unchanged','new-gap','old-gap','partial','ambiguous','near-tie'] as const) {
    const input=continuityFixture(reversed);
    if(mode==='new-gap'||mode==='old-gap'||mode==='ambiguous'||mode==='near-tie') hideBand(input,28,34,mode==='old-gap');
    if(mode==='partial') for(let y=8;y<=10;y++) for(let x=28;x<=34;x++) for(let c=0;c<3;c++) input.after[(y*input.width+x)*4+c]=80;
    if(mode==='ambiguous') input.paths=input.paths!.map(path=>({...path,points:input.paths![0]!.points}));
    if(mode==='near-tie') input.paths=input.paths!.map((path,index)=>({...path,points:input.paths![0]!.points.map(point=>({...point,y:point.y+(index?1-1e-12:0)}))}));
    equalContinuity(input);
  }
  const baseline=continuityFixture();hideBand(baseline);
  assert.equal(findDetachedTemplePixelsFromFlags({...baseline,flags:continuityFlags(baseline)}).indices.length,48);
});

test('sparse continuity candidates independently recheck all guards and support exact final changed counts',()=>{
  const input=continuityFixture();hideBand(input);input.noseRoi={x0:40,y0:8,x1:46,y1:11};
  input.eligibleIndices=Uint32Array.from(Array.from({length:input.width*input.height},(_,index)=>index));
  equalContinuity(input);
  const flags=continuityFlags(input),result=findDetachedTemplePixelsFromFlags({...input,flags});
  const initial=Array.from({length:input.width*input.height},(_,index)=>Number(different(input.before,input.after,index*4))).reduce((a,b)=>a+b,0);
  const final=input.after.slice();
  for(const index of result.indices) for(let channel=0;channel<3;channel++) final[index*4+channel]=input.background[index*4+channel]!;
  const expected=Array.from({length:input.width*input.height},(_,index)=>Number(different(input.before,final,index*4))).reduce((a,b)=>a+b,0);
  assert.equal(changedPixelsAfterContinuity(flags,initial,result.indices),expected);
  assert.throws(()=>changedPixelsAfterContinuity(flags,initial,new Uint32Array([0])),/outside the eligible/);
});

test('unavailable ordering or invalid dimensions keep compact continuity unavailable without touching flags',()=>{
  const input=continuityFixture();hideBand(input);const flags=continuityFlags(input),saved=flags.bytes.slice();
  const unavailable=findDetachedTemplePixelsFromFlags({...input,paths:null,flags});
  assert.equal(unavailable.indices.length,0);assert.match(unavailable.diagnostics.unavailableReason!,/ordering is unavailable/);
  const invalid=findDetachedTemplePixelsFromFlags({...input,width:input.width+1,flags});
  assert.equal(invalid.indices.length,0);assert.ok(invalid.diagnostics.unavailableReason);assert.deepEqual(flags.bytes,saved);
});
