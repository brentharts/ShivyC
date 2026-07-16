#!/usr/bin/env python3
"""serve_page.py -- the host side of mbos's netfetch protocol.

One UDP exchange: the guest sends a path ("/page.html"), we reply with the
file's bytes in a single datagram. This is the bare-metal stand-in for the
Wayland minibrowser shelling out to `python3 www2json.py <url>`: the host does
the fetching/serving, the kernel does the parsing/rendering.

Serves files from this directory; only names we ship are allowed. A fetched
page must fit one datagram for now (~1500 B) -- multi-datagram pages are a
later step alongside TCP.
"""
import os, socket, sys

HERE  = os.path.dirname(os.path.abspath(__file__))
PORT  = 8080
ALLOW = {"/page.html": "page.html", "/net.html": "net.html"}


def serve():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", PORT))
    sys.stderr.write("serve_page: listening on udp/127.0.0.1:%d\n" % PORT)
    while True:
        data, addr = s.recvfrom(2048)
        path = data.decode("utf-8", "replace").strip()
        name = ALLOW.get(path)
        if name and os.path.exists(os.path.join(HERE, name)):
            body = open(os.path.join(HERE, name), "rb").read()[:1400]
        else:
            body = (b"<html><body><h1>404</h1><p>no such page: " +
                    path.encode() + b"</p></body></html>")
        s.sendto(body, addr)
        sys.stderr.write("serve_page: %s -> %d bytes to %s\n"
                         % (path, len(body), addr))


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        if os.fork():   # detach so `make run-net` can continue to QEMU
            sys.exit(0)
        os.setsid()
    serve()
