"""Partner Client app icon — drawn in the family's own design tokens.

Paper field (#FAFAF7, the GUI's window body), Aletheia's gold diamond
(#D4A847 — 'North Star, not brand') with a soft glow, and a single ember
hearth-line (#c45a20, the IR site's accent) beneath it: the flame's mark
resting on the hearth. Big-Sur rounded square with standard margins.
"""
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1024
MARGIN = 100           # macOS icon grid: content within ~824px
RADIUS = 185           # Big Sur corner radius at 1024

out_dir = Path(__file__).parent / "icon-build"
out_dir.mkdir(exist_ok=True)

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# --- Paper field with the gentlest vertical warmth -------------------------
field = Image.new("RGBA", (S, S), (0, 0, 0, 0))
fd = ImageDraw.Draw(field)
top = (250, 250, 247, 255)      # #FAFAF7
bottom = (242, 241, 237, 255)   # #F2F1ED (the sidebar paper)
box = (MARGIN, MARGIN, S - MARGIN, S - MARGIN)
for y in range(box[1], box[3]):
    t = (y - box[1]) / (box[3] - box[1])
    r = int(top[0] + (bottom[0] - top[0]) * t)
    g = int(top[1] + (bottom[1] - top[1]) * t)
    b = int(top[2] + (bottom[2] - top[2]) * t)
    fd.line([(box[0], y), (box[2], y)], fill=(r, g, b, 255))
# Round the corners via mask
mask = Image.new("L", (S, S), 0)
md = ImageDraw.Draw(mask)
md.rounded_rectangle(box, radius=RADIUS, fill=255)
img.paste(field, (0, 0), mask)

# Hairline border, barely there
d.rounded_rectangle(box, radius=RADIUS, outline=(0, 0, 0, 20), width=3)

cx, cy = S // 2, S // 2 - 30

# --- Gold glow behind the diamond ------------------------------------------
glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
gd = ImageDraw.Draw(glow)
gd.ellipse((cx - 240, cy - 240, cx + 240, cy + 240), fill=(212, 168, 71, 60))
glow = glow.filter(ImageFilter.GaussianBlur(80))
# Clip the glow to the card so the dock silhouette stays crisp.
clipped_glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
clipped_glow.paste(glow, (0, 0), mask)
img = Image.alpha_composite(img, clipped_glow)

# --- The diamond (Aletheia's Fragment; the family's North-Star mark) --------
d = ImageDraw.Draw(img)
r_d = 190
diamond = [(cx, cy - r_d), (cx + r_d, cy), (cx, cy + r_d), (cx - r_d, cy)]
d.polygon(diamond, fill=(212, 168, 71, 255))
# Inner facet: a lighter gold upper-left face for depth
d.polygon([(cx, cy - r_d), (cx + r_d, cy), (cx, cy)], fill=(225, 187, 100, 255))
d.polygon([(cx, cy - r_d), (cx - r_d, cy), (cx, cy)], fill=(232, 199, 122, 255))

# --- The hearth-line: one ember arc beneath -----------------------------------
arc_y = cy + r_d + 95
d.arc((cx - 210, arc_y - 60, cx + 210, arc_y + 60), start=200, end=340,
      fill=(196, 90, 32, 235), width=26)

img.save(out_dir / "icon_1024.png")

# --- .iconset → .icns --------------------------------------------------------
iconset = out_dir / "PartnerClient.iconset"
iconset.mkdir(exist_ok=True)
sizes = [16, 32, 128, 256, 512]
for s in sizes:
    img.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
    img.resize((s * 2, s * 2), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out_dir / "PartnerClient.icns")], check=True)
print("icns written:", out_dir / "PartnerClient.icns")
