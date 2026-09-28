"""The job's authoritative state: one SQLite database in the job folder.

WAL, ``synchronous=FULL``, foreign keys, explicit ``BEGIN IMMEDIATE`` transactions and a single-runner lease with a
fencing token. Every other file in the job folder (artifacts, request payloads, raw responses, the deliverable) is
referenced from here by path and SHA-256; status JSON and manifests are exports of this database, never a second
store. A job is created into an empty folder only; reopening goes through ``Store.open`` (resume) and never rewrites
the request or the turn numbering. Do not put this database on a network filesystem whose locking is unverified.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote
import uuid

from . import PACKAGE_PROTOCOL, SCHEMA_VERSION

DB_NAME = "job.sqlite3"
# the runner's lease: every runner (runner.Session, tools.lease_renewed, rebuild) takes and renews it for this long
LEASE_TTL_S = 900

JOB_STATES = ("created", "ready", "inference_pending", "inference_unknown", "tool_pending", "tool_running",
              "needs_observation", "ready_for_final", "evaluating", "awaiting_owner",
              "delivered", "unresolved", "budget_exhausted", "cancelled", "failed", "needs_attention")
TERMINAL_STATES = frozenset({"delivered", "unresolved", "budget_exhausted", "cancelled", "failed"})
# needs_attention is a durable stop that an explicit resume may continue after the owner has looked; the others are final.
# awaiting_owner (the owner review loop, 2026-09-28) is a durable wait that costs nothing and never expires: the author asked
# for delivery and the owner judges the candidate in the live AR try-on. Only the owner leaves it: accept (-> evaluating ->
# delivered: the one way a job with owner review delivers), changes (-> ready: the owner's words go to the same author
# conversation), stop (-> unresolved, the candidate kept) or cancel.
TRANSITIONS = {
    "created": {"ready", "failed", "cancelled", "needs_attention"},
    "ready": {"inference_pending", "tool_pending", "ready_for_final", "budget_exhausted", "cancelled", "failed",
              "unresolved", "needs_attention", "needs_observation"},
    "inference_pending": {"inference_unknown", "tool_pending", "ready", "ready_for_final", "needs_attention", "failed",
                          "cancelled", "budget_exhausted", "unresolved", "needs_observation"},
    "inference_unknown": {"needs_attention", "cancelled", "failed", "ready"},
    "tool_pending": {"tool_running", "ready", "ready_for_final", "cancelled", "failed", "needs_attention"},
    "tool_running": {"needs_observation", "ready", "tool_pending", "cancelled", "failed", "needs_attention", "ready_for_final"},
    "needs_observation": {"ready", "inference_pending", "ready_for_final", "cancelled", "failed", "budget_exhausted", "unresolved", "needs_attention"},
    "ready_for_final": {"evaluating", "cancelled", "failed", "needs_attention", "delivered", "unresolved", "budget_exhausted", "awaiting_owner"},
    "evaluating": {"delivered", "unresolved", "failed", "cancelled", "needs_attention", "budget_exhausted", "awaiting_owner"},
    "awaiting_owner": {"ready", "evaluating", "unresolved", "cancelled", "failed", "needs_attention"},
    "needs_attention": {"ready", "cancelled", "failed", "unresolved"},
    "delivered": set(), "unresolved": set(), "budget_exhausted": set(), "cancelled": set(), "failed": set(),
}

REVISION_STATES = ("created", "building", "build_failed", "built", "export_failed", "exported", "observing", "observed",
                   "compatible", "incompatible", "interrupted")
OPERATION_STATES = ("pending", "running", "completed", "failed", "interrupted", "cancelled", "timed_out")
REQUEST_STATES = ("prepared", "sent", "unknown", "completed", "incomplete", "failed", "refused", "replayed", "released")
OBSERVATION_STATES = ("pending", "included", "acknowledged", "deferred", "failed")
RESERVATION_STATES = ("held", "settled", "unknown", "released")

SCHEMA = """
CREATE TABLE job (
  id INTEGER PRIMARY KEY CHECK (id = 1), schema_version INTEGER NOT NULL, protocol TEXT NOT NULL, created_utc TEXT NOT NULL,
  updated_utc TEXT NOT NULL, state TEXT NOT NULL, stop_reason TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
  lease_holder TEXT, lease_fence INTEGER NOT NULL DEFAULT 0, lease_expires_utc TEXT,
  request_json TEXT NOT NULL, request_sha256 TEXT NOT NULL, policy_json TEXT NOT NULL, fingerprints_json TEXT NOT NULL,
  model TEXT NOT NULL, reasoning_effort TEXT NOT NULL, service_tier TEXT NOT NULL, endpoint TEXT NOT NULL,
  price_version TEXT NOT NULL, tariff_json TEXT NOT NULL, cap_micro INTEGER NOT NULL, inference_operation_cap INTEGER NOT NULL,
  current_revision TEXT, selected_revision TEXT, deliverable_status TEXT, deliverable_json TEXT, driver TEXT NOT NULL,
  worker TEXT NOT NULL, worker_config_json TEXT);
CREATE TABLE artifacts (
  id TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE, kind TEXT NOT NULL, role TEXT NOT NULL, rel_path TEXT NOT NULL UNIQUE,
  sha256 TEXT NOT NULL, media_type TEXT NOT NULL, bytes INTEGER NOT NULL, revision_id TEXT, parent_id TEXT REFERENCES artifacts(id),
  recipe_json TEXT, label TEXT, synthetic INTEGER NOT NULL DEFAULT 0, created_utc TEXT NOT NULL);
CREATE TABLE revisions (
  id TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE, parent_id TEXT REFERENCES revisions(id), program_set_sha256 TEXT NOT NULL,
  modules_json TEXT NOT NULL, operation_id TEXT, worker_json TEXT, glb_artifact_id TEXT REFERENCES artifacts(id), glb_sha256 TEXT,
  compatibility_json TEXT, observation_json TEXT, state TEXT NOT NULL, synthetic INTEGER NOT NULL DEFAULT 0, rationale TEXT,
  created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL);
CREATE TABLE epochs (role TEXT NOT NULL, epoch INTEGER NOT NULL, created_utc TEXT NOT NULL, reason TEXT, compact_request_id TEXT,
  PRIMARY KEY (role, epoch));
