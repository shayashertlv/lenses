"""
Download 37 men's sunglasses from Erroca, add to catalog, and build embeddings.

Usage:
    cd lenses
    python ../scripts/add_sunglasses.py
"""

import json
import os
import sys
import urllib.request
import ssl
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
CATALOG_DIR = PROJECT_ROOT / "lenses" / "catalog"
CATALOG_JSON = CATALOG_DIR / "catalog.json"
IMAGES_DIR = CATALOG_DIR / "images"

# ── 37 Sunglasses products scraped from erroca.co.il ─────────────────────────
SUNGLASSES = [
    {
        "id": "erroca_038",
        "name": "Versace VE2289 Gold Pilot Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/11/8056262551332-510x340.webp",
        "tags": {
            "frame": {
                "shape": "pilot",
                "material": "metal",
                "color": ["gold"],
                "thickness": "thin",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["gray"],
                "shape": "teardrop",
                "size": "large"
            },
            "style": {
                "aesthetic": ["luxury", "classic", "fashion-forward"],
                "gender_target": "men",
                "face_shape_fit": ["oval", "square", "heart"],
                "occasion": ["everyday", "driving", "fashion"]
            },
            "product": {
                "brand": "Versace",
                "model_name": "VE2289",
                "price": 909.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "VE2289-GOLD"
            }
        }
    },
    {
        "id": "erroca_039",
        "name": "Versace VE2287 Black Oval Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/11/8056262577547-510x340.webp",
        "tags": {
            "frame": {
                "shape": "oval",
                "material": "metal",
                "color": ["black"],
                "thickness": "medium",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["gray"],
                "shape": "oval",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["luxury", "bold", "fashion-forward"],
                "gender_target": "men",
                "face_shape_fit": ["oval", "square", "oblong"],
                "occasion": ["fashion", "everyday"]
            },
            "product": {
                "brand": "Versace",
                "model_name": "VE2287",
                "price": 1119.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "VE2287-BLK"
            }
        }
    },
    {
        "id": "erroca_040",
        "name": "Versace VE2290 Black Square Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/11/8056262551578-510x340.webp",
        "tags": {
            "frame": {
                "shape": "square",
                "material": "metal",
                "color": ["black"],
                "thickness": "medium",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["gray"],
                "shape": "square",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["luxury", "modern", "bold"],
                "gender_target": "men",
                "face_shape_fit": ["oval", "round", "heart"],
                "occasion": ["everyday", "fashion", "driving"]
            },
            "product": {
                "brand": "Versace",
                "model_name": "VE2290",
                "price": 909.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "VE2290-BLK"
            }
        }
    },
    {
        "id": "erroca_041",
        "name": "Versace VE2290 Gold Rectangular Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/11/8056262551554-510x340.webp",
        "tags": {
            "frame": {
                "shape": "rectangular",
                "material": "metal",
                "color": ["gold"],
                "thickness": "medium",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["brown"],
                "shape": "rectangular",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["luxury", "classic", "fashion-forward"],
                "gender_target": "men",
                "face_shape_fit": ["round", "oval", "heart"],
                "occasion": ["everyday", "fashion", "driving"]
            },
            "product": {
                "brand": "Versace",
                "model_name": "VE2290",
                "price": 909.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "VE2290-GOLD"
            }
        }
    },
    {
        "id": "erroca_042",
        "name": "Versace VE2289 Gold Pilot Sunglasses Dark",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/11/8056262551318-510x340.webp",
        "tags": {
            "frame": {
                "shape": "pilot",
                "material": "metal",
                "color": ["gold"],
                "thickness": "thin",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["smoke"],
                "shape": "teardrop",
                "size": "large"
            },
            "style": {
                "aesthetic": ["luxury", "classic"],
                "gender_target": "men",
                "face_shape_fit": ["oval", "square", "heart"],
                "occasion": ["driving", "everyday", "fashion"]
            },
            "product": {
                "brand": "Versace",
                "model_name": "VE2289",
                "price": 909.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "VE2289-GOLD-DK"
            }
        }
    },
    {
        "id": "erroca_043",
        "name": "Versace VE2291 Gold Rectangular Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/11/8056262548202-510x340.webp",
        "tags": {
            "frame": {
                "shape": "rectangular",
                "material": "metal",
                "color": ["gold"],
                "thickness": "medium",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["brown"],
                "shape": "rectangular",
                "size": "large"
            },
            "style": {
                "aesthetic": ["luxury", "bold", "fashion-forward"],
                "gender_target": "men",
                "face_shape_fit": ["oval", "round", "square"],
                "occasion": ["fashion", "everyday", "formal"]
            },
            "product": {
                "brand": "Versace",
                "model_name": "VE2291",
                "price": 1189.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "VE2291-GOLD"
            }
        }
    },
    {
        "id": "erroca_044",
        "name": "Ray-Ban RB3767 Silver Oval Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/11/8056262563366-510x340.webp",
        "tags": {
            "frame": {
                "shape": "oval",
                "material": "metal",
                "color": ["silver"],
                "thickness": "thin",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["green"],
                "shape": "oval",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["classic", "retro", "casual"],
                "gender_target": "men",
                "face_shape_fit": ["square", "heart", "diamond"],
                "occasion": ["everyday", "outdoor", "beach"]
            },
            "product": {
                "brand": "Ray-Ban",
                "model_name": "RB3767",
                "price": 584.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "RB3767-SLV"
            }
        }
    },
    {
        "id": "erroca_045",
        "name": "Ray-Ban RB3768 Gold Oval Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/11/8056262562246-510x340.webp",
        "tags": {
            "frame": {
                "shape": "oval",
                "material": "metal",
                "color": ["gold"],
                "thickness": "thin",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["G-15-green"],
                "shape": "oval",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["classic", "vintage", "casual"],
                "gender_target": "men",
                "face_shape_fit": ["square", "heart", "oval"],
                "occasion": ["everyday", "outdoor", "beach"]
            },
            "product": {
                "brand": "Ray-Ban",
                "model_name": "RB3768",
                "price": 584.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "RB3768-GOLD"
            }
        }
    },
    {
        "id": "erroca_046",
        "name": "Prada PRC50S Gray Square Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/11/8056262561843-510x340.webp",
        "tags": {
            "frame": {
                "shape": "square",
                "material": "acetate",
                "color": ["black"],
                "thickness": "thick",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["gray"],
                "shape": "square",
                "size": "large"
            },
            "style": {
                "aesthetic": ["luxury", "modern", "bold"],
                "gender_target": "men",
                "face_shape_fit": ["oval", "round", "heart"],
                "occasion": ["fashion", "everyday", "formal"]
            },
            "product": {
                "brand": "Prada",
                "model_name": "PRC50S",
                "price": 1469.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "PRC50S-GRY"
            }
        }
    },
    {
        "id": "erroca_047",
        "name": "Prada PRC50S Gold Square Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/11/8056262557129-510x340.webp",
        "tags": {
            "frame": {
                "shape": "square",
                "material": "mixed-metal-acetate",
                "color": ["gold", "havana-brown"],
                "thickness": "medium",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["brown"],
                "shape": "square",
                "size": "large"
            },
            "style": {
                "aesthetic": ["luxury", "classic", "fashion-forward"],
                "gender_target": "men",
                "face_shape_fit": ["oval", "round", "heart"],
                "occasion": ["fashion", "everyday", "driving"]
            },
            "product": {
                "brand": "Prada",
                "model_name": "PRC50S",
                "price": 1469.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "PRC50S-GOLD"
            }
        }
    },
    {
        "id": "erroca_048",
        "name": "Ray-Ban RB3832 Black Rectangular Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/10/8056262563533-510x340.webp",
        "tags": {
            "frame": {
                "shape": "rectangular",
                "material": "metal",
                "color": ["black"],
                "thickness": "medium",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses", "polarized"],
                "color": ["green"],
                "shape": "rectangular",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["classic", "modern", "casual"],
                "gender_target": "men",
                "face_shape_fit": ["round", "oval", "heart"],
                "occasion": ["everyday", "driving", "outdoor"]
            },
            "product": {
                "brand": "Ray-Ban",
                "model_name": "RB3832",
                "price": 699.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "RB3832-BLK-POL"
            }
        }
    },
    {
        "id": "erroca_049",
        "name": "Ray-Ban RB3832 Black Rectangular Sunglasses Classic",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/10/8056262563489-510x340.webp",
        "tags": {
            "frame": {
                "shape": "rectangular",
                "material": "metal",
                "color": ["black"],
                "thickness": "medium",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["G-15-green"],
                "shape": "rectangular",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["classic", "casual", "minimalist"],
                "gender_target": "men",
                "face_shape_fit": ["round", "oval", "heart"],
                "occasion": ["everyday", "outdoor", "driving"]
            },
            "product": {
                "brand": "Ray-Ban",
                "model_name": "RB3832",
                "price": 584.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "RB3832-BLK"
            }
        }
    },
    {
        "id": "erroca_050",
        "name": "Ray-Ban RB3775M Black Oval Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/10/8056262668528-510x340.webp",
        "tags": {
            "frame": {
                "shape": "oval",
                "material": "metal",
                "color": ["black"],
                "thickness": "thin",
                "finish": "matte",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses", "polarized"],
                "color": ["gray"],
                "shape": "oval",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["classic", "retro", "casual"],
                "gender_target": "men",
                "face_shape_fit": ["square", "oblong", "diamond"],
                "occasion": ["everyday", "driving", "outdoor"]
            },
            "product": {
                "brand": "Ray-Ban",
                "model_name": "RB3775M",
                "price": 909.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "RB3775M-BLK"
            }
        }
    },
    {
        "id": "erroca_051",
        "name": "Ray-Ban RB3774D Gold Oval Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/10/8056262667019-510x340.webp",
        "tags": {
            "frame": {
                "shape": "oval",
                "material": "metal",
                "color": ["gold"],
                "thickness": "thin",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["B-15-brown"],
                "shape": "oval",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["vintage", "classic", "casual"],
                "gender_target": "men",
                "face_shape_fit": ["square", "oblong", "heart"],
                "occasion": ["everyday", "outdoor", "beach"]
            },
            "product": {
                "brand": "Ray-Ban",
                "model_name": "RB3774D",
                "price": 649.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "RB3774D-GOLD"
            }
        }
    },
    {
        "id": "erroca_052",
        "name": "Ray-Ban RB3770 Silver Oval Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/10/8056262563373-510x340.webp",
        "tags": {
            "frame": {
                "shape": "oval",
                "material": "metal",
                "color": ["silver"],
                "thickness": "thin",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["blue"],
                "shape": "oval",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["modern", "classic", "casual"],
                "gender_target": "men",
                "face_shape_fit": ["square", "heart", "diamond"],
                "occasion": ["everyday", "outdoor", "beach"]
            },
            "product": {
                "brand": "Ray-Ban",
                "model_name": "RB3770",
                "price": 769.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "RB3770-SLV"
            }
        }
    },
    {
        "id": "erroca_053",
        "name": "Ray-Ban RB3770 Gold Oval Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/10/8056262563397-510x340.webp",
        "tags": {
            "frame": {
                "shape": "oval",
                "material": "metal",
                "color": ["gold"],
                "thickness": "thin",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["G-15-green"],
                "shape": "oval",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["classic", "vintage", "casual"],
                "gender_target": "men",
                "face_shape_fit": ["square", "heart", "diamond"],
                "occasion": ["everyday", "outdoor", "driving"]
            },
            "product": {
                "brand": "Ray-Ban",
                "model_name": "RB3770",
                "price": 769.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "RB3770-GOLD"
            }
        }
    },
    {
        "id": "erroca_054",
        "name": "Ray-Ban RB3735 Gray Rectangular Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/10/8056262668030-510x340.webp",
        "tags": {
            "frame": {
                "shape": "rectangular",
                "material": "metal",
                "color": ["gunmetal"],
                "thickness": "medium",
                "finish": "matte",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses", "polarized"],
                "color": ["gray"],
                "shape": "rectangular",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["modern", "sporty", "casual"],
                "gender_target": "men",
                "face_shape_fit": ["round", "oval", "heart"],
                "occasion": ["driving", "everyday", "outdoor"]
            },
            "product": {
                "brand": "Ray-Ban",
                "model_name": "RB3735",
                "price": 909.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "RB3735-GRY"
            }
        }
    },
    {
        "id": "erroca_055",
        "name": "Ray-Ban RB3671CH Black Chromance Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/10/8056262673072-510x340.webp",
        "tags": {
            "frame": {
                "shape": "rectangular",
                "material": "metal",
                "color": ["black"],
                "thickness": "medium",
                "finish": "matte",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses", "polarized"],
                "color": ["blue"],
                "shape": "rectangular",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["sporty", "modern", "casual"],
                "gender_target": "men",
                "face_shape_fit": ["round", "oval", "oblong"],
                "occasion": ["driving", "outdoor", "sport"]
            },
            "product": {
                "brand": "Ray-Ban",
                "model_name": "RB3671CH",
                "price": 979.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "RB3671CH-BLK"
            }
        }
    },
    {
        "id": "erroca_056",
        "name": "Ray-Ban RB2232 Brown Rectangular Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/10/8056262562178-510x340.webp",
        "tags": {
            "frame": {
                "shape": "rectangular",
                "material": "acetate",
                "color": ["havana-brown"],
                "thickness": "thick",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["B-15-brown"],
                "shape": "rectangular",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["classic", "retro", "casual"],
                "gender_target": "men",
                "face_shape_fit": ["round", "oval", "heart"],
                "occasion": ["everyday", "outdoor", "beach"]
            },
            "product": {
                "brand": "Ray-Ban",
                "model_name": "RB2232",
                "price": 899.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "RB2232-BRN"
            }
        }
    },
    {
        "id": "erroca_057",
        "name": "Ray-Ban RB3025 Aviator Gold Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/10/8056262581032-510x340.webp",
        "tags": {
            "frame": {
                "shape": "aviator",
                "material": "metal",
                "color": ["gold"],
                "thickness": "thin",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["G-15-green"],
                "shape": "teardrop",
                "size": "large"
            },
            "style": {
                "aesthetic": ["classic", "vintage", "casual"],
                "gender_target": "men",
                "face_shape_fit": ["oval", "square", "heart", "universal"],
                "occasion": ["everyday", "driving", "outdoor", "beach"]
            },
            "product": {
                "brand": "Ray-Ban",
                "model_name": "RB3025",
                "price": 584.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "RB3025-GOLD"
            }
        }
    },
    {
        "id": "erroca_058",
        "name": "Ray-Ban RB2222 Brown Round Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/10/8056262560884-510x340.webp",
        "tags": {
            "frame": {
                "shape": "round",
                "material": "acetate",
                "color": ["havana-brown"],
                "thickness": "medium",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["brown"],
                "shape": "round",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["retro", "vintage", "casual"],
                "gender_target": "men",
                "face_shape_fit": ["square", "oblong", "diamond"],
                "occasion": ["everyday", "outdoor", "beach"]
            },
            "product": {
                "brand": "Ray-Ban",
                "model_name": "RB2222",
                "price": 584.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "RB2222-BRN"
            }
        }
    },
    {
        "id": "erroca_059",
        "name": "Prada PRA16S Black Square Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/07/8056262134474-510x340.webp",
        "tags": {
            "frame": {
                "shape": "square",
                "material": "acetate",
                "color": ["black"],
                "thickness": "thick",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["gray"],
                "shape": "square",
                "size": "large"
            },
            "style": {
                "aesthetic": ["luxury", "bold", "modern"],
                "gender_target": "men",
                "face_shape_fit": ["oval", "round", "heart"],
                "occasion": ["fashion", "everyday", "formal"]
            },
            "product": {
                "brand": "Prada",
                "model_name": "PRA16S",
                "price": 1469.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "PRA16S-BLK"
            }
        }
    },
    {
        "id": "erroca_060",
        "name": "Prada PRA08S Brown Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/07/8056262489444-510x340.webp",
        "tags": {
            "frame": {
                "shape": "rectangular",
                "material": "acetate",
                "color": ["havana-brown"],
                "thickness": "thick",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["brown"],
                "shape": "rectangular",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["luxury", "classic", "fashion-forward"],
                "gender_target": "men",
                "face_shape_fit": ["round", "oval", "heart"],
                "occasion": ["fashion", "everyday", "driving"]
            },
            "product": {
                "brand": "Prada",
                "model_name": "PRA08S",
                "price": 1119.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "PRA08S-BRN"
            }
        }
    },
    {
        "id": "erroca_061",
        "name": "Prada PRA16S Brown Blue-Lens Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/02/8056262360125-510x340.webp",
        "tags": {
            "frame": {
                "shape": "square",
                "material": "acetate",
                "color": ["havana-brown"],
                "thickness": "thick",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["blue"],
                "shape": "square",
                "size": "large"
            },
            "style": {
                "aesthetic": ["luxury", "bold", "fashion-forward"],
                "gender_target": "men",
                "face_shape_fit": ["oval", "round", "heart"],
                "occasion": ["fashion", "everyday", "beach"]
            },
            "product": {
                "brand": "Prada",
                "model_name": "PRA16S",
                "price": 1679.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "PRA16S-BRN-BLU"
            }
        }
    },
    {
        "id": "erroca_062",
        "name": "Prada PRA17S Brown Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/07/8056262349366-510x340.webp",
        "tags": {
            "frame": {
                "shape": "rectangular",
                "material": "acetate",
                "color": ["tortoiseshell"],
                "thickness": "thick",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["brown"],
                "shape": "rectangular",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["luxury", "classic", "professional"],
                "gender_target": "men",
                "face_shape_fit": ["round", "oval", "oblong"],
                "occasion": ["everyday", "driving", "formal"]
            },
            "product": {
                "brand": "Prada",
                "model_name": "PRA17S",
                "price": 1469.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "PRA17S-BRN"
            }
        }
    },
    {
        "id": "erroca_063",
        "name": "Prada PRA17S Brown Polarized Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/07/8056262360118-510x340.webp",
        "tags": {
            "frame": {
                "shape": "rectangular",
                "material": "acetate",
                "color": ["tortoiseshell"],
                "thickness": "thick",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses", "polarized"],
                "color": ["brown"],
                "shape": "rectangular",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["luxury", "classic", "professional"],
                "gender_target": "men",
                "face_shape_fit": ["round", "oval", "oblong"],
                "occasion": ["driving", "everyday", "outdoor"]
            },
            "product": {
                "brand": "Prada",
                "model_name": "PRA17S",
                "price": 1679.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "PRA17S-BRN-POL"
            }
        }
    },
    {
        "id": "erroca_064",
        "name": "Tom Ford FT1178 Black Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/06/0889214548962-510x340.webp",
        "tags": {
            "frame": {
                "shape": "square",
                "material": "acetate",
                "color": ["black"],
                "thickness": "thick",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["gray"],
                "shape": "square",
                "size": "large"
            },
            "style": {
                "aesthetic": ["luxury", "bold", "modern"],
                "gender_target": "men",
                "face_shape_fit": ["oval", "round", "heart"],
                "occasion": ["fashion", "everyday", "formal"]
            },
            "product": {
                "brand": "Tom Ford",
                "model_name": "FT1178",
                "price": 1469.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "FT1178-BLK"
            }
        }
    },
    {
        "id": "erroca_065",
        "name": "Tom Ford FT1119 Brown Green-Lens Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/06/0889214481283-510x340.webp",
        "tags": {
            "frame": {
                "shape": "rectangular",
                "material": "acetate",
                "color": ["havana-brown"],
                "thickness": "thick",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["green"],
                "shape": "rectangular",
                "size": "large"
            },
            "style": {
                "aesthetic": ["luxury", "classic", "fashion-forward"],
                "gender_target": "men",
                "face_shape_fit": ["round", "oval", "square"],
                "occasion": ["fashion", "everyday", "driving"]
            },
            "product": {
                "brand": "Tom Ford",
                "model_name": "FT1119",
                "price": 1889.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "FT1119-BRN-GRN"
            }
        }
    },
    {
        "id": "erroca_066",
        "name": "Tom Ford FT1119 Brown Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/06/0889214481276-510x340.webp",
        "tags": {
            "frame": {
                "shape": "rectangular",
                "material": "acetate",
                "color": ["havana-brown"],
                "thickness": "thick",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["brown"],
                "shape": "rectangular",
                "size": "large"
            },
            "style": {
                "aesthetic": ["luxury", "classic", "bold"],
                "gender_target": "men",
                "face_shape_fit": ["round", "oval", "square"],
                "occasion": ["fashion", "everyday", "formal"]
            },
            "product": {
                "brand": "Tom Ford",
                "model_name": "FT1119",
                "price": 1889.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "FT1119-BRN"
            }
        }
    },
    {
        "id": "erroca_067",
        "name": "Prada PRA05S Brown Square Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/06/8056597971836-510x340.webp",
        "tags": {
            "frame": {
                "shape": "square",
                "material": "acetate",
                "color": ["tortoiseshell"],
                "thickness": "thick",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["brown"],
                "shape": "square",
                "size": "large"
            },
            "style": {
                "aesthetic": ["luxury", "classic", "bold"],
                "gender_target": "men",
                "face_shape_fit": ["oval", "round", "heart"],
                "occasion": ["fashion", "everyday", "driving"]
            },
            "product": {
                "brand": "Prada",
                "model_name": "PRA05S",
                "price": 1399.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "PRA05S-BRN"
            }
        }
    },
    {
        "id": "erroca_068",
        "name": "Ray-Ban RB0840S Brown Square Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/06/8056597720694-510x340.webp",
        "tags": {
            "frame": {
                "shape": "square",
                "material": "acetate",
                "color": ["havana-brown"],
                "thickness": "thick",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["green"],
                "shape": "square",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["retro", "classic", "casual"],
                "gender_target": "men",
                "face_shape_fit": ["oval", "round", "heart"],
                "occasion": ["everyday", "outdoor", "beach"]
            },
            "product": {
                "brand": "Ray-Ban",
                "model_name": "RB0840S",
                "price": 699.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "RB0840S-BRN"
            }
        }
    },
    {
        "id": "erroca_069",
        "name": "David Beckham DB1139 Yellow Aviator Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/06/0716736965369-510x340.webp",
        "tags": {
            "frame": {
                "shape": "aviator",
                "material": "metal",
                "color": ["gold"],
                "thickness": "thin",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["blue"],
                "shape": "teardrop",
                "size": "large"
            },
            "style": {
                "aesthetic": ["classic", "fashion-forward", "bold"],
                "gender_target": "men",
                "face_shape_fit": ["oval", "square", "heart"],
                "occasion": ["fashion", "everyday", "beach"]
            },
            "product": {
                "brand": "David Beckham",
                "model_name": "DB1139",
                "price": 769.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "DB1139-YLW"
            }
        }
    },
    {
        "id": "erroca_070",
        "name": "David Beckham DB1139 Clear Aviator Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/06/0716736965376-510x340.webp",
        "tags": {
            "frame": {
                "shape": "aviator",
                "material": "metal",
                "color": ["silver"],
                "thickness": "thin",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["gray"],
                "shape": "teardrop",
                "size": "large"
            },
            "style": {
                "aesthetic": ["classic", "minimalist", "casual"],
                "gender_target": "men",
                "face_shape_fit": ["oval", "square", "heart"],
                "occasion": ["everyday", "driving", "outdoor"]
            },
            "product": {
                "brand": "David Beckham",
                "model_name": "DB1139",
                "price": 769.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "DB1139-CLR"
            }
        }
    },
    {
        "id": "erroca_071",
        "name": "David Beckham DB1007 Black Green-Lens Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/06/0197737095062-510x340.webp",
        "tags": {
            "frame": {
                "shape": "rectangular",
                "material": "acetate",
                "color": ["black"],
                "thickness": "thick",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["green"],
                "shape": "rectangular",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["classic", "bold", "professional"],
                "gender_target": "men",
                "face_shape_fit": ["round", "oval", "heart"],
                "occasion": ["everyday", "driving", "formal"]
            },
            "product": {
                "brand": "David Beckham",
                "model_name": "DB1007",
                "price": 769.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "DB1007-BLK"
            }
        }
    },
    {
        "id": "erroca_072",
        "name": "David Beckham DB1160 Brown Blue-Lens Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/06/0197737090258-510x340.webp",
        "tags": {
            "frame": {
                "shape": "rectangular",
                "material": "acetate",
                "color": ["havana-brown"],
                "thickness": "medium",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["blue"],
                "shape": "rectangular",
                "size": "medium"
            },
            "style": {
                "aesthetic": ["modern", "casual", "fashion-forward"],
                "gender_target": "men",
                "face_shape_fit": ["round", "oval", "heart"],
                "occasion": ["everyday", "outdoor", "beach"]
            },
            "product": {
                "brand": "David Beckham",
                "model_name": "DB1160",
                "price": 699.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "DB1160-BRN"
            }
        }
    },
    {
        "id": "erroca_073",
        "name": "Gucci SL738S Black Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/04/0889652520971-510x340.webp",
        "tags": {
            "frame": {
                "shape": "square",
                "material": "acetate",
                "color": ["black"],
                "thickness": "thick",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["brown"],
                "shape": "square",
                "size": "large"
            },
            "style": {
                "aesthetic": ["luxury", "bold", "fashion-forward"],
                "gender_target": "men",
                "face_shape_fit": ["oval", "round", "heart"],
                "occasion": ["fashion", "everyday", "formal"]
            },
            "product": {
                "brand": "Gucci",
                "model_name": "SL738S",
                "price": 1959.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "SL738S-BLK"
            }
        }
    },
    {
        "id": "erroca_074",
        "name": "Balenciaga BB0328S Gold Sunglasses",
        "image_url": "https://erroca.co.il/wp-content/uploads/2025/04/0889652502274-510x340.webp",
        "tags": {
            "frame": {
                "shape": "aviator",
                "material": "metal",
                "color": ["gold"],
                "thickness": "medium",
                "finish": "glossy",
                "rim_type": "full-rim"
            },
            "lenses": {
                "type": ["sunglasses"],
                "color": ["brown"],
                "shape": "teardrop",
                "size": "oversized"
            },
            "style": {
                "aesthetic": ["luxury", "bold", "fashion-forward", "streetwear"],
                "gender_target": "men",
                "face_shape_fit": ["oval", "square", "oblong"],
                "occasion": ["fashion", "everyday"]
            },
            "product": {
                "brand": "Balenciaga",
                "model_name": "BB0328S",
                "price": 1539.0,
                "currency": "ILS",
                "in_stock": True,
                "sku": "BB0328S-GOLD"
            }
        }
    },
]


