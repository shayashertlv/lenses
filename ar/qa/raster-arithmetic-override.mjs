/** QA-only independent CPU raster arithmetic; never consumes GPU captures. */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
const sha=source=>crypto.createHash('sha256').update(source).digest('hex');
const MARKER='function rasterFixture(fixture,pose,camera,w,h){';
const EXPRESSION='const view=new T.Vector4(...local,1).applyMatrix4(mv),clip=view.clone().applyMatrix4(projection),point=new T.Vector3((clip.x/clip.w+1)*w/2,(clip.y/clip.w+1)*h/2,(clip.z/clip.w+1)/2);';
const REPLACEMENT='const view=qaFloat32MatVec(mv,new T.Vector4(...local,1)),clip=qaFloat32MatVec(projection,view),point=qaFloat32Viewport(clip,w,h);qaRecordRasterArithmetic(mv,projection,local,w,h,point);';
export const FLOAT32_RASTER_HELPER=`function qaFloat32MatVec(matrix,vector){
 const values=vector.toArray(),elements=matrix.elements,result=[];
 for(let row=0;row<4;row++){let sum=0;for(let column=0;column<4;column++)sum=Math.fround(sum+Math.fround(elements[column*4+row]*values[column]));result.push(sum);}
 return new T.Vector4(...result);
}
function qaFloat32Viewport(clip,width,height){
 const component=(value,scale)=>Math.fround(Math.fround(Math.fround(value/clip.w)+1)*scale);
 return new T.Vector3(component(clip.x,width/2),component(clip.y,height/2),component(clip.z,.5));
}
function qaFusedLikeMatVec(matrix,vector){
 const values=vector.toArray(),elements=matrix.elements,result=[];
 for(let row=0;row<4;row++){let sum=0;for(let column=0;column<4;column++)sum=Math.fround(sum+elements[column*4+row]*values[column]);result.push(sum);}
 return new T.Vector4(...result);
}
function qaRecordRasterArithmetic(mv,projection,local,width,height,point){
 const stats=globalThis.__qaRasterArithmetic??= {vertexVisits:0,baselineDifferentVertices:0,baselineDifferentXYCoordinates:0,fusedLikeDifferentVertices:0,fusedLikeDifferentXYCoordinates:0,disagreements:[]};
 const index=stats.vertexVisits++,authored=new T.Vector4(...local,1),baselineClip=authored.clone().applyMatrix4(mv).applyMatrix4(projection);
 const baseline=new T.Vector3((baselineClip.x/baselineClip.w+1)*width/2,(baselineClip.y/baselineClip.w+1)*height/2,(baselineClip.z/baselineClip.w+1)/2);
 const fusedLike=qaFloat32Viewport(qaFusedLikeMatVec(projection,qaFusedLikeMatVec(mv,authored)),width,height);
 const snap=p=>[nearestEven(p.x*256)/256,nearestEven(p.y*256)/256],currentSnap=snap(point),baselineSnap=snap(baseline),fusedSnap=snap(fusedLike);
 const baselineDifferences=currentSnap.filter((v,i)=>v!==baselineSnap[i]).length,fusedDifferences=currentSnap.filter((v,i)=>v!==fusedSnap[i]).length;
 stats.baselineDifferentVertices+=Number(baselineDifferences>0);stats.baselineDifferentXYCoordinates+=baselineDifferences;
 stats.fusedLikeDifferentVertices+=Number(fusedDifferences>0);stats.fusedLikeDifferentXYCoordinates+=fusedDifferences;
 if(baselineDifferences||fusedDifferences)stats.disagreements.push({vertexVisit:index,local,width,height,current:point.toArray(),baseline:baseline.toArray(),fusedLike:fusedLike.toArray(),currentSnap,baselineSnap,fusedSnap});
}
`;
export function overrideRasterArithmetic(source,mode){
 assert(['baseline','float32'].includes(mode),'raster-arithmetic must be baseline or float32');
 if(mode==='baseline')return {code:source,receipt:{mode,applied:false,sourceSha256:sha(source),transformedSourceSha256:sha(source)}};
 assert.equal(source.split(MARKER).length-1,1,'Expected exactly one unchanged raster fixture marker');
 assert.equal(source.split(EXPRESSION).length-1,1,'Expected exactly one unchanged raster vertex expression');
 const start=source.indexOf(MARKER),end=source.indexOf('function rasterHit(',start),expression=source.indexOf(EXPRESSION);
 assert(start>=0&&expression>start&&expression<end,'Replacement is outside the independent raster fixture');
 assert(!source.includes('qaFloat32MatVec')&&!source.includes('qaFloat32Viewport'),'Refuse duplicate/preexisting raster replacement');
 const code=source.replace(MARKER,FLOAT32_RASTER_HELPER+MARKER).replace(EXPRESSION,REPLACEMENT);
 return {code,receipt:{mode,applied:true,sourceSha256:sha(source),transformedSourceSha256:sha(code),expectedExpression:EXPRESSION,
  replacementExpression:REPLACEMENT,expectedExpressionCount:1,injectionMarker:MARKER,injectionCount:1,helper:FLOAT32_RASTER_HELPER,helperSha256:sha(FLOAT32_RASTER_HELPER),
  arithmetic:{matrixVector:'Float32 round after each scalar multiply and running add, in column order 0..3.',viewport:'Float32 round after clip division, +1, and viewport scale.',raster:'Existing D3D11 n.8 nearest-even XY snapping and barycentric interpolation unchanged.'},
  scope:'Test-only CPU reference override. Independent analytic box corners and declared uploaded matrices only; no GPU geometry, capture, depth or residual enters the oracle. Original continuous slab reference, renderer, shaders, coverage and thresholds unchanged by this override.',
  limitations:['Backend-specific numerical conformance experiment, not a proof of all GPU operation scheduling.','The separate multiply/add model does not exactly reproduce every captured shader component; the diagnostic records the differences.','No general bound on all float32 compiler/FMA/viewport implementations is established.']}};
}
