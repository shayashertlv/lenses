"""Local QA uses the production Python routes without app startup or API calls."""

import argparse
import sys
from http.server import HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn


ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from UI.handler import Handler  # noqa: E402


class _Handler(Handler):
    def log_message(self, format, *args):
        pass


class _Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8104)
    args = parser.parse_args()
    if not (ROOT / "ar_v4" / "mobile-site" / "public-manifest.json").is_file():
        raise SystemExit("Build ar_v4/mobile-site before running mobile browser checks.")
    with _Server(("127.0.0.1", args.port), _Handler) as server:
        server.serve_forever()
