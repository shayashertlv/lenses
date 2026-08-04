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


if __name__ == "__main__":
    unittest.main()
