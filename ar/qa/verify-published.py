"""Read-only verification of the published AR site over HTTPS against the local manifest.

    python qa/verify-published.py --url https://web-production-ef3ca.up.railway.app [--output qa/output/published-receipt.json]

Every file listed in site/public-manifest.json must be served under /ar/ with the exact size and SHA-256, the isolation
headers and the local-only Content Security Policy; the landing page must list AR first; unlisted and private paths must
be refused. No camera, no performance claim.
"""

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def verify(base):
    if not base.startswith("https://"):
        raise ValueError("Published verification requires HTTPS")
    base = base.rstrip("/")
    site = Path(__file__).resolve().parents[1] / "site"
    manifest = json.loads((site / "public-manifest.json").read_text(encoding="utf-8"))
    with urllib.request.urlopen(base + "/", timeout=45) as response:
        landing = response.read().decode("utf-8")
        assert 'href="/ar/">AR</a>' in landing, "the landing page does not list AR"
        assert "ar_testing" not in landing, "the landing page still lists ar_testing"
        assert landing.index('href="/ar/"') < landing.index("openSmartFit()"), "AR is not the first option"
        assert not response.headers.get("Content-Security-Policy")
    checked = []
    for entry in manifest["files"]:
        url = base + "/ar/" + entry["path"]
        digest, size = hashlib.sha256(), 0
        with urllib.request.urlopen(url, timeout=120) as response:
            assert response.status == 200, (entry["path"], response.status)
            assert response.headers.get("Cross-Origin-Opener-Policy") == "same-origin"
            assert response.headers.get("Cross-Origin-Embedder-Policy") == "require-corp"
            assert "connect-src 'self' blob:" in response.headers.get("Content-Security-Policy", "")
            assert response.headers.get("Content-Length") == str(entry["size"]), entry["path"]
            assert response.headers.get("ETag") == '"sha256-' + entry["sha256"] + '"', entry["path"]
            while block := response.read(128 * 1024):
                digest.update(block)
                size += len(block)
        assert size == entry["size"] and digest.hexdigest() == entry["sha256"], entry["path"]
        checked.append({"path": entry["path"], "size": size, "sha256": digest.hexdigest()})
    with urllib.request.urlopen(base + "/ar", timeout=45) as response:
        assert response.url == base + "/ar/", response.url
    for path in ["public-manifest.json", ".gitattributes", "src/main.ts", "qa/output/private.json", "unlisted.txt"]:
        try:
            urllib.request.urlopen(base + "/ar/" + path, timeout=45)
        except urllib.error.HTTPError as error:
            assert error.code == 404, (path, error.code)
        else:
            raise AssertionError("Unlisted path was served: " + path)
    try:
        urllib.request.urlopen(base + "/ar_testing/", timeout=45)
    except urllib.error.HTTPError as error:
        assert error.code == 404, ("/ar_testing/", error.code)
    else:
        raise AssertionError("/ar_testing/ is still served")
    return {"verifiedAt": datetime.now(timezone.utc).isoformat(), "baseUrl": base, "builtAt": manifest.get("builtAt"),
            "files": checked, "privatePathsRejected": True,
            "scope": "Published bytes and route headers only; no camera or performance claim."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    receipt = verify(args.url)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(receipt['files'])} published files at {receipt['baseUrl']}/ar/ (built {receipt['builtAt']}).")
    sys.exit(0)
