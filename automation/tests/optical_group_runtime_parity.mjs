// Shared numeric payloads supplied by Python; production AR source is read only.
import fs from 'node:fs';
import {BufferGeometry, Float32BufferAttribute, Mesh, MeshPhysicalMaterial} from '../../ar/node_modules/three/build/three.module.js';
import {validateEffectiveOpticalGroups} from '../../ar/src/render/effective-optical-topology.ts';
import {LENS_APPEARANCE_EXTENSION} from '../../ar/src/eyewear/lens-appearance.ts';
const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const results = cases.map(row => {
  let part = 0;
  const meshes = row.groups.flatMap(group => group.primitives.map(member => {
    const geometry = new BufferGeometry();
    for (const [key, name, width] of [['positions','position',3],['normals','normal',3],['uv','uv',2]])
      geometry.setAttribute(name, new Float32BufferAttribute(member[key].flat(), width));
    geometry.setIndex(member.indices.flat());
    const material = new MeshPhysicalMaterial();
    material.userData.gltfExtensions = {[LENS_APPEARANCE_EXTENSION]: {schema_version:1,texcoord:0,appearance:{
      schema_version:1,color_space:'scene_linear_srgb_D65',density_interpolation:'piecewise_smoothstep_optical_density',
      vertical_coordinate:'lens_local_bottom_0_top_1',normal_reflectance_rgb:[1,1,1],refractive_index:1.5,roughness:0,
      optical_density_keyframes:[{v:0,optical_density_rgb:[0,0,0]}],angular_reflectance_keyframes:null}}};
    const mesh = new Mesh(geometry, material);
    mesh.userData={partRole:'lens',lensSurfaceProfile:'effective_optical_group_v1_experiment',
      lensUVConvention:'lens_local_bottom_0_top_1',opticalGroupId:group.group_id,opticalGroupMemberId:member.id,
      opticalSourcePartIndex:part++,opticalSourceSha256:'b'.repeat(64),lensAppearanceSha256:group.appearance_sha256,
      semanticIdentity:'unverified',materialIdentification:'unmeasured'};
    return mesh;
  }));
  try {validateEffectiveOpticalGroups(meshes); return {id:row.id,compatible:true};}
  catch (error) {return {id:row.id,compatible:false,error:String(error)};}
  finally {for(const mesh of meshes){mesh.geometry.dispose();mesh.material.dispose();}}
});
console.log(JSON.stringify(results));
