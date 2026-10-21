"""The pre-task-14 text control; no rendering, measurement or production mutation.

The normal output is paged first, then only the added colour-gap members are
removed. This retains exactly the baseline report identities, pagination and
image/footer transport. Immutable raw reports remain provenance; their lab
reader removes the same members, including text pages and nested text_parts.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace


SOURCE_COMMIT = "61d5726"
GAP_KEY = "lens_colour_gap"
METALLIC_SENTENCE = (
    "In the browser AR renderer, metallic multiplies the colour seen through the lens by (1 − metallic), so a high\n"
    "Transmission value does not keep a metallic lens see-through; judge it in the same-export photo and worn\n"
    "comparisons.\n"
)


def prompt_sources(repo: Path, job: dict, baseline_sources: dict | None = None) -> dict:
    """Build the exact historical source with the HEAD builder's extraction method.

    Refuse any drift beyond the one approved sentence rather than silently
    turning this experiment into a broader historical-code comparison.
    """
    from .trial import git_bytes, head_prompt_sources, sha256

    baseline = head_prompt_sources(repo, job) if baseline_sources is None else baseline_sources
    commit = git_bytes(repo, "rev-parse", SOURCE_COMMIT).decode("ascii").strip()
    paths = ("glasses3d/agent/brief.py", "glasses3d/agent/instructions.py")
    sources = {path: git_bytes(repo, "show", f"{commit}:{path}") for path in paths}
    brief_ns = {"__name__": "lens_lab_pre14_brief"}
    exec(compile(sources[paths[0]], paths[0] + "@" + commit, "exec"), brief_ns)
    instructions_ast = ast.parse(sources[paths[1]])
    imports = [node for node in instructions_ast.body
               if isinstance(node, ast.ImportFrom) and node.module == "brief" and node.level == 1]
    if (len(imports) != 1 or [(a.name, a.asname) for a in imports[0].names]
            != [("MODEL_REQUIREMENTS", None)]):
        raise ValueError("pre14 instruction dependency changed; review historical extraction")
    instructions_ast.body.remove(imports[0])
    namespace = {"__name__": "lens_lab_pre14_instructions", "MODEL_REQUIREMENTS": brief_ns["MODEL_REQUIREMENTS"]}
    exec(compile(instructions_ast, paths[1] + "@" + commit, "exec"), namespace)
    names = sorted(set(namespace["CORE_GUIDANCE"]) | set(namespace["SPECIALIST_GUIDANCE"]))
    instructions = namespace["build_instructions"]([SimpleNamespace(name=name) for name in names])
    brief = brief_ns["build_brief"](job)
    if (baseline["instructions"].count(METALLIC_SENTENCE) != 1
            or baseline["instructions"].replace(METALLIC_SENTENCE, "", 1) != instructions
            or baseline["brief"] != brief or baseline["main_tool_names"] != names):
        raise ValueError("pre14 must differ from baseline only by task 14's metallic sentence")
    return {**baseline, "instructions": instructions,
            "source_sha256": {path: sha256(source) for path, source in sources.items()},
            "instructions_source_commit": commit,
            "baseline_source_sha256": dict(baseline["source_sha256"])}


def _gap_pointer(pointer):
    return isinstance(pointer, str) and GAP_KEY in pointer.split("/")


def _gap_entry(value):
    return (isinstance(value, dict) and value.get("key") == GAP_KEY
            and _gap_pointer(value.get("pointer")))


def strip_gap_text(text: str) -> str:
    """Delete gap members from JSON text without reserializing other bytes.

    The JSON may have an unchanged footer. Nested JSON strings are the saved
    pager's original text_parts, and are filtered too. Ordinary strings and
    non-JSON text are untouched.
    """
    for left, right in reversed(_gap_spans(text)):
        text = text[:left] + text[right:]
    return text


def _gap_spans(text):
    decoder = json.JSONDecoder()
    start = len(text) - len(text.lstrip())
    if start == len(text) or text[start] not in "{[":
        return []
    try:
        _, final = decoder.raw_decode(text, start)
    except ValueError:
        return []
    edits = []

    def whitespace(index):
        while index < final and text[index].isspace():
            index += 1
        return index

    def visit(index):
        index = whitespace(index)
        if text[index] in "{[":
            mapping = text[index] == "{"
            close = "}" if mapping else "]"
            current = whitespace(index + 1)
            members = []
            while text[current] != close:
                begin = current
                key = None
                if mapping:
                    key, current = decoder.raw_decode(text, current)
                    current = whitespace(current)
                    if text[current] != ":":
                        raise ValueError("expected object colon")
                    current = whitespace(current + 1)
                value, end = decoder.raw_decode(text, current)
                remove = key == GAP_KEY if mapping else _gap_entry(value)
                if not remove:
                    visit(current)
                after = whitespace(end)
                comma = after if text[after] == "," else None
                members.append((begin, end, comma, remove))
                current = whitespace(after + 1) if comma is not None else after
            # Remove contiguous runs with their separators, leaving every
            # retained member's exact spelling and whitespace untouched.
            i = 0
            while i < len(members):
                if not members[i][3]:
                    i += 1
                    continue
                j = i
                while j + 1 < len(members) and members[j + 1][3]:
                    j += 1
                if j + 1 < len(members):
                    edits.append((members[i][0], members[j + 1][0]))
                elif i:
                    edits.append((members[i - 1][2], members[j][1]))
                else:
                    edits.append((members[i][0], members[j][1]))
                i = j + 1
        elif text[index] == '"':
            value, end = decoder.raw_decode(text, index)
            removals = _gap_spans(value)
            if removals:
                # Pipeline JSON strings use this exact serializer. Preserve
                # noncanonical escaped spellings too via character boundaries.
                spans = _string_spans(text[index:end])
                for left, right in removals:
                    edits.append((index + spans[left], index + spans[right]))

    visit(start)
    return sorted(edits)


def _string_spans(token):
    """Map decoded Unicode character boundaries to their original JSON bytes."""
    spans, index = [1], 1
    while index < len(token) - 1:
        if token[index] != "\\":
            index += 1
        elif token[index + 1] != "u":
            index += 2
        else:
            code = int(token[index + 2:index + 6], 16)
            index += 6
            if (0xD800 <= code <= 0xDBFF and token[index:index + 2] == "\\u"
                    and 0xDC00 <= int(token[index + 2:index + 6], 16) <= 0xDFFF):
                index += 6
        spans.append(index)
    return spans


def deletion_spans(original: str, changed: str) -> list[tuple[int, int]]:
    """A checked diff: the control may only remove bytes, never insert them."""
    spans = []
    for tag, a, b, _, _ in difflib.SequenceMatcher(None, original, changed, autojunk=False).get_opcodes():
        if tag == "delete":
            spans.append((a, b))
        elif tag != "equal":
            raise ValueError("pre14 output changed bytes outside deletion-only control")
    return spans


def _clean(value, document=None):
    if isinstance(value, dict):
        result = {key: _clean(item, document) for key, item in value.items() if key != GAP_KEY}
        if (document is not None and isinstance(value.get("preview"), str)
                and isinstance(value.get("read_pointer"), str)):
            from glasses3d.agent import tool_reports
            original = tool_reports.select(document, value["read_pointer"])
            if isinstance(original, str):
                result["preview"] = _clean_slice(original, 0, value["preview"])
        return result
    if isinstance(value, list):
        return [_clean(item, document) for item in value if not _gap_entry(item)]
    if isinstance(value, str):
        return strip_gap_text(value)
    return value


def _clean_slice(original, offset, page):
    end = offset + len(page)
    for left, right in reversed(_gap_spans(original)):
        left, right = max(left, offset), min(right, end)
        if left < right:
            page = page[:left - offset] + page[right - offset:]
    return page


def read_report(root, report, pointer="/result", offset=0, limit=20, mode="json", filter_path=None, filter_value=None):
    """Read baseline provenance, suppressing gap text without moving its pages.

    Original total/offset/character metadata intentionally retains the baseline
    coordinates. Deleted gap entries leave a hole in that original sequence.
    """
    from glasses3d.agent import tool_reports

    result = tool_reports.read_report(root, report, pointer, offset, limit, mode, filter_path, filter_value)
    if _gap_pointer(pointer):
        raise ValueError("JSON pointer is absent")
    # Filter against the complete immutable text before slicing. Filtering a
    # fragment alone could miss a gap key or a string split between two pages.
    data = tool_reports._path(root, report).read_bytes()
    if hashlib.sha256(data).hexdigest() != report:
        raise ValueError("saved report hash does not match")
    document = json.loads(data)
    if mode == "json":
        return _clean(result, document)
    value = tool_reports.select(document, pointer)
    original = value if isinstance(value, str) else tool_reports.dumps(value)
    return {**result, "text": _clean_slice(original, offset, result["text"])}


def strip_tool_output(output):
    """Filter after the normal limiter: all other transport is byte-identical."""
    from agents.tool import ToolOutputText

    if isinstance(output, str):
        return strip_gap_text(output)
    return [ToolOutputText(text=strip_gap_text(part.text)) if isinstance(part, ToolOutputText) else part
            for part in output]
