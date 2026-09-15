"""Public AR boundary checks with temporary bundles; no camera, GPU or API calls."""

import hashlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from UI.ar_site import PublicARSite


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
            "index.html": b"<!doctype html><title>Lenses AR</title>",
            "assets/index-abcdefgh.js": b"export const test = true;",
            "assets/worker-abcdefgh.js": b"self.onmessage = () => {};",
            "assets/index-abcdefgh.css": b"canvas { width:100%; }",
            "mediapipe/vision_wasm_module_internal.wasm": b"\0asm\x01\0\0\0",
            "models/manifest.json": b'{"models":[]}',
            "models/face_landmarker.task": b"model" * 100001,
            "models/hair/hair-only.tflite": b"tflite",
            "models/amber-horizon.glb": b"glTF",
            "licenses/Three-MIT.txt": b"MIT",
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
        path = self.root / "public-manifest.json"
        path.write_text(json.dumps(self.manifest), encoding="utf-8")
        # Successive rewrites inside one filesystem timestamp tick must still look changed to the stamp cache.
        self._manifest_writes = getattr(self, "_manifest_writes", 0) + 1
        stamp = time.time_ns() + self._manifest_writes * 10_000_000
        os.utime(path, ns=(stamp, stamp))

    def request(self, path, *, head=False, headers=None):
        request = _Request(headers)
        self.assertTrue(self.site.serve(request, path, head=head))
        return request

    def test_the_directory_serves_the_entry_document_and_the_bare_prefix_redirects_to_it(self):
        for head in (False, True):
            with self.subTest(head=head):
                request = self.request("/ar/", head=head)
                self.assertEqual(request.status, 200)
                self.assertEqual(request.response_headers["Content-Type"], "text/html; charset=utf-8")
                self.assertEqual(request.response_headers["Cache-Control"], "no-store")
                self.assertEqual(request.wfile.getvalue(), b"" if head else self.contents["index.html"])
                query = self.request("/ar/?exposure=312", head=head)
                self.assertEqual(query.status, 200)
        for target, location in (("/ar", "/ar/"), ("/ar?capture=960", "/ar/?capture=960")):
            for head in (False, True):
                with self.subTest(target=target, head=head):
                    request = self.request(target, head=head)
                    self.assertEqual(request.status, 302)
                    self.assertEqual(request.response_headers["Location"], location)
                    self.assertEqual(request.wfile.getvalue(), b"")

    def test_get_and_head_serve_matching_metadata_and_correct_runtime_types(self):
        expected = {
            ".html": "text/html", ".js": "application/javascript", ".wasm": "application/wasm",
            ".css": "text/css", ".json": "application/json", ".task": "application/octet-stream",
            ".tflite": "application/octet-stream", ".glb": "model/gltf-binary", ".txt": "text/plain",
        }
        for path, content in self.contents.items():
            with self.subTest(path=path):
                get = self.request("/ar/" + path + "?v=123")
                head = self.request("/ar/" + path, head=True)
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
        request = self.request("/ar/models/face_landmarker.task")
        self.assertEqual(request.wfile.getvalue(), self.contents["models/face_landmarker.task"])
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
        for path in ("/ar/", "/ar/index.html", "/ar/assets/worker-abcdefgh.js",
                     "/ar/mediapipe/vision_wasm_module_internal.wasm"):
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
            missing = self.request("/ar/unlisted.js", head=head)
            self.assertEqual(missing.status, 404)
            self.assert_local_policy(missing)
        (self.root / "assets/worker-abcdefgh.js").write_bytes(b"corrupt")
        for head in (False, True):
            corrupt = self.request("/ar/assets/worker-abcdefgh.js", head=head)
            self.assertEqual(corrupt.status, 503)
            self.assert_local_policy(corrupt)
        (self.root / "public-manifest.json").unlink()
        for head in (False, True):
            unavailable = self.request("/ar/", head=head)
            self.assertEqual(unavailable.status, 503)
            self.assert_local_policy(unavailable)

    def test_entry_and_model_manifests_revalidate_while_hashed_assets_are_immutable(self):
        for path in ("index.html", "models/manifest.json", "models/face_landmarker.task"):
            with self.subTest(path=path):
                request = self.request("/ar/" + path)
                self.assertEqual(request.response_headers["Cache-Control"], "no-store" if path == "index.html" else "no-cache")
                for tag in (request.response_headers["ETag"], "W/" + request.response_headers["ETag"], "*"):
                    cached = self.request("/ar/" + path, headers={"If-None-Match": tag})
                    self.assertEqual(cached.status, 304)
                    self.assertEqual(cached.wfile.getvalue(), b"")
        asset = self.request("/ar/assets/index-abcdefgh.js")
        self.assertIn("immutable", asset.response_headers["Cache-Control"])

    def test_traversal_double_encoding_private_files_and_listings_are_not_served(self):
        hidden = self.root / "private.jpg"
        hidden.write_bytes(b"not allowlisted")
        attacks = (
            "../README.md", "%2e%2e/README.md", "%252e%252e/README.md", "assets/../../README.md",
            "assets%5c..%5cREADME.md", "assets\\..\\README.md", "assets//index-abcdefgh.js",
            "assets/./index-abcdefgh.js", "assets/", "models/", "public-manifest.json", ".env",
            ".recovery/private.json", "recordings/private.json", "qa/output/private.json",
            "src/main.ts", "private.jpg", "assets/index-abcdefgh.js.map", "models/face_landmarker.task%00", "%ff.json",
        )
        for path in attacks:
            for head in (False, True):
                with self.subTest(path=path, head=head):
                    request = self.request("/ar/" + path, head=head)
                    self.assertEqual(request.status, 404)
                    if head:
                        self.assertEqual(request.wfile.getvalue(), b"")

    def test_missing_and_invalid_builds_are_unavailable_without_leaking_paths(self):
        (self.root / "public-manifest.json").unlink()
        for path in ("/ar/", "/ar/models/face_landmarker.task"):
            request = self.request(path)
            self.assertEqual(request.status, 503)
            self.assertNotIn(str(self.root).encode(), request.wfile.getvalue())
        self.manifest["files"].append({"path": "recordings/private.json", "size": 0, "sha256": "0" * 64})
        self.write_manifest()
        self.assertEqual(self.request("/ar/").status, 503)
        self.assertEqual(self.request("/ar/public-manifest.json").status, 404)

    def test_a_manifest_without_the_entry_document_is_unavailable(self):
        self.manifest["files"] = [entry for entry in self.manifest["files"] if entry["path"] != "index.html"]
        self.write_manifest()
        self.assertEqual(self.request("/ar/").status, 503)
        self.assertEqual(self.request("/ar/assets/index-abcdefgh.js").status, 503)

    def test_size_and_hash_mismatch_do_not_publish_corrupt_files(self):
        path = self.root / "assets/index-abcdefgh.js"
        self.assertEqual(self.request("/ar/assets/index-abcdefgh.js").status, 200)
        path.write_bytes(b"x" * len(self.contents["assets/index-abcdefgh.js"]))
        self.assertEqual(self.request("/ar/assets/index-abcdefgh.js").status, 503)
        path.write_bytes(b"wrong size")
        self.assertEqual(self.request("/ar/assets/index-abcdefgh.js", head=True).status, 503)

    def test_manifest_update_invalidates_integrity_and_cache_metadata(self):
        path = "assets/index-abcdefgh.js"
        original = self.request("/ar/" + path)
        content = b"new deployment"
        (self.root / path).write_bytes(content)
        record = next(entry for entry in self.manifest["files"] if entry["path"] == path)
        record.update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
        self.write_manifest()
        updated = self.request("/ar/" + path, headers={"If-None-Match": original.response_headers["ETag"]})
        self.assertEqual(updated.status, 200)
        self.assertEqual(updated.wfile.getvalue(), content)
        self.assertNotEqual(updated.response_headers["ETag"], original.response_headers["ETag"])

    def test_resolved_escape_or_alias_is_denied_even_if_listed(self):
        target = self.root / "assets/index-abcdefgh.js"
        original_resolve = Path.resolve

        def escaped_resolve(path, strict=False):
            return self.root.parent / "private.js" if path == target else original_resolve(path, strict=strict)

        with patch.object(Path, "resolve", escaped_resolve):
            self.assertEqual(self.request("/ar/assets/index-abcdefgh.js").status, 404)

    def test_parent_routes_are_left_to_the_original_handler(self):
        for path in ("/", "/free-search", "/api/catalog", "/static/app.js", "/ar-other/", "/ar_testing/", "/art"):
            request = _Request()
            self.assertFalse(self.site.serve(request, path))
            self.assertIsNone(request.status)
            self.assertEqual(request.response_headers, {})


