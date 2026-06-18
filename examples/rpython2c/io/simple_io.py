"""Simple I/O in rpython, lowered to plain C stdio (no runtime):

    open(path, mode)  -> fopen        f.write(s) -> fputs
    f.readline()      -> fgets        f.close()  -> fclose
    input()           -> fgets(stdin) (newline stripped)
    os.system(cmd)    -> system       print(s)   -> puts   len(s) -> strlen

Run:  echo "world" | python3 -m shivyc.main --no-cache simple_io.py -o /tmp/io && /tmp/io
The exit code is the length of the line read from stdin, so it is observable
without relying on captured stdout.
"""
import sys, os


def main() -> int:
    # 1. write a file
    f = open("/tmp/rpy_io_demo.txt", "w")
    f.write("hello from rpython\n")
    f.write("second line\n")
    f.close()

    # 2. read the first line back and echo it
    g = open("/tmp/rpy_io_demo.txt", "r")
    first = g.readline()
    g.close()
    print(first)

    # 3. shell out
    os.system("echo ran os.system from rpython")

    # 4. read a line from stdin, store it in another file
    line = input()
    h = open("/tmp/rpy_io_echo.txt", "w")
    h.write(line)
    h.write("\n")
    h.close()
    print(line)

    return len(line) % 100
