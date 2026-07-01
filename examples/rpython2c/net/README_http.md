# rpython HTTP/1.1 server

`http_server.py` is a real HTTP/1.1 server compiled straight to a
dependency-free native binary by `py2c` (BSD sockets, no runtime). It builds on
`socket_echo.py`: the parent process is the server, the `os.fork()` child is a
minimal HTTP client, and the whole exchange happens over real TCP on
`127.0.0.1` inside one binary.

What makes it more than an echo is that everything *above* the socket layer is
ordinary rpython lowered to C:

| rpython construct                         | role                                  |
|-------------------------------------------|---------------------------------------|
| `parse_path(req, n)` — index/compare `char*`, build `str` | pull the request-target out of the request line |
| `route(path)` — `str == str`              | map a path to a status code (only `/` exists) |
| `build_response(code, body)` — `str` concat, `str(int)`, `len(str)` | assemble status line + headers + body with a computed `Content-Length` |
| `socket.*` / `os.fork` / `os._exit`       | lowered to BSD sockets + `fork`/`_exit` (see `net/README.md`) |

So the request parser, the router, and the response builder are all compiled by
the same whole-program pipeline as the interpreter and the parser front end;
only `send`/`recv`/`accept` bottom out in libc.

## Build and run

```
python3 -m shivyc.main --no-cache http_server.py -o /tmp/httpd && /tmp/httpd
echo $?      # 200  — client requested "/", server routed and served 200 OK
```

The exit code is the HTTP status the server routed, so the full round trip
(accept → parse → route → respond, plus a client that read a valid response) is
observable without captured output. The router genuinely branches: point the
client at a path other than `/` and the code becomes `404 & 0xFF = 148`:

```
sed 's#GET / HTTP/1.0#GET /nope HTTP/1.0#' http_server.py > /tmp/h404.py
python3 -m shivyc.main --no-cache /tmp/h404.py -o /tmp/h404 && /tmp/h404
echo $?      # 148
```

## Note: `fork`/`_exit` prototypes

The socket prelude in `tools/py2c.py` forward-declares the BSD socket functions
so the emitted C is warning-clean under strict compilers. `fork` and `_exit`
(used by the self-testing fork idiom that `socket_echo.py` introduced) are
declared there too — without the prototypes, `clang` rejects the implicit
declarations as hard errors even though `gcc` only warns.
