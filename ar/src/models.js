/**
 * The frames available to try on, and how to load them.
 *
 * Two things vary between eyewear assets and both have to be handled here rather
 * than assumed:
 *
 * **Format.** glTF is the commerce standard, but supplier and scan pipelines emit
 * OBJ constantly. Both are loaded; the rest of the app never learns which.
 *
 * **Scale.** glTF declares metres, so a well-authored `.glb` arrives life-sized.
 * OBJ declares nothing — an exporter's units are whatever the modeller was working
 * in — so an asset can land at any size at all. Rather than eyeball a fudge factor,
 * each entry states the frame's real width in millimetres and the loader scales the
 * geometry to match. That is a number a retailer already has: it is printed on the
 * temple arm of every pair they sell.
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OBJLoader } from 'three/addons/loaders/OBJLoader.js';

/**
 * Crystal acetate, as a surface.
 *
 * Shared by every frame in that material rather than copied, because the numbers
 * below were arrived at against a face and a copy is a copy that drifts.
 *
 * Crystal has to be see-through *and* visible, and the two pull against each other.
 * Both failure modes have been shipped here: at roughness 0.55 the frame blurred the
 * whole scene behind it into a flat milky tone and read as solid plastic; at
 * transmission 0.85 it refracted so cleanly through such thin geometry that it
 * disappeared, leaving only its shadow and its metal trim on the face.
 *
 * Backing the transmission off to 0.55 was the wrong lever, and it is worth saying
 * why, because the number looks conservative and was not. Transmitted light is
 * multiplied by the material's base colour before it reaches the eye, so while the
 * scan's baked albedo was still attached, transmission bought far less than it
 * claimed: measured on a face, the frame at 0.55 carried only a third of the
 * correlation with what was actually behind it, and the bridge — the thickest, most
 * shadowed patch of that albedo, over the flattest part of the face — read as a solid
 * grey slab. Raising the transmission alone made it *darker*, not clearer. `toCrystal`
 * drops that albedo now, which is what makes the number below mean what it says.
 *
 * What keeps it visible at 0.92 is not the transmission at all — it is everything
 * around it: 92% of a frame's own silhouette still reads differently from the face
 * behind it, because a clear frame is Fresnel edges, refraction offset and a polished
 * highlight, and `clearcoat` is what puts that highlight along the rim.
 */
/**
 * An anti-reflective coating, as a surface.
 *
 * `strength` scales the lens's dielectric F0 — bare glass reflects about 4% at normal
 * incidence and a coated lens roughly a quarter of that, which is the whole point of
 * the coating and the reason a lens is *supposed* to be hard to see. `tint` is the
 * residual the layers leave behind, and it is the giveaway a viewer actually reads:
 * coated lenses flash green, blue or yellow-green, never white.
 *
 * `sheen` is a second, far broader lobe standing in for the fact that the thing being
 * reflected is a screen — an area source a foot across — rather than a point.
 */
const COATING = {
  strength: 0.4,
  /**
   * Restrained on purpose. The residual an AR stack leaves really is green, but it is
   * a *cast*, not a colour — pushed hard it stops reading as a coating and starts
   * reading as a green lamp pointed at the wearer.
   */
  tint: [0.82, 1.0, 0.91],
  /**
   * The broad lobe, and the thing to keep small. It spreads across whatever it lands
   * on, so a large flat lens takes it as an even wash — on the navigator's 59 mm lenses
   * 0.35 read as frosted glass, where on a small curved lens the same number is a
   * highlight. Sized for the worst case, since a coated lens is *supposed* to be hard
   * to see.
   */
  sheen: 0.10,
  sheenRoughness: 0.22,
};

const CRYSTAL_ACETATE = {
  transmission: 0.92,
  ior: 1.52,
  thickness: 0.004,
  /**
   * Not a finish. On a transmissive material this number is the mip level three
   * samples the refracted image at — `lod = log2(bufferWidth) * roughness`, with the
   * ior term saturating at 1 for anything above 1.5 — so it blurs what you see
   * *through* the frame as well as what you see reflected in it. At 0.15 that was a
   * mip and a half of smear on a 1024-wide feed, and the result was frosted glass:
   * the wearer's eyebrow arrived behind the rim as a smudge. At 0.05 the individual
   * hairs come through.
   *
   * 0.05 rather than 0 because the shader floors it at 0.0525 and adds the surface's
   * own geometric roughness on top; anything lower is the same picture with a number
   * that lies about it.
   */
  roughness: 0.05,
  clearcoat: 0.6,
  clearcoatRoughness: 0.1,
};

