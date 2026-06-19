"""Multi-file rpython program.

Built as one translation unit:

    python3 -m shivyc.main app.py geom.py -o app

`from geom import ...` resolves `geom` against the input files' directory, so
the calls become direct C calls into geom's translated code (one shared runtime,
no dynamic import, whole call graph visible in a single invocation).
"""
from geom import area, perimeter


def main() -> int:
    return area(4, 5) + perimeter(4, 5)      # 20 + 18 = 38
