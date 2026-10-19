"""Helpers for the stage-C loop tests: an approved job, fake tools, a fake scene journal, tiny images.

No network (conftest: LoopbackOnly + the production-transport tripwire), no Blender: the scene is ``FakeScene``, a
``loop.SceneHooks`` with an in-memory checkpoint chain and journal shaped like stage A's (``journal.json`` rows).
"""
from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import replace
from pathlib import Path
import struct
import zlib

from agents import function_tool
from agents.tool_context import ToolContext

from glasses3d.agent import approvals as A
from glasses3d.agent.loop import RunSettings, RunSpec, SceneHooks, ToolContract, instructions_sha256, tool_registry_sha256
from glasses3d.agent.replay import ScriptedOpenAI
from glasses3d.agent.session_inputs import ReferenceInput
from glasses3d.tests.agent.support import OFFLINE_KEY

INSTRUCTIONS = "You are Astra. Build the photographed glasses in the live Blender scene. (stage C test text)"
BRIEF = ("Product: Test Frame\nOwner's job description (authoritative, verbatim):\nA thin round metal frame.\n"
         "Owner's specifications (authoritative, verbatim):\n{\"lens_width_mm\": 48}\n"
         "Every reference image below is an authoritative photo of this product.")
DEFAULT_CAP = "5"


def tool_instructions(tools, instructions=INSTRUCTIONS):
    return instructions + "\n" + "\n".join(f"{tool.name}: {tool.description}" for tool in tools)


