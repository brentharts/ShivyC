#!/usr/bin/env python3
"""Convert an image (local file or URL) into a blit-ready cache file for the
minibrowser.

The minibrowser composites the whole window into one XRGB8888 software
framebuffer, so an <img> is just a rectangle of pixels blitted into that buffer
(exactly like the canvas). This tool does the decode/resize/format work with
PIL, ahead of time, so the pure-rpython browser never has to link libpng/libjpeg
or carry pixel data in its bytecode -- it only holds the cache-file path.

Cache file layout (all little-endian):

    u32 magic  = 0x494D4731  ('IMG1')
    u32 width
    u32 height
    width*height * u32 pixels, each 0xAARRGGBB

The pixel bytes are B,G,R,A in memory, i.e. a native little-endian u32 that
drops straight into the framebuffer (the alpha byte lets the widget blend).

Usage:
    python3 mb_imgcache.py <src> <out.img> [max_w] [max_h]

`src` is a local path or an http(s) URL. Prints "OK <w> <h>" on success.
"""
import hashlib
import os
import struct
import sys
import tempfile

MAGIC = 0x494D4731  # 'IMG1'
CACHE_DIR = os.path.join(tempfile.gettempdir(), "mb_img_cache")


def cache_path_for(src):
    """Deterministic cache filename for a source (so repeated loads reuse it)."""
    h = hashlib.sha1(src.encode("utf-8")).hexdigest()[:16]
    return os.path.join(CACHE_DIR, h + ".img")


def _fetch_to_temp(url):
    """Download a URL to a temp file and return its path (or None)."""
    import urllib.request
    fd, path = tempfile.mkstemp(suffix=".dl")
    os.close(fd)
    try:
        # Send a real User-Agent: some hosts (notably Wikimedia's image
        # servers) reject the default urllib agent with 403.
        req = urllib.request.Request(
            url, headers={"User-Agent": "minibrowser/0.1 (image fetch)"})
        with urllib.request.urlopen(req, timeout=15) as r, \
                open(path, "wb") as f:
            f.write(r.read())
        return path
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        return None


def convert(src, out_path, max_w=0, max_h=0):
    """Decode `src`, optionally shrink to fit (max_w,max_h), write the cache
    file at `out_path`. Returns (w, h) or raises."""
    from PIL import Image

    tmp = None
    local = src
    if src == ":test:":
        # A tiny fixed pattern for the headless self-test: 4x2 with known
        # colours including a half-transparent pixel, so a reader can verify
        # both the format and alpha end-to-end without shipping an asset.
        img = Image.new("RGBA", (4, 2), (0, 0, 0, 0))
        img.putpixel((0, 0), (255, 0, 0, 255))    # opaque red
        img.putpixel((1, 0), (0, 255, 0, 255))    # opaque green
        img.putpixel((3, 1), (0, 0, 255, 128))    # blue, half alpha
        w, h = img.size
        data = img.tobytes("raw", "BGRA")
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        tmp_out = out_path + ".tmp%d" % os.getpid()
        with open(tmp_out, "wb") as f:
            f.write(struct.pack("<III", MAGIC, w, h))
            f.write(data)
        os.replace(tmp_out, out_path)
        return w, h

    if src.startswith("http://") or src.startswith("https://"):
        tmp = _fetch_to_temp(src)
        if tmp is None:
            raise IOError("download failed: " + src)
        local = tmp
    try:
        img = Image.open(local).convert("RGBA")
        if max_w and max_h and (img.width > max_w or img.height > max_h):
            img.thumbnail((max_w, max_h))
        w, h = img.size
        # PIL packs "BGRA" as B,G,R,A bytes == little-endian 0xAARRGGBB u32,
        # which is the framebuffer's pixel layout: no per-pixel Python work.
        data = img.tobytes("raw", "BGRA")
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    # Write to a temp file then rename, so a concurrent reader never sees a
    # half-written cache file.
    tmp_out = out_path + ".tmp%d" % os.getpid()
    with open(tmp_out, "wb") as f:
        f.write(struct.pack("<III", MAGIC, w, h))
        f.write(data)
    os.replace(tmp_out, out_path)
    return w, h


def convert_cached(src, max_w=0, max_h=0):
    """Convert `src` into the shared cache dir keyed by src; return its path.
    Reuses an existing cache file when present."""
    out = cache_path_for(src)
    if os.path.exists(out) and os.path.getsize(out) >= 12:
        return out
    convert(src, out, max_w, max_h)
    return out


def main(argv):
    if len(argv) < 3:
        sys.stderr.write("usage: mb_imgcache.py <src> <out.img> "
                         "[max_w] [max_h]\n")
        return 2
    src, out = argv[1], argv[2]
    max_w = int(argv[3]) if len(argv) > 3 else 0
    max_h = int(argv[4]) if len(argv) > 4 else 0
    try:
        w, h = convert(src, out, max_w, max_h)
    except Exception as e:  # noqa: BLE001 -- CLI boundary, report and fail
        sys.stderr.write("mb_imgcache: %s\n" % e)
        return 1
    sys.stdout.write("OK %d %d\n" % (w, h))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
