# FPS tests on iPhone and desktop

The owner requested this separate Railway preview on September 14, 2026:

https://web-production-ef3ca.up.railway.app/ar_testing/fps/experiments/fps-candidate/live.html?fps=next-combined

Test 4 reduces CPU pixel composition work. Test 5 reduces repeated statistics
work. Test 6 combines those changes. G and Tests 1–3 remain selectable. G stays
the accepted version; these candidates need the owner's visual review.

Open the link in Safari on iPhone or a desktop browser, choose glasses and a
hair model, then **Open camera**. For measurements select Test 6 and choose
**Compare G / test / test / G**. Keep the page visible for about three minutes,
including setup. Each segment uses a fresh document, warms for five seconds
and at least three tracked/masked frames, then measures for 30 seconds.
Allow the reloads and tap **Open camera / continue segment** if prompted.

Repeat slow front/nose, down, up, left and right checks in each segment, then
repeat with both glasses and both hair models. Compare hair availability and
frame age as well as FPS. **Download comparison** prepares the complete JSON,
a persistent save link and selectable text. Reports contain timing and model
information, with a build fingerprint; they contain no camera images or masks.
Backgrounding, manual setting changes and cancellation retain partial results.

## Isolated delivery

`source/` freezes the local FPS experiment and the dependencies used for its G
comparison. The existing deployed efficiency studies and accepted source
entry points remain independent. Both isolated comparison paths receive the
same proven iPhone capture correction: rVFC frame counters admit frames,
optional timestamps may be zero or absent, and the synchronously captured
pixels stay owned through face, hair and rendering. Playback-clock movement
after capture cannot reject a valid image. The renderer and two-image bound
are preserved. The 45-file perfect-temples snapshot is hash-verified at build.

`vite.config.ts` builds only the FPS entry and generated JS/CSS under
`/ar_testing/fps/`, with the existing same-origin SDK guard in main and worker
bundles. It reuses the identical public model/WASM files. `package.mjs` checks
all existing public bytes, copies only the narrow FPS output and extends the
public manifest. Rebuilds may update only the FPS subtree, retain older hashed
FPS assets, and preserve all 22 original public files byte-for-byte. No Python routes,
Railway configuration, existing default page or model files are changed.

From `ar_v4/`, run `npm ci`, `npm run test:fps-public`, then
`npm run build:fps-public`. Required repository checks remain `npm test`.
For production-route browser checks, start
`python experiments/efficiency-lab/qa/mobile-server.py --port 8106`, then run
`node experiments/fps-public/qa/mobile.mjs` with the options documented there.
QA outputs are ignored and never included in the public package.

## Evidence limits

Before mobile packaging, a balanced desktop still-input experiment observed
about 10.20 AR updates/s for Test 6 versus 9.28 for G, with full tracked/masked
coverage. This preliminary result used an Arc 140T GPU and a different build
fingerprint. It is not a moving wearer, physical iPhone, thermal or display
scanout measurement. Publication FPS counts completed AR images submitted to
the canvas. Exact previous matched checks covered both glasses/hair models,
down/up and both yaw controls with nose/front/background protections.

The shared mobile capture correction and export controls require the separate
production-route checks recorded in `docs/REVIEWS.md`. Higher FPS alone does
not promote a candidate or establish unchanged visual quality.
