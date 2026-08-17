/**
 * Soft occlusion: fading the frame out where the head probably covers it.
 *
 * A depth test answers one question and it is the wrong one. It asks "is this fragment
 * behind the head", when everything upstream of it can only support "is this fragment
 * *probably* behind the head, to within about a millimetre". The occluder is a
 * deformed average head, its depth is borrowed or fitted rather than measured, the
 * landmarks that drive it carry their own noise, and the pose that carries it is
 * filtered. A binary answer to an uncertain question renders that uncertainty as a hard
 * edge in the wrong place — and, because the uncertainty moves frame to frame, as a
 * hard edge that crawls.
 *
 * That is worse at the nose than anywhere else, and for a reason worth writing down.
 * At bridge height the nasal sidewall's steepest fall is about 2:1, so below roughly
 * 27 degrees of yaw the nose has no true silhouette edge there at all — the boundary
 * between "frame in front" and "frame behind" is a shallow, near-tangential
 * intersection between two surfaces. A tangential intersection is precisely the
 * geometry that converts a small depth error into a large sideways movement of the
 * visible edge. Every capture that prompted this work is in that regime.
 *
 * So the head is rendered a second time into a depth texture, and each frame fragment
 * compares its own depth against it and fades:
 *
 *     onset = occluderDepth + FEATHER      (a hair in front of the drawn surface)
 *     alpha = 1 - smoothstep(0, FEATHER, onset - fragmentDepth)
 *
 * One-sided on purpose. A fragment in front of the occluder is fully drawn, so the
 * *onset* of occlusion is exactly where the occluder is and the boundary does not
 * shift; the fade runs backwards from there into the head. That bias is the right one:
 * of the two failure modes, a frame drawn slightly too far over skin reads as a depth
 * error, and a hole punched in a frame reads as a broken object.
 *
 * The occluder still writes depth in the main pass, at the far end of that same fade.
 * So the hard test and the soft fade agree exactly at the point they meet, and any
 * material that misses the injection below degrades to the old behaviour — occluded,
 * 1.2 mm late — rather than to a temple arm painted across a cheek.
 *
 * Resolved with an ordered dither and `discard` rather than with alpha blending,
 * because the lenses are transmissive and transmissive materials have to stay in the
 * opaque pass — that is where three gives them their dedicated buffer, and flagging
 * one transparent to get a blended edge would cost the refraction. The canvas already
 * runs MSAA, which resolves the stochastic coverage into a smooth gradient for free.
 */

import * as THREE from 'three';
import {
  OCCLUDER_LAYER, OCCLUDER_FEATHER, OCCLUDER_RELIEF, MAX_RELIEF_PX,
} from './occluder.js';

/** The pixel budget the fade is held to. The relief's budget lives with the relief. */
const MAX_FEATHER_PX = 3.0;

/**
 * The ordered dither, as the recursive 2x2 Bayer identity.
 *
 * Ordered rather than random, and this is not a style preference: a hash reseeded per
 * frame turns the fade band into television static on a head that is holding still,
 * which is far more visible than the hard edge it replaced. A fixed screen-space
 * pattern is stable under a stationary subject and only crawls as fast as the frame
 * itself moves. Eight levels deep gives 64 steps across a band that is a few pixels
 * wide, which is more than the eye resolves there.
 */
const DITHER = /* glsl */`
float occBayer2(vec2 a) { a = floor(a); return fract(a.x / 2.0 + a.y * a.y * 0.75); }
float occBayer4(vec2 a) { return occBayer2(0.5 * a) * 0.25 + occBayer2(a); }
float occBayer8(vec2 a) { return occBayer4(0.5 * a) * 0.25 + occBayer2(a); }
`;

