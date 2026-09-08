# Current AR handoff — perfect_temples

The owner accepted the rear-temple correction after live use on September 8,
2026 and authorized commit/push as `perfect_temples` on
`codex/ar-v4-nose-occlusion`. The accepted entry point is
`experiments/temple-sagittal/live.html`. Start its Vite config from ar_v4:

```powershell
npm run dev -- --config experiments/temple-sagittal/vite.config.ts
```

Perfecto `31df28eb8ca0c698467fd9bfb16f737f9a74e915` remains the unchanged renderer
and reference app at `/`. Keep that comparison available. The parent Python
Lenses application and Railway deployment remain separate. No merge or deployment
is authorized by this checkpoint. Leave unrelated model_studio work alone.

## Accepted implementation

The new renderer runs two independent native scenes. The first is untouched
perfecto. The second applies a smooth posterior Y-only curve to cloned temple
geometry, beginning 15 mm behind the lens rear plane and reaching a maximum
20 mm lowering at the existing Z cutoff. Head and camera must agree on downward
pitch; the correction tapers out with yaw. X/Z, optical/proximal geometry, original
GLBs, attachment and camera are preserved. Normals/tangents follow the curve.
This is an authored preview convention, not a physical hinge or measured fit.

The original Z cutoffs stay Amber −105 mm / Tom Ford −110 mm, with the 15 mm
paired-camera fade. Existing temple visibility policies remain unchanged.
The original face reconstruction and Raw + Option17 shape remain fixed:
`central-wp020-dp015`, width parameter .20 and forward-depth parameter .15, with
original raw fallback guards. No RGB nose repair or reconstruction experiment
was added. The old nasal-boundary source remains untouched for historical imports.

Final integer pixel composition copies perfecto under the full depth-independent
optical/proximal bounds and central eye/nose guard, and outside bounded arm
corridors. This prevents candidate depth changes from altering protected final
lens/nose pixels. Branch/mask/replay failures fall back to perfecto. Through-lens
and reflection appearances retain the original rendering. Broad guards can limit
the correction or create a join; two renderers/readback work have no measured
mobile/30 FPS claim.

The comparison owns one exact source image and detection. Hold problem frame
stops camera/worker work and retains that pair for toggling and explicit local
lossless diagnostic download. Holding cannot overlap recording. Replay restores
actual saved surface, Z cutoffs, visibility, drop and protection. The internal
variant value `candidate` remains for compatibility; its UI is Perfect temples.
New capture/diagnostic metadata identifies `acceptedRevisionLabel: perfect_temples`
and `candidateAccepted: true`; `baselineCommit` identifies perfecto. The older
`acceptedCommit` field retains its historical baseline-reference meaning.

## Evidence and limits

The owner’s live acceptance follows the completed matched evaluation. The original
16 pairs × 2 models reach about 20 degrees pitch: only two Tom Ford recordings
changed, and Amber recordings were unchanged. Do not convert that limited recorded
coverage into proof of all-angle or all-face accuracy. Separate synthetic native
40–50 degree views demonstrate both curves; they are not wearer recordings.
All original nasal ROIs, protected optical pixels and inactive controls remain
exact in the recorded audit. See docs/REVIEWS.md and the local study report.

The system does not segment hair or reconstruct ears/hair depth, and has no
gender-specific path. Hair stays in the camera background; the head/face masks
and end fade do not reliably put loose hair or bangs in front of glasses. Hair
away from the frame should be easier, but hairstyle-diverse live evidence is
missing. Substantial face covering may also degrade tracking; that is an expected
limitation, not a result measured in this study. Any future hair mask must preserve
this accepted nose/front behavior and earn acceptance on exact paired recordings.
No new hair feature or reconstruction work is authorized merely by discussing it.

## Checks and recovery

Baseline npm test passed 52 unit tests, 8 browser flows, strict types, assets and
build. The isolated implementation passed 14 unit tests and 10 browser flows.
Independent recorded auditing checked 32 cases; 8 additional cases cover no-MSAA
fallback. Historical private fixture tests skip when those recordings are absent
from a fresh checkout; other browser flows use the checked-in synthetic fixture.
Run one heavy browser/test process at a time. Optional replay/audit/gallery tools
require private local evidence. The generated comparison.html remains Git-ignored.

Preserve recordings, all earlier .recovery material and linked worktrees.
The evaluated source and evidence are in .recovery/temple-rethink-2026-09-08/;
pre-acceptance docs/source are in .recovery/perfect-temples-acceptance-2026-09-08/.
The preceding perfecto handoff and rejected experiments remain archived locally.
The study preservation audit verified 29,180 earlier recording/recovery files
and 49 baseline tracked files byte-for-byte. Git is not their backup.
