#!/usr/bin/env python3
"""test_net.py -- self-contained network fetch test for mbos.

Starts the UDP page server on 127.0.0.1:8080, boots build/mbos.elf under QEMU
with a virtio-net NIC on a slirp network whose host alias NATs to loopback,
and asserts from the serial stream that (a) the driver came up, (b) the page
source was "network", and (c) the fetched page's text rendered. A pcap of the
netdev is saved next to the ELF for inspection, plus a VGA screenshot.

Exit 0 = pass.
"""
import os, socket, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ELF  = os.environ.get("MBOS_ELF", os.path.join(HERE, "build", "mbos.elf"))
MON  = "/tmp/mbos_net_mon.sock"
PCAP = os.path.join(HERE, "build", "mbos_net.pcap")
PPM  = "/tmp/mbos_net_screen.ppm"
PNG  = os.path.join(HERE, "build", "mbos_net_screen.png")
PORT = 8080

EXPECT = [
    "[net] virtio-net up",
    "[mbos] page source: network",
    "Fetched over the network",              # h1 of net.html
    "resolved the host with ARP",            # body text off the wire
    "* ARP for 192.168.100.1, answered by QEMU slirp",
    "[mbos] done.",
]

served = {"n": 0}


def page_server(sock):
    body = open(os.path.join(HERE, "net.html"), "rb").read()[:1400]
    while True:
        try:
            data, addr = sock.recvfrom(2048)
        except OSError:
            return
        served["n"] += 1
        sock.sendto(body, addr)


def try_screenshot():
    try:
        s = socket.socket(socket.AF_UNIX)
        s.connect(MON); time.sleep(0.3); s.recv(4096)
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

    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", PORT))
    threading.Thread(target=page_server, args=(srv,), daemon=True).start()

    if os.path.exists(MON):
        os.remove(MON)
    proc = subprocess.Popen(
        ["qemu-system-x86_64", "-kernel", ELF, "-display", "none",
         "-serial", "stdio", "-no-reboot", "-vga", "std",
         "-monitor", "unix:%s,server,nowait" % MON,
         "-device", "virtio-net-pci,netdev=n0,disable-modern=on",
         "-netdev", "user,id=n0,net=192.168.100.0/24,host=192.168.100.1",
         "-object", "filter-dump,id=d0,netdev=n0,file=%s" % PCAP],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    time.sleep(6)
    shot_ok = try_screenshot()
    time.sleep(1)
    proc.terminate()
    out = proc.stdout.read().decode("utf-8", "replace")
    proc.wait()
    srv.close()

    print(out)
    missing = [e for e in EXPECT if e not in out]
    print("page server answered %d request(s); pcap: %s" % (served["n"], PCAP))
    if missing:
        print("FAIL -- missing expected output:")
        for m in missing:
            print("   %r" % m)
        sys.exit(1)
    print("PASS -- bare-metal browser fetched and rendered a page over the network.")
    if shot_ok:
        print("screenshot: %s" % PNG)
    sys.exit(0)


if __name__ == "__main__":
    main()