const COMMON = /* glsl */`
varying vec4 vOcclusionClip;
uniform sampler2D tOccluderDepth;
uniform sampler2D tCameraFrame;
uniform vec2 uScreenTexel;
uniform float uOccluderNear;
uniform float uOccluderFar;
uniform float uOccluderFeather;
uniform float uOccluderOn;
uniform float uSnapStrength;
uniform float uSnapRadius;
uniform float uSnapEdgeMin;
uniform float uOccluderShow;
/**
 * The alpha the boundary decided, kept for the debug view at the end of main().
 * A global rather than a varying: it is written and read inside one fragment.
 */
float occDebugAlpha = 1.0;
${DITHER}
// three's own perspectiveDepthToViewZ. Inlined rather than pulled in through
// <packing>, which not every material chain includes, and under a name that cannot
// collide with one that does.
float occViewZ(float depth) {
  return (uOccluderNear * uOccluderFar)
       / ((uOccluderFar - uOccluderNear) * depth - uOccluderFar);
}
float occLuma(vec2 uv) {
  return dot(texture2D(tCameraFrame, uv).rgb, vec3(0.299, 0.587, 0.114));
}
`;

const MASK = /* glsl */`
  if (uOccluderOn > 0.5) {
    // From clip space rather than gl_FragCoord.xy, so this is correct at whatever
    // resolution the current target happens to be — three renders the opaque scene a
    // second time into the transmission buffer, and that buffer is not always the
    // size of the canvas.
    vec2 occUv = vOcclusionClip.xy / vOcclusionClip.w * 0.5 + 0.5;
    float occDepth = texture2D(tOccluderDepth, occUv).x;
    // viewZ is negative and grows more negative with distance, so adding the feather
    // moves the onset towards the camera.
    float occOnset = occViewZ(occDepth) + uOccluderFeather;
    float occMine = occViewZ(gl_FragCoord.z);
    float occBehind = occOnset - occMine;

    // Derivatives before any branch. Taken inside non-uniform control flow they are
    // undefined, and the failure is a driver-dependent smear rather than an error.
    vec2 occGrad = vec2(dFdx(occBehind), dFdy(occBehind));
    float occSlope = max(length(occGrad), 1e-6);

    float occAlpha = 1.0 - smoothstep(0.0, uOccluderFeather, occBehind);

    // ---- snap the boundary onto the edge the camera can actually see ----
    //
    // The depth test decides *ordering* well and *position* badly, and the two are
    // different problems. Ordering only needs the sign of a depth difference, which is
    // large everywhere except right at the boundary. Position needs that difference
    // localised to a pixel — and at the nose the two surfaces meet at a grazing angle,
    // where a fraction of a millimetre of depth error becomes several pixels of
    // sideways error. Depth is also the one axis a single camera cannot measure.
    //
    // The nose's own edge, meanwhile, is in the video at full resolution, every frame.
    // So the depth field is used for which side of the boundary a pixel is on, and the
    // image is used for where the boundary is.
    //
    // Bounded to uSnapRadius pixels and gated on the edge actually being there: with
    // no edge to find — a nose silhouetting against a cheek in flat light — the search
    // returns nothing, the term vanishes, and what is left is the feather it started
    // with. It can sharpen and it can shift a few pixels; it cannot invent a boundary.
    //
    // The fade's half-width in pixels, floored because a sub-pixel transition is a hard
    // edge again and capped so a grazing intersection cannot stretch it across the
    // whole frame. Computed before the gate because the gate must reach one half-width
    // past the furthest the boundary may move: the re-centred fade has to finish inside
    // the gated region, or the snapped fragments and the plain-fade fragments disagree
    // at the gate line and the disagreement draws itself as a seam.
    //
    // The gate is ALSO capped in slope, because the search exists for the grazing
    // regime and only there — see the module header: at the nose the two surfaces
    // meet near-tangentially, the fade smears wide, and a sub-millimetre depth
    // error is several pixels of sideways error worth recovering. At the head's
    // own silhouette the opposite happens: the depth discontinuity into the room
    // makes occSlope enormous, the slope-scaled reach opens the gate along the
    // entire silhouette, and the luma search re-centres the occlusion boundary
    // onto the head-vs-room edge — frame fragments plainly IN FRONT of the face
    // dither out in a dashed seam that flickers with the video. A crisp boundary
    // also has nothing to recover: the sideways error is depthError/occSlope
    // pixels, sub-pixel exactly where the slope is large. So the search requires
    // the fade band to be at least ~2 px wide (occSlope < uOccluderFeather / 2)
    // — the grazing regime by definition — and steeper boundaries keep the plain
    // fade, whose 50% point is the same place the re-centred fade would put it.
    float occHalf = clamp(uOccluderFeather * 0.5 / occSlope, 1.0, 8.0);
    if (uSnapStrength > 0.0
        && occSlope < uOccluderFeather * 0.5
        && abs(occBehind) < uOccluderFeather + occSlope * (uSnapRadius + occHalf)) {
      // Across the boundary, in screen pixels.
      vec2 occDir = occGrad / occSlope;
      // How far past the 50% point this fragment sits, as a signed pixel distance —
      // fragment minus boundary, the opposite convention to occAt below.
      float occHere = (occBehind - uOccluderFeather * 0.5) / occSlope;

      float occBest = 0.0;
      float occAt = 0.0;
      for (int i = 0; i < 9; i++) {
        float t = (float(i) - 4.0) * (uSnapRadius * 0.25);
        vec2 p = occUv + occDir * t * uScreenTexel;
        // Central difference along the boundary normal — the only direction the
        // boundary can move in, so an edge running parallel to it scores nothing.
        float g = abs(occLuma(p + occDir * uScreenTexel) - occLuma(p - occDir * uScreenTexel));
        // Nearer edges win ties. Without this the search happily jumps to a stronger
        // edge on the far side of the nose.
        float score = g * (1.0 - 0.6 * abs(t) / (uSnapRadius + 1.0));
        if (score > occBest) { occBest = score; occAt = t; }
      }

      // A weak best is noise, a beard hair, or film grain. Ramp in over it rather than
      // thresholding, so the boundary does not pop between snapped and unsnapped as the
      // light changes.
      float occTrust = smoothstep(uSnapEdgeMin, uSnapEdgeMin * 3.0, occBest) * uSnapStrength;
      // The two distances point opposite ways: occAt is measured *from the fragment*
      // (edge minus here) while occHere is measured *from the boundary* (here minus
      // boundary), so the boundary-to-edge distance is their SUM. Subtracting instead
      // makes the shift a function of the fragment itself — the 50% point then lands at
      // only trust/(1+2*trust) of the way to the edge, and the fade narrows to
      // 1/(1+2*trust) of its width. The sum is constant along the boundary normal, so
      // the fade moves as one piece and keeps its screen width.
      //
      // Clamped to the radius, which is the documented promise of uSnapRadius: a
      // fragment near the gate edge can see an edge almost two radii from the boundary,
      // and trusting it whole would move the boundary further than the search was ever
      // allowed to look. The clamp is inert for fragments near the boundary — their
      // window is centred on it — and it is what keeps the re-centred fade inside the
      // widened gate above.
      float occShift = clamp((occAt + occHere) * occTrust, -uSnapRadius, uSnapRadius);

      // Re-centre the same fade on the edge that was found.
      occAlpha = 1.0 - smoothstep(-occHalf, occHalf, occHere - occShift);
    }

    occDebugAlpha = occAlpha;
    // The debug view wants to see what was hidden, so it must survive to be tinted.
    if (uOccluderShow < 0.5 && occAlpha < occBayer8(gl_FragCoord.xy)) discard;
  }
`;

