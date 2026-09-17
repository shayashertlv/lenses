# Working in Lenses

- Read README.md first. The repository root is the live Lenses Python application
  deployed from `main` to Railway through `Procfile` (`python -m UI.app`).
- `ar/` is the AR try-on pipeline. Read ar/README.md for AR work. Keep its package
  manifests, assets and tests inside it (`cd ar && npm test`).
- The live `/ar/` route serves `ar/site/`, the committed output of `cd ar && npm run publish`,
  through `UI/ar_site.py` (manifest allowlist, SHA-256 checks, isolation headers).
  AR work must not replace the live root application or change its deployment.
- Old AR generations and recovery archives are kept outside the repository. Do not add
  local-only material (`ar/modeling/`, `ar_v4/` leftovers, private recordings, `.env`) to Git.
