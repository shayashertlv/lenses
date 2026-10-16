"""Hash-bound labelled maps of the current model, beside the unchanged source photos."""
import json
import copy
from pathlib import Path
import re
import shutil

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import binary_dilation

from ...g3d_host import compare as C
from ...g3d_host.ar_camera import explicit_camera
from ...g3d_host import pose
from .. import procs
from . import arview, look, look_panels
from . import model_map_geometry as G
from . import model_map_labels as L

SCHEMA = "g3d.model_map/1"


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _relative(root, path):
    return str(Path(path).resolve().relative_to(Path(root).resolve())).replace("\\", "/")


def _bound(root, relative, sha=None):
    path = (Path(root) / relative).resolve()
    if not path.is_relative_to(Path(root).resolve()) or not path.is_file():
        raise ValueError("Map evidence is missing or escapes its job")
    if sha is not None and arview.sha256(path) != sha:
        raise ValueError(f"Map evidence changed: {relative}")
    return path


def load_map(root, map_id, expected_sha256=None):
    if not re.fullmatch(r"m-[a-f0-9]{20}", map_id):
        raise ValueError("Invalid model map id")
    path = _bound(root, f"work/model_maps/{map_id}/map.json", expected_sha256)
    manifest = _read(path)
    if manifest.get("schema") != SCHEMA or manifest.get("map_id") != map_id:
        raise ValueError("Invalid map identity")
    for asset in manifest["assets"]:
        _bound(root, asset["path"], asset["sha256"])
    return manifest


def target(manifest, target_id):
    try:
        return manifest["targets"][target_id]
    except KeyError:
        raise ValueError(f"Unknown map target: {target_id}") from None


def _snapshot(root, manifest):
    return _read(_bound(root, manifest["snapshot"]["path"], manifest["snapshot"]["sha256"]))


def _scope(root, manifest, value):
    snapshot=_snapshot(root,manifest)
    fixed=set(value.get("fixed_vertex_ids",[]))
    ids=list(value["target_ids"])
    for key in value.get("fixed_target_ids",[]):
        fixed.update(target(manifest,key)["vertex_ids"])
        if key not in ids:
            ids.append(key)
    computed = G.support(snapshot, manifest["targets"], ids,
                         vertex_weights=value.get("vertex_weights"), fixed_vertex_ids=sorted(fixed))
    if any(k in value and value[k] != computed[k] for k in ("object_id", "affected_polygon_ids", "normal_affected_polygon_ids")):
        raise ValueError("Support object or affected polygons differ from actual map topology")
    return computed


def mark_delivered(root, result, delivered=True):
    """Called by the outer SDK wrapper only after all actual image outputs were delivered."""
    if result.get("status") != "ok" or not result.get("capture_receipt"):
        return
    path = _bound(root, result["capture_receipt"], result["capture_sha256"])
    status = {"schema": "g3d.model_map_delivery/1", "capture_sha256": arview.sha256(path), "delivered": bool(delivered),
              "call_id": result.get("call_id")}
    procs.write_json_atomic(path.with_name(path.stem + "-delivery.json"), status)


