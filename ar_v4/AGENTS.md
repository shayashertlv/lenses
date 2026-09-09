# Working in ar_v4

- Read README.md and HANDOFF.md first. The owner explicitly selected G Combined
  as the current base on September 9, 2026 after comparing its live preview.
  Preserve its implementation and keep future optimizations separate until reviewed.
  Test 2 (`8baa16c`) and earlier accepted visual checkpoints remain available.
- The owner accepted `perfecto long hair but slow` on September 9, 2026.
  Preserve that checkpoint and the earlier `perfect_temples` reference
  (`b26b5584c0dccbc2b30e4f12cdd432f10df577ea`). Build and review future candidates
  separately; keep both accepted entry points and references available. Promotion
  needs matched evidence and the owner's visual acceptance. Passing automated
  tests or synthetic still checks alone does not establish no visual regression.
- Include both frame models, downward/upward and both yaw controls, and nose/front
  checks in evaluations that could affect AR rendering. State missing live or
  recorded evidence explicitly; do not silently relax existing protections.
- The long-hair checkpoint is visually accepted with known speed limitations.
  Mobile smoothness is unmeasured. Optimize separately and retain exact image/pose
  pairing and final nose/front safeguards; do not infer approval from speed alone.
- This is a development app inside the live Lenses repository. Keep AR work inside
  ar_v4; the parent Python application and Railway deployment are separate.
- Do not promote AR to the root or expose it through the live demo. Leave linked
  worktrees and local recovery files alone unless the owner requests changes.
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
