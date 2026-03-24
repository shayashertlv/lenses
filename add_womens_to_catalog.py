"""Add 37 women's eyeglasses + 37 women's sunglasses to catalog.json."""
import json
from pathlib import Path
from datetime import datetime

CATALOG_JSON = Path(r"C:\Users\Shay\PycharmProjects\lenses\lenses\catalog\catalog.json")

NEW_PRODUCTS = [
    # ── Women's Eyeglasses (075–111) ─────────────────────────────────────────
    {
        "id": "erroca_075",
        "name": "Vogue Eyewear VO5603 Cat-Eye",
        "image": "images/erroca_womens_eyeglasses_001.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["transparent"], "thickness": "thin", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["fashion-forward", "casual", "modern"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "square"], "occasion": ["everyday", "fashion"]},
            "product": {"brand": "Vogue Eyewear", "model_name": "VO5603", "price": 399.0, "currency": "ILS", "in_stock": True, "sku": "VO5603-W44"},
        },
    },
    {
        "id": "erroca_076",
        "name": "Vogue Eyewear VO5518 Rectangular",
        "image": "images/erroca_womens_eyeglasses_002.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "acetate", "color": ["tortoiseshell"], "thickness": "thin", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["classic", "modern", "professional"], "gender_target": "women", "face_shape_fit": ["oval", "round", "heart"], "occasion": ["everyday", "office"]},
            "product": {"brand": "Vogue Eyewear", "model_name": "VO5518", "price": 499.0, "currency": "ILS", "in_stock": True, "sku": "VO5518-2942"},
        },
    },
    {
        "id": "erroca_077",
        "name": "Vogue Eyewear VO5563 Cat-Eye Pink",
        "image": "images/erroca_womens_eyeglasses_003.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["pink", "transparent"], "thickness": "thin", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["fashion-forward", "bohemian", "casual"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["everyday", "fashion"]},
            "product": {"brand": "Vogue Eyewear", "model_name": "VO5563", "price": 499.0, "currency": "ILS", "in_stock": True, "sku": "VO5563-W44"},
        },
    },
    {
        "id": "erroca_078",
        "name": "Vogue Eyewear VO4333 Round Gold",
        "image": "images/erroca_womens_eyeglasses_004.webp",
        "tags": {
            "frame": {"shape": "round", "material": "metal", "color": ["gold"], "thickness": "thin", "finish": "brushed", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "round", "size": "small"},
            "style": {"aesthetic": ["vintage", "bohemian", "retro"], "gender_target": "women", "face_shape_fit": ["oval", "square", "oblong"], "occasion": ["everyday", "fashion"]},
            "product": {"brand": "Vogue Eyewear", "model_name": "VO4333", "price": 499.0, "currency": "ILS", "in_stock": True, "sku": "VO4333-848"},
        },
    },
    {
        "id": "erroca_079",
        "name": "Vogue Eyewear VO5239 Cat-Eye Multicolor",
        "image": "images/erroca_womens_eyeglasses_005.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["multicolor"], "thickness": "thin", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["fashion-forward", "bold", "artsy"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "square"], "occasion": ["everyday", "fashion"]},
            "product": {"brand": "Vogue Eyewear", "model_name": "VO5239", "price": 599.0, "currency": "ILS", "in_stock": True, "sku": "VO5239-2735"},
        },
    },
    {
        "id": "erroca_080",
        "name": "Prada PR A57V Rectangular Luxury",
        "image": "images/erroca_womens_eyeglasses_006.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "acetate", "color": ["black", "tortoiseshell"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["luxury", "professional", "modern"], "gender_target": "women", "face_shape_fit": ["oval", "round", "heart"], "occasion": ["office", "formal", "everyday"]},
            "product": {"brand": "Prada", "model_name": "PR A57V", "price": 1499.0, "currency": "ILS", "in_stock": True, "sku": "PRA57V"},
        },
    },
    {
        "id": "erroca_081",
        "name": "Vogue Eyewear VO5632B Butterfly",
        "image": "images/erroca_womens_eyeglasses_007.webp",
        "tags": {
            "frame": {"shape": "butterfly", "material": "acetate", "color": ["tortoiseshell"], "thickness": "thin", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["fashion-forward", "vintage", "bold"], "gender_target": "women", "face_shape_fit": ["oval", "square", "oblong"], "occasion": ["everyday", "fashion"]},
            "product": {"brand": "Vogue Eyewear", "model_name": "VO5632B", "price": 599.0, "currency": "ILS", "in_stock": True, "sku": "VO5632B-2990"},
        },
    },
    {
        "id": "erroca_082",
        "name": "Vogue Eyewear VO4333 Round Rose Gold",
        "image": "images/erroca_womens_eyeglasses_008.webp",
        "tags": {
            "frame": {"shape": "round", "material": "metal", "color": ["rose-gold"], "thickness": "thin", "finish": "brushed", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "round", "size": "small"},
            "style": {"aesthetic": ["vintage", "bohemian", "casual"], "gender_target": "women", "face_shape_fit": ["oval", "square", "oblong"], "occasion": ["everyday", "fashion"]},
            "product": {"brand": "Vogue Eyewear", "model_name": "VO4333", "price": 499.0, "currency": "ILS", "in_stock": True, "sku": "VO4333-5138"},
        },
    },
    {
        "id": "erroca_083",
        "name": "Prada PR A16V Cat-Eye Black",
        "image": "images/erroca_womens_eyeglasses_009.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "fashion-forward", "bold"], "gender_target": "women", "face_shape_fit": ["oval", "round", "oblong"], "occasion": ["fashion", "formal", "everyday"]},
            "product": {"brand": "Prada", "model_name": "PR A16V", "price": 1499.0, "currency": "ILS", "in_stock": True, "sku": "PRA16V"},
        },
    },
    {
        "id": "erroca_084",
        "name": "Vogue Eyewear VO4334 Round Gold",
        "image": "images/erroca_womens_eyeglasses_010.webp",
        "tags": {
            "frame": {"shape": "round", "material": "metal", "color": ["gold"], "thickness": "thin", "finish": "brushed", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "round", "size": "small"},
            "style": {"aesthetic": ["vintage", "minimalist", "classic"], "gender_target": "women", "face_shape_fit": ["oval", "square", "oblong"], "occasion": ["everyday", "office"]},
            "product": {"brand": "Vogue Eyewear", "model_name": "VO4334", "price": 499.0, "currency": "ILS", "in_stock": True, "sku": "VO4334-5210"},
        },
    },
    {
        "id": "erroca_085",
        "name": "Ray-Ban RX6513 Rectangular Metal",
        "image": "images/erroca_womens_eyeglasses_011.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "metal", "color": ["silver"], "thickness": "thin", "finish": "brushed", "rim_type": "semi-rimless"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["minimalist", "professional", "classic"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["office", "everyday"]},
            "product": {"brand": "Ray-Ban", "model_name": "RX6513", "price": 999.0, "currency": "ILS", "in_stock": True, "sku": "RX6513-2904"},
        },
    },
    {
        "id": "erroca_086",
        "name": "Prada PR 02ZV Rectangular Black",
        "image": "images/erroca_womens_eyeglasses_012.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["luxury", "professional", "modern"], "gender_target": "women", "face_shape_fit": ["oval", "round", "heart"], "occasion": ["office", "formal", "everyday"]},
            "product": {"brand": "Prada", "model_name": "PR 02ZV", "price": 1099.0, "currency": "ILS", "in_stock": True, "sku": "PR02ZV"},
        },
    },
    {
        "id": "erroca_087",
        "name": "Prada PR 11YV Cat-Eye Tortoiseshell",
        "image": "images/erroca_womens_eyeglasses_013.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["tortoiseshell"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "fashion-forward", "classic"], "gender_target": "women", "face_shape_fit": ["oval", "round", "oblong"], "occasion": ["fashion", "everyday", "office"]},
            "product": {"brand": "Prada", "model_name": "PR 11YV", "price": 1199.0, "currency": "ILS", "in_stock": True, "sku": "PR11YV-v1"},
        },
    },
    {
        "id": "erroca_088",
        "name": "Prada PR 15ZV Cat-Eye Black",
        "image": "images/erroca_womens_eyeglasses_014.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "bold", "modern"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "round"], "occasion": ["fashion", "formal", "everyday"]},
            "product": {"brand": "Prada", "model_name": "PR 15ZV", "price": 1199.0, "currency": "ILS", "in_stock": True, "sku": "PR15ZV-v1"},
        },
    },
    {
        "id": "erroca_089",
        "name": "Prada PR 11YV Cat-Eye Transparent",
        "image": "images/erroca_womens_eyeglasses_015.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["transparent"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "minimalist", "modern"], "gender_target": "women", "face_shape_fit": ["oval", "square", "oblong"], "occasion": ["everyday", "fashion"]},
            "product": {"brand": "Prada", "model_name": "PR 11YV", "price": 1199.0, "currency": "ILS", "in_stock": True, "sku": "PR11YV-v2"},
        },
    },
    {
        "id": "erroca_090",
        "name": "Prada PR 15ZV Cat-Eye Silver",
        "image": "images/erroca_womens_eyeglasses_016.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "mixed-metal-acetate", "color": ["silver", "transparent"], "thickness": "medium", "finish": "matte", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "modern", "fashion-forward"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "round"], "occasion": ["fashion", "everyday"]},
            "product": {"brand": "Prada", "model_name": "PR 15ZV", "price": 1199.0, "currency": "ILS", "in_stock": True, "sku": "PR15ZV-v2"},
        },
    },
    {
        "id": "erroca_091",
        "name": "Versace VE3367U Cat-Eye Gold",
        "image": "images/erroca_womens_eyeglasses_017.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "metal", "color": ["gold"], "thickness": "thin", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "fashion-forward", "bold"], "gender_target": "women", "face_shape_fit": ["oval", "round", "oblong"], "occasion": ["fashion", "formal", "everyday"]},
            "product": {"brand": "Versace", "model_name": "VE3367U", "price": 1199.0, "currency": "ILS", "in_stock": True, "sku": "VE3367U-GB1"},
        },
    },
    {
        "id": "erroca_092",
        "name": "Versace VE1247 Oval Gold",
        "image": "images/erroca_womens_eyeglasses_018.webp",
        "tags": {
            "frame": {"shape": "oval", "material": "metal", "color": ["gold"], "thickness": "thin", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "classic", "professional"], "gender_target": "women", "face_shape_fit": ["oval", "square", "heart"], "occasion": ["office", "formal", "everyday"]},
            "product": {"brand": "Versace", "model_name": "VE1247", "price": 1199.0, "currency": "ILS", "in_stock": True, "sku": "VE1247-1252"},
        },
    },
    {
        "id": "erroca_093",
        "name": "Versace VE1298 Rectangular Gold",
        "image": "images/erroca_womens_eyeglasses_019.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "metal", "color": ["gold", "black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["luxury", "bold", "modern"], "gender_target": "women", "face_shape_fit": ["oval", "round", "heart"], "occasion": ["office", "formal", "everyday"]},
            "product": {"brand": "Versace", "model_name": "VE1298", "price": 1199.0, "currency": "ILS", "in_stock": True, "sku": "VE1298-1252"},
        },
    },
    {
        "id": "erroca_094",
        "name": "Dolce & Gabbana DG1346 Cat-Eye Black Gold",
        "image": "images/erroca_womens_eyeglasses_020.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["black", "gold"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "bold", "fashion-forward"], "gender_target": "women", "face_shape_fit": ["oval", "round", "heart"], "occasion": ["fashion", "formal", "everyday"]},
            "product": {"brand": "Dolce & Gabbana", "model_name": "DG1346", "price": 1199.0, "currency": "ILS", "in_stock": True, "sku": "DG1346-1311"},
        },
    },
    {
        "id": "erroca_095",
        "name": "Miu Miu MU 04WV Cat-Eye Tortoiseshell",
        "image": "images/erroca_womens_eyeglasses_021.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["tortoiseshell"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "fashion-forward", "vintage"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["fashion", "everyday"]},
            "product": {"brand": "Miu Miu", "model_name": "MU 04WV", "price": 1799.0, "currency": "ILS", "in_stock": True, "sku": "MU04WV-brown"},
        },
    },
    {
        "id": "erroca_096",
        "name": "Versace VE3353 Rectangular Tortoiseshell",
        "image": "images/erroca_womens_eyeglasses_022.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "acetate", "color": ["tortoiseshell"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["luxury", "classic", "professional"], "gender_target": "women", "face_shape_fit": ["oval", "round", "heart"], "occasion": ["office", "everyday"]},
            "product": {"brand": "Versace", "model_name": "VE3353", "price": 1299.0, "currency": "ILS", "in_stock": True, "sku": "VE3353-108"},
        },
    },
    {
        "id": "erroca_097",
        "name": "Versace VE3294 Cat-Eye Gold",
        "image": "images/erroca_womens_eyeglasses_023.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "metal", "color": ["gold"], "thickness": "thin", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "fashion-forward", "modern"], "gender_target": "women", "face_shape_fit": ["oval", "round", "oblong"], "occasion": ["fashion", "everyday"]},
            "product": {"brand": "Versace", "model_name": "VE3294", "price": 999.0, "currency": "ILS", "in_stock": True, "sku": "VE3294"},
        },
    },
    {
        "id": "erroca_098",
        "name": "Versace VE3347 Cat-Eye Tortoiseshell",
        "image": "images/erroca_womens_eyeglasses_024.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["tortoiseshell"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "classic", "bold"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["fashion", "everyday", "office"]},
            "product": {"brand": "Versace", "model_name": "VE3347", "price": 1299.0, "currency": "ILS", "in_stock": True, "sku": "VE3347-5435"},
        },
    },
    {
        "id": "erroca_099",
        "name": "Dolce & Gabbana DG3360 Oval Tortoiseshell",
        "image": "images/erroca_womens_eyeglasses_025.webp",
        "tags": {
            "frame": {"shape": "oval", "material": "acetate", "color": ["tortoiseshell"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "classic", "vintage"], "gender_target": "women", "face_shape_fit": ["oval", "square", "oblong"], "occasion": ["everyday", "office"]},
            "product": {"brand": "Dolce & Gabbana", "model_name": "DG3360", "price": 1399.0, "currency": "ILS", "in_stock": True, "sku": "DG3360-3246"},
        },
    },
    {
        "id": "erroca_100",
        "name": "Ray-Ban RX6335 Rectangular Metal Silver",
        "image": "images/erroca_womens_eyeglasses_026.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "metal", "color": ["silver"], "thickness": "thin", "finish": "brushed", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["minimalist", "professional", "classic"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["office", "everyday"]},
            "product": {"brand": "Ray-Ban", "model_name": "RX6335", "price": 799.0, "currency": "ILS", "in_stock": True, "sku": "RX6335"},
        },
    },
    {
        "id": "erroca_101",
        "name": "Ray-Ban RX8416 Titanium Rectangular",
        "image": "images/erroca_womens_eyeglasses_027.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "titanium", "color": ["silver"], "thickness": "thin", "finish": "brushed", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["minimalist", "professional", "modern"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "round"], "occasion": ["office", "everyday"]},
            "product": {"brand": "Ray-Ban", "model_name": "RX8416", "price": 999.0, "currency": "ILS", "in_stock": True, "sku": "RX8416"},
        },
    },
    {
        "id": "erroca_102",
        "name": "Ray-Ban RX7047 Rectangular Black",
        "image": "images/erroca_womens_eyeglasses_028.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "plastic", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["classic", "casual", "professional"], "gender_target": "women", "face_shape_fit": ["oval", "round", "heart"], "occasion": ["everyday", "office"]},
            "product": {"brand": "Ray-Ban", "model_name": "RX7047", "price": 799.0, "currency": "ILS", "in_stock": True, "sku": "RX7047"},
        },
    },
    {
        "id": "erroca_103",
        "name": "Ray-Ban RX5441 Rectangular Acetate Black",
        "image": "images/erroca_womens_eyeglasses_029.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["classic", "casual", "modern"], "gender_target": "women", "face_shape_fit": ["oval", "round", "heart"], "occasion": ["everyday", "casual"]},
            "product": {"brand": "Ray-Ban", "model_name": "RX5441", "price": 699.0, "currency": "ILS", "in_stock": True, "sku": "RX5441-2000"},
        },
    },
    {
        "id": "erroca_104",
        "name": "Miu Miu MU 53WV Square Acetate Black",
        "image": "images/erroca_womens_eyeglasses_030.webp",
        "tags": {
            "frame": {"shape": "square", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "square", "size": "medium"},
            "style": {"aesthetic": ["luxury", "bold", "modern"], "gender_target": "women", "face_shape_fit": ["oval", "oblong", "heart"], "occasion": ["fashion", "everyday"]},
            "product": {"brand": "Miu Miu", "model_name": "MU 53WV", "price": 1999.0, "currency": "ILS", "in_stock": True, "sku": "MU53WV"},
        },
    },
    {
        "id": "erroca_105",
        "name": "Miu Miu MU 03WV Cat-Eye Tortoiseshell",
        "image": "images/erroca_womens_eyeglasses_031.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["tortoiseshell"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "vintage", "fashion-forward"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["fashion", "everyday"]},
            "product": {"brand": "Miu Miu", "model_name": "MU 03WV", "price": 1799.0, "currency": "ILS", "in_stock": True, "sku": "MU03WV"},
        },
    },
    {
        "id": "erroca_106",
        "name": "Miu Miu MU 07XV Rectangular Havana",
        "image": "images/erroca_womens_eyeglasses_032.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "acetate", "color": ["havana-brown"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["luxury", "classic", "professional"], "gender_target": "women", "face_shape_fit": ["oval", "round", "heart"], "occasion": ["office", "everyday"]},
            "product": {"brand": "Miu Miu", "model_name": "MU 07XV", "price": 1799.0, "currency": "ILS", "in_stock": True, "sku": "MU07XV-brown"},
        },
    },
    {
        "id": "erroca_107",
        "name": "Miu Miu MU 07XV Rectangular Black",
        "image": "images/erroca_womens_eyeglasses_033.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["luxury", "minimalist", "modern"], "gender_target": "women", "face_shape_fit": ["oval", "round", "heart"], "occasion": ["office", "everyday"]},
            "product": {"brand": "Miu Miu", "model_name": "MU 07XV", "price": 1799.0, "currency": "ILS", "in_stock": True, "sku": "MU07XV-black"},
        },
    },
    {
        "id": "erroca_108",
        "name": "Miu Miu MU 04UV Oval Transparent",
        "image": "images/erroca_womens_eyeglasses_034.webp",
        "tags": {
            "frame": {"shape": "oval", "material": "acetate", "color": ["transparent"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "minimalist", "modern"], "gender_target": "women", "face_shape_fit": ["oval", "square", "oblong"], "occasion": ["everyday", "fashion"]},
            "product": {"brand": "Miu Miu", "model_name": "MU 04UV", "price": 1799.0, "currency": "ILS", "in_stock": True, "sku": "MU04UV"},
        },
    },
    {
        "id": "erroca_109",
        "name": "Miu Miu MU 02WV Cat-Eye Black",
        "image": "images/erroca_womens_eyeglasses_035.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "bold", "fashion-forward"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "round"], "occasion": ["fashion", "formal", "everyday"]},
            "product": {"brand": "Miu Miu", "model_name": "MU 02WV", "price": 1799.0, "currency": "ILS", "in_stock": True, "sku": "MU02WV"},
        },
    },
    {
        "id": "erroca_110",
        "name": "Miu Miu MU 09XV Rectangular Silver",
        "image": "images/erroca_womens_eyeglasses_036.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "mixed-metal-acetate", "color": ["silver", "transparent"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["luxury", "modern", "minimalist"], "gender_target": "women", "face_shape_fit": ["oval", "round", "heart"], "occasion": ["office", "fashion"]},
            "product": {"brand": "Miu Miu", "model_name": "MU 09XV", "price": 1799.0, "currency": "ILS", "in_stock": True, "sku": "MU09XV"},
        },
    },
    {
        "id": "erroca_111",
        "name": "Miu Miu MU 04WV Cat-Eye Black",
        "image": "images/erroca_womens_eyeglasses_037.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["clear", "prescription-ready"], "color": ["clear"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "bold", "fashion-forward"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["fashion", "everyday"]},
            "product": {"brand": "Miu Miu", "model_name": "MU 04WV", "price": 1799.0, "currency": "ILS", "in_stock": True, "sku": "MU04WV-black"},
        },
    },
    # ── Women's Sunglasses (112–148) ─────────────────────────────────────────
    {
        "id": "erroca_112",
        "name": "Prada PRS C05S Cat-Eye Sunglasses Black",
        "image": "images/erroca_womens_sunglasses_001.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses", "polarized"], "color": ["brown"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "fashion-forward", "bold"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["outdoor", "fashion", "beach"]},
            "product": {"brand": "Prada", "model_name": "PRS C05S", "price": 1469.0, "currency": "ILS", "in_stock": True, "sku": "PRSC05S-v1"},
        },
    },
    {
        "id": "erroca_113",
        "name": "Miu Miu MUA56S Cat-Eye Gold Sunglasses",
        "image": "images/erroca_womens_sunglasses_002.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "metal", "color": ["gold"], "thickness": "thin", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses", "gradient"], "color": ["brown"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "fashion-forward", "vintage"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "round"], "occasion": ["outdoor", "fashion", "beach"]},
            "product": {"brand": "Miu Miu", "model_name": "MUA56S", "price": 1469.0, "currency": "ILS", "in_stock": True, "sku": "MUA56S-v1"},
        },
    },
    {
        "id": "erroca_114",
        "name": "Miu Miu MUA56S Cat-Eye Gold Brown Sunglasses",
        "image": "images/erroca_womens_sunglasses_003.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "metal", "color": ["gold", "tortoiseshell"], "thickness": "thin", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses", "gradient"], "color": ["gradient-brown"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "fashion-forward", "bohemian"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["outdoor", "fashion", "beach"]},
            "product": {"brand": "Miu Miu", "model_name": "MUA56S", "price": 1469.0, "currency": "ILS", "in_stock": True, "sku": "MUA56S-v2"},
        },
    },
    {
        "id": "erroca_115",
        "name": "Miu Miu MUA56S Cat-Eye Gold Rose Sunglasses",
        "image": "images/erroca_womens_sunglasses_004.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "metal", "color": ["rose-gold"], "thickness": "thin", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses", "gradient"], "color": ["rose"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "fashion-forward", "preppy"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "round"], "occasion": ["outdoor", "fashion", "beach"]},
            "product": {"brand": "Miu Miu", "model_name": "MUA56S", "price": 1469.0, "currency": "ILS", "in_stock": True, "sku": "MUA56S-v3"},
        },
    },
    {
        "id": "erroca_116",
        "name": "Versace VE2274 Square Gold Sunglasses",
        "image": "images/erroca_womens_sunglasses_005.webp",
        "tags": {
            "frame": {"shape": "square", "material": "metal", "color": ["gold"], "thickness": "thin", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses", "gradient"], "color": ["gradient-gray"], "shape": "square", "size": "large"},
            "style": {"aesthetic": ["luxury", "bold", "modern"], "gender_target": "women", "face_shape_fit": ["oval", "oblong", "heart"], "occasion": ["outdoor", "fashion", "beach"]},
            "product": {"brand": "Versace", "model_name": "VE2274", "price": 1399.0, "currency": "ILS", "in_stock": True, "sku": "VE2274-gold"},
        },
    },
    {
        "id": "erroca_117",
        "name": "Versace VE4501 Rectangular Havana Sunglasses",
        "image": "images/erroca_womens_sunglasses_006.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "acetate", "color": ["havana-brown"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses", "polarized"], "color": ["brown"], "shape": "rectangular", "size": "large"},
            "style": {"aesthetic": ["luxury", "classic", "casual"], "gender_target": "women", "face_shape_fit": ["oval", "round", "heart"], "occasion": ["outdoor", "driving", "everyday"]},
            "product": {"brand": "Versace", "model_name": "VE4501", "price": 909.0, "currency": "ILS", "in_stock": True, "sku": "VE4501"},
        },
    },
    {
        "id": "erroca_118",
        "name": "Versace VE4504D Rectangular Black Sunglasses",
        "image": "images/erroca_womens_sunglasses_007.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["gray"], "shape": "rectangular", "size": "large"},
            "style": {"aesthetic": ["luxury", "modern", "casual"], "gender_target": "women", "face_shape_fit": ["oval", "round", "heart"], "occasion": ["outdoor", "everyday", "driving"]},
            "product": {"brand": "Versace", "model_name": "VE4504D", "price": 769.0, "currency": "ILS", "in_stock": True, "sku": "VE4504D"},
        },
    },
    {
        "id": "erroca_119",
        "name": "Versace VE4497U Square Black Sunglasses",
        "image": "images/erroca_womens_sunglasses_008.webp",
        "tags": {
            "frame": {"shape": "square", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["gray"], "shape": "square", "size": "large"},
            "style": {"aesthetic": ["luxury", "bold", "streetwear"], "gender_target": "women", "face_shape_fit": ["oval", "oblong", "heart"], "occasion": ["outdoor", "everyday", "casual"]},
            "product": {"brand": "Versace", "model_name": "VE4497U", "price": 909.0, "currency": "ILS", "in_stock": True, "sku": "VE4497U"},
        },
    },
    {
        "id": "erroca_120",
        "name": "Versace VE2274 Square Silver Sunglasses",
        "image": "images/erroca_womens_sunglasses_009.webp",
        "tags": {
            "frame": {"shape": "square", "material": "metal", "color": ["silver"], "thickness": "thin", "finish": "brushed", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses", "mirrored"], "color": ["mirrored-silver"], "shape": "square", "size": "large"},
            "style": {"aesthetic": ["luxury", "modern", "bold"], "gender_target": "women", "face_shape_fit": ["oval", "oblong", "heart"], "occasion": ["outdoor", "fashion", "beach"]},
            "product": {"brand": "Versace", "model_name": "VE2274", "price": 1399.0, "currency": "ILS", "in_stock": True, "sku": "VE2274-silver"},
        },
    },
    {
        "id": "erroca_121",
        "name": "Ray-Ban RB3767 Rectangular Metal Sunglasses",
        "image": "images/erroca_womens_sunglasses_010.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "metal", "color": ["silver"], "thickness": "thin", "finish": "brushed", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["gray"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["classic", "minimalist", "casual"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["outdoor", "everyday", "driving"]},
            "product": {"brand": "Ray-Ban", "model_name": "RB3767", "price": 899.0, "currency": "ILS", "in_stock": True, "sku": "RB3767"},
        },
    },
    {
        "id": "erroca_122",
        "name": "Prada PRS C05S Cat-Eye Tortoiseshell Sunglasses",
        "image": "images/erroca_womens_sunglasses_011.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["tortoiseshell"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses", "polarized"], "color": ["brown"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "vintage", "fashion-forward"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "round"], "occasion": ["outdoor", "fashion", "beach"]},
            "product": {"brand": "Prada", "model_name": "PRS C05S", "price": 1469.0, "currency": "ILS", "in_stock": True, "sku": "PRSC05S-v2"},
        },
    },
    {
        "id": "erroca_123",
        "name": "Prada PRS C04S Rectangular Sunglasses",
        "image": "images/erroca_womens_sunglasses_012.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["gray"], "shape": "rectangular", "size": "large"},
            "style": {"aesthetic": ["luxury", "modern", "minimalist"], "gender_target": "women", "face_shape_fit": ["oval", "round", "heart"], "occasion": ["outdoor", "driving", "everyday"]},
            "product": {"brand": "Prada", "model_name": "PRS C04S", "price": 1469.0, "currency": "ILS", "in_stock": True, "sku": "PRSC04S"},
        },
    },
    {
        "id": "erroca_124",
        "name": "Ray-Ban RB4454 Round Tortoiseshell Sunglasses",
        "image": "images/erroca_womens_sunglasses_013.webp",
        "tags": {
            "frame": {"shape": "round", "material": "acetate", "color": ["tortoiseshell"], "thickness": "thin", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["brown"], "shape": "round", "size": "medium"},
            "style": {"aesthetic": ["vintage", "bohemian", "retro"], "gender_target": "women", "face_shape_fit": ["oval", "square", "oblong"], "occasion": ["outdoor", "casual", "fashion"]},
            "product": {"brand": "Ray-Ban", "model_name": "RB4454", "price": 839.0, "currency": "ILS", "in_stock": True, "sku": "RB4454"},
        },
    },
    {
        "id": "erroca_125",
        "name": "Oakley OO9517 Sport Wrap Sunglasses Black",
        "image": "images/erroca_womens_sunglasses_014.webp",
        "tags": {
            "frame": {"shape": "wrap", "material": "TR90", "color": ["black"], "thickness": "thick", "finish": "matte", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses", "polarized"], "color": ["gray"], "shape": "curved-wrap", "size": "large"},
            "style": {"aesthetic": ["sporty", "modern", "casual"], "gender_target": "women", "face_shape_fit": ["oval", "oblong", "universal"], "occasion": ["sport", "outdoor", "driving"]},
            "product": {"brand": "Oakley", "model_name": "OO9517", "price": 909.0, "currency": "ILS", "in_stock": True, "sku": "OO9517"},
        },
    },
    {
        "id": "erroca_126",
        "name": "Oakley OO9463 Sport Wrap Sunglasses",
        "image": "images/erroca_womens_sunglasses_015.webp",
        "tags": {
            "frame": {"shape": "wrap", "material": "TR90", "color": ["black"], "thickness": "medium", "finish": "matte", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses", "polarized"], "color": ["gray"], "shape": "curved-wrap", "size": "medium"},
            "style": {"aesthetic": ["sporty", "casual", "modern"], "gender_target": "women", "face_shape_fit": ["oval", "oblong", "universal"], "occasion": ["sport", "outdoor", "driving"]},
            "product": {"brand": "Oakley", "model_name": "OO9463", "price": 839.0, "currency": "ILS", "in_stock": True, "sku": "OO9463"},
        },
    },
    {
        "id": "erroca_127",
        "name": "Dolce & Gabbana DG4519 Cat-Eye Black Sunglasses",
        "image": "images/erroca_womens_sunglasses_016.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["black"], "thickness": "thick", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses", "gradient"], "color": ["gradient-gray"], "shape": "oval", "size": "large"},
            "style": {"aesthetic": ["luxury", "bold", "fashion-forward"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["outdoor", "fashion", "beach"]},
            "product": {"brand": "Dolce & Gabbana", "model_name": "DG4519", "price": 1119.0, "currency": "ILS", "in_stock": True, "sku": "DG4519"},
        },
    },
    {
        "id": "erroca_128",
        "name": "Oakley OO9518 Sport Wrap Sunglasses",
        "image": "images/erroca_womens_sunglasses_017.webp",
        "tags": {
            "frame": {"shape": "wrap", "material": "TR90", "color": ["black"], "thickness": "thick", "finish": "matte", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses", "polarized"], "color": ["gray"], "shape": "curved-wrap", "size": "large"},
            "style": {"aesthetic": ["sporty", "modern", "casual"], "gender_target": "women", "face_shape_fit": ["oval", "oblong", "universal"], "occasion": ["sport", "outdoor", "driving"]},
            "product": {"brand": "Oakley", "model_name": "OO9518", "price": 909.0, "currency": "ILS", "in_stock": True, "sku": "OO9518"},
        },
    },
    {
        "id": "erroca_129",
        "name": "Miu Miu MU52YS Square Black Sunglasses",
        "image": "images/erroca_womens_sunglasses_018.webp",
        "tags": {
            "frame": {"shape": "square", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["gray"], "shape": "square", "size": "large"},
            "style": {"aesthetic": ["luxury", "bold", "modern"], "gender_target": "women", "face_shape_fit": ["oval", "oblong", "heart"], "occasion": ["outdoor", "fashion", "everyday"]},
            "product": {"brand": "Miu Miu", "model_name": "MU52YS", "price": 1329.0, "currency": "ILS", "in_stock": True, "sku": "MU52YS"},
        },
    },
    {
        "id": "erroca_130",
        "name": "Ray-Ban RB3735 Rectangular Gold Sunglasses",
        "image": "images/erroca_womens_sunglasses_019.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "metal", "color": ["gold"], "thickness": "thin", "finish": "brushed", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["brown"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["classic", "minimalist", "casual"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["outdoor", "everyday", "driving"]},
            "product": {"brand": "Ray-Ban", "model_name": "RB3735", "price": 799.0, "currency": "ILS", "in_stock": True, "sku": "RB3735"},
        },
    },
    {
        "id": "erroca_131",
        "name": "Ray-Ban RB3671CH Chromance Rectangular Sunglasses",
        "image": "images/erroca_womens_sunglasses_020.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "metal", "color": ["gold"], "thickness": "thin", "finish": "brushed", "rim_type": "semi-rimless"},
            "lenses": {"type": ["sunglasses", "polarized"], "color": ["G-15-green"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["classic", "minimalist", "professional"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["outdoor", "driving", "everyday"]},
            "product": {"brand": "Ray-Ban", "model_name": "RB3671CH", "price": 899.0, "currency": "ILS", "in_stock": True, "sku": "RB3671CH"},
        },
    },
    {
        "id": "erroca_132",
        "name": "Ray-Ban RB2232 Square Black Sunglasses",
        "image": "images/erroca_womens_sunglasses_021.webp",
        "tags": {
            "frame": {"shape": "square", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["gray"], "shape": "square", "size": "large"},
            "style": {"aesthetic": ["classic", "bold", "casual"], "gender_target": "women", "face_shape_fit": ["oval", "oblong", "heart"], "occasion": ["outdoor", "casual", "everyday"]},
            "product": {"brand": "Ray-Ban", "model_name": "RB2232", "price": 799.0, "currency": "ILS", "in_stock": True, "sku": "RB2232"},
        },
    },
    {
        "id": "erroca_133",
        "name": "Ray-Ban RB3025 Aviator Classic Sunglasses",
        "image": "images/erroca_womens_sunglasses_022.webp",
        "tags": {
            "frame": {"shape": "aviator", "material": "metal", "color": ["silver"], "thickness": "thin", "finish": "brushed", "rim_type": "semi-rimless"},
            "lenses": {"type": ["sunglasses", "polarized"], "color": ["G-15-green"], "shape": "teardrop", "size": "large"},
            "style": {"aesthetic": ["classic", "vintage", "casual"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["outdoor", "driving", "everyday"]},
            "product": {"brand": "Ray-Ban", "model_name": "RB3025", "price": 799.0, "currency": "ILS", "in_stock": True, "sku": "RB3025"},
        },
    },
    {
        "id": "erroca_134",
        "name": "Ray-Ban RB2222 Square Black Sunglasses",
        "image": "images/erroca_womens_sunglasses_023.webp",
        "tags": {
            "frame": {"shape": "square", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["gray"], "shape": "square", "size": "medium"},
            "style": {"aesthetic": ["classic", "modern", "casual"], "gender_target": "women", "face_shape_fit": ["oval", "oblong", "heart"], "occasion": ["outdoor", "casual", "everyday"]},
            "product": {"brand": "Ray-Ban", "model_name": "RB2222", "price": 799.0, "currency": "ILS", "in_stock": True, "sku": "RB2222"},
        },
    },
    {
        "id": "erroca_135",
        "name": "Ray-Ban RB2221 Wayfarer Black Sunglasses",
        "image": "images/erroca_womens_sunglasses_024.webp",
        "tags": {
            "frame": {"shape": "wayfarer", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["gray"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["classic", "retro", "casual"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["outdoor", "casual", "everyday"]},
            "product": {"brand": "Ray-Ban", "model_name": "RB2221", "price": 799.0, "currency": "ILS", "in_stock": True, "sku": "RB2221"},
        },
    },
    {
        "id": "erroca_136",
        "name": "Ray-Ban RB0840S Rectangular Tortoiseshell Sunglasses",
        "image": "images/erroca_womens_sunglasses_025.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "acetate", "color": ["tortoiseshell"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["brown"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["classic", "casual", "vintage"], "gender_target": "women", "face_shape_fit": ["oval", "round", "heart"], "occasion": ["outdoor", "everyday", "casual"]},
            "product": {"brand": "Ray-Ban", "model_name": "RB0840S", "price": 799.0, "currency": "ILS", "in_stock": True, "sku": "RB0840S"},
        },
    },
    {
        "id": "erroca_137",
        "name": "Prada PR65ZS Round Tortoiseshell Sunglasses",
        "image": "images/erroca_womens_sunglasses_026.webp",
        "tags": {
            "frame": {"shape": "round", "material": "acetate", "color": ["tortoiseshell"], "thickness": "thin", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["brown"], "shape": "round", "size": "medium"},
            "style": {"aesthetic": ["luxury", "vintage", "bohemian"], "gender_target": "women", "face_shape_fit": ["oval", "square", "oblong"], "occasion": ["outdoor", "fashion", "casual"]},
            "product": {"brand": "Prada", "model_name": "PR65ZS", "price": 1399.0, "currency": "ILS", "in_stock": True, "sku": "PR65ZS"},
        },
    },
    {
        "id": "erroca_138",
        "name": "Prada PR17 Cat-Eye Black Sunglasses",
        "image": "images/erroca_womens_sunglasses_027.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["gray"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "fashion-forward", "bold"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "round"], "occasion": ["outdoor", "fashion", "everyday"]},
            "product": {"brand": "Prada", "model_name": "PR17", "price": 1399.0, "currency": "ILS", "in_stock": True, "sku": "PR17"},
        },
    },
    {
        "id": "erroca_139",
        "name": "Miu Miu MU11ZS Rectangular Sunglasses",
        "image": "images/erroca_womens_sunglasses_028.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses", "gradient"], "color": ["gradient-gray"], "shape": "rectangular", "size": "large"},
            "style": {"aesthetic": ["luxury", "modern", "bold"], "gender_target": "women", "face_shape_fit": ["oval", "round", "heart"], "occasion": ["outdoor", "fashion", "everyday"]},
            "product": {"brand": "Miu Miu", "model_name": "MU11ZS", "price": 1399.0, "currency": "ILS", "in_stock": True, "sku": "MU11ZS"},
        },
    },
    {
        "id": "erroca_140",
        "name": "Jimmy Choo JC4004HB Cat-Eye Gold Sunglasses",
        "image": "images/erroca_womens_sunglasses_029.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "mixed-metal-acetate", "color": ["gold", "havana-brown"], "thickness": "thin", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses", "gradient"], "color": ["gradient-brown"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "fashion-forward", "glamorous"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["outdoor", "fashion", "beach"]},
            "product": {"brand": "Jimmy Choo", "model_name": "JC4004HB", "price": 1299.0, "currency": "ILS", "in_stock": True, "sku": "JC4004HB"},
        },
    },
    {
        "id": "erroca_141",
        "name": "Jimmy Choo JM4004 Cat-Eye Black Sunglasses",
        "image": "images/erroca_womens_sunglasses_030.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["gray"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "bold", "fashion-forward"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "round"], "occasion": ["outdoor", "fashion", "everyday"]},
            "product": {"brand": "Jimmy Choo", "model_name": "JM4004", "price": 1299.0, "currency": "ILS", "in_stock": True, "sku": "JM4004"},
        },
    },
    {
        "id": "erroca_142",
        "name": "Jimmy Choo JM4005 Oversized Tortoiseshell Sunglasses",
        "image": "images/erroca_womens_sunglasses_031.webp",
        "tags": {
            "frame": {"shape": "oversized-square", "material": "acetate", "color": ["tortoiseshell"], "thickness": "thick", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses", "gradient"], "color": ["gradient-brown"], "shape": "square", "size": "oversized"},
            "style": {"aesthetic": ["luxury", "bold", "fashion-forward"], "gender_target": "women", "face_shape_fit": ["oval", "oblong", "heart"], "occasion": ["outdoor", "fashion", "beach"]},
            "product": {"brand": "Jimmy Choo", "model_name": "JM4005", "price": 1299.0, "currency": "ILS", "in_stock": True, "sku": "JM4005-v1"},
        },
    },
    {
        "id": "erroca_143",
        "name": "Jimmy Choo JM4005 Oversized Black Sunglasses",
        "image": "images/erroca_womens_sunglasses_032.webp",
        "tags": {
            "frame": {"shape": "oversized-square", "material": "acetate", "color": ["black"], "thickness": "thick", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["gray"], "shape": "square", "size": "oversized"},
            "style": {"aesthetic": ["luxury", "bold", "modern"], "gender_target": "women", "face_shape_fit": ["oval", "oblong", "heart"], "occasion": ["outdoor", "fashion", "beach"]},
            "product": {"brand": "Jimmy Choo", "model_name": "JM4005", "price": 1299.0, "currency": "ILS", "in_stock": True, "sku": "JM4005-v2"},
        },
    },
    {
        "id": "erroca_144",
        "name": "Jimmy Choo JM4002 Cat-Eye Black Gold Sunglasses",
        "image": "images/erroca_womens_sunglasses_033.webp",
        "tags": {
            "frame": {"shape": "cat-eye", "material": "acetate", "color": ["black", "gold"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses", "gradient"], "color": ["gradient-gray"], "shape": "oval", "size": "medium"},
            "style": {"aesthetic": ["luxury", "fashion-forward", "bold"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["outdoor", "fashion", "formal"]},
            "product": {"brand": "Jimmy Choo", "model_name": "JM4002", "price": 1299.0, "currency": "ILS", "in_stock": True, "sku": "JM4002"},
        },
    },
    {
        "id": "erroca_145",
        "name": "Ray-Ban RB2203 Wayfarer Havana Sunglasses",
        "image": "images/erroca_womens_sunglasses_034.webp",
        "tags": {
            "frame": {"shape": "wayfarer", "material": "acetate", "color": ["havana-brown"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["brown"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["vintage", "casual", "retro"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["outdoor", "casual", "everyday"]},
            "product": {"brand": "Ray-Ban", "model_name": "RB2203", "price": 799.0, "currency": "ILS", "in_stock": True, "sku": "RB2203-brown"},
        },
    },
    {
        "id": "erroca_146",
        "name": "Ray-Ban RB2203 Wayfarer Black Sunglasses",
        "image": "images/erroca_womens_sunglasses_035.webp",
        "tags": {
            "frame": {"shape": "wayfarer", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["gray"], "shape": "rectangular", "size": "medium"},
            "style": {"aesthetic": ["classic", "casual", "retro"], "gender_target": "women", "face_shape_fit": ["oval", "heart", "oblong"], "occasion": ["outdoor", "casual", "everyday"]},
            "product": {"brand": "Ray-Ban", "model_name": "RB2203", "price": 799.0, "currency": "ILS", "in_stock": True, "sku": "RB2203-black"},
        },
    },
    {
        "id": "erroca_147",
        "name": "Ray-Ban RB4458 Rectangular Black Sunglasses",
        "image": "images/erroca_womens_sunglasses_036.webp",
        "tags": {
            "frame": {"shape": "rectangular", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["gray"], "shape": "rectangular", "size": "large"},
            "style": {"aesthetic": ["modern", "casual", "minimalist"], "gender_target": "women", "face_shape_fit": ["oval", "round", "heart"], "occasion": ["outdoor", "everyday", "casual"]},
            "product": {"brand": "Ray-Ban", "model_name": "RB4458", "price": 799.0, "currency": "ILS", "in_stock": True, "sku": "RB4458"},
        },
    },
    {
        "id": "erroca_148",
        "name": "Saint Laurent SL801S Square Black Sunglasses",
        "image": "images/erroca_womens_sunglasses_037.webp",
        "tags": {
            "frame": {"shape": "square", "material": "acetate", "color": ["black"], "thickness": "medium", "finish": "glossy", "rim_type": "full-rim"},
            "lenses": {"type": ["sunglasses"], "color": ["gray"], "shape": "square", "size": "large"},
            "style": {"aesthetic": ["luxury", "minimalist", "modern"], "gender_target": "women", "face_shape_fit": ["oval", "oblong", "heart"], "occasion": ["outdoor", "fashion", "everyday"]},
            "product": {"brand": "Saint Laurent", "model_name": "SL801S", "price": 1819.0, "currency": "ILS", "in_stock": True, "sku": "SL801S"},
        },
    },
]


def main():
    with open(CATALOG_JSON, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    existing_ids = {p["id"] for p in catalog["products"]}
    added = 0
    for product in NEW_PRODUCTS:
        if product["id"] in existing_ids:
            print(f"  SKIP (exists): {product['id']}")
            continue
        catalog["products"].append(product)
        added += 1

    catalog["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    catalog["source"] = "erroca.co.il — Men's & Women's Eyeglasses & Sunglasses"

    with open(CATALOG_JSON, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)

    print(f"Added {added} products. Catalog now has {len(catalog['products'])} products total.")


if __name__ == "__main__":
    main()
