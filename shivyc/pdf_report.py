"""--pdf: a LaTeX/PDF build report for ShivyCX.

Instead of scrolling preprocessor noise in a terminal, `--pdf` renders the whole
build as a document: an overview, a section per module (the rpython source of
truth and the generated C with its auto-inferred contracts), the whole-program
safety findings (bugs in red), a TikZ call-graph diagram, and -- in an appendix
-- the captured output of actually running the program. Defaults to /tmp.

Kept deliberately simple: plain `pdflatex`, a hand-laid TikZ graph (no graph-
drawing libraries), and a graceful fall-back to leaving the .tex on disk if
pdflatex is unavailable.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time


# --------------------------------------------------------------------------- #
# LaTeX helpers
# --------------------------------------------------------------------------- #
def _is_word_char(c):
    return c == "_" or ("0" <= c <= "9") or ("a" <= c <= "z") \
        or ("A" <= c <= "Z")


def _has_word(text, word):
    """`\\bword\\b` test without regex (translator regex subset can't do it)."""
    wlen = len(word)
    tlen = len(text)
    start = 0
    while True:
        idx = text.find(word, start)
        if idx < 0:
            return False
        before_ok = (idx == 0) or (not _is_word_char(text[idx - 1]))
        after = idx + wlen
        after_ok = (after >= tlen) or (not _is_word_char(text[after]))
        if before_ok and after_ok:
            return True
        start = idx + 1


def _esc(s):
    """Escape LaTeX specials in plain text."""
    s = str(s)
    for a, b in [("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"),
                 ("$", r"\$"), ("#", r"\#"), ("_", r"\_"), ("{", r"\{"),
                 ("}", r"\}"), ("~", r"\textasciitilde{}"),
                 ("^", r"\textasciicircum{}")]:
        s = s.replace(a, b)
    return s


def _verb(code, limit=120):
    """A listing block (lstlisting) for source code, truncated for sanity."""
    lines = code.splitlines()
    if len(lines) > limit:
        lines = lines[:limit] + ["... (%d more lines)" % (len(lines) - limit)]
    return "\\begin{lstlisting}\n" + "\n".join(lines) + "\n\\end{lstlisting}\n"


# --------------------------------------------------------------------------- #
# Transpile any .py inputs, keeping the Python source of truth alongside the C.
# --------------------------------------------------------------------------- #
def _prepare(files):
    """Return (c_files, modules). `modules` is a list of dicts describing each
    input for the report: name, kind ('py'|'c'), py source (if any), c source,
    and the inferred `assert` contract clauses found in the generated C."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tools = os.path.join(repo, "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    c_files, modules = [], []
    for f in files:
        if f.endswith(".py"):
            try:
                import py2c
            except Exception:
                continue
            d = tempfile.mkdtemp(prefix="shivyc_pdf_")
            cpath, err = py2c.transpile_file(f, d)
            if err or not cpath:
                modules.append({"name": os.path.basename(f), "kind": "py",
                                "py": open(f).read(), "c": "",
                                "contracts": [], "error": str(err)})
                continue
            code = open(cpath).read()
            code = code.replace('#include "shivyc_rt.h"\n', "")
            pre = []
            for sym, proto in [("malloc", "void *malloc(unsigned long);"),
                               ("free", "void free(void *);"),
                               ("printf", "int printf(const char *, ...);"),
                               ("sqrt", "double sqrt(double);"),
                               ("atoi", "int atoi(const char *);")]:
                if _has_word(code, sym):
                    pre.append(proto)
            code = ("\n".join(pre) + "\n" + code) if pre else code
            with open(cpath, "w") as fh:
                fh.write(code)
            contracts = [ln.strip() for ln in code.splitlines()
                         if ln.strip().startswith("assert ")]
            c_files.append(cpath)
            modules.append({"name": os.path.basename(f), "kind": "py",
                            "py": open(f).read(), "c": code,
                            "contracts": sorted(set(contracts))})
        elif f.endswith(".c"):
            c_files.append(f)
            src = ""
            try:
                src = open(f).read()
            except OSError:
                pass
            contracts = [ln.strip() for ln in src.splitlines()
                         if ln.strip().startswith("assert ")]
            modules.append({"name": os.path.basename(f), "kind": "c",
                            "py": "", "c": src,
                            "contracts": sorted(set(contracts))})
    return c_files, modules


# --------------------------------------------------------------------------- #
# TikZ call graph (simple BFS-layered layout, no graphdrawing libs)
# --------------------------------------------------------------------------- #
def _safe_node(name):
    out = "n"
    i = 0
    while i < len(name):
        c = name[i]
        if ("0" <= c <= "9") or ("a" <= c <= "z") or ("A" <= c <= "Z"):
            out = out + c
        else:
            out = out + "_"
        i += 1
    return out


def _tikz_callgraph(functions, edges):
    funcs = [f for f in functions if not f.startswith("__")]
    if not funcs:
        return "\\textit{(no user functions)}"
    fset = set(funcs)
    indeg = {f: 0 for f in funcs}
    for f in funcs:
        for c in edges.get(f, ()):
            if c in fset and c != f:
                indeg[c] += 1
    # layer by BFS from roots (indegree 0); fall back to all-on-one-layer
    roots = [f for f in funcs if indeg[f] == 0] or funcs[:1]
    layer = {}
    frontier, depth = list(roots), 0
    seen = set()
    while frontier:
        nxt = []
        for f in frontier:
            if f in seen:
                continue
            seen.add(f)
            layer[f] = depth
            for c in edges.get(f, ()):
                if c in fset and c not in seen:
                    nxt.append(c)
        frontier, depth = nxt, depth + 1
    for f in funcs:
        layer.setdefault(f, depth)
    rows = {}
    for f in funcs:
        rows.setdefault(layer[f], []).append(f)

    out = ["\\begin{tikzpicture}[>=latex, node distance=18mm,",
           "  every node/.style={draw, rounded corners, fill=blue!8,",
           "  font=\\ttfamily\\small, minimum height=7mm, inner sep=4pt}]"]
    pos = {}
    for ly in sorted(rows):
        for i, f in enumerate(sorted(rows[ly])):
            x = i * 3.4
            y = -ly * 2.0
            pos[f] = (x, y)
            out.append("\\node (%s) at (%.1f,%.1f) {%s};"
                       % (_safe_node(f), x, y, _esc(f)))
    out.append("\\begin{scope}[->, draw=gray]")
    for f in funcs:
        for c in edges.get(f, ()):
            if c in fset and c != f:
                out.append("\\draw (%s) -- (%s);"
                           % (_safe_node(f), _safe_node(c)))
    out.append("\\end{scope}")
    out.append("\\end{tikzpicture}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Run the program and capture its output for the appendix
# --------------------------------------------------------------------------- #
def _capture_run(files, args, out_dir):
    if sys.implementation.name != "shivyc":
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        binpath = os.path.join(out_dir, "shivyc_report_bin")
        cmd = [sys.executable, "-m", "shivyc.main", "--no-cache"] + list(files) \
            + ["-o", binpath]
        build = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
        if build.returncode != 0 or not os.path.exists(binpath):
            return None, (build.stdout + build.stderr)
        try:
            run = subprocess.run([binpath], capture_output=True, text=True,
                                 timeout=20)
            return run.returncode, run.stdout + run.stderr
        except Exception as e:                       # may need argv, may crash
            return None, "could not run automatically: %s" % e
    return 0, ""


# --------------------------------------------------------------------------- #
# Document assembly
# --------------------------------------------------------------------------- #
_PREAMBLE = r"""\documentclass[11pt]{article}
\usepackage[margin=1in]{geometry}
\usepackage{tikz}
\usetikzlibrary{arrows.meta}
\usepackage{xcolor}
\usepackage{listings}
\usepackage{hyperref}
\lstset{basicstyle=\ttfamily\footnotesize, breaklines=true,
  columns=fullflexible, frame=single, framesep=3pt, backgroundcolor=\color{black!3}}
\newcommand{\bug}[1]{\textcolor{red}{\textbf{#1}}}
\newcommand{\ok}[1]{\textcolor{green!55!black}{#1}}
\title{ShivyCX Build Report}
\author{generated by \texttt{shivyc.main --pdf}}
\date{%s}
\begin{document}
\maketitle
"""


def _build_tex(files, modules, prog, diags, autofree, run_rc, run_out):
    funcs = sorted(f for f in prog.functions if not f.startswith("__"))
    if sys.implementation.name != "shivyc":
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
    else:
        ts = ""
    parts = [_PREAMBLE % _esc(ts)]

    # Overview
    parts.append("\\section{Overview}")
    parts.append("Inputs: " + ", ".join("\\texttt{%s}" % _esc(m["name"])
                                         for m in modules) + ".\\\\")
    parts.append("User functions: %d. Call-graph edges: %d.\\\\"
                 % (len(funcs), sum(len(prog.edges.get(f, ())) for f in funcs)))
    nbugs = len(diags)
    if nbugs:
        parts.append("Safety: \\bug{%d issue(s) found} "
                     "(see Section~\\ref{sec:safety}).\\\\" % nbugs)
    else:
        parts.append("Safety: \\ok{no use-after-free, double-free or dangling "
                     "stack pointers found}.\\\\")

    # Call graph
    parts.append("\\section{Program structure}")
    parts.append(_tikz_callgraph(funcs, prog.edges))

    # Safety findings
    parts.append("\\section{Safety analysis}\\label{sec:safety}")
    if diags:
        parts.append("These are caught by whole-program analysis; "
                     "an ordinary \\texttt{gcc -O2} build compiles them "
                     "without error.\\par\\medskip")
        parts.append("\\begin{itemize}")
        for d in diags:
            parts.append("\\item \\bug{[%s]} in \\texttt{%s}: %s"
                         % (_esc(d.kind), _esc(d.func), _esc(d.detail)))
        parts.append("\\end{itemize}")
    else:
        parts.append("\\ok{No memory-safety issues detected.}\\par")
    leaks = {fn: items for fn, items in (autofree or {}).items() if items}
    if leaks:
        parts.append("\\medskip\\noindent Auto-free candidates "
                     "(local allocations the compiler can free at exit):")
        parts.append("\\begin{itemize}")
        for fn, items in sorted(leaks.items()):
            parts.append("\\item \\texttt{%s}: %d allocation(s)"
                         % (_esc(fn), len(items)))
        parts.append("\\end{itemize}")

    # Per-module sections (Python source of truth -> generated C + contracts)
    parts.append("\\section{Modules}")
    for m in modules:
        parts.append("\\subsection{\\texttt{%s}}" % _esc(m["name"]))
        if m["contracts"]:
            parts.append("Auto-inferred contracts:")
            parts.append("\\begin{itemize}")
            for c in m["contracts"]:
                parts.append("\\item \\texttt{%s}" % _esc(c))
            parts.append("\\end{itemize}")
        if m["kind"] == "py" and m["py"]:
            parts.append("\\paragraph{rpython source (source of truth).}")
            parts.append(_verb(m["py"]))
            parts.append("\\paragraph{Generated C.}")
            parts.append(_verb(m["c"]))
        elif m["c"]:
            parts.append(_verb(m["c"]))

    # Appendix: run output
    parts.append("\\appendix")
    parts.append("\\section{Program output}")
    if run_rc is not None:
        parts.append("Exit code: \\texttt{%s}." % run_rc)
    if run_out and run_out.strip():
        parts.append(_verb(run_out, limit=200))
    else:
        parts.append("\\textit{(no captured output)}")

    parts.append("\\end{document}")
    return "\n".join(parts)


def run(files, args, out_dir):
    if sys.implementation.name != "shivyc":
        import shivyc.memory_safety as memory_safety

        out_dir = out_dir or "/tmp"
        os.makedirs(out_dir, exist_ok=True)

        c_files, modules = _prepare(files)
        # Analyze the original inputs: load_program transpiles .py itself (keeping
        # the runtime header) so rpython allocations are tracked; _prepare's
        # header-stripped C is only for source display.
        prog, diags, autofree, _ = memory_safety.analyze_program(files, args)
        run_rc, run_out = _capture_run(files, args, out_dir)

        tex = _build_tex(files, modules, prog, diags, autofree, run_rc, run_out)
        tex_path = os.path.join(out_dir, "shivyc_report.tex")
        with open(tex_path, "w") as f:
            f.write(tex)

        if not shutil.which("pdflatex"):
            print("wrote %s (pdflatex not found; install it to render the PDF)"
                  % tex_path)
            return 0
        for _ in range(2):                            # twice to settle \ref
            p = subprocess.run(["pdflatex", "-interaction=nonstopmode",
                                "-halt-on-error", "-output-directory", out_dir,
                                tex_path], capture_output=True, text=True)
        pdf_path = os.path.join(out_dir, "shivyc_report.pdf")
        if os.path.exists(pdf_path):
            print("wrote %s" % pdf_path)
            if diags:
                print("  (%d safety issue(s) highlighted in red)" % len(diags))
            return 0
        print("pdflatex failed; .tex left at %s" % tex_path)
        sys.stderr.write(p.stdout[-1500:] + p.stderr[-500:])
        return 1
    return 0
