# Lenses — AI-Powered Glasses Tools

AI-powered glasses tools powered by Google Gemini — three CLI pipelines plus a web UI for glasses recommendation, virtual try-on, and lens recoloring.

## Prerequisites

- **Python 3.10+**
- **Gemini API key** with image generation enabled (paid plan required)
- Get your key from: https://aistudio.google.com/apikey

## Installation

```bash
cd lenses
pip install -r requirements.txt
```

Set your API key (one-time — all three tools use this):

```bash
export GEMINI_API_KEY='your-key-here'       # Linux/Mac
set GEMINI_API_KEY=your-key-here            # Windows
$env:GEMINI_API_KEY = "your-key-here"       # PowerShell
```

Or create a `.env` file in this directory (copy from `.env.example`).

---

## Project Structure

```
lenses/
├── .env.example              # API key template
├── requirements.txt          # All dependencies
├── README.md                 # This file
├── Procfile                  # Deployment entry point (web: python -m UI.app)
│
├── catalog_manager.py        # Build/validate/list catalog descriptions (build-time, gitignored)
├── tag_schema.py             # Product tag vocabulary + description generator (build-time, gitignored)
│
├── lenses/                   # Shared Catalog
│   └── catalog/              #   Product database (shared by all features)
│       ├── catalog.json      #     Products — full tags + descriptions
│       └── images/           #     Product photos
│
├── UI/                       # Web Interface
│   ├── app.py                #   HTTP server entry point (localhost:8080)
│   ├── config.py             #   Paths, session store, constants
│   ├── handler.py            #   HTTP request routing + multipart parsing
│   ├── pipelines.py          #   Async pipeline execution (background threads)
│   └── templates.py          #   HTML + JavaScript frontend
│
├── lens_recolor/             # Feature 1: Lens Color Swap
│   ├── main.py               #   CLI entry point
│   ├── config.py             #   Model names, defaults
│   ├── recolor.py            #   Gemini API call logic
│   ├── prompt_engine.py      #   Prompt builder for lens recoloring
│   ├── utils.py              #   Image loading/saving/validation
│   └── tests/
│       └── test_recolor.py   #   19 tests
│
├── optimal_configuration/    # Feature 2: Optimal Configuration
│   ├── main.py               #   CLI entry point
│   ├── config.py             #   Model names, catalog paths
│   ├── search_engine.py      #   Tag-overlap catalog search
│   ├── query_interpreter.py  #   LLM query parsing (any language)
│   ├── tryon_engine.py       #   Nano Banana 2-image try-on
│   ├── tryon_prompt_builder.py#  Try-on prompt construction
│   ├── utils.py              #   Image loading/saving/validation
│   └── tests/
│       └── test_search.py    #   23 tests
│
└── face_analysis/            # Feature 3: Face Analysis
    ├── main.py               #   CLI entry point
    ├── config.py             #   Model names, paths
    ├── face_analyzer.py      #   Gemini 2.5 Flash face analysis
    ├── analysis_prompt.py    #   Face analysis prompt (outputs tags + gender)
    ├── inventory_matcher.py  #   Tag-overlap match against catalog
    ├── tryon_engine.py       #   Nano Banana 2-image try-on
    ├── tryon_prompt.py       #   Try-on prompt with face placement data
    ├── report_builder.py     #   Human-readable analysis report
    ├── utils.py              #   Image loading/saving/validation
    └── tests/
        └── test_pipeline.py  #   40 tests
```

---

## Feature 1: Lens Color Swap

**What it does:** Takes a photo of someone wearing glasses and changes ONLY the lens color — everything else stays pixel-perfect.

**Pipeline:** `Input Image → Nano Banana API → Output Image` (1 API call)

### How to run

```bash
cd lens_recolor

# Basic usage
python main.py -i photo.jpg -c "ocean blue"

# With options
python main.py -i photo.jpg -c "rose gold" --intensity light --finish gradient
python main.py -i photo.jpg -c "emerald green" --intensity dark --finish mirror -m nano-banana-pro

# Compare both models side-by-side
python main.py -i photo.jpg -c "amber" --compare

# Dry run (see the prompt without calling the API — free)
python main.py -i photo.jpg -c "emerald green" --dry-run
```

### All Parameters

