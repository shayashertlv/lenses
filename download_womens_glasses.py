"""Download women's eyeglasses and sunglasses images from Erroca."""
import urllib.request
import os
from pathlib import Path

IMAGES_DIR = Path(r"C:\Users\Shay\PycharmProjects\lenses\lenses\catalog\images")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://erroca.co.il/",
}

EYEGLASSES = [
    ("erroca_womens_eyeglasses_001.webp", "https://erroca.co.il/wp-content/uploads/2026/01/0VO5603__W44__P21__shad__qt-510x340.webp"),
    ("erroca_womens_eyeglasses_002.webp", "https://erroca.co.il/wp-content/uploads/2026/01/0VO5518__2942__P21__shad__qt-510x340.webp"),
    ("erroca_womens_eyeglasses_003.webp", "https://erroca.co.il/wp-content/uploads/2026/01/0VO5563__W44__P21__shad__qt-510x340.webp"),
    ("erroca_womens_eyeglasses_004.webp", "https://erroca.co.il/wp-content/uploads/2026/01/0VO4333__848__P21__shad__qt-510x340.webp"),
    ("erroca_womens_eyeglasses_005.webp", "https://erroca.co.il/wp-content/uploads/2026/01/0VO5239__2735__P21__shad__qt-510x340.webp"),
    ("erroca_womens_eyeglasses_006.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8056262146798-510x340.webp"),
    ("erroca_womens_eyeglasses_007.webp", "https://erroca.co.il/wp-content/uploads/2026/01/0VO5632B__2990__P21__shad__qt-510x340.webp"),
    ("erroca_womens_eyeglasses_008.webp", "https://erroca.co.il/wp-content/uploads/2026/01/0VO4333__5138__P21__shad__qt-510x340.webp"),
    ("erroca_womens_eyeglasses_009.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8056262143506-510x340.webp"),
    ("erroca_womens_eyeglasses_010.webp", "https://erroca.co.il/wp-content/uploads/2026/01/0VO4334__5210__P21__shad__qt-510x340.webp"),
    ("erroca_womens_eyeglasses_011.webp", "https://erroca.co.il/wp-content/uploads/2026/01/0RX6513__2904__P21__shad__qt-510x340.webp"),
    ("erroca_womens_eyeglasses_012.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8056597745048.webp"),
    ("erroca_womens_eyeglasses_013.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8056597633048-510x340.webp"),
    ("erroca_womens_eyeglasses_014.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8056597876988-1-510x340.webp"),
    ("erroca_womens_eyeglasses_015.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8056597618809-1-510x340.webp"),
    ("erroca_womens_eyeglasses_016.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8056597876964-510x340.webp"),
    ("erroca_womens_eyeglasses_017.webp", "https://erroca.co.il/wp-content/uploads/2026/01/0VE3367U__GB1__P21__shad__qt-510x340.webp"),
    ("erroca_womens_eyeglasses_018.webp", "https://erroca.co.il/wp-content/uploads/2026/01/0VE1247__1252__P21__shad__qt-510x340.webp"),
    ("erroca_womens_eyeglasses_019.webp", "https://erroca.co.il/wp-content/uploads/2026/01/0VE1298__1252__P21__shad__qt-510x340.webp"),
    ("erroca_womens_eyeglasses_020.webp", "https://erroca.co.il/wp-content/uploads/2026/01/0DG1346__1311__P21__shad__qt-510x340.webp"),
    ("erroca_womens_eyeglasses_021.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8056262398142-1-510x340.webp"),
    ("erroca_womens_eyeglasses_022.webp", "https://erroca.co.il/wp-content/uploads/2026/01/0VE3353__108__P21__shad__qt-510x340.webp"),
    ("erroca_womens_eyeglasses_023.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8056597340786-510x340.webp"),
    ("erroca_womens_eyeglasses_024.webp", "https://erroca.co.il/wp-content/uploads/2026/01/0VE3347__5435__P21__shad__qt-510x340.webp"),
    ("erroca_womens_eyeglasses_025.webp", "https://erroca.co.il/wp-content/uploads/2026/01/0DG3360__3246__P21__shad__qt-510x340.webp"),
    ("erroca_womens_eyeglasses_026.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8053672782554-510x340.webp"),
    ("erroca_womens_eyeglasses_027.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8053672667608-1-510x340.webp"),
    ("erroca_womens_eyeglasses_028.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8053672496901-510x340.webp"),
    ("erroca_womens_eyeglasses_029.webp", "https://erroca.co.il/wp-content/uploads/2026/01/0RX5441__2000__P21__shad__qt-510x340.webp"),
    ("erroca_womens_eyeglasses_030.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8056262410899-1-510x340.webp"),
    ("erroca_womens_eyeglasses_031.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8056262398418-510x340.webp"),
    ("erroca_womens_eyeglasses_032.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8056262143339-1-510x340.webp"),
    ("erroca_womens_eyeglasses_033.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8056262143315-1-510x340.webp"),
    ("erroca_womens_eyeglasses_034.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8056597669016-2-510x340.webp"),
    ("erroca_womens_eyeglasses_035.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8056262366905-1-510x340.webp"),
    ("erroca_womens_eyeglasses_036.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8056262145265-510x340.webp"),
    ("erroca_womens_eyeglasses_037.webp", "https://erroca.co.il/wp-content/uploads/2026/01/8056262398166-1-510x340.webp"),
]

