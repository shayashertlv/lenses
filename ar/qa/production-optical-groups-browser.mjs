/** Controlled conformance against ACTUAL production modules, not QA Transport. */
import * as T from 'three';
import {installCanonicalLensMaterials} from '../src/render/lens-material.ts';
import {CanonicalLensLayers} from '../src/render/lens-layers.ts';
import {EyewearShadow} from '../src/render/eyewear-shadow.ts';
import {LENS_APPEARANCE_EXTENSION} from '../src/eyewear/lens-appearance.ts';
import {EFFECTIVE_OPTICAL_GROUP_PROFILE} from '../src/eyewear/optical-material.ts';
import {OpticalLayerOverflowError} from '../src/render/layer-overflow.ts';
import {makeOracle,groupBounds} from './production-optical-groups-oracle.mjs';
import {probeShadowArithmetic} from './production-optical-groups-arithmetic.mjs';
import {probeViewportSubpixels} from './viewport-subpixel-probe.mjs';

const WIDTH=480,HEIGHT=320,COLOR_TOLERANCE=5e-5,DEPTH_TOLERANCE=2e-6;
const BACKGROUND=[.125,.25,.375],OPAQUE=[.625,.125,.25],ENVIRONMENT=[.5,.25,.125];
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const sha=async value=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(value)))),x=>x.toString(16).padStart(2,'0')).join('');
const maxError=(a,b)=>Math.max(...a.map((v,i)=>Math.abs(v-b[i])));
const descriptor=(r=[.15,.25,.35],bottom=[.12,.2,.3],top=[.9,.55,.2])=>({schema_version:1,color_space:'scene_linear_srgb_D65',
 density_interpolation:'piecewise_smoothstep_optical_density',vertical_coordinate:'lens_local_bottom_0_top_1',
 normal_reflectance_rgb:r,refractive_index:1.5,roughness:0,
 optical_density_keyframes:[{v:0,optical_density_rgb:bottom},{v:.4,optical_density_rgb:bottom.map((x,i)=>(x+top[i])*.5)},{v:1,optical_density_rgb:top}],
 angular_reflectance_keyframes:null});
const A=descriptor(),B=descriptor([.65,.35,.1],[.25,.1,.35],[.5,.8,.3]),MIRROR=descriptor([1,1,1],[0,0,0],[0,0,0]);
const ASYMMETRIC={...B,rear_reflection_fraction_rgb:[.02,.15,.4]};
const ANGULAR={...descriptor([.2,.35,.1],[.05,.3,.1],[1.,.1,.7]),angular_reflectance_keyframes:[
 {angle_degrees:0,reflectance_rgb:[.2,.35,.1]},{angle_degrees:25,reflectance_rgb:[.7,.15,.4]},
 {angle_degrees:55,reflectance_rgb:[.15,.6,.85]},{angle_degrees:90,reflectance_rgb:[.9,.9,.95]}]};
