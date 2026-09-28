"""One durable Astra author conversation per glasses product (``python -m modeler.agentic``).

The legacy loop (``modeler.job``) sends a fresh one-decision package every turn and forgets the response; this
package keeps the complete Responses conversation (every output item, every ``function_call_output``), reserves
every dollar before a request is sent, runs the author's Blender programs in a bounded worker, tracks which images
the author has actually received, and delivers only bytes it has verified. Design and CLI: README.md here.

Module ownership: ``pricing`` (frozen tariff and integer money), ``state`` (SQLite schema, transactions, lease),
``artifacts`` (immutable content-addressed files), ``budget`` (shared reservations across every paid role),
``responses`` (exact request/response capture, transports, parsing), ``tools`` (strict registry and adapters to
the reviewed modeler functions), ``executor`` (worker protocol: fake, native fixed fixture, Docker), ``evaluation``
(sealed evidence, critic, final evaluator, byte-bound delivery), ``config`` (request translation, the validated policy,
exit codes), ``runner`` (the state machine), ``review`` (the owner review loop's pieces), ``rebuild`` (the no-inference
rebuild of a sealed program), ``demo`` (the offline scripted scenario), ``selftest`` (the worker self-test), ``cli``.
"""
from __future__ import annotations

SCHEMA_VERSION = 1
PACKAGE_PROTOCOL = "modeler_agentic_v1"
