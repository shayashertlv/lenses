# GitHub research: evaluation, data and inspection

Researched 2026-09-25. These are recommendations from repository/documentation inspection, not measured improvements on our products. No packages, weights or datasets were installed and no inference was run.

The local pipeline already has source hashes, immutable stage receipts, bidirectional contour metrics, held-out-photo reservation, AR render comparisons and a production validation schema. The opportunity is to make independent labels and failure comparisons easier to maintain, rather than replace those mechanisms. Relevant local code: `reconstruction/metrics.py`, `evaluation_reservation.py`, `production_validation.py`, `segmented_job.py`, and `bsa/ground_truth.py`. The latter explicitly describes its existing traces as `ai_visual_trace`; those should not silently become human-verified ground truth.

## Candidates

| Repository | Specific use for Lenses | Practicality and license | Recommendation |
|---|---|---|---|
| [voxel51/fiftyone](https://github.com/voxel51/fiftyone) | Browse original photos, predicted masks, source-part labels and final AR images together; filter by product family, boundary failure and review disagreement. | Python 3.10–3.13; documented Windows setup; Apache-2.0 core. Separate local environment and database. Basic data browsing needs no model GPU; model integrations have their own requirements. | Useful once comparisons become difficult to navigate. Import existing reports as observations, retaining our hashes and verdicts. Do not build a second job engine. |
| [cvat-ai/cvat](https://github.com/cvat-ai/cvat) | Produce reviewed lens/frame polygons, bridge/hinge landmarks and visibility flags for a fixed evaluation set. | MIT community code. Docker deployment adds operational work; optional automatic-labeling model assets have separate terms. | Preferred annotation option if we want a dedicated CV review workflow. Labels are evaluation assets, not per-product manual inference corrections. |
| [HumanSignal/label-studio](https://github.com/HumanSignal/label-studio) | Alternative annotation UI, especially flexible forms combining outlines, keypoints and a material/ambiguity questionnaire. | Apache-2.0 core; Python/Docker setup. External ML backends and their checkpoints must be considered separately. | Choose this OR CVAT, not both. Favor it if custom review forms matter more than a dedicated vision annotation workflow. |
| [DLR-RM/BlenderProc](https://github.com/DLR-RM/BlenderProc) | Generate known-camera scenes with exact masks, normals and materials to test whether our evaluator detects deliberately introduced defects. | GPL-3.0; runs inside Blender's Python environment. Rendering cost depends on backend and sample count. No trained model required for this use. | Useful offline test-data tool. Start with missing temples, widened bridges, filled apertures and reversed tint gradients. Our actual AR renderer must still verify delivered appearance. |
| [bowenc0221/boundary-iou-api](https://github.com/bowenc0221/boundary-iou-api) | External reference metric for segmentation boundaries; audit our existing rim/lens contour scoring. | BSD-2-Clause-style root license; bundled dataset evaluators have their own notices. Small experimental/beta repository with four visible commits, OpenCV required. | Reference/check, not a new mandatory runtime dependency. Our existing symmetric p95/max contour measurements already retain localized errors that a single overlap score can hide. |
| [nmwsharp/polyscope](https://github.com/nmwsharp/polyscope) | Inspect per-face role confidence, displacement, normal deviations and source correspondence on meshes. | MIT; C++/Python viewer; desktop graphics environment. No ML weights. | Optional development aid. Its display is not an oracle for our custom optical material or browser renderer. |
| [treeverse/dvc](https://github.com/treeverse/dvc) | Version growing evaluation datasets and reports outside Git, with small references alongside code. | Apache-2.0; Windows supported. The historical `iterative/dvc` URL currently redirects here. | Defer until dataset sharing/versioning is an actual bottleneck. Existing immutable journals already solve much of single-host provenance. A configured remote would be a separate action, not implicit in adoption. |

The relevant feature sources are [FiftyOne grouped datasets](https://docs.voxel51.com/user_guide/groups.html), [CVAT license and optional model caveat](https://github.com/cvat-ai/cvat#license), [Label Studio keypoint template](https://github.com/HumanSignal/label-studio/blob/develop/docs/source/templates/image_keypoints.md), [BlenderProc capabilities](https://github.com/DLR-RM/BlenderProc#features), [Boundary IoU paper](https://openaccess.thecvf.com/content/CVPR2021/papers/Cheng_Boundary_IoU_Improving_Object-Centric_Image_Segmentation_Evaluation_CVPR_2021_paper.pdf), and [Polyscope structure/quantity API](https://github.com/nmwsharp/polyscope).

## Maintenance snapshot

GitHub REST repository metadata was retrieved on 2026-09-25. `pushed_at` is activity on the repository, not necessarily a release or a change on its default branch. All seven entries below reported `archived: false`. This is a useful availability signal, not evidence of reliability or eyewear accuracy.

| Repository | Reported last push (UTC date) | Metadata license |
|---|---|---|
| voxel51/fiftyone | 2026-09-25 | Apache-2.0 |
| cvat-ai/cvat | 2026-09-25 | MIT |
| HumanSignal/label-studio | 2026-09-25 | Apache-2.0 |
| treeverse/dvc | 2026-09-21 | Apache-2.0 |
| DLR-RM/BlenderProc | 2026-01-20 | GPL-3.0 |
| nmwsharp/polyscope | 2026-09-06 | MIT |
| mantasu/glasses-detector | 2025-09-12 | MIT |

The root license texts for FiftyOne, CVAT, Label Studio and Boundary IoU were also read directly; metadata alone was not used to settle those licenses.

## Dataset and eyewear search: useful exclusions

- [mantasu/glasses-detector](https://github.com/mantasu/glasses-detector) is already represented by our pinned offline lens detector. Its repository includes frame, leg and lens tasks and links to multiple training sources. That makes it a candidate for broader component proposals, but the compiled training-source list does not give all those datasets one common license, and face-centric examples do not establish isolated-product accuracy.
- [wang-yating/EyeglassesReconstruction](https://github.com/wang-yating/EyeglassesReconstruction) advertises the ECCV 2020 method, but the inspected repository contains a README and teaser with Dataset/Install/Usage marked TODO. It is not a runnable implementation to adopt.
- [cleardusk/MeGlass](https://github.com/cleardusk/MeGlass) is a face-recognition evaluation dataset. It does not provide the catalog-product geometry truth we need.
- [StoryMY/take-off-eyeglasses](https://github.com/StoryMY/take-off-eyeglasses) provides portrait glasses/shadow-removal code and links to synthetic data. It could inform shadow/occlusion research, but removing glasses is a different objective, and no root license was visible in the inspected repository listing. Do not treat the linked assets as cleared training data.
- [3DOM-FBK/NeRFBK](https://github.com/3DOM-FBK/NeRFBK) has transparent-object benchmarks, but the listed glass/cup/bottle sequences are drinking vessels, usually hundreds of views. Its data are CC BY-NC-SA 4.0. It is not a replacement for commercial catalog eyewear evaluation.

## First evaluation experiment

Use the five existing products for development only. Add distinct held-out products spanning full-rim, rimless, semi-rimless and shield geometries, with clear, dark, gradient and mirror optics. Split by physical design; repeated runs, color variants and alternate encodings cannot cross the split. The existing plan's 12 development / 8 held-out proposal is a reasonable pilot size, not statistical certification.

Review labels independently, record uncertain boundaries, and keep them outside inference/provider inputs. Score missing frame, opaque lens-core contamination, boundary p95/max, landmark reprojection and review intervention separately. Include defect-injected controls to establish that the evaluator can detect known failures. Render the exact final GLB under the existing three lighting environments. A better synthetic score or a more attractive unmatched image is insufficient to promote a pipeline change.

Do not select a new heavyweight platform before this protocol needs one: a small adapter exporting existing JSON and image paths to a review tool is the useful first integration.