class TestStartupDiagnostics(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lenses-ar-route-")
        self.site = PublicARSite(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def post(self, body, length=None):
        request = _Request({"Content-Length": str(len(body) if length is None else length)})
        request.rfile = io.BytesIO(body)
        self.assertTrue(self.site.serve_post(request, "/ar/diagnostic"))
        return request

    def test_reports_are_kept_in_order_and_readable_without_a_bundle(self):
        for index in range(3):
            request = self.post(json.dumps({"page": "abcd", "events": [{"t": index, "event": "step"}]}).encode())
            self.assertEqual(request.status, 204)
            self.assertEqual(request.response_headers["Cache-Control"], "no-store")
            self.assertEqual(request.response_headers["Cross-Origin-Embedder-Policy"], "require-corp")
        listing = _Request()
        self.assertTrue(self.site.serve(listing, "/ar/diagnostics.json"))
        self.assertEqual(listing.status, 200)
        self.assertEqual(listing.response_headers["Content-Type"], "application/json; charset=utf-8")
        reports = json.loads(listing.wfile.getvalue())["reports"]
        self.assertEqual([r["report"]["events"][0]["t"] for r in reports], [0, 1, 2])
        self.assertTrue(all("receivedAt" in r for r in reports))
        head = _Request()
        self.assertTrue(self.site.serve(head, "/ar/diagnostics.json", head=True))
        self.assertEqual(head.wfile.getvalue(), b"")

    def test_oversized_invalid_and_foreign_posts_are_refused(self):
        self.assertEqual(self.post(b"x" * (16 * 1024 + 1)).status, 413)
        self.assertEqual(self.post(b"", length=0).status, 413)
        self.assertEqual(self.post(b"not json").status, 400)
        self.assertEqual(self.post(b"[1, 2]").status, 400)
        other = _Request({"Content-Length": "2"})
        self.assertFalse(self.site.serve_post(other, "/api/upload"))
        self.assertFalse(self.site.serve_post(other, "/ar/diagnostics.json"))
        listing = _Request()
        self.site.serve(listing, "/ar/diagnostics.json")
        self.assertEqual(json.loads(listing.wfile.getvalue()), {"reports": []})

    def test_only_the_last_forty_reports_are_kept(self):
        for index in range(45):
            self.post(json.dumps({"n": index}).encode())
        listing = _Request()
        self.site.serve(listing, "/ar/diagnostics.json")
        reports = json.loads(listing.wfile.getvalue())["reports"]
        self.assertEqual(len(reports), 40)
        self.assertEqual(reports[0]["report"]["n"], 5)


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
        with patch("UI.handler.AR_SITE", site):
            for method in (Handler.do_GET, Handler.do_HEAD):
                request = _Request()
                request.path = "/ar/"
                method(request)
                self.assertEqual(request.status, 503)
                self.assertEqual(bool(request.wfile.getvalue()), method == Handler.do_GET)

    def test_the_ar_testing_route_is_gone(self):
        import importlib.util
        from UI.ar_site import AR_SITE
        self.assertIsNone(importlib.util.find_spec("UI.ar_testing"))
        request = _Request()
        self.assertFalse(AR_SITE.serve(request, "/ar_testing/"))
        self.assertIsNone(request.status)

    def test_landing_lists_ar_first_and_ar_testing_not_at_all(self):
        from UI.templates import get_template
        html = get_template("landing")
        self.assertIn('href="/ar/">AR</a>', html)
        self.assertNotIn("ar_testing", html)
        self.assertLess(html.index('href="/ar/"'), html.index("openSmartFit()"))

    def test_the_published_site_in_this_checkout_is_complete_when_present(self):
        from UI.ar_site import AR_SITE
        manifest_path = AR_SITE.directory / "public-manifest.json"
        if not manifest_path.exists():
            self.skipTest("no published site in this checkout")
        request = _Request()
        self.assertTrue(AR_SITE.serve(request, "/ar/"))
        self.assertEqual(request.status, 200)
        html = request.wfile.getvalue().decode("utf-8")
        self.assertIn('src="/ar/assets/', html)
        for path in ("models/face_landmarker.task", "models/canonical-face.json", "models/amber-horizon.glb",
                     "models/hair/hair-only.tflite", "mediapipe/vision_wasm_module_internal.wasm"):
            with self.subTest(path=path):
                head = _Request()
                self.assertTrue(AR_SITE.serve(head, "/ar/" + path, head=True))
                self.assertEqual(head.status, 200)


if __name__ == "__main__":
    unittest.main()
