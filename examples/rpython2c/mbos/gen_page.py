#!/usr/bin/env python3
"""gen_page.py -- embed page.html into page_html.h as a C string.

The bare-metal kernel has no filesystem, so the page it renders is compiled in.
This is the mbos equivalent of www2json.py emitting page_data.py: HTML stays the
human-editable source of truth, and a generated form is what the binary carries.
"""
import sys

def main(html_path, out_path):
    with open(html_path, "rb") as f:
        data = f.read()
    out = ["/* generated from page.html by gen_page.py -- do not edit */",
           "static const char PAGE_HTML[] ="]
    for line in data.decode("utf-8").splitlines():
        esc = line.replace("\\", "\\\\").replace('"', '\\"')
        out.append('    "%s\\n"' % esc)
    out.append("    ;")
    with open(out_path, "w") as f:
        f.write("\n".join(out) + "\n")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
