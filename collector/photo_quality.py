from __future__ import annotations

import math

import fitz

VALIDATION_VERSION = 3


def _rgb_pixmap(pix: fitz.Pixmap) -> fitz.Pixmap:
    if pix.n == 3 and not pix.alpha:
        return pix
    return fitz.Pixmap(fitz.csRGB, pix)


def analyze_pixmap(pix: fitz.Pixmap, sample_limit: int = 6000) -> dict:
    rgb = _rgb_pixmap(pix)
    pixels = max(1, rgb.width * rgb.height)
    step = max(1, pixels // max(500, sample_limit))
    samples = rgb.samples
    lumas = []
    unique = set()
    white = 0
    near_white = 0
    dark = 0
    mid = 0
    colored = 0
    gray = 0
    count = 0

    for i in range(0, pixels, step):
        j = i * 3
        if j + 2 >= len(samples):
            break
        r, g, b = samples[j], samples[j + 1], samples[j + 2]
        y = (299 * r + 587 * g + 114 * b) // 1000
        spread = max(r, g, b) - min(r, g, b)
        lumas.append(y)
        unique.add((r // 16, g // 16, b // 16))
        if r > 242 and g > 242 and b > 242:
            white += 1
        if r > 226 and g > 226 and b > 226:
            near_white += 1
        if y < 70:
            dark += 1
        if 45 < y < 220:
            mid += 1
        if spread >= 22:
            colored += 1
        if spread <= 8:
            gray += 1
        count += 1

    if not count:
        return {
            "width": rgb.width,
            "height": rgb.height,
            "pixels": pixels,
            "count": 0,
            "unique_bins": 0,
            "variance": 0.0,
            "white_ratio": 1.0,
            "near_white_ratio": 1.0,
            "dark_ratio": 0.0,
            "mid_ratio": 0.0,
            "color_ratio": 0.0,
            "gray_ratio": 1.0,
        }

    mean = sum(lumas) / len(lumas)
    variance = sum((v - mean) ** 2 for v in lumas) / len(lumas)
    return {
        "width": rgb.width,
        "height": rgb.height,
        "pixels": pixels,
        "count": count,
        "unique_bins": len(unique),
        "variance": variance,
        "white_ratio": white / count,
        "near_white_ratio": near_white / count,
        "dark_ratio": dark / count,
        "mid_ratio": mid / count,
        "color_ratio": colored / count,
        "gray_ratio": gray / count,
    }


def photo_score(metrics: dict) -> float | None:
    width = int(metrics.get("width") or 0)
    height = int(metrics.get("height") or 0)
    pixels = int(metrics.get("pixels") or 0)
    if width < 360 or height < 240 or pixels < 140_000:
        return None

    ratio = width / max(1, height)
    if ratio < 0.32 or ratio > 3.4:
        return None

    unique = int(metrics.get("unique_bins") or 0)
    variance = float(metrics.get("variance") or 0.0)
    white = float(metrics.get("white_ratio") or 0.0)
    near_white = float(metrics.get("near_white_ratio") or 0.0)
    dark = float(metrics.get("dark_ratio") or 0.0)
    mid = float(metrics.get("mid_ratio") or 0.0)
    color = float(metrics.get("color_ratio") or 0.0)

    # Typical text pages / scanned notices: mostly white paper plus dark glyphs.
    if unique < 42 or variance < 220:
        return None
    if white > 0.72:
        return None
    if near_white > 0.82 and mid < 0.28:
        return None
    if white > 0.56 and mid < 0.30 and color < 0.16:
        return None
    if near_white > 0.72 and dark < 0.18 and mid < 0.24 and color < 0.12:
        return None

    return (
        math.log10(max(10, pixels)) * 105
        + min(variance, 6500) / 22
        + unique * 0.9
        + mid * 125
        + color * 55
        - white * 230
        - near_white * 55
    )


def looks_like_document_scan(
    metrics: dict,
    *,
    coverage: float = 0.0,
    page_text_chars: int = 0,
    page_ratio_match: bool = False,
) -> bool:
    white = float(metrics.get("white_ratio") or 0.0)
    near_white = float(metrics.get("near_white_ratio") or 0.0)
    mid = float(metrics.get("mid_ratio") or 0.0)
    color = float(metrics.get("color_ratio") or 0.0)
    dark = float(metrics.get("dark_ratio") or 0.0)

    if page_text_chars >= 420 and coverage >= 0.28:
        return True
    if page_text_chars >= 180 and coverage >= 0.58:
        return True
    if coverage >= 0.68 and page_ratio_match and white > 0.48 and mid < 0.44:
        return True
    if coverage >= 0.62 and near_white > 0.70 and color < 0.16 and dark < 0.22:
        return True
    return False


def quality_payload(metrics: dict, score: float, *, mode: str, page: int | None = None) -> dict:
    return {
        "photo_validation_version": VALIDATION_VERSION,
        "photo_quality_pass": True,
        "photo_quality_score": round(float(score), 2),
        "photo_quality_mode": mode,
        "photo_quality_page": page,
        "photo_quality_metrics": {
            "white_ratio": round(float(metrics.get("white_ratio") or 0.0), 4),
            "mid_ratio": round(float(metrics.get("mid_ratio") or 0.0), 4),
            "color_ratio": round(float(metrics.get("color_ratio") or 0.0), 4),
            "variance": round(float(metrics.get("variance") or 0.0), 1),
            "unique_bins": int(metrics.get("unique_bins") or 0),
        },
    }
