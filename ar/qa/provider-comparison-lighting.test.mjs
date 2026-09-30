/** The AR harness's explicit-environment seam (provider-comparison-lighting.mjs) follows the runtime (review AR-R4,
 * 2026-09-30), and the harness draws a case at its try-on lensenv (review AR-R2). Explicit environments come from
 * bsa/archeck(environment=), reconstruction/lens_asset.py and segmented_astra_observe; the modeler route passes none. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import test from 'node:test';
import * as THREE from 'three';
import {FRONT_PANEL} from '../src/render/eyewear-reflection.ts';
import {classifyAssetMaterials,isTranslucentFrameMaterial,markFrameMaterial} from '../src/eyewear/optical-material.ts';
import {LENS_APPEARANCE_EXTENSION} from '../src/eyewear/lens-appearance.ts';
import {applyRuntimeLighting,caseLensEnvIntensity,createLighting,lightingConfigurations} from './provider-comparison-lighting.mjs';

/** Every PMREM createLighting generates: the source's emitters [x,y,z,emissive], its background, the blur, the target. */
function recordPmrem(t){
 const calls=[],original=THREE.PMREMGenerator.prototype.fromScene;
 THREE.PMREMGenerator.prototype.fromScene=function(scene,sigma){
  const emitters=scene.children.filter(child=>child.isMesh&&child.material?.isMeshLambertMaterial).map(child=>[...child.position.toArray(),child.material.emissiveIntensity]);
  const target={texture:new THREE.Texture(),disposed:false,dispose(){this.disposed=true;}};
  calls.push({emitters,background:scene.background,sigma,target});return target;
 };
 t.after(()=>{THREE.PMREMGenerator.prototype.fromScene=original;});
 return calls;
}
const isPanel=row=>row.join()===[...FRONT_PANEL.position,FRONT_PANEL.emissiveIntensity].join();

test('an explicit room lights canonical lenses and crystal with the runtime\'s see-through room, without the front panel',t=>{
 const calls=recordPmrem(t);
 const [configuration]=lightingConfigurations({environments:[{id:'room',preset:'room',intensity:.8}]});
 const lighting=createLighting({},configuration);
 const [regular,canonical]=calls;
 assert.equal(calls.length,2);
 assert.equal(regular.sigma,.04);assert.equal(regular.emitters.length,6);assert.ok(regular.emitters.some(isPanel),'the scene keeps three\'s room, panel included');
 assert.equal(canonical.sigma,0);assert.equal(canonical.emitters.length,5);
 assert.ok(!canonical.emitters.some(isPanel),'no emitter at the front panel: the white lens rectangles stay out of QA reports');
 assert.deepEqual(canonical.emitters,regular.emitters.filter(row=>!isPanel(row)),'every other emitter is the scene room\'s');
 assert.equal(lighting.regular,regular.target);assert.equal(lighting.canonical,canonical.target);
 assert.match(configuration.canonical_description,/without its \+Z front panel/);
 lighting.dispose();assert.ok(regular.target.disposed&&canonical.target.disposed);
});

test('an analytic studio preset lights both from the same map, as before',t=>{
 const calls=recordPmrem(t);
 for(const preset of ['broad_studio','side_studio']){
  const [configuration]=lightingConfigurations({environments:[{id:preset.replace('_','-'),preset,intensity:1.2}]});
  createLighting({},configuration).dispose();
  const [regular,canonical]=calls.splice(0);
  assert.equal(regular.sigma,.04);assert.equal(canonical.sigma,0);
  assert.ok(regular.background?.isDataTexture);assert.equal(canonical.background,regular.background);
 }
});

/** A runtime instance as the seam reads it: a canonical lens, a crystal front, a crystal temple whose near-arm overlay mesh
 *  carries [overlay clone, gold] in one slot, their see-through room map (renderer.ts), and one twin already cloned. */
function instanceWithCrystal(){
 const seeThroughRoom=new THREE.Texture(),sceneRoom=new THREE.Texture();
 const lensSource=new THREE.MeshPhysicalMaterial();
 lensSource.userData.gltfExtensions={[LENS_APPEARANCE_EXTENSION]:{schema_version:1,texcoord:0,appearance:{schema_version:1,color_space:'scene_linear_srgb_D65',
  density_interpolation:'piecewise_smoothstep_optical_density',vertical_coordinate:'lens_local_bottom_0_top_1',normal_reflectance_rgb:[1,1,1],
  refractive_index:1.5,roughness:.065,optical_density_keyframes:[{v:0,optical_density_rgb:[.3,.4,.5]}],angular_reflectance_keyframes:null}}};
 const crystalFront=new THREE.MeshPhysicalMaterial({name:'crystal front',transmission:1,envMap:seeThroughRoom,envMapIntensity:.8});
 const crystalTemple=new THREE.MeshPhysicalMaterial({name:'crystal temple',transmission:1,envMap:seeThroughRoom,envMapIntensity:.8});
 const gold=new THREE.MeshPhysicalMaterial({name:'gold',metalness:1});
 const lensMesh=new THREE.Mesh(new THREE.PlaneGeometry(),lensSource),front=new THREE.Mesh(new THREE.BoxGeometry(),crystalFront);
 const temple=new THREE.Mesh(new THREE.BoxGeometry(),crystalTemple),hinge=new THREE.Mesh(new THREE.BoxGeometry(),gold);
 lensMesh.userData={partRole:'lens',lensSurfaceProfile:'front_sheet_v1'};front.userData.partRole=hinge.userData.partRole='frame';temple.userData.partRole='temple';
 classifyAssetMaterials(new THREE.Group().add(lensMesh,front,temple,hinge));
 const overlay=crystalTemple.clone();overlay.name='crystal temple overlay';markFrameMaterial(overlay);   // as temple-visibility.ts registers it
 assert.ok([crystalFront,crystalTemple,overlay].every(isTranslucentFrameMaterial)&&!isTranslucentFrameMaterial(gold));
 // The drawn canonical lens owns the see-through room at 0.8 x lensenv 1.5 (renderer.ts).
 const lens=new THREE.MeshPhysicalMaterial({name:'canonical lens',envMap:seeThroughRoom,envMapIntensity:.8*1.5});
 const twin=crystalFront.clone();twin.name='crystal front twin';twin.transmission=0;
 const scene=new THREE.Scene();scene.environment=sceneRoom;scene.environmentIntensity=.8;
 const overlayMesh=new THREE.Mesh(new THREE.BoxGeometry(),[overlay,gold]);
 return {seeThroughRoom,sceneRoom,crystalFront,crystalTemple,overlay,gold,lens,twin,instance:{scene,renderer:{isWebGLRenderer:true},
  lensMeshes:[new THREE.Mesh(new THREE.PlaneGeometry(),lens)],canonicalLensMaterials:[lens],
  translucentFrameMeshes:[{mesh:front,original:crystalFront},{mesh:temple,original:crystalTemple},{mesh:overlayMesh,original:[overlay,gold]}],
  translucentTwins:new Map([[crystalFront,twin]])}};
}

