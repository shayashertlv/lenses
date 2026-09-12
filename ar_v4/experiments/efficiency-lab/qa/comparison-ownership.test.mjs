import assert from 'node:assert/strict';
import {test} from 'node:test';
import {registerHooks} from 'node:module';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {transformSync} from 'rolldown/utils';

const wrapper=new URL('../comparison-renderer.ts',import.meta.url);
const lower=new Set([new URL('../renderer.ts',import.meta.url).href,new URL('../../speed-lab/renderer.ts',import.meta.url).href]);
const hooks=registerHooks({load(url,context,next){
  if(lower.has(url))return {format:'module',shortCircuit:true,source:'export class LiveHairRenderer {}'};
  if(url===wrapper.href)return {format:'module',shortCircuit:true,source:transformSync(fileURLToPath(url),readFileSync(new URL(url),'utf8')).code};
  return next(url,context);
}});
const {ComparisonRenderer}=await import(wrapper.href);hooks.deregister();
class OwnedImageData {
  constructor(data,width,height,options={}){this.data=data;this.width=width;this.height=height;this.colorSpace=options.colorSpace??'srgb';}
}
function canvas(){return {width:2,height:1,getContext:()=>({
  getImageData:()=>new OwnedImageData(new Uint8ClampedArray([1,2,3,255,4,5,6,255]),2,1),putImageData(){}})};}

test('comparison present snapshots caller mask before asynchronous preparation',async t=>{
  const previous=Object.getOwnPropertyDescriptor(globalThis,'ImageData');
  Object.defineProperty(globalThis,'ImageData',{configurable:true,value:OwnedImageData});
  t.after(()=>{if(previous)Object.defineProperty(globalThis,'ImageData',previous);else delete globalThis.ImageData;});
  let release,received;
  const waiting=new Promise(resolve=>{release=resolve;});
  const boundary={setPreparationHairEnabled(){},selectVariant(){},async prepare(){await waiting;return true;},finish(mask){received=mask;return true;}};
  const output=canvas(),renderer=new ComparisonRenderer(output,boundary,boundary);
  const mask={category:new Uint8Array([1,2]),model:'hair-only'};
  const pending=renderer.present(output,{landmarks:[],matrix:null},mask,{sourceSHA256:'a'.repeat(64)},{id:'hair-only'});
  mask.category.fill(9);mask.model='changed';release();await pending;
  assert.deepEqual(received,{category:new Uint8Array([1,2]),model:'hair-only'});
  assert.notEqual(received,mask);assert.notEqual(received.category,mask.category);
});

test('held snapshot follows selected cached profile and variant and is independently owned',()=>{
  const boundary={},renderer=new ComparisonRenderer(canvas(),boundary,boundary);
  renderer.held=true;
  for(const [profile,pose] of [['g',1],['scratch',2]])renderer.outputs.set(profile,{
    snapshot:{pose,hairPreview:{variant:'hair',applied:true}},accepted:{width:2,height:1},hair:{width:2,height:1}});
  renderer.selectPipeline('scratch');renderer.selectVariant('accepted');
  const snapshot=renderer.captureSnapshot;
  assert.equal(snapshot.pose,2);assert.equal(snapshot.hairPreview.variant,'accepted');assert.equal(snapshot.hairPreview.applied,false);
  snapshot.pose=99;snapshot.hairPreview.applied=true;
  assert.equal(renderer.captureSnapshot.pose,2);assert.equal(renderer.captureSnapshot.hairPreview.applied,false);
  renderer.selectPipeline('g');renderer.selectVariant('hair');
  assert.equal(renderer.captureSnapshot.pose,1);assert.equal(renderer.captureSnapshot.hairPreview.applied,true);
});

test('held Q and T reuse the exact G output while R and S render independent candidates',async t=>{
  const previous=Object.getOwnPropertyDescriptor(globalThis,'ImageData');
  Object.defineProperty(globalThis,'ImageData',{configurable:true,value:OwnedImageData});
  t.after(()=>{if(previous)Object.defineProperty(globalThis,'ImageData',previous);else delete globalThis.ImageData;});
  const baseCalls=[],candidateCalls=[];
  const boundary=calls=>({setPreparationHairEnabled(){},selectVariant(){},
    async prepare(_frame,_detection,_pair,_model,input){calls.push(input.options);return true;},finish(){return true;},
    copyHeldInput:()=>({source:canvas(),detection:{landmarks:[],matrix:null},pair:{sourceSHA256:'a'.repeat(64)},expectedModel:{id:'hair-only'}}),
    exportDiagnostic:()=>({stats:{hasFace:true}}),captureSnapshot:{pose:1,hairPreview:{variant:'hair',applied:false}}});
  const renderer=new ComparisonRenderer(canvas(),boundary(baseCalls),boundary(candidateCalls));
  await renderer.setHeld();
  assert.equal(baseCalls.length,1,'G is rendered once and held-only aliases share its owned output.');
  assert.equal(renderer.outputs.get('publish'),renderer.outputs.get('g'));
  assert.equal(renderer.outputs.get('ui'),renderer.outputs.get('g'));
  assert.notEqual(renderer.outputs.get('region'),renderer.outputs.get('g'));
  assert.notEqual(renderer.outputs.get('lens'),renderer.outputs.get('g'));
  assert.equal(candidateCalls.filter(options=>options.cropBranchReadback).length,1);
  assert.equal(candidateCalls.filter(options=>options.omitBranchLenses).length,1);
  renderer.selectPipeline('publish');renderer.selectVariant('accepted');
  assert.equal(renderer.captureSnapshot.hairPreview.variant,'accepted');
  renderer.selectPipeline('ui');renderer.selectVariant('hair');
  assert.equal(renderer.captureSnapshot.hairPreview.variant,'hair');
});

