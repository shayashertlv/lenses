/** Deterministic local QA lighting. No asset material coefficients are fitted here. */
import * as THREE from 'three';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';

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
   direct_light:'Unchanged white directional key, intensity 2 at [-10,15,20]',
   material_coefficients:'Unchanged; environment map and illumination multiplier only'};
 });
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

export function createLighting(renderer,configuration){
 const generator=new THREE.PMREMGenerator(renderer);
 let source,texture,regular,canonical;
 try{
  if(configuration.preset==='room')source=new RoomEnvironment();
  else{source=new THREE.Scene();texture=studioTexture(configuration.preset);source.background=texture;}
  regular=generator.fromScene(source,.04);canonical=generator.fromScene(source,0);
  return {configuration,regular,canonical,dispose(){regular.dispose();canonical.dispose();}};
 }catch(error){regular?.dispose();canonical?.dispose();throw error;}
 finally{generator.dispose();source?.dispose?.();texture?.dispose();}
}

/** Same QA seam as canonical-lens-runtime.html. Runtime has no public light injection API.
 * Save and restore every touched field; material optical coefficients and model bytes stay intact. */
export function applyRuntimeLighting(instance,lighting){
 if(!instance.scene?.isScene||!instance.renderer?.isWebGLRenderer||!Array.isArray(instance.lensMeshes))throw Error('Runtime QA lighting seam changed');
 const scene=instance.scene,previousEnvironment=scene.environment,previousIntensity=scene.environmentIntensity;
 const canonical=new Set(instance.canonicalLensMaterials??[]),materials=new Set();
 for(const mesh of instance.lensMeshes)for(const material of Array.isArray(mesh.material)?mesh.material:[mesh.material])if(material.isMeshPhysicalMaterial)materials.add(material);
 const previous=[...materials].map(material=>({material,map:material.envMap,intensity:material.envMapIntensity}));
 scene.environment=lighting.regular.texture;scene.environmentIntensity=lighting.configuration.intensity;
 for(const record of previous){
  const {material,map,intensity}=record;
  // An explicit map carries the runtime's optional lens multiplier; inherited maps use scene intensity.
  if(map||canonical.has(material)){
   material.envMap=(canonical.has(material)?lighting.canonical:lighting.regular).texture;
   material.envMapIntensity=lighting.configuration.intensity*(map?intensity/.8:1);material.needsUpdate=true;
  }
 }
 return ()=>{scene.environment=previousEnvironment;scene.environmentIntensity=previousIntensity;
  for(const {material,map,intensity}of previous){material.envMap=map;material.envMapIntensity=intensity;material.needsUpdate=true;}};
}

export function environmentSuffix(configuration){return configuration.explicit?`__env-${configuration.id}`:'';}
