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
    lines = []
    lines.append("static const unsigned short %s_palette[%d] = { %s };"
                 % (prefix, len(img.palette), pal))
    lines.append("static const unsigned char %s_pixels[%d] = { %s };"
                 % (prefix, len(img.pixels), pix))
    return "\n".join(lines)


def scene_to_c(scene):
    """Emit the baked scene as portable C: per-image palette and pixel arrays,
    plus a `neogeo_layer` table describing each layer. Tile/bitplane packing for
    the real C-ROM is a later step; this is the colour-correct source data."""
    out = []
    out.append("/* Neo-Geo scene baked from ASCII art at translate time. */")
    out.append("typedef struct {")
    out.append("    const char* kind;")
    out.append("    int width, height, tile;")
    out.append("    const unsigned short* palette; int palette_len;")
    out.append("    const unsigned char* pixels;")
    out.append("} neogeo_layer;")
    out.append("")

    table = []
    n = 0
    for img in scene.backgrounds + scene.sprites:
        prefix = "ng_layer%d" % n
        out.append(_img_c(prefix, img))
        table.append('    { "%s", %d, %d, %d, %s_palette, %d, %s_pixels }'
                     % (img.kind, img.width, img.height, img.tile_size(),
                        prefix, len(img.palette), prefix))
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
