"""Append-only conversation renewal at request pressure; money remains in BudgetGuard."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import mimetypes
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from . import budget
from .receipts import write_json
from .money import money_facts
from .session_inputs import plan_session_input
from .instruction_profiles import LEGACY_PROFILE, resolve_profile, legacy_text

SUMMARY_REQUEST = """The host is renewing this conversation to keep request context bounded.
Write a state summary only; do not call tools. Use these headings, in this order:
1. Current head and best checkpoint: distinguish the host-recorded formal best from the current head and any unverified candidate; explain your kept choice. Prose does not change the formal best.
2. Remaining differences: preserve each item's evidence reference, last correction, outcome and next action. The host carries its recorded list automatically.
3. Decisions and reasons: preserve choices made, rejected approaches and the evidence behind them.
4. Outstanding owner requests: quote outstanding edit requests and what remains to do; say none if there are none.
5. Next action: state exactly what you were about to do. Preserve any pending finish review's state and follow the host notice about whether its ID remains usable.
Do not claim an unfinished check passed. The host carries every owner photo, current evidence and factual state into the fresh conversation."""
ASTRA_SUMMARY_PREFIX = "Astra's own state summary from the previous conversation:\n"
HOST_STATE_PREFIX = "Host factual state at conversation renewal (tool records, not a likeness judgement):\n"
RENEWAL_NOTICE = ("Continue the same run in this fresh conversation with the same instructions, tools and brief. "
                  "Earlier conversations remain unchanged in the run record. Money and owner approval remain the "
                  "same run's. Every attached image keeps its original pixels.")
FINISH_RESTART_NOTICE = ("The host restarted the pending two-call finish review. Use this conversation's finish "
                         "tool again to obtain its evidence before submitting the per-photo review.")
NO_SUMMARY = "No Astra summary was obtained; continue from the host's factual state and the attached evidence."
NEWEST_IMAGES_NOTICE = ("The newest tool-result images are omitted from this summary request only. "
                        "Their labels remain below; do not claim to have inspected them. "
                        "The fresh conversation receives the complete result, including these images.")


def input_digest(items):
    return hashlib.sha256(json.dumps(items, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def summary_input(items, previous_input):
    """Make a summary-only copy, preserving the previously requested prefix exactly.

    Only newly appended tool outputs are eligible. Unknown prefixes (including a
    resumed conversation before its first request) retain every image. Stored
    conversation items are never rewritten.
    """
    result = copy.deepcopy(items)
    if previous_input is None:
        return result, [], []
    count, digest = previous_input
    if len(items) < count or input_digest(items[:count]) != digest:
        return result, [], []
    latest, omitted = [], []
    for index in range(count, len(items)):
        item = items[index]
        if item.get("type") != "function_call_output" or not isinstance(item.get("output"), list):
            continue
        if not any(part.get("type") == "input_image" for part in item["output"]):
            continue
        latest.append(copy.deepcopy(item))
        parts, labels = [], []
        label = None
        for position, part in enumerate(item["output"]):
            if part.get("type") == "input_image":
                description = label or f"Image at output position {position}; call {item['call_id']}"
                labels.append(description)
                omitted.append({"call_id": item["call_id"], "output_position": position, "label": description})
            else:
                parts.append(copy.deepcopy(part))
            label = part.get("text") if part.get("type") == "input_text" else None
        # Original result text, including labels, is byte-for-byte unchanged.
        # The explicit list also covers outputs without individual labels.
        parts.append({"type": "input_text", "text": NEWEST_IMAGES_NOTICE + "\n" + "\n".join(labels)})
        result[index]["output"] = parts
    return result, latest, omitted


def summary_request(profile=None):
    """The selected profile's exact request; legacy conversation guidance is frozen."""
    return (legacy_text("summary_request") if resolve_profile(profile) == "legacy-20261021"
            else SUMMARY_REQUEST)


def durable_receipts(path):
    """Read the receipts committed atomically with conversation switches, including a crash before JSON export."""
    path = Path(path)
    if not path.is_file():
        return []
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        if not conn.execute("SELECT name FROM sqlite_master WHERE name='g3d_conversation_renewals'").fetchone():
            return []
        return [json.loads(row[0]) for row in conn.execute("SELECT receipt FROM g3d_conversation_renewals ORDER BY rowid")]


