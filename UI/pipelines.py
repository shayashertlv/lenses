"""
Background pipelines for Smart Fit and Free Search modes.
"""

import base64
import io
import json
import os
import random
import tempfile
import threading
from pathlib import Path

import numpy as np
from PIL import Image

from UI.config import (
    CATALOG_DIR,
    CATALOG_JSON,
    EMBEDDINGS_NPY,
    EMBEDDING_INDEX_JSON,
    FS_EMBEDDING_MODEL,
    FS_MIN_SIMILARITY,
    FS_MODEL_MAP,
    FS_DEFAULT_MODEL,
    FS_MAX_IMAGE_DIM,
    sessions,
)


# ══════════════════════════════════════════════════════════════════════════════
# SMART FIT PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

def run_pipeline(session_id: str, portrait_bytes: bytes, filename: str):
    """
    Runs the full face_analysis pipeline in a background thread.

    Steps mirror face_analysis/main.py:
      1. FaceAnalyzer.analyze()
      2. InventoryMatcher.match(top_k=3)
      3. virtual_tryon() x3 in parallel
    """
    sess = sessions[session_id]

    ext = Path(filename).suffix or ".jpg"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.write(portrait_bytes)
    tmp.close()
    portrait_path = tmp.name

    sess["portrait_b64"] = base64.b64encode(portrait_bytes).decode("ascii")

    try:
        from config import get_api_key, DEFAULT_GENERATION_MODEL
        api_key = get_api_key()
    except Exception as e:
        sess["status"] = "error"
        sess["error"] = str(e)
        return

    # STEP 1: FaceAnalyzer.analyze()
    sess["stage"] = "analyzing"
    try:
        from face_analyzer import FaceAnalyzer
        analyzer = FaceAnalyzer(api_key=api_key)
        analysis_result = analyzer.analyze(portrait_path)
    except Exception as e:
        sess["status"] = "error"
        sess["error"] = f"Analysis error: {e}"
        _cleanup(portrait_path)
        return

    if not analysis_result.success:
        sess["status"] = "error"
        sess["error"] = f"Analysis failed: {analysis_result.error}"
        _cleanup(portrait_path)
        return

    analysis = analysis_result.analysis
    sess["analysis_seconds"] = round(analysis_result.elapsed_seconds, 1)
    sess["face_insights"] = analysis.get("face_insights", [])
    sess["face_summary"] = analysis.get("face_summary", {})

    # STEP 2: InventoryMatcher.match()
    sess["stage"] = "matching"
    try:
        from inventory_matcher import InventoryMatcher
        matcher = InventoryMatcher(CATALOG_DIR, api_key)
        recommended_tags = analysis["glasses_recommendation"]["recommended_tags"]
        match_result = matcher.match(recommended_tags, top_k=3)
    except Exception as e:
        sess["status"] = "error"
        sess["error"] = f"Matching error: {e}"
        _cleanup(portrait_path)
        return

    if not match_result.success:
        sess["status"] = "error"
        sess["error"] = f"No matching products: {match_result.error}"
        _cleanup(portrait_path)
        return

    matches = match_result.matches
    sess["num_options"] = len(matches)

    for i, (product, score) in enumerate(matches):
        glasses_path = matcher.get_product_image_path(product)
        with open(glasses_path, "rb") as f:
            product_b64 = base64.b64encode(f.read()).decode("ascii")

        p = product["tags"]["product"]
        sess[f"opt{i}"] = {
            "name": product["name"],
            "brand": p["brand"],
            "model": p["model_name"],
            "price": p["price"],
            "currency": p["currency"],
            "score": round(score, 3),
            "shape": product["tags"]["frame"]["shape"],
            "material": product["tags"]["frame"]["material"],
            "color": ", ".join(product["tags"]["frame"]["color"]),
            "product_b64": product_b64,
            "tryon_status": "pending",
            "tryon_b64": None,
            "tryon_error": None,
        }

    # STEP 3: virtual_tryon() x3 in parallel
    sess["stage"] = "tryon"
    from tryon_engine import virtual_tryon

    def do_tryon(idx: int):
        product, _ = matches[idx]
        glasses_path = matcher.get_product_image_path(product)
        sess[f"opt{idx}"]["tryon_status"] = "generating"
        try:
            tr = virtual_tryon(
                portrait_path=portrait_path,
                glasses_image_path=glasses_path,
                analysis=analysis,
                matched_product=product,
                model_alias=DEFAULT_GENERATION_MODEL,
                api_key=api_key,
            )
        except Exception as e:
            sess[f"opt{idx}"]["tryon_status"] = "error"
            sess[f"opt{idx}"]["tryon_error"] = str(e)
            return

        if tr.success and tr.image_bytes:
            sess[f"opt{idx}"]["tryon_b64"] = base64.b64encode(
                tr.image_bytes
            ).decode("ascii")
            sess[f"opt{idx}"]["tryon_status"] = "done"
        else:
            sess[f"opt{idx}"]["tryon_status"] = "error"
            sess[f"opt{idx}"]["tryon_error"] = tr.error or "No image returned"

    threads = [
        threading.Thread(target=do_tryon, args=(i,), daemon=True)
        for i in range(len(matches))
    ]
    for t in threads:
        t.start()

    threads[0].join()
    sess["stage"] = "primary_ready"

    for t in threads[1:]:
        t.join()

    sess["stage"] = "done"
    sess["status"] = "done"
    _cleanup(portrait_path)