def validate_understanding(root, map_id, map_sha256, capture_receipts, operations):
    """Approval needs seen targets AND the exact drawn support, never an expanded later selection."""
    manifest = load_map(root, map_id, map_sha256)
    state_path=Path(root)/"work/job_scene/state.json"
    unseen=set(_read(state_path).get("unseen",[])) if state_path.is_file() else set()
    shown, scopes, files, images, legend = set(), {}, [], [], {}
    for reference in capture_receipts:
        path = _bound(root, reference)
        receipt = _read(path)
        delivery = _read(path.with_name(path.stem + "-delivery.json"))
        if (receipt.get("schema") != "g3d.model_map_capture/1" or receipt.get("map_sha256") != map_sha256 or
                delivery.get("capture_sha256") != arview.sha256(path) or delivery.get("delivered") is not True or
                delivery.get("call_id") in unseen):
            raise ValueError("Understanding requires successfully delivered images bound to this exact map")
        for asset in receipt["assets"]:
            _bound(root, asset["path"], asset["sha256"])
            files.append(asset)
        shown.update(receipt["annotated_target_ids"])
        for row in receipt.get("target_legend", []):
            if row["target_id"] not in receipt["annotated_target_ids"]:
                raise ValueError("A legend entry names a target absent from the shown map")
            prior = legend.get(row["target_id"])
            if prior is not None and prior != row:
                raise ValueError("The same target has conflicting owner labels in the shown maps")
            if any(old["label"] == row["label"] and old["target_id"] != row["target_id"] for old in legend.values()):
                raise ValueError("A displayed label cannot name two targets")
            legend[row["target_id"]] = row
        scopes.update({G.digest(s):s for s in receipt["support_scopes"]})
        files.extend([{"path": _relative(root, path), "sha256": arview.sha256(path)},
                      {"path": _relative(root, path.with_name(path.stem + "-delivery.json")),
                       "sha256": arview.sha256(path.with_name(path.stem + "-delivery.json"))}])
        images.extend(receipt["images"])
    resolved=[]
    for original in operations:
        operation=copy.deepcopy(original)
        ids = operation["target_ids"]
        if not ids or not set(ids).issubset(shown):
            raise ValueError("An operation names a target that the delivered map did not show")
        if operation.get("scope_ref_sha256"):
            reference=operation["scope_ref_sha256"]
            if reference not in scopes or scopes[reference]["target_ids"] != ids:
                raise ValueError("Scope reference must match the exact shown targets and influence")
            operation["scope"]=copy.deepcopy(scopes[reference])
        value = {**operation["scope"], "target_ids": ids}
        if "target_ids" in operation["scope"] and operation["scope"]["target_ids"] != ids:
            raise ValueError("Operation and support target IDs differ")
        canonical = _scope(root, manifest, value)
        if G.digest(canonical) not in scopes:
            raise ValueError("The operation's exact influence/support was not drawn in the delivered map")
        operation["scope"]=canonical
        resolved.append(operation)
    return {"snapshot_path": str(_bound(root, manifest["snapshot"]["path"], manifest["snapshot"]["sha256"])),
            "source": manifest["source"], "evidence_files": manifest["assets"] + files,
            "images": images, "map_path": f"work/model_maps/{map_id}/map.json", "map_sha256": map_sha256,
            "annotated_target_ids": sorted(shown),"operations":resolved,"legend":list(legend.values())}


