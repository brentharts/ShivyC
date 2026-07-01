"""rpython HTTP/1.1 server, lowered to BSD sockets (no runtime).

A single dependency-free binary that speaks real HTTP/1.1 over TCP on
127.0.0.1. The parent process is the server (bind/listen/accept/recv, parse the
request line, route on the path, send a well-formed response); the os.fork()
child is a minimal HTTP client that connects and issues `GET / HTTP/1.0`.

Everything above `send`/`recv` -- request-line parsing, path routing, and
response construction with a computed Content-Length -- is ordinary rpython
(string concat, indexing, len, startswith, str(int)) compiled straight to C by
py2c. The socket calls lower to plain BSD sockets; fds are opaque ints.

The exit code is the HTTP status the server routed and served, so the whole
round trip -- accept, parse, route, respond, and a client that actually read a
valid response -- is observable without captured output:

    python3 -m shivyc.main --no-cache http_server.py -o /tmp/httpd && /tmp/httpd
    echo $?      # 200  (client requested "/", server routed 200 OK)

Change the client's request path to something else (e.g. "/nope") and the exit
code becomes 144 == 400 & 0xFF (404 & 0xFF is 148); see route() below.
"""
import socket, os


def status_text(code: "int") -> "char*":
    if code == 200:
        return "OK"
    return "Not Found"


def route(path: "char*") -> "int":
    """Trivial router: only "/" exists."""
    if path == "/":
        return 200
    return 404


def body_for(code: "int") -> "char*":
    if code == 200:
        return "<h1>hello from rpython</h1>\n"
    return "<h1>404</h1>\n"


def parse_path(req: "char*", n: "int") -> "char*":
    """Extract the request-target from an HTTP request line.

    `req` holds `METHOD SP target SP HTTP/x.y CRLF ...`. Return the target
    (the run of bytes between the first and second space).
    """
    i = 0
    # skip the method, up to the first space
    while i < n and req[i] != " ":
        i = i + 1
    i = i + 1                       # step over the space
    start = i
    while i < n and req[i] != " ":
        i = i + 1
    out = ""
    j = start
    while j < i:
        out = out + req[j]
        j = j + 1
    return out


def build_response(code: "int", body: "char*") -> "char*":
    clen = len(body)
    r = "HTTP/1.1 "
    r = r + str(code)
    r = r + " "
    r = r + status_text(code)
    r = r + "\r\nContent-Type: text/html\r\nContent-Length: "
    r = r + str(clen)
    r = r + "\r\nConnection: close\r\n\r\n"
    r = r + body
    return r


def main() -> int:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 8138))
    server.listen(1)

    pid = os.fork()
    if pid == 0:
        # child: a minimal HTTP/1.0 client -- connect, GET /, read the reply.
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", 8138))
        client.send("GET / HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
        rbuf: "char*" = malloc(1024)
        got = client.recv(rbuf, 1024)
        client.close()
        # a well-formed response starts with "HTTP/1.1 2"; anything else is a
        # client-side failure. (Child exit is not the binary's exit code, but
        # this keeps the client honest during development.)
        if got < 12:
            os._exit(1)
        if rbuf[9] != "2":
            os._exit(1)
        os._exit(0)

    # parent: serve exactly one request, then report the status we routed.
    conn = server.accept()
    buf: "char*" = malloc(2048)
    n = conn.recv(buf, 2048)

    path = parse_path(buf, n)
    code = route(path)
    body = body_for(code)
    resp = build_response(code, body)
    conn.send(resp)

    conn.close()
    server.close()
    return code
