/**
 * The vendored runtime, in one place, because two lists of it drifted.
 *
 * `fetch-vendor.mjs` fetches and SHA-256 verifies these thirteen files.
 * `check-selfcontained.mjs` asserts they are present. Those were separate
 * lists, and the second one had **four of the thirteen** — so a `vendor/` tree
 * missing `three.core.js` (which the listed `three.module.js` imports), or
 * missing the addons, or missing three of the four wasm files, passed
 * `npm test` cleanly and died at boot with a module error naming the wrong
 * problem.
 *
 * A list whose whole job is to notice something missing cannot be allowed to
 * be incomplete, and the only way to guarantee that is for there to be one of
 * it. Neither consumer keeps its own copy now.
 */

/** three.js r185.1 (MIT) and @mediapipe/tasks-vision 1.0.1 (Apache 2.0).
 *  Versions and licences are documented in ../ATTRIBUTION.md. */
const THREE = 'https://cdn.jsdelivr.net/npm/three@0.185.1';
const MP = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1';

export const VENDOR_FILES = [
  ['three/LICENSE', `${THREE}/LICENSE`,
    '8b378ebe60e2fe500158cb0ac71cb5e8b7d92953c2abcc63a0eb90499653b5bc'],
  ['three/three.module.js', `${THREE}/build/three.module.js`,
    'bbf5ed13fe4373f5bd38b14ea8e62e9f157327da5638edc6d3863e08b167c9c7'],
  ['three/three.core.js', `${THREE}/build/three.core.js`,
    '3718df126d69c125362a03340913204470d8c50238605150e57f808840fb7759'],
  ['three/addons/loaders/GLTFLoader.js', `${THREE}/examples/jsm/loaders/GLTFLoader.js`,
    '97642d720f16cc9a0c9844934198e4d0c023bea8e89576d0f7545d03b2d103d2'],
  ['three/addons/loaders/OBJLoader.js', `${THREE}/examples/jsm/loaders/OBJLoader.js`,
    '86c8384e269b75a21d502d438e3f0dc2e09dc61b6e6da6b81947f5b119112bf8'],
  ['three/addons/environments/RoomEnvironment.js',
    `${THREE}/examples/jsm/environments/RoomEnvironment.js`,
    '55f466192cc84298755a424c5e040345006b2ee1455589b3b54126c2ea4123f4'],
  ['three/addons/utils/BufferGeometryUtils.js',
    `${THREE}/examples/jsm/utils/BufferGeometryUtils.js`,
    '5c552223a9309883743b80538d6e9cdb45e3227f30d3ec56fb2c39b46e78d595'],
  ['three/addons/utils/SkeletonUtils.js', `${THREE}/examples/jsm/utils/SkeletonUtils.js`,
    'b1632a703206c3d830de9fcbe515696770d04b71a15ee6b50afa6d2c3298c86f'],
  ['mediapipe/vision_bundle.mjs', `${MP}/vision_bundle.mjs`,
    'd885630c297c0b20b1fe86096cb06291c4c8080876f27852e724f24ac603713f'],
  ['mediapipe/wasm/vision_wasm_internal.js', `${MP}/wasm/vision_wasm_internal.js`,
    'e170ee67dd4e16c1a6fcd8840a206687e5a59b22c20e4a902bc445b095454d73'],
  ['mediapipe/wasm/vision_wasm_internal.wasm', `${MP}/wasm/vision_wasm_internal.wasm`,
    '8da277a733926eacd0474b8704b36742d6ec3231c57a860c5b889dff8f1df886'],
  ['mediapipe/wasm/vision_wasm_module_internal.js',
    `${MP}/wasm/vision_wasm_module_internal.js`,
    'da8934057f147b622e82cfb4c0dbd85461c598e268588b5a8ba9ca963a8ff82d'],
  ['mediapipe/wasm/vision_wasm_module_internal.wasm',
    `${MP}/wasm/vision_wasm_module_internal.wasm`,
    '2dabd8e23c60984628beb7bb338764c81a08e6837145273f59578684b5d53c1b'],
];
