/** Independent recipe-only slab and D3D11 raster oracle, adapted from the
 * frozen effective-group experiment. No renderer geometry, depth or pixels are
 * read. Float32 shader transforms followed by qualified direct viewport-to-n.8
 * nearest-even conversion define
 * the device-qualified raster reference; continuous slab diagnostics coexist.
 */
import * as T from 'three';
import {evaluateLensAppearance} from '../src/eyewear/lens-appearance.ts';
const BACKGROUND=[.125,.25,.375],OPAQUE=[.625,.125,.25];
const vector=a=>new T.Vector3(...a);
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const maxError=(a,b)=>Math.max(...a.map((v,i)=>Math.abs(v-b[i])));
const poseMatrix=pose=>pose.matrix.clone();
export function groupBounds(g){return [Math.min(...g.boxes.map(b=>Math.fround(Math.fround(-b.size[1]/2)+b.center[1]))),Math.max(...g.boxes.map(b=>Math.fround(Math.fround(b.size[1]/2)+b.center[1])))];}
function floatMatVec(matrix,vector){const v=vector.toArray(),e=matrix.elements,out=[];
 for(let row=0;row<4;row++){let s=0;for(let col=0;col<4;col++)s=Math.fround(s+Math.fround(e[col*4+row]*v[col]));out.push(s);}return new T.Vector4(...out);}
function floatViewport(clip,w,h){
 // Raster fixed-point conversion must not invent a float32 storage boundary
 // after (NDC+1). It can round a value just below a half tie onto that tie,
 // shifting one whole n.8 unit. The independent 150-triangle viewport probe
 // qualifies this fixed rule before the conformance run on this backend.
 const depth=Math.fround(Math.fround(Math.fround(clip.z/clip.w)+1)*.5);
 return new T.Vector3((clip.x/clip.w+1)*w/2,(clip.y/clip.w+1)*h/2,depth);
}
function boxHit(origin,direction,b){let enter=-Infinity,exit=Infinity,enterAxis=-1,exitAxis=-1,enterSign=0,exitSign=0;
 const bounds=[0,1,2].map(a=>[Math.fround(Math.fround(-b.size[a]/2)+b.center[a]),Math.fround(Math.fround(b.size[a]/2)+b.center[a])]);
 for(let a=0;a<3;a++){const [low,high]=bounds[a];
  if(Math.abs(direction[a])<1e-14){if(origin[a]<low||origin[a]>high)return null;continue;}
  let t0=(low-origin[a])/direction[a],t1=(high-origin[a])/direction[a],s0=-1,s1=1;
  if(t0>t1){[t0,t1]=[t1,t0];[s0,s1]=[s1,s0];}
  if(t0>enter){enter=t0;enterAxis=a;enterSign=s0;}if(t1<exit){exit=t1;exitAxis=a;exitSign=s1;}if(enter>exit)return null;
 }
 if(exit<=0)return null;const t=enter>0?enter:exit,axis=enter>0?enterAxis:exitAxis,normal=[0,0,0];normal[axis]=enter>0?enterSign:exitSign;
 const point=origin.map((v,i)=>v+t*direction[i]);const edgeMargin=Math.min(...[0,1,2].filter(a=>a!==axis).map(a=>Math.min(point[a]-bounds[a][0],bounds[a][1]-point[a])));
 return {t,point,exitPoint:origin.map((v,i)=>v+exit*direction[i]),normal:b.normalOverride??normal,edgeMargin};
}
function ray(camera,x,y,w,h,pose){const nx=2*(x+.5)/w-1,ny=2*(y+.5)/h-1;
 const near=new T.Vector3(nx,ny,-1).unproject(camera),far=new T.Vector3(nx,ny,1).unproject(camera);
 const origin=camera.isPerspectiveCamera?camera.getWorldPosition(new T.Vector3()):near;
 const direction=far.sub(origin).normalize(),inverse=poseMatrix(pose).invert();
 return {origin:origin.applyMatrix4(inverse).toArray(),direction:direction.transformDirection(inverse).toArray()};}
