/** GPU regression scene, generated test geometry only: isolate each authored material control against black. */
import {DataTexture, Group, Mesh, MeshPhysicalMaterial, NearestFilter, NoToneMapping, OrthographicCamera,
  PlaneGeometry, Scene, SRGBColorSpace, WebGLRenderer} from 'three';
import {classifyAssetMaterials} from '../src/eyewear/optical-material.ts';
import {createCameraTransmissionTwin, createCameraTransmissionUniforms, setCameraTransmissionSource} from '../src/render/translucent-twin.ts';

function probe(kind: 'transmission' | 'thickness' | 'opaque' | 'alpha'): number[] {
  const renderer = new WebGLRenderer({antialias: false, alpha: false, preserveDrawingBuffer: true});
  renderer.setSize(64, 32); renderer.setClearColor(0x000000); renderer.toneMapping = NoToneMapping;
  renderer.outputColorSpace = SRGBColorSpace;
  const camera = new OrthographicCamera(-1, 1, .5, -.5, .1, 10); camera.position.z = 1;
  const source = new DataTexture(new Uint8Array([255, 255, 255, 255]), 1, 1); source.needsUpdate = true;
  const map = new DataTexture(new Uint8Array([0, 0, 255, 255, 255, 255, 0, 255]), 2, 1);
  map.minFilter = NearestFilter; map.magFilter = NearestFilter; map.channel = 1; map.needsUpdate = true;
  const material = new MeshPhysicalMaterial({transmission: 1, roughness: 1, metalness: 0, color: 0xffffff,
    transparent: kind === 'alpha', opacity: kind === 'alpha' ? .4 : 1});
  if (kind === 'transmission') material.transmissionMap = map;
  if (kind === 'thickness') {
    material.thicknessMap = map; material.thickness = .005; material.attenuationDistance = .005;
    material.attenuationColor.setRGB(.25, .25, .25);
  }
  const geometry = new PlaneGeometry(2, 1); geometry.setAttribute('uv1', geometry.getAttribute('uv').clone());
  const mesh = new Mesh(geometry, material); mesh.userData.partRole = 'frame';
  const root = new Group().add(mesh); classifyAssetMaterials(root);
  const uniforms = createCameraTransmissionUniforms(); setCameraTransmissionSource(uniforms, source, 64, 32);
  const twin = createCameraTransmissionTwin(material, uniforms); twin.toneMapped = false; mesh.material = twin;
  try {
    renderer.render(new Scene().add(root), camera);
    const gl = renderer.getContext(), pixels = new Uint8Array(64 * 32 * 4);
    gl.readPixels(0, 0, 64, 32, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    return [pixels[(16 * 64 + 16) * 4]!, pixels[(16 * 64 + 48) * 4]!];
  } finally {
    geometry.dispose(); material.dispose(); twin.dispose(); source.dispose(); map.dispose(); renderer.dispose(); renderer.forceContextLoss();
  }
}

(globalThis as unknown as {twinMapProof: () => unknown}).twinMapProof = () =>
  Object.fromEntries((['transmission', 'thickness', 'opaque', 'alpha'] as const).map(kind => [kind, probe(kind)]));
