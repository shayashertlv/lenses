/**
 * The render layer.
 *
 * One WebGL canvas holds everything: the camera feed is the scene background, the
 * glasses are geometry inside it. Keeping the video inside the scene rather than
 * behind a transparent canvas buys two things — the lenses can actually refract
 * what is behind them, and there is no second element that can drift out of
 * alignment with the first.
 *
 * The only setting here that must not be guessed is the camera's field of view.
 * MediaPipe solves the head pose against an assumed pinhole camera with a 63°
 * vertical field of view; if our virtual camera disagrees, the frame will sit
 * correctly at one distance and slide off the face at every other. Matching it is
 * what makes the alignment hold as the user leans in and out.
 */

import * as THREE from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

/** The pinhole camera MediaPipe assumes when it solves the facial transform. */
export const MEDIAPIPE_VERTICAL_FOV = 63;

/**
 * How much harder the frame's glass reflects the environment than everything else.
 *
 * The environment intensity the rest of the scene runs at is chosen to *light* things
 * plausibly, and a lens is not lit by the room, it shows it. Reflected at the same
 * level the room reads as a uniform grey wash, which is the complaint that a lens
 * "just mirrors what is in front of it": with nothing structured in the reflection,
 * turning the head changes nothing about it. Reflecting the same map harder brings its
 * walls, ceiling and lit panels up out of the noise, so different parts of a curved
 * lens catch different things and the highlights move when the wearer does.
 *
 * Bounded by the opposite failure, which is a *large flat* lens: it takes the whole
 * reflection at one angle, so what reads as structure on a small curved lens reads as
 * an even haze on a big one. At 4 the navigator's 59 mm lenses frosted over. 2 keeps
 * the structure and stays out of the way, which is what a coated lens does.
 */
const GLASS_ENVIRONMENT_BOOST = 1.0;

