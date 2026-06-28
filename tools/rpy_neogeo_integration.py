"""rpy_neogeo -- py2c-side integration for the bundled Neo-Geo graphics library.

Two roles, both triggered by `import neogeo` in an rpython source:

1. **bundle** -- append `rpy_lib/neogeo.py` to the translation unit so the API is
   co-compiled, exactly like rpy_plot / rpy_torch.

2. **bake (the specialisation)** -- because a loading screen's ASCII art is a
   string *constant*, the conversion can run at translate time instead of on the
   console. `bake_source` executes the source's module-level scene-building (with
   the real `neogeo` module) and `scene_to_c` emits the resulting Neo-Geo palette
   and pixel/tile data as static C arrays plus a scene descriptor. The on-target
   program then needs only a copy loop, not a string parser -- which is what
   makes this reachable on a small target like the m68k.

`scene_to_ppm` renders the same scene to a PPM image so the ASCII-to-pixel
conversion and the colour format can be eyeballed and regression-tested off
hardware.
"""
import os
import sys

LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rpy_lib")
LIB_FILE = os.path.join(LIB_DIR, "neogeo.py")


def _ensure_lib_on_path():
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)


def imports_neogeo(path):
    """True if the rpython source at `path` imports the neogeo module."""
    import ast
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:
        return False
    for n in ast.walk(tree):
        if isinstance(n, ast.Import) and any(
                a.name == "neogeo" for a in n.names):
            return True
        if isinstance(n, ast.ImportFrom) and n.module == "neogeo":
            return True
    return False


def bundle(files):
    """Return `files`, with the neogeo library appended iff some input imports
    it and it isn't already present. Idempotent and best-effort."""
    files = list(files)
    if not os.path.isfile(LIB_FILE):
        return files
    if any(os.path.basename(f) == "neogeo.py" for f in files):
        return files
    for f in files:
        if f.endswith(".py") and imports_neogeo(f):
            files.append(LIB_FILE)
            break
    return files


# ---- compile-time bake -----------------------------------------------------

def bake_source(path):
    """Execute the module-level scene-building in `path` with the real neogeo
    module and return the populated `neogeo.scene`. The ASCII-art conversion
    runs here, at translate time."""
    _ensure_lib_on_path()
    import neogeo
    neogeo.scene.reset()
    src = open(path, encoding="utf-8").read()
    ns = {"neogeo": neogeo, "__name__": "__neogeo_bake__"}
    exec(compile(src, path, "exec"), ns)
    return neogeo.scene


def _img_c(prefix, img):
    pal = ", ".join("0x%04X" % w for w in img.palette)
    pix = ", ".join("%d" % p for p in img.pixels)
    cols, rows, size, planar = pack_image_tiles(img)
    tdata = ", ".join("0x%02X" % b for b in planar)
    lines = []
    lines.append("static const unsigned short %s_palette[%d] = { %s };"
                 % (prefix, len(img.palette), pal))
    lines.append("static const unsigned char %s_pixels[%d] = { %s };"
                 % (prefix, len(img.pixels), pix))
    lines.append("/* %d x %d tiles of %dx%d px, 4 bitplanes each (Neo-Geo gfx"
                 " ROM order) */" % (cols, rows, size, size))
    lines.append("static const unsigned char %s_tiles[%d] = { %s };"
                 % (prefix, len(planar), tdata))
    return "\n".join(lines), cols, rows, size, len(planar)


def scene_to_c(scene):
    """Emit the baked scene as portable C: per-image palette, pixel, and packed
    4-bitplane tile arrays, plus a `neogeo_layer` table describing each layer."""
    out = []
    out.append("/* Neo-Geo scene baked from ASCII art at translate time. */")
    out.append("typedef struct {")
    out.append("    const char* kind;")
    out.append("    int width, height, tile;")
    out.append("    int tile_cols, tile_rows, tile_bytes;")
    out.append("    const unsigned short* palette; int palette_len;")
    out.append("    const unsigned char* pixels;")
    out.append("    const unsigned char* tiles;")
    out.append("} neogeo_layer;")
    out.append("")

    table = []
    n = 0
    for img in scene.backgrounds + scene.sprites:
        prefix = "ng_layer%d" % n
        body, cols, rows, size, tbytes = _img_c(prefix, img)
        out.append(body)
        table.append('    { "%s", %d, %d, %d, %d, %d, %d, %s_palette, %d,'
                     ' %s_pixels, %s_tiles }'
                     % (img.kind, img.width, img.height, size, cols, rows,
                        tbytes, prefix, len(img.palette), prefix, prefix))
        n += 1
    out.append("")
    out.append("static const neogeo_layer neogeo_scene[%d] = {" % n)
    out.append(",\n".join(table))
    out.append("};")
    out.append("static const int neogeo_scene_len = %d;" % n)
    out.append("")
    return "\n".join(out) + "\n"


