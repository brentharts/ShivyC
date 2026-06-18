# rpython TCP networking

`socket_echo.py` does a real TCP round trip on `127.0.0.1` inside one binary,
lowered to BSD sockets with **no runtime**. The parent process is the server,
the `os.fork()` child is the client.

| rpython                       | C                                    |
|-------------------------------|--------------------------------------|
| `socket.socket(af, type)`     | `socket(af, type, 0)` (fd is `int`)  |
| `socket.AF_INET` / `SOCK_STREAM` / `SOL_SOCKET` / `SO_REUSEADDR` | integer constants |
| `s.bind((host, port))`        | `__py_sock_bind` (builds `sockaddr_in`) |
| `s.connect((host, port))`     | `__py_sock_connect` (builds `sockaddr_in`) |
| `s.listen(n)`                 | `listen`                             |
| `s.accept()`                  | `accept(fd, 0, 0)` (returns a new fd) |
| `s.send(data[, n])`           | `send` (length defaults to `strlen`) |
| `s.recv(buf, n)`              | `recv`                               |
| `s.setsockopt(l, o, v)`       | `setsockopt` (int option value)      |
| `s.close()`                   | `close`                              |
| `os.fork()` / `os._exit(n)`   | `fork` / `_exit`                     |

The `(host, port)` tuple is lowered into a `sockaddr_in` by an emitted helper
(`htons`/`inet_addr`); socket handles are tracked internally but emitted as
plain `int` file descriptors. Build and run:

```
python3 -m shivyc.main --no-cache socket_echo.py -o /tmp/echo && /tmp/echo
echo $?      # 5 = len("hello") received by the server
```
