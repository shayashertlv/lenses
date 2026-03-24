"""Fix men's/unisex glasses tags based on visual inspection of actual images."""
import json
from pathlib import Path

CATALOG_JSON = Path(r"C:\Users\Shay\PycharmProjects\lenses\lenses\catalog\catalog.json")

CORRECTIONS = {
    # erroca_001-005: Emporio Armani metal semi-rimless — correct shapes/rims,
    # visual confirms mixed material on several
    "erroca_001": {
        "frame": {"material": "mixed-metal-acetate", "finish": "glossy"},
    },
    "erroca_002": {
        "frame": {"material": "mixed-metal-acetate"},
    },
    "erroca_003": {
        "frame": {"material": "mixed-metal-acetate"},
    },
    "erroca_004": {
        "frame": {"material": "mixed-metal-acetate"},
    },
    "erroca_005": {
        "frame": {"material": "mixed-metal-acetate"},
    },
    # erroca_006: Emporio Armani EA3147 – thick glossy blue acetate full-rim
    "erroca_006": {
        "frame": {"thickness": "thick", "finish": "glossy"},
    },
    # erroca_007: Emporio Armani EA3237 – round gray acetate full-rim thick matte (NOT rectangular transparent)
    "erroca_007": {
        "name": "Emporio Armani EA3237 Round Gray",
        "frame": {"shape": "round", "color": ["matte-black"], "thickness": "thick", "finish": "matte"},
        "lenses": {"shape": "round"},
    },
    # erroca_008: Ray-Ban RX5441 Round – visual shows metal thin, not acetate thick
    "erroca_008": {
        "frame": {"material": "metal", "thickness": "thin"},
    },
    # erroca_009: correct
    # erroca_010: Ray-Ban RX6494 Browline – name+product confirm browline; keep as-is
    # erroca_011: Ray-Ban RX6496 Geometric – hexagonal not generic geometric
    "erroca_011": {
        "name": "Ray-Ban RX6496 Hexagonal Black Gold",
        "frame": {"shape": "hexagonal"},
        "lenses": {"shape": "rectangular"},
    },
    # erroca_012: Ray-Ban RX6509 – visual shows hexagonal not round
    "erroca_012": {
        "name": "Ray-Ban RX6509 Hexagonal Gold",
        "frame": {"shape": "hexagonal"},
        "lenses": {"shape": "rectangular"},
    },
    # erroca_013: Ray-Ban RX6513 – visual shows glossy finish and black (not matte-black)
    "erroca_013": {
        "frame": {"color": ["black"], "finish": "glossy"},
    },
    # erroca_014: Ray-Ban RX7199 – rectangular not square; mixed thick glossy; black+red
    "erroca_014": {
        "name": "Ray-Ban RX7199 Rectangular Black Red",
        "frame": {"shape": "rectangular", "color": ["black", "red"], "material": "mixed-metal-acetate",
                  "thickness": "thick", "finish": "glossy"},
        "lenses": {"shape": "rectangular"},
    },
    # erroca_015: Ray-Ban RX7208 – metal semi-rimless (not plastic full-rim)
    "erroca_015": {
        "frame": {"material": "metal", "rim_type": "semi-rimless"},
    },
    # erroca_016: correct (square, black, acetate, thick, glossy)
    # erroca_017: Versace VE3363U Wayfarer – keep wayfarer (name confirms); visual thickness correct
    # erroca_018: Versace VE3363U Wayfarer – keep wayfarer
    # erroca_019: Vogue VO5326 Round – visual confirms thick not medium
    "erroca_019": {
        "frame": {"thickness": "thick"},
    },
    # erroca_020: Ray-Ban RX3447V – visual shows transparent acetate thick (not gunmetal metal thin matte)
    "erroca_020": {
        "name": "Ray-Ban RX3447V Round Transparent",
        "frame": {"color": ["transparent"], "material": "acetate", "thickness": "thick", "finish": "glossy"},
    },
    # erroca_021: Prada PS01LV – browline semi-rimless (not rectangular full-rim)
    "erroca_021": {
        "name": "Prada Linea Rossa PS01LV Browline Black",
        "frame": {"shape": "browline", "color": ["black"], "rim_type": "semi-rimless", "finish": "glossy"},
        "lenses": {"shape": "rectangular"},
    },
    # erroca_022: Ray-Ban RX7047 – visual shows browline semi-rimless with black+red mixed
    "erroca_022": {
        "name": "Ray-Ban RX7047 Browline Black Red",
        "frame": {"shape": "browline", "color": ["black", "red"], "material": "mixed-metal-acetate",
                  "rim_type": "semi-rimless"},
        "lenses": {"shape": "rectangular"},
    },
    # erroca_023: Ray-Ban RX6335 – visual shows browline semi-rimless glossy medium
    "erroca_023": {
        "name": "Ray-Ban RX6335 Browline Black",
        "frame": {"shape": "browline", "color": ["black"], "rim_type": "semi-rimless",
                  "thickness": "medium", "finish": "glossy"},
        "lenses": {"shape": "rectangular"},
    },
    # erroca_024: Ray-Ban RX8416 – visual shows browline navy/silver mixed semi-rimless glossy
    "erroca_024": {
        "name": "Ray-Ban RX8416 Browline Blue Silver Semi-Rimless",
        "frame": {"shape": "browline", "color": ["blue", "silver"], "material": "mixed-metal-acetate",
                  "finish": "glossy"},
        "lenses": {"shape": "rectangular"},
    },
    # erroca_025: Ray-Ban Browline – confirmed browline; visual shows black+red mixed glossy
    "erroca_025": {
        "frame": {"color": ["black", "red"], "material": "mixed-metal-acetate", "finish": "glossy"},
    },
    # erroca_026: Ray-Ban – browline not rectangular; blue+red mixed
    "erroca_026": {
        "name": "Ray-Ban Browline Blue Red Semi-Rimless",
        "frame": {"shape": "browline", "color": ["blue", "red"], "material": "mixed-metal-acetate",
                  "finish": "glossy"},
        "lenses": {"shape": "rectangular"},
    },
    # erroca_027: Prada PS50GV – browline semi-rimless black+red mixed glossy
    "erroca_027": {
        "name": "Prada Linea Rossa PS50GV Browline Black Red",
        "frame": {"shape": "browline", "color": ["black", "red"], "material": "mixed-metal-acetate",
                  "rim_type": "semi-rimless", "finish": "glossy"},
        "lenses": {"shape": "rectangular"},
    },
    # erroca_028: Prada PS50GV – wayfarer blue matte (not rectangular transparent glossy)
    "erroca_028": {
        "name": "Prada Linea Rossa PS50GV Wayfarer Blue",
        "frame": {"shape": "wayfarer", "color": ["blue"], "finish": "matte"},
        "lenses": {"shape": "rectangular"},
    },
    # erroca_029: Prada PS04IV – wayfarer black (not rectangular)
    "erroca_029": {
        "name": "Prada Linea Rossa PS04IV Wayfarer Black",
        "frame": {"shape": "wayfarer", "color": ["black"]},
        "lenses": {"shape": "rectangular"},
    },
    # erroca_030: Prada PS04IV – wayfarer blue (not rectangular)
    "erroca_030": {
        "name": "Prada Linea Rossa PS04IV Wayfarer Blue",
        "frame": {"shape": "wayfarer"},
        "lenses": {"shape": "rectangular"},
    },
    # erroca_031: Ray-Ban RX7256 – wayfarer transparent (not square)
    "erroca_031": {
        "name": "Ray-Ban RX7256 Wayfarer Transparent",
        "frame": {"shape": "wayfarer"},
        "lenses": {"shape": "rectangular"},
    },
    # erroca_032: Ray-Ban Hexagonal – round black plastic matte (not acetate glossy)
    "erroca_032": {
        "frame": {"material": "plastic", "finish": "matte"},
    },
    # erroca_033: Ray-Ban Hexagonal Tortoiseshell – round black+tortoiseshell plastic matte
    "erroca_033": {
        "frame": {"color": ["black", "tortoiseshell"], "material": "plastic", "finish": "matte"},
    },
    # erroca_034: Ray-Ban RX7047 – square matte-black (not rectangular)
    "erroca_034": {
        "name": "Ray-Ban RX7047 Square Matte Black",
        "frame": {"shape": "square"},
        "lenses": {"shape": "square"},
    },
    # erroca_035: Vogue VO5519 – plastic matte (not acetate glossy)
    "erroca_035": {
        "frame": {"material": "plastic", "finish": "matte"},
    },
    # erroca_036: Prada PS50PV – square black+red mixed semi-rimless (not rectangular metal)
    "erroca_036": {
        "name": "Prada Linea Rossa PS50PV Square Black Red Semi-Rimless",
        "frame": {"shape": "square", "color": ["black", "red"], "material": "mixed-metal-acetate"},
        "lenses": {"shape": "square"},
    },
    # erroca_037: Ray-Ban RX3447V – semi-rimless (not full-rim)
    "erroca_037": {
        "frame": {"rim_type": "semi-rimless"},
    },
    # erroca_038: Versace VE2289 Pilot – visual was a promo image; keep pilot shape but
    # update to aviator (equivalent in schema and more standard term)
    # leaving as-is — pilot is valid and name+product confirm it
    # erroca_039: Versace VE2287 – aviator gold amber (not oval black gray)
    "erroca_039": {
        "name": "Versace VE2287 Aviator Gold Sunglasses",
        "frame": {"shape": "aviator", "color": ["gold"], "thickness": "thin"},
        "lenses": {"color": ["amber"], "shape": "teardrop"},
    },
    # erroca_040: Versace VE2290 Black – rectangular semi-rimless (not square full-rim)
    "erroca_040": {
        "name": "Versace VE2290 Rectangular Black Sunglasses",
        "frame": {"shape": "rectangular", "material": "mixed-metal-acetate", "rim_type": "semi-rimless"},
        "lenses": {"shape": "rectangular"},
    },
    # erroca_041: Versace VE2290 Gold – visual shows navy+white semi-rimless blue lenses
    "erroca_041": {
        "name": "Versace VE2290 Rectangular Blue Sunglasses",
        "frame": {"color": ["blue", "white"], "material": "mixed-metal-acetate", "rim_type": "semi-rimless"},
        "lenses": {"color": ["blue"]},
    },
    # erroca_042: Versace VE2289 Gold Dark – rectangular semi-rimless green lenses (not pilot teardrop)
    "erroca_042": {
        "name": "Versace VE2289 Rectangular Gold Sunglasses Dark",
        "frame": {"shape": "rectangular", "rim_type": "semi-rimless", "thickness": "medium"},
        "lenses": {"color": ["green"], "shape": "rectangular"},
    },
    # erroca_043: Versace VE2291 – aviator gold thin gradient-brown (not rectangular medium)
    "erroca_043": {
        "name": "Versace VE2291 Aviator Gold Sunglasses",
        "frame": {"shape": "aviator", "thickness": "thin"},
        "lenses": {"color": ["gradient-brown"], "shape": "teardrop"},
    },
    # erroca_044: Ray-Ban RB3767 – aviator gold G-15-green (not oval silver green)
    "erroca_044": {
        "name": "Ray-Ban RB3767 Aviator Gold Sunglasses",
        "frame": {"shape": "aviator", "color": ["gold"]},
        "lenses": {"color": ["G-15-green"], "shape": "teardrop"},
    },
    # erroca_045: Ray-Ban RB3768 – hexagonal black/gold mixed medium gray (not oval gold G-15)
    "erroca_045": {
        "name": "Ray-Ban RB3768 Hexagonal Black Gold Sunglasses",
        "frame": {"shape": "hexagonal", "color": ["black", "gold"], "material": "mixed-metal-acetate",
                  "thickness": "medium"},
        "lenses": {"color": ["gray"], "shape": "rectangular"},
    },
    # erroca_046: Prada PRC50S Gray – aviator silver metal thin blue (not square black acetate thick gray)
    "erroca_046": {
        "name": "Prada PRC50S Aviator Silver Sunglasses",
        "frame": {"shape": "aviator", "color": ["silver"], "material": "metal", "thickness": "thin"},
        "lenses": {"color": ["blue"], "shape": "teardrop"},
    },
    # erroca_047: Prada PRC50S Gold – aviator gold metal thin amber (not square mixed medium brown)
    "erroca_047": {
        "name": "Prada PRC50S Aviator Gold Sunglasses",
        "frame": {"shape": "aviator", "color": ["gold"], "material": "metal", "thickness": "thin"},
        "lenses": {"color": ["amber"], "shape": "teardrop"},
    },
    # erroca_048: Ray-Ban RB3832 – aviator gold thin gradient-brown (not rectangular black green)
    "erroca_048": {
        "name": "Ray-Ban RB3832 Aviator Gold Sunglasses",
        "frame": {"shape": "aviator", "color": ["gold"], "thickness": "thin"},
        "lenses": {"color": ["gradient-brown"], "shape": "teardrop"},
    },
    # erroca_049: Ray-Ban RB3832 Classic – aviator gold thin (not rectangular black medium)
    "erroca_049": {
        "name": "Ray-Ban RB3832 Aviator Gold Classic Sunglasses",
        "frame": {"shape": "aviator", "color": ["gold"], "thickness": "thin"},
        "lenses": {"shape": "teardrop"},
    },
    # erroca_050: Ray-Ban RB3775M – aviator black glossy (not oval matte)
    "erroca_050": {
        "name": "Ray-Ban RB3775M Aviator Black Sunglasses",
        "frame": {"shape": "aviator", "finish": "glossy"},
        "lenses": {"shape": "teardrop"},
    },
    # erroca_051: Ray-Ban RB3774D – aviator gold G-15-green (not oval B-15-brown)
    "erroca_051": {
        "name": "Ray-Ban RB3774D Aviator Gold Sunglasses",
        "frame": {"shape": "aviator"},
        "lenses": {"color": ["G-15-green"], "shape": "teardrop"},
    },
    # erroca_052: Ray-Ban RB3770 Silver – aviator black gray (not oval silver blue)
    "erroca_052": {
        "name": "Ray-Ban RB3770 Aviator Black Sunglasses",
        "frame": {"shape": "aviator", "color": ["black"]},
        "lenses": {"color": ["gray"], "shape": "teardrop"},
    },
    # erroca_053: Ray-Ban RB3770 Gold – aviator silver blue (not oval gold G-15-green)
    "erroca_053": {
        "name": "Ray-Ban RB3770 Aviator Silver Blue Sunglasses",
        "frame": {"shape": "aviator", "color": ["silver"]},
        "lenses": {"color": ["blue"], "shape": "teardrop"},
    },
    # erroca_054: Ray-Ban RB3735 – semi-rimless black glossy mirrored-silver (not full-rim gunmetal matte gray)
    "erroca_054": {
        "frame": {"color": ["black"], "rim_type": "semi-rimless", "finish": "glossy"},
        "lenses": {"color": ["mirrored-silver"]},
    },
    # erroca_055: Ray-Ban RB3671CH – tortoiseshell acetate thick glossy gray (not black metal medium matte blue)
    "erroca_055": {
        "name": "Ray-Ban RB3671CH Tortoiseshell Chromance Sunglasses",
        "frame": {"color": ["tortoiseshell"], "material": "acetate", "thickness": "thick", "finish": "glossy"},
        "lenses": {"color": ["gray"]},
    },
    # erroca_056: Ray-Ban RB2232 – aviator gold metal thin brown (not rectangular havana acetate thick)
    "erroca_056": {
        "name": "Ray-Ban RB2232 Aviator Gold Sunglasses",
        "frame": {"shape": "aviator", "color": ["gold"], "material": "metal", "thickness": "thin"},
        "lenses": {"color": ["brown"], "shape": "teardrop"},
    },
    # erroca_057: Ray-Ban RB3025 – visual shows wayfarer tortoiseshell acetate thick blue (not aviator gold metal thin)
    "erroca_057": {
        "name": "Ray-Ban RB3025 Wayfarer Tortoiseshell Sunglasses",
        "frame": {"shape": "wayfarer", "color": ["tortoiseshell"], "material": "acetate", "thickness": "thick"},
        "lenses": {"color": ["blue"], "shape": "rectangular"},
    },
    # erroca_058: Ray-Ban RB2222 – square black thick gray (not round havana medium brown)
    "erroca_058": {
        "name": "Ray-Ban RB2222 Square Black Sunglasses",
        "frame": {"shape": "square", "color": ["black"], "thickness": "thick"},
        "lenses": {"color": ["gray"], "shape": "square"},
    },
    # erroca_059: Prada PRA16S Black – rectangular tortoiseshell (not square black)
    "erroca_059": {
        "name": "Prada PRA16S Rectangular Tortoiseshell Sunglasses",
        "frame": {"shape": "rectangular", "color": ["tortoiseshell"]},
        "lenses": {"shape": "rectangular"},
    },
    # erroca_060: Prada PRA08S – square green+black thick amber (not rectangular havana brown)
    "erroca_060": {
        "name": "Prada PRA08S Square Green Sunglasses",
        "frame": {"shape": "square", "color": ["green", "black"]},
        "lenses": {"color": ["amber"], "shape": "square"},
    },
    # erroca_061: Prada PRA16S Brown – wayfarer tortoiseshell (not square havana)
    "erroca_061": {
        "name": "Prada PRA16S Wayfarer Tortoiseshell Blue-Lens Sunglasses",
        "frame": {"shape": "wayfarer", "color": ["tortoiseshell"]},
        "lenses": {"shape": "rectangular"},
    },
    # erroca_062: Prada PRA17S – wayfarer tortoiseshell (not rectangular)
    "erroca_062": {
        "name": "Prada PRA17S Wayfarer Tortoiseshell Sunglasses",
        "frame": {"shape": "wayfarer"},
        "lenses": {"shape": "rectangular"},
    },
    # erroca_063: Prada PRA17S Polarized – wayfarer tortoiseshell blue lens (not rectangular brown)
    "erroca_063": {
        "name": "Prada PRA17S Wayfarer Tortoiseshell Polarized Sunglasses",
        "frame": {"shape": "wayfarer"},
        "lenses": {"color": ["blue"], "shape": "rectangular"},
    },
    # erroca_064: Tom Ford FT1178 – aviator black metal thin (not square acetate thick)
    "erroca_064": {
        "name": "Tom Ford FT1178 Aviator Black Sunglasses",
        "frame": {"shape": "aviator", "material": "metal", "thickness": "thin"},
        "lenses": {"shape": "teardrop"},
    },
    # erroca_065: Tom Ford FT1119 – browline tortoiseshell+rose-gold mixed semi-rimless medium blue lens
    "erroca_065": {
        "name": "Tom Ford FT1119 Browline Tortoiseshell Rose-Gold Sunglasses",
        "frame": {"shape": "browline", "color": ["tortoiseshell", "rose-gold"], "material": "mixed-metal-acetate",
                  "rim_type": "semi-rimless", "thickness": "medium"},
        "lenses": {"color": ["blue"], "shape": "rectangular"},
    },
    # erroca_066: Tom Ford FT1119 – browline tortoiseshell+rose-gold mixed semi-rimless medium clear lens
    "erroca_066": {
        "name": "Tom Ford FT1119 Browline Tortoiseshell Rose-Gold Sunglasses Light",
        "frame": {"shape": "browline", "color": ["tortoiseshell", "rose-gold"], "material": "mixed-metal-acetate",
                  "rim_type": "semi-rimless", "thickness": "medium"},
        "lenses": {"color": ["clear"], "shape": "rectangular"},
    },
    # erroca_067: Prada PRA05S – square tortoiseshell+black amber lenses
    "erroca_067": {
        "frame": {"color": ["tortoiseshell", "black"]},
        "lenses": {"color": ["amber"]},
    },
    # erroca_068: Ray-Ban RB0840S – wayfarer tortoiseshell brown lens (not square havana green)
    "erroca_068": {
        "name": "Ray-Ban RB0840S Wayfarer Tortoiseshell Sunglasses",
        "frame": {"shape": "wayfarer", "color": ["tortoiseshell"]},
        "lenses": {"color": ["brown"], "shape": "rectangular"},
    },
    # erroca_069: David Beckham DB1139 Yellow – round tortoiseshell acetate thick blue-gradient
    "erroca_069": {
        "name": "David Beckham DB1139 Round Tortoiseshell Sunglasses",
        "frame": {"shape": "round", "color": ["tortoiseshell"], "material": "acetate", "thickness": "thick"},
        "lenses": {"color": ["blue"], "shape": "round"},
    },
    # erroca_070: David Beckham DB1139 Clear – round silver metal thin brown
    "erroca_070": {
        "name": "David Beckham DB1139 Round Silver Sunglasses",
        "frame": {"shape": "round"},
        "lenses": {"color": ["brown"], "shape": "round"},
    },
    # erroca_071: David Beckham DB1007 – round black+gold mixed medium gradient-gray
    "erroca_071": {
        "name": "David Beckham DB1007 Round Black Gold Sunglasses",
        "frame": {"shape": "round", "color": ["black", "gold"], "material": "mixed-metal-acetate",
                  "thickness": "medium"},
        "lenses": {"color": ["gradient-gray"], "shape": "round"},
    },
    # erroca_072: David Beckham DB1160 – round black+havana mixed medium blue
    "erroca_072": {
        "name": "David Beckham DB1160 Round Black Brown Sunglasses",
        "frame": {"shape": "round", "color": ["black", "havana-brown"], "material": "mixed-metal-acetate"},
        "lenses": {"shape": "round"},
    },
    # erroca_073: Gucci SL738S – rectangular white+silver mixed semi-rimless medium mirrored-silver
    "erroca_073": {
        "name": "Gucci SL738S Rectangular Silver Sunglasses",
        "frame": {"shape": "rectangular", "color": ["white", "silver"], "material": "mixed-metal-acetate",
                  "rim_type": "semi-rimless", "thickness": "medium"},
        "lenses": {"color": ["mirrored-silver"], "shape": "rectangular"},
    },
    # erroca_074: Balenciaga BB0328S – rectangular green acetate thick green lenses (not aviator gold metal)
    "erroca_074": {
        "name": "Balenciaga BB0328S Rectangular Green Sunglasses",
        "frame": {"shape": "rectangular", "color": ["green"], "material": "acetate", "thickness": "thick"},
        "lenses": {"color": ["green"], "shape": "rectangular"},
    },
}


def apply_corrections(catalog):
    products = {p["id"]: p for p in catalog["products"]}
    count = 0
    for pid, fixes in CORRECTIONS.items():
        if pid not in products:
            print(f"  SKIP (not found): {pid}")
            continue
        p = products[pid]
        changed = []
        if "name" in fixes:
            if p["name"] != fixes["name"]:
                p["name"] = fixes["name"]
                changed.append("name")
        for section in ("frame", "lenses"):
            if section in fixes:
                for field, val in fixes[section].items():
                    if p["tags"][section].get(field) != val:
                        p["tags"][section][field] = val
                        changed.append(f"{section}.{field}")
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

    print(f"\nDone - {changed} products updated.")


if __name__ == "__main__":
    main()