def scene_main_c(scene):
    """A small portable `main` walking the baked scene -- a stand-in for the
    on-hardware VRAM/palette upload. It counts non-transparent pixels and
    checksums the palette, prints a per-layer summary, and returns the lit-pixel
    count (mod 256) as the exit code, so the whole rpython->C->native path is
    runnable and deterministically checkable off the console."""
    return (
        "#include <stdio.h>\n"
        "int main(void){\n"
        "    int total = 0; unsigned chk = 0; int L, i;\n"
        "    for (L = 0; L < neogeo_scene_len; L++) {\n"
        "        const neogeo_layer* ly = &neogeo_scene[L];\n"
        "        int npix = ly->width * ly->height;\n"
        "        for (i = 0; i < npix; i++) {\n"
        "            unsigned char idx = ly->pixels[i];\n"
        "            if (idx) { total++; chk = (chk + ly->palette[idx]) & 0xffff; }\n"
        "        }\n"
        "        printf(\"layer %d: %s %dx%d, %d colors, tile %d\\n\",\n"
        "               L, ly->kind, ly->width, ly->height, ly->palette_len,\n"
        "               ly->tile);\n"
        "    }\n"
        "    printf(\"scene: %d layers, %d lit pixels, palette checksum 0x%04X\\n\",\n"
        "           neogeo_scene_len, total, chk);\n"
        "    return total & 255;\n"
        "}\n")


# ---- tile / bitplane packing -----------------------------------------------

def _tile_grid(img):
    """Split an image into tile_size x tile_size tiles (8 for backgrounds/fix,
    16 for sprites), row-major across the tile grid. Partial edge tiles are
    padded with transparent (index 0). Returns (cols, rows, list-of-tiles), each
    tile a flat list of `size*size` palette indices."""
    size = img.tile_size()
    cols = (img.width + size - 1) // size
    rows = (img.height + size - 1) // size
    tiles = []
    for ty in range(rows):
        for tx in range(cols):
            tile = []
            for y in range(size):
                for x in range(size):
                    sx = tx * size + x
                    sy = ty * size + y
                    if sx < img.width and sy < img.height:
                        tile.append(img.pixels[sy * img.width + sx])
                    else:
                        tile.append(0)
            tiles.append(tile)
    return cols, rows, tiles


def pack_bitplanes(tile, size):
    """Pack one tile's 4-bit indices into 4 Neo-Geo bitplanes. Each plane is one
    bit per pixel, row-major, MSB first, so a `size`x`size` tile yields 4 byte
    strings of size*size/8 bytes. (The final per-ROM byte interleave for the
    physical C/S ROMs is applied by the ngdevkit packaging step.)"""
    planes = []
    for bit in range(4):
        bytes_out = []
        acc = 0
        nbits = 0
        for idx in tile:
            acc = (acc << 1) | ((idx >> bit) & 1)
            nbits += 1
            if nbits == 8:
                bytes_out.append(acc)
                acc = 0
                nbits = 0
        if nbits:
            bytes_out.append(acc << (8 - nbits))
        planes.append(bytes_out)
    return planes


def unpack_bitplanes(planes, size):
    """Inverse of pack_bitplanes: reconstruct the size*size index list (used to
    verify packing is lossless)."""
    out = []
    for i in range(size * size):
        byte_i = i // 8
        bit_i = 7 - (i % 8)
        idx = 0
        for bit in range(4):
            idx |= ((planes[bit][byte_i] >> bit_i) & 1) << bit
        out.append(idx)
    return out


def pack_image_tiles(img):
    """Tile an image and bitplane-pack every tile. Returns (cols, rows, size,
    planar_bytes) where planar_bytes is the concatenation, per tile, of its 4
    bitplanes -- the 4bpp planar tile data the Neo-Geo gfx ROMs hold."""
    size = img.tile_size()
    cols, rows, tiles = _tile_grid(img)
    planar = []
    for tile in tiles:
        for plane in pack_bitplanes(tile, size):
            planar.extend(plane)
    return cols, rows, size, planar


# ---- off-hardware preview (testing) ----------------------------------------

def _unpack(word):
    """Neo-Geo colour word -> (r,g,b) each 0..255, for a PPM preview."""
    r = (((word >> 8) & 15) << 1) | ((word >> 14) & 1)
    g = (((word >> 4) & 15) << 1) | ((word >> 13) & 1)
    b = (((word >> 0) & 15) << 1) | ((word >> 12) & 1)
    return (r * 8, g * 8, b * 8)      # 5-bit -> 8-bit-ish


def scene_to_ppm(scene, path, scale=8, bg=(24, 24, 24)):
    """Composite the scene (backgrounds then sprites, transparent index 0 shows
    through) into a PPM image at `path`, scaled up `scale`x for visibility."""
    layers = scene.backgrounds + scene.sprites
    W = 0
    H = 0
    for img in layers:
        if img.width > W:
            W = img.width
        if img.height > H:
            H = img.height
    if W == 0 or H == 0:
        W = 1
        H = 1
    canvas = [bg] * (W * H)
    for img in layers:
        for y in range(img.height):
            for x in range(img.width):
                idx = img.pixels[y * img.width + x]
                if idx == 0:
                    continue          # transparent
                canvas[y * W + x] = _unpack(img.palette[idx])
    with open(path, "w") as f:
        f.write("P3\n%d %d\n255\n" % (W * scale, H * scale))
        for y in range(H):
            for _sy in range(scale):
                row = []
                for x in range(W):
                    r, g, b = canvas[y * W + x]
                    cell = "%d %d %d " % (r, g, b)
                    row.append(cell * scale)
                f.write("".join(row) + "\n")
    return (W, H)