SUNGLASSES = [
    ("erroca_womens_sunglasses_001.webp", "https://erroca.co.il/wp-content/uploads/2025/11/8056262665244-510x340.webp"),
    ("erroca_womens_sunglasses_002.webp", "https://erroca.co.il/wp-content/uploads/2025/11/8056262543412-510x340.webp"),
    ("erroca_womens_sunglasses_003.webp", "https://erroca.co.il/wp-content/uploads/2025/11/8056262582176-510x340.webp"),
    ("erroca_womens_sunglasses_004.webp", "https://erroca.co.il/wp-content/uploads/2025/11/8056262543429-510x340.webp"),
    ("erroca_womens_sunglasses_005.webp", "https://erroca.co.il/wp-content/uploads/2025/11/8056262661307-510x340.webp"),
    ("erroca_womens_sunglasses_006.webp", "https://erroca.co.il/wp-content/uploads/2025/11/8056262569191-510x340.webp"),
    ("erroca_womens_sunglasses_007.webp", "https://erroca.co.il/wp-content/uploads/2025/11/8056262581629-510x340.webp"),
    ("erroca_womens_sunglasses_008.webp", "https://erroca.co.il/wp-content/uploads/2025/11/8056262551868-510x340.webp"),
    ("erroca_womens_sunglasses_009.webp", "https://erroca.co.il/wp-content/uploads/2025/11/8056262661314-510x340.webp"),
    ("erroca_womens_sunglasses_010.webp", "https://erroca.co.il/wp-content/uploads/2025/11/8056262563311-510x340.webp"),
    ("erroca_womens_sunglasses_011.webp", "https://erroca.co.il/wp-content/uploads/2025/11/8056262665251-510x340.webp"),
    ("erroca_womens_sunglasses_012.webp", "https://erroca.co.il/wp-content/uploads/2025/11/8056262556870-510x340.webp"),
    ("erroca_womens_sunglasses_013.webp", "https://erroca.co.il/wp-content/uploads/2025/11/8056262416341-510x340.webp"),
    ("erroca_womens_sunglasses_014.webp", "https://erroca.co.il/wp-content/uploads/2025/11/0888392677242-510x340.webp"),
    ("erroca_womens_sunglasses_015.webp", "https://erroca.co.il/wp-content/uploads/2025/11/08883926232701-510x340.webp"),
    ("erroca_womens_sunglasses_016.webp", "https://erroca.co.il/wp-content/uploads/2025/11/8056262578360-510x340.webp"),
    ("erroca_womens_sunglasses_017.webp", "https://erroca.co.il/wp-content/uploads/2025/11/0888392677273-510x340.webp"),
    ("erroca_womens_sunglasses_018.webp", "https://erroca.co.il/wp-content/uploads/2025/11/8056262510421-510x340.webp"),
    ("erroca_womens_sunglasses_019.webp", "https://erroca.co.il/wp-content/uploads/2025/10/8056262668030-510x340.webp"),
    ("erroca_womens_sunglasses_020.webp", "https://erroca.co.il/wp-content/uploads/2025/10/8056262673072-510x340.webp"),
    ("erroca_womens_sunglasses_021.webp", "https://erroca.co.il/wp-content/uploads/2025/10/8056262562178-510x340.webp"),
    ("erroca_womens_sunglasses_022.webp", "https://erroca.co.il/wp-content/uploads/2025/10/8056262581032-510x340.webp"),
    ("erroca_womens_sunglasses_023.webp", "https://erroca.co.il/wp-content/uploads/2025/10/8056262560884-510x340.webp"),
    ("erroca_womens_sunglasses_024.webp", "https://erroca.co.il/wp-content/uploads/2025/10/8056262561447-510x340.webp"),
    ("erroca_womens_sunglasses_025.webp", "https://erroca.co.il/wp-content/uploads/2025/10/0888392588746-510x340.webp"),
    ("erroca_womens_sunglasses_026.webp", "https://erroca.co.il/wp-content/uploads/2025/08/8056262349915-510x340.webp"),
    ("erroca_womens_sunglasses_027.webp", "https://erroca.co.il/wp-content/uploads/2025/08/8056262511442-510x340.webp"),
    ("erroca_womens_sunglasses_028.webp", "https://erroca.co.il/wp-content/uploads/2024/04/8056262150511-510x340.jpg.webp"),
    ("erroca_womens_sunglasses_029.webp", "https://erroca.co.il/wp-content/uploads/2025/04/8056262019900-510x340.webp"),
    ("erroca_womens_sunglasses_030.webp", "https://erroca.co.il/wp-content/uploads/2025/07/8056262019894-510x340.webp"),
    ("erroca_womens_sunglasses_031.webp", "https://erroca.co.il/wp-content/uploads/2025/07/8056262019856-510x340.webp"),
    ("erroca_womens_sunglasses_032.webp", "https://erroca.co.il/wp-content/uploads/2025/07/8056262045961-510x340.webp"),
    ("erroca_womens_sunglasses_033.webp", "https://erroca.co.il/wp-content/uploads/2025/07/8056262048979-510x340.webp"),
    ("erroca_womens_sunglasses_034.webp", "https://erroca.co.il/wp-content/uploads/2025/07/8056597936491-510x340.webp"),
    ("erroca_womens_sunglasses_035.webp", "https://erroca.co.il/wp-content/uploads/2025/07/8056597936453-510x340.webp"),
    ("erroca_womens_sunglasses_036.webp", "https://erroca.co.il/wp-content/uploads/2025/07/8056262458204-510x340.webp"),
    ("erroca_womens_sunglasses_037.webp", "https://erroca.co.il/wp-content/uploads/2025/06/889652561141-510x340.webp"),
]


def download(filename, url):
    dest = IMAGES_DIR / filename
    if dest.exists():
        print(f"  SKIP (exists): {filename}")
        return True
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        with open(dest, "wb") as f:
            f.write(data)
        print(f"  OK  {filename}  ({len(data):,} bytes)")
        return True
    except Exception as e:
        print(f"  FAIL {filename}: {e}")
        return False


def main():
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    failed = []

    print(f"=== Downloading {len(EYEGLASSES)} women's eyeglasses ===")
    for fname, url in EYEGLASSES:
        if not download(fname, url):
            failed.append((fname, url))

    print(f"\n=== Downloading {len(SUNGLASSES)} women's sunglasses ===")
    for fname, url in SUNGLASSES:
        if not download(fname, url):
            failed.append((fname, url))

    print(f"\n=== Done: {len(EYEGLASSES) + len(SUNGLASSES) - len(failed)} downloaded, {len(failed)} failed ===")
    if failed:
        print("Failed:")
        for fname, url in failed:
            print(f"  {fname}  {url}")


if __name__ == "__main__":
    main()
