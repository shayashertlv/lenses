"""The connected author/tool/result state machine with durable recovery.

One author conversation per job: the host replays the complete window every request, executes exactly the tool the
author called, appends the ``function_call_output`` (text plus the images it carries) and asks again, without a human
prompt in between. Every paid request is reserved before it is sent, persisted before it is executed and settled from
its usage afterwards. A crash at any point leaves a state the next ``resume`` reconciles: prepared requests are
released, sent requests without a durable response stay unknown (never re-posted), completed responses are replayed
locally, pending operations run once, running operations are reattached or recorded as interrupted. Every commit of a tool
output checks the lease fence inside its transaction, so a runner whose lease was taken over publishes nothing.
"""
from __future__ import annotations

import base64
import contextlib
from datetime import timedelta
import io
import json
import os
from pathlib import Path
import traceback

from .. import author as mauthor
from ..author_astra import EVALUATOR_TOOL
from ..request import DEFAULT_LIMITS, Photo, Request
from .artifacts import AUTHOR_VISIBLE, SYNTHETIC, ArtifactStore, atomic_write
from .budget import Budget, BudgetError, BudgetExhausted
from .evaluation import (CRITIC_TASK, CRITIC_TOOL, FINAL_PROTOCOL, FINAL_TASK, EvidenceError, assert_no_sealed_pixels, checklist_from_notes, critic_blocks,
                         final_axes, final_blocks, read_heldout, record_owner_verdict, report_markdown, reserve_sealed_evidence, review_round_dir,
                         validate_critique, write_deliverable, write_review_candidate)
from .executor import Worker
from .pricing import Tariff
from .responses import (CONNECT_TIMEOUT_S, ENDPOINT, ENDPOINT_COMPACT, ENDPOINT_COUNT, PaidRefused, ParsedResponse, Transport, TransportError, UnknownOutcome,
                        build_payload, canonical, compact_payload, count_payload, image_block, image_count_in, parse_compact_body, parse_count_body,
                        parse_response_body, read_timeout_s, sha256, text_block, user_message, validate_call_arguments, window_input_sha256)
from .state import LEASE_TTL_S, TERMINAL_STATES, LeaseError, StateError, Store, TransitionError, parse_iso, runner_identity
from . import review as R
from . import tools as T

AUTHOR_PROMPT_PATH = Path(__file__).with_name("AUTHOR_PROMPT.md")
ROLE_AUTHOR, ROLE_CRITIC, ROLE_FINAL = "author", "critic", "final"
MAX_TEXT_ONLY_RESPONSES = 2
MAX_INCOMPLETE_RESPONSES = 2
# Output bounds of the one-shot roles (AT-08 / F7 / CE-5 / INF-03). Test-pilot-002's critic used 7,196 of its 8,000 output
# tokens (5,065 reasoning at effort high): one step from an incomplete reply that spends the operation and returns nothing.
# The reservation is the worst case but settlement is on use, so the headroom costs nothing unless it is spent; 16,000 is
# twice the old bound, 2.2x the largest critic seen. The final evaluator used 2,203 of 8,000; 12,000 leaves it 5x. Both
# stay under the policy's max_output_tokens, and both roles are asked for concise answers (evaluation.CRITIC_TASK / FINAL_TASK).
CRITIC_MAX_OUTPUT_TOKENS = 16_000
FINAL_MAX_OUTPUT_TOKENS = 12_000
OUTPUT_NEAR_BOUND = 0.8                 # an 'output_near_bound' event above this share of max_output_tokens
CACHE_MISS_SLACK_TOKENS = 1024          # cached below the previous request's input by more than this: a 'cache_miss' event (rolling mode)
LEASE_MARGIN_S = 120                    # the lease outlives the HTTP call it covers by this much
HOST_CALL_PREFIX = "host_"              # call ids of operations the host runs itself (the seed build): they owe no function_call_output
MIN_PRUNED_IMAGES = 4                   # an image prune epoch is opened only when at least this many images leave the window
PRUNE_STUB_NOTE = ("image not re-sent: you received it earlier in this conversation and a newer revision's images supersede it; "
                   "crop_image with its full size_px shows it again")
REVIEW_RESUME_TOKEN_LIMIT = 150_000     # config.REVIEW_RESUME_TOKEN_LIMIT: a stored policy without the key reads this
OWNER_REVIEW_FIRST_MESSAGE = ("request_delivery does not end the job by itself: the host hands the revision to the owner, who tries it on live in "
                              "the AR mirror and accepts it (the job delivers exactly those bytes), asks for changes (their words arrive in this "
                              "same conversation as a new message, with the measurements that relate to them and a new allowance) or stops. "
                              "No automatic final evaluator judges it.")


class RunnerError(RuntimeError):
    pass


class InferenceUnknownOutstanding(RunnerError):
    """A paid send was refused because an earlier request has an unknown outcome whose reservation is still counted as
    liability: nothing is sent until the owner reconciles it (`reconcile-unknown`)."""

    def __init__(self, requests: list[str]):
        self.requests = list(requests)
        super().__init__(f"{len(self.requests)} inference request(s) with unknown outcome {self.requests}: reconcile the provider dashboard "
                         "(`reconcile-unknown`) before any further inference")


def unknown_liability_requests(store: Store) -> list[str]:
    """Requests whose reservation is still an unknown liability, whatever HTTP outcome the request row recorded (a timeout
    leaves it 'unknown', a 5xx 'failed', a 200 without a usage block 'completed'), plus unknown requests that never had a
    reservation. The money truth is the reservation's state; `reconcile-unknown` settles it by request id."""
    out = []
    for r in store.requests():
        res = store.reservation(r["reservation_id"]) if r.get("reservation_id") else None
        if (res is not None and res.get("state") == "unknown") or (res is None and r["state"] == "unknown"):
            out.append(r["id"])
    return out


# list_evidence gained its nullable 'reference' argument on 2026-09-28. Under strict mode the model always sends it; a
# response recorded before (a durable response completed from its file after an upgrade) and the offline demo script
# carry {}: exactly that one legacy shape is read as reference null. Nothing else is relaxed: any other shape still
# meets the strict validator.
LEGACY_ARGUMENTS = {"list_evidence": ({}, {"reference": None})}


def validate_author_call(fc) -> tuple[dict | None, str | None]:
    """``responses.validate_call_arguments`` against the tool registry, plus the LEGACY_ARGUMENTS shapes."""
    args, err = validate_call_arguments(fc, T.TOOLS)
    legacy = LEGACY_ARGUMENTS.get(fc.name)
    if err is not None and legacy is not None and fc.parse_error is None and fc.arguments == legacy[0]:
        return dict(legacy[1]), None
    return args, err


def sent_image_ids(blocks: list[dict]) -> list[str | None]:
    """The artifact ids of the images a message carries, in order: every input_image block is preceded by its JSON meta
    text block ({"image_id": ...}, evaluation.critic_blocks / tools.image_blocks_for); an image without one binds None, so
    the list always has one entry per image sent."""
    out: list[str | None] = []
    prev = None
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "input_image":
            image_id = None
            if isinstance(prev, dict) and prev.get("type") == "input_text":
                try:
                    meta = json.loads(prev.get("text") or "")
                    image_id = meta.get("image_id") if isinstance(meta, dict) and isinstance(meta.get("image_id"), str) else None
                except ValueError:
                    image_id = None
            out.append(image_id)
        prev = b
    return out


def developer_text() -> str:
    return AUTHOR_PROMPT_PATH.read_text(encoding="utf-8")


def agentic_rules(rules: list[str], package_evidence: dict) -> list[str]:
    """The shared author rules as this route's first message states them (AT-13): the legacy decision name request_views
    is this route's render_views tool, and the evidence-provenance rule is left out when the package carries none of the
    data it speaks of (product_reading, evidence_provenance, front.evidence_reliability)."""
    has_provenance = bool(package_evidence.get("product_reading") or package_evidence.get("evidence_provenance")
                          or (package_evidence.get("front") or {}).get("evidence_reliability"))
    out = []
    for r in rules:
        if r.startswith("Evidence provenance:") and not has_provenance:
            continue
        out.append(r.replace("(request_views)", "(render_views)"))
    return out


def _image_size(block: dict) -> list[int] | None:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(base64.b64decode(str(block.get("image_url", "")).split(",", 1)[1]))) as im:
            return [int(im.width), int(im.height)]
    except Exception:  # noqa: BLE001 - the stub still names the image
        return None


def prune_window_images(window: list[dict], prunable: set[str]) -> tuple[list[dict], list[str]]:
    """(the window with every input_image whose artifact id is in ``prunable`` replaced by a stable text stub, the ids
    replaced). Only tool outputs and user messages carry images; the id is the one the preceding meta block names
    (``sent_image_ids``), and an image without one is never touched. Every other item and block is kept verbatim."""
    out, pruned = [], []
    for item in window:
        blocks = item.get("output") if item.get("type") == "function_call_output" else (
            item.get("content") if item.get("type") == "message" and item.get("role") == "user" else None)
        if not isinstance(blocks, list) or not prunable:
            out.append(item)
            continue
        ids = iter(sent_image_ids(blocks))
        new_blocks, changed = [], False
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "input_image":
                image_id = next(ids)
                if image_id is not None and image_id in prunable:
                    new_blocks.append(text_block(json.dumps({"image_not_resent": image_id, "size_px": _image_size(b), "note": PRUNE_STUB_NOTE}, sort_keys=True)))
                    pruned.append(image_id)
                    changed = True
                    continue
            new_blocks.append(b)
        if changed:
            item = dict(item, **({"output": new_blocks} if item.get("type") == "function_call_output" else {"content": new_blocks}))
        out.append(item)
    return out, pruned


