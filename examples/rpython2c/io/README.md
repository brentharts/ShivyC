# rpython simple I/O

`simple_io.py` shows rpython lowering directly to C stdio with **no runtime**:

| rpython              | C                                   |
|----------------------|-------------------------------------|
| `open(path, mode)`   | `fopen`                             |
| `f.write(s)`         | `fputs`                             |
| `f.readline()` / `f.read()` | `fgets` into a fresh buffer  |
| `f.close()`          | `fclose`                            |
| `input()`            | `fgets(stdin)` (trailing newline stripped) |
| `os.system(cmd)`     | `system`                            |
| `print(s)`           | `puts`                              |
| `len(s)`             | `strlen`                            |

File handles are opaque `void*`; `sys.stdin/stdout/stderr` map to the libc
streams. Build and run:

```
echo "world" | python3 -m shivyc.main --no-cache simple_io.py -o /tmp/io && /tmp/io
echo $?            # 5 = len("world")
```