def download_images():
    """Download all sunglasses images to catalog/images/."""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    # Allow HTTPS connections
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    success = 0
    failed = 0

    for i, product in enumerate(SUNGLASSES):
        idx = int(product["id"].split("_")[1])  # e.g. erroca_038 -> 38
        filename = f"erroca_mens_{idx:03d}.jpg"
        filepath = IMAGES_DIR / filename

        if filepath.exists():
            print(f"  [{idx:03d}] Already exists: {filename}")
            success += 1
            continue

        url = product["image_url"]
        print(f"  [{idx:03d}] Downloading {product['name']}...")
        print(f"         {url}")

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            with urllib.request.urlopen(req, context=ctx) as resp:
                data = resp.read()

            # Convert webp to jpg if needed
            if url.endswith(".webp"):
                try:
                    from PIL import Image
                    import io
                    img = Image.open(io.BytesIO(data))
                    buf = io.BytesIO()
                    img.convert("RGB").save(buf, format="JPEG", quality=90)
                    data = buf.getvalue()
                except ImportError:
                    print(f"         WARNING: Pillow not available, saving as-is")

            with open(filepath, "wb") as f:
                f.write(data)

            size_kb = len(data) / 1024
            print(f"         Saved: {filename} ({size_kb:.1f} KB)")
            success += 1

        except Exception as e:
            print(f"         FAILED: {e}")
            failed += 1

    print(f"\nDownload complete: {success} OK, {failed} failed")
    return failed == 0


