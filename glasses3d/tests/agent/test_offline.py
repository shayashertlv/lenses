"""The $0 isolation guards themselves (plan/WP2_DESIGN.md section 14, criterion 1): LoopbackOnly refuses and counts
every non-loopback connect or DNS lookup, ProductionTransportTripwire refuses and counts every real network transport
(a production client), offline clients need G3D_OFFLINE=1 and an sk-offline- key, and disable_sdk_tracing never
builds the Agents SDK's tracing exporter client (which the SDK's own set_tracing_disabled does)."""
import asyncio
import os
import socket

import httpx2
import pytest

from glasses3d.agent import offline as O
from glasses3d.agent.offline import (LoopbackOnly, NetworkRefused, OfflineRefused, ProductionClientRefused,
                                     ProductionTransportTripwire)
from glasses3d.tests.agent.support import OFFLINE_KEY


def test_every_test_runs_offline_without_a_key(offline_isolation):
    network, tripwire = offline_isolation
    assert os.environ["G3D_OFFLINE"] == "1" and "OPENAI_API_KEY" not in os.environ
    with pytest.raises(NetworkRefused):
        socket.getaddrinfo("api.openai.com", 443)
    assert network.attempts == [("getaddrinfo", "'api.openai.com'")]
    network.attempts.clear()            # the conftest teardown asserts zero; this one was the self-test


def test_loopback_only_refuses_and_counts_the_network():
    with LoopbackOnly() as guard:
        for call in (lambda: socket.getaddrinfo("api.openai.com", 443),
                     lambda: socket.gethostbyname("example.com"),
                     lambda: socket.create_connection(("example.com", 80), timeout=1)):
            with pytest.raises(NetworkRefused):
                call()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(NetworkRefused):
                sock.connect(("8.8.8.8", 443))
            with pytest.raises(NetworkRefused):
                sock.connect_ex(("1.1.1.1", 443))
        finally:
            sock.close()

        async def asynchronous():
            with pytest.raises(NetworkRefused):
                await asyncio.open_connection("api.openai.com", 443)
            with pytest.raises(NetworkRefused):
                await asyncio.open_connection("8.8.8.8", 443)     # a literal IP skips DNS: refused at connect
        asyncio.run(asynchronous())
        assert [kind for kind, _ in guard.attempts] == ["getaddrinfo", "gethostbyname", "create_connection",
                                                        "connect", "connect_ex", "getaddrinfo", "sock_connect"]


def test_loopback_only_allows_loopback_and_restores_exactly():
    outer_getaddrinfo, outer_connect = socket.getaddrinfo, socket.socket.connect
    with LoopbackOnly() as guard:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(8)                        # both connections wait unaccepted in the backlog
        port = server.getsockname()[1]
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=5):
                pass
            socket.getaddrinfo("localhost", port)

            async def asynchronous():
                writer = (await asyncio.open_connection("127.0.0.1", port))[1]
                writer.close()
            asyncio.run(asynchronous())
        finally:
            server.close()
        assert guard.attempts == []
    assert socket.getaddrinfo is outer_getaddrinfo and socket.socket.connect is outer_connect


def test_the_tripwire_refuses_production_clients_and_allows_mock_transports():
    from openai import AsyncOpenAI, OpenAI
    with ProductionTransportTripwire() as tripwire:
        with pytest.raises(ProductionClientRefused):
            AsyncOpenAI(api_key=OFFLINE_KEY, base_url="https://api.openai.com/v1")     # no http_client: production
        with pytest.raises(ProductionClientRefused):
            OpenAI(api_key=OFFLINE_KEY, base_url="https://api.openai.com/v1")
        with pytest.raises(ProductionClientRefused):
            httpx2.AsyncClient()
        assert len(tripwire.constructions) == 3
        mock = httpx2.AsyncClient(transport=httpx2.MockTransport(lambda request: httpx2.Response(200)))
        client = O.offline_inference_client(lambda request: httpx2.Response(200), api_key=OFFLINE_KEY)
        assert len(tripwire.constructions) == 3

        async def close():
            await mock.aclose()
            await client.close()
        asyncio.run(close())


def test_offline_clients_need_the_offline_switch_and_an_offline_key(monkeypatch):
    handler = lambda request: httpx2.Response(200)   # noqa: E731
    for key in ("sk-proj-real-looking", "", None, "SK-OFFLINE-upper", " sk-offline-x"):
        with pytest.raises(OfflineRefused):
            O.offline_counting_client(handler, api_key=key)
    for value in ("0", "true", ""):
        monkeypatch.setenv("G3D_OFFLINE", value)
        with pytest.raises(OfflineRefused):
            O.offline_inference_client(handler, api_key=OFFLINE_KEY)
    monkeypatch.delenv("G3D_OFFLINE")
    with pytest.raises(OfflineRefused):
        O.require_offline(OFFLINE_KEY)
    monkeypatch.setenv("G3D_OFFLINE", "1")
    O.require_offline(OFFLINE_KEY)
    client = O.offline_inference_client(handler, api_key=OFFLINE_KEY)
    assert str(client.base_url) == "https://api.openai.com/v1/" and client.max_retries == 0
    asyncio.run(client.close())


def test_the_sdks_own_set_tracing_disabled_builds_a_network_client_and_ours_does_not(monkeypatch):
    pytest.importorskip("agents")
    from agents import set_tracing_disabled
    from agents.tracing import processors, setup
    from agents.tracing.provider import DefaultTraceProvider
    for name, owner in (("GLOBAL_TRACE_PROVIDER", setup), ("_global_exporter", processors),
                        ("_global_processor", processors)):
        monkeypatch.setattr(owner, name, None)
    with ProductionTransportTripwire() as tripwire:
        with pytest.raises(ProductionClientRefused):
            set_tracing_disabled(True)          # openai-agents 0.22.3: BackendSpanExporter -> httpx2.Client()
        assert len(tripwire.constructions) == 1 and tripwire.constructions[0].endswith("HTTPTransport")
    monkeypatch.setattr(setup, "GLOBAL_TRACE_PROVIDER", None)
    with ProductionTransportTripwire() as tripwire:
        O.disable_sdk_tracing()
        assert tripwire.constructions == []
    provider = setup.GLOBAL_TRACE_PROVIDER
    assert isinstance(provider, DefaultTraceProvider) and provider._disabled
    assert provider._multi_processor._processors == ()

    class Probe:                                # an already-built provider loses its processors
        def on_trace_start(self, trace): pass
        def on_trace_end(self, trace): pass
        def on_span_start(self, span): pass
        def on_span_end(self, span): pass
        def shutdown(self): pass
        def force_flush(self): pass

    provider.register_processor(Probe())
    O.disable_sdk_tracing()
    assert provider._multi_processor._processors == () and provider._disabled
