"""Fix women's glasses tags based on visual inspection of actual images."""
import json
from pathlib import Path

CATALOG_JSON = Path(r"C:\Users\Shay\PycharmProjects\lenses\lenses\catalog\catalog.json")

# Corrections based on visual inspection of every image.
# Keys are product IDs; values are nested dicts of tag fields to overwrite.
CORRECTIONS = {
    # ── EYEGLASSES ────────────────────────────────────────────────────────────
    "erroca_075": {  # VOGUE VO5603 – rectangular black acetate full-rim medium
        "name": "Vogue Eyewear VO5603 Rectangular Black",
        "frame": {"shape": "rectangular", "color": ["black"], "thickness": "medium"},
        "lenses": {"shape": "rectangular"},
        "style": {"aesthetic": ["modern", "casual", "bold"]},
    },
    "erroca_076": {  # VOGUE VO5518 – rectangular pink/transparent acetate full-rim medium
        "name": "Vogue Eyewear VO5518 Rectangular Pink",
        "frame": {"color": ["pink", "transparent"], "thickness": "medium"},
        "lenses": {"shape": "rectangular"},
    },
    "erroca_077": {  # VOGUE VO5563 – rectangular black mixed-metal-acetate full-rim medium
        "name": "Vogue Eyewear VO5563 Rectangular Black",
        "frame": {"shape": "rectangular", "color": ["black"], "material": "mixed-metal-acetate", "thickness": "medium"},
        "lenses": {"shape": "rectangular"},
        "style": {"aesthetic": ["modern", "bold", "professional"]},
    },
    "erroca_078": {  # VOGUE VO4333 – rectangular gold metal full-rim thin
        "name": "Vogue Eyewear VO4333 Rectangular Gold",
        "frame": {"shape": "rectangular"},
        "lenses": {"shape": "rectangular"},
    },
    "erroca_079": {  # VOGUE VO5239 – rectangular havana mixed-metal-acetate full-rim medium
        "name": "Vogue Eyewear VO5239 Rectangular Havana",
        "frame": {"shape": "rectangular", "color": ["havana-brown"], "material": "mixed-metal-acetate", "thickness": "medium"},
        "lenses": {"shape": "rectangular"},
        "style": {"aesthetic": ["classic", "casual", "modern"]},
    },
    "erroca_080": {  # Prada PR A57V – oval gold metal rimless thin
        "name": "Prada PR A57V Oval Gold Rimless",
        "frame": {"shape": "oval", "color": ["gold"], "material": "metal", "rim_type": "rimless", "thickness": "thin", "finish": "brushed"},
        "lenses": {"shape": "oval"},
        "style": {"aesthetic": ["luxury", "minimalist", "professional"]},
    },
    "erroca_081": {  # VOGUE VO5632B – rectangular havana acetate full-rim medium
        "name": "Vogue Eyewear VO5632B Rectangular Havana",
        "frame": {"shape": "rectangular", "color": ["havana-brown"], "thickness": "medium"},
        "lenses": {"shape": "rectangular"},
        "style": {"aesthetic": ["classic", "casual", "modern"]},
    },
    "erroca_082": {  # VOGUE VO4333 – rectangular havana mixed-metal-acetate full-rim medium
        "name": "Vogue Eyewear VO4333 Rectangular Havana",
        "frame": {"shape": "rectangular", "color": ["havana-brown"], "material": "mixed-metal-acetate", "thickness": "medium"},
        "lenses": {"shape": "rectangular"},
        "style": {"aesthetic": ["classic", "casual", "modern"]},
    },
    "erroca_083": {  # Prada PR A16V – cat-eye black acetate full-rim THICK
        "frame": {"thickness": "thick"},
        "style": {"aesthetic": ["luxury", "bold", "fashion-forward"]},
    },
    "erroca_084": {  # VOGUE VO4334 – rectangular gold metal full-rim thin
        "name": "Vogue Eyewear VO4334 Rectangular Gold",
        "frame": {"shape": "rectangular"},
        "lenses": {"shape": "rectangular"},
    },
    "erroca_085": {  # Ray-Ban RX6513 – rectangular black mixed-metal-acetate semi-rimless medium
        "frame": {"color": ["black"], "material": "mixed-metal-acetate"},
    },
    "erroca_086": {  # Prada PR 02ZV – square black acetate full-rim thick
        "name": "Prada PR 02ZV Square Black",
        "frame": {"shape": "square", "thickness": "thick"},
        "lenses": {"shape": "square"},
        "style": {"aesthetic": ["luxury", "bold", "modern"]},
    },
    "erroca_087": {  # Prada PR 11YV – square tortoiseshell acetate full-rim thick
        "name": "Prada PR 11YV Square Tortoiseshell",
        "frame": {"shape": "square", "thickness": "thick"},
        "lenses": {"shape": "square"},
        "style": {"aesthetic": ["luxury", "bold", "fashion-forward"]},
    },
    "erroca_088": {  # Prada PR 15ZV – square black acetate full-rim thick
        "name": "Prada PR 15ZV Square Black",
        "frame": {"shape": "square", "thickness": "thick"},
        "lenses": {"shape": "square"},
        "style": {"aesthetic": ["luxury", "bold", "modern"]},
    },
    "erroca_089": {  # Prada PR 11YV v2 – square pink/tortoiseshell acetate full-rim thick
        "name": "Prada PR 11YV Square Pink Tortoiseshell",
        "frame": {"shape": "square", "color": ["pink", "tortoiseshell"], "thickness": "thick"},
        "lenses": {"shape": "square"},
        "style": {"aesthetic": ["luxury", "bold", "fashion-forward"]},
    },
    "erroca_090": {  # Prada PR 15ZV v2 – square black acetate full-rim thick glossy
        "name": "Prada PR 15ZV Square Black Bold",
        "frame": {"shape": "square", "color": ["black"], "material": "acetate", "thickness": "thick", "finish": "glossy"},
        "lenses": {"shape": "square"},
        "style": {"aesthetic": ["luxury", "bold", "modern"]},
    },
    "erroca_091": {  # Versace VE3367U – square black acetate full-rim thick
        "name": "Versace VE3367U Square Black",
        "frame": {"shape": "square", "color": ["black"], "material": "acetate", "thickness": "thick"},
        "lenses": {"shape": "square"},
        "style": {"aesthetic": ["luxury", "bold", "streetwear"]},
    },
    "erroca_092": {  # Versace VE1247 – rectangular black mixed-metal-acetate semi-rimless medium
        "name": "Versace VE1247 Rectangular Black Semi-Rimless",
        "frame": {"shape": "rectangular", "color": ["black"], "material": "mixed-metal-acetate", "rim_type": "semi-rimless", "thickness": "medium"},
        "lenses": {"shape": "rectangular"},
        "style": {"aesthetic": ["luxury", "professional", "modern"]},
    },
    "erroca_093": {  # Versace VE1298 – rectangular gold/tortoiseshell mixed-metal-acetate semi-rimless thin
        "frame": {"color": ["gold", "tortoiseshell"], "material": "mixed-metal-acetate", "rim_type": "semi-rimless", "thickness": "thin"},
        "lenses": {"shape": "rectangular"},
    },
    "erroca_094": {  # D&G DG1346 – rectangular gold metal semi-rimless thin
        "name": "Dolce & Gabbana DG1346 Rectangular Gold Semi-Rimless",
        "frame": {"shape": "rectangular", "color": ["gold"], "material": "metal", "rim_type": "semi-rimless", "thickness": "thin"},
        "lenses": {"shape": "rectangular"},
        "style": {"aesthetic": ["luxury", "minimalist", "professional"]},
    },
    "erroca_095": {  # Miu Miu MU 04WV – square tortoiseshell acetate full-rim thick
        "name": "Miu Miu MU 04WV Square Tortoiseshell",
        "frame": {"shape": "square", "thickness": "thick"},
        "lenses": {"shape": "square"},
        "style": {"aesthetic": ["luxury", "bold", "fashion-forward"]},
    },
    "erroca_096": {  # Versace VE3353 – ROUND tortoiseshell acetate full-rim medium
        "name": "Versace VE3353 Round Tortoiseshell",
        "frame": {"shape": "round"},
        "lenses": {"shape": "round"},
        "style": {"aesthetic": ["luxury", "vintage", "classic"]},
    },
    "erroca_097": {  # Versace VE3294 – square black acetate full-rim thick
        "name": "Versace VE3294 Square Black",
        "frame": {"shape": "square", "color": ["black"], "material": "acetate", "thickness": "thick"},
        "lenses": {"shape": "square"},
        "style": {"aesthetic": ["luxury", "bold", "streetwear"]},
    },
    "erroca_098": {  # Versace VE3347 – square pink/tortoiseshell acetate full-rim thick
        "name": "Versace VE3347 Square Pink Tortoiseshell",
        "frame": {"shape": "square", "color": ["pink", "tortoiseshell"], "thickness": "thick"},
        "lenses": {"shape": "square"},
        "style": {"aesthetic": ["luxury", "bold", "fashion-forward"]},
    },
    "erroca_099": {  # D&G DG3360 – square black acetate full-rim thick
        "name": "Dolce & Gabbana DG3360 Square Black",
        "frame": {"shape": "square", "color": ["black"], "thickness": "thick"},
        "lenses": {"shape": "square"},
        "style": {"aesthetic": ["luxury", "bold", "modern"]},
    },
    "erroca_100": {  # Ray-Ban RX6335 – rectangular blue mixed-metal-acetate semi-rimless thin
        "name": "Ray-Ban RX6335 Rectangular Blue Semi-Rimless",
        "frame": {"color": ["blue"], "material": "mixed-metal-acetate", "rim_type": "semi-rimless"},
        "lenses": {"shape": "rectangular"},
    },
    "erroca_101": {  # Ray-Ban RX8416 – rectangular black titanium semi-rimless thin
        "name": "Ray-Ban RX8416 Titanium Rectangular Black Semi-Rimless",
        "frame": {"color": ["black"], "rim_type": "semi-rimless"},
        "lenses": {"shape": "rectangular"},
    },
    "erroca_102": {  # Ray-Ban RX7047 – rectangular black plastic full-rim medium (correct)
        "lenses": {"shape": "rectangular"},
    },
    "erroca_103": {  # Ray-Ban RX5441 – ROUND black acetate full-rim medium
        "name": "Ray-Ban RX5441 Round Black",
        "frame": {"shape": "round"},
        "lenses": {"shape": "round"},
        "style": {"aesthetic": ["classic", "vintage", "retro"]},
    },
    "erroca_104": {  # Miu Miu MU 53WV – oval gold metal rimless thin
        "name": "Miu Miu MU 53WV Oval Gold Rimless",
        "frame": {"shape": "oval", "color": ["gold"], "material": "metal", "rim_type": "rimless", "thickness": "thin", "finish": "brushed"},
        "lenses": {"shape": "oval"},
        "style": {"aesthetic": ["luxury", "minimalist", "modern"]},
    },
    "erroca_105": {  # Miu Miu MU 03WV – square black acetate full-rim thick
        "name": "Miu Miu MU 03WV Square Black",
        "frame": {"shape": "square", "color": ["black"], "thickness": "thick"},
        "lenses": {"shape": "square"},
        "style": {"aesthetic": ["luxury", "bold", "modern"]},
    },
    "erroca_106": {  # Miu Miu MU 07XV Brown – square tortoiseshell acetate full-rim thick
        "name": "Miu Miu MU 07XV Square Tortoiseshell",
        "frame": {"shape": "square", "color": ["tortoiseshell"], "thickness": "thick"},
        "lenses": {"shape": "square"},
        "style": {"aesthetic": ["luxury", "bold", "fashion-forward"]},
    },
    "erroca_107": {  # Miu Miu MU 07XV Black – square black acetate full-rim thick
        "name": "Miu Miu MU 07XV Square Black",
        "frame": {"shape": "square", "thickness": "thick"},
        "lenses": {"shape": "square"},
        "style": {"aesthetic": ["luxury", "bold", "modern"]},
    },
    "erroca_108": {  # Miu Miu MU 04UV – square black acetate full-rim thick
        "name": "Miu Miu MU 04UV Square Black",
        "frame": {"shape": "square", "color": ["black"], "thickness": "thick"},
        "lenses": {"shape": "square"},
        "style": {"aesthetic": ["luxury", "bold", "modern"]},
    },
    "erroca_109": {  # Miu Miu MU 02WV – ROUND tortoiseshell acetate full-rim medium
        "name": "Miu Miu MU 02WV Round Tortoiseshell",
        "frame": {"shape": "round", "color": ["tortoiseshell"]},
        "lenses": {"shape": "round"},
        "style": {"aesthetic": ["luxury", "vintage", "fashion-forward"]},
    },
    "erroca_110": {  # Miu Miu MU 09XV – oval tortoiseshell acetate full-rim medium
        "name": "Miu Miu MU 09XV Oval Tortoiseshell",
        "frame": {"shape": "oval", "color": ["tortoiseshell"], "material": "acetate"},
        "lenses": {"shape": "oval"},
        "style": {"aesthetic": ["luxury", "classic", "fashion-forward"]},
    },
    "erroca_111": {  # Miu Miu MU 04WV Black – square black acetate full-rim thick
        "name": "Miu Miu MU 04WV Square Black",
        "frame": {"shape": "square", "thickness": "thick"},
        "lenses": {"shape": "square"},
        "style": {"aesthetic": ["luxury", "bold", "modern"]},
    },

    # ── SUNGLASSES ────────────────────────────────────────────────────────────
    "erroca_112": {  # Prada PRS C05S – oval tortoiseshell acetate full-rim gray lenses
        "name": "Prada PRS C05S Oval Tortoiseshell Sunglasses",
        "frame": {"shape": "oval", "color": ["tortoiseshell"]},
        "lenses": {"color": ["gray"], "shape": "oval"},
        "style": {"aesthetic": ["luxury", "classic", "fashion-forward"]},
    },
    "erroca_113": {  # Miu Miu MUA56S – aviator gold metal semi-rimless brown gradient
        "name": "Miu Miu MUA56S Aviator Gold Sunglasses",
        "frame": {"shape": "aviator", "rim_type": "semi-rimless"},
        "lenses": {"shape": "teardrop"},
    },
    "erroca_114": {  # Miu Miu MUA56S v2 – aviator gold metal semi-rimless gray lenses
        "name": "Miu Miu MUA56S Aviator Gold Gray Sunglasses",
        "frame": {"shape": "aviator", "color": ["gold"], "rim_type": "semi-rimless"},
        "lenses": {"color": ["gray"], "shape": "teardrop"},
    },
    "erroca_115": {  # Miu Miu MUA56S v3 – aviator gold metal semi-rimless brown gradient lenses
        "name": "Miu Miu MUA56S Aviator Gold Brown Sunglasses",
        "frame": {"shape": "aviator", "color": ["gold"], "rim_type": "semi-rimless"},
        "lenses": {"color": ["gradient-brown"], "shape": "teardrop"},
    },
    "erroca_116": {  # Versace VE2274 Gold – hexagonal gold metal rimless green lenses
        "name": "Versace VE2274 Hexagonal Gold Sunglasses",
        "frame": {"shape": "hexagonal", "rim_type": "rimless"},
        "lenses": {"color": ["green"], "shape": "rectangular"},
    },
    "erroca_117": {  # Versace VE4501 – oval tortoiseshell acetate full-rim gray lenses
        "name": "Versace VE4501 Oval Tortoiseshell Sunglasses",
        "frame": {"shape": "oval", "color": ["tortoiseshell"]},
        "lenses": {"color": ["gray"], "shape": "oval"},
        "style": {"aesthetic": ["luxury", "classic", "vintage"]},
    },
    "erroca_118": {  # Versace VE4504D – rectangular black acetate full-rim gray (CORRECT)
        "lenses": {"shape": "rectangular"},
    },
    "erroca_119": {  # Versace VE4497U – WRAP black acetate full-rim gray lenses
        "name": "Versace VE4497U Wrap Black Sunglasses",
        "frame": {"shape": "wrap"},
        "lenses": {"shape": "curved-wrap"},
        "style": {"aesthetic": ["luxury", "sporty", "bold"]},
    },
    "erroca_120": {  # Versace VE2274 Silver – hexagonal silver metal rimless gray lenses
        "name": "Versace VE2274 Hexagonal Silver Sunglasses",
        "frame": {"shape": "hexagonal", "rim_type": "rimless"},
        "lenses": {"color": ["gray"], "shape": "rectangular"},
    },
    "erroca_121": {  # Ray-Ban RB3767 – oval gold metal rimless green lenses
        "name": "Ray-Ban RB3767 Oval Gold Sunglasses",
        "frame": {"shape": "oval", "color": ["gold"], "rim_type": "rimless"},
        "lenses": {"color": ["green"], "shape": "oval"},
    },
    "erroca_122": {  # Prada PRS C05S v2 – oval black acetate full-rim gray lenses
        "name": "Prada PRS C05S Oval Black Sunglasses",
        "frame": {"shape": "oval", "color": ["black"]},
        "lenses": {"color": ["gray"], "shape": "oval"},
        "style": {"aesthetic": ["luxury", "modern", "bold"]},
    },
    "erroca_123": {  # Prada PRS C04S – cat-eye tortoiseshell acetate full-rim gradient-brown lenses
        "name": "Prada PRS C04S Cat-Eye Tortoiseshell Sunglasses",
        "frame": {"shape": "cat-eye", "color": ["tortoiseshell"]},
        "lenses": {"color": ["gradient-brown"], "shape": "oval"},
        "style": {"aesthetic": ["luxury", "vintage", "fashion-forward"]},
    },
    "erroca_124": {  # Ray-Ban RB4454 – rectangular black acetate full-rim gray lenses
        "name": "Ray-Ban RB4454 Rectangular Black Sunglasses",
        "frame": {"shape": "rectangular", "color": ["black"]},
        "lenses": {"color": ["gray"], "shape": "rectangular"},
        "style": {"aesthetic": ["classic", "modern", "casual"]},
    },
    "erroca_125": {  # Oakley OO9517 – wrap silver metal semi-rimless gray lenses
        "frame": {"color": ["silver"], "material": "metal", "rim_type": "semi-rimless", "thickness": "medium"},
    },
    "erroca_126": {  # Oakley OO9463 – wrap black acetate semi-rimless amber lenses
        "frame": {"material": "acetate", "rim_type": "semi-rimless"},
        "lenses": {"color": ["amber"]},
    },
    "erroca_127": {  # D&G DG4519 – oval black acetate full-rim gray lenses
        "name": "Dolce & Gabbana DG4519 Oval Black Sunglasses",
        "frame": {"shape": "oval", "thickness": "medium"},
        "lenses": {"color": ["gray"], "shape": "oval"},
        "style": {"aesthetic": ["luxury", "bold", "fashion-forward"]},
    },
    "erroca_128": {  # Oakley OO9518 – wrap yellow acetate semi-rimless amber lenses
        "frame": {"color": ["yellow"], "material": "acetate", "rim_type": "semi-rimless", "thickness": "medium"},
        "lenses": {"color": ["amber"]},
    },
    "erroca_129": {  # Miu Miu MU52YS – oval gold metal rimless brown lenses
        "name": "Miu Miu MU52YS Oval Gold Rimless Sunglasses",
        "frame": {"shape": "oval", "color": ["gold"], "material": "metal", "rim_type": "rimless"},
        "lenses": {"color": ["brown"], "shape": "oval"},
        "style": {"aesthetic": ["luxury", "minimalist", "fashion-forward"]},
    },
    "erroca_130": {  # Ray-Ban RB3735 – aviator silver metal semi-rimless blue lenses
        "name": "Ray-Ban RB3735 Aviator Silver Sunglasses",
        "frame": {"shape": "aviator", "color": ["silver"], "rim_type": "semi-rimless"},
        "lenses": {"color": ["blue"], "shape": "teardrop"},
        "style": {"aesthetic": ["classic", "retro", "casual"]},
    },
    "erroca_131": {  # Ray-Ban RB3671CH – aviator black metal semi-rimless mirrored lenses
        "name": "Ray-Ban RB3671CH Aviator Black Mirrored Sunglasses",
        "frame": {"shape": "aviator", "color": ["black"]},
        "lenses": {"color": ["mirrored-silver"], "shape": "teardrop"},
    },
    "erroca_132": {  # Ray-Ban RB2232 – rectangular tortoiseshell acetate full-rim gray lenses
        "name": "Ray-Ban RB2232 Rectangular Tortoiseshell Sunglasses",
        "frame": {"shape": "rectangular", "color": ["tortoiseshell"]},
        "lenses": {"shape": "rectangular"},
        "style": {"aesthetic": ["classic", "vintage", "casual"]},
    },
    "erroca_133": {  # Ray-Ban RB3025 Aviator – gold metal rimless brown lenses
        "frame": {"color": ["gold"], "rim_type": "rimless"},
        "lenses": {"color": ["brown"]},
    },
    "erroca_134": {  # Ray-Ban RB2222 – rectangular tortoiseshell acetate full-rim blue lenses
        "name": "Ray-Ban RB2222 Rectangular Tortoiseshell Blue Sunglasses",
        "frame": {"shape": "rectangular", "color": ["tortoiseshell"]},
        "lenses": {"color": ["blue"], "shape": "rectangular"},
    },
    "erroca_135": {  # Ray-Ban RB2221 – oval tortoiseshell acetate full-rim green lenses
        "name": "Ray-Ban RB2221 Oval Tortoiseshell Sunglasses",
        "frame": {"shape": "oval", "color": ["tortoiseshell"]},
        "lenses": {"color": ["green"], "shape": "oval"},
        "style": {"aesthetic": ["vintage", "bohemian", "retro"]},
    },
    "erroca_136": {  # Ray-Ban RB0840S – wrap black acetate semi-rimless blue lenses
        "name": "Ray-Ban RB0840S Wrap Black Sunglasses",
        "frame": {"shape": "wrap", "color": ["black"], "rim_type": "semi-rimless"},
        "lenses": {"color": ["blue"], "shape": "curved-wrap"},
        "style": {"aesthetic": ["sporty", "modern", "casual"]},
    },
    "erroca_137": {  # Prada PR65ZS – aviator gold metal rimless green lenses
        "name": "Prada PR65ZS Aviator Gold Sunglasses",
        "frame": {"shape": "aviator", "color": ["gold"], "material": "metal", "rim_type": "rimless"},
        "lenses": {"color": ["green"], "shape": "teardrop"},
        "style": {"aesthetic": ["luxury", "classic", "minimalist"]},
    },
    "erroca_138": {  # Prada PR17 – rectangular white acetate full-rim gray lenses
        "name": "Prada PR17 Rectangular White Sunglasses",
        "frame": {"shape": "rectangular", "color": ["white"]},
        "lenses": {"shape": "rectangular"},
        "style": {"aesthetic": ["luxury", "minimalist", "modern"]},
    },
    "erroca_139": {  # Miu Miu MU11ZS – rectangular black acetate full-rim YELLOW lenses
        "lenses": {"color": ["yellow"]},
    },
    "erroca_140": {  # Jimmy Choo JC4004HB – butterfly gold mixed semi-rimless brown gradient
        "name": "Jimmy Choo JC4004HB Butterfly Gold Sunglasses",
        "frame": {"shape": "butterfly", "rim_type": "semi-rimless"},
        "lenses": {"shape": "oval"},
    },
    "erroca_141": {  # Jimmy Choo JM4004 – butterfly gold mixed semi-rimless brown gradient
        "name": "Jimmy Choo JM4004 Butterfly Gold Sunglasses",
        "frame": {"shape": "butterfly", "color": ["gold"], "material": "mixed-metal-acetate", "rim_type": "semi-rimless"},
        "lenses": {"color": ["gradient-brown"], "shape": "oval"},
        "style": {"aesthetic": ["luxury", "fashion-forward", "bold"]},
    },
    "erroca_142": {  # Jimmy Choo JM4005 v1 – geometric gold metal semi-rimless thin gray lenses
        "name": "Jimmy Choo JM4005 Geometric Gold Sunglasses",
        "frame": {"shape": "geometric", "color": ["gold"], "material": "metal", "rim_type": "semi-rimless", "thickness": "thin"},
        "lenses": {"color": ["gray"], "shape": "rectangular", "size": "medium"},
        "style": {"aesthetic": ["luxury", "minimalist", "fashion-forward"]},
    },
    "erroca_143": {  # Jimmy Choo JM4005 v2 – geometric gold metal semi-rimless thin brown lenses
        "name": "Jimmy Choo JM4005 Geometric Gold Brown Sunglasses",
        "frame": {"shape": "geometric", "color": ["gold"], "material": "metal", "rim_type": "semi-rimless", "thickness": "thin"},
        "lenses": {"color": ["brown"], "shape": "rectangular", "size": "medium"},
        "style": {"aesthetic": ["luxury", "minimalist", "fashion-forward"]},
    },
    "erroca_144": {  # Jimmy Choo JM4002 – round gold metal semi-rimless gray lenses
        "name": "Jimmy Choo JM4002 Round Gold Sunglasses",
        "frame": {"shape": "round", "color": ["gold"], "material": "metal", "rim_type": "semi-rimless"},
        "lenses": {"color": ["gray"], "shape": "round"},
        "style": {"aesthetic": ["luxury", "vintage", "fashion-forward"]},
    },
    "erroca_145": {  # Ray-Ban RB2203 Brown – square tortoiseshell acetate full-rim gradient-brown lenses
        "name": "Ray-Ban RB2203 Square Tortoiseshell Sunglasses",
        "frame": {"shape": "square", "color": ["tortoiseshell"]},
        "lenses": {"color": ["gradient-brown"], "shape": "square"},
        "style": {"aesthetic": ["classic", "vintage", "retro"]},
    },
    "erroca_146": {  # Ray-Ban RB2203 Black – square black acetate full-rim gradient-gray lenses
        "name": "Ray-Ban RB2203 Square Black Sunglasses",
        "frame": {"shape": "square"},
        "lenses": {"color": ["gradient-gray"], "shape": "square"},
    },
    "erroca_147": {  # Ray-Ban RB4458 – square black acetate full-rim gradient-brown lenses
        "name": "Ray-Ban RB4458 Square Black Sunglasses",
        "frame": {"shape": "square"},
        "lenses": {"color": ["gradient-brown"], "shape": "square"},
    },
    "erroca_148": {  # Saint Laurent SL801S – rectangular black acetate full-rim gray lenses
        "name": "Saint Laurent SL801S Rectangular Black Sunglasses",
        "frame": {"shape": "rectangular"},
        "lenses": {"shape": "rectangular"},
    },
}


def apply_corrections(catalog: dict) -> int:
    products = {p["id"]: p for p in catalog["products"]}
    count = 0
    for pid, fixes in CORRECTIONS.items():
        if pid not in products:
            print(f"  SKIP (not found): {pid}")
            continue
        p = products[pid]
        changed = []
        if "name" in fixes:
            p["name"] = fixes["name"]
            changed.append("name")
        for section in ("frame", "lenses"):
            if section in fixes:
                for field, val in fixes[section].items():
                    old = p["tags"][section].get(field)
                    if old != val:
                        p["tags"][section][field] = val
                        changed.append(f"{section}.{field}")
        if "style" in fixes:
            for field, val in fixes["style"].items():
                old = p["tags"]["style"].get(field)
                if old != val:
                    p["tags"]["style"][field] = val
                    changed.append(f"style.{field}")
        if changed:
            print(f"  [{pid}] {', '.join(changed)}")
            count += 1
    return count


def main():
    with open(CATALOG_JSON, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    print(f"Applying corrections to {len(CORRECTIONS)} products...")
    changed = apply_corrections(catalog)

    with open(CATALOG_JSON, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)

    print(f"\nDone — {changed} products updated.")


if __name__ == "__main__":
    main()