def pressure(body_bytes, images, input_tokens, tariff):
    """Strictly past 80%, using the guard/provider and frozen tariff's limits."""
    values = (("images", images, budget.PAYLOAD_LIMIT_IMAGES),
              ("payload_bytes", body_bytes, budget.PAYLOAD_LIMIT_BYTES),
              ("input_tokens", input_tokens, tariff.long_context_input_tokens))
    return [{"reason": name, "measured": value, "limit": limit, "threshold": limit * 4 // 5}
            for name, value, limit in values if value is not None and value * 5 > limit * 4]


class RenewalNeeded(Exception):
    def __init__(self, payload, measurements, reasons):
        super().__init__("the next request needs conversation renewal")
        self.payload, self.measurements, self.reasons = payload, measurements, reasons


def find_error(error, kinds):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, kinds):
            return error
        error = error.__cause__ or error.__context__
    return None


def evidence_content(records, payload, *, already_carried=()):
    """Prefer the already-sent bytes; otherwise verify the scene's original image file."""
    urls = {}
    def visit(value):
        if isinstance(value, dict):
            url = value.get("image_url")
            if value.get("type") == "input_image" and isinstance(url, str) and url.startswith("data:image/"):
                raw = base64.b64decode(url.split(",", 1)[1], validate=True)
                urls[hashlib.sha256(raw).hexdigest()] = (url, value.get("detail", "high"))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(payload.get("input", []))
    content, receipt = [], []
    for record in records:
        row = dict(record)
        digest = row["sha256"]
        if digest in urls:
            url, detail = urls[digest]
        else:
            path = Path(row["path"])
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("renewal evidence bytes differ from their recorded hash")
            mime = mimetypes.guess_type(path.name)[0]
            if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                raise ValueError("renewal evidence must have a supported image type")
            url, detail = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}", "high"
        label = (f"Renewal evidence: call {row['call_id']}; checkpoint {row.get('checkpoint')}; "
                 f"view {row.get('view')}; kind {row.get('kind')}; sha256 {digest}.")
        if digest not in already_carried:
            content.extend([{"type": "input_text", "text": label},
                            {"type": "input_image", "image_url": url, "detail": detail}])
        receipt.append({**row, "detail": detail})
    return content, receipt


