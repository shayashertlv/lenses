"""Receipted Codex-as-Astra lens trials. These are lab observations, never deliveries.

No provider SDK is used here. Codex must already be signed in through ChatGPT.
Trials run serially because their tool calls can start Blender and Chromium.
"""
from __future__ import annotations

import argparse
import ast
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import unquote, urlsplit
import uuid


REPO = Path(__file__).resolve().parents[2]
SCHEMA = "g3d.lens_lab.codex_trial/1"
LAB_TOOLS = (
    "execute_blender_code", "compare_look_to_photos", "render_views", "read_image",
    "read_report", "save_checkpoint", "restore_checkpoint", "list_checkpoints", "scene_summary", "export_glb",
    "arview_capture", "finish",
)
RENDER_TOOLS = frozenset(("compare_look_to_photos", "render_views", "arview_capture"))
API_KEY_NAMES = frozenset(("OPENAI_API_KEY", "CODEX_API_KEY", "AZURE_OPENAI_API_KEY",
                           "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def lab_path(path: Path | str, repo: Path = REPO) -> Path:
    root = (repo / "glasses3d/data/lens_lab").resolve()
    target = Path(path).resolve()
    if target == root or not target.is_relative_to(root):
        raise ValueError("trial output must be below glasses3d/data/lens_lab")
    return target


def require_task17_exit(repo: Path = REPO) -> str:
    """Refuse before init can start Blender; the coordinator controls scheduling."""
    status = repo / "glasses3d/data/review_20261018/codex/task17_status.txt"
    text = status.read_text(encoding="utf-8-sig") if status.is_file() else ""
    if not re.search(r"(?im)^\s*exit\b", text):
        raise RuntimeError("Heavy work is waiting: task17_status.txt has no exit line.")
    return text


def git_bytes(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True).stdout


def head_prompt_sources(repo: Path, job: dict) -> dict:
    """Evaluate the production builder from one immutable HEAD, not the dirty tree.

    The main registry consists of CORE_GUIDANCE plus SPECIALIST_GUIDANCE. Passing
    name-only stand-ins invokes the actual builder and all its two-way contracts,
    without constructing a paid runtime or starting a scene.
    """
    head = git_bytes(repo, "rev-parse", "HEAD").decode("ascii").strip()
    paths = ("glasses3d/agent/brief.py", "glasses3d/agent/instructions.py")
    sources = {path: git_bytes(repo, "show", f"{head}:{path}") for path in paths}
    brief_ns: dict[str, Any] = {"__name__": "lens_lab_head_brief"}
    exec(compile(sources[paths[0]], paths[0] + "@" + head, "exec"), brief_ns)
    instructions_ast = ast.parse(sources[paths[1]])
    # Bind the production import to the SAME commit's brief, avoiding import of a
    # concurrently edited working-tree module. No prompt string is rewritten.
    imports = [node for node in instructions_ast.body
               if isinstance(node, ast.ImportFrom) and node.module == "brief" and node.level == 1]
    if (len(imports) != 1 or [(a.name, a.asname) for a in imports[0].names]
            != [("MODEL_REQUIREMENTS", None)]):
        raise ValueError("production instruction dependency changed; review HEAD extraction")
    instructions_ast.body.remove(imports[0])
    instructions_ns = {"__name__": "lens_lab_head_instructions",
                       "MODEL_REQUIREMENTS": brief_ns["MODEL_REQUIREMENTS"]}
    exec(compile(instructions_ast, paths[1] + "@" + head, "exec"), instructions_ns)
    names = sorted(set(instructions_ns["CORE_GUIDANCE"]) | set(instructions_ns["SPECIALIST_GUIDANCE"]))
    main = instructions_ns["build_instructions"]([SimpleNamespace(name=name) for name in names])
    brief = brief_ns["build_brief"](job)
    return {"head": head, "instructions": main, "brief": brief, "main_tool_names": names,
            "source_sha256": {path: sha256(source) for path, source in sources.items()}}


def load_trial_job(trial: Path, manifest: dict) -> tuple[Path, dict, list[Path]]:
    job_dir = Path(manifest["job_dir"]).resolve()
    if not job_dir.is_relative_to(trial):
        raise ValueError("trial job must be inside this private trial")
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    if len(job["images"]) != 6:
        raise ValueError("L1 requires exactly six owner photos")
    photos = []
    for row in job["images"]:
        # Attach the original photo bytes. The source's sent_file is a transport
        # derivative, not a seventh photo or a source of added product hints.
        photo = (job_dir / row["file"]).resolve()
        if not photo.is_relative_to(job_dir) or photo.name.lower().startswith(".env"):
            raise ValueError("owner photo escaped the copied job")
        if not photo.is_file() or file_sha256(photo) != row["sha256"]:
            raise ValueError(f"owner photo integrity failed: {row['file']}")
        photos.append(photo)
    if len(set(photos)) != 6:
        raise ValueError("the six owner photo paths must be distinct")
    return job_dir, job, photos


def lab_paragraph(trial: Path, lens_materials: list[str], python: Path) -> str:
    """One paragraph of mechanics and lab scope; no material values or recipe."""
    cli = f"& '{str(python).replace(chr(39), chr(39) * 2)}' -m glasses3d.lens_lab"
    directory = str(trial).replace("'", "''")
    return (
        "LAB ONLY — this is a lens-material experiment on a private copy, not a product deliverable: "
        "change only the existing lens material, leave all geometry, other materials, frozen cameras and owner "
        f"inputs unchanged; the editable lens materials are {json.dumps(lens_materials, ensure_ascii=False)}. "
        f"Use the lens lab tool CLI from the repository: {cli} tool '{directory}' TOOL "
        f"--args-file '{directory}/arguments.json'; write the exact JSON argument object to that trial-local file. "
        f"available tools are {', '.join(LAB_TOOLS)}, and their argument schemas and real descriptions are in "
        f"'{directory}/tool-schemas.json'. "
        "Use execute_blender_code for lens material edits including g3d.lens_coating, and inspect the actual "
        "saved result images with your built-in view_image tool during the trial before choosing the final "
        "material; a path or numeric report alone is not image inspection. "
        "Run shell commands only to read that schema, invoke this trial's "
        "lab CLI, or write a JSON argument file inside this trial; do not edit source files, read other jobs or lab results, "
        "use the network, read .env or credentials, or call a provider API. "
        "All writes belong inside this trial directory. The lab replaces the full-product tasks above with "
        "lens material work only; end with the lab finish tool."
    )


def build_prompt(sources: dict, paragraph: str) -> str:
    if "\n\n" in paragraph or "\n" in paragraph:
        raise ValueError("the lab addition must be exactly one paragraph")
    return sources["instructions"] + "\n\n" + sources["brief"] + "\n\n" + paragraph


def codex_command(executable: list[str], repo: Path, photos: list[Path], output: Path) -> list[str]:
    # No model/effort override: inherit the installed Codex configuration.
    command = [*executable, "exec", "-s", "danger-full-access", "-C", str(repo),
               "--json", "--color", "never",
               "-c", "project_doc_max_bytes=0", "-c", 'forced_login_method="chatgpt"',
               "-c", 'model_provider="openai"', "-o", str(output)]
    # Keep the native rollout: 0.163 exec stdout deliberately omits ImageView.
    for photo in photos:
        command.extend(("-i", str(photo)))
    return [*command, "-"]


def discover_codex() -> list[str]:
    executable = shutil.which("codex.exe") or shutil.which("codex")
    if not executable:
        raise RuntimeError("Codex CLI is not on PATH")
    target = Path(executable)
    # Invoke npm's JS entrypoint with Node directly, avoiding Windows batch-shell
    # reinterpretation of paths or JSON arguments. Do not read the shim/config.
    entry = target.parent / "node_modules/@openai/codex/bin/codex.js"
    if entry.is_file():
        node = shutil.which("node")
        if not node:
            raise RuntimeError("the npm Codex installation requires Node")
        return [node, str(entry)]
    if target.suffix.lower() in (".cmd", ".bat", ".ps1"):
        raise RuntimeError("point PATH at the native Codex executable or its npm installation")
    return [str(target)]


def child_environment() -> dict[str, str]:
    # Membership is checked by name. No credential value is logged or loaded
    # from a file; ChatGPT login remains Codex's responsibility.
    return {**{key: value for key, value in os.environ.items()
               if key.upper() not in API_KEY_NAMES},
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1",
            "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}


def codex_preflight(executable: list[str], env: dict[str, str]) -> dict:
    def run(*args):
        return subprocess.run([*executable, *args], env=env, capture_output=True,
                              encoding="utf-8", errors="replace", check=False)
    auth = run("login", "status")
    # Only retain the auth mode, never the raw status response in a receipt.
    if auth.returncode or "logged in using chatgpt" not in (auth.stdout + auth.stderr).lower():
        raise RuntimeError("The $0 lab requires Codex signed in using ChatGPT, not an API key.")
    version = run("--version")
    if version.returncode:
        raise RuntimeError("Codex version check failed")
    return {"auth_mode": "ChatGPT", "version": version.stdout.strip()}


def read_jsonl(path: Path) -> tuple[list[dict], int]:
    events, invalid = [], 0
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("event is not an object")
                events.append(value)
            except (ValueError, TypeError):
                invalid += 1
    return events, invalid


def _completed_item(event: dict) -> dict | None:
    if event.get("type") == "item.completed":
        return event.get("item")
    if event.get("method") == "item/completed":
        return event.get("params", {}).get("item")
    if event.get("type") == "event_msg" and event.get("payload", {}).get("type") == "item_completed":
        item = event["payload"].get("item", {})
        if item.get("type") == "CommandExecution":
            command = item.get("command", [])
            return {**item, "type": "command_execution",
                    "command": " ".join(command) if isinstance(command, list) else command}
        return item
    return None


def native_rollout_evidence(events: list[dict], calls: list[dict], trial: Path, repo: Path,
                            env: dict[str, str], started_at: str, finished_at: str, *,
                            proof_path: Path | None = None) -> dict:
    """Read only this exec's native rollout; exec 0.163 JSON omits ImageView.

    The CLI keeps its own normal session outside the trial. Locate by the exact
    stdout thread UUID, then bind the first metadata record before reading any
    further records. Retain only image/finish proof, never transcript prose,
    reasoning, owner account metadata, or base64 image payloads.
    """
    result: dict[str, Any] = {"status": "unavailable", "transport": "native Codex exec rollout"}
    try:
        identifiers = {str(e.get("thread_id")) for e in events if e.get("type") == "thread.started"}
        if len(identifiers) != 1:
            raise ValueError("stdout must identify exactly one new thread")
        thread_id = identifiers.pop()
        if str(uuid.UUID(thread_id)) != thread_id.lower():
            raise ValueError("stdout thread ID is not a canonical UUID")
        sessions = (Path(env.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions").resolve()
        candidates = list(sessions.glob(f"*/*/*/rollout-*-{thread_id}.jsonl"))
        if len(candidates) != 1:
            raise ValueError("exact thread UUID must identify one native rollout")
        path = candidates[0].resolve()
        if not path.is_relative_to(sessions):
            raise ValueError("native rollout escaped the session root")
        digest = hashlib.sha256()
        selected, retained, image_calls, deliveries, open_exec = [], [], {}, {}, {}
        invalid = 0
        with path.open("rb") as stream:
            first = stream.readline()
            digest.update(first)
            meta_event = json.loads(first)
            meta = meta_event.get("payload", {})
            created = datetime.fromisoformat(meta.get("timestamp", "").replace("Z", "+00:00"))
            if (meta_event.get("type") != "session_meta" or meta.get("id") != thread_id
                    or event_path(meta.get("cwd", ""), repo) != repo.resolve()
                    or meta.get("source") != "exec"
                    or not datetime.fromisoformat(started_at) <= created <= datetime.fromisoformat(finished_at)):
                raise ValueError("native session identity, cwd, source, or launch time does not match this exec")
            for line_number, raw in enumerate(stream, 2):
                digest.update(raw)
                try:
                    event = json.loads(raw)
                    if not isinstance(event, dict):
                        raise ValueError("not an object")
                except (ValueError, TypeError):
                    invalid += 1
                    continue
                payload = event.get("payload", {})
                item = _completed_item(event) or {}
                kind = str(item.get("type", "")).replace("_", "").lower()
                keep = kind == "imageview" or is_trial_finish(item, trial, repo)
                if kind == "imageview" and len(open_exec) == 1:
                    outer_id, outer = next(iter(open_exec.items()))
                    if outer["unambiguous"] and outer["views_images"]:
                        view_id = item.get("id")
                        image_calls[view_id] = {"line": outer["line"],
                            "path": str(event_path(item["path"], repo)),
                            "transport": "code_mode", "outer_call_id": outer_id}
                        outer["view_ids"].append(view_id)
                if event.get("type") == "response_item" and isinstance(payload, dict):
                    call_id = payload.get("call_id")
                    if payload.get("type") == "custom_tool_call" and payload.get("name") == "exec":
                        # No JS evaluation or guessed inner IDs. Only one live
                        # outer call can own the completed native view interval.
                        unambiguous = not open_exec
                        for outer in open_exec.values():
                            outer["unambiguous"] = False
                        source = payload.get("input", "")
                        views_images = isinstance(source, str) and bool(re.search(r"\btools\.view_image\s*\(", source))
                        open_exec[call_id] = {"line": line_number, "unambiguous": unambiguous,
                                             "views_images": views_images, "view_ids": []}
                        keep = views_images
                    if payload.get("type") == "function_call" and payload.get("name", "").split(".")[-1] == "view_image":
                        try:
                            arguments = json.loads(payload.get("arguments", "{}"))
                            image_calls[call_id] = {"line": line_number,
                                                   "path": str(event_path(arguments["path"], repo))}
                            keep = True
                        except (ValueError, TypeError, KeyError):
                            pass
                    output_kind = payload.get("type")
                    if (output_kind == "function_call_output" and call_id in image_calls) or (
                            output_kind == "custom_tool_call_output" and call_id in open_exec):
                        output = payload.get("output")
                        images = [row["image_url"] for row in output if isinstance(row, dict)
                                  and row.get("type") == "input_image"
                                  and isinstance(row.get("image_url"), str)
                                  and row["image_url"].startswith("data:image/")] if isinstance(output, list) else []
                        delivery = {"line": line_number, "call_id": call_id,
                                                  "native_line_sha256": sha256(raw),
                                                  "image_payload_sha256": [sha256(v.encode()) for v in images]}
                        if output_kind == "custom_tool_call_output":
                            outer = open_exec.pop(call_id)
                            decoded = []
                            for image in images:
                                try:
                                    header, data = image.split(",", 1)
                                    if header.endswith(";base64"):
                                        decoded.append(sha256(base64.b64decode(data, validate=True)))
                                except ValueError:
                                    pass
                            if outer["unambiguous"] and decoded:
                                for view_id in outer["view_ids"]:
                                    deliveries[view_id] = {**delivery, "transport": "code_mode",
                                                          "decoded_image_sha256": decoded}
                        elif images:
                            deliveries[call_id] = delivery
                if keep:
                    selected.append(event)
                    retained.append({"native_line": line_number, "native_line_sha256": sha256(raw),
                                     "event": event})
        proof = image_view_evidence(selected, calls, trial, repo)
        finish_lines = [retained[i]["native_line"] for i in proof["finish_event_indices"]]
        for row in proof["render_image_views"]:
            view_line = retained[row["event_index"]]["native_line"]
            image_call, delivery = image_calls.get(row["item_id"]), deliveries.get(row["item_id"])
            row["native_line"] = view_line
            row["image_payload_delivered"] = bool(image_call and delivery
                and image_call["path"].casefold() == row["path"].casefold()
                and image_call["line"] < view_line <= delivery["line"]
                and any(end > delivery["line"] for end in finish_lines))
            if image_call and image_call.get("transport") == "code_mode":
                # Resized/transcoded payloads remain visible native views, but
                # are NOT accepted by this strict byte-exact code-mode proof.
                row["outer_call_id"] = image_call["outer_call_id"]
                row["payload_byte_exact"] = bool(delivery and row["sha256"] in delivery["decoded_image_sha256"])
                row["image_payload_delivered"] &= row["payload_byte_exact"]
        proof["verified"] = any(row["bytes_verified"] and row["before_finish"]
                                and row["image_payload_delivered"] for row in proof["render_image_views"])
        proof["criterion"] += "; matching native view_image output delivered image payload before finish"
        proof_path = (proof_path or trial / "codex/native-image-proof.json").resolve()
        if not proof_path.is_relative_to(trial) or proof_path.exists():
            raise ValueError("native proof requires a new path inside this trial")
        proof_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(proof_path, {"thread_id": thread_id, "records": retained,
                               "image_deliveries": list(deliveries.values())})
        result.update(status="collected", thread_id=thread_id, source_path=str(path),
                      rollout_sha256=digest.hexdigest(), created_at=meta["timestamp"],
                      proof_path=str(proof_path), proof_sha256=file_sha256(proof_path),
                      invalid_jsonl_lines=invalid, image_viewing=proof)
    except (OSError, ValueError, TypeError, KeyError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
    return result


def is_trial_finish(item: dict, trial: Path, repo: Path) -> bool:
    """Recognize the finish tool and its exact private trial argument."""
    if item.get("type") != "command_execution" or item.get("exit_code") != 0:
        return False
    pattern = (r"(?<!\S)-m\s+glasses3d\.lens_lab\s+tool\s+"
               r"(?:'(?P<single>(?:[^']|'')*)'|\"(?P<double>[^\"]*)\"|(?P<plain>\S+))\s+finish(?:\s|$)")
    match = re.search(pattern, item.get("command", ""))
    if match is None:
        return False
    raw = next(value for value in match.groupdict().values() if value is not None).replace("''", "'")
    try:
        return event_path(raw, repo) == trial.resolve()
    except ValueError:
        return False


def event_path(raw: str, repo: Path) -> Path:
    """Codex 0.163 serializes local AppPath values as file:// URIs on Windows."""
    parsed = urlsplit(raw)
    if parsed.scheme.lower() == "file":
        if parsed.netloc.lower() not in ("", "localhost") or parsed.query or parsed.fragment:
            raise ValueError("event path is not a plain local file URI")
        raw = unquote(parsed.path)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:/", raw):
            raw = raw[1:]
    elif parsed.scheme and not re.match(r"^[A-Za-z]:[\\/]", raw):
        raise ValueError("event path is not a local filesystem path")
    path = Path(raw)
    return (path if path.is_absolute() else repo / path).resolve()


def image_view_evidence(events: list[dict], calls: list[dict], trial: Path, repo: Path) -> dict:
    """Accept completed native image events, not agent prose or view commands.

    A result must be a real SHA-bound render image returned by a lab call, and
    the viewing event must precede the CLI finish. Attached owner photos cannot
    satisfy this proof.
    """
    generated = {}
    for call in calls:
        if call.get("tool") in RENDER_TOOLS:
            for row in call.get("images", []):
                path = Path(row["path"]).resolve()
                if path.is_relative_to(trial):
                    generated[str(path).casefold()] = row
    finish_indices = []
    for index, event in enumerate(events):
        item = _completed_item(event) or {}
        if is_trial_finish(item, trial, repo):
            finish_indices.append(index)
    evidence, other_views = [], []
    for index, event in enumerate(events):
        item = _completed_item(event) or {}
        kind = str(item.get("type", "")).replace("_", "").lower()
        if kind != "imageview" or item.get("status") in ("failed", "error"):
            continue
        raw = item.get("path")
        if not isinstance(raw, str):
            continue
        try:
            path = event_path(raw, repo)
        except ValueError:
            other_views.append({"event_index": index, "item_id": item.get("id"), "path": raw,
                                "unavailable": "not a local file path"})
            continue
        row = generated.get(str(path).casefold())
        check = {"event_index": index, "item_id": item.get("id"), "path": str(path)}
        if row is None:
            other_views.append(check)
            continue
        check.update(sha256=row.get("sha256"),
                     bytes_verified=path.is_file() and file_sha256(path) == row.get("sha256"),
                     before_finish=any(end > index for end in finish_indices))
        evidence.append(check)
    verified = any(row["bytes_verified"] and row["before_finish"] for row in evidence)
    return {"verified": verified, "render_image_views": evidence, "other_image_views": other_views,
            "finish_event_indices": finish_indices,
            "criterion": "completed native image view of a SHA-bound lab render before lab finish"}


def event_audit(events: list[dict], trial: Path, repo: Path) -> dict:
    commands, writes, outside = [], [], []
    for index, event in enumerate(events):
        item = _completed_item(event) or {}
        if item.get("type") == "command_execution":
            commands.append({"event_index": index, "command": item.get("command"),
                             "exit_code": item.get("exit_code")})
        if item.get("type") == "file_change":
            for change in item.get("changes", []):
                raw = change.get("path", "")
                try:
                    path = event_path(raw, repo)
                except ValueError:
                    row = {"event_index": index, "path": raw, "kind": change.get("kind"),
                           "unavailable": "not a local file path"}
                    writes.append(row)
                    outside.append(row)
                    continue
                row = {"event_index": index, "path": str(path), "kind": change.get("kind")}
                writes.append(row)
                if not path.is_relative_to(trial):
                    outside.append(row)
    return {"commands": commands, "file_changes": writes, "outside_trial_file_changes": outside,
            "limit": "danger-full-access is required by L1; prompt scope and harness validation are not "
                     "an OS sandbox. Shell writes are not exhaustively represented by file_change events."}


def implementation_hashes(repo: Path) -> dict[str, str]:
    """Fingerprint current relevant implementation, including uncommitted lab code."""
    paths = {}
    for relative in ("glasses3d/lens_lab", "glasses3d/agent", "glasses3d/g3d",
                     "glasses3d/g3d_host", "glasses3d/arview/src"):
        base = repo / relative
        if base.is_dir():
            for path in sorted(base.rglob("*")):
                if path.is_file() and path.suffix in (".py", ".ts") and "__pycache__" not in path.parts:
                    paths[path.relative_to(repo).as_posix()] = file_sha256(path)
    return paths


def _score_trial(trial_dir: Path) -> dict:
    # Import and call only after the Codex child has exited. Scores and accepted
    # baseline comparisons never become trial prompt/tool input.
    from .score import score_trial
    return score_trial(trial_dir)


def score_after_codex(trial_dir: Path, receipt_dir: Path) -> dict:
    started = time.monotonic()
    status = {"status": "running", "started_at": utc_now(), "not_sent_to_codex": True}
    status_path = receipt_dir / "scoring-status.json"
    write_json(status_path, status)
    try:
        result = _score_trial(trial_dir)
        score_dir = (trial_dir / "score").resolve()
        score_json = score_dir / "score.json"
        comparison = Path(result["comparison_image"]).resolve()
        if not comparison.is_relative_to(score_dir) or not comparison.is_file():
            raise ValueError("scorer did not produce a comparison image inside this trial's score folder")
        if file_sha256(comparison) != result["comparison_sha256"]:
            raise ValueError("scoring comparison image bytes changed")
        if json.loads(score_json.read_text(encoding="utf-8")) != result:
            raise ValueError("scoring JSON does not match the returned scoring result")
        status.update(status="completed", score_json=str(score_json), score_sha256=file_sha256(score_json),
                      comparison_image=str(comparison), comparison_sha256=result["comparison_sha256"])
    except Exception as error:
        # Retain the completed model trial while reporting scoring infrastructure
        # failure separately; a batch must not silently count it as complete.
        status.update(status="failed", error=f"{type(error).__name__}: {error}")
    status.update(finished_at=utc_now(), seconds=time.monotonic() - started)
    write_json(status_path, status)
    return status


def run_trial(trial_dir: Path | str, *, variant: str = "head", source_job: Path | str | None = None,
              repo: Path = REPO, executable: list[str] | None = None) -> dict:
    """Run once; reuse a pristine initialized trial, never an earlier Codex trial."""
    trial = lab_path(trial_dir, repo)
    task17 = require_task17_exit(repo)
    executable = discover_codex() if executable is None else executable
    env = child_environment()
    preflight = codex_preflight(executable, env)
    if (trial / "codex").exists():
        raise ValueError("this trial already has Codex evidence; choose a new directory")
    if not (trial / "trial.json").exists():
        from .harness import init_trial
        init_trial(trial, source_job=source_job)
    manifest = json.loads((trial / "trial.json").read_text(encoding="utf-8"))
    _, job, photos = load_trial_job(trial, manifest)
    if (trial / "calls.jsonl").exists() and (trial / "calls.jsonl").stat().st_size:
        raise ValueError("Codex trials must start at the neutral fixture without prior tool calls")
    sources = head_prompt_sources(repo, job)
    python = (repo / "glasses3d/.venv/Scripts/python.exe") if os.name == "nt" else Path(sys.executable)
    paragraph = lab_paragraph(trial, manifest["lens_materials"], python)
    prompt = build_prompt(sources, paragraph)
    output = trial / "codex"
    output.mkdir()
    for name, content in (("instructions.txt", sources["instructions"]), ("brief.txt", sources["brief"]),
                          ("lab-paragraph.txt", paragraph), ("prompt.txt", prompt)):
        (output / name).write_bytes(content.encode("utf-8"))
    command = codex_command(executable, repo, photos, output / "last-message.txt")
    implementation = implementation_hashes(repo)
    write_json(output / "implementation.json", implementation)
    receipt = {"schema": SCHEMA, "lab": True, "variant": variant, "trial_dir": str(trial),
               "status": "starting", "started_at": utc_now(), "provider_api_spend_usd": 0,
               "codex_usage_billed_separately": True, **preflight,
               "head": sources["head"], "source_sha256": sources["source_sha256"],
               "main_tool_names": sources["main_tool_names"],
               "prompt_sha256": sha256(prompt.encode("utf-8")),
               "instructions_sha256": sha256(sources["instructions"].encode("utf-8")),
               "brief_sha256": sha256(sources["brief"].encode("utf-8")),
               "lab_paragraph_sha256": sha256(paragraph.encode("utf-8")),
               "implementation_sha256": file_sha256(output / "implementation.json"),
               "owner_photos": [{"path": str(path), "sha256": file_sha256(path)} for path in photos],
               "command": command, "task17_status": task17,
               "model_selection": "inherited Codex configuration; no model or effort override"}
    write_json(output / "receipt.json", receipt)
    started = time.monotonic()
    try:
        with (output / "prompt.txt").open("rb") as stdin, (output / "events.jsonl").open("wb") as stdout, \
                (output / "stderr.log").open("wb") as stderr:
            receipt["codex_started_at"] = utc_now()
            process = subprocess.Popen(command, cwd=repo, env=env, stdin=stdin, stdout=stdout, stderr=stderr)
            receipt.update(status="running", pid=process.pid)
            write_json(output / "receipt.json", receipt)
            code = process.wait()  # No deadline: preserve healthy, slow model/tool work.
        codex_finished_at = utc_now()
        events, invalid = read_jsonl(output / "events.jsonl")
        calls, invalid_calls = read_jsonl(trial / "calls.jsonl")
        native = native_rollout_evidence(events, calls, trial, repo, env,
                                         receipt["codex_started_at"], codex_finished_at)
        view = native.get("image_viewing", {"verified": False, "reason": "native image proof unavailable"})
        audit = event_audit(events, trial, repo)
        finish_path = trial / "finish.json"
        finish = json.loads(finish_path.read_text(encoding="utf-8")) if finish_path.exists() else None
        finish_receipted = any(call.get("tool") == "finish" and call.get("result") == finish for call in calls)
        finished = bool(finish and finish.get("status") == "lab_finished" and finish.get("lab") is True
                        and finish.get("final_materials") and finish_receipted)
        after = implementation_hashes(repo)
        changed = [path for path in sorted(set(implementation) | set(after))
                   if implementation.get(path) != after.get(path)]
        write_json(output / "implementation-after.json", after)
        success = (code == 0 and finished and view["verified"] and invalid == 0 and invalid_calls == 0
                   and native.get("invalid_jsonl_lines") == 0
                   and not audit["outside_trial_file_changes"] and not changed)
        receipt.update(status="scoring" if success else "incomplete", exit_code=code,
                       codex_status="completed" if success else "incomplete",
                       codex_finished_at=codex_finished_at, codex_seconds=time.monotonic() - started,
                       codex_usage=[e.get("usage") for e in events if e.get("type") == "turn.completed"],
                       invalid_jsonl_lines=invalid, invalid_call_lines=invalid_calls,
                       tool_calls=calls, image_viewing=view, native_rollout=native, event_audit=audit,
                       final_materials=finish.get("final_materials") if finish else None,
                       finish=finish, finish_receipted=finish_receipted,
                       finish_sha256=file_sha256(finish_path) if finish else None,
                       events_sha256=file_sha256(output / "events.jsonl"),
                       implementation_after_sha256=file_sha256(output / "implementation-after.json"),
                       implementation_changed_paths=changed,
                       implementation_change_note="Any concurrent implementation edit invalidates a stable "
                                                  "trial; this check does not attribute the change to Codex.")
        write_json(output / "receipt.json", receipt)
        if success:
            scoring = score_after_codex(trial, output)
            receipt["status"] = "completed" if scoring["status"] == "completed" else "incomplete"
        else:
            scoring = {"status": "not_started", "not_sent_to_codex": True,
                       "reason": "Codex finish, image-view, or trial-integrity proof did not complete"}
            write_json(output / "scoring-status.json", scoring)
        receipt.update(scoring=scoring, finished_at=utc_now(), seconds=time.monotonic() - started)
    except Exception as error:
        receipt.update(status="failed", finished_at=utc_now(), seconds=time.monotonic() - started,
                       error=f"{type(error).__name__}: {error}")
        write_json(output / "receipt.json", receipt)
        raise
    write_json(output / "receipt.json", receipt)
    return receipt


def run_trials(*, variant: str, count: int, output_root: Path | None = None,
               source_job: Path | None = None, repo: Path = REPO) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", variant):
        raise ValueError("variant must be a short filename-safe label")
    if type(count) is not int or count < 1:
        raise ValueError("count must be a positive integer")
    require_task17_exit(repo)
    output_root = lab_path(output_root or repo / "glasses3d/data/lens_lab/trials", repo)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    batch = output_root / variant / (stamp + "-" + uuid.uuid4().hex[:8])
    batch.mkdir(parents=True)
    summary = {"schema": "g3d.lens_lab.codex_batch/1", "lab": True, "variant": variant,
               "requested_trials": count, "started_at": utc_now(), "trials": []}
    for index in range(1, count + 1):
        result = run_trial(batch / f"trial-{index:03d}", variant=variant, source_job=source_job, repo=repo)
        summary["trials"].append({"trial_dir": result["trial_dir"], "status": result["status"],
                                  "prompt_sha256": result["prompt_sha256"], "seconds": result["seconds"],
                                  "codex_status": result.get("codex_status"), "scoring": result.get("scoring")})
        write_json(batch / "batch.json", summary)
        # Infrastructure failures cannot be interpreted as material samples.
        if result["status"] != "completed":
            break
    summary.update(finished_at=utc_now(), completed_trials=sum(
        row["status"] == "completed" for row in summary["trials"]))
    write_json(batch / "batch.json", summary)
    return {**summary, "batch_dir": str(batch)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    batch = commands.add_parser("run", help="N serial trials of the current HEAD/working-code variant")
    batch.add_argument("--variant", required=True, help="label only; never injected into Astra's prompt")
    batch.add_argument("--count", type=int, default=1)
    batch.add_argument("--output-root", type=Path)
    batch.add_argument("--source-job", type=Path)
    one = commands.add_parser("run-one", help="one new or pristine initialized private trial")
    one.add_argument("trial_dir", type=Path)
    one.add_argument("--variant", default="head")
    one.add_argument("--source-job", type=Path)
    args = parser.parse_args(argv)
    if args.command == "run":
        result = run_trials(variant=args.variant, count=args.count, output_root=args.output_root,
                            source_job=args.source_job)
        ok = result["completed_trials"] == args.count
    else:
        receipt = run_trial(args.trial_dir, variant=args.variant, source_job=args.source_job)
        result = {key: receipt[key] for key in ("status", "trial_dir", "seconds", "prompt_sha256", "scoring")}
        ok = receipt["status"] == "completed"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