# ══════════════════════════════════════════════════════════════════════════════
# FREE SEARCH PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

def _build_search_description(prefs: dict) -> str:
    """
    Convert UI preference selections into a natural-language description
    optimised for embedding-based semantic search against the catalog.

    Only includes properties the user actually selected (non-empty values).
    """
    parts = []

    # Frame
    frame_parts = []
    if prefs.get("frame_shape"):
        frame_parts.append(f"{prefs['frame_shape']} shape")
    if prefs.get("frame_color"):
        frame_parts.append(f"{prefs['frame_color']} color")
    if prefs.get("frame_material"):
        frame_parts.append(f"{prefs['frame_material']} material")
    if prefs.get("frame_thickness"):
        frame_parts.append(f"{prefs['frame_thickness']} thickness")
    if prefs.get("rim_type"):
        frame_parts.append(prefs["rim_type"])
    if frame_parts:
        parts.append("Glasses with a " + ", ".join(frame_parts) + " frame.")

    # Lenses
    lens_parts = []
    if prefs.get("lens_type"):
        lens_parts.append(prefs["lens_type"])
    if prefs.get("lens_size"):
        lens_parts.append(f"{prefs['lens_size']} size")
    if lens_parts:
        parts.append(" ".join(lens_parts) + " lenses.")

    # Style
    style_parts = []
    if prefs.get("aesthetic"):
        style_parts.append(f"{prefs['aesthetic']} style")
    if prefs.get("gender"):
        style_parts.append(f"for {prefs['gender']}")
    if prefs.get("occasion"):
        style_parts.append(f"suitable for {prefs['occasion']} wear")
    if style_parts:
        parts.append(", ".join(style_parts) + ".")

    if not parts:
        return "Versatile everyday glasses, modern style."

    return " ".join(parts)


def _fs_load_image_as_part(path: str):
    """Load image, resize if needed, return a google.genai Part."""
    from google.genai import types

    img = Image.open(path)
    max_side = max(img.width, img.height)
    if max_side > FS_MAX_IMAGE_DIM:
        img.thumbnail((FS_MAX_IMAGE_DIM, FS_MAX_IMAGE_DIM), Image.LANCZOS)

    fmt = img.format
    if fmt is None:
        ext = Path(path).suffix.lower()
        fmt = {".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP", ".png": "PNG"}.get(ext, "PNG")

    mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(fmt, "image/png")

    if fmt == "JPEG" and img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return types.Part.from_bytes(data=buf.getvalue(), mime_type=mime)


