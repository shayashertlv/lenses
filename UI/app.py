"""
Lenses UI — Upload portrait -> face analysis -> inventory match -> virtual try-on.

Pipeline (mirroring face_analysis/main.py but for the web):
  1. FaceAnalyzer.analyze(portrait_path)
       -> analysis dict with recommended_tags + alternative_recommendations
  2. InventoryMatcher(CATALOG_DIR, api_key).match(recommended_tags, top_k=3)
       -> 3 best (product, score) tuples from the catalog
  3. virtual_tryon(portrait, glasses_img, analysis, product, model, key) x3
       -> try-on image bytes for each match (all 3 run in parallel)

Frontend polls /api/status/<id> and progressively reveals results as each
try-on finishes.  Option 1 appears first; options 2 & 3 show when ready.
"""

import base64
import json
import os
import sys
import tempfile
import threading
import urllib.parse
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FACE_ANALYSIS_DIR = PROJECT_ROOT / "face_analysis"
CATALOG_DIR = str(PROJECT_ROOT / "lenses" / "catalog")

# face_analysis modules use sibling imports (from config import …),
# so we add the directory to sys.path once.
if str(FACE_ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(FACE_ANALYSIS_DIR))

# ── In-memory session store ──────────────────────────────────────────────────
sessions: dict[str, dict] = {}


# ── Background pipeline ─────────────────────────────────────────────────────

def run_pipeline(session_id: str, portrait_bytes: bytes, filename: str):
    """
    Runs the full face_analysis pipeline in a background thread.

    Steps mirror face_analysis/main.py lines 155-287 but adapted for web:
      1. FaceAnalyzer.analyze()
      2. InventoryMatcher.match(top_k=3)
      3. virtual_tryon() x3 in parallel
    """
    sess = sessions[session_id]

    # Save upload to a temp file (face_analyzer needs a file path)
    ext = Path(filename).suffix or ".jpg"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.write(portrait_bytes)
    tmp.close()
    portrait_path = tmp.name

    # Encode portrait for the frontend to display
    sess["portrait_b64"] = base64.b64encode(portrait_bytes).decode("ascii")

    # ── API key ──────────────────────────────────────────────────────────
    try:
        from config import get_api_key, DEFAULT_GENERATION_MODEL
        api_key = get_api_key()
    except Exception as e:
        sess["status"] = "error"
        sess["error"] = str(e)
        return

    # ── STEP 1  FaceAnalyzer.analyze() ───────────────────────────────────
    # (same as face_analysis/main.py line 158-171)
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

    # ── STEP 2  InventoryMatcher.match() ─────────────────────────────────
    # (same as face_analysis/main.py line 198-211)
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

    matches = match_result.matches  # list[(product_dict, score)]
    sess["num_options"] = len(matches)

    # Store product info + catalog image for each option
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
            "tryon_status": "pending",  # pending | generating | done | error
            "tryon_b64": None,
            "tryon_error": None,
        }

    # ── STEP 3  virtual_tryon() x3 in parallel ──────────────────────────
    # (same as face_analysis/main.py line 253-261, but for each match)
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

    # Launch all 3 try-ons in parallel threads
    threads = [
        threading.Thread(target=do_tryon, args=(i,), daemon=True)
        for i in range(len(matches))
    ]
    for t in threads:
        t.start()

    # Wait for option 0 first so the frontend can show the primary result early
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