export const MODELS = [
  {
    value: 'meshy',
    label: 'Acetate (AI scan)',
    url: '../assets/glasses/meshy-glasses.glb',
    // Image-to-3D output: arbitrary scale (1.88 units across), correctly oriented
    // (+Y up, lenses at +Z, arms to -Z — verified against the vertex data).
    realWidthMm: 140,
    // The exporter stamps metallicFactor 1.0 on everything, which would render
    // this cream acetate frame as dark brushed metal. The texture carries the
    // look; only the surface response needs correcting.
    pbr: { metalness: 0.08, roughness: 0.55 },
  },
  {
    value: 'aviator',
    label: 'Tortoiseshell aviator (AI scan)',
    url: '../assets/glasses/aviator-tortoiseshell.glb',
    /**
     * A Meshy image-to-3D capture, converted from a 206 MB `.blend` and arriving in
     * arbitrary units (1.897 across) the same way the other scan does.
     *
     * Three things were done to it on the way out of Blender, and the first is the
     * one that matters to the rest of this pipeline. **It is split into real parts.**
     * The capture is four loose shells — the frame front with the lenses welded into
     * it, the two temple arms, and the aviator's brow bar — so separating by loose
     * part and then by material gives `prepareTemples` an actual seam to hinge at
     * rather than a depth cut through geometry with no join in it. See the note there
     * on why a guessed pivot lands mid-arm and reads as a bent temple.
     *
     * The other two are budget. 1.95M triangles came down to 106k, and over half of
     * that saving was the *lenses*: two smooth curved shells that arrived carrying
     * 910k triangles between them, more than the entire rest of the model. Textures
     * went from an 8192 base and two 4096 maps to 2048/1024/1024. 206 MB to 3.3 MB,
     * which is the difference between a frame you can put in a catalogue and one you
     * cannot.
     *
     * No `orient`: Blender's -Y front and +Z up become glTF's +Z and +Y under the
     * exporter's own axis conversion, which is exactly the convention the rest of
     * this file expects — lenses at +Z, arms to -Z.
     */
    realWidthMm: 140,
    /**
     * Metalness only, and set to zero rather than nudged.
     *
     * The scan's packed ORM map means 0.23 in its metalness channel with no
     * `metallicFactor` to scale it, so a tortoiseshell acetate frame arrives 23%
     * metal — enough to read as dark and slightly wet rather than as polished horn.
     * Acetate is a dielectric; the honest correction is 0, not a smaller number.
     *
     * Roughness is deliberately left alone, which is the opposite of what the other
     * scan needed. This entry's map means 0.186, and since three multiplies factor by
     * map, leaving the factor at 1 gives an effective 0.186 — a polished acetate,
     * which is what tortoiseshell is. Overriding it here would *multiply* the map
     * rather than replace it and drive the frame toward a mirror.
     */
    pbr: { metalness: 0 },
    /**
     * No `crystal` key, deliberately: this frame is opaque and its materials are
     * already semantic. The lenses need no rescue either — Meshy authored them as
     * `Lens_Prescription_Glass` with a transmission of 1 and an index of refraction
     * of **1.586**, which is not a round number anybody guesses. It is the refractive
     * index of polycarbonate, the standard high-index ophthalmic lens material. The
     * exporter carried both across as `KHR_materials_transmission` and
     * `KHR_materials_ior`, so `prepareDeclaredGlass` finds them and applies the one
     * thing glTF cannot express: how that glass composites onto a photograph of a
     * face. See `matchTransmissiveRender`.
     */
  },
  {
    value: 'aviator-amber',
    label: 'Amber aviator (AI scan)',
    url: '../assets/glasses/aviator-amber.glb',
    /**
     * The cleanest asset in the catalogue, and the one that shows what the rest of
     * this file is compensating for. It arrives with the lenses *already their own
     * object* carrying `Lens_Prescription_Glass` — transmission 1, IOR 1.586,
     * roughness 0.02, base colour white — and a frame whose `Material_0` declares
     * metalness 0 and roughness 0.8 outright. So there is no `orient`, no `crystal`
     * rescue, and no `pbr` correction: nothing here needed a single number invented
     * for it.
     *
     * 1.32M triangles down to 106k, an 8192 base map to 2048, 131 MB to 2.8 MB.
     */
    realWidthMm: 140,
  },
  {
    value: 'horizon-amber',
    label: 'Amber Horizon (sunglasses, AI scan)',
    url: '../assets/glasses/horizon-amber.glb',
    /**
     * Sunglasses, and the first asset here whose lens is deliberately *tinted*.
     *
     * Meshy drove that tint from a procedural gradient — texture coordinate into a
     * Map Range into a colour ramp — and **glTF cannot carry a node graph**. The
     * exporter drops it silently and ships whatever constant is sitting in the socket
     * underneath, which is white: a graduated sunglass arriving as a clear Rx lens,
     * with nothing in the file to say it had ever been otherwise. The conversion
     * samples the ramp and writes its mean back as a flat `baseColorFactor`
     * (0.272, 0.155, 0.117) — a warm brown at 18% luminance, which is squarely in the
     * range a real sunglass transmits.
     *
     * Flat rather than graduated is a real loss and a small one: three multiplies
     * transmitted light by the base colour, so the lens still darkens and warms what
     * is behind it. What is gone is the top-to-bottom falloff.
     */
    realWidthMm: 140,
  },
  {
    value: 'horizon-sage',
    label: 'Sage Horizon (sunglasses, AI scan)',
    url: '../assets/glasses/horizon-sage.glb',
    /**
     * The same gradient lens as `horizon-amber`, on a frame that is itself
     * translucent — `Frame_Acetate_Translucent`, transmission driven by a texture at
     * about 0.52, IOR 1.49. That exports as a real `KHR_materials_transmission` with
     * a transmission *texture*, so `prepareDeclaredGlass` picks the frame up along
     * with the lenses and gives both the compositing treatment. Which is right: a
     * translucent acetate frame has exactly the same problem a lens does about
     * landing on top of a photograph.
     */
    realWidthMm: 140,
    /**
     * The frame's material ships a packed metallic-roughness texture and **no**
     * `metallicFactor`, which glTF defaults to 1.0 — so a translucent acetate frame
     * arrives 52% metal, dark and wet-looking. Acetate is a dielectric.
     *
     * Roughness is left alone for the same reason as the tortoiseshell: three
     * multiplies factor by map, so overriding it here could only drive the frame
     * glossier than the scan recorded, never rougher.
     */
    pbr: { metalness: 0 },
  },
  {
    value: 'shield-golden',
    label: 'Golden Shield (mirrored sunglasses, AI scan)',
    url: '../assets/glasses/shield-golden.glb',
    /**
     * A mirrored shield, and the first lens here that is *supposed* to reflect more
     * than it transmits.
     *
     * Meshy authored it as a **Mix Shader** — 72% of a gold metal (metallic 1.0, base
     * 0.9/0.66/0.255) over 28% of a transmissive amber lens — and glTF has one PBR
     * material per primitive and no mix node. The exporter cannot follow that link, so
     * what lands in the file is whatever it falls back to, silently and without
     * resembling the asset. The conversion collapses the blend by hand instead, onto
     * the two extensions that carry its two halves: what gets *through* becomes
     * `KHR_materials_transmission` at 0.28, tinted by the lens's own amber; what
     * bounces *off* becomes `KHR_materials_specular`'s colour, taken from the metal.
     *
     * Metallic stays 0 on purpose. A metal cannot transmit, so carrying the mix's 0.72
     * across would cancel the transmission the other half of the blend exists for. The
     * gold belongs in the specular colour, where it tints the reflection and leaves
     * what passes through alone.
     *
     * That gold is also why `matchTransmissiveRender` now checks whether a material
     * coloured its own reflection before fitting an anti-reflective coating over it.
     * An AR coat and a mirror coat are opposites — one suppresses reflection, the
     * other maximises it — and this frame is the one that says so.
     */
    realWidthMm: 140,
    /**
     * No `pbr` correction: every material here already declares `metallicFactor: 0`,
     * including the gold-*looking* frame, whose colour comes from the texture rather
     * than from a metalness channel. Zeroing it would have been a no-op, and assuming
     * it needed zeroing — as the tortoiseshell and sage frames genuinely did — would
     * have been the kind of guess this file exists to avoid.
     */
  },
  {
    value: 'crystal',
    label: 'Crystal acetate (parts scan)',
    url: '../assets/glasses/crystal-parts.glb',
    realWidthMm: 140,
    /**
     * Tripo exports this one rotated 42.7° off axis — a yaw of about 40° plus a
     * 5.3° roll. Measured from the asset's own geometry rather than eyeballed: the
     * two lens parts are symmetric, so the vector between their centroids *is* the
     * frame's width axis, and the vector from the temples' centroid to the lenses'
     * *is* its forward axis. Building an orthonormal basis from those two and
     * inverting it gives this quaternion; it takes the residual height difference
     * between the two lenses from 0.036 to 0.00000, which is the check that the
     * axes were right.
     */
    orient: [-0.126799, -0.341476, -0.003033, 0.931293],
    /**
     * The frame is crystal acetate — see `classifyScannedParts`. The exporter left
     * `KHR_materials_volume` in `extensionsUsed` and then declared it on no
     * material at all, so every part arrived fully opaque.
     */
    crystal: CRYSTAL_ACETATE,
  },
  {
    value: 'crystal-lenses',
    label: 'Crystal acetate with lenses (parts scan)',
    url: '../assets/glasses/glasses01-with-lenses.glb',
    realWidthMm: 140,
    /**
     * Same exporter and the same problem as `crystal`. The width axis is easy and
     * exact: the two lens meshes are symmetric, so the vector between their centroids
     * *is* it, and any basis built orthogonal to it takes the height difference
     * between them to 0.000000.
     *
     * Which way is **up** is the hard half, and getting it wrong is not subtle — it
     * rakes the frame front. Two plausible references both fail here. The model's own
     * +Y looks authoritative (every part sits on y=0, as though the exporter set it on
     * the ground) and leaves the front at a **20.7°** rake. The temples look like the
     * anchor a real frame gives you, since a worn temple runs level — but they hook
     * down at the tips, so their long axis is pitched, and aiming by it gives **24.7°**.
     * A frame front sitting at 20-25° reads as sharply wrong from any angle off-axis,
     * which is exactly how it was reported.
     *
     * What is left is the frame front itself, which is the flattest, most reliably
     * planar thing in the asset (its lens plane fits to 0.156 of its own spread) and
     * whose rake is a real, known property of a worn frame — pantoscopic tilt, 5-10°.
     * So up is chosen to put that plane at **7.2°**, measured off `crystal-parts.glb`:
     * the same frame, scanned by the same rig, so the number is this frame's own rather
     * than a textbook's.
     *
     * That is one constraint, and the things it does *not* constrain are the check.
     * Solving it alone brings the bounding box to 140.0 x 45.9 x 141.3 mm against the
     * sister scan's 140.0 x 46.9 x 141.2, and lands this quaternion within a degree of
     * that scan's own — which is what two captures on one rig should agree on, and did
     * not before.
     */
    orient: [-0.127356, -0.338112, -0.006472, 0.932426],
    /**
     * The frame parts arrive exactly as `crystal`'s do — three bright crystal parts,
     * three gold, two dark horn temples, none of them declaring transmission — so
     * they get the same treatment, from the same constants.
     *
     * The *lenses* are the difference, and they need none of it: this asset ships
     * them as their own meshes with `KHR_materials_transmission` actually set, so the
     * loader builds real glass and `classifyScannedParts` leaves it alone. What it
     * does not leave alone is how that glass composites — see `matchTransmissiveRender`.
     */
    crystal: CRYSTAL_ACETATE,
  },
  {
    value: 'navigator',
    label: 'Navigator (black acetate)',
    url: '../assets/glasses/navigator.glb',
    /**
     * The first asset here that was *authored* rather than scanned, and it shows in
     * everything this file usually has to correct. It arrives axis-aligned — width axis
     * exactly (1,0,0), lens plane normal exactly (0,0,1), flat to 0.075 of its own
     * spread — so there is no `orient`. It arrives in metres at life size, 147.5 mm
     * across with 140 mm temples, so there is no `realWidthMm` to normalise it by:
     * the frame is rendered at the size it was modelled, which is the only honest
     * input for true-size fitting. And its materials say what they mean, so there is
     * no `crystal` recovery either — `Lens_Gradient` declares its own transmission,
     * `Metal_Silver` its own metalness, `Acetate_Black` its own colour.
     *
     * One thing the export did drop: `Acetate_Black` carries a
     * `KHR_materials_clearcoat` with a roughness of 0.38 and no `clearcoatFactor`,
     * which defaults to zero — nobody sets a coat's roughness for a coat they do not
     * want, so the lacquer was meant to be there and did not survive. Left as
     * authored rather than guessed at; the frame reads as satin black instead of
     * polished, which is a legitimate finish and a one-line change in Blender if it
     * was not the intent.
     */
  },
  {
    value: 'base',
    label: 'Round metal (base.obj)',
    url: '../assets/glasses/base.obj',
    // Authored at arbitrary scale — 1.85 units across. Normalised to a standard
    // adult frame width. Change this to the real measurement of the actual product.
    realWidthMm: 140,
    material: 'metal',
  },
  {
    value: 'khronos',
    label: 'Sunglasses (Khronos)',
    url: '../assets/glasses/sunglasses-khronos.glb',
    // Authored in metres at life size, so nothing to correct.
  },
];