const box=(center=[0,0,0],size=[1.6,1.2,.18],extra={})=>({center,size,...extra});
const group=(id,appearance=A,boxes=[box()])=>({id,appearance,boxes});
const poses=[{yaw:0,roll:0},{yaw:37,roll:21},{yaw:180,roll:0}];
const fixtures=[
 {id:'closed_gradient',groups:[group('lens')]},
 {id:'colored_angular_mirror_gradient',groups:[group('lens',ANGULAR)]},
 {id:'multipart_same_group',groups:[group('one',A,[box([0,0,.22]),box([0,0,-.22],[1.45,1.1,.12])])]},
 {id:'equal_descriptor_distinct_groups',shareSourceMaterial:true,groups:[group('a',A,[box([-.15,0,.23])]),group('b',A,[box([.15,0,-.23])])]},
 {id:'mirror_front',groups:[group('mirror',MIRROR,[box([0,0,.3])]),group('tint',A,[box([0,0,-.3])])]},
 {id:'mirror_rear',groups:[group('tint',A,[box([0,0,.3])]),group('mirror',MIRROR,[box([0,0,-.3])])]},
 {id:'opaque_inside_closed_group',groups:[group('volume',A,[box([0,0,0],[1.6,1.2,.65])])],opaque:[box([0,0,0],[.52,.9,.08])]},
 {id:'opaque_between_groups',groups:[group('a',A,[box([0,0,.4])]),group('b',B,[box([0,0,-.4])])],opaque:[box([0,0,0],[.52,.9,.08])]},
 {id:'opaque_before_group',groups:[group('a')],opaque:[box([0,0,.55],[.52,.9,.08])]},
 {id:'four_groups',groups:[group('a',A,[box([-.12,0,.6])]),group('b',B,[box([0,0,.2])]),group('c',A,[box([.08,0,-.2])]),group('d',B,[box([.16,0,-.6])])]},
 {id:'reversed_winding',groups:[group('lens',A,[box([0,0,0],[1.6,1.2,.18],{reversed:true})])]},
 {id:'asymmetric_rear_coating',groups:[group('lens',ASYMMETRIC)]},
 {id:'asymmetric_reversed_winding',groups:[group('lens',ASYMMETRIC,[box([0,0,0],[1.6,1.2,.18],{reversed:true})])]},
];
function geometry(b,bounds){const geometry=new T.BoxGeometry(...b.size);geometry.translate(...b.center);geometry.clearGroups();
 const p=geometry.getAttribute('position'),uv=geometry.getAttribute('uv');
 for(let i=0;i<p.count;i++)uv.setXY(i,0,(p.getY(i)-bounds[0])/(bounds[1]-bounds[0]));
 if(b.reversed){const index=geometry.index;for(let i=0;i<index.count;i+=3){const a=index.getX(i);index.setX(i,index.getX(i+1));index.setX(i+1,a);}}
 return geometry;}
function target(w,h){const target=new T.WebGLRenderTarget(w,h,{type:T.FloatType,minFilter:T.NearestFilter,magFilter:T.NearestFilter,depthBuffer:true,stencilBuffer:false});target.texture.colorSpace=T.LinearSRGBColorSpace;target.texture.generateMipmaps=false;return target;}
function read(renderer,target){const raw=new Float32Array(target.width*target.height*4);renderer.readRenderTargetPixels(target,0,0,target.width,target.height,raw);return raw;}
function samples(w,h){const result=[];for(let y=11;y<h-10;y+=17)for(let x=13;x<w-10;x+=19)result.push([x,y]);return result;}
function summary(rows,key){return {maximum:Math.max(0,...rows.map(r=>r[key])),mean:rows.reduce((s,r)=>s+r[key],0)/Math.max(1,rows.length),count:rows.length};}
const worst=(rows,key)=>rows.reduce((a,b)=>!a||b[key]>a[key]?b:a,null);
function camera(kind){const camera=kind==='perspective'?new T.PerspectiveCamera(35,WIDTH/HEIGHT,.1,120):new T.OrthographicCamera(-7.8*WIDTH/HEIGHT,7.8*WIDTH/HEIGHT,7.8,-7.8,.1,120);camera.position.z=30;camera.updateMatrixWorld();return camera;}
function pose(root,rotation){root.rotation.set(0,rotation.yaw*Math.PI/180,rotation.roll*Math.PI/180,'YXZ');root.scale.setScalar(6);root.updateMatrixWorld(true);}
function flatEnvironment(renderer,color){const generator=new T.PMREMGenerator(renderer),scene=new T.Scene();scene.background=new T.Color(...color);
 try{return generator.fromScene(scene,0,.1,100);}finally{generator.dispose();}}
function screenshot(data,w,h,label){const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;const image=canvas.getContext('2d').createImageData(w,h);
 for(let y=0;y<h;y++)for(let x=0;x<w;x++){const from=(y*w+x)*4,to=((h-1-y)*w+x)*4;for(let c=0;c<3;c++){const linear=Math.max(0,Math.min(1,data[from+c]));image.data[to+c]=Math.round(255*(linear<=.0031308?12.92*linear:1.055*linear**(1/2.4)-.055));}image.data[to+3]=255;}
 canvas.getContext('2d').putImageData(image,0,0);const card=document.createElement('div');card.className='card';const title=document.createElement('div');title.textContent=label;const imageNode=new Image();imageNode.src=canvas.toDataURL();card.append(title,imageNode);document.querySelector('#cards').append(card);}

