/** Deterministic local QA lighting. No asset material coefficients are fitted here. */
import * as THREE from 'three';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import {createSeeThroughRoom} from '../src/render/eyewear-reflection.ts';
import {isTranslucentFrameMaterial} from '../src/eyewear/optical-material.ts';

export function lightingConfigurations(manifest) {
 const explicit=manifest.environments!==undefined;
 const configurations=manifest.environments??[{id:'room',preset:'room',intensity:.8}];
 if(!Array.isArray(configurations)||!configurations.length)throw Error('environments must be a nonempty array');
 const seen=new Set();
 return configurations.map(value=>{
  const {id,preset,intensity=.8}=value;
  if(!/^[a-z0-9][a-z0-9_-]{0,39}$/.test(id)||seen.has(id))throw Error('Unique safe environment IDs required');
  if(!['room','broad_studio','side_studio'].includes(preset))throw Error('Unknown environment preset '+preset);
  if(!Number.isFinite(intensity)||intensity<=0||intensity>4)throw Error('Environment intensity must be >0 and <=4');
  seen.add(id);
  return {id,preset,intensity,explicit,regular_pmrem_blur:.04,canonical_pmrem_blur:0,
   description:preset==='room'?'Existing Three.js RoomEnvironment':
    `Neutral analytic ${preset==='broad_studio'?'overhead':'side'} broad illumination; smooth nonzero directional reflections`,
   canonical_description:preset==='room'?'Canonical lenses and translucent (crystal) frame materials reflect the runtime\'s see-through room: the RoomEnvironment without its +Z front panel (src/render/eyewear-reflection.ts)':
    'Canonical lenses and translucent (crystal) frame materials reflect the same analytic map, unblurred',
   direct_light:'Unchanged white directional key, intensity 2 at [-10,15,20]',
   material_coefficients:'Unchanged; environment map and illumination multiplier only'};
 });
}

/** A case's lensenv, the canonical lens reflection's multiplier the owner's try-on link carries (?lensenv=, src/eyewear/
 *  external.ts, 0.3-4; automation's tryon.py writes the pipeline's lens_env_intensity_recommended there). Review AR-R2
 *  (2026-09-30): the harness registered every case without it, so no harness run ever drew a mirror lens at the lensenv
 *  the owner accepted it with (invu-astra2 1.49, oakley-astra2 1.23). undefined leaves the runtime default (1). The link
 *  ignores an unusable value; the harness refuses it instead of silently drawing another setting than the manifest asks. */
export function caseLensEnvIntensity(entry){
 const value=entry?.lens_env_intensity;
 if(value===undefined||value===null)return undefined;
 if(typeof value!=='number'||!Number.isFinite(value)||value<.3||value>4)throw Error(`lens_env_intensity of case ${entry.id} must be a finite number within 0.3-4 (the try-on link's lensenv range)`);
 return value;
}

function studioTexture(preset){
 const width=256,height=128,data=new Float32Array(width*height*4);
 const key=new THREE.Vector3(...(preset==='broad_studio'?[.2,.9,.45]:[-.85,.3,.45])).normalize();
 const fill=new THREE.Vector3(.75,.2,-.6).normalize();
 for(let y=0;y<height;y++)for(let x=0;x<width;x++){
  const phi=(x+.5)/width*Math.PI*2,theta=(y+.5)/height*Math.PI;
  const direction=new THREE.Vector3(-Math.sin(theta)*Math.cos(phi),Math.cos(theta),Math.sin(theta)*Math.sin(phi));
  const radiance=.10+1.8*Math.exp((direction.dot(key)-1)/.22)+.55*Math.exp((direction.dot(fill)-1)/.38);
  const offset=(y*width+x)*4;data[offset]=data[offset+1]=data[offset+2]=radiance;data[offset+3]=1;
 }
 const texture=new THREE.DataTexture(data,width,height,THREE.RGBAFormat,THREE.FloatType);
 texture.mapping=THREE.EquirectangularReflectionMapping;texture.colorSpace=THREE.LinearSRGBColorSpace;texture.needsUpdate=true;
 return texture;
}

/** `regular` is what scene.environment gets (blur 0.04), `canonical` what canonical lenses and crystal reflect (blur 0).
 *  For 'room' they are the runtime's two rooms (renderer.ts configure): the scene keeps three's RoomEnvironment, panel
 *  included, while lenses and crystal reflect the see-through room without the +Z panel behind the selfie camera. Review
 *  AR-R4 (2026-09-30): both were built from one full room, so an explicit 'room' environment drew the front panel on the
 *  canonical lenses again (the white rectangles the runtime no longer draws; the shader limit kept them at a pale sRGB
 *  243 plateau instead of 255). */