export const DEFAULT_MODEL = MODELS[0].value;

export function findModel(value) {
  return MODELS.find((m) => m.value === value) ?? MODELS[0];
}

/**
 * Loads one frame and returns its root object, already in metres.
 */
export async function loadGlassesModel(entry, baseUrl) {
  const url = new URL(entry.url, baseUrl).href;
  const isObj = /\.obj($|\?)/i.test(entry.url);

  const root = isObj
    ? await new OBJLoader().loadAsync(url)
    : (await new GLTFLoader().loadAsync(url)).scene;

  // OBJ carries no materials of its own unless an .mtl came with it, and the
  // loader's default is a flat grey that reads as plastic. Give it something that
  // at least behaves like eyewear under the environment map.
  if (isObj || entry.material) applyMaterial(root, entry.material ?? 'metal');

  // Corrects the surface response without touching the textures. Scan pipelines
  // routinely export metallicFactor 1.0 on everything, which turns a plastic
  // frame into dark metal; replacing the material outright would throw away the
  // texture that is the whole point of a scanned asset.
  if (entry.pbr) {
    root.traverse((node) => {
      if (!node.isMesh) return;
      for (const material of [].concat(node.material)) {
        if (!material) continue;
        if ('metalness' in entry.pbr) material.metalness = entry.pbr.metalness;
        if ('roughness' in entry.pbr) material.roughness = entry.pbr.roughness;
      }
    });
  }

  // Glass the asset declared for itself, on every model. This is not part of the
  // crystal recovery below and must not be: that one is a rescue for scans whose
  // materials arrived semantically blank, while this is the opposite case — an asset
  // that said what it meant, needing only the settings glTF has no way to express.
  // Run first, so the crystal conversion's own transmissive parts are not mistaken
  // for lenses afterwards.
  prepareDeclaredGlass(root);

  if (entry.crystal) classifyScannedParts(root, entry.crystal);

  // Before the width is measured, or the width would be measured across a frame
  // still lying at whatever angle the exporter left it at.
  if (entry.orient) {
    root.quaternion.premultiply(new THREE.Quaternion().fromArray(entry.orient));
    root.updateMatrixWorld(true);
  }

  if (entry.realWidthMm) normaliseWidth(root, entry.realWidthMm / 1000);

  return root;
}

