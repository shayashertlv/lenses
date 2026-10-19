"""Shared plain-label warnings; owner text is never rewritten."""
from __future__ import annotations

import re
from typing import Iterable, Mapping


_LABEL_LIKE = re.compile(r"(?<!\w)(?:[A-Z][0-9]+|[A-Z])(?!\w)")


def _labels(item: Mapping) -> list[str]:
    labels = item.get("labels")
    if labels is None:
        capture = item.get("capture", item)
        labels = [mark.get("label", "") for mark in capture.get("marks", [])]
    return list(dict.fromkeys(label for label in labels if isinstance(label, str) and label))


def _mentions(text: str, label: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", text) is not None


def _warning(code: str, label: str, message: str) -> dict:
    return {"code": code, "label": label, "message": message}


def item_warnings(items: Iterable[Mapping], item_id: str) -> list[dict]:
    """Warnings attached to one item, evaluated against the complete draft."""
    rows = list(items)
    item = next((row for row in rows if row.get("item_id") == item_id), None)
    if item is None:
        raise KeyError(item_id)
    labels = {label for row in rows for label in _labels(row)}
    texts = [row.get("request_text", "") for row in rows]
    text = item.get("request_text", "")
    warnings = []
    if not text.strip():
        warnings.append(_warning("empty_text", "", "Describe what you want to change in this item."))
    for label in sorted(set(_LABEL_LIKE.findall(text)) - labels - {"I", "A"}):
        warnings.append(_warning("unknown_label", label, f'Label "{label}" does not exist in this edit session.'))
    for label in _labels(item):
        if not any(_mentions(words, label) for words in texts):
            warnings.append(_warning("unused_label", label, f'Label "{label}" is not mentioned in any edit request.'))
    return warnings


def label_warnings(items: Iterable[Mapping]) -> list[dict]:
    """All draft warnings, preserving item order and omitting duplicates."""
    rows = list(items)
    result = []
    for row in rows:
        for warning in item_warnings(rows, row["item_id"]):
            if warning not in result:
                result.append(warning)
    return result
