"""Bound model-facing diagnostics, with immutable, losslessly readable local originals.

This policy never changes a tool's internal result, images, journals or finish decision.
Only newly returned text is compacted; earlier conversation items are never rewritten.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import inspect
from typing import Literal

from agents.tool import ToolOutputText
from agents.tool_context import ToolContext

TEXT_LIMIT = 12_000
PAGE_CHARS = 8_000
REPORT_SCHEMA = "g3d.tool_report/1"
REPORT_DOC = """Read any saved full tool report without repeating its operation. Long outputs are summaries;
all original values remain here. Select a JSON pointer, page a table or filter rows. Text mode pages the exact
JSON serialization (or original string), including a single oversized item. No scene changes or model calls.
If a fact needed for the next decision has a read_pointer, retrieve that field before claiming what it says.
If the page still contains a pointer for that fact, it is still unread. Read the saved report rather than
rerunning the operation; unrelated diagnostic fields need not be paged.

Args:
    report: The full_report.id returned by a tool; only this job's saved reports are readable.
    pointer: JSON pointer inside the record, usually /result; escape ~ as ~0 and / as ~1.
    offset: Zero-based row/key index in json mode, or character index in text mode.
    limit: Maximum rows/keys (1 to 100) or characters (1 to 8000) requested.
    mode: json for structured pages, text for lossless character pages.
    filter_path: Optional JSON pointer relative to each row, or * to search its serialized contents.
    filter_value: Case-insensitive text to find at filter_path; null disables filtering.