/**
 * Paints the boundary's own decision over the frame, for looking at.
 *
 * Green where the frame is drawn, red where the head is hiding it, and the gradient
 * between them is the fade. Put a nose in that gradient and one screenshot answers the
 * question every measurement in this file is a proxy for: *is the edge in the right
 * place*. Nothing else here can answer it, because the answer is a property of a
 * particular face at a particular distance.
 */
const SHOW = /* glsl */`
  if (uOccluderShow > 0.5) {
    vec3 occTint = mix(vec3(1.0, 0.13, 0.09), vec3(0.16, 1.0, 0.38), occDebugAlpha);
    gl_FragColor.rgb = mix(gl_FragColor.rgb, occTint, 0.82);
    gl_FragColor.a = 1.0;
  }
`;

/**
 * Creates the pre-pass target and the uniforms every masked material shares.
 *
 * The uniform objects are shared by reference across every material, so the app sets
 * the feather or turns the mask off in one place and the whole frame follows. That is
 * also why they are plain `{ value }` holders rather than numbers.
 */
export function createOcclusionMask(renderer, {
  feather = OCCLUDER_FEATHER,
  /**
   * How much of the way to the found edge the boundary is allowed to move, 0 to 1.
   *
   * Not 1. The edge search is the only part of this pipeline whose input is the raw
   * camera image rather than a measurement, and a beard, a specular highlight on the
   * nose or a hard shadow are all edges as far as a luminance gradient is concerned.
   * At 0.7 a confidently-found edge takes most of the correction and a wrongly-found
   * one costs a fraction of the radius.
   */
  snap = 0.7,
  /** How far the boundary may move, in screen pixels. */
  snapRadius = 4,
  /** The luminance step, 0 to 1, at which an edge starts being believed at all. */
  snapEdgeMin = 0.04,
} = {}) {
  const size = renderer.getDrawingBufferSize(new THREE.Vector2());

  const depthTexture = new THREE.DepthTexture(Math.max(size.x, 1), Math.max(size.y, 1));
  depthTexture.format = THREE.DepthFormat;
  // 24-bit, not 16. At selfie range with this near plane a 16-bit buffer resolves
  // about 0.3 mm, which is a quarter of the feather — the fade would come out as
  // three or four visible steps instead of a gradient.
  depthTexture.type = THREE.UnsignedIntType;
  depthTexture.minFilter = THREE.NearestFilter;
  depthTexture.magFilter = THREE.NearestFilter;

  const target = new THREE.WebGLRenderTarget(Math.max(size.x, 1), Math.max(size.y, 1), {
    depthTexture,
    depthBuffer: true,
    stencilBuffer: false,
  });

  // A stand-in until the camera is running. Sampling an unbound sampler is undefined,
  // and mid grey means the edge search finds nothing and the snap term is zero — the
  // same thing it does on a face with no edge to find.
  const blank = new THREE.DataTexture(new Uint8Array([128, 128, 128, 255]), 1, 1);
  blank.needsUpdate = true;

  const uniforms = {
    tOccluderDepth: { value: depthTexture },
    tCameraFrame: { value: blank },
    uScreenTexel: { value: new THREE.Vector2(1 / Math.max(size.x, 1), 1 / Math.max(size.y, 1)) },
    uOccluderNear: { value: 1 },
    uOccluderFar: { value: 1000 },
    uOccluderFeather: { value: feather },
    // Off until the first pre-pass has actually run: an unrendered depth texture holds
    // whatever the driver left there, and reading it would discard the frame at random.
    uOccluderOn: { value: 0 },
    uSnapStrength: { value: snap },
    uSnapRadius: { value: snapRadius },
    uSnapEdgeMin: { value: snapEdgeMin },
    uOccluderShow: { value: 0 },
  };

  /** The wearer's distance, in cm. Unknown until the first detection. */
  let distanceCm = 0;

  const scratch = new THREE.Vector2();
  let enabled = true;
  let pixelsPerCm = 0;
  /** The fade the caller asked for. What reaches the shader is this, capped on screen. */
  let featherCm = feather;
  /**
   * The relief the occluder is actually applying, in cm. Starts at the compile-time
   * derivation and follows the live value through the `relief` setter — the
   * Edge-softness slider rewrites the occluder's `reliefBase`, and a search radius
   * sized from the constant can end up barely reaching the displacement itself, with
   * nothing left over for the tracking error the snap exists to recover.
   */
  let reliefCm = OCCLUDER_RELIEF;

  /**
   * Holds the boundary's two physical offsets to a pixel budget.
   *
   * The fade narrows rather than the relief alone, because the two have to stay in step:
   * the relief is derived as `PAD_SINK + feather + margin` so that a seated pad sits in
   * front of where the fade starts, and shrinking one without the other reopens the hole
   * in the bridge that the relief exists to prevent. `occluder.js` caps the relief
   * against the same budget from the same distance, and the app feeds the same live
   * `reliefBase` through the `relief` setter, so they arrive at the same answer.
   */
  const scaleToPixels = (camera) => {
    if (!(distanceCm > 1)) { pixelsPerCm = 0; uniforms.uOccluderFeather.value = featherCm; return; }
    const half = Math.tan((camera.fov * Math.PI) / 360);
    pixelsPerCm = (target.height / 2) / (distanceCm * half);
    if (!(pixelsPerCm > 0)) return;
    uniforms.uOccluderFeather.value = Math.max(Math.min(featherCm, MAX_FEATHER_PX / pixelsPerCm), 1e-4);
    // The search has to be able to reach at least as far as the offsets displaced the
    // boundary, or it can only ever recover part of an error it can plainly see.
    const reliefPx = Math.min(reliefCm, MAX_RELIEF_PX / pixelsPerCm) * pixelsPerCm;
    uniforms.uSnapRadius.value = Math.min(Math.max(reliefPx * 2.5, 3), 14);
  };

  return {
    target,
    uniforms,

    get enabled() { return enabled; },
    set enabled(value) {
      enabled = !!value;
      if (!enabled) uniforms.uOccluderOn.value = 0;
    },

    get feather() { return featherCm; },
    set feather(value) {
      featherCm = Math.max(value, 1e-4);
      uniforms.uOccluderFeather.value = featherCm;
    },

    /** The occluder's live relief, in cm — see `reliefCm` for why this must follow it. */
    get relief() { return reliefCm; },
    set relief(value) { reliefCm = Math.max(value, 0); },

    get snap() { return uniforms.uSnapStrength.value; },
    set snap(value) { uniforms.uSnapStrength.value = Math.min(Math.max(value, 0), 1); },

    /**
     * The camera frame the boundary is snapped against.
     *
     * The same texture the scene draws as its background, so the pixels the search
     * reads are the pixels the viewer is looking at. It has to be re-pointed whenever
     * the source changes — a `VideoTexture` and a still image are different objects,
     * and holding the old one would snap the boundary to the previous source's edges.
     */
    setCameraTexture(texture) { uniforms.tCameraFrame.value = texture ?? blank; },

    get show() { return uniforms.uOccluderShow.value > 0.5; },
    set show(value) { uniforms.uOccluderShow.value = value ? 1 : 0; },

    /**
     * How far away the wearer is, in cm — which is what turns millimetres into pixels.
     *
     * Everything the boundary is built from is physical, and physical is the wrong unit
     * for an artefact: the same 1.8 mm of relief is two screen pixels at arm's length
     * and seven when someone leans into the lens to look closely, which is exactly when
     * they are looking closely. This is what lets the fade and the search know which of
     * those they are in.
     */
    setDistance(cm) { if (Number.isFinite(cm) && cm > 1) distanceCm = cm; },

    /** Screen pixels per centimetre of face at the wearer's distance, or 0 if unknown. */
    get pixelsPerCm() { return pixelsPerCm; },

    /**
     * Renders the occluder's depth, and nothing else, into the pre-pass target.
     *
     * By layer rather than by hiding the rest of the scene: toggling `visible` on
     * every other node twice a frame is both slower and a state machine that only has
     * to be got wrong once to leave the glasses hidden.
     */
    renderDepth(scene, camera) {
      if (!enabled) return;

      renderer.getDrawingBufferSize(scratch);
      if (scratch.x !== target.width || scratch.y !== target.height) {
        target.setSize(Math.max(scratch.x, 1), Math.max(scratch.y, 1));
        uniforms.uScreenTexel.value.set(1 / Math.max(scratch.x, 1), 1 / Math.max(scratch.y, 1));
      }

      uniforms.uOccluderNear.value = camera.near;
      uniforms.uOccluderFar.value = camera.far;
      scaleToPixels(camera);

      const previousTarget = renderer.getRenderTarget();
      const previousLayers = camera.layers.mask;
      // The shadow map is unchanged by a depth-only pass over one mesh, and rebuilding
      // it here would double the cost of the shadow for nothing. The main render below
      // updates it normally.
      const previousAutoUpdate = renderer.shadowMap.autoUpdate;
      renderer.shadowMap.autoUpdate = false;

      // The debug view needs the occluder to stop culling in the *main* pass so the
      // hidden fragments survive to be tinted — but this pass is where its depth comes
      // from, so it has to write here regardless. Flipped for the duration and put back.
      const rewritten = [];
      scene.traverse((node) => {
        if (!node.isMesh || !node.material || node.material.depthWrite) return;
        if (!node.layers.isEnabled(OCCLUDER_LAYER)) return;
        node.material.depthWrite = true;
        rewritten.push(node.material);
      });

      camera.layers.set(OCCLUDER_LAYER);
      renderer.setRenderTarget(target);
      renderer.clear(true, true, false);
      renderer.render(scene, camera);

      for (const material of rewritten) material.depthWrite = false;
      renderer.setRenderTarget(previousTarget);
      camera.layers.mask = previousLayers;
      renderer.shadowMap.autoUpdate = previousAutoUpdate;

      uniforms.uOccluderOn.value = 1;
    },

    dispose() {
      target.dispose();
      depthTexture.dispose();
      blank.dispose();
    },
  };
}

