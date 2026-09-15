# MediaPipe hair segmentation weights

These two unmodified Google model binaries are redistributed under the Apache License, Version 2.0. The license statements appear on the first page of each official model card, checked on 2026-09-09. A complete license copy is in [LICENSE-2.0.txt](LICENSE-2.0.txt).

| Local file | Official model and attribution | License source |
| --- | --- | --- |
| `hair-only.tflite` | Google HairSegmenter, float32, download version 1. Model card credits Google researchers (2019) and requests citation: A. Tkachenka et al., *Real-time Hair segmentation and recoloring on Mobile GPUs*, CVPR Workshop on Computer Vision for Augmented and Virtual Reality, 2019. | [Hair segmentation model card](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20-%20Hair%20Segmentation.pdf) |
| `selfie-multiclass.tflite` | Google SelfieMulticlass 256×256, float32, download version 1. Model card author: Adel Ahmadyan, Google; model date May 10, 2023. | [Multiclass segmentation model card](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Multiclass%20Segmentation.pdf) |

[manifest.json](manifest.json) records the exact versioned official download URLs, SHA-256 hashes, byte lengths, embedded labels, model input dimensions and application serving paths. Both binary files are byte-for-byte identical to the reviewed weights. Their local filenames are aliases; no model conversion or retraining was performed.

[MediaPipe Image Segmenter documentation](https://ai.google.dev/edge/mediapipe/solutions/vision/image_segmenter) describes the models and category output. Hair has zero-based category index 1 in both. A category prediction is not physical hair depth or an opacity matte. Thin strands, headwear, difficult lighting and motion can reduce mask quality; the cards describe the individual models' intended use and limitations.

`node scripts/prepare-hair-assets.mjs` checks the tracked files offline. `node scripts/prepare-hair-assets.mjs --download` restores missing weights from the pinned Google URLs, verifying length and hash before writing. It never overwrites a present mismatched file. Inference loads these assets from the app's own origin; it does not download weights from Google during camera use.
