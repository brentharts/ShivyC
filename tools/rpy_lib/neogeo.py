"""neogeo -- a tiny restricted-Python (rpython) graphics library for the Neo-Geo.

It turns multi-line ASCII art into Neo-Geo pixel art: a colour palette (in the
console's native 16-bit colour format) plus an index buffer, organised into the
hardware's tiles. The module is plain Python too, so it runs unchanged under
CPython as the correctness oracle -- and, because the ASCII art is normally a
string *constant*, py2c specialises `import neogeo`: it runs the conversion at
translate time and bakes the resulting palette/tile data straight into the
generated C (see tools/rpy_neogeo_integration.py), so the on-target code is just
data plus a copy loop rather than a string parser.

API (the four lines a loading screen needs):

    import neogeo
    a = neogeo.background.asciiart(".... multi-line ascii ....")
    b = neogeo.sprite.asciiart(".... multi-line ascii ....")
    neogeo.scene.add_background(a)
    neogeo.scene.add_sprite(b)

ASCII colour format -- one character per pixel, case = intensity:

    R r  red        G g  green      B b  blue
    C c  cyan       M m  magenta    Y y  yellow
    O o  orange     W w  white/grey K    black
    '.' or ' '      transparent (palette index 0)

Uppercase is full strength, lowercase is half. Unknown characters are treated
as transparent, so you can frame your art with any punctuation you like.
"""


# ---- Neo-Geo colour packing ------------------------------------------------
#
# A Neo-Geo palette entry is a 16-bit word. Each channel is 5 bits: the top 4
# bits sit in a nibble (red 11-8, green 7-4, blue 3-0) and the least
# significant bit sits in the "shared LSB" trio (red bit 14, green 13, blue 12);
# bit 15 is the global dark bit. White (31,31,31) packs to 0x7FFF and black to
# 0x0000, matching the hardware.

def ng_color(r: "int", g: "int", b: "int") -> "int":
    """Pack 5-bit (0..31) r,g,b into a Neo-Geo 16-bit colour word."""
    rr = r & 31
    gg = g & 31
    bb = b & 31
    word = 0
    word = word | (((rr >> 1) & 15) << 8)
    word = word | (((gg >> 1) & 15) << 4)
    word = word | (((bb >> 1) & 15) << 0)
    word = word | ((rr & 1) << 14)
    word = word | ((gg & 1) << 13)
    word = word | ((bb & 1) << 12)
    return word


def color_for_char(ch: "char*") -> "int":
    """Neo-Geo colour word for an ASCII art character (-1 means transparent)."""
    if ch == "R":
        return ng_color(31, 0, 0)
    if ch == "r":
        return ng_color(15, 0, 0)
    if ch == "G":
        return ng_color(0, 31, 0)
    if ch == "g":
        return ng_color(0, 15, 0)
    if ch == "B":
        return ng_color(0, 0, 31)
    if ch == "b":
        return ng_color(0, 0, 15)
    if ch == "C":
        return ng_color(0, 31, 31)
    if ch == "c":
        return ng_color(0, 15, 15)
    if ch == "M":
        return ng_color(31, 0, 31)
    if ch == "m":
        return ng_color(15, 0, 15)
    if ch == "Y":
        return ng_color(31, 31, 0)
    if ch == "y":
        return ng_color(15, 15, 0)
    if ch == "O":
        return ng_color(31, 16, 0)
    if ch == "o":
        return ng_color(15, 8, 0)
    if ch == "W":
        return ng_color(31, 31, 31)
    if ch == "w":
        return ng_color(15, 15, 15)
    if ch == "K":
        return ng_color(0, 0, 0)
    return -1                      # '.', ' ', and anything else: transparent


class Image:
    """An indexed-colour bitmap: a per-image palette of Neo-Geo colour words
    (index 0 is transparent) and a width*height buffer of palette indices."""

    def __init__(self, kind, width, height, palette, pixels):
        self.kind = kind           # "background" or "sprite"
        self.width = width
        self.height = height
        self.palette = palette     # list[int]: 16-bit colour words, [0]=transp.
        self.pixels = pixels       # list[int]: palette index per pixel

    def tile_size(self):
        """8 for backgrounds (fix-layer tiles), 16 for sprites (C-ROM tiles)."""
        if self.kind == "sprite":
            return 16
        return 8


def _lines(s):
    """Split `s` into rows, dropping a single leading/trailing blank line so a
    triple-quoted block reads naturally."""
    rows = s.split("\n")
    if len(rows) > 0 and rows[0] == "":
        rows = rows[1:]
    if len(rows) > 0 and rows[len(rows) - 1] == "":
        rows = rows[:len(rows) - 1]
    return rows


def build(kind, art):
    """Convert ASCII art `art` into an `Image` of the given kind. The palette is
    deduplicated in first-seen order with transparent at index 0."""
    rows = _lines(art)
    height = len(rows)
    width = 0
    i = 0
    while i < height:
        if len(rows[i]) > width:
            width = len(rows[i])
        i += 1

    palette = [ng_color(0, 0, 0)]      # index 0: transparent placeholder
    palette[0] = 0
    seen_words = [0]                   # parallel: colour word at each index
    pixels = []

    y = 0
    while y < height:
        row = rows[y]
        x = 0
        while x < width:
            ch = " "
            if x < len(row):
                ch = row[x]
            word = color_for_char(ch)
            if word < 0:
                pixels.append(0)       # transparent
            else:
                idx = -1
                k = 1
                while k < len(seen_words):
                    if seen_words[k] == word:
                        idx = k
                    k += 1
                if idx < 0:
                    seen_words.append(word)
                    palette.append(word)
                    idx = len(palette) - 1
                pixels.append(idx)
            x += 1
        y += 1

    return Image(kind, width, height, palette, pixels)


class _Factory:
    """`neogeo.background` / `neogeo.sprite`: a namespace whose `asciiart`
    builds an Image of the matching kind."""

    def __init__(self, kind):
        self.kind = kind

    def asciiart(self, art):
        return build(self.kind, art)


class _Scene:
    """`neogeo.scene`: the collection of layers to show. A loading screen is one
    background plus any number of sprites."""

    def __init__(self):
        self.backgrounds = []
        self.sprites = []

    def reset(self):
        self.backgrounds = []
        self.sprites = []

    def add_background(self, img):
        self.backgrounds.append(img)

    def add_sprite(self, img):
        self.sprites.append(img)


background = _Factory("background")
sprite = _Factory("sprite")
scene = _Scene()