/**
 * Adds the mask to every material under `root`.
 *
 * Composed onto whatever `onBeforeCompile` a material already has rather than
 * replacing it — `temples.js` installs a depth fade this way and `models.js` rebuilds
 * whole material chains, and silently dropping either of those would be a bug that
 * only shows on one asset.
 *
 * `customProgramCacheKey` is not optional and is the subtle half of that composition.
 * three caches compiled programs across materials, and two materials whose
 * `onBeforeCompile` stringify the same are considered interchangeable — so a masked
 * material and an unmasked one of the same type will happily share a program, and
 * which of the two gets compiled first decides whether the mask exists at all that
 * session. The key folds in the base material's own key for the same reason.
 */
export function installOcclusionMask(root, uniforms) {
  let patched = 0;
  root.traverse((node) => {
    if (!node.isMesh && !node.isPoints && !node.isLine) return;
    const materials = Array.isArray(node.material) ? node.material : [node.material];
    for (const material of materials) if (patchMaterial(material, uniforms)) patched++;
  });
  return patched;
}

function patchMaterial(material, uniforms) {
  if (!material || material.userData.occlusionMasked) return false;
  material.userData.occlusionMasked = true;

  const baseCompile = material.onBeforeCompile;
  const baseKey = material.customProgramCacheKey;

  material.onBeforeCompile = function occlusionMask(shader, renderer) {
    if (baseCompile) baseCompile.call(this, shader, renderer);

    Object.assign(shader.uniforms, uniforms);

    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec4 vOcclusionClip;')
      .replace('#include <project_vertex>', '#include <project_vertex>\nvOcclusionClip = gl_Position;');

    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${COMMON}`)
      // Before anything else in the shader body: a discarded fragment should not have
      // paid for a lighting solve, and on the transmissive lenses that solve is the
      // most expensive thing on the frame.
      .replace('void main() {', `void main() {\n${MASK}`)
      // And last, after every material in the chain has had its say about colour.
      .replace('#include <dithering_fragment>', `#include <dithering_fragment>\n${SHOW}`);
  };

  material.customProgramCacheKey = function occlusionMaskKey() {
    return `occlusion-mask|${baseKey ? baseKey.call(this) : this.type}`;
  };

  material.needsUpdate = true;
  return true;
}