test('crystal follows the explicit environment, twins included, and every touched field is restored',t=>{
 recordPmrem(t);
 const f=instanceWithCrystal();
 const [configuration]=lightingConfigurations({environments:[{id:'broad',preset:'broad_studio',intensity:1.4}]});
 const lighting=createLighting({},configuration),restore=applyRuntimeLighting(f.instance,lighting);
 assert.equal(f.instance.scene.environment,lighting.regular.texture);assert.equal(f.instance.scene.environmentIntensity,1.4);
 for(const material of [f.crystalFront,f.crystalTemple,f.overlay,f.twin]){
  assert.equal(material.envMap,lighting.canonical.texture,`${material.name}: the explicit environment, not the runtime room`);
  assert.equal(material.envMapIntensity,1.4,`${material.name}: the configuration's intensity; lensenv is a lens setting`);
 }
 assert.equal(f.lens.envMap,lighting.canonical.texture);assert.ok(Math.abs(f.lens.envMapIntensity-1.4*1.5)<1e-12,'the lens keeps its lensenv');
 assert.equal(f.gold.envMap,null,'gold in the overlay\'s slot keeps the scene environment');
 // A twin cloned while the explicit lighting is applied copies it from its original (the clone copies envMap)...
 const late=f.crystalTemple.clone();late.name='crystal temple twin';late.transmission=0;f.instance.translucentTwins.set(f.crystalTemple,late);
 assert.equal(late.envMap,lighting.canonical.texture);
 restore();
 assert.equal(f.instance.scene.environment,f.sceneRoom);assert.equal(f.instance.scene.environmentIntensity,.8);
 for(const material of [f.crystalFront,f.crystalTemple,f.overlay,f.twin,late]){
  assert.equal(material.envMap,f.seeThroughRoom,`${material.name}: back on the runtime's see-through room`);
  assert.equal(material.envMapIntensity,.8,material.name);
 }
 // ...and returns to its original's restored map, so a disposed explicit map is never left on a drawn twin.
 assert.equal(f.lens.envMap,f.seeThroughRoom);assert.ok(Math.abs(f.lens.envMapIntensity-.8*1.5)<1e-12);
 lighting.dispose();
});

test('a runtime without the crystal fields fails loudly instead of lighting half the eyewear',t=>{
 recordPmrem(t);
 const f=instanceWithCrystal(),[configuration]=lightingConfigurations({environments:[{id:'room',preset:'room'}]});
 const lighting=createLighting({},configuration);
 for(const key of ['translucentFrameMeshes','translucentTwins']){
  const instance={...f.instance,[key]:undefined};
  assert.throws(()=>applyRuntimeLighting(instance,lighting),/Runtime QA lighting seam changed/,key);
 }
 assert.equal(f.crystalFront.envMap,f.seeThroughRoom,'nothing was touched');
 lighting.dispose();
});

test('a case\'s lens_env_intensity is the try-on link\'s lensenv: 0.3-4, absent means the runtime default',()=>{
 assert.equal(caseLensEnvIntensity({id:'invu'}),undefined);
 assert.equal(caseLensEnvIntensity({id:'invu',lens_env_intensity:null}),undefined);
 for(const value of [.3,1,1.23,1.49,4])assert.equal(caseLensEnvIntensity({id:'invu',lens_env_intensity:value}),value);
 for(const value of [.29,4.01,0,-1,Number.NaN,Infinity,'1.49',true])assert.throws(()=>caseLensEnvIntensity({id:'invu',lens_env_intensity:value}),/0\.3-4/,String(value));
});

test('the AR page registers each case with its lensenv and reports it',async()=>{
 const page=await fs.readFile(new URL('./provider-comparison-ar.html',import.meta.url),'utf8');
 assert.ok(page.includes('const lensEnv=new Map(manifest.cases.map(entry=>[entry.id,caseLensEnvIntensity(entry)]));'),'validated before any case loads');
 assert.ok(page.includes('registerModelingAutoEyewear({name:entry.id,assetUrl:url,widthMm:entry.width_mm??145,templeClipLocalZM:-.14,...(lensEnvIntensity===undefined?{}:{lensEnvIntensity})});'));
 assert.ok(page.includes('lens_env_intensity:lensEnv.get(entry.id)??null'),'the report row records what the lenses were drawn with');
 assert.ok(page.indexOf('const lensEnv=')<page.indexOf('for(const entry of manifest.cases){'));
});
