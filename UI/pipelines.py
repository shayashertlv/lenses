"""
Background pipelines for Smart Fit and Free Search modes.
"""

import base64
import io
import json
import os
import tempfile
import threading
from pathlib import Path

from PIL import Image, ImageOps

from UI.config import (
    CATALOG_DIR,
    CATALOG_JSON,
    DEFAULT_GENERATION_MODEL,
    FACE_ANALYSIS_DIR,
    FS_MODEL_MAP,
    FS_DEFAULT_MODEL,
    FS_MAX_IMAGE_DIM,
    RECOLOR_MODEL_NAME,
    sessions,
)

from tag_matcher import compute_component_scores, preferences_to_query_tags, rank_products


# ══════════════════════════════════════════════════════════════════════════════
# PRE-LOADED CATALOG DATA (loaded once at import time, reused by all pipelines)
# ══════════════════════════════════════════════════════════════════════════════

def _preload_catalog():
    """Load catalog from disk once."""
    try:
        with open(str(CATALOG_JSON), "r", encoding="utf-8") as f:
            catalog_data = json.load(f)
        return catalog_data
    except Exception as e:
        print(f"  [pipelines] Warning: Could not preload catalog: {e}")
        return None


_CATALOG_DATA = _preload_catalog()


def _get_api_key() -> str:
    """Get the Gemini API key from os.environ, Windows registry, or .env file."""
    key = os.environ.get("GEMINI_API_KEY", "")

    # Fallback: on Windows, read from user/system env vars in the registry.
    # Env vars set via `setx` or System Properties aren't inherited by processes
    # spawned from a different process tree (e.g. preview tools, IDEs).
    if not key and os.name == "nt":
        try:
            import winreg
            # Try user environment first
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as rk:
                key, _ = winreg.QueryValueEx(rk, "GEMINI_API_KEY")
        except (FileNotFoundError, OSError):
            try:
                # Try system environment
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                ) as rk:
                    key, _ = winreg.QueryValueEx(rk, "GEMINI_API_KEY")
            except (FileNotFoundError, OSError):
                pass
        if key:
            os.environ["GEMINI_API_KEY"] = key

    # Fallback: try .env file
    if not key:
        try:
            from dotenv import load_dotenv
            load_dotenv()
            load_dotenv(FACE_ANALYSIS_DIR.parent / ".env")
            key = os.environ.get("GEMINI_API_KEY", "")
        except ImportError:
            pass

    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set.\n"
            "Get your API key from: https://aistudio.google.com/apikey\n"
            "Then set it:\n"
            "  Railway/Render: add GEMINI_API_KEY in the service's Variables tab, then redeploy.\n"
            "  setx GEMINI_API_KEY your-key-here       (Windows — permanent)\n"
            "  export GEMINI_API_KEY='your-key-here'    (Linux/Mac)"
        )
    return key


# ══════════════════════════════════════════════════════════════════════════════
# ASPECT RATIO SNAPPING
# ══════════════════════════════════════════════════════════════════════════════

# Gemini adopts the aspect ratio of the LAST image in the contents array.
# To guarantee consistent output we also pass an explicit aspect_ratio via
# ImageConfig, snapped to the nearest supported preset.

_SUPPORTED_RATIOS = [
    ("1:1",  1.0),
    ("2:3",  2 / 3),
    ("3:4",  3 / 4),
    ("9:16", 9 / 16),
    ("3:2",  3 / 2),
    ("4:3",  4 / 3),
    ("16:9", 16 / 9),
    ("21:9", 21 / 9),
]


def _snap_aspect_ratio(width: int, height: int) -> str:
    """Return the nearest Gemini-supported aspect ratio string for the given dimensions."""
    ratio = width / height
    best_label = "1:1"
    best_dist = float("inf")
    for label, value in _SUPPORTED_RATIOS:
        # Use log-space distance so portrait vs landscape errors are symmetric
        dist = abs(ratio - value) / max(ratio, value)
        if dist < best_dist:
            best_dist = dist
            best_label = label
    return best_label


