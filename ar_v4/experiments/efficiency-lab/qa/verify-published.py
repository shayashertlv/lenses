"""Read-only verification of the exact mobile package served over HTTPS."""

import argparse
import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def verify(base):
    if not base.startswith("https://"):
        raise ValueError("Published verification requires HTTPS")
    base = base.rstrip("/")
    package = Path(__file__).resolve().parents[3] / "mobile-site"
    manifest = json.loads((package / "public-manifest.json").read_text())
    release = json.loads((package / "release.json").read_text())
    with urllib.request.urlopen(base + "/", timeout=45) as response:
        landing = response.read().decode("utf-8")
        assert 'href="/ar_testing/"' in landing and ">ar_testing</a>" in landing
        assert not response.headers.get("Content-Security-Policy")
    checked = []
    for entry in manifest["files"]:
        url = base + "/ar_testing/" + entry["path"]
        digest, size = hashlib.sha256(), 0
        with urllib.request.urlopen(url, timeout=90) as response:
            assert response.status == 200
            assert response.headers.get("Cross-Origin-Opener-Policy") == "same-origin"
            assert response.headers.get("Cross-Origin-Embedder-Policy") == "require-corp"
            assert "connect-src 'self' blob:" in response.headers.get("Content-Security-Policy", "")
            assert response.headers.get("Content-Length") == str(entry["size"])
            assert response.headers.get("ETag") == '"sha256-' + entry["sha256"] + '"'
            while block := response.read(128 * 1024):
                digest.update(block)
                size += len(block)
        assert size == entry["size"] and digest.hexdigest() == entry["sha256"], entry["path"]
        checked.append({"path": entry["path"], "size": size, "sha256": digest.hexdigest()})
    with urllib.request.urlopen(base + "/ar_testing/", timeout=45) as response:
        assert response.url == base + "/ar_testing/experiments/efficiency-lab/live.html?study=review"
    for path in ["public-manifest.json", ".recovery/private.json", "recordings/private.json", "unlisted.txt"]:
        try:
            urllib.request.urlopen(base + "/ar_testing/" + path, timeout=45)
        except urllib.error.HTTPError as error:
            assert error.code == 404, (path, error.code)
        else:
            raise AssertionError("Unlisted path was served: " + path)
    return {"verifiedAt": datetime.now(timezone.utc).isoformat(), "baseUrl": base,
            "release": release, "files": checked, "privatePathsRejected": True,
            "scope": "Published bytes and route headers only; no camera or mobile performance claim."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = verify(args.url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(receipt['files'])} published files; release {receipt['release']['sourceFingerprint'][:12]}.")
