"""Production-ready regional measurements; copied verbatim into the proposed look port."""
import numpy as np

from glasses3d.agent.tools.look import colour_stats


def lens_region_stats(photo, drawing, interior):
    """Image-left/centre/right thirds of the existing eroded mask's horizontal extent."""
    columns = np.flatnonzero(np.any(interior, axis=0))
    regions = []
    x = np.arange(interior.shape[1])
    for index, name in enumerate(("left", "centre", "right")):
        mask = np.zeros_like(interior, dtype=bool)
        bounds = None
        if len(columns):
            left, width = int(columns[0]), int(columns[-1] - columns[0] + 1)
            # Integer comparisons partition pixel columns exactly, including widths not divisible by 3.
            mask = interior & (3 * (x - left) >= index * width) & (3 * (x - left) < (index + 1) * width)
            bounds = [left + index * width / 3, left + (index + 1) * width / 3]
        stats = {"photo": colour_stats(photo, mask), "drawing": colour_stats(drawing, mask)}
        for value in stats.values():
            if value["status"] == "unavailable":
                value["reason"] = "no interior pixels in this horizontal third"
        regions.append({"region": name, "x_extent_px": bounds, **stats})
    return regions


def regional_lens_colour_lines(rows, whole_lines):
    """Append factual regional medians from the full report; no scores or judgement."""
    lines = []
    for row, whole in zip(rows, whole_lines, strict=True):
        regions = row["colours"]["lens"]["regions"]

        def values(source, key):
            formatted = []
            for region in regions:
                value = region[source]
                if value["status"] != "measured":
                    formatted.append("unavailable")
                elif key == "median_lightness_Lstar":
                    formatted.append(f"{value[key]:.1f}")
                else:
                    formatted.append(",".join(f"{component:g}" for component in value[key]))
            return " / ".join(formatted)

        lines.append(whole + "\n" + f"{row['ref']} lens regions left / centre / right: "
                     f"AR L* {values('drawing', 'median_lightness_Lstar')}, "
                     f"RGB (sRGB 8-bit) {values('drawing', 'median_srgb_8bit')} vs "
                     f"photo L* {values('photo', 'median_lightness_Lstar')}, "
                     f"RGB (sRGB 8-bit) {values('photo', 'median_srgb_8bit')}")
    return lines
