"""sys.argv and sys.implementation.name in rpython.

The ShivyCX translator reports `sys.implementation.name == 'shivyc'`, so a
source file can fence off host-CPython-only code:

    if sys.implementation.name != 'shivyc':
        ...code the translator skips entirely...

That fenced block is *not* lowered to C -- it never reaches the backend -- so
it may freely use things the C runtime has no notion of (here, importing and
using the `platform` module). Under CPython the same file still runs normally.

`sys.argv` lowers to the C command line: `sys.argv[i]` -> `argv[i]`, and
`len(sys.argv)` -> `argc`. Reading argv makes the emitted `main` take
`(int argc, char** argv)`.
"""
import sys


def runtime_tag() -> str:
    if sys.implementation.name != 'shivyc':
        # Host-only: uses a module the C backend cannot translate. Skipped
        # wholesale at translation time, so it is fine that `platform` has no
        # rpython lowering.
        import platform
        return "host-" + platform.python_implementation()
    return "shivyc"


def main() -> int:
    tag = runtime_tag()             # "shivyc" once the host branch is dropped
    argc = len(sys.argv)            # -> argc
    if sys.argv:                    # any argv present -> argc > 0
        prog = sys.argv[0]          # -> argv[0]; the program name
        print(prog)
    print(tag)
    return len(tag) + argc          # 6 + 1 == 7 when run with no extra args


if __name__ == "__main__":
    sys.exit(main())