class Session:
    def __init__(self, store: Store, *, transport: Transport, worker: Worker, log=print, holder: str | None = None, intake_drivers=None):
        self.store = store
        self.artifacts = ArtifactStore(store)
        self.budget = Budget(store)
        job = store.job()
        self.policy = job["policy"]
        self.model = job["model"]
        self.effort = job["reasoning_effort"]
        self.transport = transport
        self.worker = worker
        self.log = log
        self.holder = holder or runner_identity()
        self.fence: int | None = None
        self.developer = developer_text()
        self.intake_drivers = intake_drivers
        self.ar = bool(self.policy.get("ar", True))

    # ------------------------------------------------------------------ lease
    def acquire(self) -> None:
        self.fence = self.store.acquire_lease(self.holder, LEASE_TTL_S)

    def renew(self) -> None:
        if self.fence is not None:
            self.store.renew_lease(self.holder, self.fence, LEASE_TTL_S)

    def release(self) -> None:
        if self.fence is not None:
            self.store.release_lease(self.holder, self.fence)
            self.fence = None       # a reused Session re-acquires; a released fence is never renewed

    def require_fence(self) -> None:
        """The runner still owns the job: raises LeaseError without a lease or with a fence another holder replaced.
        Called inside every transaction that prepares, sends or applies an inference, so a runner whose lease lapsed
        during a long tool cycle or count call posts nothing and appends nothing."""
        if self.fence is None:
            raise LeaseError(f"no lease held by {self.holder!r}: acquire() before posting or applying inference")
        self.store.require_fence(self.holder, self.fence)

    # ------------------------------------------------------------------ unknown outcomes
    def unknown_liabilities(self) -> list[str]:
        """Requests whose reservation is still counted as an unknown liability (or unknown requests that never had one)."""
        return unknown_liability_requests(self.store)

    def refuse_while_unknown(self) -> None:
        """No paid send while a possibly charged request is unreconciled: raises InferenceUnknownOutstanding."""
        unknown = self.unknown_liabilities()
        if unknown:
            self.store.event("inference_refused_unknown_outstanding", requests=unknown)
            raise InferenceUnknownOutstanding(unknown)

    def stop_for_unknown(self, unknown: list[str], *, expected=None) -> None:
        self.store.transition("needs_attention", f"{len(unknown)} inference request(s) with unknown outcome {unknown}; reconcile the provider dashboard "
                              f"(status shows the liability; `reconcile-unknown` closes it) before resuming", expected=expected, stop_reason="inference_unknown")

    # ------------------------------------------------------------------ creation
    @classmethod
    def create(cls, job_dir: Path, *, translated: dict, policy: dict, fingerprints: dict, worker: Worker, transport: Transport, worker_config: dict | None,
               log=print, clock=None, intake_drivers=None, seed_program: Path | None = None, seed: dict | None = None) -> "Session":
        tariff = Tariff.frozen(service_tier=policy["service_tier"], region=policy["region"])
        store = Store.create(job_dir, {"request": translated["request"], "policy": policy, "fingerprints": fingerprints, "model": policy["model"],
                                       "reasoning_effort": policy["reasoning_effort"], "service_tier": policy["service_tier"], "endpoint": ENDPOINT,
                                       "tariff": tariff.to_dict(), "cap_micro": policy["cap_micro"], "inference_operation_cap": policy["max_inference_requests"],
                                       "driver": policy["driver"], "worker": policy["worker"], "worker_config": worker_config}, clock=clock)
        store.set_setting("request_translation", translated)
        store.set_setting("worker_description", worker.describe())
        session = cls(store, transport=transport, worker=worker, log=log, intake_drivers=intake_drivers)
        session.acquire()
        try:
            session.initialize(translated, seed_program=seed_program, seed=seed)
        except Exception as e:  # noqa: BLE001
            store.event("initialize_failed", error=f"{type(e).__name__}: {e}", traceback=traceback.format_exc()[-4000:])
            try:
                store.transition("failed", f"initialize: {type(e).__name__}: {e}", stop_reason="initialize_failed")
            except TransitionError:
                pass
            # the caller never receives this session: give its lease and connection back (a failed continuation's folder
            # stays for the owner to inspect or delete, and on Windows an open database would pin it)
            with contextlib.suppress(Exception):
                session.release()
            with contextlib.suppress(Exception):
                store.close()
            raise
        return session

    def initialize(self, translated: dict, *, seed_program: Path | None = None, seed: dict | None = None) -> None:
        """``seed_program``: a program folder imported as r0001 (--seed-program); ``seed``: {"modules", "source",
        "provenance"} already verified by the caller (--seed-revision). Either way the seeded revision is built and its
        images are placed in the window before the first author turn, and the first message says so."""
        request = translated["request"]
        reservation = reserve_sealed_evidence(self.store, self.artifacts, request["photos"])
        evidence = self.run_intake(request, reservation)
        self.store.set_setting("evidence", evidence)
        # the author-visible reduction lives with the intake outputs (evidence/), outside the catalogued artifact areas
        atomic_write(self.store.job_dir / "evidence" / "author_visible.json", json.dumps(T.author_visible_evidence(evidence), indent=1, default=str).encode())
        protocol = self.freeze_protocol(evidence)
        seeded = None
        if seed_program is not None:
            seeded = self.seed_modules(read_program_folder(Path(seed_program)), source=str(seed_program))
        elif seed is not None:
            seeded = self.seed_modules(seed["modules"], source=seed["source"], provenance=seed.get("provenance"))
        blocks = self.first_message_blocks(request, evidence, protocol, seed_revision=seeded)
        assert_no_sealed_pixels(self.store, blocks)
        self.store.new_epoch(ROLE_AUTHOR, "initial")
        self.store.append_items(ROLE_AUTHOR, [user_message(blocks)])
        if seeded is not None:
            self.host_build_seed(seeded)
        self.store.transition("ready", "initialized", expected="created")

    def run_intake(self, request: dict, reservation: dict | None) -> dict:
        mode = self.policy["intake"]
        # a photo is held out iff the sealed reservation sealed it: a declared crop or a pixel-identical copy of a held-out photo is
        # sealed whatever the request's own flag says, so the intake never measures it and no derived number reaches the author
        sealed = set((reservation or {}).get("sealed_ids") or []) if reservation is not None else {p["id"] for p in request["photos"] if p["held_out"]}
        held = {p["id"]: p["id"] in sealed for p in request["photos"]}
        photos = [Photo(Path(p["path"]), p["view"], held[p["id"]]) for p in request["photos"]]
        held_views = tuple(dict.fromkeys(p["view"] for p in request["photos"] if held[p["id"]]))
        req = Request(request["product_id"], photos, dict(request.get("dimensions") or {}), request.get("notes", ""), dict(DEFAULT_LIMITS), {},
                      held_views)
        if mode in ("none", "synthetic"):
            if not self.worker.synthetic and mode == "synthetic":
                raise EvidenceError("synthetic evidence is only allowed with the synthetic worker")
            width, prov = req.front_width_mm()
            evidence = {"product_id": request["product_id"], "notes": request.get("notes", ""), "synthetic": mode == "synthetic",
                        "scale": {"front_width_mm": width, **prov}, "dimensions_stated": request.get("dimensions") or {},
                        "conventions": "mm in the model frame; +X viewer's right, +Y up, +Z front", "front": None, "sides": {},
                        "views": {p["id"]: {"view": p["view"], "flags": ["unmeasured"], "size": [0, 0]} for p in request["photos"] if not held[p["id"]]},
                        "held_out": {p["id"]: {"view": p["view"]} for p in request["photos"] if held[p["id"]]},
                        "inputs": [{"id": p["id"], "view": p["view"], "held_out": held[p["id"]], "sha256": p["sha256"]} for p in request["photos"]],
                        "note": "no code measurement ran (intake none/synthetic): every number is unmeasured"}
            self.store.event("intake", mode=mode, measured=False)
            return evidence
        from ..intake import run_intake
        width, prov = req.front_width_mm()
        self.log(f"[intake] {len(photos)} photos, front width {width} mm ({prov['source']})")
        evidence = run_intake(req, self.store.job_dir, front_width_mm=width, width_provenance=prov)
        self.store.event("intake", mode="code", measured=True, views=list(evidence.get("views", {})), held_out=list(evidence.get("held_out", {})))
        if mode == "scripted":
            from ..intake_astra import run_intake_stage
            reading, review = self.intake_drivers if self.intake_drivers else (None, None)
            if reading is None:
                raise RunnerError("intake stage requested without drivers")
            evidence, record = run_intake_stage(req, self.store.job_dir, evidence, reading, review, log=self.log)
            self.store.event("intake_stage", reading=(record.get("reading") or {}).get("status"), review=(record.get("review") or {}).get("status"),
                             actions=record.get("actions"), errors=record.get("errors"))
        return evidence

    def freeze_protocol(self, evidence: dict) -> dict:
        from .. import evaluate as mevaluate
        calibration = {"calibrated": False, "reasons": ["not read"]}
        try:
            from ..calibration import protocol_calibration
            calibration = protocol_calibration("astra" if self.policy["final_evaluator"] == "responses" else "package")
        except Exception as e:  # noqa: BLE001 - a job freezes even when the calibration set is unreadable; then the bar is uncalibrated
            calibration = {"calibrated": False, "reasons": [f"calibration unreadable: {type(e).__name__}: {e}"]}
        # the intake's vision reading when it ran; otherwise a deterministic split of the listing text (CE-8: test-pilot-002's code
        # intake froze [] and the final evaluator invented its own list, comparable with nothing)
        vision = list(evidence.get("identity_features_vision") or [])
        from_notes = [] if vision else checklist_from_notes(evidence.get("notes"))
        protocol = {"identity_checklist": vision or from_notes,
                    "checklist_source": "intake_reading" if vision else ("request_notes" if from_notes else "none"),
                    "gate_overrides": evidence.get("gate_overrides") or {}, "evaluator_protocol": mevaluate.PROTOCOL, "final": FINAL_PROTOCOL,
                    "visual_bar_calibrated": bool(calibration.get("calibrated")), "calibration_summary": {k: v for k, v in calibration.items() if k not in ("assets", "table")},
                    "gate_thresholds_mm": mevaluate.GATE_THRESHOLDS_MM, "report_only_mm": mevaluate.REPORT_ONLY_MM,
                    "input_flags": sorted({f for v in evidence.get("views", {}).values() for f in (v.get("flags") or [])}),
                    "frozen_utc": self.store.now(),
                    "leakage": {"author": "author-visible photos and their measurements; the product reading when the intake stage ran",
                                "critic": "author-visible photos and the revision's own renders", "final": "all photos including sealed, wearer renders, held-out metrics; no author rationale"}}
        self.store.set_setting("protocol", protocol)
        self.store.event("protocol_frozen", calibrated=protocol["visual_bar_calibrated"], checklist=len(protocol["identity_checklist"]))
        return protocol

    def first_message_blocks(self, request: dict, evidence: dict, protocol: dict, *, seed_revision: str | None = None) -> list[dict]:
        job = self.store.job()
        package_evidence = T.compact_evidence_safe(evidence)
        how_to_start = ("everything you need is in this message (evidence, photos, helper reference, rules): write the first program directly with "
                        "edit_program (base_revision_id null, replace mode per module, build_now true); there is nothing to list or read first. ")
        if seed_revision is not None:
            how_to_start = (f"revision {seed_revision} was imported and built by the host before this conversation began: its build result, module "
                            f"sha256 values and images follow in the next message. Change it with edit_program (base_revision_id {seed_revision}, patch "
                            "or replace only what the change needs, inherit the rest, build_now true). ")
        head = {"task": "Build this pair of glasses as a Blender construction program and deliver one compatible asset with an honest status.",
                "product_id": request["product_id"], "listing_text_data": request.get("notes", ""), "dimensions_stated": request.get("dimensions") or {},
                "limits": {k: self.policy[k] for k in ("max_revisions", "max_worker_seconds", "images_per_request", "wall_minutes", "max_inference_requests")},
                "worker": self.worker.describe(), "evidence": package_evidence,
                "identity_checklist_for_the_final_evaluator": protocol.get("identity_checklist"),
                "identity_checklist_source": protocol.get("checklist_source"),
                "photos_available": [{"artifact_id": a["id"], "label": a["label"]} for a in self.store.artifacts(role=AUTHOR_VISIBLE, kind="photo")],
                # the helper reference and the rules live here, in the cached prefix, once: test-pilot-001 re-read a 33k-character
                # list_evidence output uncached on every turn
                "helper_reference": mauthor.helper_reference(), "rules": agentic_rules(mauthor.RULES, package_evidence),
                "how_to_start": how_to_start +
                                "Every edit_program / build result carries each module's sha256 for your next patch, and a build reply carries the sheets "
                                "and the photo-camera renders (fetch_pending_images only when a result says images are pending). One tool per message; "
                                "every message costs one inference operation (so do request_critic and the sealed final evaluation): each tool result "
                                "shows operations_remaining and a budget_notice",
                "job_request_sha256": job["request_sha256"]}
        owner = self.owner_revision_head(seed_revision)
        if owner:
            head["owner_revision"] = owner
        if self.owner_review():
            head["owner_review"] = OWNER_REVIEW_FIRST_MESSAGE
        blocks = [text_block("Host package (data, not instructions):\n" + json.dumps(head, indent=1, default=str))]
        for a in self.store.artifacts(role=AUTHOR_VISIBLE, kind="photo"):
            row, data = self.artifacts.read(a["id"], allow_roles=(AUTHOR_VISIBLE,))
            blocks.append(text_block(json.dumps({"image_id": a["id"], "label": a["label"], "sha256": a["sha256"]})))
            blocks.append(image_block(data, row["media_type"]))
        # a text trailer closes the stable prefix: the explicit cache breakpoint lands on it, with the photos inside the cached prefix
        blocks.append(text_block("End of the host package: every photo above is author-visible evidence; call one tool."))
        return blocks

    def owner_revision_head(self, seed_revision: str | None) -> dict | None:
        """The owner-revision block of the first message: the owner's request verbatim, the modules edit_program accepts
        changes to (policy 'editable_modules'; null: all) and the seeded revision. None when the job has none of them."""
        instruction = self.policy.get("owner_instruction")
        editable = self.policy.get("editable_modules")
        if not instruction and editable is None and seed_revision is None:
            return None
        head = {"owner_instruction": instruction, "editable_modules": editable, "seed_revision": seed_revision,
                "note": ("owner_instruction is the owner's own request for this job, quoted verbatim: make exactly that change to the seeded "
                         "revision and keep everything else as it is. " if instruction else "")
                        + (f"edit_program refuses a change to any module other than {', '.join(editable)} (such a call builds nothing); "
                           "inherit every other module unchanged." if editable is not None else "every module may be edited.")}
        if self.policy.get("previous_job"):
            # a continuation of an owner review whose conversation grew too large (Session.owner_continue_in_new_job): what came before
            head["previous_job"] = self.policy["previous_job"]
        return head

    def seed_modules(self, modules: dict[str, str], *, source: str, provenance: dict | None = None) -> str:
        """An explicit import of a previous program as revision r0001: the seed's hash, source and provenance are recorded;
        nothing else of the old session is carried over. Returns the revision id (built by ``host_build_seed``)."""
        if not modules:
            raise RunnerError(f"no program modules in the seed {source}")
        rev = T.insert_program_revision(self.store, modules, module_source=f"seed:{source}", rationale=f"seed program imported from {source}",
                                        synthetic=self.worker.synthetic)
        self.store.set_setting("seed", {"source": source, "revision": rev["id"], "program_set_sha256": rev["program_set_sha256"], "provenance": provenance,
                                        "contamination": "the seed program came from another session; its history is not part of this conversation"})
        return rev["id"]

    def host_build_seed(self, rid: str) -> None:
        """Build and observe the seeded revision before the first author turn, and put its result and images in the window
        as a host message (the author called nothing, so there is no function_call to answer). Until 2026-09-28 the seed
        was only announced and the author's first paid turn went to build_candidate. The operation is recorded like any
        other, under a host call id that reconcile never answers with a function_call_output; its images are 'included'
        and acknowledged by the first author request that carries them."""
        call_id = f"{HOST_CALL_PREFIX}seed_build_{rid}"
        op = self.store.insert_operation(call_id=call_id, request_id=None, tool_name="build_candidate", schema_version=T.TOOLS_VERSION,
                                         args={"revision_id": rid, "deliver_if_compatible": False}, source_revision_id=rid, fence=self.fence, state="running")
        self.store.update_operation(op["id"], started_utc=self.store.now())
        ctx = self.context()
        result = T.execute(ctx, self.store.operation(op["id"]))
        item, image_ids = T.output_for(ctx, call_id, result, state_note=T.state_summary(ctx))
        head = text_block(json.dumps({"seed_build": rid, "operation": op["id"],
                                      "note": "the host built the seeded revision before your first turn; this is its build result, as a build_candidate reply"}))
        with self.store.tx():
            self.require_fence()
            self.store.append_items(ROLE_AUTHOR, [user_message([head] + list(item["output"]))])
            for aid in image_ids:
                self.store.set_observation(aid, "included", reset_request=True)
            self.store.update_operation(op["id"], state="completed" if "error" not in result.text else "failed", output_committed=1, completed_utc=self.store.now(),
                                        result_json={"summary": {k: v for k, v in result.text.items() if k in ("error", "revision", "category")}, "images": image_ids})
        self.store.event("seed_built", revision=rid, operation=op["id"], images=image_ids, error=result.text.get("error"))

    # ------------------------------------------------------------------ the loop
    def evidence(self) -> dict:
        return self.store.setting("evidence") or {}

    def protocol(self) -> dict:
        return self.store.setting("protocol") or {}

    def context(self) -> T.ToolContext:
        return T.ToolContext(store=self.store, artifacts=self.artifacts, worker=self.worker, evidence=self.evidence(), policy=self.policy, log=self.log,
                             holder=self.holder, fence=self.fence, clock=self.store.clock, critic=self.critic_callable(), ar=self.ar, heartbeat=self.renew)

    def deadline_passed(self) -> bool:
        """The wall limit of the current round: from the job's creation, or from the owner's latest change request (a job
        that waited for the owner for a day starts its change round with the whole wall limit)."""
        job = self.store.job()
        start = self.store.setting("round_started_utc") or job["created_utc"]
        return self.store.clock() > parse_iso(start) + timedelta(minutes=int(self.policy["wall_minutes"]))

    def owner_review(self) -> bool:
        """The job waits for the owner's live verdict instead of delivering (policy owner_review; off for every job before it)."""
        return bool(self.policy.get("owner_review"))

    def run(self) -> str:
        """Drive the job to a stop; returns the final state."""
        if self.fence is None:
            self.acquire()
        try:
            self.reconcile()
            self.flush_owner_calibration()      # an owner decision's calibration row a crash left unwritten (idempotent)
            guard = 0
            while True:
                guard += 1
                if guard > 10_000:
                    self.store.transition("failed", "loop guard", stop_reason="loop_guard")
                    break
                self.renew()
                job = self.store.job()
                state = job["state"]
                if state in TERMINAL_STATES or state in ("needs_attention", "awaiting_owner"):
                    break           # awaiting_owner: a durable wait that sends nothing; only the owner's decision moves it
                if job["cancel_requested"]:
                    self.cancel_now()
                    break
                if self.deadline_passed() and state in ("ready", "needs_observation", "tool_pending"):
                    self.finalize_fallback("wall_deadline")
                    break
                fallback = self.store.setting("fallback_intent")
                if fallback and state in ("ready", "needs_observation", "tool_pending", "ready_for_final"):
                    # a fallback interrupted between its transitions resumes with its recorded reason, never as an author delivery
                    self.finalize_fallback(fallback["stop_reason"])
                    continue
                pending_delivery = self.store.setting("pending_delivery")
                if state in ("ready", "needs_observation") and pending_delivery and not pending_delivery.get("auto"):
                    self.finalize(pending_delivery, stop_reason="delivered_by_author")
                    continue
                if state in ("ready", "needs_observation"):
                    self.author_step()
                elif state == "tool_pending":
                    self.execute_pending_operations()
                elif state == "tool_running":
                    # a crash mid-execution was reconciled into interrupted operations; continue with what is pending
                    self.store.transition("tool_pending" if self.store.operations(state="pending") else "ready", "recovered tool_running")
                elif state == "inference_pending":
                    self.store.transition("needs_attention", "inference_pending without a request in flight after reconciliation", stop_reason="inconsistent_state")
                elif state == "ready_for_final":
                    if pending_delivery and not pending_delivery.get("auto"):
                        self.finalize(pending_delivery, stop_reason="delivered_by_author")
                    else:
                        # nothing recorded asked for this state: never fabricate an author delivery from selected_revision
                        self.finalize_fallback("delivery_intent_lost")
                elif state == "evaluating":
                    intent = self.store.setting("owner_accept_intent")
                    if intent:
                        # the owner's acceptance was interrupted between its transitions: completed with the recorded decision
                        self.owner_accept(authorized_by=intent["authorized_by"], medium=intent["medium"], note=intent.get("note", ""),
                                          calibration_file=intent.get("calibration_file"))
                    else:
                        self.store.transition("needs_attention", "evaluation interrupted; rerun resume to retry the final step", stop_reason="evaluation_interrupted")
                else:
                    self.store.transition("needs_attention", f"unhandled state {state}", stop_reason="unhandled_state")
        finally:
            self.release()
        return self.store.state()

    # ------------------------------------------------------------------ reconciliation
    def reconcile(self) -> None:
        """Repair durable state after a restart; never re-post an inference, never rerun a committed action."""
        verify = self.artifacts.verify_all()
        if not verify["ok"]:
            self.store.event("artifact_problems", problems=verify["problems"])
        if verify["orphans"]:
            self.store.event("artifact_orphans", orphans=verify["orphans"][:200], note="files without a catalogue row are kept for audit, never deleted")
        for req in self.store.requests(state="prepared"):
            if req.get("reservation_id"):
                try:
                    self.budget.release(req["reservation_id"], "request prepared but never marked sent (restart)")
                except BudgetError:
                    pass
            self.store.update_request(req["id"], state="released", error="prepared, never sent (restart)")
        for req in self.store.requests(state="sent"):
            rp = self.store.job_dir / "host" / "requests" / req["id"] / "response.json"
            if rp.is_file():
                self.log(f"[resume] replaying the durable response of {req['id']} without HTTP")
                self.complete_request_from_file(req, rp)
            else:
                self.store.update_request(req["id"], state="unknown", error="sent; no durable response (restart)")
                if req.get("reservation_id"):
                    self._mark_unknown(req["reservation_id"], "sent without a durable response; possibly charged", request_id=req["id"])
                self.store.event("inference_unknown", request=req["id"])
        # a reservation held without a request row (crash between reserve and insert) or for a request that was never sent is released
        for res in self.store.reservations(state="held"):
            req = self.store.request(res["request_id"]) if res.get("request_id") else None
            if req is None or req["state"] in ("released", "refused"):
                try:
                    self.budget.release(res["id"], "held without a sent request (restart)")
                except BudgetError:
                    pass
        current_epoch = self.store.epoch(ROLE_AUTHOR)
        for req in self.store.requests(state="completed"):
            if req["role"] != ROLE_AUTHOR:
                continue
            rp = self.store.job_dir / "host" / "requests" / req["id"] / "response.json"
            if req["purpose"] == "compaction":
                if self.store.compaction_epoch(ROLE_AUTHOR, req["id"]) is None and rp.is_file():
                    window, _, _ = parse_compact_body(rp.read_bytes(), model=self.model)
                    self.log(f"[resume] re-applying the completed compaction {req['id']}")
                    self.apply_compaction(req, window)
                    current_epoch = self.store.epoch(ROLE_AUTHOR)
                continue
            # only a response of the current epoch can still be missing from the window (earlier epochs were compacted away), and a
            # completed response recorded with an error (unknown item types, an error object) was judged, not applied: never replayed
            if req.get("epoch") != current_epoch or req.get("error"):
                continue
            if rp.is_file() and not any(i["request_id"] == req["id"] for i in self.store.items(ROLE_AUTHOR, epoch=current_epoch)):
                parsed = parse_response_body(rp.read_bytes(), model=self.model)
                self.log(f"[resume] re-applying the completed response {req['id']} to the conversation")
                self.handle_response(req, parsed, replay=True)
        for op in self.store.operations(state="running"):
            identity = (op.get("worker") or {})
            has_worker = bool(identity.get("kind"))
            status = self.worker.reattach(identity) if has_worker else "lost"
            self.store.event("operation_reattach", operation=op["id"], status=status)
            note = {"running": "the worker was still running after a restart; the runner cannot re-own it and records it as interrupted",
                    "finished": "the worker had finished but its output was never ingested by a live runner; recorded as interrupted (no partial promotion)",
                    "lost": "the worker is gone; its output was never ingested"}[status]
            if not has_worker:
                note = "the tool was executing when the runner stopped; its result was never committed (interrupted, not rerun)"
            if status in ("running", "finished"):
                try:
                    self.worker.stop(identity)
                except Exception:  # noqa: BLE001
                    pass
            recovered = self.recovered_result(op)
            if recovered is not None:
                self.store.update_operation(op["id"], state="completed", result_json=recovered["summary"], completed_utc=self.store.now())
                result = T.ToolResult(recovered["text"])
            else:
                self.store.update_operation(op["id"], state="interrupted", result_json={"error": note, "category": "interrupted"}, completed_utc=self.store.now())
                result = T.ToolResult({"error": note, "tool": op["tool_name"], "category": "interrupted"})
            for rev in self.store.revisions():
                owned = rev.get("operation_id") == op["id"] or (op.get("source_revision_id") == rev["id"] and rev["state"] in ("building", "observing"))
                if owned and rev["state"] in ("building", "built", "exported", "export_failed", "observing"):
                    # the revision this operation was building can be rebuilt (build_candidate accepts 'interrupted'), never left half-done
                    self.store.update_revision(rev["id"], state="interrupted", compatibility_json={"compatible": False, "reasons": ["interrupted"]})
            if not op["output_committed"]:
                # a render sub-operation shares its parent's call_id (the parent owes the output); a host operation answers no call
                if op["tool_name"] in T.TOOLS and not str(op["call_id"]).startswith(HOST_CALL_PREFIX):
                    item, _ = T.output_for(self.context(), op["call_id"], result)
                    self.store.append_items(ROLE_AUTHOR, [item])
                self.store.update_operation(op["id"], output_committed=1)
        # images 'included' in a request: acknowledged when exactly that request completed, back to the queue when it did not
        for o in self.store.observations(state="included"):
            if not o["included_request_id"]:
                continue        # committed into the window and not yet sent: the next author request carries it
            req = self.store.request(o["included_request_id"])
            if req is not None and req["state"] == "completed" and not req.get("error"):
                self.store.set_observation(o["artifact_id"], "acknowledged", acknowledged_request_id=req["id"])
            else:
                self.store.set_observation(o["artifact_id"], "pending")
        # every function_call in the window needs exactly one output before the next request
        self.ensure_outputs_for_calls()
        unknown = self.unknown_liabilities()
        state = self.store.state()
        if unknown and state not in TERMINAL_STATES and state != "needs_attention":
            try:
                self.store.transition("needs_attention", f"{len(unknown)} inference request(s) with unknown outcome; reconcile the provider dashboard "
                                      f"(status shows the liability; `reconcile-unknown` closes it) before resuming", stop_reason="inference_unknown")
            except TransitionError:
                pass
            state = self.store.state()      # the durable stop above must not be undone by the repairs below
        if state == "inference_pending" and not self.store.requests(state="sent"):
            self.store.transition("ready", "no request in flight after reconciliation")
        if state == "tool_running":
            self.store.transition("tool_pending" if self.store.operations(state="pending") else "ready", "recovered")

    def recovered_result(self, op: dict) -> dict | None:
        """A tool whose paid side effect completed before the runner died is not bought twice: a critic verdict recorded
        for the operation's revision after it started is reused as the operation's result."""
        if op["tool_name"] != "request_critic" or not op.get("started_utc"):
            return None
        rid = (op.get("args") or {}).get("revision_id")
        for v in reversed(self.store.verdicts(kind="critic")):
            if v["revision_id"] == rid and v["created_utc"] >= op["started_utc"]:
                text = {"revision": rid, "critique": v["record"], "recovered": "the critic answered before the runner stopped; its verdict is reused, not requested again"}
                return {"text": text, "summary": {"summary": {"revision": rid, "recovered": True}, "images": []}}
        return None

    def _mark_unknown(self, reservation_id: str, reason: str, *, request_id: str) -> None:
        try:
            self.budget.mark_unknown(reservation_id, reason, request_id=request_id)
        except BudgetError as e:        # already settled or released before a crash: recorded; the request row is still closed by the caller
            self.store.event("settlement_problem", request=request_id, error=str(e))

    def ensure_outputs_for_calls(self) -> None:
        items = self.store.items(ROLE_AUTHOR)
        calls = {i["item"]["call_id"] for i in items if i["item"].get("type") == "function_call"}
        outs = {i["item"]["call_id"] for i in items if i["item"].get("type") == "function_call_output"}
        for call_id in sorted(calls - outs):
            ops = self.store.operations(call_id=call_id)
            if ops and ops[-1]["state"] == "pending":
                continue        # will be executed
            self.store.append_items(ROLE_AUTHOR, [T.output_for(self.context(), call_id, T.ToolResult({"error": "this call has no recorded result (restart)", "category": "interrupted"}))[0]])

    # ------------------------------------------------------------------ inference
    def count_tokens(self, payload: dict) -> int:
        try:
            res = self.transport.post(ENDPOINT_COUNT, count_payload(payload), timeout=(15, 120))
        except (UnknownOutcome, TransportError) as e:
            raise BudgetError(f"input token count unavailable ({type(e).__name__}: {e}); refusing to infer without an exact count") from None
        if res.status != 200:
            raise BudgetError(f"input token count endpoint returned HTTP {res.status}; refusing to infer")
        return parse_count_body(res.body)

    def request_once(self, *, role: str, purpose: str, payload: dict, epoch: int | None, endpoint: str = ENDPOINT, on_prepared=None) -> tuple[dict, ParsedResponse | None]:
        """Reserve, persist, send once, persist the reply, settle. Returns (request row, parsed response or None).
        ``on_prepared(req)`` runs in the transaction that creates the request row (the author step binds the carried images there).
        The lease fence is required before the count call, inside the transaction that creates the request row and again (with a
        renewal covering the whole HTTP call) in the transaction that marks it sent: a runner whose lease was taken over posts nothing
        and leaves no row (LeaseError propagates; the intruder's reconcile releases anything prepared)."""
        self.require_fence()
        count = self.count_tokens(payload)
        max_out = int(payload.get("max_output_tokens") or self.policy["compact_output_bound_tokens"] or 0)
        read_s = read_timeout_s(max_out, count)
        with self.store.tx():
            # one transaction: a reservation never exists without its request row (a crash between them would shrink the cap for nothing),
            # and neither exists under a stale fence (the count call above may have outlived the lease)
            self.require_fence()
            reservation = self.budget.reserve(role=role, purpose=purpose, input_tokens=count, max_output_tokens=max_out)
            req = self.store.insert_request(role=role, purpose=purpose, epoch=epoch, input_sha256=window_input_sha256(payload["input"]), reservation_id=reservation["id"],
                                            input_token_count=count)
            self.budget.bind_request(reservation["id"], req["id"])
            if on_prepared is not None:
                on_prepared(req)
        rdir = self.store.job_dir / "host" / "requests" / req["id"]
        raw = canonical(payload)
        atomic_write(rdir / "payload.json", raw)
        with self.store.tx():
            # renewed right before the POST, inside the write lock: the fence is re-checked and the lease covers the HTTP call; on a stale
            # fence the row stays 'prepared' (never sent) for the new holder's reconcile to release
            self.require_fence()
            # the lease outlives the longest legal reply (INF-02: the read timeout now follows max_output_tokens, up to ~1000 s)
            self.store.renew_lease(self.holder, self.fence, max(LEASE_TTL_S, read_s + LEASE_MARGIN_S))
            self.store.update_request(req["id"], payload_sha256=sha256(raw), payload_path=str(rdir / "payload.json"), state="sent", sent_utc=self.store.now())
        self.store.event("inference_sent", request=req["id"], role=role, purpose=purpose, input_tokens=count, max_output_tokens=max_out, reserved_micro=reservation["reserved_micro"],
                         images=image_count_in(payload.get("input") or []), read_timeout_s=read_s)
        try:
            res = self.transport.post(endpoint, payload, timeout=(CONNECT_TIMEOUT_S, read_s))
        except UnknownOutcome as e:
            self.store.update_request(req["id"], state="unknown", error=f"{type(e).__name__}: {e}")
            self.budget.mark_unknown(reservation["id"], f"send outcome unknown: {e}", request_id=req["id"])
            self.store.event("inference_unknown", request=req["id"], error=str(e))
            return self.store.request(req["id"]), None
        except (PaidRefused, TransportError) as e:
            self.store.update_request(req["id"], state="refused", error=f"{type(e).__name__}: {e}")
            self.budget.release(reservation["id"], f"never sent: {type(e).__name__}: {e}")
            self.store.event("inference_refused", request=req["id"], error=str(e))
            return self.store.request(req["id"]), None
        atomic_write(rdir / "response.json", res.body)
        self.store.update_request(req["id"], response_path=str(rdir / "response.json"), response_sha256=sha256(res.body), http_status=res.status)
        row, parsed = self.complete_request_from_file(self.store.request(req["id"]), rdir / "response.json", endpoint=endpoint)
        self.note_output_use(row, role=role, purpose=purpose, max_output_tokens=max_out)
        return row, parsed

    def note_output_use(self, req: dict, *, role: str, purpose: str, max_output_tokens: int) -> None:
        """An 'output_near_bound' event when a reply used OUTPUT_NEAR_BOUND or more of its output bound (test-pilot-002's critic
        used 90% of it silently)."""
        out = (req.get("usage") or {}).get("output_tokens")
        if isinstance(out, int) and max_output_tokens and out >= OUTPUT_NEAR_BOUND * max_output_tokens:
            self.store.event("output_near_bound", request=req["id"], role=role, purpose=purpose, output_tokens=out, max_output_tokens=max_output_tokens,
                             share=round(out / max_output_tokens, 3))

    def note_cache_use(self, req: dict) -> None:
        """Rolling mode: a 'cache_miss' event when an author request read less than the previous author request's input of the
        same epoch from the cache (run 1 missed once after a 13.5-minute gap inside the documented 30-minute lifetime)."""
        if self.policy.get("cache_mode") != "explicit_rolling" or not req.get("usage"):
            return
        prior = [r for r in self.store.requests(role=ROLE_AUTHOR) if r["purpose"] == "author" and r["seq"] < req["seq"] and r.get("epoch") == req.get("epoch")
                 and r["state"] == "completed" and r.get("usage")]
        if not prior:
            return
        prev = prior[-1]
        details = req["usage"].get("input_tokens_details") or {}
        cached, prev_in = int(details.get("cached_tokens") or 0), int(prev["usage"].get("input_tokens") or 0)
        if cached < prev_in - CACHE_MISS_SLACK_TOKENS:
            self.store.event("cache_miss", request=req["id"], previous_request=prev["id"], previous_input_tokens=prev_in, cached_tokens=cached,
                             cache_write_tokens=int(details.get("cache_write_tokens") or 0), previous_sent_utc=prev.get("sent_utc"), sent_utc=req.get("sent_utc"))

    def complete_request_from_file(self, req: dict, response_path: Path, *, endpoint: str | None = None) -> tuple[dict, ParsedResponse | None]:
        """Parse a durable response, settle once, mark the request; used live and on replay (no HTTP)."""
        body = response_path.read_bytes()
        status = req.get("http_status")
        rid = req["id"]
        if status is None:
            status = 200 if body[:1] == b"{" else 0
        if status != 200:
            if req.get("reservation_id"):
                try:
                    if 400 <= int(status) < 500:
                        self.budget.settle_rejected(req["reservation_id"], request_id=rid, http_status=int(status), reason=body[:300].decode("utf-8", "replace"))
                    else:
                        self.budget.mark_unknown(req["reservation_id"], f"HTTP {status} without usable usage", request_id=rid)
                except BudgetError as e:
                    # settled before a crash (the replay of a durable 4xx): recorded, and the request row is still closed below
                    self.store.event("settlement_problem", request=rid, error=str(e))
            self.store.update_request(rid, state="failed", error=f"HTTP {status}: {body[:400].decode('utf-8', 'replace')}", completed_utc=self.store.now())
            self.store.event("inference_failed", request=rid, http_status=status)
            return self.store.request(rid), None
        is_compact = (endpoint == ENDPOINT_COMPACT) or req["purpose"] == "compaction"
        try:
            if is_compact:
                window, usage, _ = parse_compact_body(body, model=self.model)
                parsed = ParsedResponse(status="completed", response_id=None, model=self.model, items=window, usage=usage)
            else:
                parsed = parse_response_body(body, model=self.model)
        except TransportError as e:
            if req.get("reservation_id"):
                self._mark_unknown(req["reservation_id"], f"unparsable 200 body: {e}", request_id=rid)
            self.store.update_request(rid, state="failed", error=f"unparsable response: {e}", completed_utc=self.store.now())
            self.store.event("inference_failed", request=rid, error=str(e))
            return self.store.request(rid), None
        if req.get("reservation_id"):
            if parsed.usage is not None:
                try:
                    self.budget.settle(req["reservation_id"], parsed.usage, request_id=rid)
                except BudgetError as e:
                    self.store.event("settlement_problem", request=rid, error=str(e))
            else:
                self._mark_unknown(req["reservation_id"], "response without a usage block", request_id=rid)
        state = "completed" if parsed.status == "completed" else ("incomplete" if parsed.status == "incomplete" else "failed")
        self.store.update_request(rid, state=state, provider_response_id=parsed.response_id, response_model=parsed.model, usage_json=parsed.usage,
                                  completed_utc=self.store.now(), error=json.dumps(parsed.error or parsed.incomplete_details) if (parsed.error or parsed.incomplete_details) else None)
        self.store.event("inference_completed", request=rid, status=parsed.status, calls=[fc.name for fc in parsed.function_calls], unknown_types=parsed.unknown_types,
                         usage=parsed.usage, response_id=parsed.response_id)
        return self.store.request(rid), parsed

    def single_call(self, *, role: str, purpose: str, developer: str, blocks: list[dict], tools: dict, tool_name: str, max_output_tokens: int) -> tuple[dict | None, dict]:
        """A fresh-context role (intake, critic, final): one forced tool call, its own epoch, the shared budget. Refused
        (InferenceUnknownOutstanding, nothing written or sent) while an unknown outcome is unreconciled; LeaseError under a stale fence."""
        self.require_fence()
        self.refuse_while_unknown()
        with self.store.tx():
            self.require_fence()
            epoch = self.store.new_epoch(role, f"{purpose}: fresh context")
            self.store.append_items(role, [user_message(blocks)], epoch=epoch)
        payload = build_payload(model=self.model, developer_text=developer, window=self.store.window(role), tools=tools, max_output_tokens=max_output_tokens,
                                reasoning_effort=self.effort, tool_choice={"type": "function", "name": tool_name}, cache_mode="none",
                                service_tier=self.policy["service_tier"])
        req, parsed = self.request_once(role=role, purpose=purpose, payload=payload, epoch=epoch)
        meta = {"request": req["id"], "state": req["state"], "usage": req.get("usage"), "response_id": req.get("provider_response_id"),
                "max_output_tokens": max_output_tokens}
        if parsed is None or parsed.status != "completed":
            meta["error"] = req.get("error") or f"response status {getattr(parsed, 'status', None)}"
            if parsed is not None:
                # what an incomplete reply carried (a truncated call's partial arguments): the caller reports it instead of dropping it
                meta.update(status=parsed.status, incomplete_details=parsed.incomplete_details,
                            received={"arguments": [fc.arguments_raw for fc in parsed.function_calls], "texts": [t["text"] for t in parsed.texts]})
            return None, meta
        with self.store.tx():
            self.require_fence()
            self.store.append_items(role, parsed.replayable_items, request_id=req["id"], epoch=epoch)
        if parsed.unknown_types:
            meta["error"] = f"unknown output item types {parsed.unknown_types}"
            return None, meta
        calls = [fc for fc in parsed.function_calls if fc.name == tool_name]
        if len(calls) != 1:
            meta["error"] = f"expected exactly one {tool_name} call, got {[fc.name for fc in parsed.function_calls]}"
            return None, meta
        args, err = validate_call_arguments(calls[0], tools)
        if err:
            meta["error"] = err
            return None, meta
        return args, meta

    # ------------------------------------------------------------------ author step
    def author_step(self) -> None:
        self.require_fence()
        unknown = self.unknown_liabilities()
        if unknown:
            # a possibly charged request is outstanding (a critic call that timed out, a compaction whose outcome is unknown): no further paid
            # send until the owner reconciles it; the tool output that reported it is already committed
            self.store.event("inference_refused_unknown_outstanding", requests=unknown)
            self.stop_for_unknown(unknown, expected=("ready", "needs_observation"))
            return
        self.maybe_prune_images()
        if self.maybe_compact() == "stopped":
            return
        window = self.store.window(ROLE_AUTHOR)
        payload = build_payload(model=self.model, developer_text=self.developer, window=window, tools=T.TOOLS, max_output_tokens=int(self.policy["max_output_tokens"]),
                                reasoning_effort=self.effort, tool_choice="required", cache_mode=self.policy["cache_mode"], service_tier=self.policy["service_tier"])
        self.store.transition("inference_pending", "author request", expected=("ready", "needs_observation"))
        # every 'included' image is in the window and unsent (reconcile guarantees it): bound to this request from the moment its row
        # exists, acknowledged only when exactly this request completes; a binding left from an earlier request is history, overwritten here
        included = self.store.observations(state="included")

        def tag(req: dict) -> None:
            for o in included:
                self.store.set_observation(o["artifact_id"], "included", included_request_id=req["id"])

        try:
            req, parsed = self.request_once(role=ROLE_AUTHOR, purpose="author", payload=payload, epoch=self.store.epoch(ROLE_AUTHOR), on_prepared=tag)
        except BudgetExhausted as e:
            self.store.event("budget_exhausted", error=str(e))
            self.store.transition("ready", "budget refused the request", expected="inference_pending")
            self.finalize_fallback("budget_exhausted")
            return
        except BudgetError as e:
            self.store.transition("needs_attention", f"budget/count problem: {e}", expected="inference_pending", stop_reason="budget_error")
            return
        if parsed is None:
            if req["state"] == "unknown":
                self.store.transition("inference_unknown", "send outcome unknown", expected="inference_pending")
                self.store.transition("needs_attention", "an inference request has an unknown outcome; its reservation stays counted", stop_reason="inference_unknown")
            else:
                self.store.transition("needs_attention", f"inference {req['state']}: {req.get('error')}", expected="inference_pending", stop_reason=f"inference_{req['state']}")
            for o in included:
                if req["state"] == "refused":
                    self.store.set_observation(o["artifact_id"], "included", reset_request=True)    # never sent: still in the window, still unsent
                else:
                    self.store.set_observation(o["artifact_id"], "pending")     # sent, never received: back to the queue (the binding stays as history)
            return
        self.note_cache_use(req)
        self.handle_response(req, parsed)

    def handle_response(self, req: dict, parsed: ParsedResponse, *, replay: bool = False) -> None:
        self.require_fence()        # a response is applied only by the runner that still owns the job
        state = self.store.state()
        included = [o for o in self.store.observations(state="included") if o["included_request_id"] == req["id"]]
        if parsed.status != "completed" or parsed.error:
            # incomplete / failed: archived and settled; nothing executed, nothing acknowledged, items not replayed; the images go back
            # to the queue (their binding to this request stays as history; re-inclusion rebinds them)
            for o in included:
                self.store.set_observation(o["artifact_id"], "pending")
            n = int(self.store.setting("consecutive_incomplete", 0)) + 1
            self.store.set_setting("consecutive_incomplete", n)
            self.store.event("response_not_completed", request=req["id"], status=parsed.status, details=parsed.incomplete_details or parsed.error)
            if n >= MAX_INCOMPLETE_RESPONSES:
                self.store.transition("needs_attention", f"{n} consecutive incomplete/failed responses", expected=state if state != "needs_attention" else None,
                                      stop_reason="responses_incomplete")
                return
            self.store.append_items(ROLE_AUTHOR, [user_message([text_block(json.dumps({"host_notice": f"your previous response was {parsed.status} "
                                                                                       f"({parsed.incomplete_details or parsed.error}); it was not applied. Answer with one tool call, "
                                                                                       f"within {self.policy['max_output_tokens']} output tokens."}))])], request_id=req["id"])
            if state == "inference_pending":
                self.store.transition("ready", "incomplete response; asking again")
            return
        if parsed.unknown_types:
            self.store.event("unknown_output_types", request=req["id"], types=parsed.unknown_types)
            # judged, not applied: the recorded error keeps reconcile from replaying it into needs_attention on every resume
            self.store.update_request(req["id"], error=f"unknown output item types {parsed.unknown_types}; the response bytes are preserved, nothing was applied")
            self.store.transition("needs_attention", f"unknown response item types {parsed.unknown_types}; bytes preserved", stop_reason="unknown_output_type")
            return
        self.store.set_setting("consecutive_incomplete", 0)
        if not replay:
            self.store.append_items(ROLE_AUTHOR, parsed.replayable_items, request_id=req["id"])
        else:
            self.store.append_items(ROLE_AUTHOR, parsed.replayable_items, request_id=req["id"])
        for o in included:
            self.store.set_observation(o["artifact_id"], "acknowledged", acknowledged_request_id=req["id"])
        if included:
            self.store.event("images_acknowledged", request=req["id"], images=[o["artifact_id"] for o in included])
        pending_auto = self.store.setting("pending_delivery")
        if pending_auto and pending_auto.get("auto"):
            names = [fc.name for fc in parsed.function_calls]
            if names and names != ["request_delivery"]:
                self.store.set_setting("pending_delivery", None)
                self.store.event("auto_delivery_cancelled", by=names)
            elif not names or names == ["request_delivery"]:
                # the author confirmed (or said nothing more): deliver the revision whose images it has just received
                self.store.set_setting("pending_delivery", dict(pending_auto, auto=False, confirmed_by=req["id"]))
                if not parsed.function_calls:
                    if state == "inference_pending":
                        self.store.transition("ready_for_final", "auto delivery confirmed")
                    return
        job = self.store.job()
        if not parsed.function_calls:
            n = int(self.store.setting("consecutive_text_only", 0)) + 1
            self.store.set_setting("consecutive_text_only", n)
            self.store.event("text_only_response", request=req["id"], texts=[t["text"][:300] for t in parsed.texts], refusals=parsed.refusals)
            if n >= MAX_TEXT_ONLY_RESPONSES:
                self.store.transition("ready", "author stopped without delivery", expected="inference_pending")
                self.finalize_fallback("author_stopped_without_delivery")
                return
            self.store.append_items(ROLE_AUTHOR, [user_message([text_block(json.dumps({"host_notice": "no tool was called; every message must call one tool. To stop, "
                                                                                       "call request_delivery with a compatible revision whose images you have received."}))])])
            self.store.transition("ready", "text-only response", expected="inference_pending")
            return
        self.store.set_setting("consecutive_text_only", 0)
        for fc in parsed.function_calls:
            existing = self.store.operations(call_id=fc.call_id)
            if existing:
                continue
            args, err = validate_author_call(fc)
            op = self.store.insert_operation(call_id=fc.call_id, request_id=req["id"], tool_name=fc.name if fc.name in T.TOOLS else "invalid", schema_version=T.TOOLS_VERSION,
                                             args=args if err is None else {"rejected": fc.arguments_raw[:4000]}, source_revision_id=job["current_revision"], fence=self.fence)
            if err is not None:
                item, _ = T.output_for(self.context(), fc.call_id, T.ToolResult({"error": err, "tool": fc.name, "category": "invalid_arguments",
                                                                                "state": T.state_summary(self.context())}))
                self.store.append_items(ROLE_AUTHOR, [item])
                self.store.update_operation(op["id"], state="failed", output_committed=1, result_json={"error": err, "category": "invalid_arguments"}, completed_utc=self.store.now())
                self.store.event("invalid_arguments", call_id=fc.call_id, tool=fc.name, error=err)
        if self.store.operations(state="pending"):
            self.store.transition("tool_pending", "tool call(s) recorded", expected=("inference_pending", "ready", "needs_observation", "tool_running"))
        else:
            if self.store.state() == "inference_pending":
                self.store.transition("ready", "no executable call")

    # ------------------------------------------------------------------ tools
    def execute_pending_operations(self) -> None:
        self.store.transition("tool_running", "executing", expected="tool_pending")
        ctx = self.context()
        for op in self.store.operations(state="pending"):
            self.store.require_fence(self.holder, self.fence)
            if self.store.job()["cancel_requested"]:
                self.cancel_now()
                return
            # running from here: a crash before the commit reconciles as one interrupted output, never as a second execution (a paid critic twice)
            self.store.update_operation(op["id"], state="running", started_utc=self.store.now(), fence=self.fence)
            self.log(f"[tool] {op['id']} {op['tool_name']} {json.dumps(op['args'], default=str)[:200]}")
            result = T.execute(ctx, op)
            note = T.state_summary(ctx)
            item, image_ids = T.output_for(ctx, op["call_id"], result, state_note=note)
            try:
                with self.store.tx():
                    self.store.require_fence(self.holder, self.fence)       # inside the write lock: a runner whose lease was taken over publishes nothing
                    self.store.append_items(ROLE_AUTHOR, [item])
                    for aid in image_ids:
                        self.store.set_observation(aid, "included", reset_request=True)    # in the window, unsent: bound to the request that carries it
                    fields = {"output_committed": 1, "completed_utc": self.store.now()}
                    cur = self.store.operation(op["id"])
                    if cur["state"] in ("pending", "running"):
                        fields["state"] = "completed" if "error" not in result.text else "failed"
                        if cur.get("result") is None:
                            fields["result_json"] = {"summary": {k: v for k, v in result.text.items() if k in ("error", "revision", "category", "selected_revision", "delivery_requested")},
                                                     "images": image_ids}
                    self.store.update_operation(op["id"], **fields)
            except LeaseError:
                self.store.event("stale_runner_output_refused", operation=op["id"], holder=self.holder, fence=self.fence)
                raise
            self.store.event("tool_completed", operation=op["id"], tool=op["tool_name"], images=image_ids, error=result.text.get("error"))
            if result.delivery:
                d = dict(result.delivery)
                if d.get("auto"):
                    pending_required = [o for o in self.store.observations(revision_id=d["revision"]) if o["required"] and o["state"] == "pending"]
                    if pending_required:
                        self.store.event("auto_delivery_deferred", revision=d["revision"], pending=len(pending_required))
                        d = None
                if d is not None:
                    self.store.set_setting("pending_delivery", d)
                    if not d.get("auto"):
                        self.store.transition("ready_for_final", "delivery requested", expected="tool_running")
                        return
        self.store.transition("needs_observation" if self.store.observations(state="pending") else "ready", "tools done", expected="tool_running")

    def critic_callable(self):
        mode = self.policy.get("critic", "none")
        if mode == "none":
            return None

        def critic(ctx: T.ToolContext, rev: dict, question: str) -> dict:
            blocks = critic_blocks(self.store, self.artifacts, self.evidence(), rev, question)
            max_out = min(int(self.policy["max_output_tokens"]), CRITIC_MAX_OUTPUT_TOKENS)
            try:
                args, meta = self.single_call(role=ROLE_CRITIC, purpose="critic", developer=CRITIC_TASK, blocks=blocks, tools=CRITIC_TOOL, tool_name="report_critique",
                                              max_output_tokens=max_out)
            except BudgetExhausted as e:
                raise T.ToolError(f"the budget cannot afford a critic call: {e}") from None
            except InferenceUnknownOutstanding as e:
                # the failed tool output is committed by the runner (one output per call_id); the job then stops before the next author send
                raise T.ToolError(f"critic call refused: {e}") from None
            if args is None:
                if meta.get("status") == "incomplete":
                    # a truncated critique is reported with what arrived: the operation is spent either way, the partial text is not dropped
                    received = meta.get("received") or {}
                    partial = "".join(received.get("arguments") or []) or "\n".join(received.get("texts") or [])
                    reason = (meta.get("incomplete_details") or {}).get("reason")
                    used = (meta.get("usage") or {}).get("output_tokens")
                    self.store.event("critic_truncated", request=meta["request"], revision=rev["id"], reason=reason, output_tokens=used,
                                     max_output_tokens=max_out, received_chars=len(partial))
                    raise T.ToolError(f"the critic's reply was truncated ({reason}; {used} of {max_out} output tokens) and cannot be validated; the operation "
                                      f"is spent. What arrived ({len(partial)} characters of partial report_critique arguments): {partial[:6000]}")
                raise T.ToolError(f"critic call failed: {meta.get('error')}")
            verdict = validate_critique(args)
            # bound to exactly the images the critic was sent (the photos and the revision's author-visible sheets and renders);
            # until 2026-09-28 it listed every non-sealed sheet / render, the host-only singles the critic never sees included
            self.store.append_verdict(kind="critic", revision_id=rev["id"], asset_sha256=rev.get("glb_sha256"), verdict=verdict["verdict"],
                                      bindings={"request": meta["request"], "images": sent_image_ids(blocks)},
                                      record=verdict)
            return verdict
        return critic

    # ------------------------------------------------------------------ image prune epochs
    def prunable_image_ids(self) -> set[str]:
        """Acknowledged images of revisions superseded by a newer revision whose images the author has received. The newest
        such revision and the selected revision keep theirs; photos (no revision) and unacknowledged images are never pruned."""
        acked = [o for o in self.store.observations(state="acknowledged") if o.get("revision_id")]
        if not acked:
            return set()
        seq = {r["id"]: r["seq"] for r in self.store.revisions()}
        keep = {max((o["revision_id"] for o in acked), key=lambda rid: seq.get(rid, 0)), self.store.job()["selected_revision"]}
        return {o["artifact_id"] for o in acked if o["revision_id"] not in keep}

    def maybe_prune_images(self) -> bool:
        """F2/F9: seen sheet images were re-sent on every turn; the author context grew ~19k tokens per edit and reached the
        200k compaction threshold, where the run stops (compact_output_bound_tokens unset), after ~9 edits. When the last
        author request of this epoch counted ``policy['image_prune_tokens']`` or more, a new epoch starts holding the same
        window with every superseded revision's images replaced by a stable text stub (``prune_window_images``).

        The epoch boundary is the only place the window changes, so the cached prefix survives up to the first pruned
        image and every request after it is append-only again; pruning rarely (a threshold near the compaction threshold)
        keeps the one re-write per event small. Without the policy key (jobs before 2026-09-28) nothing is pruned. The images
        stay acknowledged; crop_image returns any of them again."""
        limit = self.policy.get("image_prune_tokens")
        if not limit:
            return False
        epoch = self.store.epoch(ROLE_AUTHOR)
        last = [r for r in self.store.requests(role=ROLE_AUTHOR) if r["purpose"] == "author" and r["input_token_count"] is not None]
        if not last or last[-1].get("epoch") != epoch or int(last[-1]["input_token_count"]) < int(limit):
            return False
        if self.store.operations(state="pending") or self.store.operations(state="running") or self.store.requests(state="sent"):
            return False
        rows = self.store.items(ROLE_AUTHOR)
        pruned_window, pruned = prune_window_images([r["item"] for r in rows], self.prunable_image_ids())
        if len(pruned) < MIN_PRUNED_IMAGES:
            return False
        with self.store.tx():
            self.require_fence()
            new = self.store.new_epoch(ROLE_AUTHOR, "image_prune")
            # item by item with the request each came from: reconcile finds a completed response's items by request id
            for row, item in zip(rows, pruned_window):
                self.store.append_items(ROLE_AUTHOR, [item], request_id=row["request_id"], epoch=new)
        self.store.event("images_pruned", epoch=new, images=pruned, items=len(rows), trigger_request=last[-1]["id"],
                         trigger_input_tokens=int(last[-1]["input_token_count"]), threshold=int(limit))
        self.log(f"[prune] epoch {new}: {len(pruned)} superseded images replaced by stubs (last input {last[-1]['input_token_count']} >= {limit})")
        return True

    # ------------------------------------------------------------------ compaction
    def maybe_compact(self) -> str | None:
        last = [r for r in self.store.requests(role=ROLE_AUTHOR) if r["purpose"] == "author" and r["input_token_count"] is not None]
        if not last or int(last[-1]["input_token_count"]) < int(self.policy["compact_threshold_tokens"]):
            return None
        if last[-1].get("epoch") != self.store.epoch(ROLE_AUTHOR):
            return None         # that count measured a window compacted since; the next author request measures the new epoch
        if self.store.operations(state="pending") or self.store.operations(state="running") or self.store.requests(state="sent"):
            return None
        unknown = self.unknown_liabilities()
        if unknown:
            self.store.event("inference_refused_unknown_outstanding", requests=unknown, purpose="compaction")
            self.stop_for_unknown(unknown)
            return "stopped"
        bound = self.policy.get("compact_output_bound_tokens")
        if not bound:
            self.store.event("compaction_refused", reason="no verified output bound for the compact endpoint (compact_output_bound_tokens unset)")
            self.finalize_fallback("context_limit_compaction_unbounded")
            return "stopped"
        window = self.store.window(ROLE_AUTHOR)
        payload = compact_payload(model=self.model, developer_text=self.developer, window=window, tools=T.TOOLS)
        epoch = self.store.epoch(ROLE_AUTHOR)

        def note(req: dict) -> None:        # in the transaction that creates the row, before anything is sent
            self.store.event("compaction_sent", request=req["id"], input_tokens=req["input_token_count"], epoch=epoch)

        try:
            # the same fenced, reserved, single-send path as every other inference; the compact body carries no max_output_tokens, so the
            # reservation takes the configured output bound (request_once)
            req, parsed = self.request_once(role=ROLE_AUTHOR, purpose="compaction", payload=payload, epoch=epoch, endpoint=ENDPOINT_COMPACT, on_prepared=note)
        except BudgetExhausted as e:
            self.store.event("compaction_refused", reason=f"budget: {e}")
            self.finalize_fallback("context_limit_compaction_unaffordable")
            return "stopped"
        except BudgetError as e:
            self.store.transition("needs_attention", f"compaction count/budget problem: {e}", stop_reason="compaction_error")
            return "stopped"
        if parsed is None:
            if req["state"] == "unknown":
                self.store.transition("needs_attention", "compaction outcome unknown; the window is unchanged", stop_reason="compaction_unknown")
            elif req["state"] == "refused":
                self.finalize_fallback("context_limit_compaction_refused")
            else:
                self.store.transition("needs_attention", f"compaction failed: {req.get('error')}; the window is unchanged", stop_reason="compaction_failed")
            return "stopped"
        self.apply_compaction(req, parsed.items)
        return None

    def apply_compaction(self, req: dict, returned: list[dict]) -> None:
        """Atomically start a new epoch holding the entire returned canonical window (our developer message is
        re-sent on every request, so a returned copy of it is dropped) plus a host checkpoint of the immutable facts.
        Images committed into the old window but never carried by a request go back to the queue: the compacted
        window may not hold their bytes, so nothing may acknowledge them blindly."""
        window = [i for i in returned if not (i.get("type") == "message" and i.get("role") == "developer")]
        job = self.store.job()
        with self.store.tx():
            self.require_fence()        # a new epoch is written only by the runner that still owns the job
            requeued = [o["artifact_id"] for o in self.store.observations(state="included") if not o["included_request_id"]]
            for aid in requeued:
                self.store.set_observation(aid, "pending", reset_request=True)
            checkpoint = {"host_checkpoint_after_compaction": True, "request": req["id"], "current_revision": job["current_revision"], "selected_revision": job["selected_revision"],
                          "revisions": T.state_summary(self.context())["revisions"],
                          "photos": [{"artifact_id": a["id"], "label": a["label"]} for a in self.store.artifacts(role=AUTHOR_VISIBLE, kind="photo")],
                          "images_by_revision": {r["id"]: [a["id"] for a in self.store.artifacts(revision_id=r["id"]) if a["kind"] in ("sheet", "render", "crop") and a["role"] in (AUTHOR_VISIBLE, SYNTHETIC)]
                                                 for r in self.store.revisions()},
                          "pending_images": [o["artifact_id"] for o in self.store.observations(state="pending")],
                          "limits": {k: self.policy[k] for k in ("max_revisions", "max_worker_seconds", "images_per_request", "wall_minutes")},
                          "note": "the conversation was compacted; earlier images can be requested again by artifact id (crop_image / fetch_pending_images); the goal, rules and tools are unchanged; "
                                  "list_evidence with reference: true returns the rules and the helper reference of the first message"}
            epoch = self.store.new_epoch(ROLE_AUTHOR, "compaction", compact_request_id=req["id"])
            self.store.append_items(ROLE_AUTHOR, window + [user_message([text_block(json.dumps(checkpoint, default=str))])], request_id=req["id"], epoch=epoch)
        self.store.event("compaction_applied", request=req["id"], epoch=epoch, returned_items=len(returned), kept_items=len(window), requeued_images=requeued)

    # ------------------------------------------------------------------ finalization
    def finalize(self, delivery: dict, *, stop_reason: str) -> None:
        rev = self.store.revision(delivery["revision"]) if delivery.get("revision") else None
        if rev is None:
            self.store.event("delivery_revision_missing", delivery=delivery)
            self.store.set_setting("pending_delivery", None)
            self.finalize_fallback("delivery_revision_missing")
            return
        state = self.store.state()
        if state not in ("ready_for_final", "evaluating"):
            self.store.transition("ready_for_final", "finalizing", expected=("ready", "needs_observation", "tool_running"))
        if self.store.state() != "evaluating":
            self.store.transition("evaluating", "final checks and sealed evaluation", expected="ready_for_final")
        rdir = self.store.job_dir / "revisions" / rev["id"]
        problems = []
        if not (rev.get("compatibility") or {}).get("compatible"):
            problems.append("the delivered revision is not compatible")
        if not rev["synthetic"] and (not (rdir / "model.glb").is_file() or sha256((rdir / "model.glb").read_bytes()) != rev.get("glb_sha256")):
            problems.append("the GLB bytes changed since the build")
        unack = [o for o in self.store.observations(revision_id=rev["id"]) if o["required"] and o["state"] != "acknowledged"]
        if unack:
            problems.append(f"{len(unack)} required images were never received by the author")
        if not problems and self.owner_review() and self.await_owner(rev, delivery, source="author", stop_reason=stop_reason):
            return          # the owner decides in the live try-on; nothing is delivered until they accept
        final, axes, final_meta = None, None, {}
        if not problems:
            try:
                final, final_meta = self.run_final(rev)
            except InferenceUnknownOutstanding as e:
                # the delivery intent stays recorded: `reconcile-unknown` then `resume --acknowledge-attention` finalizes this same revision
                self.stop_for_unknown(e.requests, expected="evaluating")
                return
            axes = final_axes(rev, final, self.protocol(), evaluation_error=final_meta.get("error"), heldout=read_heldout(rdir),
                              revision_dir=rdir, evidence=self.evidence(), final_meta=final_meta)
        else:
            axes = final_axes(rev, None, self.protocol(), evaluation_error="; ".join(problems), heldout=read_heldout(rdir),
                              revision_dir=rdir, evidence=self.evidence(), final_meta=None)
        limitations = self.limitations(rev, final_meta)
        manifest = write_deliverable(self.store, self.artifacts, rev, revision_dir=rdir, final={"evaluation": final, "meta": final_meta, "delivery": delivery},
                                     axes=axes, stop_reason=stop_reason, budget_summary=self.budget.totals(), limitations=limitations + problems, blocking=problems)
        if manifest["asset"]:
            self.store.transition("delivered", "compatible asset delivered", expected="evaluating", stop_reason=stop_reason)
        else:
            self.store.transition("unresolved", "no compatible real asset to deliver", expected="evaluating",
                                  stop_reason=stop_reason if not rev["synthetic"] else "synthetic_demo_complete")
        self.store.set_setting("pending_delivery", None)
        self.store.set_setting("fallback_intent", None)
        self.stamp_manifest(manifest)

    def run_final(self, rev: dict) -> tuple[dict | None, dict]:
        mode = self.policy.get("final_evaluator", "none")
        if mode == "none":
            return None, {"error": "no final evaluator configured (--final-evaluator none)"}
        if rev["synthetic"]:
            return None, {"error": "synthetic revision: the final evaluator has nothing real to judge"}
        prior = [v for v in self.store.verdicts(kind="final") if v["revision_id"] == rev["id"] and v["asset_sha256"] == rev.get("glb_sha256")]
        if prior:
            # the final evaluator already judged exactly these bytes (a restart between its answer and the deliverable): reused, not bought twice
            v = prior[-1]
            return v["record"], {"request": (v["bindings"] or {}).get("request"), "bindings": v["bindings"], "recovered": "final verdict recorded before a restart; reused"}
        blocks, bindings = final_blocks(self.store, self.artifacts, self.evidence(), rev, self.protocol(), revision_dir=self.store.job_dir / "revisions" / rev["id"])
        if not bindings.get("evidence_complete", True):
            # no paid request for a verdict that cannot rise above 'unmeasured': the wearer renders of this exact candidate are required
            self.store.event("final_evaluation_skipped", revision=rev["id"], problems=bindings.get("evidence_problems"))
            return None, {"error": "required wearer evidence missing: " + "; ".join(bindings.get("evidence_problems") or []), "bindings": bindings}
        try:
            args, meta = self.single_call(role=ROLE_FINAL, purpose="final", developer=FINAL_TASK, blocks=blocks, tools=EVALUATOR_TOOL, tool_name="report_evaluation",
                                          max_output_tokens=min(int(self.policy["max_output_tokens"]), FINAL_MAX_OUTPUT_TOKENS))
        except BudgetExhausted as e:
            return None, {"error": f"budget exhausted before the final evaluation: {e}", "bindings": bindings}
        meta["bindings"] = bindings
        if args is None:
            if meta.get("status") == "incomplete":
                received = meta.get("received") or {}
                partial = "".join(received.get("arguments") or []) or "\n".join(received.get("texts") or [])
                self.store.event("final_truncated", request=meta.get("request"), revision=rev["id"], reason=(meta.get("incomplete_details") or {}).get("reason"),
                                 output_tokens=(meta.get("usage") or {}).get("output_tokens"), max_output_tokens=meta.get("max_output_tokens"), received_chars=len(partial))
                meta["error"] = f"the final evaluator's reply was truncated ({meta.get('error')}); {len(partial)} characters of partial arguments are in the request's response.json"
            return None, meta
        from .. import evaluate as mevaluate
        evaluation = mevaluate.validate_evaluation(args)
        self.store.append_verdict(kind="final", revision_id=rev["id"], asset_sha256=rev.get("glb_sha256"), verdict=evaluation["overall"], bindings=bindings, record=evaluation)
        return evaluation, meta

    def limitations(self, rev: dict | None, final_meta: dict) -> list[str]:
        out = ["automatic visual verdicts are not calibrated on unseen products; owner acceptance is a separate verdict",
               "the AR harness renders material and loading through the production renderer's retained implementation; the full live wearer loop (pose, hair, guards, fit) is not measured here",
               "millimetres are nominal unless a physical dimension was stated in the request"]
        if rev is not None and rev["synthetic"]:
            out.insert(0, "SYNTHETIC worker: no Blender ran, no export, no AR check; this job proves orchestration only")
        if final_meta.get("error"):
            out.append(f"final evaluation: {final_meta['error']}")
        if self.owner_review():
            out.append("owner review: the owner's live AR verdict decides delivery; the automatic visual axis is not the acceptance")
        if not self.ar:
            out.append("the AR renderer was disabled (--no-ar): no revision can be a production-compatible deliverable")
        return out

    def finalize_fallback(self, stop_reason: str) -> None:
        """Limits reached or the author stopped: deliver a previously observed compatible revision, or record unresolved."""
        job = self.store.job()
        chosen = None
        candidates = []
        if job["selected_revision"]:
            candidates.append(self.store.revision(job["selected_revision"]))
        candidates += [r for r in reversed(self.store.revisions()) if r["id"] != job["selected_revision"]]
        for r in candidates:
            if r is None or not (r.get("compatibility") or {}).get("compatible"):
                continue
            obs = self.store.observations(revision_id=r["id"])
            if not any(o["required"] for o in obs) or any(o["required"] and o["state"] != "acknowledged" for o in obs):
                continue        # only a revision the author has actually seen rendered can be delivered on its behalf
            if T.owner_lock_fallback_refusal(self.store, r):
                continue        # a locked change round hands the owner only its own revisions (locked modules equal) or the reviewed one
            chosen = r
            break
        if chosen is not None and self.owner_review() and stop_reason != "cancelled":
            # under the owner review a fallback hands its revision to the owner too: they can accept it, or grant another round
            state = self.store.state()
            with self.store.tx():
                if state not in ("ready_for_final", "evaluating"):
                    self.store.transition("ready_for_final", f"fallback: {stop_reason}", expected=("ready", "needs_observation", "tool_pending", "tool_running", "inference_pending"))
                self.store.set_setting("fallback_intent", {"stop_reason": stop_reason, "revision": chosen["id"]})
            self.store.event("fallback_to_owner_review", stop_reason=stop_reason, revision=chosen["id"])
            if self.await_owner(chosen, None, source="fallback", stop_reason=stop_reason):
                return
        state = self.store.state()
        with self.store.tx():
            if state not in ("ready_for_final", "evaluating"):
                self.store.transition("ready_for_final", f"fallback: {stop_reason}", expected=("ready", "needs_observation", "tool_pending", "tool_running", "inference_pending"))
            # the intent survives a crash between the transitions: run() resumes this fallback, never an author delivery
            self.store.set_setting("fallback_intent", {"stop_reason": stop_reason, "revision": chosen["id"] if chosen else None})
        self.store.event("fallback_delivery", stop_reason=stop_reason, revision=chosen["id"] if chosen else None)
        if self.store.state() != "evaluating":
            self.store.transition("evaluating", "fallback delivery", expected="ready_for_final")
        final, final_meta, axes = None, {"error": f"stopped: {stop_reason}"}, None
        if chosen is not None and stop_reason != "budget_exhausted":
            try:
                final, final_meta = self.run_final(chosen)
            except InferenceUnknownOutstanding as e:
                # fallback_intent stays recorded: run() resumes this fallback after the owner reconciles and acknowledges
                self.stop_for_unknown(e.requests, expected="evaluating")
                return
        if chosen is not None:
            axes = final_axes(chosen, final, self.protocol(), evaluation_error=final_meta.get("error"),
                              heldout=read_heldout(self.store.job_dir / "revisions" / chosen["id"]),
                              revision_dir=self.store.job_dir / "revisions" / chosen["id"], evidence=self.evidence(), final_meta=final_meta)
        manifest = write_deliverable(self.store, self.artifacts, chosen, revision_dir=self.store.job_dir / "revisions" / chosen["id"] if chosen else None,
                                     final={"evaluation": final, "meta": final_meta, "delivery": None}, axes=axes, stop_reason=stop_reason,
                                     budget_summary=self.budget.totals(), limitations=self.limitations(chosen, final_meta))
        if stop_reason == "budget_exhausted":
            self.store.transition("budget_exhausted", "the shared cap refused the next request", expected="evaluating", stop_reason=stop_reason)
        elif manifest["asset"]:
            self.store.transition("delivered", "fallback compatible asset delivered", expected="evaluating", stop_reason=stop_reason)
        else:
            self.store.transition("unresolved", "no observed compatible revision to deliver", expected="evaluating", stop_reason=stop_reason)
        self.store.set_setting("pending_delivery", None)
        self.store.set_setting("fallback_intent", None)
        self.stamp_manifest(manifest)

    def stamp_manifest(self, manifest: dict) -> None:
        """The manifest is an export of the database: it carries the job's final state and stop reason, and the
        deliverable status stays a separate field (exit 0 with an unresolved visual status is a normal completion).
        report.md is rewritten from the stamped manifest: write_deliverable writes it in 'evaluating', before the final
        transition (test-pilot-002's report said 'State: **evaluating**' on a delivered job)."""
        job = self.store.job()
        manifest = dict(manifest, state=job["state"], stop_reason=job["stop_reason"], exit_semantics={"delivered": 0, "unresolved": 0, "budget_exhausted": 4, "cancelled": 130, "failed": 3, "needs_attention": 3,
                                                                                                       "awaiting_owner": 0})
        self.store.update_job(deliverable_json=manifest)
        atomic_write(self.store.job_dir / "deliverable" / "manifest.json", json.dumps(manifest, indent=1, default=str).encode("utf-8"))
        atomic_write(self.store.job_dir / "deliverable" / "report.md", report_markdown(manifest).encode("utf-8"))

    # ------------------------------------------------------------------ the owner review loop (review.py)
    def await_owner(self, rev: dict, delivery: dict | None, *, source: str, stop_reason: str | None) -> bool:
        """Present ``rev`` to the owner: the byte-bound candidate under review/round-NN/, the round row, state awaiting_owner.
        Idempotent across a crash (a round left open for the same revision is reused). False when there is nothing to review
        (the candidate's checks failed): the caller then finalizes as without the owner review."""
        open_round = self.store.open_owner_round()
        if open_round is not None and open_round["revision_id"] != rev["id"]:
            self.store.decide_owner_round(open_round["round"], "cancelled", {"reason": f"superseded by {rev['id']} before the owner decided"})
            open_round = None
        if open_round is None:
            open_round = self.store.insert_owner_round(revision_id=rev["id"], asset_sha256=rev.get("glb_sha256"), source=source,
                                                        candidate={"revision": rev["id"], "delivery": delivery, "stop_reason": stop_reason})
        n = open_round["round"]
        rdir = self.store.job_dir / "revisions" / rev["id"]
        product = (self.store.job().get("request") or {}).get("product_id") or self.store.job_dir.name
        related = R.related_measurements(rev, rdir)
        flags = R.runtime_limited_flags(R.observation_summary(rev))
        manifest = write_review_candidate(self.store, rev, revision_dir=rdir, round_no=n, source=source, delivery=delivery, stop_reason=stop_reason,
                                          related=related, runtime_limited=flags, budget_summary=self.budget.totals(),
                                          tryon=lambda asset: R.tryon_data(self.store.job_dir, rev, asset, round_no=n, product_id=product))
        if manifest["problems"]:
            self.store.decide_owner_round(n, "cancelled", {"reason": "nothing to review: " + "; ".join(manifest["problems"])})
            return False
        with self.store.tx():
            self.store.update_owner_round_candidate(n, manifest)
            if self.store.state() not in ("ready_for_final", "evaluating"):
                self.store.transition("ready_for_final", "owner review", expected=("ready", "needs_observation", "tool_pending", "tool_running", "inference_pending"))
            self.store.transition("awaiting_owner", f"round {n}: {rev['id']} waits for the owner's live review", stop_reason="awaiting_owner")
            self.store.update_job(selected_revision=rev["id"])
            self.store.set_setting("pending_delivery", None)
            self.store.set_setting("fallback_intent", None)
        link = (manifest.get("tryon") or {}).get("link")
        self.store.event("awaiting_owner", round=n, revision=rev["id"], asset_sha256=rev.get("glb_sha256"), source=source, stop_reason=stop_reason, link=link)
        self.log(f"[owner] round {n}: {rev['id']} awaits your review" + (f"; try it on: {link}" if link else " (synthetic: no GLB to try on)"))
        return True

    def _open_round_or_raise(self, *, allow_evaluating: bool = False) -> tuple[dict, dict]:
        state = self.store.state()
        r = self.store.open_owner_round()
        ok = state == "awaiting_owner" or (allow_evaluating and state == "evaluating" and self.store.setting("owner_accept_intent"))
        if r is None or not ok:
            raise RunnerError(f"nothing awaits the owner's review (state {state}, open round {r['round'] if r else None})")
        rev = self.store.revision(r["revision_id"])
        if rev is None:
            raise RunnerError(f"the review round {r['round']} names the missing revision {r['revision_id']}")
        return r, rev

    def _verify_candidate(self, r: dict, rev: dict) -> None:
        """The bytes the owner judged are the candidate's, and still the revision's."""
        asset = (r.get("candidate") or {}).get("asset")
        if rev["synthetic"]:
            return
        if not asset:
            raise RunnerError(f"round {r['round']} has no candidate file to accept")
        p = Path(asset["path"])
        actual = sha256(p.read_bytes()) if p.is_file() else None
        if actual is None or actual != r["asset_sha256"] or actual != rev.get("glb_sha256"):
            raise RunnerError(f"the candidate's bytes changed since round {r['round']} was opened ({str(actual)[:12]} vs {str(r['asset_sha256'])[:12]}); "
                              "refusing a decision against bytes the owner did not judge")

    def review_export(self, pending: dict | None = None) -> dict:
        """The owner review as the deliverable manifest carries it: every round with its decision (``pending`` stands for the
        decision being recorded now), the candidate kept and the lineage."""
        rounds = []
        for r in self.store.owner_rounds():
            rec = r.get("decision_record") or {}
            row = {"round": r["round"], "revision_id": r["revision_id"], "asset_sha256": r["asset_sha256"], "source": r["source"],
                   "opened_utc": r["opened_utc"], "decision": r["decision"], "decided_utc": r["decided_utc"], "text": rec.get("text"),
                   "candidate": ((r.get("candidate") or {}).get("asset") or {}).get("path")}
            if pending and pending.get("round") == r["round"] and r["decision"] is None:
                row.update(decision=pending.get("decision"), text=pending.get("text"), decided_utc=self.store.now())
            rounds.append(row)
        kept = next((x["candidate"] for x in reversed(rounds) if x.get("candidate")), None)
        return {"enabled": self.owner_review(), "rounds": rounds, "kept_candidate": kept, "lineage": self.store.setting("lineage")}

    def _decision_record(self, r: dict, rev: dict, *, decision: str, verdict: str, authorized_by: str, medium: str, note: str, text: str | None = None) -> dict:
        cand = r.get("candidate") or {}
        return {"decision": decision, "verdict": verdict, "round": r["round"], "revision": rev["id"], "asset_sha256": r["asset_sha256"],
                "candidate": (cand.get("asset") or {}).get("path"), "text": text, "note": note, "authorized_by": authorized_by, "medium": medium,
                "decided_utc": self.store.now(),
                "measurements": cand.get("related_measurements") if cand.get("related_measurements") is not None else R.related_measurements(rev, self.store.job_dir / "revisions" / rev["id"]),
                "runtime_limited": cand.get("runtime_limited") if cand.get("runtime_limited") is not None else R.runtime_limited_flags(R.observation_summary(rev)),
                "metrics": {k: v for k, v in R.observation_summary(rev).items() if isinstance(v, (int, float)) and not isinstance(v, bool)}}

    def queue_calibration(self, record: dict, rev: dict, calibration_file) -> None:
        """Inside the decision's transaction: the decision's calibration row is recorded as pending (setting
        'owner_calibration_pending'), so a crash between the decision and the file append never loses it; a synthetic
        candidate has nothing to calibrate. flush_owner_calibration writes it."""
        if rev["synthetic"] or not record.get("asset_sha256"):
            return
        pending = list(self.store.setting("owner_calibration_pending") or [])
        pending.append({"record": record, "revision": rev["id"], "calibration_file": str(calibration_file) if calibration_file else None})
        self.store.set_setting("owner_calibration_pending", pending)

    def flush_owner_calibration(self) -> list[dict]:
        """Write every pending calibration row (idempotently: a row already in the file is not appended again) and clear
        what was written; a row the file refused stays pending for the next decision or run."""
        pending = list(self.store.setting("owner_calibration_pending") or [])
        if not pending:
            return []
        written, left = [], []
        for p in pending:
            rev = self.store.revision(p["revision"])
            row = self.append_calibration(p["record"], rev, calibration_file=p.get("calibration_file")) if rev is not None else None
            (written if row is not None or rev is None else left).append(p)
        self.store.set_setting("owner_calibration_pending", left or None)
        return written

    def append_calibration(self, record: dict, rev: dict, *, calibration_file) -> dict | None:
        """The decision as a calibration row (modeler.owner_verdict.review_record), appended once: a row of this job, round,
        verdict and asset digest already in the file is returned instead of appended again (a retry after a crash). None
        when there is nothing to calibrate (synthetic) or the file could not be written (journaled; the row stays pending)."""
        if rev["synthetic"] or not record.get("asset_sha256"):
            return None
        from .. import owner_verdict as mov
        target = mov.CALIBRATION_FILE if calibration_file is None else Path(calibration_file)
        row = mov.review_record(verdict=record["verdict"], job_dir=self.store.job_dir, product_id=(self.store.job().get("request") or {}).get("product_id") or "",
                                revision=rev["id"], asset_path=record.get("candidate"), asset_sha256=record["asset_sha256"], round_no=record["round"],
                                medium=record.get("medium") or "", note=record.get("note") or "", text=record.get("text"), summary=R.observation_summary(rev),
                                related=record.get("measurements"), runtime_limited=record.get("runtime_limited"), authorized_by=record.get("authorized_by"),
                                when=record.get("decided_utc") or self.store.now())
        existing = calibration_row_present(target, row)
        if existing is not None:
            self.store.event("calibration_row_present", round=record["round"], verdict=record["verdict"], file=str(target))
            return existing
        try:
            mov.append_review_record(row, target)
        except OSError as e:
            self.store.event("calibration_append_failed", error=f"{type(e).__name__}: {e}", round=record["round"])
            return None
        self.store.event("calibration_row_appended", round=record["round"], verdict=record["verdict"], file=str(target))
        return row

    def check_allowance(self, allowance: dict, *, committed_micro: int, committed_operations: int) -> None:
        """The ceilings the CLI's owner_allowance applies, re-checked for every caller of the session API: a positive integer
        grant, at most OWNER_ROUND_MAX_USD / OWNER_ROUND_MAX_OPERATIONS for one round, and the job's round caps (what it has
        committed plus the grant) at most JOB_MAX_USD / JOB_MAX_OPERATIONS."""
        from .config import JOB_MAX_OPERATIONS, JOB_MAX_USD, OWNER_ROUND_MAX_OPERATIONS, OWNER_ROUND_MAX_USD
        from .pricing import usd_to_micro
        add_micro, add_ops = allowance.get("add_micro"), allowance.get("add_operations")
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (add_micro, add_ops)) or add_micro <= 0 or add_ops <= 0:
            raise RunnerError("a change round's allowance is a positive integer micro-USD amount and a positive operation count")
        if add_micro > usd_to_micro(OWNER_ROUND_MAX_USD) or add_ops > OWNER_ROUND_MAX_OPERATIONS:
            raise RunnerError(f"one change round is granted at most {OWNER_ROUND_MAX_USD} USD and {OWNER_ROUND_MAX_OPERATIONS} operations")
        if committed_micro + add_micro > usd_to_micro(JOB_MAX_USD) or committed_operations + add_ops > JOB_MAX_OPERATIONS:
            raise RunnerError(f"the job's caps would exceed {JOB_MAX_USD} USD / {JOB_MAX_OPERATIONS} operations with this allowance "
                              f"(committed {committed_micro} micro-USD and {committed_operations} operations)")

    def live_continuation(self, r: dict) -> dict | None:
        """The new job that round ``r``'s change request already made (the continuation intent names its folder, its
        previous_job is this job's round) while it can still run: any state but a terminal one or 'created' (a job that died
        inside initialize never runs). {"output", "state"}, or None."""
        intent = self.store.setting("owner_continuation_intent") or {}
        output = intent.get("output")
        if intent.get("round") != r["round"] or not output or not (Path(output) / "job.sqlite3").is_file():
            return None
        try:
            nstore = Store.open(Path(output), readonly=True)
        except StateError:
            return None
        try:
            job = nstore.job()
            prev = (job.get("policy") or {}).get("previous_job") or {}
            if prev.get("job") != str(self.store.job_dir) or prev.get("round") != r["round"]:
                return None
            if job["state"] in TERMINAL_STATES or job["state"] == "created":
                return None
            return {"output": str(output), "state": job["state"]}
        finally:
            nstore.close()

    def refuse_orphaned_continuation(self, r: dict, decision: str) -> None:
        """An accept or a stop of round ``r`` is refused while the continuation its change request made can still run: the
        decision would end this job while a linked job keeps its own allowance (the verifier's orphan: a stop exited 0 and
        `resume` of the new job then spent). The owner finishes the link or cancels the new job, then decides again."""
        live = self.live_continuation(r)
        if live is None:
            return
        out = live["output"]
        how = (f"finish the link (repeat the same --changes with --new-job-output {out}) or cancel it" if live["state"] == "ready" else "cancel it")
        raise RunnerError(f"round {r['round']}'s change request already made the continuation job {out} (state {live['state']}), which can still "
                          f"run and spend its own allowance; --{decision} would leave it orphaned. First {how} "
                          f'(python -m modeler.agentic cancel --job "{out}"), then decide again')

    def _clear_continuation_intent(self, r: dict, decision: str) -> None:
        """Inside a terminal decision's transaction: a continuation intent of this round names no job that can run (checked
        by refuse_orphaned_continuation), so it is cleared and journaled."""
        intent = self.store.setting("owner_continuation_intent")
        if intent and intent.get("round") == r["round"]:
            self.store.set_setting("owner_continuation_intent", None)
            self.store.event("continuation_abandoned", round=r["round"], decision=decision, intent=intent)

    def owner_accept(self, *, authorized_by: str, medium: str = "live AR mirror", note: str = "", calibration_file=None) -> dict:
        """The owner accepts the candidate: the job delivers exactly that revision (deliverable/, byte-bound), the verdict is
        appended (verdicts kind 'owner' and the calibration set). The final evaluator runs only with policy final_on_accept.
        Refused while this round's continuation job can still run (``refuse_orphaned_continuation``)."""
        self.require_fence()
        r, rev = self._open_round_or_raise(allow_evaluating=True)
        if self.store.state() == "awaiting_owner":
            self.refuse_orphaned_continuation(r, "accept")
        self._verify_candidate(r, rev)
        run_final = bool(self.policy.get("final_on_accept")) and self.policy.get("final_evaluator", "none") != "none"
        if run_final:
            self.refuse_while_unknown()
        if self.store.state() == "awaiting_owner":
            with self.store.tx():
                self.require_fence()
                self.store.set_setting("owner_accept_intent", {"round": r["round"], "authorized_by": authorized_by, "medium": medium, "note": note,
                                                               "calibration_file": str(calibration_file) if calibration_file else None})
                self.store.transition("evaluating", f"the owner accepted round {r['round']}", expected="awaiting_owner")
        if run_final:
            final, meta = self.run_final(rev)
        else:
            final, meta = None, {"error": "not run: the owner accepted this candidate in the live review (policy final_on_accept off)"}
        rdir = self.store.job_dir / "revisions" / rev["id"]
        axes = final_axes(rev, final, self.protocol(), evaluation_error=meta.get("error"), heldout=read_heldout(rdir), revision_dir=rdir,
                          evidence=self.evidence(), final_meta=meta if run_final else None)
        record = self._decision_record(r, rev, decision="accept", verdict="accept", authorized_by=authorized_by, medium=medium, note=note)
        limitations = self.limitations(rev, meta) + [f"the owner accepted revision {rev['id']} (round {r['round']}) in the {medium}"]
        manifest = write_deliverable(self.store, self.artifacts, rev, revision_dir=rdir, final={"evaluation": final, "meta": meta, "delivery": (r.get("candidate") or {}).get("delivery")},
                                     axes=axes, stop_reason="accepted_by_owner", budget_summary=self.budget.totals(), limitations=limitations,
                                     review=self.review_export({"round": r["round"], "decision": "accept"}))
        # ONE transaction: the final transition, the verdict row, the round decision, the cleared intent and the pending
        # calibration row. A failure anywhere inside rolls all of it back to 'evaluating' with the intent kept, and run()
        # completes the acceptance (until 2026-09-28 a crash after 'delivered' lost the verdict and the decision for good).
        # The manifest/report files written inside are exports: a rolled-back attempt's files are rewritten by the retry.
        with self.store.tx():
            self.require_fence()
            if manifest["asset"]:
                self.store.transition("delivered", "the owner accepted the candidate in the live review", expected="evaluating", stop_reason="accepted_by_owner")
                self.stamp_manifest(manifest)
                row = record_owner_verdict(self.store, verdict="accept", sha256=r["asset_sha256"], medium=medium, note=note, extra=record)
            else:
                self.store.transition("unresolved", "accepted, but there is no real asset to deliver" + (" (synthetic)" if rev["synthetic"] else ""),
                                      expected="evaluating", stop_reason="accepted_by_owner")
                self.stamp_manifest(manifest)
                row = self.store.append_verdict(kind="owner", revision_id=rev["id"], asset_sha256=r["asset_sha256"], verdict="accept",
                                                bindings={"round": r["round"], "asset_sha256": r["asset_sha256"], "synthetic": bool(rev["synthetic"])}, record=record)
            self.store.decide_owner_round(r["round"], "accept", dict(record, verdict_id=row["id"]))
            self.store.set_setting("owner_accept_intent", None)
            self._clear_continuation_intent(r, "accept")
            self.queue_calibration(record, rev, calibration_file)
            self.store.event("owner_accepted", round=r["round"], revision=rev["id"], asset_sha256=r["asset_sha256"], authorized_by=authorized_by)
        self.flush_owner_calibration()
        return {"decision": "accept", "round": r["round"], "revision": rev["id"], "state": self.store.state(), "asset": manifest["asset"]}

    def owner_stop(self, *, authorized_by: str, note: str = "", medium: str = "live AR mirror", calibration_file=None) -> dict:
        """The owner stops the job: unresolved, stop reason owner_stopped; the last candidate stays under review/round-NN/.
        Refused while this round's continuation job can still run (``refuse_orphaned_continuation``)."""
        self.require_fence()
        r, rev = self._open_round_or_raise()
        self.refuse_orphaned_continuation(r, "stop")
        record = self._decision_record(r, rev, decision="stop", verdict="stop", authorized_by=authorized_by, medium=medium, note=note)
        review = self.review_export({"round": r["round"], "decision": "stop", "text": note or None})
        manifest = write_deliverable(self.store, self.artifacts, None, revision_dir=None, final={"evaluation": None, "meta": {}, "delivery": None}, axes=None,
                                     stop_reason="owner_stopped", budget_summary=self.budget.totals(),
                                     limitations=[f"the owner stopped the job at review round {r['round']}; the candidate {rev['id']} is kept at "
                                                  f"{review['kept_candidate'] or review_round_dir(self.store.job_dir, r['round'])} and not delivered"], review=review)
        with self.store.tx():
            self.require_fence()
            row = self.store.append_verdict(kind="owner", revision_id=rev["id"], asset_sha256=r["asset_sha256"], verdict="stop",
                                            bindings={"round": r["round"], "asset_sha256": r["asset_sha256"]}, record=record)
            self.store.decide_owner_round(r["round"], "stop", dict(record, verdict_id=row["id"]))
            self.store.transition("unresolved", f"the owner stopped the job at round {r['round']}", expected="awaiting_owner", stop_reason="owner_stopped")
            self._clear_continuation_intent(r, "stop")
            self.queue_calibration(record, rev, calibration_file)
        self.stamp_manifest(manifest)
        self.flush_owner_calibration()
        return {"decision": "stop", "round": r["round"], "revision": rev["id"], "state": self.store.state(), "kept_candidate": review["kept_candidate"]}

    def last_author_input_tokens(self) -> int | None:
        last = [q for q in self.store.requests(role=ROLE_AUTHOR) if q["purpose"] == "author" and q["input_token_count"] is not None]
        return int(last[-1]["input_token_count"]) if last else None

    def conversation_too_large(self) -> tuple[bool, int | None, int]:
        """(a change request must start a new job, the last author request's input tokens, the policy's limit)."""
        limit = int(self.policy.get("review_resume_token_limit") or REVIEW_RESUME_TOKEN_LIMIT)
        size = self.last_author_input_tokens()
        return (size is not None and size >= limit), size, limit

    def owner_request_changes(self, *, text: str, allowance: dict, authorized_by: str, editable: list[str] | None = None, add_revisions: int | None = None,
                              wall_minutes: int | None = None, note: str = "", medium: str = "live AR mirror", calibration_file=None) -> dict:
        """The owner asks for changes: their words VERBATIM as a new user message in the same author conversation, with the
        related measurements, the runtime-limited flags and the new allowance; the round's caps become the committed amounts
        plus exactly the allowance (journaled with who authorized it and any dropped leftover), so the round spends its grant
        and nothing more; the round's module locks (``editable``: None = every module, bound to the reviewed revision's
        bytes) and revision cap (revisions used + ``add_revisions``) are set; the job returns to ready. Returns {"path": "new_job", ...} WITHOUT recording anything when the
        conversation is too large (``conversation_too_large``): the caller continues in a new seeded job instead."""
        self.require_fence()
        r, rev = self._open_round_or_raise()
        too_large, size, limit = self.conversation_too_large()
        if too_large:
            return {"path": "new_job", "input_tokens": size, "limit": limit, "round": r["round"]}
        n = r["round"]
        from ..candidates import MODULE_ORDER
        revisions = self.store.revisions()
        revisions_used = len(revisions)
        add_rev = int(allowance["add_operations"] if add_revisions is None else add_revisions)
        if add_rev < 1:
            raise RunnerError("a change round allows at least one new revision")
        # the round's own revisions: exactly add_rev new ones (not the leftover of the job's max_revisions as well)
        max_revisions = revisions_used + add_rev
        wall = int(wall_minutes or self.policy["wall_minutes"])
        locked = [] if editable is None else [m for m in MODULE_ORDER if m not in set(editable)]
        record = self._decision_record(r, rev, decision="changes", verdict="changes_requested", authorized_by=authorized_by, medium=medium, note=note, text=text)
        # the locks bind to the REVIEWED revision's bytes (tools.owner_lock_refusal): edits, deliveries and fallbacks of this round
        reviewed_shas = T.module_shas(rev)
        lock = {"round": n, "reviewed_revision": rev["id"], "locked_modules": locked, "locked_sha256": {m: reviewed_shas.get(m) for m in locked},
                "revisions_before": [x["id"] for x in revisions]}
        with self.store.tx():
            self.require_fence()
            committed = self.budget.totals()
            self.check_allowance(allowance, committed_micro=committed["upper_bound_micro"], committed_operations=committed["operations_used"])
            grant = self.budget.grant_allowance(add_micro=int(allowance["add_micro"]), add_operations=int(allowance["add_operations"]), authorized_by=authorized_by,
                                                reason=f"owner change round {n}", round=n)
            self.store.update_policy({"editable_modules": editable, "max_revisions": max_revisions, "wall_minutes": wall}, reason=f"owner change round {n}")
            self.store.set_setting("owner_change_round", lock)
            totals = self.budget.totals()
            blocks = R.owner_message_blocks(round_no=n, revision_id=rev["id"], text=text, related=record["measurements"], runtime_limited=record["runtime_limited"],
                                            allowance=allowance, totals_after=totals, editable=editable, locked=locked, max_revisions=max_revisions,
                                            revisions_used=revisions_used, wall_minutes=wall)
            assert_no_sealed_pixels(self.store, blocks)
            self.store.append_items(ROLE_AUTHOR, [user_message(blocks)])
            record.update(allowance={k: allowance[k] for k in ("add_micro", "add_operations", "budget_usd")}, grant=grant, editable_modules=editable,
                          locked_modules=locked, max_revisions=max_revisions, wall_minutes=wall, conversation_input_tokens=size, resume_token_limit=limit)
            row = self.store.append_verdict(kind="owner", revision_id=rev["id"], asset_sha256=r["asset_sha256"], verdict="changes_requested",
                                            bindings={"round": n, "asset_sha256": r["asset_sha256"]}, record=record)
            self.store.decide_owner_round(n, "changes", dict(record, verdict_id=row["id"]))
            self.store.set_setting("round_started_utc", self.store.now())
            self.store.set_setting("consecutive_text_only", 0)
            self.store.set_setting("consecutive_incomplete", 0)
            self.store.update_job(stop_reason=None)
            self.store.transition("ready", f"owner change round {n}: the owner's words are in the conversation", expected="awaiting_owner")
            self.queue_calibration(record, rev, calibration_file)
        self.policy = self.store.job()["policy"]
        self.store.event("owner_changes_requested", round=n, revision=rev["id"], authorized_by=authorized_by, add_micro=allowance["add_micro"],
                         add_operations=allowance["add_operations"], editable_modules=editable)
        self.flush_owner_calibration()
        return {"path": "same_conversation", "round": n, "revision": rev["id"], "state": self.store.state(), "caps": grant["after"], "input_tokens": size, "limit": limit}

    def owner_continue_in_new_job(self, output: Path, *, text: str, allowance: dict, authorized_by: str, editable: list[str] | None, worker, transport,
                                  worker_config: dict | None, fingerprints: dict, add_revisions: int | None = None, note: str = "", medium: str = "live AR mirror",
                                  calibration_file=None, log=None, intake_drivers=None) -> "Session":
        """The change request when the conversation is too large to continue cheaply: a NEW job seeded from the candidate
        revision (its sealed program, verified against its build bundle), the owner's words as its owner_instruction, the
        round's locks as its editable_modules, a short summary of this job in its first message, and exactly the allowance
        as its caps. Both jobs record the link (setting 'lineage'); this job ends unresolved (continued_in_new_job). The new
        job is created (intake, the seed built) but not run; the caller runs it."""
        self.require_fence()
        r, rev = self._open_round_or_raise()
        _too_large, size, limit = self.conversation_too_large()
        self.check_allowance(allowance, committed_micro=0, committed_operations=0)      # the new job's caps are exactly the allowance
        output = Path(output)
        add_rev = int(allowance["add_operations"] if add_revisions is None else add_revisions)
        # what the new job gets, recorded in the intent: a retry that reuses the folder must repeat it exactly (the verifier's
        # retry with other words recorded words and an allowance on this job that the reused new job never received)
        wanted = {"text": text, "allowance": {k: allowance[k] for k in ("add_micro", "add_operations", "budget_usd")},
                  "editable_modules": None if editable is None else list(editable), "add_revisions": add_rev}
        # the continuation's intent is recorded BEFORE the new job exists: a crash before this job ends leaves it waiting with
        # the intent, and a retry finishes the link with the same folder instead of making a second continuation. A new job
        # that failed to initialize (a seed build error, a crash inside initialize) never ran: the attempt is recorded
        # (intent failed_attempts, event continuation_failed) and a retry into a new folder is allowed
        intent = self.store.setting("owner_continuation_intent")
        if intent and intent.get("round") == r["round"]:
            if intent.get("output") is not None:
                if not (Path(intent["output"]) / "job.sqlite3").is_file():
                    intent = dict(intent, output=None)      # nothing was made there (a stop before Store.create): the intent is rewritten below
                else:
                    failed = self.failed_continuation(Path(intent["output"]), r)
                    if failed is not None:
                        intent = self.record_failed_continuation(intent, failed)
            if intent.get("output") is not None and not same_path(intent["output"], output):
                raise RunnerError(f"round {r['round']}'s change request already started continuing in {intent['output']}; retry with "
                                  f"--new-job-output {intent['output']} (another folder would make a second continuation)")
            if (output / "job.sqlite3").is_file() and any(same_path(a["output"], output) for a in intent.get("failed_attempts") or []):
                att = [a for a in intent["failed_attempts"] if same_path(a["output"], output)][-1]
                raise RunnerError(f"the continuation in {output} failed to initialize ({att['error']}); the attempt is recorded and the folder "
                                  f"is kept for inspection. Retry with --new-job-output <a new empty folder>")
            if intent.get("output") is not None:
                self.check_continuation_values(intent, wanted, r)
            else:
                with self.store.tx():
                    self.require_fence()
                    self.store.set_setting("owner_continuation_intent", dict(intent, output=str(output), utc=self.store.now(), authorized_by=authorized_by, **wanted))
        else:
            with self.store.tx():
                self.require_fence()
                self.store.set_setting("owner_continuation_intent", {"round": r["round"], "output": str(output), "authorized_by": authorized_by,
                                                                     "utc": self.store.now(), **wanted})
        if (output / "job.sqlite3").is_file():
            new = self.reopen_continuation(output, r, worker=worker, transport=transport, log=log, intake_drivers=intake_drivers, wanted=wanted)
        else:
            try:
                new = self.create_continuation(output, r, rev, text=text, allowance=allowance, editable=editable, worker=worker, transport=transport,
                                               worker_config=worker_config, fingerprints=fingerprints, add_revisions=add_revisions, log=log,
                                               intake_drivers=intake_drivers)
            except Exception as e:  # noqa: BLE001
                if not (output / "job.sqlite3").is_file():
                    raise           # nothing was made (the policy or the folder was refused): the intent's folder holds no job, a retry may use any folder
                # Session.create marked the new job failed (initialize_failed) and released it: record the attempt so a retry
                # into a new folder is allowed, and say so. A crash that skips this handler is found by failed_continuation
                self.record_failed_continuation(self.store.setting("owner_continuation_intent") or {"round": r["round"]},
                                                {"output": str(output), "error": f"{type(e).__name__}: {e}", "state": "failed", "utc": self.store.now()})
                raise RunnerError(f"the new job in {output} failed to initialize ({type(e).__name__}: {e}); nothing was decided on this job, "
                                  f"the attempt is recorded and the folder is kept for inspection. Fix the cause, then retry the same "
                                  f"--changes with --new-job-output <a new empty folder>") from e
        try:
            link = {"job": str(self.store.job_dir), "round": r["round"], "revision": rev["id"], "asset_sha256": r["asset_sha256"], "owner_text": text,
                    "authorized_by": authorized_by, "utc": self.store.now(), "conversation_input_tokens": size, "resume_token_limit": limit}
            if not ((new.store.setting("lineage") or {}).get("continued_from") or {}).get("job") == str(self.store.job_dir):
                new.store.set_setting("lineage", {"continued_from": link})
                new.store.event("lineage", continued_from=link)
            record = self._decision_record(r, rev, decision="continued_in_new_job", verdict="changes_requested", authorized_by=authorized_by, medium=medium,
                                           note=note, text=text)
            record.update(continued_in=str(new.store.job_dir), allowance={k: allowance[k] for k in ("add_micro", "add_operations", "budget_usd")},
                          editable_modules=editable, conversation_input_tokens=size, resume_token_limit=limit)
            lineage = dict(self.store.setting("lineage") or {})
            lineage["continued_in"] = list(lineage.get("continued_in") or []) + [{"job": str(new.store.job_dir), "round": r["round"], "revision": rev["id"],
                                                                                  "asset_sha256": r["asset_sha256"], "utc": self.store.now()}]
            review = self.review_export({"round": r["round"], "decision": "continued_in_new_job", "text": text})
            manifest = write_deliverable(self.store, self.artifacts, None, revision_dir=None, final={"evaluation": None, "meta": {}, "delivery": None}, axes=None,
                                         stop_reason="continued_in_new_job", budget_summary=self.budget.totals(),
                                         limitations=[f"the owner's change request of round {r['round']} continues in {new.store.job_dir} (this conversation had "
                                                      f"{size} input tokens, the limit is {limit}); the candidate {rev['id']} is kept at {review['kept_candidate']}"],
                                         review=review)
            # the forward link, the verdict, the decision and the end of this job are one transaction (until 2026-09-28 the link
            # was written before it, so a crash in between left a waiting job linked to a continuation)
            with self.store.tx():
                self.require_fence()
                self.store.set_setting("lineage", lineage)
                row = self.store.append_verdict(kind="owner", revision_id=rev["id"], asset_sha256=r["asset_sha256"], verdict="changes_requested",
                                                bindings={"round": r["round"], "asset_sha256": r["asset_sha256"], "continued_in": str(new.store.job_dir)}, record=record)
                self.store.decide_owner_round(r["round"], "continued_in_new_job", dict(record, verdict_id=row["id"]))
                self.store.transition("unresolved", f"the owner's change request continues in {new.store.job_dir.name}", expected="awaiting_owner",
                                      stop_reason="continued_in_new_job")
                self.store.set_setting("owner_continuation_intent", None)
                self.queue_calibration(record, rev, calibration_file)
        except BaseException:
            # the new job stays on disk (the intent names it) for the retry; its lease and connection are given back
            with contextlib.suppress(Exception):
                new.release()
            with contextlib.suppress(Exception):
                new.store.close()
            raise
        self.stamp_manifest(manifest)
        self.store.event("continued_in_new_job", job=str(new.store.job_dir), round=r["round"], revision=rev["id"])
        self.flush_owner_calibration()
        return new

    def create_continuation(self, output: Path, r: dict, rev: dict, *, text: str, allowance: dict, editable: list[str] | None, worker, transport,
                            worker_config: dict | None, fingerprints: dict, add_revisions: int | None, log, intake_drivers) -> "Session":
        """The new job of a continuation: seeded from the reviewed revision, the owner's words as its owner_instruction, the
        round's locks as its editable_modules, a summary of this job in its first message, exactly the allowance as its caps."""
        from .cli import seed_from_revision
        from .config import build_policy, translate_request
        old = self.policy
        add_rev = int(allowance["add_operations"] if add_revisions is None else add_revisions)
        rrl = old.get("review_resume_token_limit")
        if not isinstance(rrl, int) or isinstance(rrl, bool) or not 20_000 <= rrl <= int(old["compact_threshold_tokens"]):
            rrl = None          # a value build_policy would refuse (set past it) falls back to the default
        policy = build_policy(driver=old["driver"], worker=old["worker"], allow_paid=bool(old.get("allow_paid")), budget_usd=allowance["budget_usd"],
                              max_inference_requests=int(allowance["add_operations"]), max_output_tokens=old["max_output_tokens"],
                              max_revisions=min(40, 1 + add_rev), max_worker_seconds=old["max_worker_seconds"], wall_minutes=old["wall_minutes"],
                              images_per_request=old["images_per_request"], reasoning_effort=old["reasoning_effort"],
                              cache_mode=old.get("cache_mode") or "explicit_rolling", compact_threshold_tokens=old["compact_threshold_tokens"],
                              compact_output_bound_tokens=old.get("compact_output_bound_tokens"), ar=bool(old.get("ar", True)),
                              intake=old["intake"] if old.get("intake") in ("none", "code", "synthetic") else "code", critic=old.get("critic", "none"),
                              final_evaluator=old.get("final_evaluator", "none"), service_tier=old.get("service_tier", "default"), region=old.get("region", "global"),
                              image_prune_tokens=old.get("image_prune_tokens"), owner_instruction=text, editable_modules=editable, owner_review=True,
                              review_resume_token_limit=rrl, final_on_accept=bool(old.get("final_on_accept")))
        policy["previous_job"] = R.previous_job_summary(self.store, r, rev, text)
        request = self.store.job()["request"]
        raw = {"product_id": request["product_id"], "dimensions": request.get("dimensions") or {}, "notes": request.get("notes", ""),
               "held_out_views": request.get("held_out_views") or [],
               "photos": [{k: ph[k] for k in ("path", "view", "held_out", "id", "sha256", "source_photo_id", "crop_xyxy") if ph.get(k) is not None}
                          for ph in request["photos"]]}
        translated = translate_request(raw, self.store.job_dir)
        seed = seed_from_revision(f"{self.store.job_dir}:{rev['id']}")
        translated = dict(translated, notes=list(translated["notes"]) + [f"continues {self.store.job_dir.name} round {r['round']} from {rev['id']} (owner review)"])
        return Session.create(Path(output), translated=translated, policy=policy, fingerprints=fingerprints, worker=worker, transport=transport,
                              worker_config=worker_config, log=log or self.log, seed=seed, intake_drivers=intake_drivers)

    def failed_continuation(self, output: Path, r: dict) -> dict | None:
        """The continuation of round ``r`` in ``output`` when it failed to initialize and never ran (state failed with stop
        reason initialize_failed, or still 'created': the process died inside initialize), as a failed-attempt record; None
        otherwise (no such job, another job's continuation, or one that initialized)."""
        if not (Path(output) / "job.sqlite3").is_file():
            return None
        try:
            nstore = Store.open(Path(output), readonly=True)
        except StateError:
            return None
        try:
            job = nstore.job()
            prev = (job.get("policy") or {}).get("previous_job") or {}
            if prev.get("job") != str(self.store.job_dir) or prev.get("round") != r["round"]:
                return None
            if job["state"] == "failed" and job["stop_reason"] == "initialize_failed":
                ev = nstore.events("initialize_failed")
                error = ev[-1]["data"].get("error") if ev else "initialize failed"
            elif job["state"] == "created":
                error = "initialization never finished (the process stopped inside it)"
            else:
                return None
            return {"output": str(output), "error": error, "state": job["state"], "utc": self.store.now()}
        finally:
            nstore.close()

    def record_failed_continuation(self, intent: dict, failed: dict) -> dict:
        """Journal a continuation attempt that failed to initialize: the intent keeps it in failed_attempts and names no
        folder any more, so the retry may use a new one (the failed folder is kept for inspection and never reused)."""
        with self.store.tx():
            self.require_fence()
            new = dict(intent, output=None, failed_attempts=list(intent.get("failed_attempts") or []) + [failed])
            self.store.set_setting("owner_continuation_intent", new)
            self.store.event("continuation_failed", output=failed["output"], error=failed["error"], failed_attempts=new["failed_attempts"])
        self.log(f"[owner] the continuation in {failed['output']} failed to initialize ({failed['error']}); retry with a new --new-job-output folder")
        return new

    @staticmethod
    def _values_text(v: dict) -> str:
        a = v.get("allowance") or {}
        return (f"words {v.get('text')!r}, allowance {a.get('add_micro')} micro-USD / {a.get('add_operations')} operations, "
                f"editable modules {v.get('editable_modules') if v.get('editable_modules') is not None else 'all'}, {v.get('add_revisions')} new revisions")

    def check_continuation_values(self, intent: dict, wanted: dict, r: dict) -> None:
        """A retry that finishes a continuation already started must give it exactly what the first attempt recorded."""
        differ = []
        if "text" in intent and intent["text"] != wanted["text"]:
            differ.append("words")
        if "allowance" in intent and any(int(intent["allowance"].get(k) or 0) != int(wanted["allowance"][k]) for k in ("add_micro", "add_operations")):
            differ.append("allowance")
        if "editable_modules" in intent and _module_set(intent["editable_modules"]) != _module_set(wanted["editable_modules"]):
            differ.append("editable modules")
        if "add_revisions" in intent and intent["add_revisions"] is not None and int(intent["add_revisions"]) != int(wanted["add_revisions"]):
            differ.append("revisions")
        if differ:
            raise RunnerError(f"round {r['round']}'s change request already started continuing in {intent['output']} with "
                              f"{self._values_text(intent)}; this retry gives other {', '.join(differ)} ({self._values_text(wanted)}). Repeat the "
                              f"recorded values to finish it (the new job already holds them); a different change can be asked in the new job's review")

    def reopen_continuation(self, output: Path, r: dict, *, worker, transport, log, intake_drivers, wanted: dict | None = None) -> "Session":
        """The continuation a crash left made but unlinked: reused when it is this round's continuation, still in its initial
        'ready' state (never run) and made with exactly ``wanted`` (the words, the allowance as its caps, the editable modules,
        the revisions); anything else is refused for the owner to inspect."""
        nstore = Store.open(Path(output))
        try:
            job = nstore.job()
            policy = job.get("policy") or {}
            prev = policy.get("previous_job") or {}
            state = nstore.state()
            if prev.get("job") != str(self.store.job_dir) or prev.get("round") != r["round"] or state != "ready":
                raise RunnerError(f"{output} holds a job in state {state} that continues {prev.get('job')} round {prev.get('round')}, not this job's round "
                                  f"{r['round']} in its initial state; inspect it before deciding again")
            if wanted is not None:
                got = {"text": policy.get("owner_instruction"), "allowance": {"add_micro": int(job["cap_micro"]), "add_operations": int(job["inference_operation_cap"])},
                       "editable_modules": policy.get("editable_modules"), "add_revisions": int(policy.get("max_revisions") or 1) - 1}
                if (got["text"] != wanted["text"] or got["allowance"]["add_micro"] != int(wanted["allowance"]["add_micro"])
                        or got["allowance"]["add_operations"] != int(wanted["allowance"]["add_operations"])
                        or _module_set(got["editable_modules"]) != _module_set(wanted["editable_modules"])
                        or int(policy.get("max_revisions") or 0) != min(40, 1 + int(wanted["add_revisions"]))):
                    raise RunnerError(f"the continuation in {output} was made with {self._values_text(got)}; this retry gives "
                                      f"{self._values_text(wanted)}. Repeat the new job's values to finish the link (a different change can be "
                                      f"asked in the new job's review)")
            new = Session(nstore, transport=transport, worker=worker, log=log or self.log, intake_drivers=intake_drivers)
            new.acquire()
        except BaseException:
            nstore.close()
            raise
        new.store.event("continuation_reused", continues=str(self.store.job_dir), round=r["round"])
        return new

    def cancel_now(self) -> None:
        for op in self.store.operations(state="running"):
            try:
                self.worker.stop(op.get("worker") or {})
            except Exception:  # noqa: BLE001
                pass
            self.store.update_operation(op["id"], state="cancelled", result_json={"error": "cancelled by the owner", "category": "cancelled"}, completed_utc=self.store.now())
        state = self.store.state()
        if state not in TERMINAL_STATES:
            try:
                self.store.transition("cancelled", "owner cancellation", stop_reason="cancelled")
            except TransitionError:
                self.store.transition("ready", "cancel path")
                self.store.transition("cancelled", "owner cancellation", stop_reason="cancelled")
        self.store.event("cancelled")


