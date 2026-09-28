/** Display-copy regression. Composed RGB is the reference, not a physics oracle. */
import * as T from 'three';
import {installCanonicalLensMaterials} from '../src/render/lens-material.ts';
import {CanonicalLensLayers} from '../src/render/lens-layers.ts';
import {LENS_APPEARANCE_EXTENSION} from '../src/eyewear/lens-appearance.ts';
import {EFFECTIVE_OPTICAL_GROUP_PROFILE} from '../src/eyewear/optical-material.ts';

const WIDTH=480,HEIGHT=320,TOLERANCE=2/255;
const appearance={schema_version:1,color_space:'scene_linear_srgb_D65',density_interpolation:'piecewise_smoothstep_optical_density',
 vertical_coordinate:'lens_local_bottom_0_top_1',normal_reflectance_rgb:[0,0,0],refractive_index:1.5,roughness:.1,
 optical_density_keyframes:[{v:0,optical_density_rgb:[.3,.7,1.2]},{v:1,optical_density_rgb:[1.8,1.2,.6]}],angular_reflectance_keyframes:null};
const box=(z=0,x=0,opaque=false)=>({z,x,opaque});
const fixtures=[
 {id:'dense_closed_group',groups:[[box()]]},
 {id:'multipart_same_group',groups:[[box(.15,-.08),box(-.15,.08)]]},
 {id:'overlapping_distinct_groups',groups:[[box(.15,-.12)],[box(-.15,.12)]]},
 {id:'opaque_between_groups',groups:[[box(.2)],[box(-.2)]],opaque:[box(0,0,true)]},
 {id:'opaque_before_closed_group',groups:[[box()]],opaque:[box(.5,0,true)]},
];
const poses=[{yaw:0,roll:0},{yaw:37,roll:21},{yaw:180,roll:0}];
const assert=(condition,message)=>{if(!condition)throw Error(message);};
const sha=async value=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(value)))),b=>b.toString(16).padStart(2,'0')).join('');
function geometry(b){const g=new T.BoxGeometry(b.opaque?.55:1.6,b.opaque?.95:1.2,.12,32,24,2);g.translate(b.x,0,b.z);g.clearGroups();
 const uv=g.getAttribute('uv'),p=g.getAttribute('position');g.computeBoundingBox();
 const low=g.boundingBox.min.y,span=g.boundingBox.max.y-low;
 for(let i=0;i<p.count;i++)uv.setXY(i,0,(p.getY(i)-low)/span);return g;}
