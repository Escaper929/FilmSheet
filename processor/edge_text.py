# -*- coding: utf-8 -*-
"""Pure function for edge text generation.

Independent of FilmProcessor state — takes raw strings, returns structured edge text.
Can be called from any backend (desktop, web, mobile).
"""

# Chinese brand → English mapping
BRAND_MAP: dict[str, str] = {
    '柯达': 'KODAK', 'Kodak': 'KODAK', 'KODAK': 'KODAK',
    '富士': 'FUJIFILM', 'Fujifilm': 'FUJIFILM', 'FUJIFILM': 'FUJIFILM',
    '柯尼卡': 'KONICA', 'Konica': 'KONICA', 'KONICA': 'KONICA',
    '伊尔福': 'ILFORD', 'Ilford': 'ILFORD', 'ILFORD': 'ILFORD',
    '阿克发': 'AGFA', 'Agfa': 'AGFA', 'AGFA': 'AGFA',
    '乐凯': 'LUCKY', 'Lucky': 'LUCKY', 'LUCKY': 'LUCKY',
    '波尔': 'PORST', 'Porst': 'PORST', 'PORST': 'PORST',
    '斯达法': 'STADIA', 'Stadia': 'STADIA', 'STADIA': 'STADIA',
    '哈苏': 'HASSELBLAD', 'Hasselblad': 'HASSELBLAD',
}

# Chinese film type → English mapping (partial match)
TYPE_MAP: dict[str, str] = {
    'Portra 160': 'Portra 160', 'Portra 160nc': 'Portra 160NC',
    'Portra 400': 'Portra 400', 'Portra 400nc': 'Portra 400NC',
    'Portra 800': 'Portra 800',
    'Ektar 100': 'Ektar 100',
    'Gold 200': 'Gold 200', 'Ultramax 200': 'Ultramax 200',
    'Ultramax 400': 'Ultramax 400', 'Supra 200': 'Supra 200',
    'Supra 400': 'Supra 400',
    'ColorPlus 200': 'ColorPlus 200',
    'Tri-X 400': 'Tri-X 400', 'T-Max 100': 'T-Max 100',
    'T-Max 400': 'T-Max 400', 'Panatomic-X': 'Panatomic-X',
    'Pro 400H': 'Pro 400H',
    'Velvia 50': 'Velvia 50', 'Provia 100F': 'Provia 100F',
    'Astia 100F': 'Astia 100F', 'Eterna 500T': 'Eterna 500T',
    'Vision3 500T': 'Vision3 500T', 'Vision3 250D': 'Vision3 250D',
    'Vision3 50D': 'Vision3 50D',
    'Superia 200': 'Superia 200', 'Superia 400': 'Superia 400',
    'Superia 800': 'Superia 800',
    'CineVision 500T': 'CineVision 500T',
    '5207': '5207', '5219': '5219', '5294': '5294', '5203': '5203',
    '5222': '5222',
    '横轴': '横轴', '纵轴': '纵轴',
}

# Cinema/motion picture keywords → Eastman brand
CINEMA_KEYWORDS = ['VISION3', 'CINEVISION', 'EKTACHROME', 'MOTION PICTURE',
                   '5207', '5219', '5294', '5203', '5222']

# Brand prefixes to strip when converting to Eastman
BRANCH_PREFIXES = ['柯达', 'Kodak', 'KODAK', '富士', 'FUJIFILM']


def generate_edge_text(info_film: str, custom_edge_text: str = "") -> dict:
    """Generate structured edge text from raw film info.

    This is a pure function — no side effects, no config object needed.

    Args:
        info_film: Raw film info from user (e.g. "柯达 Portra 400").
        custom_edge_text: Custom edge text if user typed something.

    Returns:
        Dict with keys: brand, film_type, lot_code, direction, custom.
    """
    if custom_edge_text and custom_edge_text.strip():
        return {
            "brand": custom_edge_text.strip(),
            "film_type": "",
            "lot_code": "",
            "direction": "◀",
            "custom": True,
        }

    raw = info_film.strip()
    upper_film = raw.upper()

    # Cinema/motion picture → Eastman
    if any(kw in upper_film for kw in CINEMA_KEYWORDS):
        brand = "EASTMAN"
        # Strip brand prefix to avoid duplication
        film_type = _strip_brand_prefix(raw)
    else:
        parts = raw.split()
        raw_brand = parts[0] if parts else ""
        raw_film_type = ' '.join(parts[1:]) if len(parts) > 1 else ""

        brand = BRAND_MAP.get(raw_brand, raw_brand.upper())
        film_type = _map_film_type(raw_film_type)

    # Consistent lot code from brand + film type
    lot_hash = hash(brand + film_type) & 0xFFFFFF
    lot_code = f"E{lot_hash // 10000 % 10} {lot_hash % 10000:04d}"

    return {
        "brand": brand,
        "film_type": film_type,
        "lot_code": lot_code,
        "direction": "◀",
        "custom": False,
    }


def _strip_brand_prefix(text: str) -> str:
    """Strip brand prefix from film info."""
    for prefix in BRANCH_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return text


def _map_film_type(raw: str) -> str:
    """Map Chinese film type to English."""
    for cn, en in TYPE_MAP.items():
        if cn in raw:
            return en
    return raw