async function create(renderer,fixture,environment){const root=new T.Group(),scene=new T.Scene();scene.background=new T.Color(...BACKGROUND);scene.add(root);
 const sourceHash=await sha(fixture),sourceMaterials=[],geometries=[],opticalMeshes=[];let part=0,shared=null;
 for(const g of fixture.groups){const bounds=groupBounds(g),appearanceHash=await sha(g.appearance);
  let material=fixture.shareSourceMaterial?shared:null;
  if(!material){material=new T.MeshPhysicalMaterial({transmission:0});material.userData.gltfExtensions={[LENS_APPEARANCE_EXTENSION]:{schema_version:1,texcoord:0,appearance:g.appearance}};sourceMaterials.push(material);if(fixture.shareSourceMaterial)shared=material;}
  for(const [index,b]of g.boxes.entries()){const geom=geometry(b,bounds);geometries.push(geom);const mesh=new T.Mesh(geom,material);
   mesh.name=`${g.id}/member-${index}`;mesh.userData={partRole:'lens',lensSurfaceProfile:EFFECTIVE_OPTICAL_GROUP_PROFILE,lensUVConvention:'lens_local_bottom_0_top_1',opticalGroupId:g.id,opticalGroupMemberId:`member-${index}`,opticalSourcePartIndex:part++,opticalSourceSha256:sourceHash,lensAppearanceSha256:appearanceHash,semanticIdentity:'unverified',materialIdentification:'unmeasured'};
   root.add(mesh);opticalMeshes.push(mesh);}}
 for(const b of fixture.opaque??[]){const geom=geometry(b,[-1,1]),material=new T.MeshBasicMaterial({color:new T.Color(...OPAQUE),side:T.DoubleSide,toneMapped:false});geometries.push(geom);sourceMaterials.push(material);root.add(new T.Mesh(geom,material));}
 const installation=installCanonicalLensMaterials(root);
 assert(installation.profile===EFFECTIVE_OPTICAL_GROUP_PROFILE,'Actual installer did not install effective groups');
 assert(installation.effectiveGroups.length===fixture.groups.length,'Installer changed explicit group count');
 assert(installation.materials.length===fixture.groups.length,'Mutable materials were aliased across groups');
 for(const material of installation.materials){material.envMap=environment.texture;material.envMapIntensity=1;material.needsUpdate=true;}
 scene.environment=environment.texture;scene.environmentIntensity=1;
 const layers=new CanonicalLensLayers(opticalMeshes),display=target(WIDTH,HEIGHT);
 const face=new T.PlaneGeometry(24,24);face.translate(0,0,-7);
 const shadows=new EyewearShadow(renderer,root,face);
 const canvas=document.createElement('canvas');canvas.width=WIDTH;canvas.height=HEIGHT;canvas.getContext('2d').fillStyle='white';canvas.getContext('2d').fillRect(0,0,WIDTH,HEIGHT);
 const source=new T.CanvasTexture(canvas);source.colorSpace=T.SRGBColorSpace;
 return {root,scene,layers,display,shadows,source,installation,opticalMeshes,
  dispose(){shadows.dispose();layers.dispose();display.dispose();face.dispose();source.dispose();for(const material of [...sourceMaterials,...installation.materials])material.dispose();for(const geom of geometries)geom.dispose();}};
}
function checkVisible(renderer,fixture,instance,rotation,kind,environment){const view=camera(kind);pose(instance.root,rotation);
 instance.layers.render(renderer,instance.scene,view,WIDTH,HEIGHT,draw=>draw());
 const composed=read(renderer,instance.layers.layers[1]);
 renderer.setRenderTarget(instance.display);renderer.setViewport(0,0,WIDTH,HEIGHT);renderer.state.buffers.depth.setClear(1);renderer.setClearColor(0,0);renderer.clear(true,true,false);renderer.render(instance.scene,view);
 const displayed=read(renderer,instance.display),oracle=makeOracle(fixture,instance.root.matrixWorld,view,WIDTH,HEIGHT);
 const symmetricFixture={...fixture,groups:fixture.groups.map(g=>{const appearance={...g.appearance};delete appearance.rear_reflection_fraction_rgb;return {...g,appearance};})};
 const frontOnlyOracle=makeOracle(symmetricFixture,instance.root.matrixWorld,view,WIDTH,HEIGHT);
 const groups=instance.installation.effectiveGroups.map(g=>g.id),maps=new Map(instance.layers.nearest.groups.map(g=>[g.id,read(renderer,g.target)]));
 const rows=[];let excluded=0,optical=0,maxGroups=0,opaquePixels=0;
 for(const [x,y]of samples(WIDTH,HEIGHT)){const continuous=oracle.continuous(x,y,environment);if(continuous.margin<.035||continuous.ambiguities.length){excluded++;continue;}
  const expected=oracle.at(x,y,environment),offset=(y*WIDTH+x)*4,actual=Array.from(composed.slice(offset,offset+3)),display=Array.from(displayed.slice(offset,offset+3));
  if(expected.relevant.length)optical++;if(expected.opaqueDepth<1)opaquePixels++;maxGroups=Math.max(maxGroups,expected.relevant.length);
  const depthError=Math.max(...groups.map(id=>Math.abs(maps.get(id)[offset+3]-(expected.hits.find(h=>h.group===id)?.depth??1))));
  let doubled=expected.opaqueDepth<1?[...OPAQUE]:[...BACKGROUND];for(const hit of [...expected.relevant].reverse())for(let repeat=0;repeat<2;repeat++)doubled=doubled.map((v,c)=>v*hit.T[c]+hit.R[c]*environment[c]);
  let merged=expected.opaqueDepth<1?[...OPAQUE]:[...BACKGROUND];for(const hit of expected.relevant.slice(0,1).reverse())merged=merged.map((v,c)=>v*hit.T[c]+hit.R[c]*environment[c]);
  rows.push({x,y,groups:expected.relevant.map(h=>h.group),actual,display,expected:expected.color,error:maxError(actual,expected.color),displayError:maxError(display,expected.color),depthError,
   continuousError:maxError(actual,continuous.color),doubleApplicationDifference:maxError(actual,doubled),groupMergeDifference:maxError(actual,merged),
   ignoredRearResponseDifference:maxError(actual,frontOnlyOracle.at(x,y,environment).color)});
 }
 if(kind==='perspective'&&rotation.yaw===0&&environment===ENVIRONMENT)screenshot(displayed,WIDTH,HEIGHT,fixture.id);
 return {fixture:fixture.id,pose:rotation,projection:kind,environment,samples:rows.length,opticalSamples:optical,opaqueSamples:opaquePixels,excludedByPredeclaredGeometryMargin:excluded,maximumGroups:maxGroups,
  color:summary(rows,'error'),display:summary(rows,'displayError'),depth:summary(rows,'depthError'),continuousSlabColor:summary(rows,'continuousError'),worst:worst(rows,'error'),displayWorst:worst(rows,'displayError'),depthWorst:worst(rows,'depthError'),
  ignoredRearResponseNegativeControl:summary(rows,'ignoredRearResponseDifference').maximum,
  doubledGroupNegativeControl:summary(rows,'doubleApplicationDifference').maximum,mergedGroupsNegativeControl:summary(rows,'groupMergeDifference').maximum};
}
function checkShadow(renderer,fixture,instance,rotation){pose(instance.root,rotation);const view=camera('perspective');
 instance.shadows.render(view,instance.source,WIDTH,HEIGHT,{enabled:true,frameStrength:1,lensStrength:1,softness:0});
 const light=instance.shadows.lightCamera,targets=instance.shadows.canonicalTargets.slice(0,4),raw=targets.map(t=>read(renderer,t)),size=targets[0].width;
 const oracle=makeOracle(fixture,instance.root.matrixWorld,light,size,size),rows=[];let excluded=0,optical=0,maxGroups=0,opaquePixels=0;
 for(const [x,y]of samples(size,size)){const continuous=oracle.continuous(x,y,ENVIRONMENT);if(continuous.margin<.035||continuous.ambiguities.length){excluded++;continue;}
  const expected=oracle.at(x,y,ENVIRONMENT),offset=(y*size+x)*4;if(expected.relevant.length)optical++;if(expected.opaqueDepth<1)opaquePixels++;maxGroups=Math.max(maxGroups,expected.relevant.length);
  for(let layer=0;layer<4;layer++){const hit=expected.relevant[layer],nominal=continuous.relevant[layer],actual=Array.from(raw[layer].slice(offset,offset+3));
   rows.push({x,y,layer,group:hit?.group??null,actual,expected:hit?.T??[1,1,1],actualDepth:raw[layer][offset+3],expectedDepth:hit?.depth??1,
    error:maxError(actual,hit?.T??[1,1,1]),depthError:Math.abs(raw[layer][offset+3]-(hit?.depth??1)),continuousError:maxError(actual,nominal?.T??[1,1,1]),continuousDepthError:Math.abs(raw[layer][offset+3]-(nominal?.depth??1))});}
 }
 return {fixture:fixture.id,pose:rotation,rays:rows.length/4,opticalSamples:optical,opaqueSamples:opaquePixels,maximumGroups:maxGroups,excludedByPredeclaredGeometryMargin:excluded,
  transmission:summary(rows,'error'),depth:summary(rows,'depthError'),continuousSlabTransmission:summary(rows,'continuousError'),continuousSlabDepth:summary(rows,'continuousDepthError'),worst:worst(rows,'error'),depthWorst:worst(rows,'depthError')};
}
const smooth=(lo,hi,value)=>{const t=Math.max(0,Math.min(1,(value-lo)/(hi-lo)));return t*t*(3-2*t);};
const linear=byte=>{const value=byte/255;return value<=.04045?value/12.92:((value+.055)/1.055)**2.4;};
function receiverFilter(oracle,lp,mapSize,depthSpan,contactEnd){const u=lp.x*.5+.5,v=lp.y*.5+.5,receiverDepth=lp.z*.5+.5,expected=[0,0,0],taps=[];
 const weight=depth=>{const separation=(receiverDepth-depth)*depthSpan;return smooth(.025,contactEnd,separation)*(1-smooth(4,10,separation));};
 for(const [dx,dy]of [[0,0],[1,0],[-1,0],[0,1],[0,-1]]){const gx=u*mapSize+dx-.5,gy=v*mapSize+dy-.5,bx=Math.floor(gx),by=Math.floor(gy),fx=gx-bx,fy=gy-by;
  for(const [ox,oy,w]of [[0,0,(1-fx)*(1-fy)],[1,0,fx*(1-fy)],[0,1,(1-fx)*fy],[1,1,fx*fy]]){
   const x=bx+ox,y=by+oy;let transmission=[1,1,1],activeLayers=0,opaque=false;
   if(x>=0&&x<mapSize&&y>=0&&y<mapSize){const sample=oracle.at(x,y,ENVIRONMENT);
    for(const hit of sample.relevant)if(hit.depth<receiverDepth){const strength=weight(hit.depth);transmission=transmission.map((value,c)=>value*(1-strength+strength*hit.T[c]));if(strength>0)activeLayers++;}
    if(sample.opaqueDepth<.99999){const strength=weight(sample.opaqueDepth);transmission=transmission.map(value=>value*(1-strength));opaque=strength>0;}}
   for(let c=0;c<3;c++)expected[c]+=w/5*transmission[c];taps.push({x,y,weight:w/5,activeLayers,opaque,transmission});}}
 return {expected,taps};}