function analytic(fixture,pose,camera,x,y,w,h){const r=ray(camera,x,y,w,h,pose),matrix=poseMatrix(pose),normalMatrix=new T.Matrix3().getNormalMatrix(camera.matrixWorldInverse.clone().multiply(matrix));
 const materialFront=new T.Vector3(0,0,1).transformDirection(camera.matrixWorldInverse.clone().multiply(matrix));
 const worldNormal=normal=>vector(normal).applyMatrix3(normalMatrix).normalize();
 const depth=point=>(vector(point).applyMatrix4(matrix).project(camera).z+1)/2;
 let margin=Infinity,opaque=null;for(const b of fixture.opaque??[]){const hit=boxHit(r.origin,r.direction,b);if(hit&&(!opaque||hit.t<opaque.t))opaque=hit;}
 if(opaque)margin=Math.min(margin,opaque.edgeMargin);
 const hits=[],ambiguities=[];
 for(const g of fixture.groups){const candidates=g.boxes.map(b=>boxHit(r.origin,r.direction,b)).filter(Boolean).sort((a,b)=>a.t-b.t);if(!candidates.length)continue;
  const hit=candidates[0];margin=Math.min(margin,hit.edgeMargin);for(const other of candidates.slice(1))if(Math.abs(other.t-hit.t)<1e-9&&maxError(other.normal,hit.normal)>1e-7)ambiguities.push('same_group_conflicting_attributes');
  const bounds=groupBounds(g),rawV=(hit.point[1]-bounds[0])/(bounds[1]-bounds[0]);
  assert(rawV>=-2e-12&&rawV<=1+2e-12,'Analytic intersection escaped group height');const v=Math.max(0,Math.min(1,rawV));
  const N=worldNormal(hit.normal),pointView=vector(hit.point).applyMatrix4(matrix).applyMatrix4(camera.matrixWorldInverse),V=camera.isPerspectiveCamera?pointView.negate().normalize():new T.Vector3(0,0,1);
  if(N.dot(V)<0)N.negate();const cosine=Math.max(0,Math.min(1,N.dot(V))),angle=Math.acos(cosine)*180/Math.PI;
  const response=evaluateLensAppearance(g.appearance,v,angle,materialFront.dot(V)<0?'rear':'front'),reflected=V.clone().negate().addScaledVector(N,2*cosine);
  hits.push({...hit,group:g.id,depth:depth(hit.point),v,angle,R:response.reflectance_rgb,T:response.transmission_rgb,E:[.55,.42,.28].map((a,i)=>a+.16*reflected.getComponent(i))});
 }
 hits.sort((a,b)=>a.t-b.t);for(let i=1;i<hits.length;i++)if(Math.abs(hits[i].t-hits[i-1].t)<1e-8)ambiguities.push('coincident_distinct_groups');
 const relevant=hits.filter(hit=>!opaque||hit.t<opaque.t);let color=opaque?[...OPAQUE]:[...BACKGROUND];
 for(const hit of [...relevant].reverse())color=color.map((v,i)=>hit.T[i]*v+hit.R[i]*hit.E[i]);
 return {hits,relevant,opaqueDepth:opaque?depth(opaque.point):1,color,margin,ambiguities};
}

