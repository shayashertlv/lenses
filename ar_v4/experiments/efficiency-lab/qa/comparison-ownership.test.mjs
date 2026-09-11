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