// ---------------------------------------------------------------- scanned parts

/**
 * Above this metalness a part is metal, and metal cannot transmit light.
 * Measured on the sample: the gold trim reads 0.86-0.90, everything else <= 0.42.
 */
const METAL_THRESHOLD = 0.6;
/**
 * Below this base-colour luminance a part is dark, and dark acetate is opaque.
 * Measured: the crystal parts read 0.66-0.76, the horn temples 0.14-0.15.
 */
const CRYSTAL_LUMINANCE = 0.45;

const sampler = document.createElement('canvas');

/**
 * Boundary edges above this and a mesh is an open sheet, not a closed solid.
 *
 * A closed solid has zero edges used by only one triangle; a handful is scanner
 * slop; hundreds or thousands is a shell with a real open rim. Measured across the
 * catalogue after a position weld: the truly closed lenses (aviator-amber,
 * crystal-lenses, navigator, the khronos pads) count 0, the khronos lens sheets 128,
 * and the open scan shells 4,600–5,900.
 */
const OPEN_SHELL_EDGES = 32;

const shellCache = new WeakMap();

/**
 * Counts a geometry's boundary edges after welding coincident vertices.
 *
 * Welded first, because scan exporters routinely split one watertight surface into
 * primitives with duplicated seam vertices — topologically open, geometrically
 * closed — and the question here is geometric: can the camera see through this
 * mesh's back, or not.
 */