| Parameter | Required | Default | Description |
|---|---|---|---|
| `--input / -i` | **Yes** | — | Path to input image (portrait with glasses) |
| `--output / -o` | No | auto-generated | Path for the output image |
| `--color / -c` | **Yes** | — | Target lens color — any descriptive string |
| `--intensity` | No | `medium` | Tint darkness: `light` (25-30% opacity), `medium` (50-60%), `dark` (80-90%) |
| `--finish` | No | `standard` | Lens finish style: `standard` (uniform tint), `gradient` (dark top → clear bottom), `mirror` (reflective), `polarized` (subtle sheen) |
| `--model / -m` | No | `nano-banana-2` | Model: `nano-banana-2` (fast) or `nano-banana-pro` (higher quality) |
| `--preserve-reflections` | No | `True` | Keep natural lens reflections. Use `--no-preserve-reflections` to disable |
| `--compare` | No | `False` | Run BOTH models and save a side-by-side comparison image |
| `--dry-run` | No | `False` | Print the full prompt without calling the API (no cost) |

### Supported Colors

Any natural language works — the model interprets it:

- **Basic:** `blue`, `red`, `green`, `yellow`, `purple`, `pink`, `brown`, `gray`
- **Specific shades:** `ocean blue`, `rose gold`, `amber`, `emerald green`, `deep burgundy`, `warm honey brown`, `steel gray`, `midnight blue`, `champagne gold`
- **Industry standard:** `classic aviator green (G-15)`, `Ray-Ban brown (B-15)`, `smoke`, `yellow night-driving`, `photochromic gray`
- **Creative:** `cotton candy pink`, `electric blue`, `sunset orange gradient`, `holographic purple`, `vintage sepia`, `ice blue`

### Tests

```bash
cd lens_recolor
python -m unittest tests.test_recolor -v   # 19 tests
```

---

## Feature 2: Optimal Configuration

**What it does:** Takes a text query describing the glasses you want (in any language) + your portrait, finds the best-matching glasses from a product catalog using tag-based search, then generates a virtual try-on image.

**Pipeline:** `Query + Portrait → Gemini Flash (parse query → tags) → Tag-overlap ranking → Best match → Nano Banana (try-on) → Output Image` (2 API calls)

### First-Time Setup — The Shared Catalog

The catalog ships ready to use (`lenses/catalog/catalog.json` + `images/`). `catalog_manager.py` is an optional build-time helper:

```bash
# Run from project root (catalog_manager.py is a build-time tool, gitignored)
python catalog_manager.py build      # Regenerate product descriptions from tags (offline, no API)
python catalog_manager.py validate   # Verify images exist and tags are valid
python catalog_manager.py list       # List all products
```

The catalog lives in `lenses/catalog/` and is shared by Optimal Configuration, Face Analysis, and the Web UI.

### How to run

```bash
cd optimal_configuration

# Basic usage — interactive product selection
python main.py -p selfie.jpg -q "round glasses with thin gold frame"

# Auto-select best match
python main.py -p selfie.jpg -q "black sunglasses" --auto

# Multi-language queries work
python main.py -p selfie.jpg -q "משקפיים עגולות עם מסגרת זהב"
python main.py -p selfie.jpg -q "lunettes de soleil aviateur dorées"

# Search only, no try-on (saves API cost)
python main.py -p selfie.jpg -q "luxury cat-eye frames" --dry-run

# Show more results
python main.py -p selfie.jpg -q "aviator sunglasses" -k 5

# Use higher quality model
python main.py -p selfie.jpg -q "round gold frames" --auto -m nano-banana-pro
```

### All Parameters

| Parameter | Required | Default | Description |
|---|---|---|---|
| `--portrait / -p` | **Yes** | — | Path to user's portrait photo |
| `--query / -q` | **Yes** | — | Natural language description of desired glasses (any language) |
| `--output / -o` | No | `tryon_result.png` | Output image path |
| `--model / -m` | No | `nano-banana-2` | Model: `nano-banana-2` (fast) or `nano-banana-pro` (higher quality) |
| `--top-k / -k` | No | `3` | Number of search results to display |
| `--auto` | No | `False` | Auto-select the #1 match (skip interactive selection) |
| `--dry-run` | No | `False` | Search only — show results but skip the try-on API call |
| `--results-dir` | No | `./results/` | Directory to save output files |