def status_report(store: Store) -> dict:
    job = store.job()
    budget = Budget(store)
    revs = store.revisions()
    ops = store.operations()
    reqs = store.requests()
    d = job.get("deliverable") or {}
    return {"job": store.job_dir.name, "state": job["state"], "stop_reason": job["stop_reason"], "cancel_requested": bool(job["cancel_requested"]),
            "lease": {"holder": job["lease_holder"], "fence": job["lease_fence"], "expires_utc": job["lease_expires_utc"]},
            "driver": job["driver"], "worker": job["worker"], "model": job["model"], "current_revision": job["current_revision"], "selected_revision": job["selected_revision"],
            "revisions": [{k: r.get(k) for k in ("id", "parent_id", "state", "glb_sha256", "synthetic")} | {"compatible": bool((r.get("compatibility") or {}).get("compatible"))} for r in revs],
            "operations": [{k: o.get(k) for k in ("id", "call_id", "tool_name", "state", "attempt")} for o in ops],
            "requests": [{k: r.get(k) for k in ("id", "role", "purpose", "state", "http_status", "input_token_count", "provider_response_id")} for r in reqs],
            "observations": {s: len(store.observations(state=s)) for s in ("pending", "included", "acknowledged", "failed", "deferred")},
            "budget": budget.totals(), "deliverable_status": job.get("deliverable_status"), "axes": d.get("axes"),
            "unknown_requests": unknown_liability_requests(store), "epochs": {"author": store.epoch(ROLE_AUTHOR)},
            "settings": {"pending_delivery": store.setting("pending_delivery"), "fallback_intent": store.setting("fallback_intent"), "seed": store.setting("seed"),
                         "worker_config_history": store.setting("worker_config_history"), "owner_accept_intent": store.setting("owner_accept_intent"),
                         "owner_continuation_intent": store.setting("owner_continuation_intent")},
            # calibration rows of the owner's decisions the file refused: `resume` writes them (also on a terminal job)
            "owner_calibration_pending": len(store.setting("owner_calibration_pending") or []),
            "owner_review": owner_review_status(store)}


