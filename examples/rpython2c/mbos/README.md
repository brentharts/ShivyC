# mbos — minibrowser on bare-metal VGA

A bare-metal build of the minibrowser DOM engine: boot a freestanding kernel
under QEMU, parse HTML into a DOM, and render it to the screen. No libc, no host
OS. The default kernel is **64-bit long mode** and renders the DOM into a
**VGA graphics framebuffer** with a bitmap font; it can also **fetch the page
over the network** (virtio-net + ARP + UDP). Everything has self-contained
headless tests.

```
make            # build build/mbos.elf  (64-bit, graphics)
make run        # boot in a QEMU window (embedded page)
make serial     # boot headless, page text streamed to your terminal
make test       # headless self-test: render + assert + save a screenshot
make run-net    # boot with a virtio NIC; the page comes from serve_page.py
make test-net   # headless self-test: fetch net.html over ARP+UDP, render, assert
make hires      # 1920x1080 via -device VGA,vgamem_mb=64
make test-hires # ...and its headless test
make rpython[-test][-net]   # same kernel, but dom/html/render generated from
                            # rpython by py2c (see rpy/README.md)
make text32     # the original 32-bit text-mode kernel (build/mbos_text32.elf)
```

## Why it boots the way it does

`qemu-system-x86_64 -kernel mbos.elf` loads the kernel directly — no GRUB, no
ISO, no disk — which is what keeps the tests self-contained. The trick for a
64-bit kernel is the Multiboot1 **AOUT kludge**: `boot64.S`'s header sets flag
bit 16 and carries load/entry addresses, so QEMU loads the image flat by those
addresses instead of parsing the ELF (its `-kernel` loader otherwise rejects a
64-bit ELF: *"give a 32bit one"*). `boot64.S` enters in 32-bit protected mode as
Multiboot guarantees, identity-maps the first **4 GiB** (1 GiB isn't enough —
the graphics framebuffer sits at ~0xFD000000), enables SSE, switches to long
mode, and calls `kmain`.

The `text32` target still builds the original 32-bit multiboot text-mode kernel
(`boot.S` + `console_text.c`), kept as a reference/fallback.

## Graphics