function countBoundaryEdges(geometry) {
  if (shellCache.has(geometry)) return shellCache.get(geometry);

  const position = geometry?.attributes?.position;
  if (!position) {
    shellCache.set(geometry, 0);
    return 0;
  }

  if (!geometry.boundingBox) geometry.computeBoundingBox();
  const size = geometry.boundingBox.getSize(new THREE.Vector3());
  const eps = Math.max(size.x, size.y, size.z, 1e-9) * 1e-5;

  // Weld: quantised position -> canonical vertex id.
  const canon = new Map();
  const remap = new Uint32Array(position.count);
  for (let i = 0; i < position.count; i++) {
    const key = `${Math.round(position.getX(i) / eps)},${Math.round(position.getY(i) / eps)},${Math.round(position.getZ(i) / eps)}`;
    let id = canon.get(key);
    if (id === undefined) {
      id = canon.size;
      canon.set(key, id);
    }
    remap[i] = id;
  }

  const index = geometry.index;
  const triCount = (index ? index.count : position.count) / 3 | 0;
  const edges = new Map();
  for (let t = 0; t < triCount; t++) {
    const a = remap[index ? index.getX(t * 3) : t * 3];
    const b = remap[index ? index.getX(t * 3 + 1) : t * 3 + 1];
    const c = remap[index ? index.getX(t * 3 + 2) : t * 3 + 2];
    if (a === b || b === c || c === a) continue;
    for (const [u, v] of [[a, b], [b, c], [c, a]]) {
      const key = u < v ? u * 4294967296 + v : v * 4294967296 + u;
      edges.set(key, (edges.get(key) ?? 0) + 1);
    }
  }

  let boundary = 0;
  for (const uses of edges.values()) if (uses === 1) boundary++;

  shellCache.set(geometry, boundary);
  return boundary;
}

/**
 * The mean colour of a texture, 0..1 per channel, or null if it cannot be read.
 *
 * A 32x32 downsample: the question is "what colour is this part, roughly", which
 * survives any amount of blurring, and a full-resolution readback of 24 textures
 * would not be free.
 */
function textureMean(texture, size = 32) {
  const image = texture?.image;
  if (!image || !(image.width > 0) || !(image.height > 0)) return null;
  try {
    sampler.width = size;
    sampler.height = size;
    const ctx = sampler.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(image, 0, 0, size, size);
    const { data } = ctx.getImageData(0, 0, size, size);
    let r = 0; let g = 0; let b = 0;
    for (let i = 0; i < data.length; i += 4) { r += data[i]; g += data[i + 1]; b += data[i + 2]; }
    const n = (data.length / 4) * 255;
    return [r / n, g / n, b / n];
  } catch {
    // A texture that cannot be read back leaves the part exactly as it arrived.
    return null;
  }
}

/**
 * Restores the material semantics a scan pipeline throws away.
 *
 * A parts-based scan arrives as N meshes with N identical-looking PBR materials
 * and no hint of which is glass, which is metal and which is plastic. This asset
 * is the extreme case: its exporter listed `KHR_materials_volume` among the
 * extensions it uses and then declared it on no material at all — so a crystal
 * acetate frame arrives 100% solid, with the *intent* to be transparent recorded
 * nowhere a renderer can act on.
 *
 * Something has to supply the missing information. The one honest source is the
 * asset itself: the textures it *did* ship. Metalness comes straight out of the
 * packed metallic-roughness map, and brightness out of the base colour — and those
 * two separate this frame's three materials cleanly (gold trim at 0.89 metalness,
 * horn temples at 0.15 luminance, crystal at 0.7). No part is guessed at by index
 * or by name, and a model that ships proper materials never reaches this code.
 */
function classifyScannedParts(root, options) {
  const rebuilt = new Map();
  const counts = {
    crystal: 0, glass: 0, metal: 0, opaque: 0, unreadable: 0,
  };

  root.traverse((node) => {
    if (!node.isMesh) return;
    const list = [].concat(node.material).map((material) => {
      if (!material) return material;
      if (rebuilt.has(material)) return rebuilt.get(material);

      const base = textureMean(material.map);
      // glTF packs roughness in green and metalness in blue of one texture, which
      // the loader hands to both slots.
      const packed = textureMean(material.metalnessMap ?? material.roughnessMap);
      let result = material;

      if (material.transmission > 0) {
        // Already glass, and said so. Nothing to infer — this is a part whose
        // exporter set `KHR_materials_transmission` rather than merely listing it,
        // which on this catalogue means the lenses. Its optical properties are the
        // asset's business; only how it composites is ours. Side-culling follows the
        // geometry it is drawn on, same as `prepareDeclaredGlass`.
        matchTransmissiveRender(material, {
          closed: countBoundaryEdges(node.geometry) <= OPEN_SHELL_EDGES,
        });
        counts.glass += 1;
      } else if (!base || !packed) {
        counts.unreadable += 1;
      } else if (packed[2] > METAL_THRESHOLD) {
        // Metal: already right. The absent metallicFactor defaults to 1 and the
        // map carries the rest, which is exactly what gold should do.
        counts.metal += 1;
      } else if (0.2126 * base[0] + 0.7152 * base[1] + 0.0722 * base[2] > CRYSTAL_LUMINANCE) {
        result = toCrystal(material, options, base);
        counts.crystal += 1;
      } else {
        counts.opaque += 1;
      }

      rebuilt.set(material, result);
      return result;
    });
    node.material = Array.isArray(node.material) ? list : list[0];
  });

  root.userData.scannedParts = counts;
  return counts;
}

