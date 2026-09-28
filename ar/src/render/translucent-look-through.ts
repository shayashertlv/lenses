/** The look-through image of translucent (crystal) frame materials: what a crystal front or arm shows behind itself.
 *
 *  Three's physical transmission refracts through its own pre-pass: a render of the opaque scene into an internal,
 *  mip-mapped target, sampled with a thickness offset and a roughness/IOR level-of-detail blur. For a crystal arm that
 *  hid the hardware inside it twice over: the arm exclusion (temple-visibility.ts) kept the arms out of that pre-pass
 *  as if a lens read it, and the near-arm overlays write no colour there while the head proxy writes depth, so a core
 *  relieved in front of the head was missing; where it survived, the blur smeared a 0.6 mm wire into a wide band.
 *
 *  With canonical lenses the renderer owns this image instead. Once per frame, after the canonical lens input and before
 *  the canvas passes, the eyewear scene is rendered into a multisampled linear half-float target: the camera background
 *  (or its shadowed composite), the head occluders' depth, the opaque eyewear with the arms (no lens-input exclusion:
 *  this is not lens input) and the near-arm overlays in colour, so relieved hardware is in it too (at full relief up to
 *  the drop distance: the crystal overlay sampling it applies the relief coverage once). Opaque materials get
 *  the display response they get on the canvas (opaque-display.ts, the capture the canonical lens input uses): the twin
 *  is not tone-mapped, so an HDR core would otherwise show saturated and bleed wider than itself. The lenses and every
 *  material that samples the image (the crystal twins, translucent-twin.ts) are hidden by material, so descendants and
 *  sibling primitives keep drawing. The twins replace the crystal in every pass and, in the canvas passes, sample the
 *  image at the fragment's own pixel with the identity UV: no refraction offset, no LOD blur, the volume absorption
 *  applied as before. The canonical lens input is the exception: it excludes the arms, and this image has them, so a
 *  crystal part drawn there (a far rim or bridge seen through a lens) samples the background instead, and the far arm's
 *  hardware never crosses the near lens (renderer.ts). Nothing drawn then transmits, so Three runs no pre-pass of its
 *  own: the owned pass replaces it rather than adding to it.
 *
 *  Legacy assets (a transmissive lens without a canonical descriptor) keep Three's transmission untouched; they carry no
 *  translucent frame material (classification needs canonical optics) and never get this image. */
import {HalfFloatType, LinearSRGBColorSpace, Mesh, MeshPhysicalMaterial, NearestFilter, Vector4, Color, WebGLRenderTarget} from 'three';
import type {Camera, Material, Object3D, Scene, Texture, WebGLRenderer} from 'three';
import {isOpticalMaterial} from '../eyewear/optical-material.ts';
import {readMaterialLensAppearance} from '../eyewear/lens-appearance.ts';
import {OpaqueDisplayCapture} from './opaque-display.ts';

/** Whether any optical material under `root` is a legacy transmissive lens: optical, no canonical descriptor, and still
 *  transmitting. Such a lens samples Three's internal pre-pass, which then stays lens input. Canonical lenses carry
 *  their descriptor whatever their current transmission (1 at install, 0 once their layers own the transport). */
export function hasLegacyTransmissiveOptics(root: Object3D): boolean {
  let legacy = false;
  root.traverse(object => {
    if (legacy || !(object instanceof Mesh)) return;
    for (const material of (Array.isArray(object.material) ? object.material : [object.material]) as Material[]) {
      if (isOpticalMaterial(material) && readMaterialLensAppearance(material) === null
        && material instanceof MeshPhysicalMaterial && material.transmission > 0) legacy = true;
    }
  });
  return legacy;
}

export class TranslucentLookThrough {
  /** Multisampled like Three's own transmission target (a sub-pixel wire is antialiased, not dropped), resolved into a
   *  single-level texture sampled at pixel centres. */
  readonly target = new WebGLRenderTarget(1, 1, {type: HalfFloatType, samples: 4, depthBuffer: true, stencilBuffer: false,
    generateMipmaps: false, minFilter: NearestFilter, magFilter: NearestFilter, colorSpace: LinearSRGBColorSpace});
  /** The opaque display capture of the eyewear scene. A material has one capture owner, so the canonical lens layers
   *  share this one (lens-layers.ts); it is disposed with the image. */
  readonly display = new OpaqueDisplayCapture();
  private disposed = false;

  get texture(): Texture {return this.target.texture;}

  /** Render `scene` as `camera` sees it into the image, `hidden` materials off, the renderer's state restored. Returns
   *  the image. The scene's background, stencil and hair settings are the caller's; `withInput` wraps the draw (the
   *  near-arm overlays' look-through relief, temple-visibility.ts). */
  render(renderer: WebGLRenderer, scene: Scene, camera: Camera, width: number, height: number, hidden: Iterable<Material>,
    withInput: (draw: () => void) => void = draw => draw()): Texture {
    if (this.disposed) throw new Error('The translucent look-through image is disposed.');
    if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0) {
      throw new Error('The translucent look-through image needs a positive integer size.');
    }
    if (this.target.width !== width || this.target.height !== height) this.target.setSize(width, height);
    this.display.prepare(scene);
    const previous = {
      target: renderer.getRenderTarget(), face: renderer.getActiveCubeFace(), mip: renderer.getActiveMipmapLevel(),
      viewport: renderer.getViewport(new Vector4()), scissor: renderer.getScissor(new Vector4()), scissorTest: renderer.getScissorTest(),
      clear: renderer.getClearColor(new Color()), alpha: renderer.getClearAlpha(), autoClear: renderer.autoClear,
    };
    const visibility = new Map<Material, boolean>();
    try {
      for (const material of hidden) if (!visibility.has(material)) {visibility.set(material, material.visible); material.visible = false;}
      renderer.autoClear = false; renderer.setScissorTest(false);
      renderer.setRenderTarget(this.target); renderer.setViewport(0, 0, width, height);
      renderer.setClearColor(0, 1); renderer.clear(true, true, false);
      withInput(() => this.display.withCapture(renderer, () => renderer.render(scene, camera)));
    } finally {
      for (const [material, visible] of visibility) material.visible = visible;
      renderer.setRenderTarget(previous.target, previous.face, previous.mip);
      renderer.setViewport(previous.viewport); renderer.setScissor(previous.scissor); renderer.setScissorTest(previous.scissorTest);
      renderer.setClearColor(previous.clear, previous.alpha); renderer.autoClear = previous.autoClear;
    }
    return this.target.texture;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true; this.target.dispose(); this.display.dispose();
  }
}
