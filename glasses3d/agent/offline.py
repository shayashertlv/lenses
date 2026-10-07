"""Offline OpenAI clients and the $0 isolation guards (plan/WP2_DESIGN.md sections 7 and 14).

The offline clients are the production classes (``AsyncOpenAI`` over openai's ``DefaultAsyncHttpxClient``, an httpx2
client) with only the transport replaced by ``httpx2.MockTransport``: BudgetGuard, the event hooks and the SDK run as
in a paid run, and nothing can reach a socket. They are refused unless ``G3D_OFFLINE=1`` and the key starts with
``sk-offline-``.

``LoopbackOnly`` patches socket connect and name resolution (sync sockets, ``socket.create_connection``,
``getaddrinfo``/``gethostbyname`` and both asyncio event loops' ``sock_connect``) so that anything but 127.0.0.1, ::1
or localhost is refused and counted. ``ProductionTransportTripwire`` refuses and counts the construction of a real
httpx/httpx2 network transport, which is what a production client builds; a client over MockTransport builds none.
"""
from __future__ import annotations

import asyncio.proactor_events
import asyncio.selector_events
import os
import socket

OFFLINE_ENV = "G3D_OFFLINE"
OFFLINE_KEY_PREFIX = "sk-offline-"
BASE_URL = "https://api.openai.com/v1"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class OfflineRefused(RuntimeError):
    """An offline client was requested outside the offline proof conditions."""


class NetworkRefused(ConnectionRefusedError):
    """A non-loopback connection or name lookup was attempted under LoopbackOnly."""


class ProductionClientRefused(AssertionError):
    """A real network transport (a production client) was constructed under the tripwire."""


def require_offline(api_key) -> None:
    if os.environ.get(OFFLINE_ENV) != "1":
        raise OfflineRefused(f"offline clients need {OFFLINE_ENV}=1")
    if not isinstance(api_key, str) or not api_key.startswith(OFFLINE_KEY_PREFIX):
        raise OfflineRefused(f"offline clients need a key starting with {OFFLINE_KEY_PREFIX!r}")


def offline_counting_client(handler, *, api_key: str, timeout: float = 60.0):
    """The token-counting client (no hooks) over MockTransport(handler)."""
    require_offline(api_key)
    import httpx2
    from openai import AsyncOpenAI, DefaultAsyncHttpxClient
    return AsyncOpenAI(api_key=api_key, base_url=BASE_URL, max_retries=0, timeout=timeout,
                       http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))


def offline_inference_client(handler, *, api_key: str, event_hooks: dict | None = None, timeout: float = 900.0):
    """The inference client over MockTransport(handler) with the given httpx2 event hooks (no redirects)."""
    require_offline(api_key)
    import httpx2
    from openai import AsyncOpenAI, DefaultAsyncHttpxClient
    return AsyncOpenAI(api_key=api_key, base_url=BASE_URL, max_retries=0, timeout=timeout,
                       http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler),
                                                           event_hooks=event_hooks or {}, follow_redirects=False))


def disable_sdk_tracing() -> None:
    """Disable Agents SDK tracing without ever building its exporter (call this instead of set_tracing_disabled).

    openai-agents 0.22.3 ``set_tracing_disabled(True)`` first initializes the default trace provider, whose
    ``BackendSpanExporter`` constructs a real ``httpx2.Client`` for https://api.openai.com/v1/traces/ingest
    (agents/tracing/processors.py:90, setup.py:59). Installing a processor-less provider first means no tracing
    client exists in the process; an already-built provider loses its processors."""
    from agents import set_tracing_disabled
    from agents.tracing import set_trace_provider, setup
    from agents.tracing.provider import DefaultTraceProvider

    provider = setup.GLOBAL_TRACE_PROVIDER
    if provider is None:
        set_trace_provider(DefaultTraceProvider())
    else:
        provider.set_processors([])
    set_tracing_disabled(True)