function checkReceivers(renderer,fixture,instance){pose(instance.root,poses[0]);const view=camera('perspective'),shadow=instance.shadows;
 const render=()=>shadow.render(view,instance.source,WIDTH,HEIGHT,{enabled:true,frameStrength:1,lensStrength:1,softness:0});render();
 const light=shadow.lightCamera,size=shadow.canonicalTargets[0].width,oracle=makeOracle(fixture,instance.root.matrixWorld,light,size,size);
 const guide=new T.Vector3().applyMatrix4(instance.root.matrixWorld),lp=guide.clone().project(light),x=(lp.x+1)*size/2-.5,y=(lp.y+1)*size/2-.5;
 const center=oracle.continuous(x,y,ENVIRONMENT),near=new T.Vector3(lp.x,lp.y,-1).unproject(light),far=new T.Vector3(lp.x,lp.y,1).unproject(light),direction=far.sub(near).normalize();
 assert(center.hits.length>=1,'Receiver control requires a central optical group');
 const distance=point=>new T.Vector3(...point).applyMatrix4(instance.root.matrixWorld).sub(near).dot(direction);
 const first=distance(center.hits[0].point),last=distance(center.hits.at(-1).point),exit=distance(center.hits.at(-1).exitPoint);
 const locations=center.hits.length===1?[['before',first-.7],['inside',.5*(first+exit)],['behind',exit+.7]]:[['before',first-.7],['between',.5*(first+last)],['behind',exit+.7]];
 const receiver=shadow.receiverScene.children.find(object=>object.isMesh),original=receiver.geometry,rows=[];
 const normal=direction.clone().negate(),uniforms=shadow.receiverMaterial.uniforms,contactEnd=Math.max(.14,2*uniforms.mapSpanCm.value/size);
 try{for(const [location,t]of locations){const centerPoint=near.clone().addScaledVector(direction,t),geometry=new T.PlaneGeometry(2,2);
   geometry.applyQuaternion(new T.Quaternion().setFromUnitVectors(new T.Vector3(0,0,1),normal));geometry.translate(...centerPoint.toArray());receiver.geometry=geometry;
   try{render();const target=shadow.compositeTarget,pixels=new Uint8Array(WIDTH*HEIGHT*4);renderer.readRenderTargetPixels(target,0,0,WIDTH,HEIGHT,pixels);
    const projected=centerPoint.clone().project(view),px=Math.floor((projected.x+1)*WIDTH/2),py=Math.floor((projected.y+1)*HEIGHT/2),plane=new T.Plane().setFromNormalAndCoplanarPoint(normal,centerPoint),samples=[];
    for(const dy of [-1,0,1])for(const dx of [-1,0,1]){const x=px+dx,y=py+dy,ray=new T.Raycaster();ray.setFromCamera(new T.Vector2(2*(x+.5)/WIDTH-1,2*(y+.5)/HEIGHT-1),view);
     const point=ray.ray.intersectPlane(plane,new T.Vector3());assert(point,'Receiver sample must intersect controlled plane');const lp=point.project(light),expected=receiverFilter(oracle,lp,size,uniforms.depthSpanCm.value,contactEnd),offset=(y*WIDTH+x)*4;
     const actual=[0,1,2].map(c=>linear(pixels[offset+c]));samples.push({x,y,actual,...expected,error:maxError(actual,expected.expected)});}
    const taps=samples.flatMap(sample=>sample.taps).filter(tap=>tap.weight>0);
    rows.push({location,samples,maximumError:Math.max(...samples.map(s=>s.error)),
     activeLayerRange:[Math.min(...taps.map(tap=>tap.activeLayers)),Math.max(...taps.map(tap=>tap.activeLayers))],
     opaqueTapCount:taps.filter(tap=>tap.opaque).length});
   }finally{receiver.geometry=original;geometry.dispose();}}
 }finally{receiver.geometry=original;}
 return {fixture:fixture.id,tolerance:.008,probes:rows,maximumError:Math.max(...rows.map(r=>r.maximumError)),
  scope:'Actual EyewearShadow receiver, white source, unit strengths, zero softness. Independent recipe-only 20 shadow-texel comparisons per screen pixel, with production contact/falloff and receiver-depth qualification; sRGB8 result decoded to linear.'};}
