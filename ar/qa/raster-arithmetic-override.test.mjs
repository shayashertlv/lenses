import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import test from 'node:test';
import * as T from 'three';
import {FLOAT32_RASTER_HELPER,overrideRasterArithmetic} from './raster-arithmetic-override.mjs';
const source=await fs.readFile(new URL('./effective-optical-groups-browser.mjs',import.meta.url),'utf8');
test('baseline keeps exact source and rejects unknown mode',()=>{
 assert.equal(overrideRasterArithmetic(source,'baseline').code,source);
 assert.throws(()=>overrideRasterArithmetic(source,'adaptive'));
});
test('override is confined to the independent CPU raster expression',()=>{
 const {code,receipt}=overrideRasterArithmetic(source,'float32');
 assert.equal(code.replace(FLOAT32_RASTER_HELPER,'').replace(receipt.replacementExpression,receipt.expectedExpression),source);
 assert.equal(code.slice(0,source.indexOf('function rasterFixture(')),source.slice(0,source.indexOf('function rasterFixture(')));
 assert.equal(code.slice(code.indexOf('function rasterHit(')),source.slice(source.indexOf('function rasterHit(')));
});
test('changed, duplicate or already transformed source is rejected',()=>{
 assert.throws(()=>overrideRasterArithmetic(source.replace('function rasterFixture(','function changed('),'float32'));
 assert.throws(()=>overrideRasterArithmetic(source+source,'float32'));
 assert.throws(()=>overrideRasterArithmetic(overrideRasterArithmetic(source,'float32').code,'float32'));
});
test('separate operations round every multiply/add and viewport stage',()=>{
 const {matvec,viewport}=Function('T',`${FLOAT32_RASTER_HELPER};return {matvec:qaFloat32MatVec,viewport:qaFloat32Viewport};`)(T);
 const m=new T.Matrix4().set(1e8,1,-1e8,0,0,1,0,0,0,0,1,0,0,0,0,1);
 assert.equal(matvec(m,new T.Vector4(1,1,1,1)).x,0);
 const clip=new T.Vector4(.333,.3744751513004303,-.52,1),actual=viewport(clip,480,320);
 assert.equal(actual.y,Math.fround(Math.fround(Math.fround(clip.y/clip.w)+1)*160));
 assert.equal(actual.z,Math.fround(Math.fround(Math.fround(clip.z/clip.w)+1)*.5));
});