export function createLighting(renderer,configuration){
 const generator=new THREE.PMREMGenerator(renderer);
 let source,seeThrough,texture,regular,canonical;
 try{
  if(configuration.preset==='room'){source=new RoomEnvironment();seeThrough=createSeeThroughRoom();}
  else{source=new THREE.Scene();texture=studioTexture(configuration.preset);source.background=texture;}
  regular=generator.fromScene(source,.04);canonical=generator.fromScene(seeThrough??source,0);
  return {configuration,regular,canonical,dispose(){regular.dispose();canonical.dispose();}};
 }catch(error){regular?.dispose();canonical?.dispose();throw error;}
 finally{generator.dispose();source?.dispose?.();seeThrough?.dispose();texture?.dispose();}
}

/** The materials of a mesh-material slot (a mesh can carry an array). */
const slot=material=>Array.isArray(material)?material:[material];

/** Same QA seam as canonical-lens-runtime.html. Runtime has no public light injection API.
 * Save and restore every touched field; material optical coefficients and model bytes stay intact.
 * Crystal (review AR-R4): translucent frame materials own the see-through room at the scene intensity (renderer.ts), and
 * their camera-transmission twins, which draw every crystal pixel, copy envMap when they are cloned at the first render.
 * Relighting only the lenses left the crystal on the runtime room while scene.environment switched, so the report's
 * 'environment map and illumination multiplier only' was false for crystal. The originals (near-arm overlay clones
 * included) and every twin that exists get the explicit canonical map; a twin cloned while this lighting is applied
 * copies it from its original, and the undo returns it to its original's restored map. */
export function applyRuntimeLighting(instance,lighting){
 if(!instance.scene?.isScene||!instance.renderer?.isWebGLRenderer||!Array.isArray(instance.lensMeshes)
  ||!Array.isArray(instance.translucentFrameMeshes)||!(instance.translucentTwins instanceof Map))throw Error('Runtime QA lighting seam changed');
 const scene=instance.scene,previousEnvironment=scene.environment,previousIntensity=scene.environmentIntensity;
 const canonical=new Set(instance.canonicalLensMaterials??[]),materials=new Set(),crystal=new Set();
 for(const mesh of instance.lensMeshes)for(const material of slot(mesh.material))if(material.isMeshPhysicalMaterial)materials.add(material);
 // The runtime's filter (renderer.ts): a mesh's slot can mix crystal with gold or acetate, which keep the scene room.
 for(const {original}of instance.translucentFrameMeshes)for(const material of slot(original))if(material&&isTranslucentFrameMaterial(material))crystal.add(material);
 for(const twin of instance.translucentTwins.values())crystal.add(twin);
 for(const material of crystal)materials.add(material);
 const previous=[...materials].map(material=>({material,map:material.envMap,intensity:material.envMapIntensity}));
 scene.environment=lighting.regular.texture;scene.environmentIntensity=lighting.configuration.intensity;
 for(const record of previous){
  const {material,map,intensity}=record;
  // Crystal reflects the see-through room at the scene intensity (lensenv is a lens setting).
  if(crystal.has(material)){material.envMap=lighting.canonical.texture;material.envMapIntensity=lighting.configuration.intensity;material.needsUpdate=true;continue;}
  // An explicit map carries the runtime's optional lens multiplier; inherited maps use scene intensity.
  if(map||canonical.has(material)){
   material.envMap=(canonical.has(material)?lighting.canonical:lighting.regular).texture;
   material.envMapIntensity=lighting.configuration.intensity*(map?intensity/.8:1);material.needsUpdate=true;
  }
 }
 const touched=new Set(materials);
 return ()=>{scene.environment=previousEnvironment;scene.environmentIntensity=previousIntensity;
  for(const {material,map,intensity}of previous){material.envMap=map;material.envMapIntensity=intensity;material.needsUpdate=true;}
  for(const [original,twin]of instance.translucentTwins)if(!touched.has(twin)){twin.envMap=original.envMap;twin.envMapIntensity=original.envMapIntensity;twin.needsUpdate=true;}};
}

export function environmentSuffix(configuration){return configuration.explicit?`__env-${configuration.id}`:'';}
