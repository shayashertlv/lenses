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

from PIL import Image

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

from tag_matcher import preferences_to_query_tags, rank_products


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
    sess["stage"] = "analyzing"
    try:
        from face_analyzer import FaceAnalyzer
        analyzer = FaceAnalyzer(api_key=api_key, client=client)
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

    def do_tryon(idx: int):
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
                portrait_part=portrait_part,
                client=tryon_client,
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
                      api_key: str, portrait_part=None, client=None) -> dict:
    """
    Run a virtual try-on. Returns dict with keys:
      success, image_bytes, error

    Args:
        portrait_part: Pre-loaded portrait Part (skips loading from path if provided).
        client: Pre-created genai.Client (skips creating a new one if provided).
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
        if preferences.get("gender"):
            filters["gender"] = preferences["gender"]
        if preferences.get("lens_type"):
            filters["lens_type"] = preferences["lens_type"]

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
    # Wait for portrait pre-loading (started before search, should be done by now)
    sess["stage"] = "tryon"
    portrait_thread.join()
    if _portrait_result[1] is not None:
        sess["status"] = "error"
        sess["error"] = f"Portrait load error: {_portrait_result[1]}"
        _cleanup(portrait_path)
        return
    portrait_part = _portrait_result[0]

    def do_tryon(idx: int):
        product, _ = matches[idx]
        glasses_path = os.path.join(CATALOG_DIR, product["image"])
        sess[f"opt{idx}"]["tryon_status"] = "generating"

        tr = _fs_virtual_tryon(portrait_path, glasses_path, product, api_key,
                               portrait_part=portrait_part, client=client)

        if tr["success"] and tr["image_bytes"]:
            sess[f"opt{idx}"]["tryon_b64"] = base64.b64encode(
                tr["image_bytes"]
            ).decode("ascii")
            sess[f"opt{idx}"]["tryon_status"] = "done"
        else:
            sess[f"opt{idx}"]["tryon_status"] = "error"
            sess[f"opt{idx}"]["tryon_error"] = tr.get("error", "No image returned")

    # Store each match and fire its try-on thread immediately — don't wait
    # for all matches to be stored before starting try-ons.
    threads = []
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

        t = threading.Thread(target=do_tryon, args=(i,), daemon=True)
        t.start()
        threads.append(t)

    # Wait for primary result first
    threads[0].join()
    sess["stage"] = "primary_ready"

    # Then wait for the rest
    for t in threads[1:]:
        t.join()

    sess["stage"] = "done"
    sess["status"] = "done"
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
            "shape": tags["frame"].get("shape", ""),
            "material": tags["frame"].get("material", ""),
            "color": ", ".join(tags["frame"]["color"]) if isinstance(tags["frame"].get("color"), list) else str(tags["frame"].get("color", "")),
            "rim_type": tags["frame"].get("rim_type", ""),
            "lens_type": ", ".join(tags["lenses"]["type"]) if isinstance(tags["lenses"].get("type"), list) else str(tags["lenses"].get("type", "")),
            "gender": tags["style"].get("gender_target", "unisex"),
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

    sess["portrait_b64"] = base64.b64encode(portrait_bytes).decode("ascii")
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
                           client=client)

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
    return f"""Edit this photo by changing ONLY the color of the glasses lenses. Apply the following lens modification:

TARGET LENS COLOR: {target_color}
TINT INTENSITY: a balanced, medium tint — approximately 50-60% opacity, like standard sunglasses where the eyes are partially visible
LENS FINISH STYLE: a uniform, smooth color tint across the entire lens surface

If the original lenses have natural light reflections, glare spots, or environmental reflections visible on the lens surface, preserve them naturally on top of the new color tint. The reflections should interact realistically with the new lens color.

CRITICAL PRESERVATION RULES — do NOT change any of the following:
- The person's face, skin tone, skin texture, facial features, expression, and makeup must remain IDENTICAL
- The person's hair (color, style, texture, stray hairs) must remain IDENTICAL
- The glasses FRAME (shape, color, material, thickness, temple arms, nose pads, any frame details) must remain COMPLETELY UNCHANGED — only the lens area inside the frame changes
- The person's clothing, accessories, jewelry must remain IDENTICAL
- The background (color, texture, bokeh, lighting, objects) must remain IDENTICAL
- The overall lighting, shadows, and color grading of the photo must remain IDENTICAL
- The photo composition, framing, and resolution must remain IDENTICAL
- Any text, logos, or watermarks present must remain IDENTICAL

LENS COLOR APPLICATION GUIDANCE:
- Apply the {target_color} tint ONLY to the transparent/semi-transparent lens area bounded by the glasses frame
- The tint should look like a real, physical lens — it should follow the curvature and shape of the lens
- Where the lens overlaps the person's eyes and face, the {target_color} tint should blend naturally, as real tinted glass does — the skin and eye features behind the lens should show through at the appropriate opacity level
- The edge of the color change must PRECISELY follow the inner edge of the glasses frame — no color bleeding onto the frame or face
- Both lenses must have the SAME color treatment applied consistently

OUTPUT: Return the edited photo maintaining the EXACT same dimensions, quality, and format as the input. The result must be photorealistic — it should look like an actual photograph of someone wearing {target_color} tinted glasses, NOT like a digital color overlay was applied."""


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

    sess["portrait_b64"] = base64.b64encode(portrait_bytes).decode("ascii")

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
        print(f"  [recolor] Generating {color} (slot {idx}) via nano-banana-pro ...")

        result = _recolor_single(portrait_path, color, api_key,
                                 portrait_part=portrait_part, client=client)

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

    result = _recolor_single(portrait_path, color, api_key)

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