function readFloat(renderer,target){const pixels=new Float32Array(WIDTH*HEIGHT*4);renderer.readRenderTargetPixels(target,0,0,WIDTH,HEIGHT,pixels);return pixels;}
async function create(fixture){const root=new T.Group(),scene=new T.Scene(),owned=[];scene.background=new T.Color(.85,.75,.65);scene.add(root);
 const sourceHash=await sha(fixture),appearanceHash=await sha(appearance),meshes=[];
 for(const [groupIndex,boxes]of fixture.groups.entries())for(const [memberIndex,b]of boxes.entries()){
  const g=geometry(b),material=new T.MeshPhysicalMaterial();material.userData.gltfExtensions={[LENS_APPEARANCE_EXTENSION]:{schema_version:1,texcoord:0,appearance}};
  const mesh=new T.Mesh(g,material);mesh.userData={partRole:'lens',lensSurfaceProfile:EFFECTIVE_OPTICAL_GROUP_PROFILE,
   lensUVConvention:'lens_local_bottom_0_top_1',opticalGroupId:`g${groupIndex}`,opticalGroupMemberId:`m${memberIndex}`,
   opticalSourcePartIndex:meshes.length,opticalSourceSha256:sourceHash,lensAppearanceSha256:appearanceHash,
   semanticIdentity:'unverified',materialIdentification:'unmeasured'};
  meshes.push(mesh);root.add(mesh);owned.push(g,material);
 }
 for(const b of fixture.opaque??[]){const g=geometry(b),m=new T.MeshBasicMaterial({color:new T.Color(.18,.4,.65),side:T.DoubleSide,toneMapped:false});root.add(new T.Mesh(g,m));owned.push(g,m);}
 const installation=installCanonicalLensMaterials(root);owned.push(...installation.materials);
 const layers=new CanonicalLensLayers(meshes);
 return {root,scene,layers,dispose(){layers.dispose();for(const resource of owned)resource.dispose();}};
}
export async function run(){const report={schema_version:1,status:'running',cases:[],accepted:false,
 scope:'MSAA display-copy regression only. Actual optical composition is checked by separate analytic conformance.',
 reference:'Already-composed single-sample linear RGB texture; all final surfaces must copy the same RGB. No per-surface optical response is applied in display.',
 mask:'Pixels at least two texels inside the union of captured optical group silhouettes and away from opaque silhouettes. Single-sample reference does not predict partial MSAA coverage at external geometric edges. Internal source triangle boundaries remain included.',
 tolerance_linear_rgb:TOLERANCE,fixtures,poses};window.displayRegressionReport=report;
 const renderer=new T.WebGLRenderer({antialias:true});renderer.setSize(WIDTH,HEIGHT);renderer.outputColorSpace=T.LinearSRGBColorSpace;renderer.toneMapping=T.NoToneMapping;
 const gl=renderer.getContext(),debug=gl.getExtension('WEBGL_debug_renderer_info');report.backend=debug?gl.getParameter(debug.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER);
 const samples=Math.min(4,gl.getParameter(gl.MAX_SAMPLES));assert(samples>1,'Regression requires multisampled display');report.display_samples=samples;
 const target=new T.WebGLRenderTarget(WIDTH,HEIGHT,{samples,minFilter:T.NearestFilter,magFilter:T.NearestFilter,depthBuffer:true});
 target.texture.colorSpace=T.LinearSRGBColorSpace;target.texture.generateMipmaps=false;
 const camera=new T.PerspectiveCamera(35,WIDTH/HEIGHT,.1,100);camera.position.z=4;camera.updateMatrixWorld();
 try{for(const fixture of fixtures){const instance=await create(fixture);try{for(const pose of poses){
  instance.root.rotation.set(0,pose.yaw*Math.PI/180,pose.roll*Math.PI/180,'YXZ');instance.root.updateMatrixWorld(true);
  instance.layers.render(renderer,instance.scene,camera,WIDTH,HEIGHT,draw=>draw());
  const expected=readFloat(renderer,instance.layers.layers[1]);
  const opaque=readFloat(renderer,instance.layers.opaque);
  const maps=instance.layers.nearest.groups.map(group=>readFloat(renderer,group.target));
  renderer.setRenderTarget(target);renderer.setViewport(0,0,WIDTH,HEIGHT);renderer.setClearColor(0,0);renderer.clear(true,true,false);renderer.render(instance.scene,camera);
  const actual=new Uint8Array(WIDTH*HEIGHT*4);renderer.readRenderTargetPixels(target,0,0,WIDTH,HEIGHT,actual);
  const coverage=new Uint8Array(WIDTH*HEIGHT);for(let pixel=0;pixel<coverage.length;pixel++)coverage[pixel]=Number(maps.some(map=>map[pixel*4+3]<1));
  const opaqueCoverage=new Uint8Array(WIDTH*HEIGHT);for(let pixel=0;pixel<opaqueCoverage.length;pixel++)opaqueCoverage[pixel]=Number(Math.abs(opaque[pixel*4]-.18)<.01);
  let tested=0,failed=0,maximum=0,worst=null;
  for(let y=2;y<HEIGHT-2;y++)for(let x=2;x<WIDTH-2;x++){
   let inside=true;const opaqueHere=opaqueCoverage[y*WIDTH+x];
   for(let dy=-2;dy<=2&&inside;dy++)for(let dx=-2;dx<=2;dx++){
    const pixel=(y+dy)*WIDTH+x+dx;if(!coverage[pixel]||opaqueCoverage[pixel]!==opaqueHere){inside=false;break;}}
   if(!inside)continue;tested++;const offset=(y*WIDTH+x)*4;
   const error=Math.max(...[0,1,2].map(c=>Math.abs(actual[offset+c]/255-expected[offset+c])));
   if(error>TOLERANCE)failed++;if(error>maximum){maximum=error;worst={x,y,actual:Array.from(actual.slice(offset,offset+3)).map(v=>v/255),expected:Array.from(expected.slice(offset,offset+3))};}
  }
  report.cases.push({fixture:fixture.id,pose,samples:tested,failed_samples:failed,maximum_error:maximum,worst});
 }}finally{instance.dispose();}}
 report.maximum_error=Math.max(...report.cases.map(row=>row.maximum_error));report.failed_samples=report.cases.reduce((sum,row)=>sum+row.failed_samples,0);
 report.status=report.cases.length===fixtures.length*poses.length&&report.cases.every(row=>row.samples>1000)&&report.failed_samples===0?'passed':'failed';
 document.querySelector('#status').textContent=JSON.stringify(report,null,2);return report;
 }finally{renderer.setRenderTarget(null);target.dispose();renderer.dispose();}
}