"""
PRIORITY = ("status", "ok", "pass", "graded_pass", "code", "message", "reasons", "failures", "errors", "warnings", "summary",
            "lens_colour_gap", "lens_colour_judgement",
            "verdicts", "worst_gap", "worst_deficit", "stations", "required", "points", "verdict", "value",
            "checkpoint", "fingerprint", "sha256", "glb_sha256", "export_sha256", "call_id", "basis",
            "counts", "n_fail", "n_pass", "worst", "max", "min", "images", "image_receipt")


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _path(root, digest=None, *, create=False):
    base = Path(root).resolve()
    current = base
    for part in ("work", "tool_reports", *((digest + ".json",) if digest else ())):
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError("tool report paths cannot contain filesystem links")
        if not current.resolve().is_relative_to(base):
            raise ValueError("tool report path escaped the job")
    if create:
        (current.parent if digest else current).mkdir(parents=True, exist_ok=True)
    return current


def save_report(root, record):
    data = dumps(record).encode("utf8")
    digest = hashlib.sha256(data).hexdigest()
    path = _path(root, digest, create=True)
    try:
        with path.open("xb") as stream:
            stream.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError("saved tool report bytes changed")
    return {"id": digest, "path": path.relative_to(Path(root).resolve()).as_posix(),
            "sha256": digest, "bytes": len(data), "tool": "read_report", "pointer": "/result"}


def _pointer(base, key):
    return base + "/" + str(key).replace("~", "~0").replace("/", "~1")


def select(value, pointer):
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise ValueError("pointer must be empty or start with /")
    for part in pointer.split("/")[1:] if pointer else ():
        if re.search(r"~(?![01])", part):
            raise ValueError("invalid JSON pointer escape")
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", key) or int(key) >= len(value):
                raise ValueError("JSON pointer array index is absent")
            value = value[int(key)]
        elif isinstance(value, dict) and key in value:
            value = value[key]
        else:
            raise ValueError("JSON pointer is absent")
    return value


def _rank(row):
    if not isinstance(row, dict):
        return (0, 0)
    status = str(row.get("status", row.get("verdict", row.get("grade", "")))).lower()
    failing = any(row.get(key) is False for key in ("ok", "pass", "graded_pass")) or any(
        word in status for word in ("fail", "refus", "error", "unmeasured"))
    scores = [abs(value) for key, value in row.items() if type(value) in (float, int)
              and math.isfinite(value) and any(word in key.lower() for word in ("error", "residual", "miss", "overlap", "gap", "deficit"))]
    # Seating's required-station value is capture: the smallest value is the worst deficit.
    if row.get("required") is True and type(row.get("value")) in (int, float):
        scores.append(-row["value"])
    for value in row.values():
        if isinstance(value, dict):
            child = _rank(value)
            failing = failing or bool(child[0])
            scores.append(child[1])
    return (int(failing), max(scores, default=0))


def summarize(value, pointer="/result", *, width=6, levels=5, string_chars=500):
    """A deterministic sample: failures first, then largest explicit residuals; originals are never discarded."""
    if isinstance(value, str):
        return value if len(value) <= string_chars else {"characters": len(value), "preview": value[:string_chars],
                                                        "read_pointer": pointer, "mode": "text"}
    if not isinstance(value, (dict, list)):
        return value
    if levels <= 0:
        return {"kind": "object" if isinstance(value, dict) else "array", "count": len(value),
                "read_pointer": pointer}
    if isinstance(value, dict):
        # Stable sorting retains insertion order for equal priorities without quadratic index lookups.
        keys = sorted(value, key=lambda key: PRIORITY.index(key) if key in PRIORITY else len(PRIORITY))
        maximum = len(keys) if pointer == "/result" else max(12, width * 4)
        answer = {key: summarize(value[key], _pointer(pointer, key), width=width, levels=levels-1,
                                 string_chars=string_chars) for key in keys[:maximum]}
        if len(keys) > maximum:
            answer["_remaining"] = {"keys": len(keys) - maximum, "read_pointer": pointer}
        return answer
    if len(value) <= width:
        return [summarize(row, _pointer(pointer, index), width=width, levels=levels-1,
                          string_chars=string_chars) for index, row in enumerate(value)]
    chosen = sorted(range(len(value)), key=lambda index: _rank(value[index]), reverse=True)[:width]
    counts = Counter(str(row.get("status", row.get("verdict", row.get("grade", "unknown")))) for row in value if isinstance(row, dict))
    return {"count": len(value), "status_counts": dict(counts.most_common(12)), "read_pointer": pointer,
            "sample_order": "failures first, then largest explicit error/residual, then original order",
            "sample": [{"index": index, "value": summarize(value[index], _pointer(pointer, index), width=width,
                                                               levels=levels-1, string_chars=string_chars)}
                       for index in chosen]}


def _tables(value):
    found = {}
    def visit(item, pointer):
        if isinstance(item, dict):
            for key, child in item.items():
                visit(child, _pointer(pointer, key))
        elif isinstance(item, list):
            if len(item) > 12 and any(isinstance(row, dict) for row in item):
                digest = hashlib.sha256(dumps(item).encode()).hexdigest()
                if digest not in found:
                    found[digest] = summarize(item, pointer, width=3, levels=3, string_chars=120)
                else:
                    found[digest].setdefault("also_at", []).append(pointer)
            else:
                for index, child in enumerate(item):
                    visit(child, _pointer(pointer, index))
    visit(value, "/result")
    return sorted(found.values(), key=lambda item: item["count"], reverse=True)[:6]


def _body(text):
    try:
        value, end = json.JSONDecoder().raw_decode(text)
        return value, text[end:]
    except ValueError:
        return {"status": "reported", "text": text}, ""


def _short(value, limit):
    value = str(value)
    return value if len(value) <= limit else value[:max(0, limit - 1)] + "…"


def _image_label(text, record, ordinal, view_chars):
    """Keep identity fields intact; only the human-readable view may be abbreviated."""
    record = record if isinstance(record, dict) else {}
    digest = record.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{12,64}", digest):
        match = re.search(r"sha256 ([0-9a-f]{12,64})\b", text)
        digest = match.group(1) if match else None
    origin = record.get("reused_from_call") or record.get("call_id")
    reused = bool(record.get("reused_from_call"))
    if not origin:
        match = re.search(r"Same image already delivered in call (.*?)\.$", text, re.S)
        reused = bool(match)
        if not match:
            match = re.match(r"Image: call (.*?); checkpoint ", text, re.S)
        origin = match.group(1) if match else None
    view = record.get("view")
    if not isinstance(view, str):
        match = re.search(r"; view (.*?); (?:[^\n]*?; )?sha256 [0-9a-f]+", text, re.S)
        view = match.group(1) if match else text
    label = f"Image {ordinal}"
    if view_chars:
        label += ": " + _short(view, view_chars)
    if digest:
        label += f"; sha256 {digest[:12]}"
    if origin:
        label += f"; {'reused from' if reused else 'call'} {origin}"
    return label


def _must_show(body, *, width=2, levels=3, string_chars=120, page_refs=False, pointer="/result"):
    """Protocol echoes are exact; diagnostic summaries stay visible even in the smallest envelope."""
    if not isinstance(body, dict):
        return {}
    answer = {}
    def reference_field(value, path):
        if page_refs and isinstance(value, (list, dict)) and len(dumps(value)) > 512:
            return {"count": len(value), "read_pointer": path,
                    "read_note": "Required values exceed the text budget. Read every page here with read_report before echoing them."}
        return value
    for key in ("review_call_id", "required_refs", "usable_refs", "unusable_refs"):
        if key in body:
            answer[key] = reference_field(body[key], _pointer(pointer, key))
    if isinstance(body.get("decision_table"), list):
        # fit_cameras: one compact row per photo is the decision record; it is never sampled. Its rows already
        # carry each unusable photo's refusal, so a long unusable_refs detail moves behind its read pointer.
        answer["decision_table"] = body["decision_table"]
        unusable = body.get("unusable_refs")
        if isinstance(unusable, dict) and len(dumps(unusable)) > 1500 and not page_refs:
            answer["unusable_refs"] = {"refs": sorted(unusable), "read_pointer": _pointer(pointer, "unusable_refs"),
                                       "read_note": "Each refusal is in decision_table; full details are here."}
    if isinstance(body.get("camera_orientation"), dict):
        orientation = body["camera_orientation"]
        answer["camera_orientation"] = {
            "note": orientation.get("note"),
            "unresolved_refs": reference_field(orientation.get("unresolved_refs", []),
                _pointer(_pointer(pointer, "camera_orientation"), "unresolved_refs"))}
        if "unavailable_refs" in orientation:
            answer["camera_orientation"]["unavailable_refs"] = reference_field(orientation["unavailable_refs"],
                _pointer(_pointer(pointer, "camera_orientation"), "unavailable_refs"))
    if body.get("schema") == "g3d.crease_angle_comparison/1" and isinstance(body.get("summary"), dict):
        # This bounded five-choice summary is the direct decision record. Large
        # fit dependencies and export tables must not crowd its numbers out.
        answer["summary"] = body["summary"]
    if body.get("schema") in {"g3d.look_comparison/1", "g3d.lens_candidates/1"} and isinstance(body.get("lens_colour_gap"), list):
        # One factual line per compared photo, never sampled or moved behind a read pointer.
        answer["lens_colour_gap"] = body["lens_colour_gap"]
    if body.get("schema") in {"g3d.look_comparison/1", "g3d.lens_candidates/1"} and isinstance(body.get("lens_colour_judgement"), str):
        answer["lens_colour_judgement"] = body["lens_colour_judgement"]
    if body.get("schema") == "g3d.lens_candidates/1" and isinstance(body.get("apply_materials"), list):
        answer["apply_materials"] = reference_field(body["apply_materials"], _pointer(pointer, "apply_materials"))
    if isinstance(body.get("review"), dict):
        answer["review"] = {key: reference_field(body["review"][key], _pointer(_pointer(pointer, "review"), key)) for key in
                            ("required_refs", "review_call_id", "unavailable", "appearance_is_information", "evidence_reuse")
                            if key in body["review"]}
    if isinstance(body.get("contacts"), dict) and isinstance(body["contacts"].get("near_misses"), list):
        # Near misses between parts are a structural fact; the first rows are never sampled away.
        contact = body["contacts"]
        answer["contacts"] = {"near_misses": contact["near_misses"][:8], "complete": contact.get("complete"),
                              "touching_pairs": contact.get("touching_pairs"), "rule": contact.get("rule")}
        if len(contact["near_misses"]) > 8 or contact.get("omitted_near_misses"):
            answer["contacts"]["read_pointer"] = _pointer(_pointer(pointer, "contacts"), "near_misses")
    if "surface_quality" in body:
        answer["surface_quality"] = summarize(body["surface_quality"], _pointer(pointer, "surface_quality"),
            width=width, levels=max(1, levels), string_chars=string_chars)
    if isinstance(body.get("look_comparison"), dict):
        look = _must_show(body["look_comparison"], width=width, levels=levels, string_chars=string_chars,
                          page_refs=page_refs, pointer=_pointer(pointer, "look_comparison"))
        if look:
            answer["look_comparison"] = look
    return answer


def _merge_required(compact, required):
    for key, value in required.items():
        if key in ("required_refs", "review_call_id", "usable_refs", "unusable_refs", "surface_quality", "summary"):
            compact[key] = value
        elif isinstance(value, dict) and isinstance(compact.get(key), dict):
            if compact[key].get("kind") in ("object", "array") and "read_pointer" in compact[key]:
                compact[key] = {}
            _merge_required(compact[key], value)
        else:
            compact[key] = value


def _tail_text(output, texts, body, footer, *, body_reserve=2000):
    """Reserve a nonempty label for EVERY image/reuse before spending text on the summary.

    A huge number of panels can outgrow even compact view/hash/call labels. In that case a short I<n>
    key identifies the exact original text_parts entry in the lossless report. No image is moved or dropped.
    """
    labels = [(position, index, text) for position, (index, text) in enumerate(texts[1:], 1)
              if text.startswith("Image") or
              index + 1 < len(output) and getattr(output[index + 1], "type", None) == "image"]
    label_indices = {index for _, index, _ in labels}
    # The regular budget footer is <500 characters. Preserve it exactly, whether appended to the
    # first JSON text (facet tools) or supplied as a final text part (main tools).
    footer = _short(footer, 1000) if len(footer) > 1000 else footer
    tail, ordinary_budget = {}, 2000
    for index, text in reversed(texts[1:]):
        if index in label_indices:
            continue
        limit = min(ordinary_budget, 1000 if index == texts[-1][0] else 500)
        tail[index] = _short(text, limit) if limit else ""
        ordinary_budget -= len(tail[index])
    budget = TEXT_LIMIT - body_reserve - len(footer) - sum(map(len, tail.values()))
    records = body.get("images", []) if isinstance(body, dict) else []
    if not isinstance(records, list):
        records = []
    if len(labels) == 1 and isinstance(body, dict) and isinstance(body.get("image_receipt"), dict):
        records = [body["image_receipt"]]
    mapped = records if len(records) == len(labels) else [{} for _ in labels]
    candidates = None
    for view_chars in (96, 48, 16, 0):
        candidates = {index: _image_label(text, record, position, view_chars)
                      for (position, index, text), record in zip(labels, mapped)}
        if sum(map(len, candidates.values())) <= budget:
            break
    else:
        candidates = {index: f"I{position}" for position, index, _ in labels}
        if sum(map(len, candidates.values())) > budget:
            raise ValueError("too many image identities for the fixed tool text limit; full output is saved")
    tail.update(candidates)
    note = ("Each Image n or In label identifies text_parts[n] in full_report, which retains the exact original "
            "view, SHA-256 and source/reuse call; image positions are unchanged.") if labels else None
    return tail, footer, note


def limit_output(root, call_id, tool, output):
    """Cap aggregate text, including image labels and footer, preserving image positions and all original text."""
    if isinstance(output, str):
        if len(output) <= TEXT_LIMIT:
            return output
        return limit_output(root, call_id, tool, [ToolOutputText(text=output)])[0].text
    texts = [(index, part.text) for index, part in enumerate(output) if getattr(part, "type", None) == "text"]
    if sum(len(text) for _, text in texts) <= TEXT_LIMIT:
        return output
    first, text = texts[0]
    body, footer = _body(text)
    record = {"schema": REPORT_SCHEMA, "tool": tool, "call_id": call_id, "result": body,
              "text_parts": [{"output_index": index, "text": text} for index, text in texts]}
    receipt = save_report(root, record)
    # Never remove/reorder images or their adjacent text parts. Every label gets space, including reused images
    # whose label is their only output; all originals remain in the lossless report.
    minimum_required = _must_show(body, width=1, levels=1, string_chars=80)
    page_refs = False
    try:
        tail, footer, label_note = _tail_text(output, texts, body, footer,
            body_reserve=max(2000, len(dumps(minimum_required)) + 1200))
    except ValueError:
        # Unbounded owner photo names/counts cannot always fit even before diagnostics.
        # Keep the field and explicit retrieval contract, never silently omit or sample it.
        page_refs = True
        minimum_required = _must_show(body, width=1, levels=1, string_chars=80, page_refs=True)
        tail, footer, label_note = _tail_text(output, texts, body, footer,
            body_reserve=max(2000, len(dumps(minimum_required)) + 1200))
    available = TEXT_LIMIT - sum(map(len, tail.values())) - len(footer)
    candidate = None
    tables = _tables(body)
    for width, levels, chars in ((6, 5, 500), (4, 4, 300), (3, 3, 200), (2, 2, 120), (1, 1, 80)):
        compact = summarize(body, width=width, levels=levels, string_chars=chars)
        if not isinstance(compact, dict):
            compact = {"result": compact}
        _merge_required(compact, _must_show(body, width=width, levels=levels, string_chars=chars, page_refs=page_refs))
        compact.update(paged=True, full_report=receipt,
                       paging_note="Summary only. read_report can retrieve every value, table, filter or text page.")
        if label_note:
            compact["image_label_note"] = label_note
        if tables:
            compact["table_summaries"] = tables
        candidate = dumps(compact)
        if len(candidate) <= available:
            break
    else:
        compact = {"status": str(body.get("status", "reported"))[:80] if isinstance(body, dict) else "reported",
                   "paged": True, "full_report": receipt}
        _merge_required(compact, minimum_required)
        if label_note:
            compact["image_label_note"] = label_note
        candidate = dumps(compact)
    if len(candidate) > available:
        raise ValueError("tool report receipt exceeds text policy")
    out = list(output)
    out[first] = ToolOutputText(text=candidate + footer)
    for index, value in tail.items():
        out[index] = ToolOutputText(text=value)
    assert sum(len(part.text) for part in out if getattr(part, "type", None) == "text") <= TEXT_LIMIT
    return out


def read_report(root, report, pointer="/result", offset=0, limit=20, mode="json", filter_path=None, filter_value=None):
    if not isinstance(report, str) or not re.fullmatch(r"[a-f0-9]{64}", report):
        raise ValueError("report must be a saved full_report.id")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= (8000 if mode == "text" else 100):
        raise ValueError("invalid page offset or limit")
    if mode not in ("json", "text") or (filter_path is None) != (filter_value is None):
        raise ValueError("invalid mode or incomplete filter")
    path = _path(root, report)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != report:
        raise ValueError("saved report hash does not match")
    doc = json.loads(data)
    if doc.get("schema") != REPORT_SCHEMA:
        raise ValueError("not a saved tool report")
    value = select(doc, pointer)
    result = {"status": "ok", "report": report, "pointer": pointer[:1000], "mode": mode, "offset": offset}
    if len(pointer) > 1000:
        result.update(pointer_echo_shortened=True, pointer_characters=len(pointer))
    if mode == "text":
        if filter_path is not None:
            raise ValueError("filters apply to structured table pages")
        text = value if isinstance(value, str) else dumps(value)
        result.update(characters=len(text), text=text[offset:offset+limit],
                      next_offset=offset+limit if offset+limit < len(text) else None)
        # Escapes may make the JSON envelope larger than the raw character slice.
        while len(dumps(result)) > PAGE_CHARS and result["text"]:
            result["text"] = result["text"][:len(result["text"]) // 2]
            result["next_offset"] = offset + len(result["text"])
        return result
    entries = list(enumerate(value)) if isinstance(value, list) else list(value.items()) if isinstance(value, dict) else [(None, value)]
    total = len(entries)
    if filter_path is not None:
        def matches(item):
            try:
                chosen = item if filter_path == "*" else select(item, filter_path)
            except ValueError:
                return False
            return str(filter_value).casefold() in (chosen if isinstance(chosen, str) else dumps(chosen)).casefold()
        entries = [(key, item) for key, item in entries if matches(item)]
    result.update(total=total, matched=len(entries), filter_path=filter_path, filter_value=filter_value, entries=[])
    for key, item in entries[offset:offset+limit]:
        item_pointer = pointer if key is None else _pointer(pointer, key)
        entry = {"key": key, "pointer": item_pointer, "value": item}
        if len(dumps(entry)) > PAGE_CHARS // 2:
            entry = {"key": key, "pointer": item_pointer, "summary": summarize(item, item_pointer, width=2, levels=2,
                                                                                string_chars=120),
                     "read_note": "Select this pointer; mode=text retrieves the entire value losslessly."}
        if len(dumps({**result, "entries": [*result["entries"], entry]})) > PAGE_CHARS - 100:
            break
        result["entries"].append(entry)
    count = len(result["entries"])
    if not count and offset < len(entries):
        key, _ = entries[offset]
        result["entries"] = [{"key": key, "pointer": _pointer(pointer, key), "read_note": "Select this pointer in text mode."}]
        count = 1
    result["next_offset"] = offset+count if offset+count < len(entries) else None
    return result


def report_tool(root, tool, call):
    """Register the same confined reader in generic and specialist tool sets."""
    async def reader(ctx: ToolContext, report: str, pointer: str = "/result", offset: int = 0, limit: int = 20,
                     mode: Literal["json", "text"] = "json", filter_path: str | None = None,
                     filter_value: str | None = None):
        return await call(ctx, "read_report", lambda cid: read_report(root, report, pointer, offset, limit, mode,
                                                                     filter_path, filter_value))
    reader.__doc__ = REPORT_DOC
    return tool("read_report")(reader)


def bounded_tool(tool, root):
    """Also cover SDK schema failures and callbacks, including separately decorated specialist tools."""
    invoke = tool.on_invoke_tool
    async def bounded(ctx, arguments):
        return limit_output(root, getattr(ctx, "tool_call_id", tool.name), tool.name, await invoke(ctx, arguments))
    tool.on_invoke_tool = bounded
    for field in ("failure_error_function", "timeout_error_function"):
        original = getattr(tool, field, None)
        if original is None:
            continue
        def callback(ctx, error, original=original):
            result = original(ctx, error)
            if inspect.isawaitable(result):
                async def waited():
                    return limit_output(root, getattr(ctx, "tool_call_id", tool.name), tool.name, await result)
                return waited()
            return limit_output(root, getattr(ctx, "tool_call_id", tool.name), tool.name, result)
        setattr(tool, field, callback)
    return tool