### How Search Works

1. **Query Interpretation** — Gemini Flash parses your freeform text (any language) into structured query tags + optional filters (price, gender)
2. **Tag-Overlap Ranking** — Your query tags are scored against each product's tags via weighted overlap (instant, local, no API)
3. **Ranking** — Top-K results shown with match scores, filtered by stock status

### Adding Products to the Catalog

1. Add product image to `lenses/catalog/images/` (front-facing, clean background, JPG/PNG/WEBP)
2. Add product entry to `lenses/catalog/catalog.json` with full tags
3. Run `python catalog_manager.py build` from the project root to regenerate descriptions (optional)

### Tests

```bash
cd optimal_configuration
python -m unittest tests.test_search -v   # 24 tests
```

---

## Feature 3: Face Analysis

**What it does:** Analyzes facial features from a portrait, recommends the optimal glasses based on optician-grade face-shape-to-frame rules, matches the recommendation against the real product catalog, and generates a try-on image. Gender is detected and used to filter the catalog to relevant products.

**Pipeline:** `Portrait → Gemini 2.5 Flash (analyze face + recommend tags + detect gender) → Filter by gender → Tag-overlap match against catalog → Nano Banana (try-on with real product photo) → Output Image` (2 API calls + local tag-overlap ranking)

**Depends on:** `lenses/catalog/` (ships ready to use — `catalog.json` + `images/`).

### How to run

```bash
cd face_analysis

# Full pipeline — analysis + matching + try-on
python main.py -p selfie.jpg
python main.py -p selfie.jpg -o result.png --auto

# Analysis only — just face analysis, no matching or try-on (1 API call)
python main.py -p selfie.jpg --dry-run
python main.py -p selfie.jpg --dry-run --save-analysis analysis.json

# Analysis + matching — see what glasses match, skip try-on (2 API calls)
python main.py -p selfie.jpg --report-only
python main.py -p selfie.jpg --report-only -k 5 --save-report report.txt

# Full pipeline with report saved
python main.py -p selfie.jpg -o result.png --auto --save-report report.txt --save-analysis analysis.json

# Higher quality try-on
python main.py -p selfie.jpg -o result_pro.png -m nano-banana-pro --auto
```

### All Parameters

| Parameter | Required | Default | Description |
|---|---|---|---|
| `--portrait / -p` | **Yes** | — | Path to portrait photo (chest-up, face visible) |
| `--output / -o` | No | `face_analysis_result.png` | Output image path |
| `--model / -m` | No | `nano-banana-2` | Nano Banana model: `nano-banana-2` (fast) or `nano-banana-pro` (higher quality) |
| `--analysis-model` | No | `gemini-2.5-flash` | Model for face analysis (keep default in most cases) |
| `--top-k / -k` | No | `3` | Number of inventory matches to show |
| `--auto` | No | `False` | Auto-select #1 match without interactive selection |
| `--save-report` | No | — | Save human-readable report to a text file |
| `--save-analysis` | No | — | Save raw analysis JSON to a file |
| `--report-only` | No | `False` | Do analysis + matching, skip the try-on image generation |
| `--dry-run` | No | `False` | Do analysis only, skip matching + try-on |

### What the Analysis Covers

The face analysis extracts:
- **Face shape** — oval, round, square, heart, diamond, oblong, rectangular, triangle
- **Gender** — detected from the portrait; used to filter catalog to relevant products
- **Forehead** — width, height, hairline shape
- **Cheekbones** — prominence, width relative to jaw
- **Jawline** — shape, definition, width relative to forehead
- **Chin** — shape, size
- **Nose bridge** — width, height (critical for glasses fit)
- **Eyes** — spacing, size, shape, color
- **Eyebrows** — shape, thickness
- **Skin** — tone, undertone (warm/cool/neutral — affects frame color recommendation)
- **Hair** — color, style, texture, length
- **Facial hair** — presence, type
- **Photo context** — lighting, setting, face angle

The recommendation applies optician rules:
- Round face → angular frames; square face → rounder frames
- Warm skin undertone → gold/tortoiseshell; cool → silver/black
- Narrow nose bridge → metal frames with adjustable pads
- Frame width should match face width at temples

