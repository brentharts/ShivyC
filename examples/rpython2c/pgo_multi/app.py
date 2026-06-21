"""Multi-file profile-guided auto-typing.

    python3 tools/py2c.py app.py hist.py -fprofile-generate --out /tmp/d
    python3 -m shivyc.main app.py hist.py -o app          # boxed (default)
    RPY_PROFILE_GENERATE=1 python3 -m shivyc.main app.py hist.py -o app

One profiling run (entry = app.py) instruments *both* modules, so `counts`
inside hist.histogram is typed even though it lives in another file.
"""
from hist import histogram


def main() -> int:
    data = [5, 2, 5, 5, 2, 9, 5]
    return histogram(data)         # 5 appears 4x -> peak 4 -> 44


if __name__ == "__main__":
    import sys
    sys.exit(main())
