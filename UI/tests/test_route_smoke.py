"""End-to-end smoke over the real handler. No API key, no network, no Gemini.

Every page renders and the whole 147-product catalogue serves without a
GEMINI_API_KEY, so this exercises the real routing stack in about a tenth of a
second. Nothing else in the suite touches handler.py at all.
"""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer
from socketserver import ThreadingMixIn

from UI.handler import Handler


class _Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class TestRoutes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = _Server(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()

    def test_pages_render(self):
        for path in ("/", "/free-search", "/lens-recolor", "/storefront"):
            with self.subTest(path=path):
                status, ctype, body = self._get(path)
                self.assertEqual(status, 200)
                self.assertIn("text/html", ctype)
                self.assertGreater(len(body), 2000, f"{path} served a suspiciously small page")
                self.assertIn(b"</html>", body)

    def test_static_assets_serve(self):
        for path in ("/static/css/tokens.css", "/static/js/common.js",
                     "/static/fonts/archivo-var-latin.woff2"):
            with self.subTest(path=path):
                status, _, body = self._get(path)
                self.assertEqual(status, 200)
                self.assertGreater(len(body), 100)

    def test_catalog_serves_without_api_key(self):
        status, ctype, body = self._get("/api/catalog")
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        products = json.loads(body)
        self.assertGreater(len(products), 100, "catalogue should carry the full inventory")
        first = products[0]
        for field in ("id", "name", "brand", "price", "currency", "image", "gender"):
            self.assertIn(field, first)

    def test_catalog_images_serve(self):
        products = json.loads(self._get("/api/catalog")[2])
        name = products[0]["image"].replace("images/", "")
        status, ctype, body = self._get("/api/catalog-image/" + name)
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("image/"), ctype)
        self.assertGreater(len(body), 1000)

    def test_unknown_route_404s(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/does-not-exist")
        self.assertEqual(cm.exception.code, 404)

    def test_templates_reload_without_restart(self):
        """get_template() is stat-cached, so an edit must be visible immediately."""
        from pathlib import Path
        from UI.templates import get_template
        path = Path(__file__).resolve().parent.parent / "templates" / "landing.html"
        original = path.read_text(encoding="utf-8")
        marker = "<!-- reload-probe -->"
        try:
            path.write_text(original.replace("</body>", marker + "</body>"), encoding="utf-8")
            self.assertIn(marker, get_template("landing"))
            self.assertIn(marker.encode(), self._get("/")[2])
        finally:
            path.write_text(original, encoding="utf-8")
        self.assertNotIn(marker, get_template("landing"))

    def test_static_refs_are_versioned(self):
        """Every /static/ reference a page serves carries its file's version.

        A deploy that changes a stylesheet but not the page loading it left
        every browser on the old stylesheet, and the page looked unshipped no
        matter how many times it went out — reported twice as "it's still
        showing". A changed URL cannot be answered from a cache.
        """
        import re
        for path in ("/", "/free-search", "/lens-recolor", "/storefront"):
            with self.subTest(path=path):
                body = self._get(path)[2].decode("utf-8")
                refs = re.findall(r'(?:href|src)="(/static/[^"]+)"', body)
                self.assertTrue(refs, f"{path} references no static assets at all")
                bare = [r for r in refs if "?v=" not in r]
                self.assertEqual(bare, [], f"{path} serves unversioned assets: {bare}")
                # and the versioned URL must still resolve
                for ref in refs[:3]:
                    self.assertEqual(self._get(ref)[0], 200, f"{ref} did not serve")

    def test_asset_version_tracks_the_file(self):
        """The stamp has to move when the bytes could have, or it is decoration."""
        import re
        from pathlib import Path
        from UI.templates import get_template
        css = Path(__file__).resolve().parent.parent / "static" / "css" / "landing.css"
        original = css.read_bytes()
        grab = lambda: re.search(r'landing\.css\?v=([^"]+)', get_template("landing")).group(1)
        before = grab()
        try:
            css.write_bytes(original + b"\n/* probe */\n")
            after = grab()
        finally:
            css.write_bytes(original)
        self.assertNotEqual(before, after, "editing a stylesheet did not change its version stamp")
        self.assertEqual(grab(), before, "restoring the stylesheet did not restore its stamp")

    def test_rapid_edits_are_never_served_stale(self):
        """This test used to fail about two runs in three, and it was right to.

        Filesystem timestamps advance in ticks, so consecutive writes are often
        stamped identically — measured here at 182 of 200 back-to-back writes.
        The cache keyed on mtime alone could not see the second write and kept
        serving the first, which is exactly what the restore at the end of the
        test above tripped over.

        The markers below are deliberately the same length for the first ten
        iterations, so size cannot distinguish them either and the settle window
        is what has to do the work.
        """
        from pathlib import Path
        from UI.templates import get_template
        path = Path(__file__).resolve().parent.parent / "templates" / "landing.html"
        original = path.read_text(encoding="utf-8")
        stale = []
        try:
            for i in range(40):
                marker = f"<!-- probe-{i:02d} -->"          # fixed width on purpose
                path.write_text(original.replace("</body>", marker + "</body>"), encoding="utf-8")
                if marker not in get_template("landing"):
                    stale.append(marker)
        finally:
            path.write_text(original, encoding="utf-8")
        self.assertEqual(stale, [], f"{len(stale)}/40 edits served from a stale cache: {stale}")
        self.assertNotIn("probe-", get_template("landing"))

    def test_pages_must_be_revalidated(self):
        """Versioned asset URLs are only as fresh as the page carrying them.

        These responses went out with no Cache-Control, no ETag and no
        Last-Modified, which is not "do not cache" — it is "guess", and a
        heuristic hit serves an older document along with that document's
        asset stamps.
        """
        for path in ("/", "/free-search", "/lens-recolor", "/storefront"):
            with self.subTest(path=path):
                with urllib.request.urlopen(self.base + path, timeout=10) as r:
                    cc = r.headers.get("Cache-Control", "")
                self.assertIn("no-cache", cc, f"{path} may be served from a heuristic cache")


class TestScoreBreakdownContract(unittest.TestCase):
    """What /api/status is allowed to say about the fit / style / colour chips.

    The endpoint used to default each one to the overall score, so a pipeline
    that computed no breakdown still shipped three chips reading 80 / 80 / 80 —
    one number wearing three labels, indistinguishable on the page from a real
    breakdown that happened to agree.
    """

    @classmethod
    def setUpClass(cls):
        cls.server = _Server(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    _OPT = {
        "name": "Test Frame", "brand": "Test", "model": "T1", "price": 100,
        "currency": "ILS", "shape": "round", "material": "acetate",
        "color": "black", "product_b64": "", "tryon_status": "pending",
        "tryon_b64": None,
    }

    def _status(self, opt):
        from UI.config import sessions
        sid = "contract-test"
        sessions[sid] = {"status": "processing", "stage": "tryon", "num_options": 1,
                         "opt0": dict(self._OPT, **opt)}
        try:
            with urllib.request.urlopen(f"{self.base}/api/status/{sid}", timeout=10) as r:
                return json.loads(r.read())["opt0"]
        finally:
            sessions.pop(sid, None)

    def test_a_real_breakdown_is_passed_through(self):
        opt = self._status({"score": 91.0, "fit_score": 80.0,
                            "style_score": 64.0, "color_score": 55.0})
        self.assertEqual((opt["fit_score"], opt["style_score"], opt["color_score"]),
                         (80.0, 64.0, 55.0))

    def test_a_missing_breakdown_is_null_not_the_overall_score(self):
        """The storefront try-on has no query to score against — the visitor
        picked the frame. It must say nothing rather than something untrue."""
        opt = self._status({"score": 80.0})
        for key in ("fit_score", "style_score", "color_score"):
            self.assertIsNone(opt[key], f"{key} invented a value from the overall score")

    def test_a_zero_breakdown_survives_serialisation(self):
        """0.0 is a legitimate score and must not be read as absent."""
        opt = self._status({"score": 0.0, "fit_score": 0.0,
                            "style_score": 0.0, "color_score": 0.0})
        for key in ("fit_score", "style_score", "color_score"):
            self.assertEqual(opt[key], 0.0)


if __name__ == "__main__":
    unittest.main()
