"""Four bounded SAM 3 lens-mask probes on saved Tripo renders, not source photos.

No result becomes a production label. PNG masks are requested instead of the
provider's underspecified RLE encoding. Requests and submission reservations are
immutable; an uncertain submission is never retried. Network commands explicitly
read automation/.env through the existing benchmark Client.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw

from qa.provider_benchmark import Client, ROOT, canonical, digest, now, read_json, write_json

OUTPUT = ROOT / "data/render-mask-probes-v1"
RENDERS = ROOT / "data/provider-comparison-v1/render-final"
ENDPOINT = "fal-ai/sam-3/image"
CASES = tuple(f"{product}-{view}" for product in ("oakley", "miu") for view in ("front", "angled"))
SETTINGS = dict(prompt="eyeglass lenses", apply_mask=False, sync_mode=False,
                output_format="png", return_multiple_masks=True, max_masks=3,
                include_scores=True, include_boxes=True)
HEADERS = {"X-Fal-No-Retry": "1", "x-app-fal-disable-fallback": "true"}
COLORS = [(225, 35, 75), (20, 155, 235), (240, 165, 20)]


def immutable_json(path, value):
    if path.exists():
        if read_json(path) != value:
            raise ValueError(f"Immutable receipt differs: {path.name}")
    else:
        write_json(path, value, exclusive=True)


def prepare():
    report_path = RENDERS / "report.json"
    report = read_json(report_path)
    cases = []
    for case in CASES:
        product, view = case.split("-")
        source_case = next(item for item in report["cases"] if item["id"] == product + "-tripo")
        render = next(item for item in source_case["renders"] if item["mode"] == "raw"
                      and item["background"] == "light" and item["view"] == view)
        source = RENDERS / render["filename"]
        raw = source.read_bytes()
        if digest(raw) != render["sha256"]:
            raise ValueError("Render differs from source report")
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if image.format != "PNG":
                raise ValueError("Expected saved PNG render")
        request = dict(schema_version=1, case=case, endpoint=ENDPOINT, settings=SETTINGS,
                       input_kind="existing_raw_tripo_render", image=dict(path=str(source.resolve()),
                       sha256=digest(raw), bytes=len(raw), width=width, height=height),
                       render_report=dict(path=str(report_path.resolve()), sha256=digest(report_path.read_bytes())),
                       model_sha256=source_case["model_sha256"], render_metadata=render,
                       headers=HEADERS, production_accepted=False)
        directory = OUTPUT / case
        immutable_json(directory / "request.json", request)
        cases.append(dict(case=case, request_sha256=digest(canonical(request))))
    immutable_json(OUTPUT / "prepared.json", dict(schema_version=1, max_submissions=4, cases=cases,
        purpose="Exploratory lens masks on controlled Tripo renders; no source-photo or mesh labeling claim",
        documentation="https://fal.ai/models/fal-ai/sam-3/image/api"))
    return dict(state="prepared", cases=list(CASES), maximum_paid_submissions=4)


def verified_request(case):
    if case not in CASES:
        raise ValueError("Case is outside the four-call experiment")
    request = read_json(OUTPUT / case / "request.json")
    prepared = read_json(OUTPUT / "prepared.json")
    entry = next(item for item in prepared["cases"] if item["case"] == case)
    if digest(canonical(request)) != entry["request_sha256"]:
        raise ValueError("Prepared request changed")
    if request["endpoint"] != ENDPOINT or request["settings"] != SETTINGS or request["headers"] != HEADERS:
        raise ValueError("Unsupported probe settings")
    if digest(Path(request["image"]["path"]).read_bytes()) != request["image"]["sha256"]:
        raise ValueError("Input render changed")
    return request


def submit(case, client):
    directory = OUTPUT / case
    request = verified_request(case)
    if (directory / "submission.json").exists():
        return dict(case=case, state="already_submitted")
    if (directory / "submission-reserved.json").exists():
        return dict(case=case, state="submission_uncertain_no_retry")
    if len(list(OUTPUT.glob("*/submission-reserved.json"))) >= 4:
        raise ValueError("Four-call submission limit reached")
    payload = dict(request["settings"])
    payload["image_url"] = "data:image/png;base64," + base64.b64encode(Path(request["image"]["path"]).read_bytes()).decode("ascii")
    write_json(directory / "submission-reserved.json", dict(at=now(), request_sha256=digest(canonical(request)),
               payload_sha256=digest(canonical(payload)), headers=HEADERS), exclusive=True)
    try:
        status, value = client.api("POST", "https://queue.fal.run/" + ENDPOINT, "fal", json=payload, headers=HEADERS)
    except requests.RequestException as exc:
        write_json(directory / "submission-error.json", dict(at=now(), error_type=type(exc).__name__), exclusive=True)
        return dict(case=case, state="submission_uncertain_no_retry")
    write_json(directory / "submission.json", dict(at=now(), http=status, response=value), exclusive=True)
    return dict(case=case, state="submitted" if value.get("request_id") else "rejected",
                http=status, request_id=value.get("request_id"))


def poll(case, client):
    directory = OUTPUT / case
    verified_request(case)
    if (directory / "artifacts.json").exists():
        return dict(case=case, state="downloaded")
    receipt_path = directory / "submission.json"
    if not receipt_path.exists():
        return dict(case=case, state="not_submitted")
    submitted = read_json(receipt_path)["response"]
    if not submitted.get("request_id"):
        return dict(case=case, state="rejected")
    result_path = directory / "result.json"
    if result_path.exists():
        result_receipt = read_json(result_path)
        code, result = result_receipt["http"], result_receipt["response"]
    else:
        code, status = client.api("GET", submitted["status_url"], "fal")
        write_json(directory / "status.json", dict(at=now(), http=code, response=status))
        if code != 200 or status.get("status") != "COMPLETED":
            return dict(case=case, state=status.get("status", "unknown"), http=code)
        code, result = client.api("GET", submitted["response_url"], "fal")
        write_json(result_path, dict(at=now(), http=code, response=result), exclusive=True)
    if code != 200:
        return dict(case=case, state="provider_failed", http=code)
    masks = result.get("masks")
    if not isinstance(masks, list) or len(masks) > 3:
        raise ValueError("Unexpected provider mask list")
    artifacts = []
    for index, mask in enumerate(masks):
        destination = directory / f"mask-{index:02d}-provider.png"
        if destination.exists():
            item = dict(path=str(destination.resolve()), bytes=destination.stat().st_size,
                        sha256=digest(destination.read_bytes()))
        else:
            item = client.download(mask["url"], destination, max_bytes=20 * 1024**2)
        item.update(index=index, provider=mask)
        artifacts.append(item)
    write_json(directory / "artifacts.json", dict(at=now(), masks=artifacts, result_sha256=digest(result_path.read_bytes())), exclusive=True)
    return dict(case=case, state="downloaded", masks=len(artifacts))


def read_binary_mask(path, expected_size):
    with Image.open(path) as image:
        if image.size != expected_size or image.format != "PNG":
            raise ValueError("Mask dimensions or format differ from original render")
        rgba = np.asarray(image.convert("RGBA"))
    if not np.all(rgba[..., 3] == 255):
        raise ValueError("Unexpected alpha mask: do not guess its interpretation")
    if not np.all(rgba[..., :3] == rgba[..., :1]):
        raise ValueError("Provider output is colored, not a binary mask")
    gray = rgba[..., 0]
    values = set(np.unique(gray).tolist())
    if not (values <= {0, 255} or values <= {0, 1}):
        raise ValueError("Provider output is not binary: no implicit threshold")
    return gray != 0


def review(case):
    directory = OUTPUT / case
    request = verified_request(case)
    if not (directory / "artifacts.json").exists():
        return dict(case=case, state="not_downloaded")
    response = read_json(directory / "result.json")["response"]
    artifacts = read_json(directory / "artifacts.json")
    original = Image.open(request["image"]["path"]).convert("RGB")
    source = np.asarray(original)
    overlay = source.astype(np.float64)
    masks, reports = [], []
    for entry in artifacts["masks"]:
        path = Path(entry["path"])
        if digest(path.read_bytes()) != entry["sha256"]:
            raise ValueError("Downloaded mask changed")
        index = entry["index"]
        mask = read_binary_mask(path, original.size)
        masks.append(mask)
        Image.fromarray((mask * 255).astype(np.uint8)).save(directory / f"mask-{index:02d}.png")
        ys, xs = np.where(mask)
        bounds = [int(xs.min()), int(ys.min()), int(xs.max())+1, int(ys.max())+1] if xs.size else None
        metadata = next((item for item in response.get("metadata", []) if item.get("index") == index), {})
        scores, boxes = response.get("scores") or [], response.get("boxes") or []
        score = scores[index] if index < len(scores) else metadata.get("score")
        provider_box = boxes[index] if index < len(boxes) else metadata.get("box")
        width, height = original.size
        derived_box = [(bounds[0]+bounds[2])/(2*width), (bounds[1]+bounds[3])/(2*height),
                       (bounds[2]-bounds[0])/width, (bounds[3]-bounds[1])/height] if bounds else None
        box_error = float(np.max(np.abs(np.asarray(provider_box)-derived_box))) if provider_box and derived_box else None
        color = np.array(COLORS[index], dtype=np.float64)
        overlay[mask] = 0.58 * overlay[mask] + 0.42 * color
        single = source.astype(np.float64)
        single[mask] = 0.58 * single[mask] + 0.42 * color
        Image.fromarray(single.astype(np.uint8)).save(directory / f"overlay-{index:02d}.png")
        reports.append(dict(index=index, pixels=int(mask.sum()), score=score,
                            bounds_xyxy=bounds, provider_box_cxcywh=provider_box,
                            measured_box_cxcywh=derived_box, box_max_error=box_error,
                            binary_png_valid=True, mask_sha256=entry["sha256"]))
    overlay_path = directory / "overlay.png"
    Image.fromarray(overlay.astype(np.uint8)).save(overlay_path)
    union = np.logical_or.reduce(masks) if masks else np.zeros(source.shape[:2], dtype=bool)
    Image.fromarray((union*255).astype(np.uint8)).save(directory / "union-mask.png")
    pairs = [dict(first=i, second=j, overlap_pixels=int((masks[i] & masks[j]).sum()))
             for i in range(len(masks)) for j in range(i+1, len(masks))]
    result = dict(case=case, state="decoded", input_sha256=request["image"]["sha256"],
                  mask_count=len(masks), masks=reports, pair_overlaps=pairs,
                  union_pixels=int(union.sum()), overlay=str(overlay_path.resolve()),
                  production_accepted=False, quality_verdict="requires_visual_inspection",
                  caveat="Image-space proposals on opaque generated mesh renders; no optical or 3D identity claim")
    write_json(directory / "review.json", result)
    return result


def contact_sheet():
    tile_w, tile_h, banner = 480, 360, 35
    sheet = Image.new("RGB", (tile_w*2, (tile_h+banner)*4), "white")
    draw = ImageDraw.Draw(sheet)
    summaries = []
    for row, case in enumerate(CASES):
        request = verified_request(case)
        result = review(case)
        summaries.append(result)
        if result["state"] != "decoded":
            continue
        for column, path in enumerate((request["image"]["path"], result["overlay"])):
            y = row*(tile_h+banner)
            with Image.open(path) as image:
                sheet.paste(image.convert("RGB").resize((tile_w,tile_h)), (column*tile_w,y+banner))
            draw.text((column*tile_w+10,y+10), f"{case} | {'input render' if column == 0 else str(result['mask_count'])+' masks'}", fill="black")
    path = OUTPUT / "contact-sheet.png"
    sheet.save(path)
    report = dict(schema_version=1, cases=summaries, contact_sheet=str(path.resolve()),
                  maximum_paid_submissions=4, reservations=len(list(OUTPUT.glob("*/submission-reserved.json"))),
                  production_accepted=False)
    write_json(OUTPUT / "report.json", report)
    return dict(state="reviewed", contact_sheet=str(path.resolve()), cases=len(summaries))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "submit", "poll", "review"))
    parser.add_argument("--case", action="append", choices=CASES)
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    if args.command == "prepare":
        print(json.dumps(prepare()))
        return
    if args.command == "review":
        print(json.dumps(contact_sheet()))
        return
    if args.env.resolve() != (ROOT / ".env").resolve():
        raise ValueError("This probe reads only the explicit automation/.env")
    client = Client(args.env)
    for case in args.case or CASES:
        try:
            result = submit(case, client) if args.command == "submit" else poll(case, client)
        except Exception as exc:
            result = dict(case=case, state="client_error", error_type=type(exc).__name__)
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
