# Current AR review — September 8, 2026

**The owner accepted perfect_temples after live use** and authorized commit/push
under that name. Its entry point remains `experiments/temple-sagittal/live.html`.
Perfecto `31df28eb8ca0c698467fd9bfb16f737f9a74e915` stays preserved at `/` with
unchanged rendering source and assets. Pre-acceptance docs/source are archived
in `.recovery/perfect-temples-acceptance-2026-09-08/`.

The downward failure involves the posterior trajectory and incomplete head/ear
occlusion. Pitch projects the rigid arms toward the forehead; this is not by
itself evidence of a projection bug or increased inward convergence. Baseline
recorded ablations found no side-overlay contribution in the strongest frontal
down view; its frontal visibility approximation already hides much of the rear
arm. The supplied steep screenshot has no original paired pose. Recordings reach
about 20 degrees and do not reproduce that extreme view.

The new candidate curves only the posterior Y coordinates downward, preserving
original X/Z, optical/proximal geometry, nose configuration, attachment, camera,
visibility policy, original Z cutoffs and 15 mm fade. Its default maximum is 20 mm,
activated only when head/camera agree on down and tapered out with yaw. This is
an authored preview convention, not measured fit or a physical hinge.

A separate untouched native perfecto renderer now owns the final protected
optical/nasal pixels. This closes a weakness in earlier safeguards: moving opaque
arms can affect the main depth pass and later lens appearance even if the nose
mesh and internal transmission geometry are unchanged. Final integer composition
copies perfecto under full depth-independent optical/proximal bounds and a
central eye/nose guard, and everywhere outside bounded arm corridors. Invalid
candidate/replay/mask state falls back to perfecto. Through-lens and reflection
appearances retain baseline pixels; broad guards can limit the correction or
produce a join. Extra rendering/readback work has no frame-rate claim.

The completed recorded sweep contains 16 exact original pairs for each model.
Every before image matches an independently loaded original perfecto renderer;
original source/detection/poses, current Option17 surface, hidden optical footprint
coverage were checked, with eight cases exercising replay/ownership/failure paths. Independent
PNG auditing confirms all frozen nasal ROIs, 1,918,734 protected pixel samples and
all pixels outside arm corridors remain byte-exact. Twenty-six inactive controls
remain fully exact. Only Tom Ford dropout459 and flicker70 change, by 174 and 392
pixels; all 16 Amber outputs remain unchanged. Recorded improvement is not
established; the owner's subsequent positive live review is the acceptance basis.

Separate synthetic math and native renders show a changed rear path at 40–50
degrees down in both models, without widening and with upward/both-yaw controls
exact. Generated canonical images/landmarks/poses are not wearer recordings or
anatomical validation. No detector was run on the supplied overlaid screenshot.

The live comparison can hold the last displayed original image/detection pair,
stop camera/worker callbacks, switch its two outputs and explicitly download a
lossless diagnostic. Holding cannot overlap a recording. User live judgment,
including steep angles, joins, motion and nose appearance, remains decisive.
`npm test` passes 52 unit tests, eight browser flows, strict types, assets and
build. Candidate strict types, 14 unit tests and ten browser flows pass. The
preservation audit confirms all 29,180 recordings/earlier recovery files and 49
baseline tracked files remained byte-exact before the authorized checkpoint.
Detailed test/preservation receipts are recorded in the linked study report.

See [study report](../.recovery/temple-rethink-2026-09-08/REPORT.md),
[matched gallery](../experiments/temple-sagittal/comparison.html), and
[live comparison](../experiments/temple-sagittal/live.html). Earlier archives,
original recordings and unrelated model_studio remain outside this task's edits.
The authorized checkpoint includes this accepted implementation and its docs/tests.
Private generated galleries/recordings remain excluded. No parent Python change,
Railway deployment or unrelated model_studio work is included.

Hair remains ordinary camera background: there is no hair segmentation or
gender-specific rendering. Loose hair/bangs across glasses may have incorrect
foreground order; the temple fade only softens ends. Hair/ear coverage and
hairstyle-diverse live behavior remain unverified and unchanged by this acceptance.