export function createScene(canvas, { preserveDrawingBuffer = false } = {}) {
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: false,
    powerPreference: 'high-performance',
    // Off in the app — it costs bandwidth every frame. On in the test harness,
    // which reads the rendered image back to prove the frame actually drew.
    preserveDrawingBuffer,
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  // Shadows are what ground the frame on the face. Without one the glasses read as
  // pasted on, however good the placement — nothing else in the image says the two
  // surfaces are touching.
  renderer.shadowMap.enabled = true;
  // PCF, not PCFSoft: r185 deprecated the soft variant and silently falls back to
  // PCF anyway, with a console warning per context.
  renderer.shadowMap.type = THREE.PCFShadowMap;
  // A transmissive frame costs a second render pass — three draws the opaque scene
  // into a buffer for the glass to refract.
  //
  // This ran at half resolution, on the argument that what it holds is a
  // roughness-blurred image anyway so half of it is free. That argument belonged to a
  // frame at roughness 0.15, and it went away with it: at 0.05 the refracted image is
  // sampled at almost mip 0, so the buffer's own resolution *is* the sharpness of
  // everything seen through the frame. Half of it put a visible softness step at the
  // silhouette — an eyebrow crisp beside the rim and mushy behind it, which reads as
  // frosted glass rather than clear. It costs one extra draw of the opaque scene, and
  // only on frames that have transmissive parts at all. 0.75 is the first thing to
  // give back if a phone cannot hold the frame rate.
  renderer.transmissionResolutionScale = 1.0;
  // The video background carries an sRGB colour space, which three excludes from
  // tone mapping — so the camera pixels pass through untouched while the frames
  // still get a filmic response.
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;

  const scene = new THREE.Scene();

  // Near/far are in centimetres, matching the canonical face model. A face lives
  // between roughly 20cm and 150cm from the lens.
  const camera = new THREE.PerspectiveCamera(MEDIAPIPE_VERTICAL_FOV, 1, 1, 1000);

  // A small room bounced into an environment map. Procedural, so nothing to load,
  // and it gives the metal and glass something to reflect — without it a PBR
  // frame renders as a flat silhouette.
  const pmrem = new THREE.PMREMGenerator(renderer);
  pmrem.compileEquirectangularShader();
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

  // A key light so the frame reads three-dimensionally against the video. It also
  // casts the contact shadow, so it tracks the head (see render()) — a fixed light
  // would need a shadow frustum covering everywhere the head might go, and the
  // shadow would pop in and out of resolution as the wearer leans.
  const key = new THREE.DirectionalLight(0xffffff, 1.2);
  key.position.set(30, 60, 100);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  // Softness is what makes a contact shadow read as a shadow rather than as a
  // second, darker pair of glasses drawn on the face. A frame sits about a
  // centimetre off the skin, and at that distance a real shadow's edge is already
  // diffuse; a pin-sharp one looks painted on. The larger map pays for the blur.
  key.shadow.radius = 5;
  // Units are centimetres: a head is ~25cm, so a ±25cm box catches frame + face.
  key.shadow.camera.left = -25;
  key.shadow.camera.right = 25;
  key.shadow.camera.top = 25;
  key.shadow.camera.bottom = -25;
  key.shadow.camera.near = 5;
  key.shadow.camera.far = 300;
  key.shadow.bias = -0.0002;
  key.shadow.normalBias = 0.6;
  scene.add(key);
  scene.add(key.target);

  const ambient = new THREE.AmbientLight(0xffffff, 0.35);
  scene.add(ambient);

  /**
   * The screen the wearer is looking at.
   *
   * This is what puts glass in the frames, and without it there is none. A lens facing
   * the camera reflects whatever is *behind* the camera, and until this existed there
   * was nothing there to reflect: `scene.environment` is a generic procedural room with
   * no bright feature behind the viewer, and the key sits ~30° off the view axis, which
   * is thirty times the width of a polished lens's specular lobe at roughness 0.02.
   * Measured on a real capture, scaling the lens's environment reflection **twenty-fold
   * moved a single pixel**. So a frame with modelled lenses rendered as a frame with
   * empty rims, which is exactly how it was reported.
   *
   * Putting a light at the camera is not a cheat, it is the one light in the room whose
   * position this app actually knows. Somebody using a webcam is lit by, and looking
   * at, a screen roughly where the lens is — and a screen reflection is the single most
   * characteristic thing on a pair of glasses in any webcam photograph.
   *
   * Weak on purpose, and that is the property that makes it usable. With the light and
   * the view coincident the half-vector *is* the surface normal, so a polished surface
   * concentrates the whole lobe into a highlight while a matte one barely registers it.
   * Measured: at 0.15 it changes 444 pixels at a mean delta of 35/765 — a bright, tight
   * spot — where a light strong enough to be seen as *fill* would have to be four times
   * that and would flatten the face along with it.
   */
  const screen = new THREE.DirectionalLight(0xffffff, 0.35);
  scene.add(screen);
  scene.add(screen.target);

  // Direction the key sits in relative to the head, in cm. Upper-front-front-right,
  // the classic portrait key; far enough out that the shadow direction barely
  // changes as the head translates.
  const keyOffset = new THREE.Vector3(30, 60, 100);
  const headPosition = new THREE.Vector3();

  /** The soft-occlusion pre-pass, once the app installs one. See `setOcclusionMask`. */
  let occlusionMask = null;

  /**
   * The frame's glass, and how hard it reflects the room.
   *
   * Held apart from every other material because it is the one surface here that is a
   * *mirror*. Everything else in the scene wants an environment scaled to light it
   * plausibly; a lens wants one strong enough to show the room's structure, which is
   * the whole difference between a lens that catches a different highlight as the head
   * turns and one that just shows what is behind it. See `attachGlassEnvironment` for
   * why this cannot be a per-material setting without help.
   */
  const glassMaterials = new Set();
  let glassEnvironment = GLASS_ENVIRONMENT_BOOST;

  /**
   * The head. Its matrix comes straight from the tracker each frame, so
   * everything parented here is expressed in canonical-face-model space and rides
   * the real face automatically.
   */
  const head = new THREE.Object3D();
  head.matrixAutoUpdate = false;
  head.visible = false;
  scene.add(head);

  const glasses = new THREE.Group();
  glasses.matrixAutoUpdate = false;
  head.add(glasses);

  return {
    renderer,
    scene,
    camera,
    head,
    glasses,
    pmrem,

    /**
     * Shows the camera feed behind the 3D, at 1:1 with the tracker's input.
     *
     * The texture class is not interchangeable here. A `<video>` element wrapped in
     * a plain `Texture` uploads nothing and renders solid black — only
     * `VideoTexture` drives the per-frame upload. A `<canvas>` works with either.
     * There is a check for this in tests/, because the failure is silent: tracking
     * keeps working perfectly against a video the renderer is not drawing.
     */
    setBackground(element) {
      const isVideo = typeof HTMLVideoElement !== 'undefined' && element instanceof HTMLVideoElement;
      const texture = isVideo ? new THREE.VideoTexture(element) : new THREE.Texture(element);

      texture.colorSpace = THREE.SRGBColorSpace;
      texture.minFilter = THREE.LinearFilter;
      texture.magFilter = THREE.LinearFilter;
      texture.generateMipmaps = false;
      texture.needsUpdate = true;

      scene.background?.dispose?.();
      scene.background = texture;
      return texture;
    },

    /**
     * `aspect` must be the *source* aspect ratio, not the canvas'. The canvas is
     * sized to match it so the two never disagree, but it is the source geometry
     * that MediaPipe solved against.
     */
    resize(width, height, aspect) {
      renderer.setSize(width, height, false);
      camera.aspect = aspect;
      camera.updateProjectionMatrix();
    },

    /** The key light, the ambient and the screen, for lighting adaptation. */
    keyLight: key,
    ambientLight: ambient,
    screenLight: screen,

    /**
     * Hands the frame's glass its own reference to the environment.
     *
     * This looks redundant — `scene.environment` already reaches every material — and
     * it is the difference between a lens that reflects the room and one that cannot.
     * three has this, in `WebGLRenderer`:
     *
     *     if ( ( material.isMeshStandardMaterial || … ) && material.envMap === null
     *          && scene.environment !== null ) {
     *       m_uniforms.envMapIntensity.value = scene.environmentIntensity;
     *     }
     *
     * A material with no `envMap` of its own has its `envMapIntensity` **overwritten**
     * by the scene's, every frame. So the per-material control is dead for everything
     * in this app, and a lens is stuck at whatever strength suits the diffuse lighting
     * — which for a mirror is nowhere near enough. It also makes the setting untestable
     * in the obvious way: scaling it twentyfold changes literally nothing, which reads
     * as "there is nothing to reflect" and is really "that dial is not connected".
     *
     * Pointing `envMap` at the same texture restores the dial. Nothing else changes:
     * it is the same environment the rest of the frame is lit by.
     */
    attachGlassEnvironment(root) {
      // One model wears the environment at a time, so attaching the next releases the
      // last. Without this the set holds every pair the session ever loaded: the app
      // disposes a replaced model's GPU resources, but a Set entry is a strong JS
      // reference, and each glass material pins its textures and their decoded
      // images — megabytes per pair, growing with every flick through the catalogue,
      // while `setLighting` keeps writing intensities into materials long disposed.
      glassMaterials.clear();
      root.traverse((node) => {
        if (!node.isMesh) return;
        for (const material of [].concat(node.material)) {
          if (!material?.userData?.declaredGlass) continue;
          material.envMap = scene.environment;
          material.envMapIntensity = glassEnvironment;
          material.needsUpdate = true;
          glassMaterials.add(material);
        }
      });
    },

    /**
     * Matches the virtual lighting to the scene the camera actually sees.
     *
     * `key`/`ambient`/`environment` are intensities; `tint` is an [r, g, b] colour
     * cast, each 0..1. Applied directly — smoothing is the caller's job, because
     * the caller decides how often it samples.
     *
     * `environment` is the one that matters most and the easiest to forget: a
     * metal frame is almost entirely environment reflections (metalness 0.95
     * leaves ~5% for the lights), so dimming only the key and ambient leaves it
     * glowing studio-bright in a dark room — the exact composite giveaway this
     * exists to fix.
     */
    setLighting({
      key: keyIntensity, ambient: ambientIntensity, environment, screen: screenIntensity, tint,
    } = {}) {
      if (typeof keyIntensity === 'number') key.intensity = keyIntensity;
      if (typeof ambientIntensity === 'number') ambient.intensity = ambientIntensity;
      if (typeof environment === 'number') {
        scene.environmentIntensity = environment;
        // The glass tracks the room like everything else, just harder — its own
        // `envMapIntensity` is live precisely because it carries its own `envMap`.
        glassEnvironment = environment * GLASS_ENVIRONMENT_BOOST;
        for (const material of glassMaterials) material.envMapIntensity = glassEnvironment;
      }
      if (typeof screenIntensity === 'number') screen.intensity = screenIntensity;
      if (tint) {
        key.color.setRGB(tint[0], tint[1], tint[2]);
        ambient.color.setRGB(tint[0], tint[1], tint[2]);
        // The screen deliberately keeps its own colour. Everything else here is
        // matching the *room*, and the room's cast is measured off the wearer's face;
        // a monitor is its own illuminant and is near enough white in every case that
        // tinting it skin-toned would only be wrong.
      }
    },

    /**
     * Installs the soft-occlusion pre-pass.
     *
     * Held by the scene rather than called by the app, because it has to run against
     * the same camera, immediately before the same draw — and `render()` is the only
     * place that is true by construction. Passing null goes back to the plain hard
     * depth test, which is what the occluder still writes in the main pass.
     */
    setOcclusionMask(mask) { occlusionMask = mask; },
    get occlusionMask() { return occlusionMask; },

    render() {
      // The head's own depth, into its own buffer, before anything samples it.
      occlusionMask?.renderDepth(scene, camera);

      // Keep the key light and its shadow frustum on the head wherever it is.
      headPosition.setFromMatrixPosition(head.matrixWorld);
      key.position.copy(headPosition).add(keyOffset);
      key.target.position.copy(headPosition);
      key.target.updateMatrixWorld();
      // And the screen shines from the camera at whatever the head is doing, which
      // keeps it on the view axis — the whole reason it lands on a lens at all.
      //
      // A head sitting exactly at the camera makes the light's own direction
      // degenerate, and a zero-length direction is not "no light", it is a NaN: the
      // subject then renders unlit and it is not obvious why. That never happens with
      // a tracked face, which is always some way in front of the lens, and it happens
      // constantly to anything that renders the frame on its own without a pose.
      screen.position.set(0, 0, 0);
      if (headPosition.lengthSq() > 1e-6) screen.target.position.copy(headPosition);
      else screen.target.position.set(0, 0, -1);
      screen.target.updateMatrixWorld();
      renderer.render(scene, camera);
    },

    dispose() {
      pmrem.dispose();
      renderer.dispose();
    },
  };
}

