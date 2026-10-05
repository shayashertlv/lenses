/** Where the GLB decoders live (WP1_DESIGN.md §1, Format row: Draco, meshopt and KTX2 are decoded). The Draco decoder
 *  and the Basis (KTX2) transcoder are wasm modules from three's own `examples/jsm/libs`; vite.config.ts serves them
 *  from node_modules in development and copies them into the build, at these published paths (under Vite's base). The
 *  meshopt decoder is a JS module with its wasm inlined, imported directly. Nothing is fetched from outside the page. */

/** Directory of each decoder, relative to the page's base URL (DRACOLoader / KTX2Loader take a directory). */
export const DECODER_DIRECTORIES = Object.freeze({draco: 'decoders/draco/', basis: 'decoders/basis/'} as const);

/** Published path -> file inside node_modules (both relative). */
export const DECODER_FILES: Readonly<Record<string, string>> = Object.freeze({
  'decoders/draco/draco_decoder.js': 'three/examples/jsm/libs/draco/gltf/draco_decoder.js',
  'decoders/draco/draco_decoder.wasm': 'three/examples/jsm/libs/draco/gltf/draco_decoder.wasm',
  'decoders/draco/draco_wasm_wrapper.js': 'three/examples/jsm/libs/draco/gltf/draco_wasm_wrapper.js',
  'decoders/basis/basis_transcoder.js': 'three/examples/jsm/libs/basis/basis_transcoder.js',
  'decoders/basis/basis_transcoder.wasm': 'three/examples/jsm/libs/basis/basis_transcoder.wasm',
});