### How the Pipeline Connects to the Catalog

The face analysis outputs `recommended_tags` using the **exact same tag vocabulary** as the product catalog (`lenses/catalog/catalog.json`). The recommended tags are scored directly against each product's tags via weighted overlap. Because both sides share one vocabulary, the recommendation and catalog match on **identical structured attributes** — no embeddings or external API needed.

### Tests

```bash
cd face_analysis
python -m unittest tests.test_pipeline -v   # 41 tests
```

---

## Web UI

**What it does:** Browser-based interface with four modes — **Smart Fit** (face analysis pipeline), **Free Search** (preference-based search pipeline), **Lens Recolor** (lens color swap pipeline), and **Storefront** (browse the full product catalog and try on any product). Results load progressively as each try-on finishes.

### How to run

```bash
python -m UI.app
```

Open http://127.0.0.1:8080 in your browser.

### Modes

- **Smart Fit** (`/`) — Upload a portrait. Analyzes facial features and gender, matches against the catalog (filtered by gender), and generates try-on images for the top 3 recommended products.
- **Free Search** (`/free-search`) — Upload a portrait + choose preferences (frame shape, color, material, thickness, rim type, lens type, lens size, aesthetic, gender, occasion, max price). Runs semantic search against the catalog and generates try-on images.
- **Lens Recolor** (`/lens-recolor`) — Upload a photo of someone wearing glasses + pick up to 3 lens colors from a 16-color palette. Generates recolored versions for each color.
- **Storefront** (`/storefront`) — Browse the full product catalog in a grid. Click any product to upload a portrait and get a virtual try-on. Click a color swatch to recolor the lenses on a product photo.

Results stream in progressively via polling (`/api/status/<id>`, `/api/recolor-status/<id>`, `/api/storefront-recolor-status/<id>`).

### API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Smart Fit / landing page |
| `GET` | `/free-search` | Free Search page |
| `GET` | `/lens-recolor` | Lens Recolor page |
| `GET` | `/storefront` | Storefront catalog page |
| `GET` | `/api/catalog` | JSON list of all products |
| `GET` | `/api/catalog-image/<filename>` | Serve a product image |
| `GET` | `/api/status/<id>` | Poll Smart Fit / Free Search result |
| `GET` | `/api/recolor-status/<id>` | Poll Lens Recolor result |
| `GET` | `/api/storefront-recolor-status/<id>` | Poll Storefront recolor result |
| `POST` | `/api/upload` | Start Smart Fit pipeline |
| `POST` | `/api/free-search` | Start Free Search pipeline |
| `POST` | `/api/lens-recolor` | Start Lens Recolor pipeline |
| `POST` | `/api/storefront-tryon` | Start Storefront try-on |
| `POST` | `/api/storefront-recolor` | Start Storefront single-color recolor |

---

## Models Used

| Model | Purpose | Used by |
|---|---|---|
| `gemini-3.1-flash-image-preview` (nano-banana-2) | Image generation — fast | All features + Web UI |
| `gemini-3-pro-image-preview` (nano-banana-pro) | Image generation — high quality | All features + Web UI |
| `gemini-2.5-flash` | Face analysis (vision + reasoning) | Face Analysis, Web UI (Smart Fit) |
| `gemini-2.5-flash` | Query interpretation (text) | Optimal Configuration, Free Search |

## Troubleshooting

| Issue | Solution |
|---|---|
| `GEMINI_API_KEY not set` | Set the env var or create a `.env` file — key from aistudio.google.com |
| `Rate limited / 429` | Wait a moment, then retry |
| `Safety filter blocked` | The image was flagged — try a different photo |
| `No image in response` | Tools retry automatically. If persistent, try a different portrait or model |
| `API key issue / 403` | Ensure your key has image generation enabled (paid plan) |
| `Catalog not found` | Ensure `lenses/catalog/catalog.json` and `images/` are present |
| `Low match scores` | Add more diverse products to the catalog |

## Running All Tests

```bash
cd lens_recolor && python -m unittest tests.test_recolor -v && cd ../optimal_configuration && python -m unittest tests.test_search -v && cd ../face_analysis && python -m unittest tests.test_pipeline -v
```

All 82 tests should pass (19 + 23 + 40).
