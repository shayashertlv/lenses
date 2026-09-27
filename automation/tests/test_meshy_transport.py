"""Offline protocol/security tests. No socket is allowed to reach a provider."""
import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from reconstruction.initializer import MESHY_ENDPOINT, MESHY_DEFAULT_SETTINGS, MESHY_SPLIT_ENDPOINT, MeshyBackend
from reconstruction.meshy_transport import (BoundedMeshyTransport, ExpiredDownloadError,
    HTTPStatusError, LocalCancellation, TransportError, TransportPolicyError,
    _PinnedHTTPSConnection, estimated_credits)


def address(ip="8.8.8.8"):
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))


class Response:
    def __init__(self, body=b"", status=200, headers=(), read_hook=None):
        self.status, self.headers, self.body = status, list(headers), io.BytesIO(body)
        self.read_hook, self.closed = read_hook, False

    def getheaders(self):
        return self.headers

    def read1(self, count):
        if self.read_hook:
            self.read_hook()
        return self.body.read(count)

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, response, record):
        self.response, self.record, self.sock = response, record, None

    def request(self, method, target, *, body, headers):
        self.record.update(method=method, target=target, body=body, headers=headers)
        if isinstance(self.response, Exception):
            raise self.response

    def getresponse(self):
        return self.response

    def close(self):
        self.record["closed"] = True


class Network:
    def __init__(self, responses, records=None):
        self.responses = list(responses)
        self.connections, self.lookups = [], []
        self.records = records or {}

    def resolve(self, host, port, *, type):
        self.lookups.append((host, port, type))
        return self.records.get(host, [address()])

    def connect(self, host, resolved, timeout):
        record = {"host": host, "address": resolved, "timeout": timeout}
        self.connections.append(record)
        return Connection(self.responses.pop(0), record)


