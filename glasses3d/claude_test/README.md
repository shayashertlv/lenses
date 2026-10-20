# M13 Claude test

This personal runner uses Anthropic `claude-opus-5-5` at max effort with the
existing glasses tools. All output stays under `claude_test/work/`. The root
application, Astra runner and owner's dev UI remain independent.

Use the private environment from the repository root:

```powershell
$py = "glasses3d/claude_test/.venv/Scripts/python.exe"
& $py -m glasses3d.claude_test --help
```

Prepare a fresh copy containing only the verified owner manifest and its photos.
The example amount is a planning input, not permission to spend. Replace it with
the amount the owner chooses. Preparation starts no worker, reads no key and sends
no request.

```powershell
& $py -m glasses3d.claude_test prepare `
  --source glasses3d/data/jobs/oakley-oo9208-v2-911a2b0e `
  --cutoff 20
```

The result names `job_dir`, `run_id` and the execution-request hash. A destination
already containing a copy is refused. Use `--destination` under the work folder
for a separate fresh run; its final directory must retain the source job ID.

The free live check accepts that private job directory and the owner's exact
words authorizing uploads and counting:

```powershell
& $py -m glasses3d.claude_test livecheck <job_dir> `
  --env-file glasses3d/claude_test/.env --owner-ok "<owner's exact words>"
```

It uploads the six native owner photos with seven-day expiry, reads their metadata,
counts the opening request and deletes its uploads. It starts no Blender worker.
The transport refuses all Messages inference calls, including accidental calls;
the live check has no paid approval and cannot spend. To match a prepared run's
opening text exactly, add `--run-id <run_id> --cutoff <planned_amount>`. Without
those options, it uses a fresh check ID and the ordinary $20 planning default.
These options grant no spending permission.

`work/livecheck/<id>/livecheck.json` records the owner words, token count, upload
and metadata responses, deletion outcomes, HTTP statuses and request IDs. It also
records the complete opening and its counting projection, replacing inline image
bytes with hashes. Counting uses the same instructions, effective tools, brief and
request settings as the runner, with base64 photos and `oversized_image: "error"`.
Fields absent from the pinned SDK's counting schema are listed with their values:
currently `max_tokens`, `stream`, `service_tier` and `inference_geo`. Counting
cannot validate file-ID inference or prompt-cache hits. A failed free step still
deletes successfully uploaded files. Failed deletions are reported once with the
expiry backup and make the command return a failure exit code.

Only after the owner explicitly approves that run and its cost estimate, record
the owner's exact words, model and cutoff:

```powershell
& $py -m glasses3d.claude_test approve --job-dir <job_dir> --run-id <run_id> `
  --model claude-opus-5-5 --effort max --cutoff <approved_amount> `
  --words "<owner's exact approval>"
& $py -m glasses3d.claude_test run --job-dir <job_dir> --run-id <run_id> `
  --env-file glasses3d/claude_test/.env
```

The owner supplies the key file. Only authorized production execution loads it;
prepare, approval, status, stop and review do not. The approval is single use and
binds the inputs, instructions, tools and settings. Changed inputs or a partial
approval seal refuse. An offline fixture approval cannot start production.

Money is settled only: a request can start while settled spend is below the
cutoff, its reply may cross it, and then no further request starts. Unknown charges
stop spending. Parent and specialist share one ledger. Status shows settled spend,
the cutoff and money left, with no reserved amount or projected finish cost.
After every ending, the runner closes admission and deletes this run's uploads,
including specialist images. Receipts and the summary report successful deletions
and any failures with their expiry time. Failed deletes leave the original run
status intact; every upload also has seven-day expiry.

```powershell
& $py -m glasses3d.claude_test status --job-dir <job_dir> --run-id <run_id>
& $py -m glasses3d.claude_test stop --job-dir <job_dir> --run-id <run_id>
& $py -m glasses3d.claude_test history
& $py -m glasses3d.claude_test review --job-dir <job_dir> --run-id <run_id>
```

Review prints a personal page URL with the provider/model, owner photos, delivered
evidence, run summary and a link to the verified immutable AR model. Keep the
command running; Ctrl+C closes only the review servers it created. It exposes no
paid or edit endpoint and does not touch port 8260. A finish certifies the existing
technical procedure; appearance still needs the owner's judgement. Drafts retain
their actual unfinished status. No command records an owner verdict.

The script command creates a separate fresh copy and can use only a fake HTTP
transport behind the real SDK:

```powershell
& $py -m glasses3d.claude_test scripted --source <source_job> `
  --destination <work_path/source_job_id> --cutoff 1 --script <turns.json>
```

`turns.json` is an array of `ScriptTurn` fields from `scripted.py`, such as
`content`, `stop_reason` and optional fake `usage`. No key is loaded, external
network access is blocked, and the fixture records an offline-only approval.
Model receipts, append-only history, image provenance and the shared settled
ledger remain available under the run directory. An offline successful finish
is a tool test, never a new product verdict. C3's real-tool proof entry point is
`python -m glasses3d.claude_test.proof_c3`.

Run the isolated unit suite:

```powershell
& $py -m pytest -c glasses3d/claude_test/pytest.ini glasses3d/claude_test/tests -q -n 14
```

The normal glasses3d test suite does not collect these tests. The separate C4
`livecheck` command calls `count_tokens`; the paid runner never counts during a run. Every
inference uses file IDs, native pixels or overlapping native tiles, and explicit
reject-resize settings. Context forecasts are estimates anchored to settled
usage. Server clears preserve local history; renewal keeps the owner photos and
current evidence while starting a fresh signed-history epoch.
