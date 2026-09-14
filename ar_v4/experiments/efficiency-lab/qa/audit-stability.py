"""Read-only, independent raw audit of a downloaded G stability ZIP.

Usage: python experiments/efficiency-lab/qa/audit-stability.py ZIP [--output JSON]
No camera images are extracted or interpreted; page clocks remain separate.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
import statistics
import zipfile
from pathlib import Path


def native_path(path):
    absolute = str(path.resolve())
    return Path("\\\\?\\" + absolute) if os.name == "nt" and not absolute.startswith("\\\\?\\") else path


def check(condition, reason):
    if not condition:
        raise ValueError(reason)


def close(actual, expected, reason):
    if expected is None:
        check(actual is None, reason)
    else:
        check(isinstance(actual, (int, float)) and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-7), reason)


def distribution(values):
    values = sorted(values)
    return None if not values else {"median": statistics.median(values), "p95": values[math.ceil(len(values) * .95) - 1], "max": values[-1]}


def interval(report, window_index, start, end, summary):
    rows = [r for r in report["rows"] if r["windowIndex"] == window_index and r["phase"] == "measured"
            and start <= r["fields"]["capturedAtMs"] <= r["fields"]["publishedAtMs"] < end]
    duration = end - start
    check(summary["frames"] == len(rows), "Summary frame count differs from admitted raw rows")
    close(summary["durationMs"], duration, "Summary uses a different measurement duration")
    close(summary["completedArFps"], len(rows) * 1000 / duration if duration else None, "AR throughput differs from full-clock raw rate")
    ages = [r["fields"]["publishedAtMs"] - r["fields"]["capturedAtMs"] for r in rows]
    times = [start, *sorted(r["fields"]["publishedAtMs"] for r in rows), end]
    gaps = [b - a for a, b in zip(times, times[1:])]
    for name, values in [("frameAgeMs", ages), ("completionGapMsIncludingEndpoints", gaps)]:
        expected = distribution(values)
        if expected is None:
            check(summary[name] is None, f"Unexpected {name} summary")
        else:
            for key, value in expected.items():
                close(summary[name][key], value, f"{name}.{key} differs from raw clocks")
    tracked = sum(r["fields"]["hasFace"] is True for r in rows)
    masked = sum(r["fields"]["hasFace"] is True and r["fields"]["hasMask"] is True for r in rows)
    check(summary["coverage"]["trackedFrames"] == tracked, "Tracking count differs")
    check(summary["coverage"]["maskedTrackedFrames"] == masked, "Matching-mask count differs")
    observations = [r for r in report["videoObservations"] if start <= r["atMs"] < end]
    span = observations[-1]["atMs"] - observations[0]["atMs"] if observations else 0
    camera = (observations[-1]["presentedFrames"] - observations[0]["presentedFrames"]) * 1000 / span if span else None
    close(summary["camera"]["deliveryFps"], camera, "Camera rate differs from independent observations")
    epochs = sorted({r.get("native", {}).get("runtime.generation") for r in rows})
    check(len(epochs) <= 1, "A measurement interval mixes runtime generations")
    return {"durationMs": duration, "frames": len(rows), "completedArFps": summary["completedArFps"],
            "cameraFps": camera, "trackedFraction": tracked / len(rows) if rows else None,
            "matchingMaskFraction": masked / tracked if tracked else None, "frameAgeMs": distribution(ages),
            "completionGapsMs": distribution(gaps), "runtimeGenerations": epochs,
            "stages": summary.get("stages", {})}


def audit(path):
    with zipfile.ZipFile(native_path(path)) as archive:
        check(archive.testzip() is None, "ZIP CRC failure")
        check(len(archive.namelist()) == len(set(archive.namelist())), "Duplicate ZIP entries")
        root = json.loads(archive.read("telemetry.json"))
        check(root["schema"] == "ar-g-stability-export-v1", "Not a G stability archive")
        suite = root["suite"]
        names = {"telemetry.json", *(part["filename"] for part in root["parts"])}
        check(set(archive.namelist()) == names, "Unexpected or missing ZIP entries")
        output, documents, sessions, origins = [], set(), set(), set()
        for part in root["parts"]:
            raw_bytes = archive.read(part["filename"])
            report = json.loads(raw_bytes)
            check(report["schema"] == "ar-continuous-comparison-v1", "Unexpected raw report schema")
            owner = report["metadata"]["stability"]
            check(owner["suiteId"] == suite["id"] and owner["documentId"] == part["documentId"]
                  and owner["chunkId"] == part["chunkId"], "Raw report owner differs from archive owner")
            check(owner["buildId"] == suite["identity"]["buildId"] == report["metadata"]["build"]["id"], "Mixed builds")
            check(owner["timeOrigin"] == report["metadata"]["performanceTimeOriginMs"], "Raw report has conflicting clock origins")
            check(owner["documentId"] not in documents and report["sessionId"] not in sessions
                  and owner["timeOrigin"] not in origins, "Different parts reuse document, session or clock ownership")
            documents.add(owner["documentId"]); sessions.add(report["sessionId"]); origins.add(owner["timeOrigin"])
            admitted = [r for r in report["rows"] if r["phase"] != "excluded" and r.get("exclusion") is None]
            excluded = [r for r in report["rows"] if r["phase"] == "excluded" or r.get("exclusion") is not None]
            if part["completed"]:
                check(not report["retention"]["truncated"] and not report["retention"]["rejectedRows"], "Completed report has truncated/rejected frame rows")
            sequences = [r["fields"]["sequence"] for r in admitted]
            check(len(sequences) == len(set(sequences)), "Duplicate completed frame sequence")
            check(all(r["fields"]["sessionId"] == report["sessionId"] and r["fields"]["pipeline"] == "g"
                      for r in admitted), "Mixed session or non-G admitted frames")
            identity = suite["identity"]
            check(all(report["workload"][key] == identity[key] for key in
                      ["eyewearId", "hairModelId", "variant", "sourceWidth", "sourceHeight"]), "Mixed workloads")
            windows = []
            for window in report["windows"]:
                summary = window["summary"]
                if summary["startAtMs"] is None or summary["endAtMs"] is None:
                    continue
                measured = interval(report, window["index"], summary["startAtMs"], summary["endAtMs"], summary)
                measured.update(index=window["index"], completed=window["completed"], validWarmupFrames=window["validWarmupFrames"], switchWaitMs=window["switchWaitMs"])
                if window["completed"]:
                    check(window["validWarmupFrames"] >= 3, "A completed window did not earn matching-mask warmup")
                    check(window["measureStartedAtMs"] - window["switchedAtMs"] >= 5000, "Shortened warmup")
                    close(measured["durationMs"], report["protocol"]["measureMs"], "Shortened measurement")
                windows.append(measured)
            bins = [dict(interval(report, b["windowIndex"], b["summary"]["startAtMs"], b["summary"]["endAtMs"], b["summary"]),
                         index=b["index"], completed=b["completed"]) for b in report.get("analysisBins", [])]
            if part["completed"]:
                check(report["completed"] and not report["partial"] and report["hairDeliveryDrain"]["state"] == "drained", "Completion or hair drain mismatch")
            output.append({"filename": part["filename"], "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                           "chunkId": part["chunkId"], "documentId": owner["documentId"], "sessionId": report["sessionId"],
                           "timeOrigin": owner["timeOrigin"], "completed": part["completed"], "rows": len(report["rows"]),
                           "diagnostics": {"admittedRows": len(admitted), "excludedRows": len(excluded),
                                           "exclusions": dict(Counter(str(r.get("exclusion")) for r in excluded)),
                                           "invalidFields": sum(len(r.get("invalidFields", [])) for r in report["rows"]),
                                           "rejectedRows": report["retention"]["rejectedRows"], "truncated": report["retention"]["truncated"]},
                           "windows": windows, "analysisBins": bins})
        if suite["status"] == "complete":
            check(len(output) == len(suite["plan"]) and all(p["completed"] for p in output), "Completed suite lacks completed parts")
            check(sum(w["durationMs"] for p in output for w in p["windows"]) == sum(p["totalMeasureMs"] for p in suite["plan"]), "Completed suite duration differs from plan")
    return {"zip": str(path.resolve()), "sha256": hashlib.sha256(native_path(path).read_bytes()).hexdigest(), "suiteId": suite["id"],
            "status": suite["status"], "identity": suite["identity"], "selection": suite["selection"], "direction": suite["direction"],
            "audit": "Raw rows, separate page clocks, warmup, full duration, AR/camera rates, frame age, endpoint stalls and matching-mask counts verified.",
            "limitations": "No visual-quality conclusion or automatic speed winner. Compare order, motion, mask coverage, thermal/power context and within-condition drift.", "parts": output}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.zip)
    encoded = json.dumps(result, indent=2)
    if args.output:
        native_path(args.output).write_text(encoded + "\n", encoding="utf-8")
    print(json.dumps({"audit": "passed", "status": result["status"], "parts": len(result["parts"]),
                      "measuredMs": sum(w["durationMs"] for p in result["parts"] for w in p["windows"]), "sha256": result["sha256"]}))
