"""Public AR boundary checks with temporary bundles; no camera, GPU or API calls."""

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from UI.ar_testing import PublicARSite


class _Output(io.BytesIO):
    largest_write = 0

    def write(self, data):
        self.largest_write = max(self.largest_write, len(data))
        return super().write(data)


class _Request:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.response_headers = {}
        self.wfile = _Output()
        self.status = None

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers[name] = value

    def end_headers(self):
        pass


class TestPublicARSite(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lenses-ar-route-")
        self.root = Path(self.temp.name)
        self.site = PublicARSite(self.root)
        self.contents = {
            "experiments/efficiency-lab/live.html": b"<!doctype html><title>AR comparison</title>",
            "assets/live-abcdefgh.js": b"export const test = true;",
            "assets/worker-abcdefgh.js": b"self.onmessage = () => {};",
            "assets/vision.wasm": b"\0asm\x01\0\0\0",
            "assets/view.css": b"canvas { width:100%; }",
            "models/manifest.json": b'{"models":[]}',
            "models/face.task": b"model" * 100001,
            "models/hair/model.tflite": b"tflite",
            "models/glasses.glb": b"glTF",
            "assets/font.woff2": b"font",
        }
        self.manifest = {"schemaVersion": 1, "files": []}
        for name, data in self.contents.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            self.manifest["files"].append({
                "path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            })
        self.write_manifest()

    def tearDown(self):
        # TemporaryDirectory is the one exact directory allocated by this test.
        self.assertEqual(self.root.resolve().parent, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(self.root.name.startswith("lenses-ar-route-"))
        self.temp.cleanup()

    def write_manifest(self):
        (self.root / "public-manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def request(self, path, *, head=False, headers=None):
        request = _Request(headers)
        self.assertTrue(self.site.serve(request, path, head=head))
        return request

    def test_public_entry_redirects_to_the_review_comparison(self):
        for path in ("/ar_testing", "/ar_testing/", "/ar_testing/?anything=ignored"):
            for head in (False, True):
                with self.subTest(path=path, head=head):
                    request = self.request(path, head=head)
                    self.assertEqual(request.status, 302)
                    self.assertEqual(request.response_headers["Location"],
                                     "/ar_testing/experiments/efficiency-lab/live.html?study=review")
                    self.assertEqual(request.wfile.getvalue(), b"")

    def test_get_and_head_serve_matching_metadata_and_correct_runtime_types(self):
        expected = {
            ".html": "text/html", ".js": "application/javascript", ".wasm": "application/wasm",
            ".css": "text/css", ".json": "application/json", ".task": "application/octet-stream",
            ".tflite": "application/octet-stream", ".glb": "model/gltf-binary", ".woff2": "font/woff2",
        }
        for path, content in self.contents.items():
            with self.subTest(path=path):
                get = self.request("/ar_testing/" + path + "?v=123")
                head = self.request("/ar_testing/" + path, head=True)
                self.assertEqual(get.status, 200)
                self.assertEqual(head.status, 200)
                self.assertEqual(get.response_headers, head.response_headers)
                self.assertEqual(get.wfile.getvalue(), content)
                self.assertEqual(head.wfile.getvalue(), b"")
                self.assertEqual(int(get.response_headers["Content-Length"]), len(content))
                self.assertEqual(get.response_headers["Content-Type"].split(";")[0],
                                 expected[Path(path).suffix])
                self.assertEqual(get.response_headers["Cross-Origin-Opener-Policy"], "same-origin")
                self.assertEqual(get.response_headers["Cross-Origin-Embedder-Policy"], "require-corp")
                self.assertEqual(get.response_headers["X-Content-Type-Options"], "nosniff")

    def test_large_models_are_streamed_in_bounded_chunks(self):
        request = self.request("/ar_testing/models/face.task")
        self.assertEqual(request.wfile.getvalue(), self.contents["models/face.task"])
        self.assertLessEqual(request.wfile.largest_write, 128 * 1024)

    def assert_local_policy(self, request):
        policy = request.response_headers["Content-Security-Policy"]
        directives = {parts[0]: parts[1:] for value in policy.split(";") if (parts := value.split())}
        self.assertEqual(directives, {
            "default-src": ["'none'"], "script-src": ["'self'", "'wasm-unsafe-eval'"],
            "worker-src": ["'self'", "blob:"], "connect-src": ["'self'", "blob:"],
            "img-src": ["'self'", "blob:", "data:"], "media-src": ["'self'", "blob:"],
            "style-src": ["'self'", "'unsafe-inline'"], "font-src": ["'self'"],
            "object-src": ["'none'"], "base-uri": ["'none'"], "form-action": ["'none'"],
            "frame-ancestors": ["'none'"],
        })
        self.assertNotIn("Content-Security-Policy-Report-Only", request.response_headers)
        self.assertNotIn("Reporting-Endpoints", request.response_headers)

    def test_enforcing_local_policy_reaches_documents_workers_wasm_head_and_cache_hits(self):
        for path in ("/ar_testing/", "/ar_testing/experiments/efficiency-lab/live.html",
                     "/ar_testing/assets/worker-abcdefgh.js", "/ar_testing/assets/vision.wasm"):
            for head in (False, True):
                with self.subTest(path=path, head=head):
                    request = self.request(path, head=head)
                    self.assert_local_policy(request)
                    if request.status == 200:
                        cached = self.request(path, head=head, headers={"If-None-Match": request.response_headers["ETag"]})
                        self.assertEqual(cached.status, 304)
                        self.assert_local_policy(cached)
                        self.assertEqual(cached.wfile.getvalue(), b"")

    def test_ar_error_responses_keep_the_same_enforcing_policy(self):
        for head in (False, True):
            missing = self.request("/ar_testing/unlisted.js", head=head)
            self.assertEqual(missing.status, 404)
            self.assert_local_policy(missing)
        (self.root / "assets/worker-abcdefgh.js").write_bytes(b"corrupt")
        for head in (False, True):
            corrupt = self.request("/ar_testing/assets/worker-abcdefgh.js", head=head)
            self.assertEqual(corrupt.status, 503)
            self.assert_local_policy(corrupt)
        (self.root / "public-manifest.json").unlink()
        for head in (False, True):
            unavailable = self.request("/ar_testing/", head=head)
            self.assertEqual(unavailable.status, 503)
            self.assert_local_policy(unavailable)

    def test_entry_and_model_manifests_revalidate_while_hashed_assets_are_immutable(self):
        for path in ("experiments/efficiency-lab/live.html", "models/manifest.json", "models/face.task"):
            with self.subTest(path=path):
                request = self.request("/ar_testing/" + path)
                self.assertEqual(request.response_headers["Cache-Control"], "no-cache")
                for tag in (request.response_headers["ETag"], "W/" + request.response_headers["ETag"], "*"):
                    cached = self.request("/ar_testing/" + path, headers={"If-None-Match": tag})
                    self.assertEqual(cached.status, 304)
                    self.assertEqual(cached.wfile.getvalue(), b"")
        asset = self.request("/ar_testing/assets/live-abcdefgh.js")
        self.assertIn("immutable", asset.response_headers["Cache-Control"])

    def test_traversal_double_encoding_private_files_and_listings_are_not_served(self):
        hidden = self.root / "private.jpg"
        hidden.write_bytes(b"not allowlisted")
        attacks = (
            "../README.md", "%2e%2e/README.md", "%252e%252e/README.md", "assets/../../README.md",
            "assets%5c..%5cREADME.md", "assets\\..\\README.md", "assets//live-abcdefgh.js",
            "assets/./live-abcdefgh.js", "assets/", "models/", "public-manifest.json", ".env",
            ".recovery/private.json", "recordings/private.json", "qa/output/private.json",
            "private.jpg", "assets/live-abcdefgh.js.map", "models/face.task%00", "%ff.json",
        )
        for path in attacks:
            for head in (False, True):
                with self.subTest(path=path, head=head):
                    request = self.request("/ar_testing/" + path, head=head)
                    self.assertEqual(request.status, 404)
                    if head:
                        self.assertEqual(request.wfile.getvalue(), b"")

    def test_missing_and_invalid_builds_are_unavailable_without_leaking_paths(self):
        (self.root / "public-manifest.json").unlink()
        for path in ("/ar_testing/", "/ar_testing/models/face.task"):
            request = self.request(path)
            self.assertEqual(request.status, 503)
            self.assertNotIn(str(self.root).encode(), request.wfile.getvalue())
        self.manifest["files"].append({"path": "recordings/private.json", "size": 0, "sha256": "0" * 64})
        self.write_manifest()
        self.assertEqual(self.request("/ar_testing/").status, 503)
        self.assertEqual(self.request("/ar_testing/public-manifest.json").status, 404)

    def test_size_and_hash_mismatch_do_not_publish_corrupt_files(self):
        path = self.root / "assets/live-abcdefgh.js"
        self.assertEqual(self.request("/ar_testing/assets/live-abcdefgh.js").status, 200)
        path.write_bytes(b"x" * len(self.contents["assets/live-abcdefgh.js"]))
        self.assertEqual(self.request("/ar_testing/assets/live-abcdefgh.js").status, 503)
        path.write_bytes(b"wrong size")
        self.assertEqual(self.request("/ar_testing/assets/live-abcdefgh.js", head=True).status, 503)

    def test_manifest_update_invalidates_integrity_and_cache_metadata(self):
        path = "assets/live-abcdefgh.js"
        original = self.request("/ar_testing/" + path)
        content = b"new deployment"
        (self.root / path).write_bytes(content)
        record = next(entry for entry in self.manifest["files"] if entry["path"] == path)
        record.update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
        self.write_manifest()
        updated = self.request("/ar_testing/" + path, headers={"If-None-Match": original.response_headers["ETag"]})
        self.assertEqual(updated.status, 200)
        self.assertEqual(updated.wfile.getvalue(), content)
        self.assertNotEqual(updated.response_headers["ETag"], original.response_headers["ETag"])

    def test_resolved_escape_or_alias_is_denied_even_if_listed(self):
        target = self.root / "assets/live-abcdefgh.js"
        original_resolve = Path.resolve

        def escaped_resolve(path, strict=False):
            return self.root.parent / "private.js" if path == target else original_resolve(path, strict=strict)

        with patch.object(Path, "resolve", escaped_resolve):
            self.assertEqual(self.request("/ar_testing/assets/live-abcdefgh.js").status, 404)

    def test_parent_routes_are_left_to_the_original_handler(self):
        for path in ("/", "/free-search", "/api/catalog", "/static/app.js", "/ar_testing-other/"):
            request = _Request()
            self.assertFalse(self.site.serve(request, path))
            self.assertIsNone(request.status)
            self.assertEqual(request.response_headers, {})


class TestParentARIntegration(unittest.TestCase):
    def test_original_parent_pages_keep_their_existing_headers_and_content(self):
        from UI.handler import Handler
        for path in ("/", "/free-search", "/lens-recolor", "/storefront"):
            for method in (Handler.do_GET, Handler.do_HEAD):
                with self.subTest(path=path, method=method.__name__):
                    request = _Request()
                    request.path = path
                    request._html = lambda content: Handler._html(request, content)
                    method(request)
                    self.assertEqual(request.status, 200)
                    self.assertNotIn("Content-Security-Policy", request.response_headers)
                    self.assertNotIn("Cross-Origin-Embedder-Policy", request.response_headers)
                    self.assertNotIn("Cross-Origin-Opener-Policy", request.response_headers)
                    if method == Handler.do_GET:
                        self.assertIn(b"</html>", request.wfile.getvalue())
                    else:
                        self.assertEqual(request.wfile.getvalue(), b"")

    def test_parent_handler_routes_both_get_and_head_to_the_public_boundary(self):
        from UI.handler import Handler
        site = PublicARSite(Path(tempfile.gettempdir()) / "lenses-ar-nonexistent-test-bundle")
        self.assertFalse(site.directory.exists())
        with patch("UI.handler.AR_TESTING_SITE", site):
            for method in (Handler.do_GET, Handler.do_HEAD):
                request = _Request()
                request.path = "/ar_testing/"
                method(request)
                self.assertEqual(request.status, 503)
                self.assertEqual(bool(request.wfile.getvalue()), method == Handler.do_GET)

    def test_landing_has_the_requested_option(self):
        from UI.templates import get_template
        html = get_template("landing")
        self.assertIn('href="/ar_testing/">ar_testing</a>', html)


if __name__ == "__main__":
    unittest.main()