class ConversationRenewal:
    def __init__(self, *, spec, scene, session, log, guard, client, directory, owner_notes=None):
        self.spec, self.scene, self.session = spec, scene, session
        self.log, self.guard, self.client = log, guard, client
        self.directory = Path(directory)
        self.owner_notes = spec.owner_notes if owner_notes is None else owner_notes
        self.records = []
        self.finish_review_deferrals = []
        self._finish_review_requests = set()
        self._after_finish_review_deferral = False
        self.fresh = False
        self._previous_input = None

    def _remember_input(self, payload, tokens):
        if tokens is not None:
            items = payload.get("input", [])
            self._previous_input = (len(items), input_digest(items))
            profile = getattr(getattr(self.spec, "settings", None), "instruction_profile",
                              getattr(self.scene, "instruction_profile", None))
            if resolve_profile(profile) != LEGACY_PROFILE:
                from .image_delivery import sync_context
                sync_context(self.scene, items)

    def check(self, payload, body_bytes, images, tokens):
        reasons = pressure(body_bytes, images, tokens, self.guard.tariff)
        measurements = {"payload_bytes": body_bytes, "images": images, "input_tokens": tokens}
        review = self.scene.renewal_state().get("finish_review", {})
        review_key = (self.session.session_id, review.get("call_id"))
        pending = review.get("status") == "pending" and bool(review_key[1])
        review_request = (pending and review_key not in self._finish_review_requests
                          and not self._after_finish_review_deferral)
        self._after_finish_review_deferral = False
        if pending:
            # One request to submit this review, even if that request has no soft
            # pressure. A refusal or a different tool must not defer renewal again.
            self._finish_review_requests.add(review_key)
        if self.fresh:
            # A minimal conversation can itself exceed a soft threshold. Renewing
            # that very same input forever would remove access to the owner's photos.
            # Permit this first rebuilt request through the unchanged hard/money guards.
            self.fresh = False
            self.records[-1]["rebuilt_request"] = measurements
            self.records[-1]["irreducible_pressure"] = reasons
            self._save()
            self.log.event("conversation_rebuilt_request", **measurements, irreducible_pressure=reasons,
                           conversation_id=self.session.session_id)
            self._remember_input(payload, tokens)
            return
        if reasons:
            within_hard_limits = (
                tokens is not None
                and tokens <= min(self.guard.tariff.long_context_input_tokens, budget.MODEL_MAX_INPUT_TOKENS)
                and images <= budget.PAYLOAD_LIMIT_IMAGES
                and body_bytes <= budget.PAYLOAD_LIMIT_BYTES
            )
            if review_request and within_hard_limits:
                row = {"schema": "g3d.conversation_renewal_deferral/1",
                       "conversation_id": self.session.session_id, "review_call_id": review_key[1],
                       "request_sequence": self.guard.summary()["requests"] + 1,
                       "measurements": measurements, "reasons": reasons}
                self.finish_review_deferrals.append(row)
                # Even another first finish must allow normal renewal next time.
                self._after_finish_review_deferral = True
                self._save()
                self.log.event("conversation_renewal_deferred", deferral=row)
                self._remember_input(payload, tokens)
                return
            raise RenewalNeeded(payload, measurements, reasons)
        self._remember_input(payload, tokens)

    def _save(self):
        write_json(self.directory / "renewals.json", {"schema": "g3d.conversation_renewals/1",
                                                     "renewals": self.records,
                                                     "finish_review_deferrals": self.finish_review_deferrals})

    async def renew(self, trigger, *, agent, context, hooks):
        old_id = self.session.session_id
        old_items = await self.session.get_items()
        old_hash = hashlib.sha256(json.dumps(old_items, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        summary, skipped, summary_sequence = "", None, None
        profile = getattr(getattr(self.spec, "settings", None), "instruction_profile",
                          getattr(self.scene, "instruction_profile", None))
        decision_tools = any(getattr(tool, "name", None) == "record_difference"
                             for tool in getattr(self.spec, "tools", ()))
        decision_state = decision_tools and resolve_profile(profile) != LEGACY_PROFILE
        summary_items, latest, omitted = (summary_input(trigger.payload.get("input", []), self._previous_input)
                                          if decision_state else (copy.deepcopy(trigger.payload.get("input", [])), [], []))
        callback = self.guard.request_pressure
        self.guard.request_pressure = None  # the summary is admitted normally, without recursive renewal
        self.guard.renewal_summary = True
        try:
            # The ordinary temple lab has its own best/revision state and tools.
            # Only main-tool conversations use the host differences protocol.
            summary_item = {"role": "user", "content": summary_request(profile if decision_state else LEGACY_PROFILE)}
            # Persist exactly one appended host message even if admission refuses it.
            await self.session.add_items([summary_item])
            await hooks.on_llm_start(context, agent, agent.instructions, trigger.payload.get("input", []))
            request = copy.deepcopy(trigger.payload)
            request["input"] = [*summary_items, summary_item]
            request["tool_choice"] = "none"
            response = await self.client.responses.create(**request)
            await hooks.on_llm_end(context, agent, SimpleNamespace(response_id=response.id,
                                      request_id=getattr(response, "_request_id", None), usage=response.usage))
            summary_sequence = self.guard.summary()["requests"]
            summary = response.output_text or ""
            # No tool can execute in a summary turn, even if a scripted/provider reply violates tool_choice.
            if any(item.type == "function_call" for item in response.output):
                skipped = "summary returned a tool call; no tool was executed"
                summary = ""
            else:
                await self.session.add_items([item.model_dump(exclude_none=True) for item in response.output])
                if not summary:
                    skipped = "summary response contained no written state"
            self.log.event("renewal_summary_response", request_seq=summary_sequence, status=response.status)
        except Exception as error:
            refusal = find_error(error, (budget.BudgetExceeded, budget.StageBudgetExceeded, budget.PayloadTooLarge))
            if refusal is None:
                raise
            self.log.interactions -= 1  # proven pre-admission refusal, not a model turn
            skipped = f"{type(refusal).__name__}: {refusal}"
        finally:
            self.guard.renewal_summary = False
            self.guard.request_pressure = callback
        # Preserve exact earlier item content, then rotate atomically. The host never
        # edits a prior conversation to regain cache space.
        after = await self.session.get_items()
        if after[:len(old_items)] != old_items:
            raise RuntimeError("conversation prefix changed while obtaining renewal summary")
        latest_calls = {item["call_id"] for item in latest}
        carried_hashes = set()
        for item in latest:
            for part in item["output"]:
                url = part.get("image_url")
                if part.get("type") == "input_image" and isinstance(url, str) and url.startswith("data:image/"):
                    carried_hashes.add(hashlib.sha256(base64.b64decode(url.split(",", 1)[1], validate=True)).hexdigest())
        # A latest result may reuse some earlier images by reference. Carry those
        # selected evidence bytes too; only actual attached pixels are duplicates.
        # Receipt metadata remains complete even when the latest output already
        # carries the pixels; deduplication changes content placement only.
        evidence, evidence_rows = evidence_content(self.scene.renewal_evidence(), trigger.payload,
                                                   already_carried=carried_hashes)
        latest_content = []
        for item in latest:
            latest_content.append({"type": "input_text", "text":
                f"Complete latest tool result from call {item['call_id']}; its images were not inspected by the summary:"})
            latest_content.extend(copy.deepcopy(item["output"]))
        restart = self.scene.renewal_started()
        scene_state = self.scene.renewal_state()
        state = {**scene_state, "finish_review_renewal": restart, "money": money_facts(self.guard.summary()),
                 "tool_calls_so_far": list(self.log.tool_calls),
                 "owner_requests_supplied_for_this_run": list(self.owner_notes)}
        if decision_state:
            from . import differences
            state.update(differences=differences.snapshot(self.scene),
                         formal_best_checkpoint=differences.formal_best(self.scene))
        notice = RENEWAL_NOTICE
        if restart.get("finish_review_restarted"):
            notice += " " + FINISH_RESTART_NOTICE
        plan = plan_session_input([], prompt=self.spec.brief, photos=self.spec.photos,
                                  invocation_text=notice, owner_notes=self.owner_notes)
        content = [*plan.content,
                   {"type": "input_text", "text": ASTRA_SUMMARY_PREFIX + summary if summary else NO_SUMMARY},
                   {"type": "input_text", "text": HOST_STATE_PREFIX + json.dumps(state, ensure_ascii=False)},
                   *evidence, *latest_content]
        row = {"schema": "g3d.conversation_renewal/1", "number": len(self.records) + 1,
               "run_id": self.log.run_id, "receipt_directory": str(self.directory),
               "old_conversation_id": old_id,
               "reasons": trigger.reasons, "measurements": trigger.measurements,
               "old_items": len(old_items), "old_items_sha256": old_hash,
               "summary_sha256": hashlib.sha256(summary.encode("utf-8")).hexdigest() if summary else None,
               "summary_text": summary, "summary_request_sequence": summary_sequence,
               "summary_skipped": skipped, "evidence": evidence_rows, "input_receipt": plan.receipt,
               "summary_omitted_images": omitted,
               "latest_result_call_ids": sorted(latest_calls),
               "preserved_request_prefix": ({"items": self._previous_input[0], "sha256": self._previous_input[1]}
                                            if omitted else None),
               "host_state": state}
        new_id = await self.session.renew([{"role": "user", "content": content}], receipt=row)
        self.records.append(row)
        self.fresh = True
        self._previous_input = None
        self._save()
        self.log.note(f"Conversation renewed: {old_id} -> {new_id}; "
                      + ", ".join(r["reason"] for r in trigger.reasons), renewal=row)
        self.log.event("conversation_renewed", renewal=row)
