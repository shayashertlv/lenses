/** Real source triangles in the unchanged QA-only nearest-group transport.
 * The local runner appends exports through a pinned Vite bridge; the original
 * synthetic harness and production sources are never modified.
 */
import * as T from 'three';
import {Transport,makeCamera,descriptor,BACKGROUND,OPAQUE,COLOR_TOLERANCE,DEPTH_TOLERANCE} from './effective-optical-groups-browser.mjs?sourcePrototype';
import {evaluateLensAppearance} from '../src/eyewear/lens-appearance.ts';

const WIDTH=480,HEIGHT=320;
const POSES=[{id:'front',yaw:0,roll:0},{id:'yaw_roll',yaw:37,roll:21},{id:'back',yaw:180,roll:0}];
const CONTROLS=[{id:'neutral_tint',appearance:descriptor([.04,.04,.04],[.35,.35,.35],[.35,.35,.35])},
 {id:'gradient_mirror',appearance:descriptor([.8,.6,.4],[.1,.2,.3],[.8,.4,.1])}];
const assert=(value,message)=>{if(!value)throw Error(message);};
const maximumError=(a,b)=>Math.max(...a.map((v,i)=>Math.abs(v-b[i])));
const hash=async buffer=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',buffer)),x=>x.toString(16).padStart(2,'0')).join('');
const nearestEven=value=>{const base=Math.floor(value),part=value-base;return part<.5?base:part>.5?base+1:base%2===0?base:base+1;};
const magnitude=a=>Math.hypot(...a);
const normalize=a=>{const n=magnitude(a);return n>0?a.map(v=>v/n):null;};
const dot=(a,b)=>a.reduce((sum,v,i)=>sum+v*b[i],0);

async function loadArray(directory,record){const response=await fetch(`/source-bundle/${directory}/${record.path}`);assert(response.ok,'Missing source array');
 const buffer=await response.arrayBuffer();assert(buffer.byteLength===record.byte_length,'Source array length mismatch');assert(await hash(buffer)===record.sha256,'Source array digest mismatch');
 return record.dtype==='<f8'?new Float64Array(buffer):record.dtype==='<u4'?new Uint32Array(buffer):(()=>{throw Error('Unexpected source array dtype');})();}
async function loadCase(record){const groups=[];for(const g of record.groups){const arrays={};for(const [key,item]of Object.entries(g.arrays))arrays[key]=await loadArray(record.directory,item);groups.push({...g,...arrays});}
 return {...record,groups,opaque:{...record.opaque,positions:await loadArray(record.directory,record.opaque.positions),indices:await loadArray(record.directory,record.opaque.indices)}};}
function poseMatrix(record,pose){const center=record.normalization.center,scale=record.normalization.display_extent/record.normalization.maximum_extent;
 return new T.Matrix4().makeRotationFromEuler(new T.Euler(0,pose.yaw*Math.PI/180,pose.roll*Math.PI/180,'YXZ'))
  .multiply(new T.Matrix4().makeScale(scale,scale,scale)).multiply(new T.Matrix4().makeTranslation(...center.map(v=>-v)));}
function sourceGeometry(source,optical){const geometry=new T.BufferGeometry();geometry.setAttribute('position',new T.BufferAttribute(Float32Array.from(source.positions),3));
 geometry.setIndex(new T.BufferAttribute(Uint32Array.from(source.indices),1));
 if(optical){geometry.setAttribute('normal',new T.BufferAttribute(Float32Array.from(source.normals),3));geometry.setAttribute('uv',new T.BufferAttribute(Float32Array.from(source.uv),2));}
 return geometry;}
function transportFor(renderer,record,pose,camera,control){const fixture={groups:record.groups.map(g=>({id:g.id,appearance:control.appearance,boxes:[]})),opaque:[]};
 const matrix=poseMatrix(record,pose),transport=new Transport(renderer,fixture,{matrix},camera,WIDTH,HEIGHT);
 for(let i=0;i<record.groups.length;i++){const geometry=sourceGeometry(record.groups[i],true);transport.geometry.push(geometry);
  for(const scene of [transport.scenes[i],transport.all]){const mesh=new T.Mesh(geometry,transport.materials[i]);mesh.matrixAutoUpdate=false;mesh.matrix.copy(matrix);mesh.frustumCulled=false;scene.add(mesh);}}
 const geometry=sourceGeometry(record.opaque,false),material=new T.MeshBasicMaterial({color:new T.Color(...OPAQUE),toneMapped:false,side:T.DoubleSide});
 transport.geometry.push(geometry);transport.opaqueMaterials.push(material);const mesh=new T.Mesh(geometry,material);mesh.matrixAutoUpdate=false;mesh.matrix.copy(matrix);mesh.frustumCulled=false;transport.opaqueScene.add(mesh);
 return transport;}