def update_catalog():
    """Add sunglasses products to catalog.json."""
    with open(CATALOG_JSON, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    existing_ids = {p["id"] for p in catalog["products"]}
    added = 0

    for product in SUNGLASSES:
        if product["id"] in existing_ids:
            print(f"  [{product['id']}] Already in catalog, skipping")
            continue

        idx = int(product["id"].split("_")[1])
        entry = {
            "id": product["id"],
            "name": product["name"],
            "image": f"images/erroca_mens_{idx:03d}.jpg",
            "tags": product["tags"],
        }
        catalog["products"].append(entry)
        added += 1
        print(f"  [{product['id']}] Added: {product['name']}")

    # Update metadata
    catalog["source"] = "erroca.co.il — Men's Eyeglasses & Sunglasses"
    catalog["last_updated"] = "2026-03-24T18:00:00Z"

    with open(CATALOG_JSON, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)

    total = len(catalog["products"])
    print(f"\nCatalog updated: {added} added, {total} total products")
    return True


def validate():
    """Quick validation: check images exist and tags are valid."""
    sys.path.insert(0, str(PROJECT_ROOT / "lenses"))
    from tag_schema import validate_tags

    with open(CATALOG_JSON, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    errors = 0
    for product in catalog["products"]:
        pid = product["id"]
        # Check image
        img_path = CATALOG_DIR / product["image"]
        if not img_path.exists():
            print(f"  [{pid}] MISSING IMAGE: {img_path}")
            errors += 1
        # Check tags
        warnings = validate_tags(product["tags"])
        if warnings:
            for w in warnings:
                print(f"  [{pid}] TAG WARNING: {w}")
            errors += 1

    if errors == 0:
        print(f"  All {len(catalog['products'])} products validated OK")
    else:
        print(f"  {errors} issues found")
    return errors == 0


if __name__ == "__main__":
    print("=" * 60)
    print("STEP 1: Download sunglasses images")
    print("=" * 60)
    if not download_images():
        print("Some downloads failed. Fix and retry.")
        sys.exit(1)

    print()
    print("=" * 60)
    print("STEP 2: Add products to catalog.json")
    print("=" * 60)
    update_catalog()

    print()
    print("=" * 60)
    print("STEP 3: Validate catalog")
    print("=" * 60)
    if not validate():
        print("Validation failed. Fix issues before building embeddings.")
        sys.exit(1)

    print()
    print("=" * 60)
    print("STEP 4: Build embeddings (requires GEMINI_API_KEY)")
    print("=" * 60)
    print("Run: cd lenses && python catalog_manager.py build")
    print("This will generate descriptions and embeddings for ALL products.")