/**
 * Makes an already-transmissive material composite against a camera feed.
 *
 * glTF describes what a material *is* — transmission, ior, thickness — and says
 * nothing about the two settings that decide whether it lands correctly on top of a
 * photograph. Both were learned the hard way on the crystal frame and both apply
 * verbatim to a lens, which is a larger, flatter, more transmissive piece of the same
 * problem sitting directly over an eye.
 *
 * `toneMapped` first, because it is the one that shows. The camera feed is
 * `scene.background` as an sRGB texture and three excludes those pixels from tone
 * mapping — they reach the screen as the camera left them. Light coming *through* the
 * lens is the same pixels: three fills the transmission buffer with tone mapping
 * forced off, the shader samples it, and ACES then lands on it on the way out. So the
 * eye behind the lens is graded twice and the eye beside it once, and the difference
 * falls exactly along the lens's rim. Nothing gives a composite away faster than an
 * edge where the photograph changes.
 *
 * `transparent` second: transmissive materials belong in the opaque pass, where three
 * gives them their dedicated buffer. Flagged transparent they sort with the
 * alpha-blended objects and the effect is simply lost.
 */
/**
 * Finds the parts an asset itself declared transmissive and gives them what glTF
 * cannot say.
 *
 * Every eyewear asset that ships real lenses lands here, whether or not it needs
 * anything else done to it: `KHR_materials_transmission` describes what the material
 * *is* and has no way to describe how it composites onto a photograph, which is the
 * half that decides whether it looks like a lens or a hole.
 */
function prepareDeclaredGlass(root) {
  // Two passes, because side-culling is a property of the GEOMETRY a material is
  // drawn on, not of the material — and a material shared between a closed lens and
  // an open trim shell has to be treated as open, or the trim holes out.
  const open = new Map();
  root.traverse((node) => {
    if (!node.isMesh) return;
    for (const material of [].concat(node.material)) {
      if (!material || !(material.transmission > 0)) continue;
      const isOpen = countBoundaryEdges(node.geometry) > OPEN_SHELL_EDGES;
      open.set(material, (open.get(material) ?? false) || isOpen);
    }
  });
  for (const [material, isOpen] of open) {
    matchTransmissiveRender(material, { closed: !isOpen });
  }
  root.userData.declaredGlass = open.size;
  return open.size;
}

