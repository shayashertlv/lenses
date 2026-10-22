"""Read-only owner list metadata from recorded provenance and exact asset verdicts."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def read_object(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def marked_test(record):
    """Only structured provenance is evidence; names and descriptions are not."""
    if not isinstance(record, dict):
        return False
    if any(record.get(key) is True for key in
           ("offline_test_mode", "tool_test", "imported_test_fixture", "test_fixture", "scripted")):
        return True
    if record.get("proof_root"):
        return True
    if record.get("scope") == "tool_test" or record.get("origin") == "imported_test_fixture":
        return True
    if record.get("kind") == "imported_fixture_glb":
        return True
    # Imported fixture provenance is retained in delivery source/checkpoint
    # metadata, even when the fixture is moved outside the proof directory.
    if any(marked_test(record.get(key)) for key in ("source", "checkpoint", "source_checkpoint", "exit_checkpoint")):
        return True
    client = record.get("client")
    kind = client.get("kind") if isinstance(client, dict) else client
    return kind in {"offline", "scripted", "test", "replay"}


def proof_location(root, config):
    root = Path(root).resolve()
    data = config.repo_root / "glasses3d/data"
    return any(root.is_relative_to(path.resolve()) for path in
               (data / "m12/proofs", data / "assembly", config.repo_root / "glasses3d/tests"))


def job_is_test(job, config, runs=(), deliveries=()):
    root = Path(job["root"])
    if marked_test(job) or marked_test(read_object(root / "job.json")) or proof_location(root, config):
        return True
    # A product job can also contain a later tool-test copy. Its real run keeps
    # the product job visible; that copy is filtered independently below.
    recorded = [read_object(path) for path in (root / "runs").glob("*/run-config.json")]
    recorded += [row for row in runs if row.get("kind") == "job" and
                 Path(row["root"]).resolve() == root.resolve()]
    if recorded:
        return all(marked_test(row) for row in recorded)
    reports = [read_object(Path(row["root"]) / "deliveries" / row["delivery_id"] / "report.json")
               for row in deliveries if Path(row["root"]).resolve() == root.resolve()]
    return bool(reports) and all(marked_test(row) for row in reports)


def delivery_is_test(record, report, config, *, parent_test=False, _seen=None):
    root = Path(record["root"])
    if parent_test or marked_test(record) or marked_test(report) or proof_location(root, config):
        return True
    if marked_test(read_object(root / "job.json")):
        return True
    run_id = report.get("run_id")
    if isinstance(run_id, str) and Path(run_id).name == run_id:
        if marked_test(read_object(root / "runs" / run_id / "run-config.json")):
            return True
    # Owner looks inherit this exact source's provenance, never that of an
    # unrelated offline run in the same product job. Read only authenticated
    # immutable references; this also covers edits of previous owner edits.
    seen = set() if _seen is None else _seen
    identity = (str(root.resolve()), record["delivery_id"])
    if identity in seen or len(seen) >= 32:
        return False
    seen.add(identity)
    binding = report.get("owner_applied") or {}
    if binding.get("path") != "owner-applied/report.json":
        return False
    try:
        payload = (root / "deliveries" / record["delivery_id"] / binding["path"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != binding.get("sha256"):
            return False
        application = json.loads(payload)
        sources = [(application.get("owner_lens_edit") or {}).get("source"),
                   (application.get("owner_look_edit") or {}).get("native_source")]
        for source in sources:
            if not isinstance(source, dict):
                continue
            source_root = Path(source.get("root") or source["job"])
            source_id = source["delivery_id"]
            if not isinstance(source_id, str) or Path(source_id).name != source_id:
                continue
            directory = source_root / "deliveries" / source_id
            manifest_bytes = (directory / "manifest.json").read_bytes()
            if hashlib.sha256(manifest_bytes).hexdigest() != source["manifest_sha256"]:
                continue
            manifest = json.loads(manifest_bytes)
            report_bytes = (directory / "report.json").read_bytes()
            if (manifest["glb_sha256"] != source["glb_sha256"] or
                    hashlib.sha256(report_bytes).hexdigest() != manifest["files"]["report.json"]["sha256"]):
                continue
            if delivery_is_test({"root": str(source_root), "delivery_id": source_id},
                                json.loads(report_bytes), config, _seen=seen):
                return True
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return False


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def version_order(row):
    return (row.get("owner_verdict") == "accepted", timestamp(row.get("created_utc")), row["delivery_key"])


def _global_verdicts(path):
    try:
        rows = [json.loads(line) for line in Path(path).read_bytes().split(b"\n") if line.strip()]
        return [row for row in rows if isinstance(row, dict)]
    except (OSError, ValueError):
        return []


class VersionLabels:
    """Per-request snapshot: verdict changes are visible without restarting the UI."""
    def __init__(self, service):
        self.service = service
        self.global_rows = _global_verdicts(service.config.repo_root / "glasses3d/data/ledger/owner_verdicts.jsonl")
        self.local_rows = {}

    def verdict(self, record, dto):
        root = Path(record["root"])
        if root not in self.local_rows:
            # This checks the append-only chain and its head receipt without
            # creating a lock file in an existing job. Exact delivery binding is
            # checked below against the authenticated UI delivery.
            from glasses3d.agent.verdicts import _read
            try:
                self.local_rows[root] = _read(root / "reviews/verdicts.jsonl")
            except (OSError, ValueError):
                self.local_rows[root] = []
        matches = []
        for row in [*self.global_rows, *self.local_rows[root]]:
            if (row.get("glb_sha256") != dto["glb_sha256"] or row.get("scope", "product") != "product"
                    or marked_test(row) or row.get("verdict") not in {"accept", "accepted", "changes", "reject", "rejected"}):
                continue
            if row.get("origin") == "live":
                manifest = root / "deliveries" / record["delivery_id"] / "manifest.json"
                if (row.get("job_id") != root.resolve().name or row.get("delivery_id") != record["delivery_id"]
                        or not manifest.is_file()
                        or hashlib.sha256(manifest.read_bytes()).hexdigest() != row.get("manifest_sha256")):
                    continue
            matches.append(row)
        if not matches:
            return None
        latest = max(matches, key=lambda row: (timestamp(row.get("recorded_at") or row.get("date")), row.get("sequence", 0)))
        # "changes" is the owner asking for changes, not a rejection; keep his meaning in the label.
        return ("accepted" if latest["verdict"] in {"accept", "accepted"} else
                "changes requested" if latest["verdict"] == "changes" else "rejected")

    def summary(self, record, dto, name, *, test_job=False, swatch=False):
        verdict = self.verdict(record, dto)
        status = f"{verdict} by you" if verdict else (
            "finished" if dto.get("finished") else "not finished")
        date = timestamp(dto.get("created_utc"))
        date_label = date.strftime("%Y-%m-%d %H:%M UTC") if date.year > 1 else "date unavailable"
        origin = dto["version_label"]
        result = {key: dto[key] for key in ("delivery_key", "job_id", "created_utc")}
        result.update(job_name=name, origin_label=origin, owner_verdict=verdict, verdict_label=status,
                      test_job=test_job, label=f"{name} — {date_label} — {origin} — {status}")
        if swatch:
            result["lens_swatch"] = self.swatch(record, dto)
        return result

    def swatch(self, record, dto):
        cache = self.service.__dict__.setdefault("_list_swatches", {})
        digest = dto["glb_sha256"]
        if digest not in cache:
            try:
                model = self.service.get("deliveries", record["key"])["model"]
                rows = self.service.core.read_look_materials(Path(model))["materials"]
                cache[digest] = lens_swatch(rows)
            except (OSError, ValueError, KeyError, AttributeError):
                cache[digest] = None
        return cache[digest]


def unique_materials(rows):
    result, seen = [], set()
    for row in sorted(rows, key=lambda row: ("lens", "frame", "temple").index(row["role"])):
        if row["material_index"] not in seen:
            result.append(row)
            seen.add(row["material_index"])
    return result


def _hex(linear):
    rgb = [max(0., min(1., float(value))) for value in linear]
    srgb = [12.92 * value if value <= .0031308 else 1.055 * value ** (1 / 2.4) - .055 for value in rgb]
    return "#" + "".join(f"{int(value * 255 + .5):02x}" for value in srgb)


def lens_swatch(rows):
    lenses = [row for row in unique_materials(rows) if row["role"] == "lens"]
    if not lenses:
        return None
    gradient = lenses[0].get("gradient") or lenses[0].get("values", {}).get("gradient")
    if gradient:
        from glasses3d.agent.owner_look_gradient import sample_gradient
        positions = [i / 20 for i in range(21)]
        sample = [abs(2 * x - 1) for x in positions] if gradient["direction"] == "centre_to_sides" else positions
        colors = [_hex(rgb) for rgb in sample_gradient(gradient, sample)]
        direction = gradient["direction"]
    else:
        colors = [_hex(row["values"]["tint"]) for row in lenses]
        positions = [i / max(1, len(colors) - 1) for i in range(len(colors))]
        direction = "left_to_right"
    stops = ", ".join(f"{color} {position * 100:g}%" for color, position in zip(colors, positions))
    angle = "to bottom" if direction == "top_to_bottom" else "to right"
    if direction == "around_point":
        # List chips are 22 px high. Use equal pixel radii for Circle even when
        # the Copy and Versions chips have different widths. This is a colour
        # summary, not an optical render or a model-specific lens projection.
        x, y = gradient["centre"]
        shape = f"ellipse {10 * gradient['roundness']:g}px 10px at {50 + 50 * x:g}% {50 - 50 * y:g}%"
        css = f"radial-gradient({shape}, {stops})"
    else:
        css = colors[0] if len(colors) == 1 else f"linear-gradient({angle}, {stops})"
    return {"colors": colors, "positions": [x * 100 for x in positions], "direction": direction,
            "css": css}