def owner_review_status(store: Store) -> dict:
    """The owner review as status shows it: whether the job waits, the candidate with its try-on data, every round, the
    lineage. A job recorded before the owner review (no owner_rounds table, no policy key) reads as disabled with no rounds."""
    job = store.job()
    rounds = store.owner_rounds()
    open_round = next((r for r in reversed(rounds) if r["decision"] is None), None)
    candidate = None
    if open_round is not None and job["state"] == "awaiting_owner":
        c = open_round["candidate"] or {}
        candidate = {"round": open_round["round"], "revision": open_round["revision_id"], "asset": c.get("asset"), "synthetic": c.get("synthetic"),
                     "source": open_round["source"], "stop_reason": c.get("stop_reason"), "delivery": c.get("delivery"), "tryon": c.get("tryon"),
                     "runtime_limited": c.get("runtime_limited"), "label": "awaiting your review",
                     "manifest": str(store.job_dir / "review" / f"round-{open_round['round']:02d}" / "manifest.json")}
    return {"enabled": bool((job.get("policy") or {}).get("owner_review")), "awaiting": job["state"] == "awaiting_owner", "candidate": candidate,
            "rounds": [{"round": r["round"], "revision": r["revision_id"], "asset_sha256": r["asset_sha256"], "source": r["source"], "opened_utc": r["opened_utc"],
                        "decision": r["decision"], "decided_utc": r["decided_utc"], "text": (r.get("decision_record") or {}).get("text"),
                        "authorized_by": (r.get("decision_record") or {}).get("authorized_by")} for r in rounds],
            "lineage": store.setting("lineage")}