def png_bytes(width: int = 2, height: int = 2, seed: int = 0) -> bytes:
    """A valid tiny RGB PNG (distinct bytes per seed)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + bytes((seed * 37 + x * 11 + y * 5) % 256 for x in range(width * 3))
                    for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def data_url(payload: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


def photos(count: int = 2) -> list[ReferenceInput]:
    views = ["front", "side", "top", "rear", "angled", "side"]
    return [ReferenceInput(label=f"Reference {k + 1} of {count}", path=f"images/reference-0{k + 1}.png",
                           image_url=data_url(png_bytes(seed=k + 1)), view=views[k], size_px=(2, 2))
            for k in range(count)]


class FakeScene(SceneHooks):
    """An in-memory checkpoint chain + journal (the shape stage A's BlenderSession writes)."""

    def __init__(self):
        self.head = "c0000"
        self.count = 0
        self.entries: list[dict] = []
        self.events: list[str] = []
        self.restored: list[tuple[str, str]] = []

    def start(self):
        self.events.append("start")
        return {"head": self.head}

    def mutate(self, call_id: str, tool: str, *, changed: bool = True) -> dict:
        before = self.head
        if changed:
            self.count += 1
            self.head = f"c{self.count:04d}"
        entry = {"call_id": call_id, "tool": tool, "mutating": changed, "checkpoint_before": before,
                 "checkpoint_after": self.head}
        self.entries.append(entry)
        return entry

    def journal_entries(self):
        return list(self.entries)

    def restore(self, checkpoint_id: str, *, reason: str):
        self.count += 1
        self.head = f"c{self.count:04d}"
        self.restored.append((checkpoint_id, reason))
        self.entries.append({"kind": "rollback", "checkpoint": checkpoint_id, "head": self.head, "reason": reason})
        self.events.append(f"restore {checkpoint_id}")
        return {"checkpoint": self.head, "restored_from": checkpoint_id}

    def exit_checkpoint(self):
        self.events.append("exit_checkpoint")
        return {"id": self.head, "sha256": "f" * 64, "verified": True}

    def worker_info(self):
        return {"worker_pid": 4242, "worker_cpu_s": 1.5}

    def close(self):
        self.events.append("close")


def make_tools(scene: FakeScene | None = None, *, stop_file: Path | None = None, crash_after: str | None = None):
    """scene_summary (read-only), execute_blender_code (mutating, journaled in the fake scene), slow_tool (async)."""
    scene = scene or FakeScene()

    @function_tool
    def scene_summary() -> dict:
        """Summarize the live scene: objects, materials and units."""
        return {"status": "ok", "objects": 3, "units": "mm", "head": scene.head}

    @function_tool
    def execute_blender_code(ctx: ToolContext, code: str, label: str = "") -> dict:
        """Run validated bpy code in the live scene; a changed scene becomes a new checkpoint.

        Args:
            code: The Python snippet.
            label: A short label for the checkpoint.
        """
        entry = scene.mutate(ctx.tool_call_id, "execute_blender_code")
        result = {"status": "ok", "checkpoint": entry["checkpoint_after"], "seconds": 0.31,
                  "render": "work/renders/0001/front.png"}
        if crash_after == "execute_blender_code":
            raise SimulatedCrash("the runner died after the call completed")
        return result

    @function_tool
    async def slow_tool(seconds: float) -> dict:
        """Wait for a host process (a stand-in for a long render).

        Args:
            seconds: How long the host work takes.
        """
        if stop_file is not None:
            stop_file.write_text("stop", encoding="utf-8")   # the owner's `job stop` arrives mid-tool
        await asyncio.sleep(seconds)
        return {"status": "ok", "seconds": seconds}

    return scene, [scene_summary, execute_blender_code, slow_tool]


class SimulatedCrash(BaseException):
    """Stands in for the runner process dying mid-turn (a BaseException: no handler turns it into a tool error)."""


def make_job(root: Path, *, tools, run_id: str = "20261007T120000-run001", cap: str = DEFAULT_CAP,
             settings: RunSettings | None = None, approve: bool = True, instructions: str = INSTRUCTIONS,
             name: str = "test-frame-0000abcd", notes=()) -> tuple[Path, str]:
    """A job directory whose run ``run_id`` has an estimate and (optionally) the owner's approval."""
    settings = settings or RunSettings(effort="max", max_output_tokens=4096, max_turns=10)
    if settings.settled_stop_usd is None:
        settings = replace(settings, settled_stop_usd=cap)
    job = root / "jobs" / name
    (job / "inputs" / "images").mkdir(parents=True, exist_ok=True)
    if not (job / "job.json").exists():
        (job / "job.json").write_text(json.dumps({"schema": "g3d.job/1", "name": "Test Frame"}), encoding="utf-8")
        for k, photo in enumerate(photos(2), 1):
            (job / "inputs" / "images" / f"reference-0{k}.png").write_bytes(base64.b64decode(
                photo.image_url.split(",", 1)[1]))
    request = A.build_request(job, instructions_sha256=instructions_sha256(tool_instructions(tools, instructions)),
                              tool_registry_sha256=tool_registry_sha256(tools),
                              settings=settings.approval_settings(), unsent_owner_notes=list(notes),
                              run_inputs=A.run_input_binding(job, BRIEF, photos(2)))
    A.write_approval_request(job / "runs" / run_id, job_id=job.name, request=request,
                             estimate={"expected_usd": "1.00"}, suggested_cap_usd=cap)
    if approve:
        A.write_approval(job / "runs" / run_id, max_usd=cap, owner_words=f"Yes, run it with ${cap}.")
    return job, A.request_sha256(request)


def spec_for(job: Path, run_id: str, request_sha: str, tools, transport: ScriptedOpenAI, *,
             settings: RunSettings | None = None, resume: bool = False, notes=(), **extra) -> RunSpec:
    settings = settings or RunSettings(effort="max", max_output_tokens=4096, max_turns=10)
    if settings.settled_stop_usd is None:
        bound = json.loads((job / "runs" / run_id / "approval-request.json").read_text(encoding="utf-8"))
        settings = replace(settings, settled_stop_usd=bound["requested_cap_usd"])
    return RunSpec(job_dir=job, run_id=run_id,
                   instructions=tool_instructions(tools, extra.pop("instructions", INSTRUCTIONS)), tools=tools,
                   brief=BRIEF, current_request_sha256=request_sha, photos=photos(2), owner_notes=list(notes),
                   settings=settings, resume=resume,
                   offline_handler=transport.handle, offline_api_key=OFFLINE_KEY,
                   heartbeat_interval_s=extra.pop("heartbeat_interval_s", 0.05),
                   contract=extra.pop("contract", ToolContract(frozenset(tool.name for tool in tools))), **extra)


def occurrences(root: Path, needle: bytes) -> list[str]:
    """Every file under root whose bytes contain needle."""
    return [str(path.relative_to(root)) for path in sorted(root.rglob("*"))
            if path.is_file() and needle in path.read_bytes()]


def ledger_rows(job: Path, run_id: str) -> list[dict]:
    return json.loads((job / "runs" / run_id / "budget.json").read_text(encoding="utf-8"))["rows"]


def result_of(job: Path, run_id: str) -> dict:
    return json.loads((job / "runs" / run_id / "result.json").read_text(encoding="utf-8"))
