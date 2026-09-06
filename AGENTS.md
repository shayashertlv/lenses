# Working in Lenses

- Read README.md and HANDOFF.md first. Current mirror is the accepted baseline.
- The repository root is the sole active application, promoted from ar_v4.
- Keep changes inside this repository. Leave linked worktrees and local recovery
  files alone unless the owner requests changes to them.
- Preserve exact image/detection/pose pairing and explicit session ownership.
- Keep strict TypeScript and real Three.js types. Process frames locally.
- Review geometry with known coordinates and rendered output; review lifecycle
  under cancellation, failure, stop and restart. Run npm test for behavior changes.
- Record concise current findings/corrections in docs/REVIEWS.md. Historical
  experiments and reviews belong only in the recovery archive.
- Preserve recordings byte-for-byte. Git is not their backup.
- A generic face or synthetic test is not a personal scan, measured fit, or proof
  of anatomical accuracy and realistic motion. Keep empirical unknowns explicit.
- Research on large-yaw nose occlusion is pending. Do not restart reconstruction
  experiments or tune to the wearer without a new request. Future personalization
  must retain this baseline and earn acceptance on matched wearer recordings.