// Independent CPU projection/interpolation reads pinned arrays, never GPU
// buffers/depth. D3D11 n.8 snapping is fixed by the backend specification.
// Float32 inputs/matrices are modeled, but JS arithmetic is double precision;
// GPU arithmetic residuals and geometry ties remain measured, not concealed.
class SourceRaster{
 constructor(source,matrix,camera,optical){this.source=source;this.optical=optical;this.bins=new Map();this.binSize=16;
  const mv=camera.matrixWorldInverse.clone().multiply(matrix),normal=new T.Matrix3().getNormalMatrix(mv),projection=camera.projectionMatrix.clone();
  for(const m of [mv,normal,projection])m.elements=m.elements.map(Math.fround);
  const count=source.positions.length/3;this.screen=new Float64Array(count*3);this.normals=optical?new Float64Array(count*3):null;this.inverseW=new Float64Array(count);this.v=optical?new Float32Array(count):null;
  for(let i=0;i<count;i++){const local=Array.from(source.positions.slice(i*3,i*3+3),Math.fround),view=new T.Vector4(...local,1).applyMatrix4(mv),clip=view.clone().applyMatrix4(projection);
   this.screen.set([nearestEven((clip.x/clip.w+1)*WIDTH/2*256)/256,nearestEven((clip.y/clip.w+1)*HEIGHT/2*256)/256,(clip.z/clip.w+1)/2],i*3);this.inverseW[i]=1/clip.w;
   if(optical){const n=new T.Vector3(...Array.from(source.normals.slice(i*3,i*3+3),Math.fround)).applyMatrix3(normal).normalize();this.normals.set(n.toArray(),i*3);this.v[i]=source.uv[i*2+1];}}
  const index=source.indices;for(let f=0;f<index.length;f+=3){const ids=[index[f],index[f+1],index[f+2]],xs=ids.map(i=>this.screen[i*3]),ys=ids.map(i=>this.screen[i*3+1]);
   const loX=Math.max(0,Math.floor(Math.min(...xs)/this.binSize)),hiX=Math.min(Math.floor((WIDTH-1)/this.binSize),Math.floor(Math.max(...xs)/this.binSize));
   const loY=Math.max(0,Math.floor(Math.min(...ys)/this.binSize)),hiY=Math.min(Math.floor((HEIGHT-1)/this.binSize),Math.floor(Math.max(...ys)/this.binSize));
   for(let y=loY;y<=hiY;y++)for(let x=loX;x<=hiX;x++){const key=y*100+x;if(!this.bins.has(key))this.bins.set(key,[]);this.bins.get(key).push(f);}}
 }
 hit(x,y){const candidates=this.bins.get(Math.floor(y/this.binSize)*100+Math.floor(x/this.binSize))??[],hits=[];
  for(const f of candidates){const ids=Array.from(this.source.indices.slice(f,f+3)),p=ids.map(i=>Array.from(this.screen.slice(i*3,i*3+3))),[a,b,c]=p;
   const den=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1]);if(Math.abs(den)<1e-18)continue;
   const first=((b[1]-c[1])*(x+.5-c[0])+(c[0]-b[0])*(y+.5-c[1]))/den,second=((c[1]-a[1])*(x+.5-c[0])+(a[0]-c[0])*(y+.5-c[1]))/den;
   const weights=[first,second,1-first-second];if(weights.some(v=>v<0))continue;const depth=weights.reduce((sum,v,i)=>sum+v*p[i][2],0);if(depth<0||depth>=1)continue;
   const iw=weights.map((v,i)=>v*this.inverseW[ids[i]]),total=iw.reduce((a,b)=>a+b,0),perspective=iw.map(v=>v/total),hit={depth,face:f/3,weights};
   if(this.optical){let v=0;const n=[0,0,0];for(let k=0;k<3;k++){v+=perspective[k]*this.v[ids[k]];for(let axis=0;axis<3;axis++)n[axis]+=perspective[k]*this.normals[ids[k]*3+axis];}
    hit.v=Math.max(0,Math.min(1,v));hit.normal=normalize(n);}
   hits.push(hit);}
  if(!hits.length)return null;hits.sort((a,b)=>a.depth-b.depth||a.face-b.face);const result=hits[0];
  result.float32DepthTies=hits.filter(h=>h!==result&&Math.fround(h.depth)===Math.fround(result.depth));
  result.conflictingTies=this.optical?result.float32DepthTies.filter(h=>!h.normal||!result.normal||Math.abs(h.v-result.v)>1e-6||maximumError(h.normal,result.normal)>1e-6).length:0;
  return result;
 }
}
function sourceReference(record,pose,camera){const matrix=poseMatrix(record,pose);return {groups:record.groups.map(g=>new SourceRaster(g,matrix,camera,true)),opaque:new SourceRaster(record.opaque,matrix,camera,false)};}
function expectedAt(reference,control,x,y){const opaque=reference.opaque.hit(x,y),hits=reference.groups.map((r,i)=>{const hit=r.hit(x,y);return hit?{...hit,groupIndex:i}:null;});
 let color=opaque?[...OPAQUE]:[...BACKGROUND];const relevant=hits.filter(hit=>hit&&(!opaque||hit.depth<opaque.depth)).sort((a,b)=>b.depth-a.depth||a.groupIndex-b.groupIndex);
 let invalid=false;for(const hit of relevant){if(!hit.normal){invalid=true;continue;}const N=hit.normal[2]<0?hit.normal.map(v=>-v):hit.normal,cosine=Math.max(0,Math.min(1,N[2])),angle=Math.acos(cosine)*180/Math.PI;
  const response=evaluateLensAppearance(control.appearance,hit.v,angle),reflection=[2*cosine*N[0],2*cosine*N[1],-1+2*cosine*N[2]],environment=[.55,.42,.28].map((v,i)=>v+.16*reflection[i]);
  color=color.map((v,i)=>v*response.transmission_rgb[i]+environment[i]*response.reflectance_rgb[i]);hit.angle=angle;}
 return {opaque,hits,relevant,color,invalid};}
