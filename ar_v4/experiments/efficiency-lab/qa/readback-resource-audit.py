"""Independently audit saved production-browser resource receipts against a G readback ZIP.

The progress file contains observation-only browser snapshots. Neither input is
modified. The raw ZIP auditor independently recomputes timing and native metrics.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("readback_raw_audit", Path(__file__).with_name("readback-audit.py"))
raw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(raw)
check = raw.check


def audit(progress_path, zip_path):
    measured = raw.audit(zip_path)
    check(measured["status"] == "complete" and len(measured["parts"]) == 2, "Need both complete production parts")
    progress_bytes = raw.native_path(progress_path).read_bytes()
    receipts = json.loads(progress_bytes)
    check(isinstance(receipts, list) and len(receipts) >= 3, "Missing resource progress receipts")
    check(all(a["atMs"] <= b["atMs"] for a, b in zip(receipts, receipts[1:])), "Progress observations run backward")
    check(all(item["status"]["suiteId"] == measured["suiteId"] for item in receipts), "Mixed suites")
    check(len({item["resources"]["historyLength"] for item in receipts}) == 1, "Navigation grew history")
    check(all(item["resources"]["navigationType"] == "reload" for item in receipts), "A measured page did not actually reload")
    running = [item for item in receipts if item["status"]["state"] == "running"]
    check(len(running) == 2, "Need one actual tracking observation per measured document")
    check(len({item["resources"]["documentId"] for item in running}) == 2, "Camera observations reuse a document")
    check(len({item["status"]["documentId"] for item in running}) == 2, "Protocol reused document ownership")
    check(len({len(item["resources"]["contexts"]) for item in running}) == 1, "Conditions allocated different GL context counts")
    check(len({len(item["resources"]["workers"]) for item in running}) == 1, "Conditions allocated different worker counts")
    conditions = []
    for item, part in zip(running, measured["parts"]):
        status, resource = item["status"], item["resources"]
        diagnostic = resource["diagnostics"]
        check(status["condition"] == part["condition"] and status["documentId"] == part["documentId"], "Progress condition/owner differs from saved raw part")
        check(status["active"]["chunkId"] == part["chunkId"] and status["active"]["documentId"] == part["documentId"], "Active claim differs")
        check(status["ownsLock"] is True and status["cameraAttempts"] == 1, "Missing single owner/camera attempt")
        check(diagnostic["sessionId"] == part["sessionId"] and diagnostic["runtimePipeline"] == part["pipeline"], "Observed runtime differs from saved raw session")
        check(diagnostic["runtimeGeneration"] == 1 and diagnostic["runtimeReady"] is True, "Unexpected runtime rebuild/unready state")
        check(diagnostic["state"] == "tracking" and diagnostic["phase"] == "live", "Observed page was not tracking live")
        check(diagnostic["currentBase"] == "g" and diagnostic["candidateAccepted"] is False, "Baseline metadata was promoted")
        observed_sample = diagnostic.get("performanceSample") or diagnostic.get("stats") or {}
        check(observed_sample.get("hasFace") is True and observed_sample.get("hasMask") is True, "Running snapshot lacks tracked matching mask")
        check(resource["streams"] == [["live"]] and len(resource["workers"]) == 2
              and all(worker["terminated"] is False for worker in resource["workers"]), "Unexpected running camera/worker allocation")
        endings = [candidate for candidate in receipts if candidate["resources"]["documentId"] == resource["documentId"]
                   and candidate["status"]["state"] in ["saving", "complete"]]
        check(len(endings) >= 1, "No saved cleanup receipt for a measured document")
        last = endings[-1]["resources"]
        check(last["streams"] == [["ended"]], "Camera not stopped before saving/complete")
        check(len(last["workers"]) == len(resource["workers"])
              and all(worker["terminated"] is True for worker in last["workers"]), "Worker left running")
        check(len(last["contexts"]) == len(resource["contexts"])
              and all(context["lost"] is True for context in last["contexts"]), "GL context left live")
        check(last["diagnostics"]["state"] == "idle" and last["diagnostics"]["sessionId"] is None
              and last["diagnostics"]["processing"] is False and last["diagnostics"]["presented"] is None, "Runtime retained owned frame/session")
        conditions.append({"condition": part["condition"], "pipeline": part["pipeline"], "documentId": part["documentId"],
                           "sessionId": part["sessionId"], "contexts": len(resource["contexts"]), "workers": len(resource["workers"]),
                           "cleanupAtEndOfDocument": True})
    final = receipts[-1]["status"]
    check(final["state"] == final["savedState"] == "complete" and final["completedChunks"] == final["planLength"] == 2,
          "No durable suite completion")
    check(final["active"] is None and final["pending"] is False and final["progress"]["running"] is False,
          "Active claim or work retained after suite completion")
    return {"audit": "passed", "suiteId": measured["suiteId"], "zipSHA256": measured["sha256"],
            "progressSHA256": hashlib.sha256(progress_bytes).hexdigest(), "progressReceipts": len(receipts),
            "measuredMs": sum(w["durationMs"] for part in measured["parts"] for w in part["windows"]),
            "historyLength": receipts[0]["resources"]["historyLength"], "conditions": conditions,
            "limits": "Independent saved-receipt and raw measurement audit; not a claim that the original trace-heavy Playwright postprocessing completed, physical iPhone speed, or wearer-motion acceptance."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("progress", type=Path)
    parser.add_argument("zip", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.progress, args.zip)
    if args.output:
        raw.native_path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf8")
    print(json.dumps(result))