// Independent D3D11 raster reference built from analytic box face corners.
// The actual renderer mesh, framebuffer and depth textures are never consulted.
// D3D11 requires n.8 nearest-even XY snapping and interpolation from snapped
// positions (§3.4.1 and15.16). Continuous slab-ray diagnostics remain reported.
const nearestEven=value=>{const base=Math.floor(value),part=value-base;return part<.5?base:part>.5?base+1:base%2===0?base:base+1;};
function rasterFixture(fixture,pose,camera,w,h){const mv=camera.matrixWorldInverse.clone().multiply(poseMatrix(pose)),normal=new T.Matrix3().getNormalMatrix(mv),projection=camera.projectionMatrix.clone();
 for(const m of [mv,normal,projection])m.elements=m.elements.map(Math.fround);
 function boxTriangles(b,bounds){const triangles=[],faces=[[2,1,0,-1,-1,1],[2,1,0,1,-1,-1],[0,2,1,1,1,1],[0,2,1,1,-1,-1],[0,1,2,1,-1,1],[0,1,2,-1,-1,-1]];
  for(const [u,v,a,us,vs,as]of faces){const vertices=[];
   for(let y=0;y<2;y++)for(let x=0;x<2;x++){const local=[0,0,0];local[u]=(x-.5)*b.size[u]*us;local[v]=(y-.5)*b.size[v]*vs;local[a]=b.size[a]/2*as;
    for(let k=0;k<3;k++)local[k]=Math.fround(Math.fround(local[k])+b.center[k]);
    const n=[0,0,0];n[a]=as;const N=vector((b.normalOverride??n).map(Math.fround)).applyMatrix3(normal).normalize();
    const view=floatMatVec(mv,new T.Vector4(...local,1)),clip=floatMatVec(projection,view),point=floatViewport(clip,w,h);
    vertices.push({point:new T.Vector3(nearestEven(point.x*256)/256,nearestEven(point.y*256)/256,point.z),inverseW:1/clip.w,
     v:Math.fround((local[1]-bounds[0])/(bounds[1]-bounds[0])),normal:N,view:new T.Vector3(-view.x,-view.y,-view.z)});
   }
   for(const indices of [[0,2,1],[2,3,1]])triangles.push(indices.map(i=>vertices[i]));
  }return triangles;
 }
 return {materialFront:new T.Vector3(0,0,1).transformDirection(mv),groups:fixture.groups.map(g=>({...g,triangles:g.boxes.flatMap(b=>boxTriangles(b,groupBounds(g)))})),opaque:(fixture.opaque??[]).flatMap(b=>boxTriangles(b,[-1,1])),perspective:!!camera.isPerspectiveCamera};
}
function rasterHit(triangles,x,y){let nearest=null;for(const t of triangles){const [a,b,c]=t.map(v=>v.point),den=(b.y-c.y)*(a.x-c.x)+(c.x-b.x)*(a.y-c.y);if(Math.abs(den)<1e-12)continue;
 const p=((b.y-c.y)*(x+.5-c.x)+(c.x-b.x)*(y+.5-c.y))/den,q=((c.y-a.y)*(x+.5-c.x)+(a.x-c.x)*(y+.5-c.y))/den,weights=[p,q,1-p-q];if(weights.some(v=>v<0))continue;
 const depth=weights.reduce((sum,v,i)=>sum+v*t[i].point.z,0);if(depth<0||depth>=1||nearest&&depth>=nearest.depth)continue;
 const iw=weights.map((v,i)=>v*t[i].inverseW),total=iw.reduce((a,b)=>a+b,0),perspective=iw.map(v=>v/total),normal=new T.Vector3(),view=new T.Vector3();let v=0;
 for(let i=0;i<3;i++){normal.addScaledVector(t[i].normal,perspective[i]);view.addScaledVector(t[i].view,perspective[i]);v+=perspective[i]*t[i].v;}
 nearest={depth,v:Math.max(0,Math.min(1,v)),normal:normal.normalize(),view:view.normalize()};
 }return nearest;}
function rasterExpected(raster,x,y){const opaque=rasterHit(raster.opaque,x,y),hits=[];for(const g of raster.groups){const hit=rasterHit(g.triangles,x,y);if(!hit)continue;
 const V=raster.perspective?hit.view:new T.Vector3(0,0,1),N=hit.normal;if(N.dot(V)<0)N.negate();const cosine=Math.max(0,Math.min(1,N.dot(V))),angle=Math.acos(cosine)*180/Math.PI;
 const response=evaluateLensAppearance(g.appearance,hit.v,angle,raster.materialFront.dot(V)<0?'rear':'front'),reflection=V.clone().negate().addScaledVector(N,2*cosine);
 hits.push({group:g.id,depth:hit.depth,v:hit.v,angle,normal:N.toArray(),view:V.toArray(),T:response.transmission_rgb,R:response.reflectance_rgb,E:[.55,.42,.28].map((v,i)=>v+.16*reflection.getComponent(i))});}
 hits.sort((a,b)=>a.depth-b.depth);const relevant=hits.filter(hit=>!opaque||hit.depth<opaque.depth);let color=opaque?[...OPAQUE]:[...BACKGROUND];for(const hit of [...relevant].reverse())color=color.map((v,i)=>hit.T[i]*v+hit.R[i]*hit.E[i]);
 return {hits,relevant,color,opaqueDepth:opaque?.depth??1};
}

function compose(result,environment){let color=result.opaqueDepth<1?[...OPAQUE]:[...BACKGROUND];
 for(const hit of [...result.relevant].reverse())color=color.map((v,c)=>v*hit.T[c]+hit.R[c]*environment[c]);return {...result,color};}
export function makeOracle(fixture,matrix,camera,width,height){const pose={matrix},raster=rasterFixture(fixture,pose,camera,width,height);
 return {at(x,y,environment){return compose(rasterExpected(raster,x,y),environment);},
 continuous(x,y,environment){return compose(analytic(fixture,pose,camera,x,y,width,height),environment);}};}