/**
 * The occluder lives in `occluder.js` now.
 *
 * It moved because it stopped being a thing the render layer owns. What used to be
 * here was geometry plus one sideways stretch; what is there now also owns the depth
 * field `seat()` solves against, and the whole point of the rewrite is that those two
 * cannot be separated — the field is rasterised from the triangles that write the
 * depth buffer. Splitting them across two modules is exactly how they drifted apart
 * in the first place.
 *
 * Re-exported here so callers that only ever wanted 'the head' keep working.
 */
export {
  createOccluder, createShadowCatcher, updateOccluder, headProfileFor, surfaceOf,
  OCCLUDER_LAYER, OCCLUDER_FEATHER,
} from './occluder.js';

/**
 * The face wireframe lives in `occluder.js` now, as `createOccluderDebugMesh`.
 *
 * What was here took a copy of the canonical mesh at boot and drew it, rigidly posed,
 * for ever. That was an honest picture of the occluder right up until the occluder
 * started being deformed onto the wearer, at which point it became a picture of
 * something the renderer no longer uses — showing the average nose sitting off the real
 * one, which is a true statement about the average nose and a misleading one about the
 * occluder. A debug overlay that disagrees with the thing it is debugging is worse than
 * no overlay, so it now shares the occluder's own vertices.
 */

/**
 * Marks the anchors the fit is solved against, so a bad placement can be read off
 * the screen instead of guessed at.
 */
export function createAnchorDebug(face, indices) {
  const positions = new Float32Array(indices.length * 3);
  indices.forEach((index, i) => {
    positions[i * 3] = face.positions[index * 3];
    positions[i * 3 + 1] = face.positions[index * 3 + 1];
    positions[i * 3 + 2] = face.positions[index * 3 + 2];
  });

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));

  const points = new THREE.Points(
    geometry,
    new THREE.PointsMaterial({
      color: 0xf59e0b,
      size: 0.35,
      depthTest: false,
      toneMapped: false,
    }),
  );
  points.renderOrder = 11;
  points.visible = false;
  return points;
}
