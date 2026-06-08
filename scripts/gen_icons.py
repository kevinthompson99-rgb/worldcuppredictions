"""Generator for the PWA app icons: a classic black-and-white hex-panel football.

No SVG/raster toolchain is available in this env (no Pillow, cairosvg, rsvg-convert,
Inkscape, ImageMagick, headless Chrome, ...), so this builds the icon as actual SVG
polygon geometry (written to icon.svg for reference/editing) and then rasterizes that
*same* geometry to PNG with a small hand-rolled renderer (point-in-polygon fill +
a minimal PNG encoder over zlib/struct — no deps). SVG and PNG are therefore two
views of one shape list, not independently drawn.

Panel layout: a pointy-top hexagonal grid over the ball, axial-coordinate cells
selected by (q - r) % 3 == 0 rendered as the dark "pentagon" panels. That residue
rule is what gives a real truncated-icosahedron its defining property — picking it
out guarantees no two dark panels are ever adjacent, each fully ringed by light
panels — exactly the classic football look, without needing to hand-place 32 panels.

Run with `python3 scripts/gen_icons.py`; output lands in app/static/icons/.
"""
import math
import os
import struct
import zlib

BG = (0x12, 0x14, 0x17)     # #121417 - app background, kept behind the ball
WHITE = (0xf5, 0xf5, 0xf0)  # ball's light panels
BLACK = (0x16, 0x18, 0x1b)  # ball's dark panels + outline (near-black, not pure)


# ---- Geometry: build the hex-panel ball as a list of (polygon, fill) ----

def hex_corners(cx, cy, size):
    """Pointy-top regular hexagon vertices, going clockwise from the top corner."""
    return [
        (cx + size * math.sin(math.radians(60 * i)), cy - size * math.cos(math.radians(60 * i)))
        for i in range(6)
    ]


def axial_to_pixel(q, r, size):
    return (size * math.sqrt(3) * (q + r / 2), size * 1.5 * r)


def build_ball(radius, hex_size):
    """Returns (panels, outline_radius) where panels is [(points, fill), ...].

    Covers a square of cells wide enough to blanket the circle, keeping any cell
    whose centre falls within `radius + hex_size` (so edge panels still tile up
    to the rim — the rasterizer clips the final result to the circle anyway).
    """
    panels = []
    cell_range = int(radius / (hex_size * math.sqrt(3))) + 2
    for q in range(-cell_range, cell_range + 1):
        for r in range(-cell_range, cell_range + 1):
            cx, cy = axial_to_pixel(q, r, hex_size)
            if math.hypot(cx, cy) > radius + hex_size:
                continue
            fill = BLACK if (q - r) % 3 == 0 else WHITE
            panels.append((hex_corners(cx, cy, hex_size), fill))
    return panels


# ---- SVG: written for reference/editing — same polygons, real <svg> markup ----

def render_svg(size, panels, ball_radius, ring_width):
    cx = cy = size / 2
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">',
        f'  <rect width="{size}" height="{size}" fill="rgb{BG}"/>',
        f'  <defs><clipPath id="ball"><circle cx="{cx}" cy="{cy}" r="{ball_radius}"/></clipPath></defs>',
        f'  <circle cx="{cx}" cy="{cy}" r="{ball_radius + ring_width / 2}" '
        f'fill="none" stroke="rgb{BLACK}" stroke-width="{ring_width}"/>',
        f'  <circle cx="{cx}" cy="{cy}" r="{ball_radius}" fill="rgb{WHITE}"/>',
        '  <g clip-path="url(#ball)">',
    ]
    for points, fill in panels:
        pts = " ".join(f"{cx + x:.2f},{cy + y:.2f}" for x, y in points)
        parts.append(f'    <polygon points="{pts}" fill="rgb{fill}"/>')
    parts.append("  </g>")
    parts.append("</svg>")
    return "\n".join(parts)


# ---- Rasterizer: fills the same polygon list into a pixel grid ----

def point_in_polygon(x, y, points):
    inside = False
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            x_intersect = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_intersect:
                inside = not inside
    return inside


def make_pixel_fn(size, panels, ball_radius, ring_width):
    cx = cy = size / 2

    def pixel(x, y):
        px, py = x + 0.5 - cx, y + 0.5 - cy
        dist = math.hypot(px, py)
        if dist > ball_radius + ring_width:
            return BG
        if dist > ball_radius:
            return BLACK
        for points, fill in panels:
            if point_in_polygon(px, py, points):
                return fill
        return WHITE  # uncovered sliver right at the rim - treat as ball surface

    return pixel


# ---- Minimal PNG encoder (zlib + struct, no Pillow) ----

def write_png(path, size, pixel_fn):
    rows = []
    for y in range(size):
        row = bytearray([0])  # filter type 0 (none) per scanline
        for x in range(size):
            row.extend(pixel_fn(x, y))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    idat = zlib.compress(raw, 9)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


if __name__ == "__main__":
    out_dir = os.path.join(os.path.dirname(__file__), "..", "app", "static", "icons")
    os.makedirs(out_dir, exist_ok=True)

    REFERENCE_SIZE = 512
    ball_radius = REFERENCE_SIZE * 0.40
    ring_width = REFERENCE_SIZE * 0.012
    hex_size = ball_radius * 0.225
    panels = build_ball(ball_radius, hex_size)

    svg_path = os.path.join(out_dir, "icon.svg")
    with open(svg_path, "w") as f:
        f.write(render_svg(REFERENCE_SIZE, panels, ball_radius, ring_width))
    print(f"wrote icon.svg ({len(panels)} panels)")

    for size in (192, 512):
        scale = size / REFERENCE_SIZE
        scaled_panels = [
            ([(x * scale, y * scale) for x, y in points], fill) for points, fill in panels
        ]
        write_png(
            os.path.join(out_dir, f"icon-{size}.png"),
            size,
            make_pixel_fn(size, scaled_panels, ball_radius * scale, ring_width * scale),
        )
        print(f"wrote icon-{size}.png")
