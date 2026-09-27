"""Build the owner's blind A/B rating page for a BSA run vs the previous route.

Usage (from automation/): python -m bsa.blind [run]   (default run: m1)

A/B assignment is a keyed hash per product (not visible in the page); the key is written
separately. Images are the actual-AR renders produced by the pipeline (same harness, same
views, same lighting for both models) plus the product photos.
"""
import base64, hashlib, io, json, sys
from PIL import Image

from bsa.core import BSA_DATA, PRODUCTS, VIEWS

RUN_NAME = sys.argv[1] if len(sys.argv) > 1 else 'm1'
RUN = BSA_DATA / 'runs' / RUN_NAME
OUT = RUN / 'blind'
OUT.mkdir(exist_ok=True)
AR_VIEWS = ('front', 'angled', 'rolled')


def jpg(path, width):
    im = Image.open(path).convert('RGB')
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.Resampling.LANCZOS)
    buf = io.BytesIO(); im.save(buf, 'JPEG', quality=86)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()


def renders(kind, product):
    base = RUN / 's9_ar' / 'all' if kind == 'bsa' else RUN / 'previous_ar'
    tag = f'{product}-bsa-m1' if kind == 'bsa' else f'{product}-previous'
    paths = [base / f'{tag}__actual-ar__{v}.png' for v in AR_VIEWS]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise FileNotFoundError(missing)
    return paths


key, sections = {}, []
order = sorted(PRODUCTS, key=lambda p: hashlib.sha256(f'order:{p}:bsa-m1'.encode()).hexdigest())
for n, p in enumerate(order, 1):
    bsa_is_a = int(hashlib.sha256(f'side:{p}:bsa-m1-blind'.encode()).hexdigest(), 16) % 2 == 0
    key[f'product_{n}'] = {'product': p, 'A': 'bsa' if bsa_is_a else 'previous', 'B': 'previous' if bsa_is_a else 'bsa'}
    photos = ''.join(f'<figure><img src="{jpg(PRODUCTS[p].photo_path(v), 520)}" alt="{v} photo"><figcaption>{v}</figcaption></figure>' for v in VIEWS)
    opts = []
    for label in ('A', 'B'):
        kind = key[f'product_{n}'][label]
        imgs = ''.join(f'<figure><img src="{jpg(q, 640)}" alt="option {label} {v}"><figcaption>{v}</figcaption></figure>'
                       for q, v in zip(renders(kind, p), AR_VIEWS))
        sel = ''.join(f'<option value="{i}">{i}</option>' for i in range(1, 11))
        opts.append(f'<div class="opt"><h3>Option {label}</h3><div class="row">{imgs}</div>'
                    f'<label>How much does it look like the product? <select data-q="p{n}_{label}"><option value="">-</option>{sel}</select> / 10</label></div>')
    pref = ''.join(f'<label><input type="radio" name="p{n}_pref" value="{v}"> {t}</label>'
                   for v, t in (('A', 'A is better'), ('equal', 'About the same'), ('B', 'B is better')))
    sections.append(f'<section><h2>Product {n}</h2><div class="row photos">{photos}</div>{"".join(opts)}'
                    f'<div class="pref">{pref}</div><textarea data-q="p{n}_note" placeholder="Optional: what is wrong with either?"></textarea></section>')

html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blind Glasses Rating</title>
<style>
:root{{--bg:#f6f5f2;--fg:#1d1d1f;--muted:#6b6b70;--card:#fff;--line:#e3e1dc;--accent:#2f5d8a}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#16171a;--fg:#ececef;--muted:#a0a0a8;--card:#1f2024;--line:#33343a;--accent:#8db4dc}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px 80px}}
h1{{font-size:24px;margin:0 0 6px}} p.lead{{color:var(--muted);margin:0 0 24px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:0 0 28px}}
h2{{margin:0 0 10px;font-size:18px}} h3{{margin:14px 0 6px;font-size:16px}}
.row{{display:flex;gap:8px;overflow-x:auto}} figure{{margin:0;flex:0 0 auto}} figure img{{height:150px;border-radius:6px;display:block}}
.photos figure img{{height:95px;background:#fff}} figcaption{{font-size:12px;color:var(--muted);text-align:center}}
.opt{{border-top:1px solid var(--line);margin-top:10px}} label{{display:inline-block;margin:8px 16px 0 0}}
select,textarea{{font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:4px}}
textarea{{width:100%;box-sizing:border-box;min-height:48px;margin-top:10px}}
.pref{{margin-top:10px;font-weight:600}} button{{font:inherit;background:var(--accent);color:#fff;border:0;border-radius:8px;padding:10px 16px;cursor:pointer}}
#out{{width:100%;min-height:120px;margin-top:12px}}
@media (max-width:640px){{figure img{{height:110px}} .photos figure img{{height:70px}}}}
</style></head><body><main>
<h1>Blind rating: which 3D model looks more like the product?</h1>
<p class="lead">Each product shows its photos, then two 3D models rendered in the actual try-on renderer (same lighting, same angles). The two options are shown in a random order per product. Rate each 1-10 and pick the better one. When done, press the button and paste the text back to Claude.</p>
{"".join(sections)}
<button id="copy">Copy my answers</button><textarea id="out" readonly></textarea>
</main><script>
document.getElementById('copy').onclick=async()=>{{const a={{}};
document.querySelectorAll('[data-q]').forEach(e=>a[e.dataset.q]=e.value);
document.querySelectorAll('input[type=radio]:checked').forEach(e=>a[e.name]=e.value);
const t=JSON.stringify({{page:'m1-blind-rating',answers:a}});document.getElementById('out').value=t;
try{{await navigator.clipboard.writeText(t)}}catch(e){{}}}};
</script></body></html>'''
(OUT / 'blind_rating.html').write_text(html, encoding='utf-8')
(OUT / 'blind_key.json').write_text(json.dumps(key, indent=1))
print(OUT / 'blind_rating.html', round(len(html) / 1e6, 2), 'MB')
