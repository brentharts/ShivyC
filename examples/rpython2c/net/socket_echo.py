"""rpython TCP networking, lowered to BSD sockets (no runtime).

A single binary that talks to itself over real TCP on 127.0.0.1: the parent is
a server (bind/listen/accept/recv), the child (after os.fork) is a client
(connect/send). Socket fds are plain ints; the (host, port) tuple is lowered
into a sockaddr_in by an emitted helper. The exit code is the number of bytes
the server received, so the round trip is observable without captured output.

    python3 -m shivyc.main --no-cache socket_echo.py -o /tmp/echo && /tmp/echo
    echo $?      # 5 = len("hello")
"""
import socket, os


def main() -> int:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 8137))
    server.listen(1)

    pid = os.fork()
    if pid == 0:
        # child: connect and send, then leave without running server code
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", 8137))
        client.send("hello")
        client.close()
        os._exit(0)

    # parent: accept the connection and read the message
    conn = server.accept()
    buf: "char*" = malloc(256)
    n = conn.recv(buf, 256)
    conn.close()
    server.close()
    return n
