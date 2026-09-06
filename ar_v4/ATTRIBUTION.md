# Assets and dependencies

`public/models/amber-horizon.glb`: converted from the user-supplied `Meshy_AI_Amber_Horizon_Sunglas_0813121934_texture (1)_seamfix.blend`, SHA-256 `58d2abde997458eae74d88b0af5e44fb53789399932ca9397056481a0da4d5c0`. The original file in Downloads is unchanged. Blender 5.2 applied the existing seam-smoothing modifier, reduced the mesh to 99,521 triangles, resized the frame atlas to 2048 × 2048 and baked the procedural lens gradient. Textures are embedded, with source transmission, IOR and roughness retained. Normalized source coordinates use an assumed 145 mm preview width, not a measured physical size. Original texture markings are retained; this project does not assert authorship of the supplied model. Export notes are in `../.recovery/model-swap/`. The previous Navigator asset remains in the recovery archive.

`public/models/canonical-face.json` (prepared from the canonical OBJ) and `face_landmarker.task`: MediaPipe, copyright Google, Apache 2.0. <https://github.com/google-ai-edge/mediapipe>. The canonical face uses centimetres. It is a generic model, not a scan of the wearer.

`tests/fixtures/face-a.jpg`: synthetic StyleGAN2 face originally obtained via thispersondoesnotexist.com, inherited from ar_v2. Used only for automated development checks, excluded from the production build. It is not a photograph of a real person and is never a camera fallback.

`@mediapipe/tasks-vision`: Apache 2.0. The pinned npm package supplies the JavaScript and locally served WebAssembly. `three`: MIT, copyright three.js authors. Full Apache 2.0 and Three.js MIT texts are included in `public/licenses` and copied into the production build. See `package-lock.json` for exact versions and integrity hashes, and `public/models/manifest.json` for copied asset hashes.
