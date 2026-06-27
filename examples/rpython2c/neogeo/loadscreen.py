"""Neo-Geo loading-screen demo (no input).

The whole screen is described as ASCII art and handed to the `neogeo` library,
which turns it into Neo-Geo pixel art -- a 16-bit palette plus an index buffer
per layer. Because the art is constant, `import neogeo` lets py2c bake the
conversion at translate time (see tools/rpy_neogeo_integration.py): the emitted
C carries the finished palette/tile data, so the on-target code is just a copy.

Colour key (case = intensity): R/r red, G/g green, B/b blue, C/c cyan,
M/m magenta, Y/y yellow, O/o orange, W/w white/grey, K black, '.'/' ' clear.
"""
import neogeo


# The backdrop: a cyan frame, a white title bar, and a little starfield.
BACKGROUND = """
CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC
C............................WC
C..y.......WWWWWWWWWW.......b.C
C.........WWWWWWWWWWWW........C
C....b....WWWWWWWWWWWW....y...C
C.........WWWWWWWWWWWW........C
C..........WWWWWWWWWW.........C
C............................C
C...y...................b....C
C............................C
C.b.....................y...WC
C............................C
CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC
"""

# A small sprite that sits on top of the backdrop: a red/orange rocket.
ROCKET = """
....W....
...WWW...
...RYR...
...RYR...
..RRYRR..
..R.Y.R..
.OO.O.OO.
..o...o..
"""


a = neogeo.background.asciiart(BACKGROUND)
b = neogeo.sprite.asciiart(ROCKET)
neogeo.scene.add_background(a)
neogeo.scene.add_sprite(b)