function matchTransmissiveRender(material, { closed = true } = {}) {
  // Marked rather than inferred, because "transmissive" stops telling you where a
  // material came from the moment anything else sets transmission — and the frame's
  // crystal parts do exactly that a few lines later.
  material.userData.declaredGlass = true;
  // Recorded so anything downstream (the harness, a debug readout) can tell which
  // side decision this material was given and why, without re-deriving topology.
  material.userData.openShell = !closed;
  material.toneMapped = false;

  // Front faces only — but only when the mesh has a back to cull.
  //
  // Double-siding is cheap insurance everywhere else on a frame: eyewear is thin,
  // exported winding is unreliable, and a culled face leaves a hole. A transmissive
  // part that is a *closed solid* is the exception, because three draws the scene a
  // transmissive mesh refracts into a separate buffer first — and for a double-sided
  // one it draws that mesh's own *back* faces into it. The front of the lens then
  // refracts the inside of the lens, which is the milky look transmission exists to
  // remove. Measured on the frame parts, this was worth as much as raising the
  // transmission from 0.55 to 0.85.
  //
  // The premise "the lenses here are closed solids" turned out to be false for most
  // of the catalogue: audited from the files, five of the eight transmissive assets
  // ship OPEN sheets — the tortoiseshell aviator's lens counts 5,099 boundary edges,
  // the sage's entire translucent frame 5,855 — and an open sheet forced FrontSide
  // culls wherever its winding faces away. At yaw the culled set sweeps across the
  // part, so glass vanishes and reappears with every head turn, which on a try-on is
  // indistinguishable from broken occlusion. For those, coverage beats the milky
  // back-face refraction: they stay double-sided.
  material.side = closed ? THREE.FrontSide : THREE.DoubleSide;
  // ...but not for the shadow, which is a separate question and was silently getting
  // the wrong answer from the line above.
  //
  // three derives `shadowSide` from `side` when it is left null, and the mapping
  // inverts: a FrontSide material casts from its BACK faces. That is fine on a closed
  // solid and quietly destructive on a scanned frame, where the back faces are the
  // inside of a shell with whatever winding the scanner produced. Measured on two
  // frames of the same family and size, one opaque and one translucent acetate: 0.61%
  // of the portrait carried a contact shadow against **0.09%** — a 7x loss that reads
  // as the glasses floating rather than resting.
  //
  // Forcing FrontSide is about the *transmission pass* — stopping the mesh refracting
  // its own back faces — and it has no business changing what the frame casts. Naming
  // `shadowSide` explicitly separates the two.
  material.shadowSide = THREE.DoubleSide;
  material.transparent = false;
  material.depthWrite = true;

  // The anti-reflective coating, which is what you actually see when you see a lens —
  // and getting this wrong is what made the first attempt look like a camera flash.
  //
  // The physics is not "glass reflects 4%". Every modern spectacle lens carries a
  // multi-layer AR coating whose entire purpose is that it does *not* do that: the
  // layers pass nearly all the light and leave a **soft coloured residual** — green,
  // blue or yellow-green, depending on the stack. That tint is the single most
  // recognisable thing about a coated lens, and it is why an untinted white highlight
  // reads as fake however well it is placed: nobody's glasses reflect white.
  //
  // So the reflection is scaled down and tinted rather than left at bare-glass
  // strength. `specularColor` and `specularIntensity` are exactly the right controls —
  // they are the dielectric F0, which is what a coating modifies — and neither touches
  // what the lens transmits.
  //
  // Skipped entirely when the asset already coloured its own reflection. A mirrored
  // sunglass says gold, and an anti-reflective coating is the precise opposite of what
  // it is asking for: AR layers exist to *suppress* reflection, a mirror coat to
  // maximise it. Overwriting one with the other turns a gold mirror lens green, and
  // the material had said plainly what it wanted. A white specular colour is glTF's
  // default — i.e. "nothing stated" — which is the only case worth filling in.
  const statedItsOwn = material.specularColor
    && (material.specularColor.r < 0.99
      || material.specularColor.g < 0.99
      || material.specularColor.b < 0.99);
  if (material.specularIntensity === 1 && !material.specularColorMap && !statedItsOwn) {
    material.specularIntensity = COATING.strength;
    material.specularColor.setRGB(...COATING.tint, THREE.SRGBColorSpace);
  }

  // A second, much broader lobe for the sheen across the body of the lens. It has to
  // be `clearcoat` rather than roughness: on a transmissive material roughness is the
  // mip level three samples the refracted image at, so raising it would frost the eye
  // behind the lens — the trap documented in `toCrystal`. `clearcoatRoughness` is a
  // separate layer and leaves what you see *through* the lens sharp.
  //
  // Weak and wide. Strong and narrow was the flare.
  if (!material.clearcoat) {
    material.clearcoat = COATING.sheen;
    material.clearcoatRoughness = COATING.sheenRoughness;
  }

  material.needsUpdate = true;
  return material;
}

/**
 * Rebuilds one material as transmissive crystal.
 *
 * The conversion is the crux and it is easy to get silently wrong. Transmission
 * lives only on `MeshPhysicalMaterial`, and the loader produces a
 * `MeshStandardMaterial` for any asset without a physical glTF extension — which
 * is precisely the asset that needs this treatment. Setting `.transmission` on the
 * standard material would do nothing at all: the property is not in its shader.
 *
 * Copying is done through `MeshStandardMaterial.prototype.copy` rather than the
 * physical material's own, because that one reads `source.transmission`,
 * `source.ior` and friends — all `undefined` on a standard material — and would
 * poison the very fields being set here.
 *
 * `base` is the part's mean base colour, already measured by the caller — see the
 * note on `crystal.color` below for what it is used for and what is thrown away.
 */