def _module_set(value) -> frozenset | None:
    """Editable modules compared as a set (None: every module is editable)."""
    return None if value is None else frozenset(value)


def same_path(a, b) -> bool:
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def calibration_row_present(calibration_file: Path, row: dict) -> dict | None:
    """The row of the calibration file that records the same owner decision as ``row`` (the same kind, job folder, round,
    verdict and asset digest), or None; unreadable lines (a torn last write) are skipped."""
    try:
        text = Path(calibration_file).read_text(encoding="utf-8")
    except OSError:
        return None
    key = tuple(row.get(k) for k in ("kind", "job_dir", "round", "verdict", "asset_sha256"))
    for line in text.splitlines():
        try:
            other = json.loads(line)
        except ValueError:
            continue
        if isinstance(other, dict) and tuple(other.get(k) for k in ("kind", "job_dir", "round", "verdict", "asset_sha256")) == key:
            return other
    return None


def read_program_folder(seed_dir: Path) -> dict[str, str]:
    """The program modules (candidates.MODULE_ORDER) of a folder; RunnerError when it holds none."""
    from ..candidates import MODULE_ORDER
    modules = {}
    for name in MODULE_ORDER:
        p = Path(seed_dir) / f"{name}.py"
        if p.is_file():
            modules[name] = p.read_text(encoding="utf-8")
    if not modules:
        raise RunnerError(f"no program modules under {seed_dir}")
    return modules