function focusedFixture(t, prepare=async()=>true) {
  const previous=Object.getOwnPropertyDescriptor(globalThis,'ImageData');
  Object.defineProperty(globalThis,'ImageData',{configurable:true,value:OwnedImageData});
  t.after(()=>{if(previous)Object.defineProperty(globalThis,'ImageData',previous);else delete globalThis.ImageData;});
  const calls={base:0,candidate:0,inputs:[],disposed:0};
  const boundary=kind=>({setPreparationHairEnabled(){},selectVariant(){},
    async prepare(...args){calls[kind]++;return prepare(...args);},finish(){return true;},
    copyHeldInput:()=>{const source=canvas();calls.inputs.push(source);return {source,
      detection:{landmarks:[],matrix:null},pair:{sourceSHA256:'a'.repeat(64)},expectedModel:{id:'hair-only'}};},
    exportDiagnostic:()=>({stats:{hasFace:true},pair:{sourceSHA256:'a'.repeat(64)}}),
    captureSnapshot:{pose:1,hairPreview:{variant:'hair',applied:false}},dispose(){calls.disposed++;}});
  return {calls,renderer:new ComparisonRenderer(canvas(),boundary('base'),boundary('candidate'))};
}

test('focused G/V Hold owns its scope before awaits, orders G first and renders no old candidate',async t=>{
  let release;
  const waiting=new Promise(resolve=>{release=resolve;});
  const {renderer,calls}=focusedFixture(t,async()=>{await waiting;return true;});
  renderer.active=renderer.requested='mask-bytes';
  const choices=['mask-bytes','g'];
  const holding=renderer.setHeld(choices);
  choices.splice(0,choices.length,'g','word-compose','unknown');
  release();await holding;
  assert.deepEqual([...renderer.outputs.keys()],['g','mask-bytes']);
  assert.equal(calls.base,1);assert.equal(calls.candidate,0);
  assert.equal(renderer.outputs.get('mask-bytes'),renderer.outputs.get('g'));
  assert.equal(renderer.pipeline,'mask-bytes');assert.equal(renderer.requestedPipeline,'mask-bytes');
  const report=renderer.exportDiagnostic();
  assert.deepEqual(report.comparedPipelines,['g','mask-bytes']);
  assert.deepEqual(Object.keys(report).sort(),['baseCommit','candidateAccepted','comparedPipelines','g','inputPolicy','mask-bytes','schema','selectedPipeline'].sort());
  assert.equal(report.candidateAccepted,false);
  assert.match(report.inputPolicy,/shared SDK full mask comparison does not independently test V live mask extraction/);
  report.comparedPipelines.push('word-compose');report.g.pair.sourceSHA256='changed';
  assert.deepEqual(renderer.exportDiagnostic().comparedPipelines,['g','mask-bytes']);
  assert.equal(renderer.exportDiagnostic().g.pair.sourceSHA256,'a'.repeat(64));
});

test('invalid focused Hold scopes fail before copying input or replacing the selected output',async t=>{
  const {renderer,calls}=focusedFixture(t);
  renderer.active=renderer.requested='mask-bytes';
  for(const choices of [null,[],['g','g','mask-bytes'],['g','unknown','mask-bytes'],['mask-bytes'],['g']]) {
    await assert.rejects(renderer.setHeld(choices),/held comparison choices/i);
    assert.equal(renderer.pipeline,'mask-bytes');assert.equal(renderer.requestedPipeline,'mask-bytes');
    assert.equal(renderer.held,false);assert.equal(renderer.exportDiagnostic(),null);
  }
  assert.equal(calls.inputs.length,0);assert.equal(calls.base,0);assert.equal(calls.candidate,0);
});

test('failed focused Hold releases new input and retains the previous owned held comparison',async t=>{
  const failure=new Error('owned render failure');
  let fail=false;
  const {renderer,calls}=focusedFixture(t,async()=>{if(fail)throw failure;return true;});
  await renderer.setHeld(['g','mask-bytes']);
  const previous=renderer.exportDiagnostic(),previousInput=calls.inputs[0];
  fail=true;
  await assert.rejects(renderer.setHeld(['g','mask-bytes']),error=>error===failure);
  assert.deepEqual(renderer.exportDiagnostic(),previous);assert.equal(renderer.held,true);
  assert.equal(previousInput.width,2);assert.equal(previousInput.height,1);
  assert.equal(calls.inputs[1].width,0);assert.equal(calls.inputs[1].height,0);
  assert.equal(calls.candidate,0);
});

test('disposing during focused Hold releases its input and cannot publish a late alias',async t=>{
  let release;
  const waiting=new Promise(resolve=>{release=resolve;});
  const {renderer,calls}=focusedFixture(t,async()=>{await waiting;return true;});
  const holding=renderer.setHeld(['g','mask-bytes']);
  renderer.dispose();release();
  await assert.rejects(holding,error=>error.name==='AbortError');
  assert.equal(renderer.exportDiagnostic(),null);assert.equal(renderer.outputs.size,0);
  assert.equal(calls.inputs[0].width,0);assert.equal(calls.inputs[0].height,0);
  assert.equal(calls.base,1);assert.equal(calls.candidate,0);assert.equal(calls.disposed,2);
});