function toCrystal(material, {
  transmission = 0.55, ior = 1.5, thickness = 0.004, roughness = 0.05,
  clearcoat = 0, clearcoatRoughness = 0.1,
}, base = null) {
  const crystal = new THREE.MeshPhysicalMaterial();

  // `copy` brings the shader defines with it, and the standard material's are not
  // the physical material's: it assigns `{ STANDARD: '' }` wholesale, dropping the
  // `PHYSICAL` flag this material was constructed with. The renderer then sees
  // `transmission > 0`, adds `USE_TRANSMISSION` to a shader with no PHYSICAL block
  // to define it against, and the program fails to compile — leaving the mesh
  // silently undrawn. It is a uniquely nasty failure: the material reports
  // transmission 0.55 when asked, the geometry submits its triangles, and the frame
  // is simply not on the screen. Any transmission at all triggers it and none is
  // fine, so it reads as "transparent" rather than as broken.
  const physicalDefines = crystal.defines;
  THREE.MeshStandardMaterial.prototype.copy.call(crystal, material);
  crystal.defines = physicalDefines;

  crystal.transmission = transmission;
  crystal.ior = ior;
  crystal.thickness = thickness;

  // The base colour goes, and this is the change that actually made the frame
  // transparent.
  //
  // Transmitted light does not arrive unfiltered — three multiplies it by the
  // material's own base colour on the way through, so whatever is in `.map` tints
  // and darkens everything seen through the part. That is right for tinted glass
  // and completely wrong for this map. A photogrammetry albedo of a *clear* object
  // is not an albedo at all: it is a photograph of whatever was behind the plastic
  // at scan time, with the rig's shading, contact shadows and ambient occlusion
  // baked into it. Left attached it acts as a dirty grey filter, and it is why the
  // bridge in particular read as a solid slab — being the deepest, most enclosed
  // part of the frame, it carries the darkest baked shading of the three (mean
  // luminance 0.66 against 0.75 for the rims) and it sits over the flattest,
  // lowest-contrast stretch of the face, where there is least structure to survive
  // the filtering. Measured on a face, dropping this map alone moves the frame from
  // 0.30 to 0.56 correlation with what is behind it; nothing else came close.
  //
  // What is kept is the *hue* the scan recorded, at full value: a clear material's
  // colour is its cast, not its brightness, and its brightness in the scan is a
  // record of the lighting rather than of the plastic. Normalising the measured
  // mean so its strongest channel is 1 keeps the one and discards the other.
  crystal.map = null;
  // Named colour space, because `setRGB` writes into the *working* (linear) one
  // unless told otherwise and these are sRGB-encoded byte averages read back off a
  // canvas. Unnamed, the cast lands about half as strong as it was measured — which
  // is invisible on this frame, whose crystal is neutral to within 2%, and wrong on
  // the smoke or honey version of the same asset.
  if (base) {
    const peak = Math.max(base[0], base[1], base[2], 1e-6);
    crystal.color.setRGB(base[0] / peak, base[1] / peak, base[2] / peak, THREE.SRGBColorSpace);
  } else {
    crystal.color.setRGB(1, 1, 1, THREE.SRGBColorSpace);
  }

  // The polished lacquer of an acetate rim. This is a lot of why crystal reads as
  // an object at all rather than as a hole: it is the highlight running along the
  // edge, and it survives however transparent the body underneath is.
  crystal.clearcoat = clearcoat;
  crystal.clearcoatRoughness = clearcoatRoughness;
  // Metal cannot transmit, and a scan's packed map reads ~0.4 metalness on clear
  // plastic — left alone it would cancel the transmission entirely.
  crystal.metalness = 0;
  crystal.metalnessMap = null;
  // The packed map's roughness channel is near-uniform on this asset, so it costs
  // nothing to drop and keeps the surface predictably polished. The *normal* map
  // is kept: that is where the real surface detail lives.
  crystal.roughnessMap = null;
  crystal.roughness = roughness;
  // Transmissive materials belong in the opaque pass — three renders them in a
  // dedicated pass that samples what is behind them. Flagging them `transparent`
  // instead would sort them with the alpha-blended objects and lose the effect.
  crystal.transparent = false;
  crystal.depthWrite = true;
  // Front faces only — the one place on this model where double-siding costs
  // something rather than insuring against something.
  //
  // Elsewhere it is cheap insurance: eyewear is thin, exported winding is
  // unreliable, and a culled face leaves a hole. A transmissive part is different,
  // because three has to draw the scene the part refracts into a separate buffer
  // first — and for a double-sided transmissive mesh it draws that mesh's own *back*
  // faces into it. The front of the bridge then refracts the inside of the bridge:
  // an opaque grey surface a few millimetres behind it, which is exactly the milky
  // look the transmission was meant to remove. These parts are watertight scanned
  // solids with consistent winding, so there is no hole to insure against. Measured,
  // this is worth as much as raising the transmission from 0.55 to 0.85.
  crystal.side = THREE.FrontSide;
  // The frame opts out of tone mapping, because the face beside it already has.
  //
  // The camera feed is `scene.background` as an sRGB texture, and three excludes any
  // sRGB background from tone mapping — those pixels reach the screen as the camera
  // left them. Light coming *through* the frame is the same pixels: three fills the
  // transmission buffer with tone mapping forced off, the shader samples it, and then
  // ACES lands on it on the way out. So one patch of skin was being graded twice, and
  // the difference fell exactly along the frame's silhouette. Nothing gives a
  // composite away faster than an edge where the photograph changes.
  //
  // The opaque parts keep ACES on purpose. They have nothing in the image to match,
  // so a filmic roll-off there is a look. A transmissive part has its reference one
  // pixel away.
  crystal.toneMapped = false;
  crystal.needsUpdate = true;
  return crystal;
}

/**
 * Rescales the model so it is `targetWidthM` across.
 *
 * Width is the one dimension of a frame that is both unambiguous and published, so
 * it is the right thing to key off. Applied to the root's transform rather than to
 * the vertices, so the source asset is left alone.
 */
function normaliseWidth(root, targetWidthM) {
  root.updateMatrixWorld(true);
  // `precise`: see the note in `analyseModel`. A rotated model measured loosely
  // comes out wider than it is, and would then be scaled down to compensate — the
  // parts scan would wear at 84mm instead of 140.
  const size = new THREE.Box3().setFromObject(root, true).getSize(new THREE.Vector3());
  if (!(size.x > 1e-9)) return;

  root.scale.multiplyScalar(targetWidthM / size.x);
  root.updateMatrixWorld(true);
}

const MATERIALS = {
  metal: () => new THREE.MeshPhysicalMaterial({
    color: 0x8d9196,
    metalness: 0.95,
    roughness: 0.28,
    envMapIntensity: 1.1,
  }),
};

function applyMaterial(root, kind) {
  const material = (MATERIALS[kind] ?? MATERIALS.metal)();
  root.traverse((node) => {
    if (!node.isMesh) return;
    node.material?.dispose?.();
    node.material = material;
    // Blender exports frequently arrive with inconsistent winding; eyewear is thin
    // and reads badly with half its faces culled.
    node.material.side = THREE.DoubleSide;
  });
}
