# Superseded instructions and historical evidence

These snapshots preserve the state before the owner requested a WP6 session handoff on 2026-10-08.
They are evidence only. Do not execute their resume steps, revive stopped workflows, ask resolved questions,
or apply their old scope restrictions to the current task.

Current work: [PROGRESS](../../PROGRESS.md) and [WP6 v2 design](../../WP6_FACETS.md); the WP6 v1 handoff and
session prompt moved to [archive/20261010](../20261010/README.md) and were deleted there on 2026-10-13 (git tag
`review-20261013-pre-cleanup`). Historical local paths remain under the same repository.
Relative Markdown links inside snapshots refer to their original `plan/` location. `snapshots.json` records
the exact as-captured body hashes; only an archive warning was prepended. Git may normalize Markdown line
endings, so compare the body with `git show d5e5917:glasses3d/plan/<name>` after normalizing CRLF to LF when
verifying a fresh checkout. No measurements or evidence were removed.

2026-10-13 cleanup: `WP5_SESSION_PROMPT.md` (superseded instruction text) was deleted; read it with
`git show review-20261013-pre-cleanup:glasses3d/plan/archive/20261008/WP5_SESSION_PROMPT.md` (its `snapshots.json`
entry is kept and marked `deleted`). The banners' pointer to the non-existent `../../WP6_HANDOFF.md` was dropped;
`snapshots.json` carries the new `prefix_bytes` and `archive_sha256` (body hashes unchanged).
