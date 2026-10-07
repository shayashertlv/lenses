"""Isolation for every glasses3d/tests/agent test (plan/WP2_DESIGN.md section 14, criterion 1).

Each test runs with G3D_OFFLINE=1 and no OpenAI key or endpoint in the environment, under LoopbackOnly (any connect or
DNS lookup that is not 127.0.0.1 / ::1 / localhost is refused) and ProductionTransportTripwire (constructing a real
httpx/httpx2 network transport, i.e. a production client, is refused). Teardown asserts 0 non-loopback attempts and 0
production transports, so a test that swallowed the refusal still fails.
"""
import pytest

from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire

_SCRUBBED = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_ADMIN_KEY",
             "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


@pytest.fixture(autouse=True)
def offline_isolation(monkeypatch):
    monkeypatch.setenv("G3D_OFFLINE", "1")
    for name in _SCRUBBED:
        monkeypatch.delenv(name, raising=False)
    with LoopbackOnly() as network, ProductionTransportTripwire() as tripwire:
        yield network, tripwire
    assert network.attempts == [], f"non-loopback network attempts: {network.attempts}"
    assert tripwire.constructions == [], f"production transports constructed: {tripwire.constructions}"