function summary(values){return {count:values.length,maximum:values.length?Math.max(...values):null,mean:values.length?values.reduce((a,b)=>a+b,0)/values.length:null};}
function show(record,pose,control,data){const figure=document.createElement('figure'),canvas=document.createElement('canvas');canvas.width=WIDTH;canvas.height=HEIGHT;
 const image=new ImageData(WIDTH,HEIGHT),encode=x=>255*(x<=.0031308?12.92*x:1.055*Math.pow(x,1/2.4)-.055);
 for(let y=0;y<HEIGHT;y++)for(let x=0;x<WIDTH;x++){const src=(y*WIDTH+x)*4,dst=((HEIGHT-1-y)*WIDTH+x)*4;for(let c=0;c<3;c++)image.data[dst+c]=Math.max(0,Math.min(255,Math.round(encode(data[src+c]))));image.data[dst+3]=255;}
 canvas.getContext('2d').putImageData(image,0,0);const label=document.createElement('figcaption');label.textContent=`${record.id} · ${pose.id} · ${control.id} (control material; unverified groups)`;figure.append(label,canvas);document.querySelector('#images').append(figure);
 return canvas.toDataURL('image/png');}

export async function run(){const manifestResponse=await fetch('/source-bundle/manifest.json'),manifest=await manifestResponse.json();
 const report=window.sourceGroupReport={schemaVersion:1,method:'real_source_effective_optical_group_browser_v1',accepted:false,qualityVerdict:'unmeasured',status:'running',
  dimensions:[WIDTH,HEIGHT],camera:'orthographic',threeRevision:T.REVISION,poses:POSES,controls:CONTROLS,tolerances:{linearRGB:COLOR_TOLERANCE,normalizedDepth:DEPTH_TOLERANCE},cases:[],
  bundle:manifest,lightReceiverCoverage:'unmeasured_in_this_real_source_run',
  assumptions:['Nearest hit per explicitly supplied group, not recovered physical identity.',
   'All nonselected source triangles are constant opaque controls; original material/color fidelity is unmeasured.',
   'Original source positions, winding, normals and indices are retained; UV height is the saved group hypothesis.',
   'Fixed sample lattice: x=7+17n, y=9+13n; no sample is removed based on residual.',
   'Float32 depth ties and conflicting interpolated attributes remain reported; importer ambiguity rejection is not implemented.',
   'Projected-triangle reference models D3D11 n.8 vertex snapping and float32 inputs/matrices; JS arithmetic remains double precision.',
   'This experiment does not modify or validate production front_sheet_v1 compatibility.']};
 const renderer=new T.WebGLRenderer({antialias:false});renderer.setSize(WIDTH,HEIGHT);renderer.outputColorSpace=T.LinearSRGBColorSpace;renderer.toneMapping=T.NoToneMapping;
 const gl=renderer.getContext(),debug=gl.getExtension('WEBGL_debug_renderer_info');report.backend={renderer:debug?gl.getParameter(debug.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER),subpixelBits:gl.getParameter(gl.SUBPIXEL_BITS)};
 assert(/Direct3D11|D3D11/i.test(report.backend.renderer),'Source raster reference requires D3D11');assert(renderer.extensions.has('EXT_color_buffer_float'),'Float color targets unavailable');
 report.rasterSpecification='https://microsoft.github.io/DirectX-Specs/d3d/archive/D3D11_3_FunctionalSpec.htm#CoordinateSnapping';
 try{for(const source of manifest.cases){const record=await loadCase(source);for(const pose of POSES){const camera=makeCamera('orthographic',WIDTH,HEIGHT),reference=sourceReference(record,pose,camera);
   for(const control of CONTROLS){const transport=transportFor(renderer,record,pose,camera,control),started=performance.now();let data;
    try{transport.render();gl.finish();const synchronizedRenderMs=performance.now()-started;data=transport.read();const maps=transport.maps.map(map=>transport.read(map));
     const colorErrors=[],depthErrors=[],groupCoverage=record.groups.map(g=>({id:g.id,referencePixels:0,gpuPixels:0,stoppedByOpaque:0,float32DepthTieSamples:0,conflictingTieSamples:0}));
     let opticalSamples=0,opaqueSamples=0,invalidOracleSamples=0,supportDisagreements=0,worst=null,opaqueStopSamples=0;
     for(let y=9;y<HEIGHT;y+=13)for(let x=7;x<WIDTH;x+=17){const expected=expectedAt(reference,control,x,y),index=(y*WIDTH+x)*4,actual=Array.from(data.slice(index,index+3));
      if(expected.opaque)opaqueSamples++;if(expected.relevant.length)opticalSamples++;if(expected.invalid)invalidOracleSamples++;
      const error=maximumError(actual,expected.color);colorErrors.push(error);let sampleDepthError=0;
      for(let i=0;i<maps.length;i++){const hit=expected.hits[i],gpuDepth=maps[i][index+3],cpuDepth=hit?.depth??1;const depthError=Math.abs(gpuDepth-cpuDepth);depthErrors.push(depthError);sampleDepthError=Math.max(sampleDepthError,depthError);
       if(hit)groupCoverage[i].referencePixels++;if(gpuDepth<1)groupCoverage[i].gpuPixels++;if(!!hit!==(gpuDepth<1))supportDisagreements++;
       if(hit?.float32DepthTies.length)groupCoverage[i].float32DepthTieSamples++;if(hit?.conflictingTies)groupCoverage[i].conflictingTieSamples++;
       if(hit&&expected.opaque&&hit.depth>=expected.opaque.depth){groupCoverage[i].stoppedByOpaque++;opaqueStopSamples++;}}
      if(!worst||error>worst.error)worst={x,y,error,actual,expected:expected.color,opaqueDepth:expected.opaque?.depth??null,depthError:sampleDepthError,
       hits:expected.hits.map(h=>h?{groupIndex:h.groupIndex,sourceFace:h.face,depth:h.depth,v:h.v,normal:h.normal,angle:h.angle,weights:h.weights,conflictingTies:h.conflictingTies}:null)};}
     const item={source:record.id,sourceSha256:record.source_sha256,pose:pose.id,control:control.id,synchronizedRenderMs,color:summary(colorErrors),depth:summary(depthErrors),
      opticalSamples,opaqueSamples,opaqueStopSamples,invalidOracleSamples,supportDisagreements,groupCoverage,worst,
      strictNumericStatus:Math.max(...colorErrors)<=COLOR_TOLERANCE&&Math.max(...depthErrors)<=DEPTH_TOLERANCE&&invalidOracleSamples===0?'passed':'failed',
      image:show(record,pose,control,data)};
     if(worst&&worst.hits.some(Boolean)){const first=worst.hits.findIndex(Boolean);item.worstGpuDiagnostics={groupIndex:first,values:transport.diagnostic(first,worst.x,worst.y)};}
     report.cases.push(item);document.querySelector('#status').textContent=`Measured ${record.id} ${pose.id} ${control.id}: RGB ${item.color.maximum.toExponential(3)}, depth ${item.depth.maximum.toExponential(3)}`;
    }finally{transport.dispose();}await new Promise(resolve=>setTimeout(resolve,0));}
  }}
  report.coverageComplete=report.cases.length===manifest.cases.length*POSES.length*CONTROLS.length;
  report.maximumColorError=Math.max(...report.cases.map(c=>c.color.maximum));report.maximumDepthError=Math.max(...report.cases.map(c=>c.depth.maximum));
  report.strictNumericStatus=report.coverageComplete&&report.cases.every(c=>c.strictNumericStatus==='passed')?'passed':'failed';
  report.status='measured';document.querySelector('#status').textContent=JSON.stringify({status:report.status,strictNumericStatus:report.strictNumericStatus,cases:report.cases.length,maximumColorError:report.maximumColorError,maximumDepthError:report.maximumDepthError,accepted:false},null,2);return report;
 }finally{renderer.dispose();}
}
