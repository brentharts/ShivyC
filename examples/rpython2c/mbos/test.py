#!/usr/bin/env python3
"""test.py -- self-contained mbos smoke test.

Boots build/mbos.elf under QEMU with no display, captures the serial output (the
console mirrors every rendered glyph there), and asserts the page rendered: the
heading, wrapped paragraph text, the link's href, and the list items. Also grabs
a VGA screenshot via the QEMU monitor for a visual artifact.

Exit code 0 = pass. Needs qemu-system-x86_64 in PATH; the screenshot step
additionally needs Pillow but is optional (skipped with a note if unavailable).
"""
import os, socket, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ELF  = os.environ.get("MBOS_ELF", os.path.join(HERE, "build", "mbos.elf"))
MON  = "/tmp/mbos_mon.sock"
PPM  = "/tmp/mbos_screen.ppm"
PNG  = os.path.join(HERE, "build", "mbos_screen.png")

# Substrings that must appear in the rendered serial stream.
EXPECT = [
    "[mbos] boot ok",
    "mbos",                                   # h1 heading
    "=" * 40,                                 # h1 underline
    "What works",                             # h2
    "word wrap",                              # wrapped paragraph text
    "[http://example.com]",                   # anchor href annotation
    "[/about.html]",
    "* parse HTML to a DOM",                  # list item
    "[mbos] done.",
]


def run_and_capture(timeout=10):
    if os.path.exists(MON):
        os.remove(MON)
    proc = subprocess.Popen(
        ["qemu-system-x86_64", "-kernel", ELF, "-display", "none",
         "-serial", "stdio", "-no-reboot"]
        + os.environ.get("MBOS_VGA_ARGS", "-vga std").split() + [
         "-monitor", "unix:%s,server,nowait" % MON],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    # give it time to boot + render, then screenshot, then stop
    time.sleep(4)
    shot_ok = try_screenshot()
    time.sleep(1)
    proc.terminate()
    try:
        out = proc.stdout.read().decode("utf-8", "replace")
    except Exception:
        out = ""
    proc.wait()
    return out, shot_ok


def try_screenshot():
    try:
        s = socket.socket(socket.AF_UNIX)
        s.connect(MON)
        time.sleep(0.3); s.recv(4096)
        s.sendall(("screendump %s\n" % PPM).encode()); time.sleep(1.0)
        s.recv(4096); s.close()
        from PIL import Image
        Image.open(PPM).save(PNG)
        return True
    except Exception as e:
        sys.stderr.write("screenshot skipped: %s\n" % e)
        return False


def main():
    if not os.path.exists(ELF):
        sys.exit("missing %s -- run `make` first" % ELF)
    out, shot_ok = run_and_capture()
    print(out)
    missing = [e for e in EXPECT if e not in out]
    if missing:
        print("FAIL -- missing expected output:")
        for m in missing:
            print("   %r" % m)
        sys.exit(1)
    print("PASS -- all %d expected fragments rendered." % len(EXPECT))
    if shot_ok:
        print("screenshot: %s" % PNG)
    sys.exit(0)


if __name__ == "__main__":
    main()