def is_loopback(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    return host.strip("[]").split("%", 1)[0].lower() in LOOPBACK_HOSTS


def _address_host(address):
    if isinstance(address, tuple) and address:
        return address[0]
    return address


class LoopbackOnly:
    """Refuse (NetworkRefused) and record every non-loopback connect or DNS lookup while active."""

    def __init__(self):
        self.attempts: list[tuple[str, str]] = []
        self._saved: list[tuple[object, str, bool, object]] = []

    def _refuse(self, kind: str, host) -> None:
        self.attempts.append((kind, repr(host)[:200]))
        raise NetworkRefused(f"network access refused under LoopbackOnly: {kind} {host!r}")

    def _patch(self, owner, name: str, replacement) -> None:
        own = name in vars(owner)
        self._saved.append((owner, name, own, vars(owner)[name] if own else None))
        setattr(owner, name, replacement)

    def __enter__(self) -> "LoopbackOnly":
        guard = self
        getaddrinfo, gethostbyname, gethostbyname_ex = socket.getaddrinfo, socket.gethostbyname, socket.gethostbyname_ex
        create_connection = socket.create_connection
        connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
        selector_connect = asyncio.selector_events.BaseSelectorEventLoop.sock_connect
        proactor_connect = asyncio.proactor_events.BaseProactorEventLoop.sock_connect

        def checked(kind, host):
            if host is not None and not is_loopback(host):
                guard._refuse(kind, host)

        def network_socket(sock) -> bool:
            return getattr(sock, "family", None) in (socket.AF_INET, socket.AF_INET6)

        def patched_getaddrinfo(host, *args, **kwargs):
            checked("getaddrinfo", host)
            return getaddrinfo(host, *args, **kwargs)

        def patched_gethostbyname(host):
            checked("gethostbyname", host)
            return gethostbyname(host)

        def patched_gethostbyname_ex(host):
            checked("gethostbyname_ex", host)
            return gethostbyname_ex(host)

        def patched_create_connection(address, *args, **kwargs):
            checked("create_connection", _address_host(address))
            return create_connection(address, *args, **kwargs)

        def patched_connect(sock, address):
            if network_socket(sock):
                checked("connect", _address_host(address))
            return connect(sock, address)

        def patched_connect_ex(sock, address):
            if network_socket(sock):
                checked("connect_ex", _address_host(address))
            return connect_ex(sock, address)

        async def patched_selector_connect(loop, sock, address):
            if network_socket(sock):
                checked("sock_connect", _address_host(address))
            return await selector_connect(loop, sock, address)

        async def patched_proactor_connect(loop, sock, address):
            if network_socket(sock):
                checked("sock_connect", _address_host(address))
            return await proactor_connect(loop, sock, address)

        self._patch(socket, "getaddrinfo", patched_getaddrinfo)
        self._patch(socket, "gethostbyname", patched_gethostbyname)
        self._patch(socket, "gethostbyname_ex", patched_gethostbyname_ex)
        self._patch(socket, "create_connection", patched_create_connection)
        self._patch(socket.socket, "connect", patched_connect)
        self._patch(socket.socket, "connect_ex", patched_connect_ex)
        self._patch(asyncio.selector_events.BaseSelectorEventLoop, "sock_connect", patched_selector_connect)
        self._patch(asyncio.proactor_events.BaseProactorEventLoop, "sock_connect", patched_proactor_connect)
        return self

    def __exit__(self, *exc) -> None:
        while self._saved:
            owner, name, own, original = self._saved.pop()
            if own:
                setattr(owner, name, original)
            else:
                delattr(owner, name)    # it was inherited: remove the shadow, never leave a copy behind


class ProductionTransportTripwire:
    """Refuse (ProductionClientRefused) and record the construction of a real httpx/httpx2 network transport."""

    def __init__(self):
        self.constructions: list[str] = []
        self._saved: list[tuple[type, object]] = []

    def __enter__(self) -> "ProductionTransportTripwire":
        import httpx2
        targets = [httpx2.AsyncHTTPTransport, httpx2.HTTPTransport]
        try:
            import httpx
            targets += [httpx.AsyncHTTPTransport, httpx.HTTPTransport]
        except ImportError:  # pragma: no cover
            pass
        for cls in targets:
            self._saved.append((cls, cls.__dict__["__init__"]))
            cls.__init__ = self._refusing(cls)
        return self

    def _refusing(self, cls):
        tripwire = self
        name = f"{cls.__module__}.{cls.__qualname__}"

        def __init__(transport, *args, **kwargs):
            tripwire.constructions.append(name)
            raise ProductionClientRefused(f"a real network transport ({name}) was constructed: a production client")
        return __init__

    def __exit__(self, *exc) -> None:
        while self._saved:
            cls, original = self._saved.pop()
            cls.__init__ = original