async function overflow(renderer,environment){const fixture={id:'overflow',groups:Array.from({length:5},(_,i)=>group(`g${i}`,A,[box([0,0,.8-i*.4],[1.6,1.2,.12])]))},instance=await create(renderer,fixture,environment),view=camera('perspective'),result=[];pose(instance.root,poses[0]);
 try{for(const path of ['camera','shadow']){let rejected=false,message;try{if(path==='camera')instance.layers.render(renderer,instance.scene,view,WIDTH,HEIGHT,draw=>draw());else instance.shadows.render(view,instance.source,WIDTH,HEIGHT,{enabled:true,frameStrength:1,lensStrength:1,softness:0});}
  catch(error){rejected=error instanceof OpticalLayerOverflowError;message=String(error);}result.push({path,rejected,message});}}finally{instance.dispose();}return result;}

export async function run(mode='conformance'){const report=window.productionGroupReport={schemaVersion:1,status:'running',accepted:false,qualityVerdict:'unmeasured',profile:EFFECTIVE_OPTICAL_GROUP_PROFILE,
 implementation:'Actual installCanonicalLensMaterials (including createCanonicalLensMaterial), CanonicalLensLayers, NearestOpticalGroups and EyewearShadow',fixtures,cases:[],shadowCases:[],receiverCases:[],
 tolerances:{linearRGB:COLOR_TOLERANCE,normalizedDepth:DEPTH_TOLERANCE,linearDecodedSRGB8Receiver:.008},
 limitations:['Synthetic recipe-generated geometry; actual five-GLB/GLTFLoader evidence is a separate harness.',
  'No TryOnRenderer face fitting, AR protection or camera stream is exercised by this bounded low-level production transport harness.',
  'Float32 shader-transform reference and separately qualified direct viewport-to-n.8 snapping are device-qualified, not a universal arithmetic proof.',
  'Geometry edge margin .035 source units is fixed before pixel errors. Continuous slab diagnostics are also retained.',
  'Flat controlled PMREM environment and roughness zero; no photographic material identification or roughness reconstruction.',
  'Receiver controls use known planes and no hair mask; face tracking and hair segmentation remain outside this fixture.']};
 const renderer=new T.WebGLRenderer({antialias:false});renderer.setSize(WIDTH,HEIGHT);renderer.outputColorSpace=T.LinearSRGBColorSpace;renderer.toneMapping=T.NoToneMapping;renderer.toneMappingExposure=1;
 const gl=renderer.getContext(),debug=gl.getExtension('WEBGL_debug_renderer_info');report.backend={renderer:debug?gl.getParameter(debug.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER),vendor:debug?gl.getParameter(debug.UNMASKED_VENDOR_WEBGL):gl.getParameter(gl.VENDOR),version:gl.getParameter(gl.VERSION),subpixelBits:gl.getParameter(gl.SUBPIXEL_BITS),maxTextureUnits:gl.getParameter(gl.MAX_TEXTURE_IMAGE_UNITS)};
 assert(/Direct3D11|D3D11/i.test(report.backend.renderer),'Device-qualified raster oracle requires D3D11');assert(renderer.extensions.has('EXT_color_buffer_float'),'Float color targets unavailable');
 report.viewportRaster=probeViewportSubpixels();
 const environment=flatEnvironment(renderer,ENVIRONMENT),black=flatEnvironment(renderer,[0,0,0]);
 try{if(mode==='arithmetic'){const fixture=fixtures.find(f=>f.id==='multipart_same_group'),instance=await create(renderer,fixture,environment);try{report.shadowCases.push(checkShadow(renderer,fixture,instance,poses[1]));report.arithmetic=probeShadowArithmetic(renderer,fixture,instance.root,instance.shadows);report.status='diagnostic';return report;}finally{instance.dispose();}}
  for(const fixture of fixtures){const instance=await create(renderer,fixture,environment);try{
   for(const rotation of poses){for(const kind of ['perspective','orthographic'])report.cases.push(checkVisible(renderer,fixture,instance,rotation,kind,ENVIRONMENT));report.shadowCases.push(checkShadow(renderer,fixture,instance,rotation));}
   if(['closed_gradient','equal_descriptor_distinct_groups','opaque_between_groups'].includes(fixture.id))report.receiverCases.push(checkReceivers(renderer,fixture,instance));
   if(fixture.id==='closed_gradient'||fixture.id==='mirror_front'){instance.scene.environment=black.texture;for(const material of instance.installation.materials)material.envMap=black.texture;
    report.cases.push(checkVisible(renderer,fixture,instance,poses[0],'perspective',[0,0,0]));}
  }finally{instance.dispose();}
  document.querySelector('#status').textContent=`Measured ${fixture.id}: ${report.cases.length} camera, ${report.shadowCases.length} light cases`;await new Promise(r=>setTimeout(r,0));}
  report.capacity=await overflow(renderer,environment);
  report.maximumColorError=Math.max(...report.cases.flatMap(c=>[c.color.maximum,c.display.maximum]),...report.shadowCases.map(c=>c.transmission.maximum));
  report.maximumDepthError=Math.max(...report.cases.map(c=>c.depth.maximum),...report.shadowCases.map(c=>c.depth.maximum));
  report.maximumReceiverError=Math.max(...report.receiverCases.map(c=>c.maximumError));
  report.coverageComplete=report.cases.length===fixtures.length*6+2&&report.shadowCases.length===fixtures.length*3&&report.cases.every(c=>c.opticalSamples>=8)&&report.shadowCases.every(c=>c.opticalSamples>=8)
   &&report.cases.some(c=>c.fixture==='four_groups'&&c.maximumGroups===4)&&report.shadowCases.some(c=>c.fixture==='four_groups'&&c.maximumGroups===4);
  report.negativeControls={doubleApplicationDetected:report.cases.some(c=>c.fixture==='closed_gradient'&&c.doubledGroupNegativeControl>1e-3),mergedGroupDetected:report.cases.some(c=>c.fixture==='equal_descriptor_distinct_groups'&&c.maximumGroups===2&&c.mergedGroupsNegativeControl>1e-3),
   asymmetricRearDetected:report.cases.some(c=>c.fixture==='asymmetric_rear_coating'&&c.pose.yaw===180&&c.ignoredRearResponseNegativeControl>.01),
   reversedWindingRetainsRear:report.cases.some(c=>c.fixture==='asymmetric_reversed_winding'&&c.pose.yaw===180&&c.ignoredRearResponseNegativeControl>.01),
   asymmetricFrontUnchanged:report.cases.filter(c=>c.fixture.startsWith('asymmetric_')&&c.pose.yaw===0).every(c=>c.ignoredRearResponseNegativeControl<=COLOR_TOLERANCE)};
  report.receiverCoverageComplete=report.receiverCases.length===3&&report.receiverCases.every(c=>c.probes.length===3&&c.probes.every(p=>p.samples.length===9)
   &&c.probes[0].activeLayerRange[1]===0&&c.probes[0].opaqueTapCount===0&&c.probes[1].activeLayerRange[0]===1
   &&c.probes[2].activeLayerRange[0]===(c.fixture==='equal_descriptor_distinct_groups'?2:1)
   &&(c.fixture!=='opaque_between_groups'||c.probes[2].opaqueTapCount>0));
  report.status=report.viewportRaster.status==='passed'&&report.viewportRaster.backend===report.backend.renderer&&report.coverageComplete&&report.receiverCoverageComplete&&Object.values(report.negativeControls).every(Boolean)&&report.capacity.every(c=>c.rejected)&&report.maximumColorError<=COLOR_TOLERANCE&&report.maximumDepthError<=DEPTH_TOLERANCE&&report.maximumReceiverError<=.008?'passed':'failed';
  document.querySelector('#status').textContent=JSON.stringify({status:report.status,cases:report.cases.length,shadowCases:report.shadowCases.length,maximumColorError:report.maximumColorError,maximumDepthError:report.maximumDepthError,capacity:report.capacity},null,2);return report;
 }finally{renderer.setRenderTarget(null);environment.dispose();black.dispose();renderer.dispose();}}