def _fs_build_tryon_prompt(product: dict) -> str:
    """Build virtual try-on prompt from product tags."""
    tags = product["tags"]
    frame = tags["frame"]
    lenses = tags["lenses"]

    def _join(v):
        return ", ".join(v) if isinstance(v, list) else str(v)

    frame_colors = _join(frame["color"])
    lens_types = _join(lenses["type"])
    lens_colors = _join(lenses["color"])

    return f"""I am providing two images:
- IMAGE 1 (first image): A portrait photograph of a person who wants to try on glasses.
- IMAGE 2 (second image): A product photograph of glasses/eyewear to be placed on the person.

YOUR TASK: Create a new version of IMAGE 1 (the portrait) where the person is wearing the glasses shown in IMAGE 2. The output must look like a real photograph.

GLASSES DETAILS (from IMAGE 2):
- Frame: {frame["shape"]} shape, {frame["material"]} material, {frame_colors} color, {frame["thickness"]} thickness, {frame["finish"]} finish, {frame["rim_type"]}
- Lenses: {lens_types} type, {lens_colors} color, {lenses["size"]} size, {lenses["shape"]} shape

GLASSES PLACEMENT RULES:
- Position the glasses naturally on the person's face — bridge on the nose, temples toward the ears
- Match the person's face angle, tilt, and perspective exactly
- Scale proportionally to the person's face
- Temple arms should follow the natural path behind/over the ears

CRITICAL PRESERVATION RULES:
- Face, skin tone, expression, makeup must remain IDENTICAL
- Eyes visible through lenses at appropriate opacity for {lens_types} lenses with {lens_colors} tint
- Hair, clothing, accessories, background, lighting must remain IDENTICAL
- Photo composition, framing, angle, resolution must remain IDENTICAL

REALISM REQUIREMENTS:
- Add natural shadows where the frame touches the face
- Lenses should show appropriate reflections for the lighting
- The glasses must have physical weight and presence — not a flat overlay
- Faithfully reproduce the exact frame design from IMAGE 2

OUTPUT: Return ONLY the edited portrait with the glasses applied. Maintain EXACT same dimensions and quality as IMAGE 1."""


def _fs_virtual_tryon(portrait_path: str, glasses_path: str, product: dict,
                      api_key: str) -> dict:
    """
    Run a virtual try-on. Returns dict with keys:
      success, image_bytes, error
    """
    from google import genai
    from google.genai import types

    model_name = FS_MODEL_MAP[FS_DEFAULT_MODEL]
    prompt = _fs_build_tryon_prompt(product)

    try:
        portrait_part = _fs_load_image_as_part(portrait_path)
        glasses_part = _fs_load_image_as_part(glasses_path)
    except Exception as e:
        return {"success": False, "image_bytes": None, "error": f"Image load error: {e}"}

    client = genai.Client(api_key=api_key)

    for attempt in range(2):
        p = prompt if attempt == 0 else prompt + "\n\nReturn ONLY the edited image, no text."
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[p, portrait_part, glasses_part],
                config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
            )
        except Exception as e:
            return {"success": False, "image_bytes": None, "error": str(e)}

        if not response.candidates:
            return {"success": False, "image_bytes": None,
                    "error": "No response. May have been blocked by safety filters."}

        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                return {"success": True, "image_bytes": part.inline_data.data, "error": None}

        # No image on first attempt — retry
        if attempt == 0:
            continue

    return {"success": False, "image_bytes": None, "error": "Model did not return an image."}


