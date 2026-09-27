"""The transcript audit lists tool calls and flags paths outside the package (synthetic transcript)."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from modeler.audit_transcript import audit


class Audit(unittest.TestCase):
    def test_flags_outside_reads_and_ignores_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "turn-0000"
            pkg.mkdir()
            rows = [
                {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": str(pkg / "request.json")}}]}},
                {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": str(pkg / "images" / "front.jpg")}}]}},
                {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "WebFetch", "input": {"url": "https://example.com/x"}}]}},
                {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write", "input": {"file_path": str(pkg / "response.json"), "content": "{}"}}]}},
            ]
            t = root / "clean.jsonl"
            t.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            res = audit(t, pkg)
            self.assertTrue(res["clean"], res["outside_package"])
            self.assertEqual(res["tool_calls"], 4)
            rows.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": str(root / "elsewhere" / "solved.glb")}}]}})
            t2 = root / "dirty.jsonl"
            t2.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            res2 = audit(t2, pkg)
            self.assertFalse(res2["clean"])
            self.assertEqual(len(res2["outside_package"]), 1)


if __name__ == "__main__":
    unittest.main()