`vbe.c` drives QEMU's std VGA through the Bochs display interface (DISPI ports
0x1CE/0x1CF): it finds the device on PCI, enables its framebuffer BAR, and sets
a 32-bpp linear mode with no BIOS calls. `console.c` then draws each character
as an 8x16 glyph (`font8x16.h`, rasterized from DejaVu Sans Mono) into that
framebuffer, mapping the 16-colour VGA attribute palette to RGB — or falls back
to the 0xB8000 text buffer if no framebuffer is present. Because the DOM
renderers only ever call the `con_*` API, **nothing above `console.c` changed**
when moving from an 80x25 text grid to a 1024x768 graphics surface: the same
traversal now lays out more columns and paints them with a real font. Default
geometry is 1024x768 (fits std VGA's 16 MiB); `make hires` uses 1920x1080 with a
64 MiB VGA device.

## Architecture (and how it maps to the Wayland minibrowser)

The traversal and the Node shape deliberately match
`examples/rpython2c/minibrowser`, so each C piece here has a rpython counterpart
it can be **generated from** by `tools/py2c.py` (and is — see `rpy/`):

| mbos (freestanding C) | minibrowser (rpython → C via py2c) | role |
|---|---|---|
| `dom.h` / `dom.c`     | `dom.py`        | `Node{tag_name,text,href,children}` + arena |
| `html.c`              | `www2json.py`   | HTML text → DOM tree |
| `render.c`            | `json2qt.py`    | walk the DOM, lay it out, paint |
| `console.c` + `vbe.c` + `font8x16.h` | `rpyqt` + Wayland glue | the "display" backend |
| `net.c` + `serve_page.py` | shelling out to `www2json.py <url>` | fetching the page |
| `page.html`→`page_html.h` (`gen_page.py`) | `page.json`/`page_data.py` (`www2json.py`) | the page, compiled in |

`dom.c` bump-allocates every Node/string out of one static arena and frees it in
one shot — the same "one arena, no GC" model py2c uses (`aalloc`), just sized at
compile time because there is no `malloc` on bare metal.

### What renders today
Block layout (each block on its own line, blank line between paragraphs), word
wrap at the 80-column margin, and colour standing in for role: `<h1>` yellow with
an `===` underline, `<h2>/<h3>` white, `<p>` grey, `<a>` cyan with its `href`
shown as `[…]`, `<ul>/<li>` bulleted. Unknown tags stay generic so their text
still shows; `<head>/<script>/<style>` are skipped. Every glyph is mirrored to
COM1 so the test can assert on the output without scraping the framebuffer.

### Networking (step 1.5) and its relationship to minikraft's stack

minikraft ships a full network stack: a virtio-net driver
(`src/drivers/virtio-net/`), the uk_netdev/uk_netbuf device API, and a
packet-level ARP/IP/UDP/TCP echo server in `src/app/app.c`
(`mk.build(..., bare_metal=False, echo=True)` builds it). mbos's `net.c` is the
**compact, polled stand-in for that stack**: legacy virtio-PCI only, one RX +
one TX queue, static buffers, no interrupts — about 250 lines that follow the
spec bring-up order (reset → ACK → DRIVER → features → queue PFNs → DRIVER_OK).
The addressing convention is minikraft's echo demo: guest static
`192.168.100.2`, host alias `192.168.100.1` (which QEMU slirp NATs to
`127.0.0.1`, where `serve_page.py` answers).

Two things worth knowing:
* The netfetch protocol is deliberately one UDP exchange — the guest sends a
  path, the server replies with the HTML in a single datagram (pages ≤ ~1400 B
  for now). TCP + multi-packet pages are the follow-on step, and minikraft's
  `app.c` already contains the TCP state handling to crib from.
* minikraft's own virtio-net driver did not receive packets under the QEMU in
  this environment (8.2; its comments reference QEMU 10.2 ordering workarounds).
  `test_net.py` writes a pcap (`build/mbos_net.pcap`) precisely so driver-level
  issues like that are diagnosable from the wire: the passing capture is 4
  packets — guest ARP request, slirp ARP reply, guest UDP request, slirp UDP
  reply with the page. Reconciling the two drivers (fixing minikraft's under
  QEMU 8.2, or upstreaming mbos's polled driver into minikraft as a fallback
  path) is tracked in the roadmap.

If no NIC is present, `kmain` falls back to the embedded page, so the offline
targets keep working unchanged.

## Roadmap

1. **✅ basic HTML → VGA text.** gcc-freestanding, 32-bit multiboot, headless
   screenshot test.
2. **✅ fetch the page over the network** (this commit): polled virtio-net +
   ARP + UDP netfetch, host-side page server, pcap-verified test.
3. **TCP + multi-datagram pages**, then real HTTP GET so mbos can talk to any
   web server — minikraft's `app.c` TCP echo handling is the reference.
4. **Compile the render path with ShivyCX** instead of gcc. ShivyCX emits
   x86-64 objects, so this rides on the 64-bit boot path (GRUB-loaded
   `boot64.S`), the same split `shivycx_baremetal.py --image` resolves.
5. **✅ Generate `dom`/`html`/`render` from rpython** (this commit): `dom.py`
   (shared with the Wayland browser) + `htmlparse.py` + `render.py` translated
   by `tools/py2c.py` and linked into the kernel behind `-DMBOS_RPYTHON`, with a
   freestanding backing for the generated runtime. Byte-identical output to the
   hand-written C path; passes both the embedded and network self-tests. See
   `rpy/README.md` (including the 32-bit obj-boxing bug writeup).
6. **✅ Graphics-mode VGA** (this commit): a Bochs-VBE linear framebuffer
   (`vbe.c`) with an 8x16 bitmap font (`font8x16.h`), driven by a
   framebuffer-aware `console.c`. The DOM renderers were untouched. Next within
   this thread: proportional fonts and real box layout, then `<img>` (reusing
   `mb_imgcache` ideas), and a virtio-gpu backend.
7. **✅ 64-bit long mode** (this commit): `boot64.S` boots a 64-bit kernel
   under a plain `qemu -kernel` via the Multiboot AOUT kludge — which, besides
   unlocking the >1 GiB framebuffer mapping, sidesteps the whole class of 32-bit
   struct-ABI hazards the rpython path hit. `make hires` runs 1920x1080 with:
   ```
   qemu-system-x86_64 ... -vga none -device VGA,vgamem_mb=64
   ```
8. **Compile the render path with ShivyCX** instead of gcc. ShivyCX emits
   x86-64 objects, which now matches the 64-bit kernel directly — the earlier
   32/64 obstacle is gone. This is the remaining toolchain-convergence step.
9. **TCP + multi-datagram pages**, then real HTTP GET so mbos can fetch any web
   server — minikraft's `app.c` TCP handling is the reference.

## Files
```
boot64.S linker64.ld   64-bit long-mode entry (AOUT kludge, 4 GiB map) + link
boot.S linker.ld       32-bit multiboot entry (text32 build)
mbos.h                 types, port I/O, mini libc + console/graphics API
libmini.c              memset/memcpy/strlen/strcmp (freestanding)
vbe.c                  Bochs-VBE linear-framebuffer graphics driver
font8x16.h             8x16 bitmap font (rasterized from DejaVu Sans Mono)
console.c              graphics/text console + COM1 serial mirror
console_text.c         text-only console for the 32-bit text32 build
dom.h dom.c            Node model + arena (mirrors dom.py)
html.c                 HTML → DOM (mirrors www2json.py)
render.c               DOM → console layout (mirrors json2qt.py)
net.c                  polled virtio-net + ARP/IPv4/UDP netfetch client
main.c                 kmain: fetch (or embedded) page, parse, render, idle
page.html gen_page.py  the built-in page + its embed generator
net.html serve_page.py the network page + the host-side UDP server
Makefile               build/run/test (+ net, hires, rpython, text32) targets
test.py test_net.py    headless self-tests (serial asserts + screenshots)
rpy/                   the rpython render path (py2c-generated); see rpy/README.md
```