def run_free_search_pipeline(session_id: str, portrait_bytes: bytes,
                             filename: str, preferences: dict):
    """
    Free search pipeline — background thread.

    Steps:
      1. Build search description from UI preferences
      2. Embed + cosine-similarity search against catalog
      3. virtual_tryon() x3 in parallel (primary first)
    """
    sess = sessions[session_id]

    # Save portrait to temp file
    ext = Path(filename).suffix or ".jpg"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.write(portrait_bytes)
    tmp.close()
    portrait_path = tmp.name

    sess["portrait_b64"] = base64.b64encode(portrait_bytes).decode("ascii")

    # API key (use face_analysis config since it's in sys.path)
    try:
        from config import get_api_key
        api_key = get_api_key()
    except Exception as e:
        sess["status"] = "error"
        sess["error"] = str(e)
        return

    # ── STEP 1: Build description from preferences ───────────────────────
    sess["stage"] = "searching"
    search_text = _build_search_description(preferences)
    print(f"  [free-search] Query: {search_text}")

    # ── STEP 2: Embed + search catalog ───────────────────────────────────
    try:
        from google import genai

        client = genai.Client(api_key=api_key)

        # Load catalog
        with open(str(CATALOG_JSON), "r", encoding="utf-8") as f:
            catalog = json.load(f)

        embeddings = np.load(str(EMBEDDINGS_NPY))
        with open(str(EMBEDDING_INDEX_JSON), "r", encoding="utf-8") as f:
            index_map = json.load(f)

        # Normalise catalog embeddings
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings_norm = embeddings / norms

        # Embed query
        result = client.models.embed_content(
            model=FS_EMBEDDING_MODEL,
            contents=search_text,
        )
        query_vec = np.array(result.embeddings[0].values)
        query_norm = query_vec / np.linalg.norm(query_vec)

        # Cosine similarity
        similarities = embeddings_norm @ query_norm
        ranked = np.argsort(similarities)[::-1]

        # Build filters from preferences
        filters = {}
        if preferences.get("max_price"):
            try:
                filters["max_price"] = float(preferences["max_price"])
            except ValueError:
                pass
        if preferences.get("gender"):
            filters["gender"] = preferences["gender"]

        # Collect top 3
        matches = []
        for idx in ranked:
            pid = index_map[str(idx)]
            product = None
            for p in catalog["products"]:
                if p["id"] == pid:
                    product = p
                    break
            if product is None:
                continue

            score = float(similarities[idx])
            if score < FS_MIN_SIMILARITY and len(matches) > 0:
                break

            # Apply filters
            if filters:
                ptags = product["tags"]["product"]
                stags = product["tags"]["style"]
                if filters.get("max_price") is not None:
                    if ptags.get("price", 0) > filters["max_price"]:
                        continue
                if filters.get("gender"):
                    target = stags.get("gender_target", "unisex")
                    if target != "unisex" and target != filters["gender"]:
                        continue

            matches.append((product, score))
            if len(matches) >= 3:
                break

    except Exception as e:
        sess["status"] = "error"
        sess["error"] = f"Search error: {e}"
        _cleanup(portrait_path)
        return

    if not matches:
        sess["status"] = "error"
        sess["error"] = "No matching glasses found. Try adjusting your preferences."
        _cleanup(portrait_path)
        return

    sess["num_options"] = len(matches)

    # Store product info for each match
    for i, (product, score) in enumerate(matches):
        glasses_path = os.path.join(CATALOG_DIR, product["image"])
        with open(glasses_path, "rb") as f:
            product_b64 = base64.b64encode(f.read()).decode("ascii")

        ptags = product["tags"]["product"]
        base = round(score, 3)
        fit_score = min(1.0, max(0.3, base + random.uniform(-0.08, 0.12)))
        style_score = min(1.0, max(0.3, base + random.uniform(-0.1, 0.08)))
        color_score = min(1.0, max(0.3, base + random.uniform(-0.12, 0.1)))

        sess[f"opt{i}"] = {
            "name": product["name"],
            "brand": ptags["brand"],
            "model": ptags["model_name"],
            "price": ptags["price"],
            "currency": ptags["currency"],
            "score": base,
            "fit_score": round(fit_score, 3),
            "style_score": round(style_score, 3),
            "color_score": round(color_score, 3),
            "shape": product["tags"]["frame"]["shape"],
            "material": product["tags"]["frame"]["material"],
            "color": ", ".join(product["tags"]["frame"]["color"]),
            "product_b64": product_b64,
            "tryon_status": "pending",
            "tryon_b64": None,
            "tryon_error": None,
        }

    # ── STEP 3: Virtual try-on x3 in parallel ────────────────────────────
    sess["stage"] = "tryon"

    def do_tryon(idx: int):
        product, _ = matches[idx]
        glasses_path = os.path.join(CATALOG_DIR, product["image"])
        sess[f"opt{idx}"]["tryon_status"] = "generating"

        tr = _fs_virtual_tryon(portrait_path, glasses_path, product, api_key)

        if tr["success"] and tr["image_bytes"]:
            sess[f"opt{idx}"]["tryon_b64"] = base64.b64encode(
                tr["image_bytes"]
            ).decode("ascii")
            sess[f"opt{idx}"]["tryon_status"] = "done"
        else:
            sess[f"opt{idx}"]["tryon_status"] = "error"
            sess[f"opt{idx}"]["tryon_error"] = tr.get("error", "No image returned")

    threads = [
        threading.Thread(target=do_tryon, args=(i,), daemon=True)
        for i in range(len(matches))
    ]
    for t in threads:
        t.start()

    # Wait for primary result first
    threads[0].join()
    sess["stage"] = "primary_ready"

    # Then wait for the rest
    for t in threads[1:]:
        t.join()

    sess["stage"] = "done"
    sess["status"] = "done"
    _cleanup(portrait_path)


def _cleanup(path: str):
    try:
        os.unlink(path)
    except OSError:
        pass
