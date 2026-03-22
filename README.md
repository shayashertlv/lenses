# Lenses — AI-Powered Glasses Tools

Three Python CLI tools powered by Google Gemini for glasses-related image editing and recommendation.

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
├── .env.example              # API key template (one for whole project)
├── requirements.txt          # All dependencies (one for whole project)
├── README.md                 # This file
│
├── lenses/                   # Erroca Product Catalog & Scraping Tools
│   ├── scrape_images.py      #   Scrape product image URLs from erroca.co.il
│   ├── download_images.py    #   Download images as JPG with retry logic
│   ├── run_all.py            #   Orchestrator: scrape → download
│   ├── catalog_manager.py    #   Build/validate/list catalog + embeddings
│   ├── tag_schema.py         #   Product tag vocabulary + description gen
│   ├── requirements.txt      #   Scraping dependencies
│   ├── image_urls.txt        #   Discovered image URLs (auto-generated)
│   ├── catalog/              #   Product database (shared by all features)
│   │   ├── catalog.json      #     37 products — full tags + descriptions
│   │   ├── embeddings.npy    #     Pre-computed embedding vectors (3072-dim)
│   │   ├── embedding_index.json#   Index mapping
│   │   └── images/           #     Product photos (erroca_mens_001.jpg, ...)
│   └── downloaded_images/    #   Raw downloads (before catalog curation)
│
├── lens_recolor/             # Feature 1: Lens Color Swap
│   ├── main.py               #   CLI entry point
│   ├── config.py             #   Model names, defaults
│   ├── recolor.py            #   Gemini API call logic
│   ├── prompt_engine.py      #   Prompt builder for lens recoloring
│   ├── utils.py              #   Image loading/saving/validation
│   ├── examples/             #   Sample input/output images
│   └── tests/
│       └── test_recolor.py   #   19 tests
│
├── optimal_configuration/    # Feature 2: Optimal Configuration
│   ├── main.py               #   CLI entry point
│   ├── config.py             #   Model names, catalog paths → lenses/catalog/
│   ├── search_engine.py      #   Embedding-based semantic search
│   ├── query_interpreter.py  #   LLM query parsing (any language)
│   ├── tag_schema.py         #   Product tag vocabulary + description gen
│   ├── catalog_manager.py    #   Build/validate/list catalog
│   ├── tryon_engine.py       #   Nano Banana 2-image try-on
│   ├── tryon_prompt_builder.py#  Try-on prompt construction
│   ├── utils.py              #   Image loading/saving/validation
│   └── tests/
│       └── test_search.py    #   24 tests
│
└── face_analysis/            # Feature 3: Face Analysis
    ├── main.py               #   CLI entry point
    ├── config.py             #   Model names, paths → lenses/catalog/
    ├── face_analyzer.py      #   Gemini 2.5 Flash face analysis
    ├── analysis_prompt.py    #   Face analysis prompt (outputs tags)
    ├── inventory_matcher.py  #   Embed tags → match against catalog
    ├── tryon_engine.py       #   Nano Banana 2-image try-on
    ├── tryon_prompt.py       #   Try-on prompt with face placement data
    ├── report_builder.py     #   Human-readable analysis report
    ├── utils.py              #   Image loading/saving/validation
    └── tests/
        └── test_pipeline.py  #   41 tests
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

**What it does:** Takes a text query describing the glasses you want (in any language) + your portrait, finds the best-matching glasses from a product catalog using semantic search, then generates a virtual try-on image.

**Pipeline:** `Query + Portrait → Gemini Flash (parse query) → Embed → Cosine similarity → Best match → Nano Banana (try-on) → Output Image` (3 API calls)

### First-Time Setup — Build the Catalog Index

Before using this feature, you must build the embedding index from the shared catalog:

```bash
cd lenses
python catalog_manager.py build      # Generates descriptions + embeddings (requires API)
python catalog_manager.py validate   # Verify images exist and tags are valid
python catalog_manager.py list       # List all 37 products
```

The catalog lives in `lenses/catalog/` and is shared by both Optimal Configuration and Face Analysis.

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

1. **Query Interpretation** — Gemini Flash parses your freeform text (any language) into structured search intent + optional filters (price, gender)
2. **Embedding** — Your parsed query is embedded with `gemini-embedding-001` (supports 100+ languages)
3. **Cosine Similarity** — Compared against pre-computed product embeddings (instant, local numpy)
4. **Ranking** — Top-K results shown with similarity scores, filtered by stock status

### Adding Products to the Catalog

1. Add product image to `lenses/catalog/images/` (front-facing, clean background, JPG/PNG/WEBP)
2. Add product entry to `lenses/catalog/catalog.json` with full tags
3. Run `cd lenses && python catalog_manager.py build` to rebuild embeddings

### Tests

```bash
cd optimal_configuration
python -m unittest tests.test_search -v   # 24 tests
```

---

## Feature 3: Face Analysis

**What it does:** Analyzes facial features from a portrait, recommends the optimal glasses based on optician-grade face-shape-to-frame rules, matches the recommendation against the real product catalog, and generates a try-on image.

**Pipeline:** `Portrait → Gemini 2.5 Flash (analyze face + recommend tags) → Embed tags → Match against catalog → Nano Banana (try-on with real product photo) → Output Image` (3 API calls + local cosine similarity)

**Depends on:** `lenses/catalog/` must be set up with embeddings built (`cd lenses && python catalog_manager.py build`).

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

The face analysis outputs `recommended_tags` using the **exact same tag vocabulary** as the product catalog (`lenses/catalog/catalog.json`). These tags are converted into a natural-language description using the same template as catalog products, embedded with the same model (`gemini-embedding-001`), and compared via cosine similarity. This ensures the recommendation and catalog live in the **same semantic space**.

### Tests

```bash
cd face_analysis
python -m unittest tests.test_pipeline -v   # 41 tests
```

---

## Models Used

| Model | Purpose | Used by |
|---|---|---|
| `gemini-3.1-flash-image-preview` (nano-banana-2) | Image generation — fast | All 3 features |
| `gemini-3-pro-image-preview` (nano-banana-pro) | Image generation — high quality | All 3 features |
| `gemini-2.5-flash` | Face analysis (vision + reasoning) | Face Analysis |
| `gemini-2.5-flash` | Query interpretation (text) | Optimal Configuration |
| `gemini-embedding-001` | Text embedding (3072-dim, 100+ languages) | Optimal Config + Face Analysis |

## Troubleshooting

| Issue | Solution |
|---|---|
| `GEMINI_API_KEY not set` | Set the env var or create a `.env` file — key from aistudio.google.com |
| `Rate limited / 429` | Wait a moment, then retry |
| `Safety filter blocked` | The image was flagged — try a different photo |
| `No image in response` | Tools retry automatically. If persistent, try a different portrait or model |
| `API key issue / 403` | Ensure your key has image generation enabled (paid plan) |
| `Catalog not found` | Run `cd lenses && python catalog_manager.py build` |
| `embeddings.npy not found` | Same — build the catalog first |
| `Low match scores` | Add more diverse products to the catalog |

## Running All Tests

```bash
cd lens_recolor && python -m unittest tests.test_recolor -v && cd ../optimal_configuration && python -m unittest tests.test_search -v && cd ../face_analysis && python -m unittest tests.test_pipeline -v
```

All 84 tests should pass (19 + 24 + 41).