# ── HTTP handler ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def do_HEAD(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            data = LANDING_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/":
            self._html(LANDING_HTML)
        elif path.startswith("/api/status/"):
            sid = path[len("/api/status/"):]
            self._serve_status(sid)
        else:
            self.send_error(404)

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path == "/api/upload":
            self._handle_upload()
        else:
            self.send_error(404)

    # ── upload ───────────────────────────────────────────────────────────
    def _handle_upload(self):
        ctype = self.headers.get("Content-Type", "")
        clen = int(self.headers.get("Content-Length", 0))

        if "multipart/form-data" not in ctype:
            return self._json(400, {"error": "Expected multipart/form-data"})

        boundary = ctype.split("boundary=")[1].strip()
        body = self.rfile.read(clen)
        file_data, file_name = _parse_multipart(body, boundary)

        if not file_data:
            return self._json(400, {"error": "No file in upload"})

        sid = uuid.uuid4().hex[:12]
        sessions[sid] = {"status": "processing", "stage": "uploading",
                         "error": None, "num_options": 0}

        threading.Thread(
            target=run_pipeline, args=(sid, file_data, file_name), daemon=True
        ).start()

        self._json(200, {"session_id": sid})

    # ── status polling ───────────────────────────────────────────────────
    def _serve_status(self, sid: str):
        sess = sessions.get(sid)
        if not sess:
            return self._json(404, {"error": "Unknown session"})

        resp: dict = {
            "status": sess["status"],
            "stage": sess.get("stage", ""),
            "error": sess.get("error"),
            "num_options": sess.get("num_options", 0),
            "portrait_b64": sess.get("portrait_b64"),
        }

        for i in range(sess.get("num_options", 0)):
            opt = sess.get(f"opt{i}")
            if not opt:
                continue
            resp[f"opt{i}"] = {
                "name": opt["name"],
                "brand": opt["brand"],
                "model": opt["model"],
                "price": opt["price"],
                "currency": opt["currency"],
                "score": opt["score"],
                "shape": opt["shape"],
                "material": opt["material"],
                "color": opt["color"],
                "product_b64": opt["product_b64"],
                "tryon_status": opt["tryon_status"],
                "tryon_b64": opt["tryon_b64"],
                "tryon_error": opt.get("tryon_error"),
            }

        self._json(200, resp)

    # ── helpers ──────────────────────────────────────────────────────────
    def _html(self, content: str):
        data = content.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code: int, obj: dict):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")


# ── Multipart parser (stdlib-only) ───────────────────────────────────────────

def _parse_multipart(body: bytes, boundary: str) -> tuple[bytes | None, str]:
    """Extract the first file from a multipart/form-data body."""
    delim = f"--{boundary}".encode()
    for part in body.split(delim):
        if b"filename=" not in part:
            continue
        hdr_end = part.find(b"\r\n\r\n")
        if hdr_end == -1:
            continue
        header = part[:hdr_end].decode("utf-8", errors="replace")
        payload = part[hdr_end + 4:]
        # strip trailing CRLF / closing boundary
        if payload.endswith(b"--\r\n"):
            payload = payload[:-4]
        elif payload.endswith(b"--"):
            payload = payload[:-2]
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]

        # extract filename from Content-Disposition
        fname = "upload.jpg"
        for seg in header.split("\r\n"):
            if 'filename="' in seg:
                s = seg.index('filename="') + 10
                fname = seg[s:seg.index('"', s)]
                break
        return payload, fname

    return None, ""


# ── Landing page HTML ────────────────────────────────────────────────────────

LANDING_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Lenses</title>
<style>
/* ── Reset & base ────────────────────────────────── */
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:'Inter',-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:#f7f7fb;color:#1e1e2f;min-height:100vh;
  -webkit-font-smoothing:antialiased;
}

/* ── Shared transitions ──────────────────────────── */
.view{transition:opacity .45s ease;opacity:0;pointer-events:none;position:absolute;
  inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center}
.view.active{opacity:1;pointer-events:auto;position:relative}

/* ── LANDING ─────────────────────────────────────── */
#landing{
  min-height:100vh;padding:2rem;text-align:center;
  background:linear-gradient(165deg,#f7f7fb 0%,#edeef6 40%,#e3e0f3 100%);
}
#landing .logo{font-size:3.2rem;font-weight:800;letter-spacing:-.04em;
  color:#1e1e2f;margin-bottom:.25rem}
#landing .tagline{font-size:1.08rem;color:#7c7c96;margin-bottom:3rem;
  font-weight:400;letter-spacing:.01em}

.upload-area{
  width:280px;height:280px;border:2.5px dashed #c4c3d6;border-radius:50%;
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  cursor:pointer;transition:all .3s ease;background:rgba(255,255,255,.55);
  margin-bottom:2.5rem;position:relative;
}
.upload-area:hover{border-color:#6c63ff;background:rgba(108,99,255,.04);
  transform:scale(1.03)}

.upload-area svg{width:48px;height:48px;stroke:#8b85b8;stroke-width:1.5;
  fill:none;margin-bottom:1rem;transition:stroke .3s}
