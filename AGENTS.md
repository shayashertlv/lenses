# Working in Lenses

- Read README.md first. The repository root is the live Lenses Python application
  deployed from `main` to Railway through `Procfile` (`python -m UI.app`).
- `ar_v4/` is a separate development application. Read its AGENTS.md, README.md and
  HANDOFF.md for AR work. Keep its package manifests, assets and tests inside it.
- AR work must not replace the live root application or change its deployment.
  Do not expose AR routes through the live demo without an explicit new request.
- Preserve private recordings, verified recovery archives and linked worktrees.
  Local `.recovery/` material must remain excluded from Git and deployments.