def _font(size=18):
    for name in ("C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _asset_points(obj, camera):
    points = np.asarray(obj["vertices_world_mm"], float)
    parsed = pose.parse_pose(camera.get("temple_pose"))
    if obj["role"] == "temple" and obj["side"] in parsed:
        theta, axis = parsed[obj["side"]]
        points = pose.rot_about_hinge(points, obj["side"], theta, axis)
    return points


def _photo_render(root, export, rows, directory, timeout_s):
    node = shutil.which("node")
    if not node:
        raise ValueError("Node.js unavailable for the AR renderer")
    cameras = directory / "cameras.json"
    procs.write_json_atomic(cameras, {"schema": "arview.cameras/1", "cameras": [r["camera"] for r in rows]})
    out = directory / "render"
    code, timed_out = arview.run_capture([node, str(arview.ARVIEW / "bin/arview.mjs"), "capture", export["path"],
        "--cameras", str(cameras), "--no-face", "--fixed-scale", "--lensenv", str(arview.LENS_ENV_INTENSITY),
        "--out", str(out)], directory, timeout_s=timeout_s)
    if timed_out or code not in (0, 3):
        raise ValueError(f"Map photo capture failed (exit {code}, timeout {timed_out})")
    path = out / "report.json"
    look.verified_report(root, {"report": str(path), "report_sha256": arview.sha256(path),
                               "export_sha256": export["sha256"]}, export["sha256"])
    for row in rows:
        row["drawing"] = str(out / (row["camera"]["id"] + "__beauty.png"))
        row["part_id"] = str(out / (row["camera"]["id"] + "__part-id.png"))
    return path


def _select(manifest, focus, kind):
    kinds = {"parts": "object", "faces": "face_region", "edges": "edge_chain", "points": "point"}
    if kind not in kinds:
        raise ValueError("kind must be parts, faces, edges or points")
    if focus:
        explicit = [target(manifest, key) for key in focus]
        selected = []
        for value in manifest["targets"].values():
            if value["kind"] != kinds[kind]:
                continue
            for base in explicit:
                if value["id"] == base["id"] or (base["kind"] != kinds[kind] and value["object_id"] == base["object_id"] and
                    (base["kind"] == "object" or set(value["vertex_ids"]).intersection(base["vertex_ids"]))):
                    selected.append(value)
                    break
        return selected
    return [t for t in manifest["targets"].values() if t["kind"] == kinds[kind]]


def _annotate(row, snapshot, selected, geometry, source_obj, source_poly, scopes, directory, page,
              display_labels=None, descriptions=None):
    display_labels = {} if display_labels is None else display_labels
    descriptions = {} if descriptions is None else descriptions
    camera = row["camera"]
    photo = Image.open(row["photo"]).convert("RGB")
    drawing = Image.open(row["drawing"]).convert("RGB")
    ids = np.asarray(Image.open(row["part_id"]).convert("RGB"))
    visible = np.max(ids, axis=2) > 24
    posed = look_panels.posed_geometry(geometry, camera.get("temple_pose"))
    hits = G.first_hits(posed, camera, binary_dilation(visible, iterations=2))
    hit_obj = np.full(hits.shape, "", object)
    hit_poly = np.full(hits.shape, -1, int)
    okay = hits >= 0
    hit_obj[okay], hit_poly[okay] = source_obj[hits[okay]], source_poly[hits[okay]]
    objects = {o["id"]: o for o in snapshot["objects"]}
    counts = {}
    for oid,obj in objects.items():
        pixels=hit_poly[hit_obj==oid]
        counts[oid]=np.bincount(pixels[pixels>=0],minlength=len(obj["polygons"]))
    available = []
    for value in selected:
        count=int((hit_obj==value["object_id"]).sum()) if value["kind"]=="object" else int(counts[value["object_id"]][value["polygon_ids"]].sum())
        # Boundary points/edges can be continuously visible even when their tiny
        # adjacent polygons contain no native pixel centre. Their own rays decide.
        if not count and value["kind"] in ("object", "face_region"):
            continue
        if value["kind"] in ("point", "edge_chain"):
            obj = objects[value["object_id"]]
            points = _asset_points(obj,camera)
            if value["kind"] == "point":
                samples = points[value["vertex_ids"]]
            else:
                projected,_ = G.project(points,camera)
                samples=[]
                for ei in value["edge_ids"]:
                    a,b=obj["edges"][ei]
                    length=float(np.linalg.norm(projected[b]-projected[a]))
                    count=max(2,min(10000,int(length/2)+1)) if np.isfinite(length) else 2
                    samples.extend(points[a]*(1-t)+points[b]*t for t in np.linspace(0,1,count))
                samples=np.asarray(samples)
            visible_samples=G.visible_points(samples,camera,posed)
            xy,_=G.project(samples[visible_samples],camera)
            mask=np.zeros(hits.shape,bool)
            for x,y in np.rint(xy).astype(int):
                if 0<=x<mask.shape[1] and 0<=y<mask.shape[0]:
                    mask[y,x]=True
        else:
            mask=None
        if mask is None or mask.any():
            available.append((count if mask is None else int(mask.sum()), value, mask))
    available.sort(key=lambda item: (-item[0], item[1]["id"]))
    # A fixed label-cell grid guarantees readable nonoverlapping tags even when
    # every target projects to one small cluster. Smaller images get more pages.
    label_cell_w, label_cell_h = 110, 38
    columns = max(1, camera["width"] // label_cell_w)
    rows_count = max(1, (camera["height"] - 8) // label_cell_h)
    page_size = min(24, columns * rows_count)
    shown = available[page * page_size:(page + 1) * page_size]
    shown=[(count,value,mask if mask is not None else ((hit_obj==value["object_id"]) &
            (True if value["kind"]=="object" else np.isin(hit_poly,value["polygon_ids"]))))
           for count,value,mask in shown]
    if scopes is None:
        scopes=[]
    annotated = drawing.convert("RGBA")
    overlay = Image.new("RGBA", drawing.size)
    draw = ImageDraw.Draw(overlay)
    labels, shown_ids, drawn_scopes, normal_masks, target_legend, occupied = [], [], [], [], [], set()
    label_draws = []
    for index, (_, value, mask) in enumerate(shown):
        color = [(0, 114, 178), (213, 94, 0), (0, 158, 115), (204, 121, 167), (230, 159, 0)][index % 5]
        obj = objects[value["object_id"]]
        points = _asset_points(obj, camera)
        xy, _ = G.project(points, camera)
        if value["kind"] in ("object", "face_region"):
            tint = np.zeros((*mask.shape, 4), np.uint8)
            tint[mask] = (*color, 55)
            overlay.alpha_composite(Image.fromarray(tint))
        else:
            for y,x in zip(*np.where(mask)):
                radius=5 if value["kind"]=="point" else 2
                draw.ellipse((x-radius,y-radius,x+radius,y+radius),fill=(*color,255))
        yy, xx = np.where(mask)
        centroid = np.array([np.mean(xx), np.mean(yy)])
        j = np.argmin((xx-centroid[0])**2 + (yy-centroid[1])**2)
        x, y = int(xx[j]), int(yy[j])
        label_row = L.allocate(value, obj, display_labels, descriptions)
        target_legend.append(label_row)
        label = label_row["label"]
        label_font = _font(28)
        width=draw.textbbox((0,0),label,font=label_font)[2]
        slots = [(col,row_number) for col in range(columns) for row_number in range(rows_count)
                 if (col,row_number) not in occupied]
        col,row_number = min(slots,key=lambda slot:(4+slot[0]*label_cell_w+width/2-x)**2+
                           (4+slot[1]*label_cell_h+14-y)**2)
        occupied.add((col,row_number))
        lx,ly = 4+col*label_cell_w,4+row_number*label_cell_h
        box = draw.textbbox((lx,ly), label, font=label_font)
        label_draws.append((x,y,lx,ly,width,box,label,color))
        shown_ids.append(value["id"])
        labels.append({"id": value["id"], **label_row, "font_px":28, "aliases": value["aliases"], "visible_pixels": int(mask.sum()),
                       "source_editable": value["source_editable"],"label_box":[max(0,box[0]-3),max(0,box[1]-2),min(camera["width"],box[2]+3),min(camera["height"],box[3]+2)]})
    # Every explicitly displayed influence is drawn using its actual weighted vertices.
    for scope in scopes:
        if not set(scope["target_ids"]).issubset(shown_ids):
            continue
        obj = objects[scope["object_id"]]
        normal_mask = (hit_obj == scope["object_id"]) & np.isin(hit_poly, scope["normal_affected_polygon_ids"])
        normal_masks.append(normal_mask)
        tint = np.zeros((*normal_mask.shape, 4), np.uint8)
        tint[normal_mask] = (255, 190, 0, 65)
        overlay.alpha_composite(Image.fromarray(tint))
        xy, _ = G.project(_asset_points(obj, camera), camera)
        fixed = set(scope["fixed_vertex_ids"])
        for weight in scope["vertex_weights"]:
            vi, w = weight["vertex_id"], weight["weight"]
            x, y = xy[vi]
            if 0 <= x < camera["width"] and 0 <= y < camera["height"]:
                color = (30,30,30,255) if vi in fixed else (180,0,100,round(70+185*w))
                draw.ellipse((x-2,y-2,x+2,y+2), fill=color)
        drawn_scopes.append(scope)
    # All leaders and support marks are below every opaque text box.
    for x,y,lx,ly,width,box,label,color in label_draws:
        draw.line((x,y,lx+width/2,ly+14),fill=(*color,255),width=2)
    for x,y,lx,ly,width,box,label,color in label_draws:
        draw.rectangle((box[0]-3,box[1]-2,box[2]+3,box[3]+2), fill=(255,255,255,255))
        draw.text((lx,ly),label,font=_font(28),fill=(*color,255))
    annotated = Image.alpha_composite(annotated, overlay).convert("RGB")
    full = Image.new("RGB", (photo.width*2,photo.height+44), "white")
    full.paste(photo,(0,44)); full.paste(annotated,(photo.width,44))
    legend = ("magenta: movement, black: fixed, amber: normal influence" if scopes
              else "numbered actual-mesh targets")
    ImageDraw.Draw(full).text((12,10), f"{row['ref']} | PHOTO | MODEL — {legend}", font=_font(), fill="black")
    full = L.append_legend(full, target_legend, _font(22))
    pair = directory / (row["ref"] + "-full.png")
    full.save(pair)
    # Native crop tiles, not resized substitutes. Full native pairs remain separately available.
    images = []
    if shown:
        union = np.logical_or.reduce([entry[2] for entry in shown] + normal_masks)
        yy, xx = np.where(union)
        box = [max(0,int(xx.min())-35), max(0,int(yy.min())-35), min(photo.width,int(xx.max())+36), min(photo.height,int(yy.max())+36)]
        tiles=list(look_panels._tiles(box, side=500))
        for label in labels:
            a,b,c,d=label["label_box"]
            if not any(t[0]<=a and t[1]<=b and t[2]>=c and t[3]>=d for t in tiles):
                x0=max(0,min(photo.width-500,(a+c)//2-250))
                y0=max(0,min(photo.height-500,(b+d)//2-250))
                tiles.append([x0,y0,min(photo.width,x0+500),min(photo.height,y0+500)])
        for i, tile in enumerate(tiles):
            x0,y0,x1,y1 = tile
            piece = Image.new("RGB", ((x1-x0)*2,y1-y0+35),"white")
            piece.paste(photo.crop(tile),(0,35)); piece.paste(annotated.crop(tile),(x1-x0,35))
            ImageDraw.Draw(piece).text((7,7), f"{row['ref']} | PHOTO first | MODEL map | native tile {i+1}", font=_font(15), fill="black")
            # Each label's legend is supplied as its own image when necessary;
            # native model pixels and 28px tags must never shrink to fit long text.
            path = directory / f"{row['ref']}-tile-{i:03d}.png"
            piece.save(path)
            images.append({"path": str(path), "sha256": arview.sha256(path), "view": f"{row['ref']} map native tile {i+1}: {','.join(r['label'] for r in target_legend)}"})
        for i in range(0,len(target_legend),4):
            entries = target_legend[i:i+4]
            legend_image = L.append_legend(Image.new("RGB",(1000,1),"white"),entries,_font(24))
            path = directory / f"{row['ref']}-legend-{i:03d}.png"
            legend_image.save(path)
            images.append({"path":str(path),"sha256":arview.sha256(path),
                           "view":f"{row['ref']} legend: " + "; ".join(f"{entry['label']} = {entry['description']}" for entry in entries)})
    return {"ref": row["ref"], "full_pair": str(pair), "full_pair_sha256": arview.sha256(pair),
            "labels": labels, "annotated_target_ids": shown_ids, "support_scopes": drawn_scopes,
            "legend": legend,"target_legend":target_legend,
            "visible_target_count": len(available), "page": page,"page_size":page_size,
            "pages": (len(available)+page_size-1)//page_size,
            "images": images, "camera": camera, "native_photo_shape": [photo.height,photo.width]}


def capture(scene, call_id, *, photos=None, focus=None, kind="faces", map_id=None,
            support_scopes=None, page=0, timeout_s=280, label_descriptions=None):
    root = Path(scene.root).resolve()
    if type(page) is not int or page < 0:
        raise ValueError("page must be a nonnegative integer")
    export = scene.seeing.export_head(call_id=call_id)
    if export.get("status") != "exported":
        raise ValueError("A current export is required for the model map")
    bs = C.load_basis(root)
    if map_id is None:
        map_id = "m-" + G.digest([call_id, export["fingerprint"]])[:20]
        directory = root / "work/model_maps" / map_id
        directory.mkdir(parents=True, exist_ok=False)
        result = scene.s.call_read("labelled_model_map", "model_map_snapshot", {"path": str(directory/"snapshot.json")},
                                   deadline_s=120, call_id=call_id)
        if result.get("status") != "ok" or result.get("result",{}).get("status") != "ok":
            raise ValueError(f"Actual mesh snapshot failed: {result.get('status')}")
        snap = result["result"]
        snapshot = _read(snap["path"])
        if snapshot["fingerprint"] != export["fingerprint"]:
            raise ValueError("The current export and actual mesh snapshot differ")
        cat = G.catalogue(snapshot)
        assets = [{"path": _relative(root,snap["path"]), "sha256": snap["sha256"]},
                  {"path": _relative(root,export["path"]), "sha256": export["sha256"]},
                  {"path": _relative(root,bs.dir/"basis.json"), "sha256": arview.sha256(bs.dir/"basis.json")},
                  {"path": _relative(root,bs.dir/"cameras.json"), "sha256": arview.sha256(bs.dir/"cameras.json")}]
        assets += [{"path": _relative(root,bs.photo_path(rid)), "sha256": bs.refs[rid]["photo_sha256"]} for rid in bs.refs]
        manifest = {"schema": SCHEMA, "map_id": map_id,
                    "source": {"fingerprint": export["fingerprint"], "export_sha256": export["sha256"], "checkpoint": export["checkpoint"]},
                    "basis": bs.id, "snapshot": assets[0], "assets": assets, **cat}
        (directory/"map.json").write_text(json.dumps(manifest,separators=(",",":"),allow_nan=False),encoding="utf-8")
    else:
        manifest = load_map(root,map_id)
        directory = root/"work/model_maps"/map_id
        if manifest["source"]["fingerprint"] != export["fingerprint"] or manifest["source"]["export_sha256"] != export["sha256"]:
            raise ValueError("The model changed; make a new map and confirm its new targets")
        if manifest["basis"] != bs.id:
            raise ValueError("The frozen camera basis changed; make a new map")
        snapshot = _snapshot(root,manifest)
    labels_path = directory / "display-labels.json"
    display_labels = _read(labels_path) if labels_path.exists() else {}
    descriptions = L.validate_descriptions(label_descriptions, manifest["targets"], display_labels)
    if focus:
        focus = [L.resolve(key, manifest["targets"], display_labels) for key in focus]
    selected = _select(manifest,focus,kind)
    scopes = None
    if support_scopes:
        scopes = [_scope(root,manifest,s) for s in support_scopes]
        for scope in scopes:
            for key in scope["target_ids"]:
                value = target(manifest,key)
                if value not in selected:
                    selected.append(value)
    capture_id = "v-" + G.digest([call_id,kind,focus,page,support_scopes])[:20]
    output = directory/"captures"/capture_id
    output.mkdir(parents=True,exist_ok=False)
    wanted = bs.usable_refs() if photos is None else [C.ref_id(name) for name in photos]
    wanted = list(dict.fromkeys(wanted))
    wanted.sort(key=lambda rid: bs.refs[rid].get("view_kind") != "front")
    rows = []
    for i,rid in enumerate(wanted):
        photo = bs.photo(rid)
        camera = explicit_camera(bs.camera(rid),bs.frame,bs.shape(rid),name=f"photo-{i:03d}",
                                  background=look.border_colour(photo)["hex"],size_px=min(4096,max(photo.shape[:2])),temple_pose=bs.pose(rid))
        if camera["meta"]["pixel_scale"] != 1:
            raise ValueError("This photo exceeds the renderer's native resolution; native tiled capture is required")
        path = output/f"{rid}-photo.png"
        Image.fromarray(photo).save(path)
        rows.append({"ref": rid,"camera":camera,"photo":str(path),"photo_sha256":bs.refs[rid]["photo_sha256"]})
    report = _photo_render(root,export,rows,output,timeout_s)
    geometry = look_panels.load_geometry(export["path"])
    source_obj,source_poly,mapping = G.export_correspondence(snapshot,geometry)
    views = [_annotate(row,snapshot,selected,geometry,source_obj,source_poly,scopes,output,page,
                      display_labels,descriptions) for row in rows]
    procs.write_json_atomic(labels_path, display_labels)
    shown = sorted({key for row in views for key in row["annotated_target_ids"]})
    drawn = {G.digest(s):s for row in views for s in row["support_scopes"]}
    assets = [{"path":_relative(root,p),"sha256":arview.sha256(p)} for p in sorted(output.rglob("*")) if p.is_file()]
    map_sha = arview.sha256(directory/"map.json")
    images = [image for row in views for image in row["images"]]
    target_legend = [display_labels[key] for key in shown]
    objects={o["id"]:o for o in snapshot["objects"]}
    details=[{**target(manifest,key),"positions_world_mm":[objects[target(manifest,key)["object_id"]]["vertices_world_mm"][i]
              for i in target(manifest,key)["vertex_ids"]]} for key in shown]
    suggested=[G.support(snapshot,manifest["targets"],[key]) for key in shown if target(manifest,key)["source_editable"]]
    receipt = {"schema":"g3d.model_map_capture/1","map_id":map_id,"map_sha256":map_sha,
               "source":manifest["source"],"annotated_target_ids":shown,"support_scopes":list(drawn.values()),
               "views":views,"images":images,"assets":assets,"export_correspondence":mapping,"target_legend":target_legend,
               "ar_report":_relative(root,report),"read_only":True}
    receipt_path = output/"capture.json"
    procs.write_json_atomic(receipt_path,receipt)
    return {"status":"ok","schema":SCHEMA,"map_id":map_id,"map_sha256":map_sha,
            "manifest_path":str(directory/"map.json"),"capture_receipt":_relative(root,receipt_path),
            "capture_sha256":arview.sha256(receipt_path),"annotated_target_ids":shown,
            "summary":[f"{r['label']} = {r['description']}" for r in target_legend],"target_legend":target_legend,
            "target_details":details,"suggested_unit_scopes":suggested,
            "scope_refs":[{"sha256":key,"target_ids":scope["target_ids"],"weighted_vertices":len(scope["vertex_weights"]),
                           "affected_polygons":len(scope["affected_polygon_ids"]),
                           "normal_affected_polygons":len(scope["normal_affected_polygon_ids"])} for key,scope in drawn.items()],
            "support_scopes":list(drawn.values()),"full_size_pairs":[row["full_pair"] for row in views],
            "views":views,"image_delivery":"separate","images":images,
            "notes":["Photo first; frozen camera/opening; actual mesh positions, no model changes.",
                     "Short labels stay the same within this map; each legend binds a label to an exact target ID. Operation IDs use those exact IDs.",
                     "Amber shows the separate normal-influence ring; its vertex positions remain fixed unless a movement weight also applies.",
                     "Support dots show all influenced vertices, including occluded vertices; black dots are fixed. Face/edge labels use first-hit visibility."]}


def tool_for(scene, tool, call):
    from agents.tool_context import ToolContext

    @tool("labelled_model_map")
    async def labelled_model_map(ctx: ToolContext, photos: list[str] | None = None, focus: list[str] | None = None,
                                 kind: str = "faces", map_id: str | None = None, supports_json: str | None = None,
                                 page: int = 0, label_descriptions_json: str | None = None):
        """Show numbered actual model parts/faces/edges/points beside the same full-size photo.
        Short labels O1/T1/E1/P1 identify parts/surfaces/borders/corners; a plain legend names each one.
        Start automatically, then focus displayed short labels or exact target IDs. Reuse map_id to keep labels.
        Verified temple facet/edge/corner names accompany current actual geometry, even after manual edits.
        For understand, draw the exact operation support (weights/fixed vertices) via supports_json before
        confirming it. The catalogue and full-size pairs remain available losslessly; native tiles are shown.
        No geometry, photos, frozen cameras or temple openings change.

        Args:
            photos: Frozen reference IDs or original filenames; null uses all usable photos, front first.
            focus: Previously displayed short labels or target IDs; null discovers parts/features automatically.
            kind: parts, faces, edges or points.
            map_id: Existing map ID to keep the same numbers; null creates a fresh map.
            supports_json: Optional JSON list. A concise {target_ids, fixed_target_ids} descriptor selects those
                shown regions at unit weight and fixes the shown point/edge targets at zero; omitted fixed IDs
                means none. Or supply exact object_id, vertex_weights, fixed_vertex_ids, affected_polygon_ids.
                All support stays within its targets. Successful images return scope_refs; understand_finish
                can reference that SHA instead of repeating the full vertex arrays. target_details and suggested
                scopes are losslessly available through read_report; a suggestion is not approval evidence until drawn.
            page: Zero-based page of at most 24 visible labels per photo; complete catalogue is retained.
            label_descriptions_json: Optional JSON object mapping target IDs or known short labels to plain words
                describing the feature and side. Supply before first showing that target; descriptions then stay
                fixed for this map. Automatic descriptions use verified names and the part's role and side.
        """
        supports = json.loads(supports_json) if supports_json else None
        return await call(ctx,"labelled_model_map",lambda cid:capture(scene,cid,photos=photos,focus=focus,
            kind=kind,map_id=map_id,support_scopes=supports,page=page,
            label_descriptions=json.loads(label_descriptions_json) if label_descriptions_json else None))
    return labelled_model_map
