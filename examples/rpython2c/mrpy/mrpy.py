"""mrpy -- a MicroPython front end built through ShivyCX.

Compiled by py2c to C and linked against the MicroPython core, `mrpy script.py`
reads the script from disk and runs it on the embedded interpreter, the way
`python3 script.py` does. The file is read with ordinary (compiled-to-C) file
I/O; only the source string is handed to MicroPython, so no MicroPython
filesystem is needed.
"""
import sys
import micropython


def main() -> "int":
    if len(sys.argv) < 2:
        print("usage: mrpy <script.py>")
        return 1
    micropython.run_file(sys.argv[1])
    return 0