CREATE TABLE conversation_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, epoch INTEGER NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL,
  item_json TEXT NOT NULL, item_sha256 TEXT NOT NULL, request_id TEXT, created_utc TEXT NOT NULL, UNIQUE (role, epoch, seq),
  FOREIGN KEY (role, epoch) REFERENCES epochs(role, epoch));
CREATE TABLE inference_requests (
  id TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE, role TEXT NOT NULL, purpose TEXT NOT NULL, epoch INTEGER, input_sha256 TEXT,
  payload_sha256 TEXT, payload_path TEXT, reservation_id TEXT, state TEXT NOT NULL, response_path TEXT, response_sha256 TEXT,
  provider_response_id TEXT, response_model TEXT, service_tier TEXT, http_status INTEGER, usage_json TEXT, input_token_count INTEGER,
  error TEXT, created_utc TEXT NOT NULL, sent_utc TEXT, completed_utc TEXT);
CREATE TABLE tool_operations (
  id TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE, call_id TEXT NOT NULL, request_id TEXT REFERENCES inference_requests(id),
  tool_name TEXT NOT NULL, schema_version TEXT NOT NULL, args_sha256 TEXT NOT NULL, args_json TEXT NOT NULL, source_revision_id TEXT,
  attempt INTEGER NOT NULL, state TEXT NOT NULL, worker_json TEXT, deadline_utc TEXT, fence INTEGER, output_manifest_sha256 TEXT,
  result_json TEXT, output_committed INTEGER NOT NULL DEFAULT 0, created_utc TEXT NOT NULL, started_utc TEXT, completed_utc TEXT,
  parent_operation_id TEXT REFERENCES tool_operations(id), UNIQUE (call_id, attempt));
CREATE TABLE observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, artifact_id TEXT NOT NULL UNIQUE REFERENCES artifacts(id), revision_id TEXT, operation_id TEXT,
  required INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL, included_request_id TEXT, acknowledged_request_id TEXT,
  created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL);
CREATE TABLE reservations (
  id TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE, role TEXT NOT NULL, purpose TEXT NOT NULL, request_id TEXT, reserved_micro INTEGER NOT NULL,
  state TEXT NOT NULL, settled_micro INTEGER, liability_micro INTEGER NOT NULL DEFAULT 0, reason TEXT, usage_json TEXT,
  price_version TEXT NOT NULL, counts_operation INTEGER NOT NULL DEFAULT 1, created_utc TEXT NOT NULL, settled_utc TEXT);
CREATE TABLE verdicts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, revision_id TEXT, asset_sha256 TEXT, verdict TEXT NOT NULL,
  bindings_json TEXT NOT NULL, record_json TEXT NOT NULL, created_utc TEXT NOT NULL);
CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, utc TEXT NOT NULL, kind TEXT NOT NULL, data_json TEXT NOT NULL);
CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
"""


# Additive columns: a job created before one existed gets it on its first read-write open (Store.__init__), journaled as
# 'schema_migrated'; a read-only open never writes and reads the column as None. SCHEMA_VERSION stays, because an old
# runner ignores the column and a new one reads NULL exactly like the recorded history (nothing is backfilled).
# tool_operations.parent_operation_id (2026-09-28, INF-18): the tool operation a sub-operation (a render_only under a
# running build or edit) belongs to; NULL for a tool's own operation and for every row recorded before it existed.
ADDITIVE_COLUMNS = (("tool_operations", "parent_operation_id", "TEXT REFERENCES tool_operations(id)"),)
# Additive tables, created the same way (first read-write open, one transaction, journaled as 'schema_migrated'); a
# read-only open of an older job reads them as empty. owner_rounds (2026-09-28): one row per candidate the owner was asked
# to review (the owner review loop): the byte-bound candidate, when it was opened, and the owner's decision on it
# (accept / changes / stop / continued_in_new_job / cancelled) with everything the decision recorded.
ADDITIVE_TABLES = (("owner_rounds",
                    "CREATE TABLE owner_rounds (round INTEGER PRIMARY KEY, revision_id TEXT NOT NULL, asset_sha256 TEXT, source TEXT NOT NULL, "
                    "candidate_json TEXT NOT NULL, opened_utc TEXT NOT NULL, decision TEXT, decided_utc TEXT, decision_json TEXT)"),)


class StateError(RuntimeError):
    pass


class TransitionError(StateError):
    pass


class LeaseError(StateError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(value) -> str:
    return sha256_bytes(canonical_json(value))


def _row(cur) -> dict | None:
    r = cur.fetchone()
    return dict(r) if r is not None else None


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


class Store:
    """The database handle. Every write happens inside ``with store.tx():`` (BEGIN IMMEDIATE ... COMMIT)."""

    def __init__(self, job_dir: Path, *, clock=None, readonly: bool = False):
        self.job_dir = Path(job_dir).resolve()
        self.path = self.job_dir / DB_NAME
        self.clock = clock or utcnow
        self._depth = 0
        if not self.path.exists():
            raise StateError(f"no job database at {self.path}")
        # percent-encoded: a '#' or '%xx' in a folder name would otherwise select a different (or no) database
        uri = f"file:{quote(self.path.as_posix(), safe='/:')}?mode={'ro' if readonly else 'rw'}"
        self.conn = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=10.0)
        try:
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA busy_timeout = 10000")
            self.conn.execute("PRAGMA foreign_keys = ON")
            if not readonly:
                self.conn.execute("PRAGMA journal_mode = WAL")
                self.conn.execute("PRAGMA synchronous = FULL")
            job = _row(self.conn.execute("SELECT schema_version, protocol FROM job WHERE id = 1"))
            if job is None:
                raise StateError("job row missing")
            if job["schema_version"] != SCHEMA_VERSION or job["protocol"] != PACKAGE_PROTOCOL:
                raise StateError(f"job schema {job['schema_version']}/{job['protocol']} is not {SCHEMA_VERSION}/{PACKAGE_PROTOCOL}; migration required")
            self.columns = {table: self._table_columns(table) for table in {t for t, _c, _d in ADDITIVE_COLUMNS}}
            self.tables = self._table_names()
            if not readonly:
                self._add_missing_columns()
        except BaseException:
            # a refused open must not leave its handle behind: on Windows it would keep the job folder locked until garbage collection
            self.conn.close()
            raise

    def _table_columns(self, table: str) -> set[str]:
        return {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    def _table_names(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    def _add_missing_columns(self) -> None:
        """ADDITIVE_COLUMNS and ADDITIVE_TABLES a job created before them lacks, added in one transaction (re-checked inside
        it: two openers race harmlessly) and journaled once."""
        if all(c in self.columns[t] for t, c, _d in ADDITIVE_COLUMNS) and all(t in self.tables for t, _ddl in ADDITIVE_TABLES):
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            added = []
            for table, column, decl in ADDITIVE_COLUMNS:
                if column not in self._table_columns(table):
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                    added.append(f"{table}.{column}")
            present = self._table_names()
            for table, ddl in ADDITIVE_TABLES:
                if table not in present:
                    self.conn.execute(ddl)
                    added.append(table)
            if added:
                self.conn.execute("INSERT INTO events (utc, kind, data_json) VALUES (?, 'schema_migrated', ?)",
                                  (self.now(), canonical_json({"added": added}).decode()))
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.columns = {table: self._table_columns(table) for table in self.columns}
        self.tables = self._table_names()

    # ------------------------------------------------------------------ creation / opening
    @classmethod
    def create(cls, job_dir: Path, job: dict, *, clock=None) -> "Store":
        """A fresh job into an empty (or absent) folder; refuses a non-empty one."""
        job_dir = Path(job_dir).resolve()
        if job_dir.exists():
            if not job_dir.is_dir():
                raise StateError(f"{job_dir} is not a directory")
            if any(job_dir.iterdir()):
                raise StateError(f"refusing to create a job in the non-empty folder {job_dir}")
        else:
            job_dir.mkdir(parents=True)
        path = job_dir / DB_NAME
        now = iso((clock or utcnow)())
        conn = sqlite3.connect(str(path), isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = FULL")
            conn.execute("PRAGMA foreign_keys = ON")
            # executescript commits any open transaction first, so it runs before BEGIN; a new job has every additive table from the start
            conn.executescript(SCHEMA + "".join(f"\n{ddl};" for _t, ddl in ADDITIVE_TABLES))
            conn.execute("BEGIN IMMEDIATE")
            required = ("request", "policy", "fingerprints", "model", "reasoning_effort", "service_tier", "endpoint", "tariff",
                        "cap_micro", "inference_operation_cap", "driver", "worker")
            missing = [k for k in required if k not in job]
            if missing:
                raise StateError(f"job record lacks {missing}")
            conn.execute(
                "INSERT INTO job (id, schema_version, protocol, created_utc, updated_utc, state, request_json, request_sha256, policy_json, "
                "fingerprints_json, model, reasoning_effort, service_tier, endpoint, price_version, tariff_json, cap_micro, inference_operation_cap, "
                "driver, worker, worker_config_json) VALUES (1, ?, ?, ?, ?, 'created', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (SCHEMA_VERSION, PACKAGE_PROTOCOL, now, now, canonical_json(job["request"]).decode(), sha256_json(job["request"]),
                 canonical_json(job["policy"]).decode(), canonical_json(job["fingerprints"]).decode(), job["model"], job["reasoning_effort"],
                 job["service_tier"], job["endpoint"], job["tariff"]["price_version"], canonical_json(job["tariff"]).decode(),
                 int(job["cap_micro"]), int(job["inference_operation_cap"]), job["driver"], job["worker"],
                 canonical_json(job.get("worker_config")).decode() if job.get("worker_config") is not None else None))
            conn.execute("INSERT INTO events (utc, kind, data_json) VALUES (?, 'job_created', ?)", (now, canonical_json({"request_sha256": sha256_json(job["request"])}).decode()))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            conn.close()
            for suffix in ("", "-wal", "-shm"):
                # a refused creation leaves the folder as it found it, so the same output folder can be used again
                Path(str(path) + suffix).unlink(missing_ok=True)
            raise
        conn.close()
        return cls(job_dir, clock=clock)

    @classmethod
    def open(cls, job_dir: Path, *, clock=None, readonly: bool = False) -> "Store":
        return cls(job_dir, clock=clock, readonly=readonly)

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ transactions
    @contextmanager
    def tx(self):
        """BEGIN IMMEDIATE at the outermost level; nested uses join the transaction."""
        if self._depth == 0:
            self.conn.execute("BEGIN IMMEDIATE")
        self._depth += 1
        try:
            yield self.conn
        except BaseException:
            self._depth -= 1
            if self._depth == 0:
                self.conn.execute("ROLLBACK")
            raise
        else:
            self._depth -= 1
            if self._depth == 0:
                self.conn.execute("COMMIT")

    def now(self) -> str:
        return iso(self.clock())

    def _next_seq(self, table: str) -> int:
        return int(self.conn.execute(f"SELECT COALESCE(MAX(seq), 0) + 1 FROM {table}").fetchone()[0])

    # ------------------------------------------------------------------ job row
    def job(self) -> dict:
        j = _row(self.conn.execute("SELECT * FROM job WHERE id = 1"))
        for k in ("request_json", "policy_json", "fingerprints_json", "tariff_json", "deliverable_json", "worker_config_json"):
            j[k[:-5]] = json.loads(j[k]) if j.get(k) else None
        return j

    def update_job(self, **fields) -> None:
        allowed = {"stop_reason", "cancel_requested", "current_revision", "selected_revision", "deliverable_status", "deliverable_json",
                   "worker_config_json"}
        bad = set(fields) - allowed
        if bad:
            raise StateError(f"job fields {sorted(bad)} are not writable this way")
        with self.tx():
            for k, v in fields.items():
                if k.endswith("_json") and v is not None and not isinstance(v, str):
                    v = canonical_json(v).decode()
                self.conn.execute(f"UPDATE job SET {k} = ?, updated_utc = ? WHERE id = 1", (v, self.now()))

    def state(self) -> str:
        return self.conn.execute("SELECT state FROM job WHERE id = 1").fetchone()[0]

    def transition(self, new_state: str, reason: str = "", *, expected: str | tuple | None = None, stop_reason: str | None = None) -> str:
        """Legal state change or TransitionError; recorded as an event. Returns the previous state."""
        if new_state not in JOB_STATES:
            raise TransitionError(f"unknown state {new_state!r}")
        with self.tx():
            cur = self.state()
            if expected is not None:
                exp = (expected,) if isinstance(expected, str) else tuple(expected)
                if cur not in exp:
                    raise TransitionError(f"expected state {exp}, found {cur!r}")
            if new_state not in TRANSITIONS[cur]:
                raise TransitionError(f"illegal transition {cur!r} -> {new_state!r} ({reason})")
            self.conn.execute("UPDATE job SET state = ?, updated_utc = ?, stop_reason = COALESCE(?, stop_reason) WHERE id = 1",
                              (new_state, self.now(), stop_reason))
            self.event("transition", previous=cur, state=new_state, reason=reason)
            return cur

    # ------------------------------------------------------------------ lease
    def acquire_lease(self, holder: str, ttl_s: int = 600) -> int:
        """A new fence for this runner; refuses while another live holder owns the lease."""
        with self.tx():
            j = self.job()
            now = self.clock()
            if j["lease_holder"] and j["lease_holder"] != holder and j["lease_expires_utc"] and parse_iso(j["lease_expires_utc"]) > now:
                raise LeaseError(f"the job is held by {j['lease_holder']!r} until {j['lease_expires_utc']}")
            fence = int(j["lease_fence"]) + 1
            self.conn.execute("UPDATE job SET lease_holder = ?, lease_fence = ?, lease_expires_utc = ?, updated_utc = ? WHERE id = 1",
                              (holder, fence, iso(now + timedelta(seconds=int(ttl_s))), self.now()))
            self.event("lease_acquired", holder=holder, fence=fence, previous_holder=j["lease_holder"])
            return fence

    def renew_lease(self, holder: str, fence: int, ttl_s: int = 600) -> None:
        with self.tx():
            self.require_fence(holder, fence)
            self.conn.execute("UPDATE job SET lease_expires_utc = ?, updated_utc = ? WHERE id = 1",
                              (iso(self.clock() + timedelta(seconds=int(ttl_s))), self.now()))

    def release_lease(self, holder: str, fence: int) -> None:
        with self.tx():
            j = self.job()
            if j["lease_holder"] == holder and int(j["lease_fence"]) == int(fence):
                self.conn.execute("UPDATE job SET lease_holder = NULL, lease_expires_utc = NULL, updated_utc = ? WHERE id = 1", (self.now(),))
                self.event("lease_released", holder=holder, fence=fence)

    def require_fence(self, holder: str, fence: int) -> None:
        j = _row(self.conn.execute("SELECT lease_holder, lease_fence FROM job WHERE id = 1"))
        if j["lease_holder"] != holder or int(j["lease_fence"]) != int(fence):
            raise LeaseError(f"stale lease: fence {fence} of {holder!r} is not the current {j['lease_fence']} of {j['lease_holder']!r}")

    # ------------------------------------------------------------------ events / settings
    def event(self, kind: str, **data) -> None:
        with self.tx():
            self.conn.execute("INSERT INTO events (utc, kind, data_json) VALUES (?, ?, ?)", (self.now(), kind, canonical_json(data).decode()))

    def events(self, kind: str | None = None) -> list[dict]:
        if kind:
            rows = _rows(self.conn.execute("SELECT * FROM events WHERE kind = ? ORDER BY id", (kind,)))
        else:
            rows = _rows(self.conn.execute("SELECT * FROM events ORDER BY id"))
        for r in rows:
            r["data"] = json.loads(r.pop("data_json"))
        return rows

    def set_setting(self, key: str, value) -> None:
        with self.tx():
            self.conn.execute("INSERT INTO settings (key, value_json) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                              (key, canonical_json(value).decode()))

    def setting(self, key: str, default=None):
        r = self.conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    # ------------------------------------------------------------------ artifacts
    def insert_artifact(self, *, kind: str, role: str, rel_path: str, sha256: str, media_type: str, nbytes: int, revision_id=None,
                        parent_id=None, recipe=None, label: str = "", synthetic: bool = False, prefix: str = "art") -> dict:
        with self.tx():
            seq = self._next_seq("artifacts")
            aid = f"{prefix}{seq:04d}"
            self.conn.execute("INSERT INTO artifacts (id, seq, kind, role, rel_path, sha256, media_type, bytes, revision_id, parent_id, recipe_json, label, synthetic, created_utc) "
                              "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              (aid, seq, kind, role, rel_path, sha256, media_type, int(nbytes), revision_id, parent_id,
                               canonical_json(recipe).decode() if recipe is not None else None, label, int(bool(synthetic)), self.now()))
            return self.artifact(aid)

    def artifact(self, aid: str) -> dict | None:
        r = _row(self.conn.execute("SELECT * FROM artifacts WHERE id = ?", (aid,)))
        if r and r.get("recipe_json"):
            r["recipe"] = json.loads(r["recipe_json"])
        return r

    def artifacts(self, *, role: str | None = None, kind: str | None = None, revision_id: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM artifacts WHERE 1 = 1", []
        for col, val in (("role", role), ("kind", kind), ("revision_id", revision_id)):
            if val is not None:
                q += f" AND {col} = ?"
                args.append(val)
        rows = _rows(self.conn.execute(q + " ORDER BY seq", args))
        for r in rows:
            if r.get("recipe_json"):
                r["recipe"] = json.loads(r["recipe_json"])
        return rows

    # ------------------------------------------------------------------ revisions
    def insert_revision(self, *, parent_id: str | None, program_set_sha256: str, modules: dict, rationale: str = "",
                        synthetic: bool = False) -> dict:
        with self.tx():
            seq = self._next_seq("revisions")
            rid = f"r{seq:04d}"
            now = self.now()
            self.conn.execute("INSERT INTO revisions (id, seq, parent_id, program_set_sha256, modules_json, state, synthetic, rationale, created_utc, updated_utc) "
                              "VALUES (?, ?, ?, ?, ?, 'created', ?, ?, ?, ?)",
                              (rid, seq, parent_id, program_set_sha256, canonical_json(modules).decode(), int(bool(synthetic)), rationale, now, now))
            return self.revision(rid)

    def revision(self, rid: str) -> dict | None:
        r = _row(self.conn.execute("SELECT * FROM revisions WHERE id = ?", (rid,)))
        if r is None:
            return None
        for k in ("modules_json", "worker_json", "compatibility_json", "observation_json"):
            r[k[:-5]] = json.loads(r[k]) if r.get(k) else None
        return r

    def revisions(self) -> list[dict]:
        return [self.revision(r["id"]) for r in _rows(self.conn.execute("SELECT id FROM revisions ORDER BY seq"))]

    def update_revision(self, rid: str, **fields) -> None:
        allowed = {"operation_id", "worker_json", "glb_artifact_id", "glb_sha256", "compatibility_json", "observation_json", "state"}
        bad = set(fields) - allowed
        if bad:
            raise StateError(f"revision fields {sorted(bad)} are not writable")
        if "state" in fields and fields["state"] not in REVISION_STATES:
            raise StateError(f"unknown revision state {fields['state']!r}")
        with self.tx():
            if self.revision(rid) is None:
                raise StateError(f"no revision {rid}")
            for k, v in fields.items():
                if k.endswith("_json") and v is not None and not isinstance(v, str):
                    v = canonical_json(v).decode()
                self.conn.execute(f"UPDATE revisions SET {k} = ?, updated_utc = ? WHERE id = ?", (v, self.now(), rid))

    # ------------------------------------------------------------------ conversations
    def epoch(self, role: str) -> int:
        r = self.conn.execute("SELECT MAX(epoch) FROM epochs WHERE role = ?", (role,)).fetchone()[0]
        return int(r) if r is not None else 0

    def compaction_epoch(self, role: str, request_id: str) -> int | None:
        """The epoch a compaction request opened, or None when its returned window was never applied."""
        r = _row(self.conn.execute("SELECT epoch FROM epochs WHERE role = ? AND compact_request_id = ?", (role, request_id)))
        return int(r["epoch"]) if r else None

    def new_epoch(self, role: str, reason: str, compact_request_id: str | None = None) -> int:
        with self.tx():
            e = self.epoch(role) + 1
            self.conn.execute("INSERT INTO epochs (role, epoch, created_utc, reason, compact_request_id) VALUES (?, ?, ?, ?, ?)",
                              (role, e, self.now(), reason, compact_request_id))
            self.event("epoch", role=role, epoch=e, reason=reason)
            return e

    def append_items(self, role: str, items: list[dict], *, request_id: str | None = None, epoch: int | None = None) -> list[int]:
        """Complete items in order (user, developer, assistant messages, reasoning, function_call, function_call_output,
        compaction ...): stored verbatim, opaque fields included, never decoded."""
        with self.tx():
            e = self.epoch(role) if epoch is None else epoch
            if e == 0:
                raise StateError(f"role {role!r} has no epoch yet")
            seq = int(self.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM conversation_items WHERE role = ? AND epoch = ?", (role, e)).fetchone()[0])
            out = []
            for item in items:
                if not isinstance(item, dict):
                    raise StateError("a conversation item must be an object")
                seq += 1
                kind = item.get("type") or (f"message:{item.get('role')}" if item.get("role") else "unknown")
                raw = canonical_json(item)
                self.conn.execute("INSERT INTO conversation_items (role, epoch, seq, kind, item_json, item_sha256, request_id, created_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                  (role, e, seq, kind, raw.decode(), sha256_bytes(raw), request_id, self.now()))
                out.append(seq)
            return out

    def items(self, role: str, epoch: int | None = None) -> list[dict]:
        e = self.epoch(role) if epoch is None else epoch
        rows = _rows(self.conn.execute("SELECT * FROM conversation_items WHERE role = ? AND epoch = ? ORDER BY seq", (role, e)))
        for r in rows:
            r["item"] = json.loads(r.pop("item_json"))
        return rows

    def window(self, role: str) -> list[dict]:
        """The active conversation window: the items of the latest epoch, as the API expects them."""
        return [r["item"] for r in self.items(role)]

    def window_sha256(self, role: str) -> str:
        return sha256_json([r["item_sha256"] for r in self.items(role)])

    # ------------------------------------------------------------------ inference requests
    def insert_request(self, *, role: str, purpose: str, epoch: int | None, input_sha256: str | None, reservation_id: str | None,
                       payload_sha256: str | None = None, payload_path: str | None = None, input_token_count: int | None = None,
                       state: str = "prepared") -> dict:
        if state not in REQUEST_STATES:
            raise StateError(f"unknown request state {state!r}")
        with self.tx():
            seq = self._next_seq("inference_requests")
            qid = f"q{seq:04d}"
            self.conn.execute("INSERT INTO inference_requests (id, seq, role, purpose, epoch, input_sha256, payload_sha256, payload_path, reservation_id, state, input_token_count, created_utc) "
                              "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              (qid, seq, role, purpose, epoch, input_sha256, payload_sha256, payload_path, reservation_id, state, input_token_count, self.now()))
            return self.request(qid)

    def request(self, qid: str) -> dict | None:
        r = _row(self.conn.execute("SELECT * FROM inference_requests WHERE id = ?", (qid,)))
        if r and r.get("usage_json"):
            r["usage"] = json.loads(r["usage_json"])
        return r

    def requests(self, *, state: str | None = None, role: str | None = None) -> list[dict]:
        q, args = "SELECT id FROM inference_requests WHERE 1 = 1", []
        if state:
            q += " AND state = ?"
            args.append(state)
        if role:
            q += " AND role = ?"
            args.append(role)
        return [self.request(r["id"]) for r in _rows(self.conn.execute(q + " ORDER BY seq", args))]

    def update_request(self, qid: str, **fields) -> None:
        allowed = {"state", "payload_sha256", "payload_path", "response_path", "response_sha256", "provider_response_id", "response_model",
                   "service_tier", "http_status", "usage_json", "error", "sent_utc", "completed_utc", "input_token_count", "reservation_id"}
        bad = set(fields) - allowed
        if bad:
            raise StateError(f"request fields {sorted(bad)} are not writable")
        if "state" in fields and fields["state"] not in REQUEST_STATES:
            raise StateError(f"unknown request state {fields['state']!r}")
        with self.tx():
            if self.request(qid) is None:
                raise StateError(f"no request {qid}")
            for k, v in fields.items():
                if k.endswith("_json") and v is not None and not isinstance(v, str):
                    v = canonical_json(v).decode()
                self.conn.execute(f"UPDATE inference_requests SET {k} = ? WHERE id = ?", (v, qid))

    # ------------------------------------------------------------------ tool operations
    def insert_operation(self, *, call_id: str, request_id: str | None, tool_name: str, schema_version: str, args: dict,
                         source_revision_id: str | None, fence: int | None, deadline_utc: str | None = None, state: str = "pending",
                         parent_operation_id: str | None = None) -> dict:
        """A tool operation. ``attempt`` counts the operations of ``call_id`` (a retry of a finished call is attempt 2).

        A sub-operation (a render_only the observation runs under a build or edit) is NOT another attempt of its call
        (review 2026-09-28, INF-18: test-pilot-002 recorded six renders as 'attempt 2', and the container names carried
        '-2'): it is attempt 1 of its own call id ``<parent call id>/<its op id>`` with ``parent_operation_id`` naming
        the tool's operation. ``parent_operation_id`` given explicitly must be an operation of the same call; without it,
        an insert while the call's own operation is RUNNING is that operation's sub-operation (a tool never retries its
        own call while it runs). The call's own rows stay alone under ``call_id``, so the output the call owes is read
        from the parent's state; a host call's prefix (runner.HOST_CALL_PREFIX) is kept in the derived id."""
        if state not in OPERATION_STATES:
            raise StateError(f"unknown operation state {state!r}")
        with self.tx():
            parent = self._parent_for(call_id, parent_operation_id)
            seq = self._next_seq("tool_operations")
            oid = f"op{seq:04d}"
            if parent is not None:
                call_id, attempt = f"{parent['call_id']}/{oid}", 1
            else:
                attempt = 1 + int(self.conn.execute("SELECT COUNT(*) FROM tool_operations WHERE call_id = ?", (call_id,)).fetchone()[0])
            raw = canonical_json(args)
            cols = ["id", "seq", "call_id", "request_id", "tool_name", "schema_version", "args_sha256", "args_json", "source_revision_id", "attempt", "state",
                    "fence", "deadline_utc", "created_utc"]
            vals = [oid, seq, call_id, request_id, tool_name, schema_version, sha256_bytes(raw), raw.decode(), source_revision_id, attempt, state, fence,
                    deadline_utc, self.now()]
            if parent is not None:
                cols.append("parent_operation_id")
                vals.append(parent["id"])
            self.conn.execute(f"INSERT INTO tool_operations ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
            return self.operation(oid)

    def _parent_for(self, call_id: str, parent_operation_id: str | None) -> dict | None:
        """The operation a new row of ``call_id`` is a sub-operation of (insert_operation), or None for a call's own attempt."""
        if parent_operation_id is not None:
            parent = self.operation(parent_operation_id)
            if parent is None:
                raise StateError(f"no parent operation {parent_operation_id}")
            if parent["call_id"] != call_id:
                raise StateError(f"operation {parent_operation_id} belongs to call {parent['call_id']!r}, not {call_id!r}")
            if parent["parent_operation_id"] is not None:
                raise StateError(f"operation {parent_operation_id} is itself a sub-operation")
            return parent
        row = _row(self.conn.execute("SELECT id FROM tool_operations WHERE call_id = ? AND state = 'running' ORDER BY seq DESC LIMIT 1", (call_id,)))
        return self.operation(row["id"]) if row else None

    def operation(self, oid: str) -> dict | None:
        r = _row(self.conn.execute("SELECT * FROM tool_operations WHERE id = ?", (oid,)))
        if r is None:
            return None
        r["args"] = json.loads(r["args_json"])
        r.setdefault("parent_operation_id", None)          # a read-only open of a job recorded before the column existed
        for k in ("worker_json", "result_json"):
            r[k[:-5]] = json.loads(r[k]) if r.get(k) else None
        return r

    def operations(self, *, state: str | None = None, call_id: str | None = None, parent_operation_id: str | None = None) -> list[dict]:
        """Operations by seq; ``parent_operation_id`` lists a tool operation's sub-operations (none in a job recorded before them)."""
        q, args = "SELECT id FROM tool_operations WHERE 1 = 1", []
        if parent_operation_id is not None:
            if "parent_operation_id" not in self.columns["tool_operations"]:
                return []
            q += " AND parent_operation_id = ?"
            args.append(parent_operation_id)
        if state:
            q += " AND state = ?"
            args.append(state)
        if call_id:
            q += " AND call_id = ?"
            args.append(call_id)
        return [self.operation(r["id"]) for r in _rows(self.conn.execute(q + " ORDER BY seq", args))]

    def update_operation(self, oid: str, **fields) -> None:
        allowed = {"state", "worker_json", "deadline_utc", "fence", "output_manifest_sha256", "result_json", "output_committed", "started_utc", "completed_utc"}
        bad = set(fields) - allowed
        if bad:
            raise StateError(f"operation fields {sorted(bad)} are not writable")
        if "state" in fields and fields["state"] not in OPERATION_STATES:
            raise StateError(f"unknown operation state {fields['state']!r}")
        with self.tx():
            if self.operation(oid) is None:
                raise StateError(f"no operation {oid}")
            for k, v in fields.items():
                if k.endswith("_json") and v is not None and not isinstance(v, str):
                    v = canonical_json(v).decode()
                self.conn.execute(f"UPDATE tool_operations SET {k} = ? WHERE id = ?", (v, oid))

    # ------------------------------------------------------------------ observation queue
    def enqueue_observation(self, artifact_id: str, *, revision_id: str | None, operation_id: str | None, required: bool) -> dict:
        with self.tx():
            now = self.now()
            self.conn.execute("INSERT INTO observations (artifact_id, revision_id, operation_id, required, state, created_utc, updated_utc) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                              (artifact_id, revision_id, operation_id, int(bool(required)), now, now))
            return self.observation(artifact_id)

    def observation(self, artifact_id: str) -> dict | None:
        return _row(self.conn.execute("SELECT * FROM observations WHERE artifact_id = ?", (artifact_id,)))

    def observations(self, *, state: str | None = None, revision_id: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM observations WHERE 1 = 1", []
        if state:
            q += " AND state = ?"
            args.append(state)
        if revision_id:
            q += " AND revision_id = ?"
            args.append(revision_id)
        return _rows(self.conn.execute(q + " ORDER BY id", args))

    def set_observation(self, artifact_id: str, state: str, *, included_request_id=None, acknowledged_request_id=None, reset_request: bool = False) -> None:
        """``reset_request`` clears the request the image was included in (it goes back to the queue or to the unsent window);
        without it a None id keeps the recorded one, so a re-carried image would stay bound to a request that never completed."""
        if state not in OBSERVATION_STATES:
            raise StateError(f"unknown observation state {state!r}")
        with self.tx():
            if self.observation(artifact_id) is None:
                raise StateError(f"no observation for {artifact_id}")
            if reset_request:
                self.conn.execute("UPDATE observations SET included_request_id = NULL WHERE artifact_id = ?", (artifact_id,))
            self.conn.execute("UPDATE observations SET state = ?, included_request_id = COALESCE(?, included_request_id), "
                              "acknowledged_request_id = COALESCE(?, acknowledged_request_id), updated_utc = ? WHERE artifact_id = ?",
                              (state, included_request_id, acknowledged_request_id, self.now(), artifact_id))

    # ------------------------------------------------------------------ reservations (the budget module owns the rules)
    def insert_reservation(self, *, role: str, purpose: str, request_id: str | None, reserved_micro: int, price_version: str,
                           counts_operation: bool) -> dict:
        with self.tx():
            seq = self._next_seq("reservations")
            bid = f"b{seq:04d}"
            self.conn.execute("INSERT INTO reservations (id, seq, role, purpose, request_id, reserved_micro, state, liability_micro, price_version, counts_operation, created_utc) "
                              "VALUES (?, ?, ?, ?, ?, ?, 'held', 0, ?, ?, ?)",
                              (bid, seq, role, purpose, request_id, int(reserved_micro), price_version, int(bool(counts_operation)), self.now()))
            return self.reservation(bid)

    def reservation(self, bid: str) -> dict | None:
        r = _row(self.conn.execute("SELECT * FROM reservations WHERE id = ?", (bid,)))
        if r and r.get("usage_json"):
            r["usage"] = json.loads(r["usage_json"])
        return r

    def reservations(self, *, state: str | None = None) -> list[dict]:
        if state:
            rows = _rows(self.conn.execute("SELECT id FROM reservations WHERE state = ? ORDER BY seq", (state,)))
        else:
            rows = _rows(self.conn.execute("SELECT id FROM reservations ORDER BY seq"))
        return [self.reservation(r["id"]) for r in rows]

    def update_reservation(self, bid: str, **fields) -> None:
        allowed = {"state", "settled_micro", "liability_micro", "reason", "usage_json", "settled_utc", "request_id"}
        bad = set(fields) - allowed
        if bad:
            raise StateError(f"reservation fields {sorted(bad)} are not writable")
        if "state" in fields and fields["state"] not in RESERVATION_STATES:
            raise StateError(f"unknown reservation state {fields['state']!r}")
        with self.tx():
            if self.reservation(bid) is None:
                raise StateError(f"no reservation {bid}")
            for k, v in fields.items():
                if k.endswith("_json") and v is not None and not isinstance(v, str):
                    v = canonical_json(v).decode()
                self.conn.execute(f"UPDATE reservations SET {k} = ? WHERE id = ?", (v, bid))

    # ------------------------------------------------------------------ verdicts (append-only)
    def append_verdict(self, *, kind: str, revision_id: str | None, asset_sha256: str | None, verdict: str, bindings: dict, record: dict) -> dict:
        with self.tx():
            self.conn.execute("INSERT INTO verdicts (kind, revision_id, asset_sha256, verdict, bindings_json, record_json, created_utc) VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (kind, revision_id, asset_sha256, verdict, canonical_json(bindings).decode(), canonical_json(record).decode(), self.now()))
            return self.verdicts(kind=kind)[-1]

    def verdicts(self, *, kind: str | None = None) -> list[dict]:
        rows = _rows(self.conn.execute("SELECT * FROM verdicts WHERE (? IS NULL OR kind = ?) ORDER BY id", (kind, kind)))
        for r in rows:
            r["bindings"] = json.loads(r.pop("bindings_json"))
            r["record"] = json.loads(r.pop("record_json"))
        return rows

    # ------------------------------------------------------------------ the owner review loop
    def grant_allowance(self, *, add_micro: int, add_operations: int, authorized_by: str, reason: str, base_micro: int, base_operations: int,
                        open_reservations: dict | None = None, **extra) -> dict:
        """The one way a job's caps grow after creation: an owner's change round. The round's caps are what the job had
        committed at the grant (``base_micro``: settled + unknown liability + held; ``base_operations``: operations used)
        plus exactly ``add_micro`` / ``add_operations``, so the round can spend its grant and nothing more; any leftover of
        the caps before it is dropped (journaled as dropped_leftover_*). ``open_reservations`` ({reservation id: {"micro",
        "operation"}}: the held and unknown amounts inside ``base_micro``) is kept as setting 'round_open_reservations':
        when one of them later resolves lower, resolve_pre_grant lowers the caps by the difference, so an amount counted at
        the grant never becomes the round's money. One transaction, journaled as 'allowance_granted' with who authorized it
        and the caps before and after. update_job can never change a cap."""
        ints = (add_micro, add_operations, base_micro, base_operations)
        if any(isinstance(v, bool) or not isinstance(v, int) for v in ints):
            raise StateError("an allowance is integer micro-USD and an integer operation count")
        if add_micro < 0 or add_operations < 0 or (add_micro == 0 and add_operations == 0):
            raise StateError("an allowance only grows the caps, by a positive amount")
        if base_micro < 0 or base_operations < 0:
            raise StateError("the committed amounts a round starts from are never negative")
        if not str(authorized_by or "").strip():
            raise StateError("an allowance needs the owner's authorization text")
        with self.tx():
            j = self.job()
            before = {"cap_micro": int(j["cap_micro"]), "inference_operation_cap": int(j["inference_operation_cap"])}
            after = {"cap_micro": base_micro + add_micro, "inference_operation_cap": base_operations + add_operations}
            self.conn.execute("UPDATE job SET cap_micro = ?, inference_operation_cap = ?, updated_utc = ? WHERE id = 1",
                              (after["cap_micro"], after["inference_operation_cap"], self.now()))
            record = {"authorized_by": authorized_by, "reason": reason, "add_micro": add_micro, "add_operations": add_operations,
                      "before": before, "after": after, "committed_micro": base_micro, "committed_operations": base_operations,
                      "dropped_leftover_micro": max(before["cap_micro"] - base_micro, 0),
                      "dropped_leftover_operations": max(before["inference_operation_cap"] - base_operations, 0),
                      "open_reservations": dict(open_reservations or {}), **extra}
            self.set_setting("round_open_reservations", dict(open_reservations) if open_reservations else None)
            self.event("allowance_granted", **record)
            return record

    def resolve_pre_grant(self, reservation_id: str, *, counted_micro: int, counts_operation: bool) -> dict | None:
        """A reservation counted in the committed amounts of the current round's grant (setting 'round_open_reservations')
        resolved (settled, released, reconciled): when it now counts less than at the grant, the caps are lowered by the
        difference, so the round keeps exactly its grant (the verifier's 2 USD unknown reconciled as no charge gave a 1 USD
        round 3 USD). A resolution above the amount at the grant raises nothing: the round pays the excess. Inside the
        caller's transaction; journaled as 'allowance_pre_grant_resolved'. None when the reservation was not open at the grant."""
        with self.tx():
            open_ = dict(self.setting("round_open_reservations") or {})
            entry = open_.pop(reservation_id, None)
            if entry is None:
                return None
            lower_micro = max(int(entry["micro"]) - int(counted_micro), 0)
            lower_ops = 1 if entry.get("operation") and not counts_operation else 0
            if lower_micro or lower_ops:
                self.conn.execute("UPDATE job SET cap_micro = cap_micro - ?, inference_operation_cap = inference_operation_cap - ?, updated_utc = ? WHERE id = 1",
                                  (lower_micro, lower_ops, self.now()))
            self.set_setting("round_open_reservations", open_ or None)
            record = {"reservation": reservation_id, "counted_at_grant_micro": int(entry["micro"]), "counted_now_micro": int(counted_micro),
                      "cap_lowered_micro": lower_micro, "operation_cap_lowered": lower_ops}
            self.event("allowance_pre_grant_resolved", **record)
            return record

    def update_policy(self, changes: dict, *, reason: str) -> dict:
        """Change policy keys of an existing job (the owner review loop's per-round module locks and revision / wall limits),
        journaled as 'policy_changed' with the values before and after. Caps are not policy: grant_allowance."""
        if not isinstance(changes, dict) or not changes:
            raise StateError("a policy change names at least one key")
        if {"cap_micro", "budget_usd", "max_inference_requests", "driver", "worker", "allow_paid", "model"} & set(changes):
            raise StateError("caps, the driver, the worker, paid mode and the model are not changed through the policy")
        with self.tx():
            policy = dict(self.job()["policy"] or {})
            before = {k: policy.get(k) for k in changes}
            policy.update(changes)
            self.conn.execute("UPDATE job SET policy_json = ?, updated_utc = ? WHERE id = 1", (canonical_json(policy).decode(), self.now()))
            self.event("policy_changed", reason=reason, before=before, after=dict(changes))
            return policy

    def owner_rounds(self) -> list[dict]:
        """Every review round in order ([] for a job recorded before the table existed, opened read-only)."""
        if "owner_rounds" not in self.tables:
            return []
        rows = _rows(self.conn.execute("SELECT * FROM owner_rounds ORDER BY round"))
        for r in rows:
            r["candidate"] = json.loads(r.pop("candidate_json"))
            r["decision_record"] = json.loads(r.pop("decision_json")) if r.get("decision_json") else None
        return rows

    def open_owner_round(self) -> dict | None:
        """The round waiting for the owner's decision, or None."""
        rounds = [r for r in self.owner_rounds() if r["decision"] is None]
        return rounds[-1] if rounds else None

    def insert_owner_round(self, *, revision_id: str, asset_sha256: str | None, source: str, candidate: dict) -> dict:
        with self.tx():
            if self.open_owner_round() is not None:
                raise StateError("a review round is already open; the owner decides it before another candidate is presented")
            n = int(self.conn.execute("SELECT COALESCE(MAX(round), 0) + 1 FROM owner_rounds").fetchone()[0])
            self.conn.execute("INSERT INTO owner_rounds (round, revision_id, asset_sha256, source, candidate_json, opened_utc) VALUES (?, ?, ?, ?, ?, ?)",
                              (n, revision_id, asset_sha256, source, canonical_json(dict(candidate, round=n)).decode(), self.now()))
            self.event("owner_round_opened", round=n, revision=revision_id, asset_sha256=asset_sha256, source=source)
            return self.owner_rounds()[n - 1]

    def update_owner_round_candidate(self, n: int, candidate: dict) -> None:
        with self.tx():
            self.conn.execute("UPDATE owner_rounds SET candidate_json = ? WHERE round = ? AND decision IS NULL", (canonical_json(candidate).decode(), n))

    def decide_owner_round(self, n: int, decision: str, record: dict) -> dict:
        if decision not in ("accept", "changes", "stop", "continued_in_new_job", "cancelled"):
            raise StateError(f"unknown owner decision {decision!r}")
        with self.tx():
            row = _row(self.conn.execute("SELECT decision FROM owner_rounds WHERE round = ?", (n,)))
            if row is None:
                raise StateError(f"no review round {n}")
            if row["decision"] is not None:
                raise StateError(f"review round {n} was already decided ({row['decision']})")
            self.conn.execute("UPDATE owner_rounds SET decision = ?, decided_utc = ?, decision_json = ? WHERE round = ?",
                              (decision, self.now(), canonical_json(record).decode(), n))
            self.event("owner_decision", round=n, decision=decision)
            return next(r for r in self.owner_rounds() if r["round"] == n)


def runner_identity() -> str:
    return f"{os.getpid()}@{os.environ.get('COMPUTERNAME') or os.uname().nodename if hasattr(os, 'uname') else os.environ.get('COMPUTERNAME', 'host')}:{uuid.uuid4().hex[:8]}"