def _detect_portrait_ratio(portrait_bytes: bytes) -> str:
    """Open portrait bytes just enough to read dimensions and snap to a preset."""
    img = Image.open(io.BytesIO(portrait_bytes))
    img = ImageOps.exif_transpose(img)
    return _snap_aspect_ratio(img.width, img.height)


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

    _pimg = Image.open(io.BytesIO(portrait_bytes))
    _pimg = ImageOps.exif_transpose(_pimg)
    if _pimg.mode in ("RGBA", "P"):
        _pimg = _pimg.convert("RGB")
    _pbuf = io.BytesIO()
    _pimg.save(_pbuf, format="JPEG")
    sess["portrait_b64"] = base64.b64encode(_pbuf.getvalue()).decode("ascii")

    # Detect portrait aspect ratio and snap to nearest Gemini-supported preset
    try:
        portrait_aspect = _detect_portrait_ratio(portrait_bytes)
    except Exception:
        portrait_aspect = "1:1"

    try:
        api_key = _get_api_key()
    except Exception as e:
        sess["status"] = "error"
        sess["error"] = str(e)
        return

    # Create a single client to reuse across all API calls in this pipeline
    from google import genai
    client = genai.Client(api_key=api_key)

    # Pre-load portrait Part in background thread (runs during face analysis + matching)
    from utils import load_image_as_part
    _portrait_result = [None, None]  # [part, error]
    def _preload_portrait():
        try:
            _portrait_result[0] = load_image_as_part(portrait_path)
        except Exception as e:
            _portrait_result[1] = e
    portrait_thread = threading.Thread(target=_preload_portrait, daemon=True)
    portrait_thread.start()

    # Pre-create InventoryMatcher with pre-loaded catalog data (near-instant)
    from inventory_matcher import InventoryMatcher
    matcher = InventoryMatcher(
        CATALOG_DIR,
        catalog_data=_CATALOG_DATA,
    )

    # STEP 1: FaceAnalyzer.analyze() — portrait pre-loading runs concurrently
    # Cache analysis results by image hash for consistent recommendations.
    sess["stage"] = "analyzing"
    from analysis_cache import compute_image_hash, get_cached_analysis, put_analysis, derive_seed
    image_hash = compute_image_hash(portrait_bytes)
    cached = get_cached_analysis(image_hash)

    if cached:
        analysis = cached
        sess["analysis_seconds"] = 0
    else:
        try:
            from face_analyzer import FaceAnalyzer
            seed = derive_seed(image_hash)
            analyzer = FaceAnalyzer(api_key=api_key, client=client)
            analysis_result = analyzer.analyze(portrait_path, seed=seed)
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
        put_analysis(image_hash, analysis)
        sess["analysis_seconds"] = round(analysis_result.elapsed_seconds, 1)

    sess["face_insights"] = analysis.get("face_insights", [])
    sess["face_summary"] = analysis.get("face_summary", {})

    # STEP 2: InventoryMatcher.match() — matcher pre-created, call immediately
    sess["stage"] = "matching"
    try:
        recommended_tags = analysis["glasses_recommendation"]["recommended_tags"]
        detected_gender = analysis["gender"]  # "men" or "women" — always present
        match_result = matcher.match(recommended_tags, top_k=3, gender=detected_gender)
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
            "score": round(score, 1),
            "shape": product["tags"]["frame"].get("shape", ""),
            "material": product["tags"]["frame"].get("material", ""),
            "color": ", ".join(product["tags"]["frame"]["color"]) if isinstance(product["tags"]["frame"].get("color"), list) else str(product["tags"]["frame"].get("color", "")),
            "product_b64": product_b64,
            "tryon_status": "pending",
            "tryon_b64": None,
            "tryon_error": None,
        }

    # STEP 3: virtual_tryon() x3 in parallel
    # Wait for portrait pre-loading to complete (started before face analysis)
    sess["stage"] = "tryon"
    from tryon_engine import virtual_tryon
    portrait_thread.join()
    if _portrait_result[1] is not None:
        sess["status"] = "error"
        sess["error"] = f"Portrait load error: {_portrait_result[1]}"
        _cleanup(portrait_path)
        return
    portrait_part = _portrait_result[0]

    # Build per-thread portrait Parts so each thread serialises its own copy.
    # Sharing a single protobuf Part across concurrent API calls can cause the
    # later requests to send corrupt / partially-serialised image data, which
    # consistently degrades alt-2 quality.
    import copy as _copy
    _portrait_parts = [_copy.deepcopy(portrait_part) for _ in matches]

    import time as _time

    def do_tryon(idx: int):
        # Stagger alt requests by ~1 s to avoid Gemini rate-limit pressure on
        # concurrent image-generation calls (alt-2 was consistently degraded).
        if idx > 0:
            _time.sleep(idx * 1.0)

        product, _ = matches[idx]
        glasses_path = matcher.get_product_image_path(product)
        sess[f"opt{idx}"]["tryon_status"] = "generating"
        # Each thread gets its own client to avoid HTTP connection serialisation.
        tryon_client = genai.Client(api_key=api_key)
        try:
            tr = virtual_tryon(
                portrait_path=portrait_path,
                glasses_image_path=glasses_path,
                analysis=analysis,
                matched_product=product,
                model_alias=DEFAULT_GENERATION_MODEL,
                api_key=api_key,
                portrait_part=_portrait_parts[idx],
                client=tryon_client,
                aspect_ratio=portrait_aspect,
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

def _build_query_tags(prefs: dict) -> dict:
    """Convert UI preference selections into structured query tags for tag matching."""
    return preferences_to_query_tags(prefs)


def _fs_load_image_as_part(path: str):
    """Load image, resize if needed, return a google.genai Part."""
    from google.genai import types

    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
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
- IMAGE 1: A product photo of glasses.
- IMAGE 2: A portrait photo of a person.

YOUR TASK: Create the EXACT same photo as IMAGE 2, but with the person wearing the glasses from IMAGE 1. The result must look like a real photograph — as if the person was already wearing these glasses when the photo was taken.

GLASSES FROM IMAGE 1:
- Frame: {frame.get("shape", "classic")}, {frame.get("material", "")}, {frame_colors}, {frame.get("rim_type", "")}
- Lenses: {lens_types}, {lens_colors}
- Reproduce the glasses from IMAGE 1 faithfully — same design, proportions, and details.

PLACEMENT:
- Position naturally on the face — bridge on nose, temples toward ears
- Match the person's face angle and perspective exactly
- Scale proportionally to the face
- Eyes visible through lenses at appropriate opacity for {lens_types} lenses with {lens_colors} tint

CRITICAL RULES:
- The output must be a 1:1 compositional match to IMAGE 2 — same head size, crop, zoom, framing, and camera distance
- Do NOT change the aspect ratio of IMAGE 2 — the output must have the same aspect ratio as IMAGE 2
- Do NOT crop, zoom in/out, re-center, reframe, or change what is visible at the edges — no close-up
- The edges of the output must show the EXACT same content as IMAGE 2 — same background, same body parts visible, same space above/below/around the head
- Keep everything else in the image exactly the same, preserving the original style, lighting, and composition
- The person's face, skin, hair, expression, clothing, background, and lighting must remain IDENTICAL
- Do NOT alter, smooth, or enhance any facial features
- Add realistic shadows from the glasses consistent with the existing lighting

OUTPUT: Return ONLY the edited photo. Same dimensions and quality as IMAGE 2. Photorealistic."""


def _fs_virtual_tryon(portrait_path: str, glasses_path: str, product: dict,
                      api_key: str, portrait_part=None, client=None,
                      aspect_ratio: str | None = None) -> dict:
    """
    Run a virtual try-on. Returns dict with keys:
      success, image_bytes, error

    Args:
        portrait_part: Pre-loaded portrait Part (skips loading from path if provided).
        client: Pre-created genai.Client (skips creating a new one if provided).
        aspect_ratio: Gemini-supported aspect ratio string to pin output framing.
    """
    from google import genai
    from google.genai import types

    model_name = FS_MODEL_MAP[FS_DEFAULT_MODEL]
    prompt = _fs_build_tryon_prompt(product)

    try:
        if portrait_part is None:
            portrait_part = _fs_load_image_as_part(portrait_path)
        glasses_part = _fs_load_image_as_part(glasses_path)
    except Exception as e:
        return {"success": False, "image_bytes": None, "error": f"Image load error: {e}"}

    if client is None:
        client = genai.Client(api_key=api_key)

    img_cfg = None
    if aspect_ratio:
        img_cfg = types.ImageConfig(aspect_ratio=aspect_ratio)

    for attempt in range(2):
        p = prompt if attempt == 0 else prompt + "\n\nReturn ONLY the edited image, no text."
        try:
            response = client.models.generate_content(
                model=model_name,
                # Glasses first, portrait last — model adopts last image's aspect ratio
                contents=[p, glasses_part, portrait_part],
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_modalities=["TEXT", "IMAGE"],
                    image_config=img_cfg,
                ),
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

    _pimg = Image.open(io.BytesIO(portrait_bytes))
    _pimg = ImageOps.exif_transpose(_pimg)
    if _pimg.mode in ("RGBA", "P"):
        _pimg = _pimg.convert("RGB")
    _pbuf = io.BytesIO()
    _pimg.save(_pbuf, format="JPEG")
    sess["portrait_b64"] = base64.b64encode(_pbuf.getvalue()).decode("ascii")

    # Detect portrait aspect ratio and snap to nearest Gemini-supported preset
    try:
        portrait_aspect = _detect_portrait_ratio(portrait_bytes)
    except Exception:
        portrait_aspect = "1:1"

    try:
        api_key = _get_api_key()
    except Exception as e:
        sess["status"] = "error"
        sess["error"] = str(e)
        return

    # Create a single client to reuse across all API calls in this pipeline
    from google import genai
    client = genai.Client(api_key=api_key)

    # Pre-load portrait Part in background thread (runs during search + matching)
    _portrait_result = [None, None]  # [part, error]
    def _preload_portrait():
        try:
            _portrait_result[0] = _fs_load_image_as_part(portrait_path)
        except Exception as e:
            _portrait_result[1] = e
    portrait_thread = threading.Thread(target=_preload_portrait, daemon=True)
    portrait_thread.start()

    # ── STEP 1: Build query tags from preferences ───────────────────────
    sess["stage"] = "searching"
    print(f"  [free-search] Raw preferences: {preferences}")
    query_tags = _build_query_tags(preferences)
    print(f"  [free-search] Query tags: {query_tags}")

    # ── STEP 2: Tag-based search against catalog ─────────────────────────
    try:
        catalog = _CATALOG_DATA

        # Fallback: load from disk if preload failed
        if catalog is None:
            with open(str(CATALOG_JSON), "r", encoding="utf-8") as f:
                catalog = json.load(f)

        # Build filters from preferences
        filters = {"in_stock_only": True}
        if preferences.get("max_price"):
            try:
                filters["max_price"] = float(preferences["max_price"])
            except ValueError:
                pass

        matches = rank_products(
            query_tags=query_tags,
            products=catalog["products"],
            top_k=3,
            filters=filters,
        )

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

    # ── STEP 3: Store product info + launch try-ons immediately ───────────
    _TRYON_TIMEOUT = 120  # seconds – max wait per try-on thread

    try:
        # Wait for portrait pre-loading (started before search, should be done by now)
        sess["stage"] = "tryon"
        portrait_thread.join(timeout=30)
        if portrait_thread.is_alive():
            sess["status"] = "error"
            sess["error"] = "Portrait loading timed out."
            _cleanup(portrait_path)
            return
        if _portrait_result[1] is not None:
            sess["status"] = "error"
            sess["error"] = f"Portrait load error: {_portrait_result[1]}"
            _cleanup(portrait_path)
            return
        portrait_part = _portrait_result[0]

        # Build per-thread portrait Parts — same fix as smart-fit pipeline.
        import copy as _copy
        import time as _time
        _portrait_parts = [_copy.deepcopy(portrait_part) for _ in matches]

        def do_tryon(idx: int):
            try:
                # Stagger alt requests to avoid Gemini rate-limit pressure.
                if idx > 0:
                    _time.sleep(idx * 1.0)

                product, _ = matches[idx]
                glasses_path = os.path.join(CATALOG_DIR, product["image"])
                sess[f"opt{idx}"]["tryon_status"] = "generating"

                tryon_client = genai.Client(api_key=api_key)
                tr = _fs_virtual_tryon(portrait_path, glasses_path, product, api_key,
                                       portrait_part=_portrait_parts[idx], client=tryon_client,
                                       aspect_ratio=portrait_aspect)

                if tr["success"] and tr["image_bytes"]:
                    sess[f"opt{idx}"]["tryon_b64"] = base64.b64encode(
                        tr["image_bytes"]
                    ).decode("ascii")
                    sess[f"opt{idx}"]["tryon_status"] = "done"
                else:
                    sess[f"opt{idx}"]["tryon_status"] = "error"
                    sess[f"opt{idx}"]["tryon_error"] = tr.get("error", "No image returned")
            except Exception as e:
                sess[f"opt{idx}"]["tryon_status"] = "error"
                sess[f"opt{idx}"]["tryon_error"] = f"Try-on failed: {e}"

        # Store each match and fire its try-on thread immediately — don't wait
        # for all matches to be stored before starting try-ons.
        threads = []
        for i, (product, score) in enumerate(matches):
            glasses_path = os.path.join(CATALOG_DIR, product["image"])
            with open(glasses_path, "rb") as f:
                product_b64 = base64.b64encode(f.read()).decode("ascii")

            ptags = product["tags"]["product"]
            base = round(score, 1)
            sub = compute_component_scores(query_tags, product["tags"])
            fit_score = round(sub["fit"], 1)
            style_score = round(sub["style"], 1)
            color_score = round(sub["color"], 1)

            sess[f"opt{i}"] = {
                "name": product["name"],
                "brand": ptags["brand"],
                "model": ptags["model_name"],
                "price": ptags["price"],
                "currency": ptags["currency"],
                "score": base,
                "fit_score": round(fit_score, 1),
                "style_score": round(style_score, 1),
                "color_score": round(color_score, 1),
                "shape": product["tags"]["frame"].get("shape", ""),
                "material": product["tags"]["frame"].get("material", ""),
                "color": ", ".join(product["tags"]["frame"]["color"]) if isinstance(product["tags"]["frame"].get("color"), list) else str(product["tags"]["frame"].get("color", "")),
                "product_b64": product_b64,
                "tryon_status": "pending",
                "tryon_b64": None,
                "tryon_error": None,
            }

            t = threading.Thread(target=do_tryon, args=(i,), daemon=True)
            t.start()
            threads.append(t)

        # Wait for primary result first (with timeout)
        threads[0].join(timeout=_TRYON_TIMEOUT)
        if threads[0].is_alive():
            sess[f"opt0"]["tryon_status"] = "error"
            sess[f"opt0"]["tryon_error"] = "Try-on timed out"
        sess["stage"] = "primary_ready"

        # Then wait for the rest (with timeout)
        for i, t in enumerate(threads[1:], start=1):
            t.join(timeout=_TRYON_TIMEOUT)
            if t.is_alive():
                sess[f"opt{i}"]["tryon_status"] = "error"
                sess[f"opt{i}"]["tryon_error"] = "Try-on timed out"

        sess["stage"] = "done"
        sess["status"] = "done"

    except Exception as e:
        sess["status"] = "error"
        sess["error"] = f"Pipeline error: {e}"
    finally:
        _cleanup(portrait_path)


def get_catalog_products() -> list[dict]:
    """Return a list of product summaries for the storefront."""
    if _CATALOG_DATA is None:
        return []
    products = []
    for p in _CATALOG_DATA["products"]:
        tags = p["tags"]
        ptags = tags["product"]
        products.append({
            "id": p["id"],
            "name": p["name"],
            "image": p["image"],
            "brand": ptags.get("brand", ""),
            "model": ptags.get("model_name", ""),
            "price": ptags.get("price", 0),
            "currency": ptags.get("currency", "ILS"),
            "in_stock": ptags.get("in_stock", True),
            "shape": tags["lenses"].get("shape", ""),
            "material": tags["frame"].get("material", ""),
            "color": ", ".join(tags["frame"]["color"]) if isinstance(tags["frame"].get("color"), list) else str(tags["frame"].get("color", "")),
            "rim_type": tags["frame"].get("rim_type", ""),
            "lens_type": ", ".join(tags["lenses"]["type"]) if isinstance(tags["lenses"].get("type"), list) else str(tags["lenses"].get("type", "")),
            "lens_color": ", ".join(tags["lenses"]["color"]) if isinstance(tags["lenses"].get("color"), list) else str(tags["lenses"].get("color", "")),
            "gender": tags["style"].get("gender_target", "unisex"),
            "thickness": tags["frame"].get("thickness", ""),
            "lens_size": tags["lenses"].get("size", ""),
            "aesthetic": tags["style"].get("aesthetic", []),
            "occasion": tags["style"].get("occasion", []),
        })
    return products


def run_storefront_tryon_pipeline(session_id: str, portrait_bytes: bytes,
                                  filename: str, product_id: str):
    """
    Storefront try-on pipeline — single product virtual try-on.
    Reuses the free-search try-on engine.
    """
    sess = sessions[session_id]

    if _CATALOG_DATA is None:
        sess["status"] = "error"
        sess["error"] = "Catalog not loaded"
        return

    # Find product by ID
    product = None
    for p in _CATALOG_DATA["products"]:
        if p["id"] == product_id:
            product = p
            break

    if product is None:
        sess["status"] = "error"
        sess["error"] = f"Product '{product_id}' not found"
        return

    ext = Path(filename).suffix or ".jpg"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.write(portrait_bytes)
    tmp.close()
    portrait_path = tmp.name

    _pimg = Image.open(io.BytesIO(portrait_bytes))
    _pimg = ImageOps.exif_transpose(_pimg)
    if _pimg.mode in ("RGBA", "P"):
        _pimg = _pimg.convert("RGB")
    _pbuf = io.BytesIO()
    _pimg.save(_pbuf, format="JPEG")
    sess["portrait_b64"] = base64.b64encode(_pbuf.getvalue()).decode("ascii")
    try:
        portrait_aspect = _detect_portrait_ratio(portrait_bytes)
    except Exception:
        portrait_aspect = "1:1"
    sess["stage"] = "tryon"

    try:
        api_key = _get_api_key()
    except Exception as e:
        sess["status"] = "error"
        sess["error"] = str(e)
        _cleanup(portrait_path)
        return

    from google import genai
    client = genai.Client(api_key=api_key)

    # Load product image
    glasses_path = os.path.join(CATALOG_DIR, product["image"])
    try:
        with open(glasses_path, "rb") as f:
            product_b64 = base64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        sess["status"] = "error"
        sess["error"] = f"Product image error: {e}"
        _cleanup(portrait_path)
        return

    ptags = product["tags"]["product"]
    sess["num_options"] = 1
    sess["opt0"] = {
        "name": product["name"],
        "brand": ptags.get("brand", ""),
        "model": ptags.get("model_name", ""),
        "price": ptags.get("price", 0),
        "currency": ptags.get("currency", "ILS"),
        "score": 1.0,
        "shape": product["tags"]["frame"].get("shape", ""),
        "material": product["tags"]["frame"].get("material", ""),
        "color": ", ".join(product["tags"]["frame"]["color"]) if isinstance(product["tags"]["frame"].get("color"), list) else "",
        "product_b64": product_b64,
        "tryon_status": "generating",
        "tryon_b64": None,
        "tryon_error": None,
    }

    tr = _fs_virtual_tryon(portrait_path, glasses_path, product, api_key,
                           client=client, aspect_ratio=portrait_aspect)

    if tr["success"] and tr["image_bytes"]:
        sess["opt0"]["tryon_b64"] = base64.b64encode(
            tr["image_bytes"]
        ).decode("ascii")
        sess["opt0"]["tryon_status"] = "done"
    else:
        sess["opt0"]["tryon_status"] = "error"
        sess["opt0"]["tryon_error"] = tr.get("error", "No image returned")

    sess["stage"] = "done"
    sess["status"] = "done"
    _cleanup(portrait_path)


def _cleanup(path: str):
    try:
        os.unlink(path)
    except OSError:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# LENS RECOLOR PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

def _build_recolor_prompt(target_color: str) -> str:
    """
    Build a recolor prompt for Nano Banana Pro.

    The model independently determines the most realistic way to change the
    glasses lens color. The output must be the exact same photo with only
    the lens color changed, blended naturally.
    """
    return f"""Create the EXACT same photo, but change ONLY the glasses lens color.

TARGET: {target_color}, medium tint (~50-60% opacity, eyes partially visible), uniform smooth tint
Preserve any existing lens reflections naturally on top of the new color.

WHAT TO CHANGE:
- Apply {target_color} tint only to the lens area inside the glasses frame
- Both lenses must have the same color treatment
- The tint should look like real tinted glass — eyes and face visible through at appropriate opacity
- Color edges must follow the inner frame edge precisely — no bleeding onto frame or skin

CRITICAL RULES:
- The output must be a 1:1 compositional match — same head size, crop, zoom, framing, and camera distance
- Do NOT change the aspect ratio — the output must have the same aspect ratio as the input
- Do NOT crop, zoom in/out, re-center, reframe, or change what is visible at the edges — no close-up
- The edges of the output must show the EXACT same content as the input — same background, same body parts visible
- The glasses FRAME must remain completely unchanged — only the lens color changes
- The person's face, skin, hair, expression, clothing, background, and lighting must remain IDENTICAL

OUTPUT: Return the edited photo. Same dimensions and quality as the input. Photorealistic — real tinted glasses, not a digital overlay."""


def _recolor_single(portrait_path: str, target_color: str, api_key: str,
                    portrait_part=None, client=None) -> dict:
    """
    Call Nano Banana Pro to recolor lenses to the target color.
    Returns dict with keys: success, image_bytes, error.

    Args:
        portrait_part: Pre-loaded portrait Part (skips loading from path if provided).
        client: Pre-created genai.Client (skips creating a new one if provided).
    """
    from google import genai
    from google.genai import types

    prompt = _build_recolor_prompt(target_color)

    try:
        if portrait_part is None:
            portrait_part = _fs_load_image_as_part(portrait_path)
    except Exception as e:
        return {"success": False, "image_bytes": None, "error": f"Image load error: {e}"}

    if client is None:
        client = genai.Client(api_key=api_key)

    for attempt in range(2):
        p = prompt if attempt == 0 else prompt + "\n\nReturn ONLY the edited image, no text."
        try:
            response = client.models.generate_content(
                model=RECOLOR_MODEL_NAME,
                contents=[p, portrait_part],
                config=types.GenerateContentConfig(temperature=0, response_modalities=["TEXT", "IMAGE"]),
            )
        except Exception as e:
            return {"success": False, "image_bytes": None, "error": str(e)}

        if not response.candidates:
            return {"success": False, "image_bytes": None,
                    "error": "No response. May have been blocked by safety filters."}

        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                return {"success": True, "image_bytes": part.inline_data.data, "error": None}

        if attempt == 0:
            continue

    return {"success": False, "image_bytes": None, "error": "Model did not return an image."}


def run_recolor_pipeline(session_id: str, portrait_bytes: bytes,
                         filename: str, colors: list[str]):
    """
    Lens recolor pipeline — background thread.

    Takes a user photo and 3 chosen lens colors, calls Nano Banana Pro
    for each color in parallel, stores results in session.
    """
    sess = sessions[session_id]

    ext = Path(filename).suffix or ".jpg"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.write(portrait_bytes)
    tmp.close()
    portrait_path = tmp.name

    _pimg = Image.open(io.BytesIO(portrait_bytes))
    _pimg = ImageOps.exif_transpose(_pimg)
    if _pimg.mode in ("RGBA", "P"):
        _pimg = _pimg.convert("RGB")
    _pbuf = io.BytesIO()
    _pimg.save(_pbuf, format="JPEG")
    sess["portrait_b64"] = base64.b64encode(_pbuf.getvalue()).decode("ascii")

    try:
        api_key = _get_api_key()
    except Exception as e:
        sess["status"] = "error"
        sess["error"] = str(e)
        return

    # Create a single client to reuse across all API calls in this pipeline
    from google import genai
    client = genai.Client(api_key=api_key)

    # Pre-load portrait image once and reuse across all recolors
    try:
        portrait_part = _fs_load_image_as_part(portrait_path)
    except Exception as e:
        sess["status"] = "error"
        sess["error"] = f"Portrait load error: {e}"
        _cleanup(portrait_path)
        return

    sess["stage"] = "recoloring"
    sess["num_colors"] = len(colors)

    for i, color in enumerate(colors):
        sess[f"color{i}"] = {
            "name": color,
            "status": "pending",
            "b64": None,
            "error": None,
        }

    def do_recolor(idx: int):
        color = colors[idx]
        sess[f"color{idx}"]["status"] = "generating"
        print(f"  [recolor] Generating {color} (slot {idx}) ...")

        recolor_client = genai.Client(api_key=api_key)
        result = _recolor_single(portrait_path, color, api_key,
                                 portrait_part=portrait_part, client=recolor_client)

        if result["success"] and result["image_bytes"]:
            sess[f"color{idx}"]["b64"] = base64.b64encode(
                result["image_bytes"]
            ).decode("ascii")
            sess[f"color{idx}"]["status"] = "done"
            print(f"  [recolor] {color} done.")
        else:
            sess[f"color{idx}"]["status"] = "error"
            sess[f"color{idx}"]["error"] = result.get("error", "No image returned")
            print(f"  [recolor] {color} error: {result.get('error')}")

    threads = [
        threading.Thread(target=do_recolor, args=(i,), daemon=True)
        for i in range(len(colors))
    ]
    for t in threads:
        t.start()

    # Wait for the first result so we can show it immediately
    threads[0].join()
    sess["stage"] = "primary_ready"

    for t in threads[1:]:
        t.join()

    sess["stage"] = "done"
    sess["status"] = "done"
    _cleanup(portrait_path)


# ══════════════════════════════════════════════════════════════════════════════
# SINGLE-COLOR RECOLOR (for storefront inline recolor)
# ══════════════════════════════════════════════════════════════════════════════

def run_single_recolor_pipeline(session_id: str, image_bytes: bytes, color: str):
    """Single-color lens recolor for storefront try-on results."""
    sess = sessions[session_id]

    ext = ".png"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.write(image_bytes)
    tmp.close()
    portrait_path = tmp.name

    try:
        api_key = _get_api_key()
    except Exception as e:
        sess["status"] = "error"
        sess["error"] = str(e)
        return

    sess["stage"] = "recoloring"
    print(f"  [storefront-recolor] Generating {color} ...")

    try:
        portrait_part = _fs_load_image_as_part(portrait_path)
    except Exception as e:
        sess["status"] = "error"
        sess["error"] = f"Portrait load error: {e}"
        _cleanup(portrait_path)
        return

    result = _recolor_single(portrait_path, color, api_key,
                             portrait_part=portrait_part)

    if result["success"] and result["image_bytes"]:
        sess["result_b64"] = base64.b64encode(result["image_bytes"]).decode("ascii")
        sess["status"] = "done"
        sess["stage"] = "done"
        print(f"  [storefront-recolor] {color} done.")
    else:
        sess["status"] = "error"
        sess["error"] = result.get("error", "Recolor failed")
        print(f"  [storefront-recolor] {color} error: {result.get('error')}")

    _cleanup(portrait_path)