.upload-area:hover svg{stroke:#6c63ff}
.upload-area .up-label{font-size:1.1rem;font-weight:600;color:#3a3a52}
.upload-area .up-hint{font-size:.82rem;color:#9b99ae;margin-top:.3rem}

#file-input{display:none}

/* ── PROCESSING ──────────────────────────────────── */
#processing{min-height:100vh;padding:2rem;
  background:linear-gradient(165deg,#f7f7fb 0%,#edeef6 40%,#e3e0f3 100%)}

.proc-card{
  background:#fff;border-radius:24px;padding:2.5rem 2.8rem;
  box-shadow:0 8px 40px rgba(30,30,47,.07);max-width:480px;width:100%;
  text-align:center;
}

/* portrait thumbnail */
.portrait-wrap{
  width:120px;height:120px;border-radius:50%;overflow:hidden;
  margin:0 auto 1.8rem;border:3px solid #edeef6;
  box-shadow:0 4px 20px rgba(30,30,47,.08);
}
.portrait-wrap img{width:100%;height:100%;object-fit:cover}

/* step progress */
.steps{display:flex;justify-content:center;gap:.5rem;margin-bottom:2rem}
.step{display:flex;align-items:center;gap:.35rem;font-size:.78rem;
  color:#b0afc2;font-weight:500;transition:color .3s}
.step.active{color:#6c63ff}
.step.done{color:#34c78a}
.step-dot{width:28px;height:28px;border-radius:50%;
  border:2px solid #d8d7e5;display:flex;align-items:center;
  justify-content:center;font-size:.72rem;font-weight:700;
  transition:all .3s;color:#b0afc2}
.step.active .step-dot{border-color:#6c63ff;color:#6c63ff;
  box-shadow:0 0 0 4px rgba(108,99,255,.12)}
.step.done .step-dot{border-color:#34c78a;background:#34c78a;color:#fff}
.step-line{width:32px;height:2px;background:#e2e1ed;border-radius:1px;
  align-self:center;transition:background .3s}
.step-line.done{background:#34c78a}

/* animated progress bar */
.prog-bar{width:100%;height:3px;background:#edeef6;border-radius:2px;
  margin-bottom:1.6rem;overflow:hidden}
.prog-fill{height:100%;background:linear-gradient(90deg,#6c63ff,#a78bfa);
  border-radius:2px;transition:width .6s ease;width:0%}

#stage-text{font-size:.95rem;color:#4a4a64;font-weight:500;margin-bottom:1.4rem;
  min-height:1.4em}

/* rotating tips */
.tip-box{min-height:3.5em;display:flex;align-items:center;justify-content:center}
.tip{font-size:.82rem;color:#9b99ae;line-height:1.5;max-width:340px;
  transition:opacity .4s;font-style:italic}

/* ── RESULTS ─────────────────────────────────────── */
#results{min-height:auto;padding:2rem 1.5rem;position:relative;
  display:block;max-width:960px;margin:0 auto;
  background:transparent;opacity:0;transition:opacity .45s ease}
#results.active{opacity:1}

.res-hdr{text-align:center;margin-bottom:2.2rem;padding-top:.5rem}
.res-hdr h1{font-size:1.55rem;font-weight:700;color:#1e1e2f;margin-bottom:.25rem}
.res-hdr p{color:#8b85b8;font-size:.88rem}

/* ── option card ─────────────────────────────────── */
.opt{background:#fff;border-radius:18px;margin-bottom:1.8rem;overflow:hidden;
  box-shadow:0 2px 16px rgba(30,30,47,.06);
  animation:cardIn .5s ease both}
@keyframes cardIn{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}
.opt:nth-child(2){animation-delay:.1s}
.opt:nth-child(3){animation-delay:.2s}

.opt.primary{box-shadow:0 4px 24px rgba(108,99,255,.13);
  border:2px solid #6c63ff}

.opt-label{padding:.65rem 1.4rem;font-weight:700;font-size:.82rem;
  text-transform:uppercase;letter-spacing:.06em;
  background:#f7f7fb;color:#6b6b80}
.opt.primary .opt-label{
  background:linear-gradient(135deg,#6c63ff,#8b7bff);color:#fff}

.opt-body{display:flex;gap:1.5rem;padding:1.4rem;align-items:stretch}

/* try-on image — hero */
.tryon-col{flex:1 1 0;min-width:0;display:flex;align-items:center;justify-content:center}
.tryon-col img{width:100%;max-height:420px;object-fit:contain;border-radius:12px;
  display:block;background:#f4f4f8}

/* product sidebar */
.prod-col{flex:0 0 185px;display:flex;flex-direction:column;gap:.6rem}
.prod-col img{width:100%;aspect-ratio:1/1;object-fit:cover;border-radius:12px;
  background:#f4f4f8}
.prod-info h3{font-size:.88rem;font-weight:600;line-height:1.3;margin-bottom:.15rem}
.prod-info .brand{font-size:.76rem;color:#8b85b8}
.prod-info .tags{display:flex;flex-wrap:wrap;gap:.3rem;margin-top:.45rem}
.prod-info .tag{background:#f0eef9;color:#6c63ff;font-size:.68rem;
  padding:.15rem .5rem;border-radius:8px;font-weight:500}
.prod-info .price{font-size:.95rem;font-weight:700;color:#1e1e2f;margin-top:.55rem}
.prod-info .score{font-size:.72rem;color:#b0afc2;margin-top:.1rem}

/* ── loading shimmer inside result card ──────────── */
.tryon-loading{
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  min-height:300px;width:100%;border-radius:12px;
  background:linear-gradient(110deg,#f4f4f8 8%,#edeef6 18%,#f4f4f8 33%);
  background-size:200% 100%;animation:shimmer 1.6s linear infinite;
}
@keyframes shimmer{to{background-position:-200% 0}}
.tryon-loading p{font-size:.82rem;color:#9b99ae;margin-top:.5rem}
.tryon-loading .mini-spin{width:28px;height:28px;border:3px solid #e2e1ed;
  border-top-color:#6c63ff;border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

.tryon-error{display:flex;align-items:center;justify-content:center;
  min-height:220px;background:#fef5f5;border-radius:12px;
  color:#d44;font-size:.85rem;padding:1.2rem;text-align:center;width:100%}

/* ── start over ──────────────────────────────────── */
.start-over{display:block;margin:1.5rem auto 3rem;padding:.75rem 2.2rem;
  background:#1e1e2f;color:#fff;border:none;border-radius:50px;
  font-size:.92rem;font-weight:600;cursor:pointer;transition:all .2s}
.start-over:hover{background:#6c63ff;transform:translateY(-2px)}

/* ── error view ──────────────────────────────────── */
#error-view{min-height:100vh;padding:2rem;
  background:linear-gradient(165deg,#f7f7fb 0%,#edeef6 40%,#e3e0f3 100%)}
.err-card{background:#fff;border-radius:24px;padding:2.5rem 2.8rem;
  box-shadow:0 8px 40px rgba(30,30,47,.07);max-width:420px;width:100%;text-align:center}
.err-card .err-icon{font-size:2.4rem;margin-bottom:1rem}
.err-card h2{font-size:1.15rem;color:#1e1e2f;margin-bottom:.6rem}
.err-card p{font-size:.88rem;color:#8b85b8;line-height:1.5;margin-bottom:1.5rem;
  word-break:break-word}

/* ── responsive ──────────────────────────────────── */
@media(max-width:640px){
  .opt-body{flex-direction:column}
  .prod-col{flex:none;flex-direction:row;gap:1rem;align-items:center}
  .prod-col img{width:90px;height:90px;flex-shrink:0}
  .tryon-col img{max-height:320px}
  .proc-card{padding:2rem 1.5rem}
}
</style>
</head>
<body>

<!-- ═══════════════ LANDING ═══════════════ -->
<div id="landing" class="view active">
  <div class="logo">Lenses</div>
  <p class="tagline">AI-powered glasses fitting, just from a selfie</p>

  <div class="upload-area" onclick="document.getElementById('file-input').click()">
    <svg viewBox="0 0 48 48"><path d="M24 32V16m0 0l-8 8m8-8l8 8" stroke-linecap="round" stroke-linejoin="round"/><rect x="6" y="6" width="36" height="36" rx="8" stroke-linecap="round"/></svg>
    <span class="up-label">Upload a Photo</span>
    <span class="up-hint">JPG, PNG, or WebP</span>
  </div>

  <input type="file" id="file-input" accept="image/*"/>
</div>

<!-- ═══════════════ PROCESSING ═══════════════ -->
<div id="processing" class="view">
  <div class="proc-card">

    <div class="portrait-wrap" id="portrait-preview" style="display:none">
      <img id="portrait-img" src="" alt="Your photo"/>
    </div>

    <!-- step indicators -->
    <div class="steps">
      <div class="step" id="s1"><span class="step-dot">1</span><span>Analyze</span></div>
      <div class="step-line" id="sl1"></div>
      <div class="step" id="s2"><span class="step-dot">2</span><span>Match</span></div>
      <div class="step-line" id="sl2"></div>
      <div class="step" id="s3"><span class="step-dot">3</span><span>Try-On</span></div>
    </div>

    <div class="prog-bar"><div class="prog-fill" id="prog-fill"></div></div>

    <p id="stage-text">Uploading your photo...</p>

    <div class="tip-box"><p class="tip" id="tip-text"></p></div>
  </div>
</div>

<!-- ═══════════════ ERROR ═══════════════ -->
<div id="error-view" class="view">
  <div class="err-card">
    <div class="err-icon">:/</div>
    <h2>Something went wrong</h2>
    <p id="error-msg"></p>
    <button class="start-over" onclick="reset()">Try Again</button>
  </div>
</div>

<!-- ═══════════════ RESULTS ═══════════════ -->
<div id="results">
  <div class="res-hdr">
    <h1>Your Recommendations</h1>
    <p>Curated by AI based on your facial features</p>
  </div>
  <div id="opts"></div>
  <button class="start-over" onclick="reset()">Try Another Photo</button>
</div>

<!-- ═══════════════ SCRIPT ═══════════════ -->
<script>
const $=id=>document.getElementById(id);
const landing=$('landing'), processing=$('processing'),
      results=$('results'), errorView=$('error-view'),
      stageEl=$('stage-text'), optsEl=$('opts'),
      fileIn=$('file-input'), progFill=$('prog-fill'),
      tipEl=$('tip-text'), errorMsg=$('error-msg'),
      portraitPreview=$('portrait-preview'), portraitImg=$('portrait-img');

let sid=null, poll=null, tipTimer=null, tipIdx=0;

/* ── Fun facts / tips shown while waiting ────────── */
const tips=[
  "Round faces pair best with angular frames to add definition.",
  "The top of your frames should follow your brow line for the most natural look.",
  "Titanium frames are up to 40% lighter than standard metal \u2014 great for all-day wear.",
  "Semi-rimless frames are the most popular style for professional settings.",
  "Your frames should be roughly as wide as the widest part of your face.",
  "Acetate frames come in more colors and patterns than any other material.",
  "The right pair of glasses can visually balance facial proportions.",
  "Heart-shaped faces look great in bottom-heavy frames that add width below the eyes.",
  "Blue-light filtering lenses can reduce digital eye strain by up to 23%.",
  "Warm skin undertones pair beautifully with tortoiseshell and gold frames.",
  "Cool skin undertones are complemented by silver, black, and jewel-toned frames.",
  "Square faces benefit from rounded frames that soften strong angles.",
];

function startTips(){
  tipIdx=0; showTip();
  tipTimer=setInterval(()=>{tipIdx=(tipIdx+1)%tips.length;showTip()},4500);
}
function stopTips(){if(tipTimer)clearInterval(tipTimer);tipTimer=null}
function showTip(){
  tipEl.style.opacity='0';
  setTimeout(()=>{tipEl.textContent=tips[tipIdx];tipEl.style.opacity='1'},350);
}

/* ── View management ─────────────────────────────── */
function show(id){
  [landing,processing,errorView].forEach(v=>v.classList.remove('active'));
  results.style.display='none';results.classList.remove('active');

  if(id==='landing') landing.classList.add('active');
  else if(id==='processing') processing.classList.add('active');
  else if(id==='error') errorView.classList.add('active');
  else if(id==='results'){results.style.display='block';
    requestAnimationFrame(()=>results.classList.add('active'))}
}

/* ── Step + progress helpers ─────────────────────── */
function setStep(n){
  for(let i=1;i<=3;i++){
    const s=$('s'+i);
    s.classList.remove('active','done');
    if(i<n) s.classList.add('done');
    else if(i===n) s.classList.add('active');
  }
  $('sl1').className='step-line'+(n>1?' done':'');
  $('sl2').className='step-line'+(n>2?' done':'');
}

const progMap={uploading:5,analyzing:20,matching:50,tryon:65,primary_ready:85,done:100};
function setProg(stage){
  progFill.style.width=(progMap[stage]||5)+'%';
}

/* ── Upload ──────────────────────────────────────── */
fileIn.addEventListener('change', async e=>{
  const f=e.target.files[0]; if(!f) return;
  show('processing'); setStep(1); setProg('uploading');
  stageEl.textContent='Uploading your photo...';
  startTips();

  // show portrait preview immediately
  const reader=new FileReader();
  reader.onload=ev=>{
    portraitImg.src=ev.target.result;
    portraitPreview.style.display='block';
  };
  reader.readAsDataURL(f);

  const fd=new FormData(); fd.append('photo',f);
  try{
    const r=await fetch('/api/upload',{method:'POST',body:fd});
    const j=await r.json();
    if(j.error){showError(j.error);return}
    sid=j.session_id; pollStatus();
  }catch(err){showError('Upload failed: '+err.message)}
});

/* ── Poll ────────────────────────────────────────── */
function pollStatus(){
  if(!sid) return;
  fetch('/api/status/'+sid).then(r=>r.json()).then(d=>{
    if(d.status==='error'){showError(d.error||'Unknown error');return}

    // update stage text + steps + progress
    const msgs={
      uploading:'Uploading your photo...',
      analyzing:'Analyzing your facial features...',
      matching:'Searching our catalog for ideal frames...',
      tryon:'Generating virtual try-on images...',
      primary_ready:'Your best match is ready!',
      done:'All recommendations are ready'
    };
    const stepMap={uploading:1,analyzing:1,matching:2,tryon:3,primary_ready:3,done:3};
    stageEl.textContent=msgs[d.stage]||'Processing...';
    setStep(stepMap[d.stage]||1);
    setProg(d.stage);

    if(d.num_options>0 && (d.stage==='primary_ready'||d.stage==='done')){
      stopTips(); render(d);
    }

    if(d.status!=='done' && d.status!=='error')
      poll=setTimeout(pollStatus,2000);
    else if(d.status==='done'){stopTips();render(d)}
  }).catch(()=>{poll=setTimeout(pollStatus,3000)});
}

/* ── Error ───────────────────────────────────────── */
function showError(msg){
  stopTips(); errorMsg.textContent=msg; show('error');
}

/* ── Render results ──────────────────────────────── */
function render(d){
  show('results');
  optsEl.innerHTML='';

  for(let i=0;i<d.num_options;i++){
    const o=d['opt'+i]; if(!o) continue;
    const primary=i===0;
    const card=document.createElement('div');
    card.className='opt'+(primary?' primary':'');

    const label=primary
      ? 'Best Match \u2014 Recommended For You'
      : 'Alternative '+i;

    let tryonHtml;
    if(o.tryon_status==='done' && o.tryon_b64){
      tryonHtml=`<img src="data:image/png;base64,${o.tryon_b64}" alt="Virtual try-on"/>`;
    }else if(o.tryon_status==='error'){
      tryonHtml=`<div class="tryon-error">Try-on could not be generated${o.tryon_error?': '+o.tryon_error:''}</div>`;
    }else{
      tryonHtml=`<div class="tryon-loading"><div class="mini-spin"></div><p>Generating try-on...</p></div>`;
    }

    const price=o.price.toLocaleString()+' '+o.currency;

    card.innerHTML=`
      <div class="opt-label">${label}</div>
      <div class="opt-body">
        <div class="tryon-col">${tryonHtml}</div>
        <div class="prod-col">
          <img src="data:image/jpeg;base64,${o.product_b64}" alt="${o.name}"/>
          <div class="prod-info">
            <h3>${o.name}</h3>
            <p class="brand">${o.brand} \u2014 ${o.model}</p>
            <div class="tags">
              <span class="tag">${o.shape}</span>
              <span class="tag">${o.material}</span>
              <span class="tag">${o.color}</span>
            </div>
            <p class="price">${price}</p>
            <p class="score">Match: ${(o.score*100).toFixed(1)}%</p>
          </div>
        </div>
      </div>`;
    optsEl.appendChild(card);
  }
}

/* ── Reset ───────────────────────────────────────── */
function reset(){
  sid=null; if(poll)clearTimeout(poll); stopTips();
  fileIn.value=''; optsEl.innerHTML='';
  portraitPreview.style.display='none'; portraitImg.src='';
  progFill.style.width='0%'; setStep(1);
  show('landing');
}
</script>
</body>
</html>
"""


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    host, port = "127.0.0.1", 8080
    server = HTTPServer((host, port), Handler)
    print(f"Lenses UI running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