def payload():
    return {**deepcopy(MESHY_DEFAULT_SETTINGS),
            "image_urls": ["data:image/png;base64," + base64.b64encode(b"mock image").decode()]}


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name) / "transport"
        self.secret = "TEST_ONLY_NEVER_A_REAL_KEY"

    def transport(self, network, **options):
        return BoundedMeshyTransport(self.secret, receipt_dir=self.folder, resolver=network.resolve,
                                     connection_factory=network.connect, **options)

    def test_pricing_estimate_is_explicit_not_hard_spend_cap(self):
        self.assertEqual(estimated_credits({}), 30)
        self.assertEqual(estimated_credits({"should_texture": False}), 20)
        self.assertEqual(estimated_credits({"texture_resolution": "8k", "geometry_resolution": "2k"}), 40)
        net = Network([])
        for options in ({}, {"max_new_tasks": 1, "max_estimated_credits": 29}):
            with self.assertRaises(TransportPolicyError):
                self.transport(net, **options).post_json(MESHY_ENDPOINT, payload())
        self.assertFalse(self.folder.exists())
        self.assertEqual(net.connections, [])
        allowed = self.transport(net, max_new_tasks=1, max_estimated_credits=30).check_submission({})
        self.assertFalse(allowed["provider_hard_spend_ceiling"])
        self.assertIn("docs.meshy.ai", allowed["pricing_source"])
        with self.assertRaises(TransportPolicyError):
            self.transport(net, max_new_tasks=2)

    def test_one_post_auth_only_api_and_exact_payload_evidence(self):
        net = Network([Response(b'{"result":"task-1"}')])
        transport = self.transport(net, max_new_tasks=1, max_estimated_credits=30)
        self.assertEqual(transport.post_json(MESHY_ENDPOINT, payload()), {"result": "task-1"})
        call = net.connections[0]
        self.assertEqual(call["headers"]["Authorization"], "Bearer " + self.secret)
        self.assertEqual(call["address"], address())
        receipt = json.loads((self.folder / "submission.json").read_bytes())
        self.assertEqual(receipt["payload_sha256"], hashlib.sha256(call["body"]).hexdigest())
        self.assertEqual(receipt["estimated_credits"], 30)
        self.assertNotIn("image_urls", receipt)
        for client in (transport, self.transport(net, max_new_tasks=1, max_estimated_credits=30)):
            with self.assertRaises(TransportPolicyError):
                client.post_json(MESHY_ENDPOINT, payload())
        self.assertEqual(len(net.connections), 1)
        self.assertTrue(all(self.secret.encode() not in p.read_bytes() for p in self.folder.rglob('*') if p.is_file()))

    def test_split_post_is_a_separately_estimated_task_on_the_split_path(self):
        split = {"mode": "by_parts", "layout": "assembled", "target_formats": ["glb"],
                 "input_task_id": "task-1", "prompt": "frame front, left lens, right lens"}
        self.assertEqual(estimated_credits(split), 10)
        net = Network([Response(b'{"result":"split-1"}')])
        transport = self.transport(net, max_new_tasks=1, max_estimated_credits=10)
        self.assertEqual(transport.post_json(MESHY_SPLIT_ENDPOINT, split), {"result": "split-1"})
        call = net.connections[0]
        self.assertEqual(call["target"], "/openapi/v1/print/split")
        self.assertEqual(json.loads(call["body"]), split)
        receipt = json.loads((self.folder / "submission.json").read_bytes())
        self.assertEqual(receipt["estimated_credits"], 10)
        self.assertEqual(receipt["image_count"], 0)
        with self.assertRaises(TransportPolicyError):
            transport.post_json(MESHY_SPLIT_ENDPOINT, split)
        self.folder = Path(self.temporary.name) / "split-2"
        for bad, endpoint in (({**split, "prompt": ""}, MESHY_SPLIT_ENDPOINT), ({**split, "layout": "exploded"}, MESHY_SPLIT_ENDPOINT),
                              ({**split, "input_task_id": "../x"}, MESHY_SPLIT_ENDPOINT), ({**split, "extra": 1}, MESHY_SPLIT_ENDPOINT),
                              (split, MESHY_ENDPOINT), (payload(), MESHY_SPLIT_ENDPOINT)):
            with self.subTest(bad=bad, endpoint=endpoint), self.assertRaises(TransportPolicyError):
                self.transport(Network([]), max_new_tasks=1, max_estimated_credits=30).post_json(endpoint, bad)
        self.assertEqual(len(net.connections), 1)
        with self.assertRaises(TransportPolicyError):
            self.transport(Network([]), max_new_tasks=1, max_estimated_credits=9).post_json(MESHY_SPLIT_ENDPOINT, split)
        client = self.transport(Network([Response(b'{"id":"split-1","status":"SUCCEEDED"}')]))
        self.assertEqual(client.get_json(MESHY_SPLIT_ENDPOINT + "/split-1")["id"], "split-1")
        with self.assertRaises(TransportPolicyError):
            client.get_json(MESHY_SPLIT_ENDPOINT)

    def test_uncertain_post_never_retried_after_failure_or_redirect(self):
        for response in (TimeoutError(self.secret), Response(status=307, headers=[("Location", "https://evil.example/path")])):
            with self.subTest(response=response):
                self.folder = Path(self.temporary.name) / ("timeout" if isinstance(response, Exception) else "redirect")
                net = Network([response])
                client = self.transport(net, max_new_tasks=1, max_estimated_credits=30)
                with self.assertRaises((TimeoutError, HTTPStatusError)) as raised:
                    client.post_json(MESHY_ENDPOINT, payload())
                self.assertNotIn(self.secret, str(raised.exception))
                self.assertTrue((self.folder / "submission.json").is_file())
                with self.assertRaises(TransportPolicyError):
                    self.transport(net, max_new_tasks=1, max_estimated_credits=30).post_json(MESHY_ENDPOINT, payload())
                self.assertEqual(len(net.connections), 1)

    def test_authenticated_host_paths_and_redirects_are_restricted(self):
        net = Network([])
        client = self.transport(net)
        for url in ("https://api.meshy.ai.evil.example/openapi/v1/multi-image-to-3d/task",
                    "https://api.meshy.ai/openapi/v1/balance", MESHY_ENDPOINT + "/task?other=1",
                    MESHY_ENDPOINT + "/task#fragment", MESHY_ENDPOINT + "/%2e%2e"):
            with self.subTest(url=url), self.assertRaises(TransportPolicyError):
                client.get_json(url)
        self.assertEqual(net.connections, [])
        net.responses = [Response(status=302, headers=[("Location", "https://assets.meshy.ai/model.glb")])]
        with self.assertRaises(HTTPStatusError):
            client.get_json(MESHY_ENDPOINT + "/task")
        self.assertEqual(len(net.connections), 1)

    def test_download_has_no_auth_at_any_redirect_and_each_address_is_validated(self):
        net = Network([Response(status=302, headers=[("Location", "https://cdn.example/model.glb")]), Response(b"glb")])
        client = self.transport(net)
        self.assertEqual(client.download_public("https://assets.meshy.ai/start", max_bytes=50), b"glb")
        self.assertEqual([call[0] for call in net.lookups], ["assets.meshy.ai", "cdn.example"])
        self.assertTrue(all("Authorization" not in call["headers"] for call in net.connections))
        self.assertTrue(all(call["address"] == address() for call in net.connections))

    def test_private_literal_dns_mixed_and_redirect_targets_rejected(self):
        net = Network([], {"private.example": [address("127.0.0.1")],
                           "mixed.example": [address(), address("10.2.3.4")]})
        client = self.transport(net)
        for url in ("https://127.0.0.1/a", "https://[::ffff:127.0.0.1]/a", "https://169.254.169.254/a",
                    "https://private.example/a", "https://mixed.example/a", "https://public.example:444/a",
                    "https://user:password@public.example/a", "https://localhost/a", "http://public.example/a"):
            with self.subTest(url=url), self.assertRaises(TransportPolicyError):
                client.download_public(url, max_bytes=50)
        self.assertEqual(net.connections, [])
        net.responses = [Response(status=302, headers=[("Location", "https://private.example/a")])]
        with self.assertRaises(TransportPolicyError):
            client.download_public("https://public.example/a", max_bytes=50)
        self.assertEqual(len(net.connections), 1)

    def test_stream_and_content_length_limits_and_truncation(self):
        for response, exception in ((Response(b"123456"), TransportPolicyError),
                                    (Response(b"", headers=[("Content-Length", "6")]), TransportPolicyError),
                                    (Response(b"12", headers=[("Content-Length", "5")]), TransportError),
                                    (Response(b"12", headers=[("Content-Encoding", "gzip")]), TransportPolicyError)):
            with self.subTest(response=response), self.assertRaises(exception):
                self.transport(Network([response])).download_public("https://public.example/a", max_bytes=5)
            self.assertTrue(response.closed)

    def test_expiry_requires_specific_status_and_body_not_generic_forbidden(self):
        for status, body, expected in ((403, b"<Code>ExpiredToken</Code>", ExpiredDownloadError),
                                       (401, b"Request has expired", ExpiredDownloadError),
                                       (403, b"AccessDenied", HTTPStatusError),
                                       (500, b"Request has expired", HTTPStatusError)):
            with self.subTest(status=status, body=body), self.assertRaises(expected) as raised:
                self.transport(Network([Response(body, status)])).download_public("https://public.example/a", max_bytes=100)
            self.assertEqual(type(raised.exception), expected)

    def test_pending_get_zero_new_tasks_preserves_raw_progress_credits_without_key(self):
        raw = json.dumps({"id": "task", "status": "IN_PROGRESS", "progress": 48, "consumed_credits": 30,
                          "echo": self.secret}).encode()
        client = self.transport(Network([Response(raw, headers=[("X-Request-Id", self.secret)])]))
        task = MeshyBackend(client).retrieve("task")
        self.assertEqual((task["status"], task["progress"], task["consumed_credits"]), ("pending", 48, 30))
        self.assertEqual(task["provider_response"]["echo"], "[REDACTED_API_KEY]")
        self.assertEqual(task["transport_receipt"], client.last_receipt)
        self.assertFalse((self.folder / "submission.json").exists())
        self.assertTrue(all(self.secret.encode() not in p.read_bytes() for p in self.folder.rglob('*') if p.is_file()))
        metadata = json.loads(next(self.folder.glob("http-*/response.json")).read_bytes())
        self.assertEqual(metadata["raw_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertTrue(metadata["secret_redacted"])

    def test_unicode_escaped_credential_echo_is_redacted_after_json_decoding(self):
        escaped = "".join("\\u%04x" % ord(c) for c in self.secret)
        raw = ('{"message":"' + escaped + '"}').encode()
        client = self.transport(Network([Response(raw)]))
        self.assertEqual(client.get_json(MESHY_ENDPOINT + "/task"), {"message": "[REDACTED_API_KEY]"})
        saved = next(self.folder.glob("http-*/response.bin")).read_bytes()
        self.assertEqual(json.loads(saved)["message"], "[REDACTED_API_KEY]")
        self.assertNotIn(escaped.encode(), saved)

    def test_dns_wait_is_bounded_and_cancelled_before_a_connection(self):
        released = threading.Event()
        net = Network([])
        client = self.transport(net, request_timeout=.06)
        client._resolver = lambda *args, **kwargs: released.wait(2)
        try:
            with self.assertRaises(TimeoutError):
                client.get_json(MESHY_ENDPOINT + "/task")
        finally:
            released.set()
        self.assertEqual(net.connections, [])

    def test_redirect_count_is_bounded_without_auth(self):
        net = Network([Response(status=302, headers=[("Location", "/next")]) for _ in range(6)])
        with self.assertRaises(TransportPolicyError):
            self.transport(net).download_public("https://public.example/start", max_bytes=50)
        self.assertEqual(len(net.connections), 6)
        self.assertTrue(all("Authorization" not in row["headers"] for row in net.connections))

    def test_deadline_interrupts_a_blocked_response_even_after_connection_releases_socket(self):
        released = threading.Event()
        class Socket:
            def shutdown(self, how): released.set()
            def settimeout(self, timeout): pass
        class Blocked(Response):
            def read1(self, size):
                if not released.wait(.7):
                    raise AssertionError("watchdog did not interrupt the blocking read")
                return b""
        class ReleasedConnection(Connection):
            def __init__(self, response, record):
                super().__init__(response, record)
                self.sock = Socket()
            def getresponse(self):
                self.sock = None  # HTTPConnection releases Connection: close sockets
                return self.response
        client = self.transport(Network([]), download_timeout=.06)
        client._connection_factory = lambda *args: ReleasedConnection(Blocked(), {})
        with self.assertRaises(TimeoutError):
            client.download_public("https://public.example/a", max_bytes=50)
        self.assertTrue(released.is_set())

    def test_cancel_watcher_interrupts_socket_published_after_first_close(self):
        cancelled, first_close, interrupted = threading.Event(), threading.Event(), threading.Event()
        class LateSocket:
            def shutdown(self, how): interrupted.set()
        class LateConnection(Connection):
            def request(self, method, target, *, body, headers):
                cancelled.set()
                if not first_close.wait(.5):
                    raise AssertionError("first cancellation close did not happen")
                # Simulates a connect/TLS transition publishing a new socket
                # after the watchdog saw None and tried its first close.
                self.sock = LateSocket()
                if not interrupted.wait(.5):
                    raise AssertionError("replacement socket escaped cancellation")
                raise OSError("cancelled socket")
            def close(self): first_close.set()
        client = self.transport(Network([]), cancel_event=cancelled)
        client._connection_factory = lambda *args: LateConnection(Response(), {})
        with self.assertRaises(LocalCancellation):
            client.get_json(MESHY_ENDPOINT + "/task")
        self.assertTrue(interrupted.is_set())

    def test_absolute_deadline_and_local_cancellation_stop_stream_no_delete(self):
        clock = [0.0]
        def advance():
            clock[0] += 2
        net = Network([Response(b"stream", read_hook=advance)])
        client = self.transport(net, download_timeout=1, clock=lambda: clock[0])
        with self.assertRaises(TimeoutError):
            client.download_public("https://public.example/a", max_bytes=50)
        event = threading.Event()
        net = Network([Response(b"stream", read_hook=event.set)])
        client = self.transport(net, cancel_event=event)
        with self.assertRaises(LocalCancellation):
            client.download_public("https://public.example/a", max_bytes=50)
        self.assertEqual([r["method"] for r in net.connections], ["GET"])
        with self.assertRaises(LocalCancellation):
            client.get_json(MESHY_ENDPOINT + "/task")
        self.assertEqual(len(net.connections), 1)

    def test_pinned_connection_never_resolves_hostname_again_and_uses_tls_sni(self):
        class Socket:
            def __init__(self):
                self.connected = None
            def settimeout(self, timeout): pass
            def connect(self, destination): self.connected = destination
            def getpeername(self): return ("8.8.8.8", 443)
            def close(self): pass
            def do_handshake(self): self.handshake = True
        raw = Socket()
        context = unittest.mock.Mock()
        context.wrap_socket.return_value = raw
        with patch("reconstruction.meshy_transport.ssl.create_default_context", return_value=context), \
             patch("reconstruction.meshy_transport.socket.socket", return_value=raw), \
             patch("reconstruction.meshy_transport.socket.getaddrinfo", side_effect=AssertionError("second DNS lookup")):
            connection = _PinnedHTTPSConnection("assets.meshy.ai", address(), 5)
            connection.connect()
        self.assertEqual(raw.connected, ("8.8.8.8", 443))
        context.wrap_socket.assert_called_once_with(raw, server_hostname="assets.meshy.ai", do_handshake_on_connect=False)
        self.assertTrue(raw.handshake)


if __name__ == "__main__":
    unittest.main()
