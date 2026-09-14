import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

export class ModelViewer {
  private renderer: THREE.WebGLRenderer | null = null;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(35, 1, 0.001, 1000);
  private controls: OrbitControls | null = null;
  private observer: ResizeObserver | null = null;
  private model: THREE.Object3D | null = null;
  private frame = 0;
  private generation = 0;
  private loaded = '';
  private initialized = false;
  private disposed = false;
  constructor(private host: HTMLElement, private status: HTMLElement) {}
  private initialize(): void {
    if (this.initialized || this.disposed) return;
    this.initialized = true;
    const { host, status } = this;
    try {
      const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: false });
      this.renderer = renderer;
      renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.1;
      renderer.domElement.setAttribute('aria-label', 'Interactive model: drag to rotate, scroll to zoom');
      renderer.domElement.setAttribute('role', 'img');
      host.prepend(renderer.domElement);
      this.controls = new OrbitControls(this.camera, renderer.domElement);
      this.controls.enableDamping = true;
      this.controls.dampingFactor = 0.08;
      this.scene.add(new THREE.HemisphereLight(0xf3f7ff, 0x74715c, 2.5));
      for (const [x, y, z, intensity] of [[3, 5, 4, 4], [-4, 2, -2, 2], [0, -2, 4, 1]]) {
        const light = new THREE.DirectionalLight(0xffffff, intensity!);
        light.position.set(x!, y!, z!);
        this.scene.add(light);
      }
      this.observer = new ResizeObserver(() => this.resize());
      this.observer.observe(host);
      this.resize();
      this.animate();
    } catch {
      cancelAnimationFrame(this.frame);
      this.observer?.disconnect();
      this.controls?.dispose();
      this.renderer?.dispose();
      this.renderer?.domElement.remove();
      this.renderer = null;
      status.textContent = 'Interactive preview is unavailable on this device. The rendered views below are still available.';
    }
  }
  private resize(): void {
    if (!this.renderer) return;
    const width = Math.max(1, this.host.clientWidth), height = Math.max(1, this.host.clientHeight);
    this.renderer.setSize(width, height);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }
  private animate = (): void => {
    this.frame = requestAnimationFrame(this.animate);
    this.controls?.update();
    this.renderer?.render(this.scene, this.camera);
  };
  async load(url: string | null): Promise<void> {
    if (this.disposed) return;
    if (url === this.loaded) return;
    this.loaded = url ?? '';
    const generation = ++this.generation;
    if (this.model) { this.scene.remove(this.model); this.disposeObject(this.model); this.model = null; }
    if (!url) { this.status.textContent = 'Your model will appear here after the first Blender revision.'; return; }
    this.initialize();
    if (!this.renderer) {
      this.status.textContent = 'Interactive preview is unavailable on this device. The rendered views below are still available.';
      return;
    }
    this.status.textContent = 'Loading saved model…';
    try {
      const gltf = await new GLTFLoader().loadAsync(url);
      if (generation !== this.generation) { this.disposeObject(gltf.scene); return; }
      this.model = gltf.scene;
      this.scene.add(gltf.scene);
      this.resetView();
      this.status.textContent = '';
    } catch {
      if (generation === this.generation) this.status.textContent = 'The interactive model could not load. Inspect the rendered views or reopen this job.';
    }
  }
  resetView(): void {
    if (!this.model || !this.controls) return;
    const box = new THREE.Box3().setFromObject(this.model);
    if (box.isEmpty()) return;
    const center = box.getCenter(new THREE.Vector3());
    const size = Math.max(box.getSize(new THREE.Vector3()).length(), 0.001);
    this.camera.near = size / 1000;
    this.camera.far = size * 100;
    this.camera.position.copy(center).add(new THREE.Vector3(size * 0.7, size * 0.35, size * 1.5));
    this.camera.updateProjectionMatrix();
    this.controls.target.copy(center);
    this.controls.minDistance = size * 0.1;
    this.controls.maxDistance = size * 10;
    this.controls.update();
  }
  private disposeObject(object: THREE.Object3D): void {
    const textures = new Set<THREE.Texture>(), materials = new Set<THREE.Material>();
    object.traverse(item => {
      if (!(item instanceof THREE.Mesh)) return;
      item.geometry.dispose();
      for (const material of Array.isArray(item.material) ? item.material : [item.material]) {
        materials.add(material);
        for (const value of Object.values(material)) if (value instanceof THREE.Texture) textures.add(value);
      }
    });
    textures.forEach(texture => texture.dispose());
    materials.forEach(material => material.dispose());
  }
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    ++this.generation;
    cancelAnimationFrame(this.frame);
    this.observer?.disconnect();
    this.controls?.dispose();
    if (this.model) this.disposeObject(this.model);
    this.renderer?.dispose();
  }
}
