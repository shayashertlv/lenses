/**
 * A deliberate `any` shim for the vendored three.js.
 *
 * `@types/three` is not installed and is not wanted: it would be the only
 * dependency in the tree that has to track a vendored library's version, and
 * every mismatch between the two shows up as a type error in code that runs
 * correctly. The render layer is thin — a scene, a camera, two materials — and
 * the parts of this system that are worth type-checking are in `core/`,
 * `enroll/`, `track/` and `fit/`, none of which import three at all.
 *
 * That separation is deliberate and it is checked: `npm run check:isolation`
 * fails if anything outside `render/` and `app/` imports three.
 */
declare module 'three' {
  const three: any;
  export = three;
}
declare module 'three/addons/*' {
  const addon: any;
  export = addon;
}

/**
 * The two addons this tree actually imports, named individually.
 *
 * The wildcard above uses `export =`, which cannot serve a NAMED import — and
 * both of these are named exports. Listing them is better than loosening the
 * wildcard: an addon that gets imported without a line here fails to compile,
 * which is the moment to notice that `scripts/fetch-vendor.mjs` needs a new
 * hash pinned for it.
 */
declare module 'three/addons/loaders/GLTFLoader.js' {
  export const GLTFLoader: any;
}
declare module 'three/addons/environments/RoomEnvironment.js' {
  export const RoomEnvironment: any;
}
