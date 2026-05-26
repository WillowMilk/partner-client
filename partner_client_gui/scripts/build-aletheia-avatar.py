#!/usr/bin/env python3
"""
Crop Aletheia's hero image into a circular avatar source for the GUI.

Aletheia's spec (2026-05-26 consultation #3):
  - "Tight crop on the gaze and core of composition"
  - Square 1:1, will become circular via CSS
  - Source: /Users/willow/Aletheia/Memory/Media/Aletheia_Hero_Image.png (1672x941)
  - Focal points: her head (upper-third) + the heart-point of light (center-vertical
    just below head); these form the "gaze + core" Aletheia named
  - Destination: partner_client_gui/public/avatars/aletheia.png
  - Output size: 512x512 (renders crisply at any size 24-128px on Retina)

The crop region is chosen by analyzing the composition:
  - Figure is centered roughly at X=820-870 (image center is 836)
  - Head extends from Y~50 to Y~220
  - Heart-point of light is around Y~280-340
  - To capture "gaze + core" (head + heart): crop spans roughly Y=30 to Y=550

This gives a 520x520 square crop centered at (835, 290). Resized to 512x512.
"""

from PIL import Image
from pathlib import Path

SRC = Path("/Users/willow/Aletheia/Memory/Media/Aletheia_Hero_Image.png")
DST_DIR = Path("/Users/willow/Code/partner-client/partner_client_gui/public/avatars")
DST = DST_DIR / "aletheia.png"

# Crop region — captures head + heart-point of light, Aletheia's "gaze + core"
CROP_CENTER_X = 835
CROP_CENTER_Y = 290
CROP_SIZE = 520  # square side length in source-image pixels

# Output size (high-DPI; renders crisply at any display size)
OUTPUT_SIZE = 512


def main():
    DST_DIR.mkdir(parents=True, exist_ok=True)

    img = Image.open(SRC)
    print(f"Source: {SRC.name} — {img.size[0]}x{img.size[1]} {img.mode}")

    # Compute bounding box for crop
    half = CROP_SIZE // 2
    box = (
        CROP_CENTER_X - half,
        CROP_CENTER_Y - half,
        CROP_CENTER_X + half,
        CROP_CENTER_Y + half,
    )
    print(f"Crop box: {box} (center {CROP_CENTER_X},{CROP_CENTER_Y}, size {CROP_SIZE}x{CROP_SIZE})")

    # Crop + resize
    cropped = img.crop(box)
    resized = cropped.resize((OUTPUT_SIZE, OUTPUT_SIZE), Image.LANCZOS)

    # Convert to RGBA so we can preserve transparency if we ever decide to mask in PIL
    if resized.mode != "RGBA":
        resized = resized.convert("RGBA")

    resized.save(DST, "PNG", optimize=True)
    file_size = DST.stat().st_size
    print(f"Saved: {DST} — {resized.size[0]}x{resized.size[1]} ({file_size:,} bytes)")

    # Also save a small preview for quick inspection
    preview = resized.resize((128, 128), Image.LANCZOS)
    preview_path = DST_DIR / "aletheia_preview_128.png"
    preview.save(preview_path, "PNG", optimize=True)
    print(f"Preview: {preview_path} — 128x128 ({preview_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
