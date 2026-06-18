"""Token-level handling of GCC alias/weak attributes.

musl creates weak aliases with

    #define weak_alias(old, new) \\
        extern __typeof(old) new __attribute__((__weak__, __alias__(#old)))

ShivyC cannot parse that declaration (it needs ``__typeof``), but an alias only
needs the two symbol names. This pass runs after preprocessing and:

* recognizes ``__attribute__((... __alias__("target") ...))``, records the
  alias ``(declared_name, target, is_weak)``, and removes the whole alias
  declaration (which we cannot parse) from the token stream, and
* strips any other ``__attribute__((...))`` occurrences, which ShivyC ignores.

The recorded aliases are emitted as ``.weak`` / ``.set`` assembler directives.
"""

import shivyc.token_kinds as token_kinds


def _spell(t: "Token"):
    if t.rep:
        return t.rep
    if isinstance(t.content, str):
        return t.content
    return str(t.kind)


def _ident(t: "Token"):
    return t.content if t.kind is token_kinds.identifier else None


def _norm(name):
    """Normalize an attribute name: __weak__ -> weak, __alias__ -> alias."""
    return name.strip("_") if name else name


def extract_aliases(tokens: "list[Token]"):
    """Return (filtered_tokens, aliases).

    aliases is a list of (alias_name, target_name, is_weak) tuples.
    """
    aliases = []
    result = []
    i = 0
    n = len(tokens)

    while i < n:
        t = tokens[i]
        name = _ident(t)
        if (name in ("__attribute__", "__attribute")
                and i + 1 < n and _spell(tokens[i + 1]) == "("):
            # Find the matching close paren of the (doubled) attribute parens.
            depth = 0
            j = i + 1
            while j < n:
                s = _spell(tokens[j])
                if s == "(":
                    depth += 1
                elif s == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1

            target, weak = _parse_attr(tokens[i + 1:j])
            if target is not None:
                alias_name = _last_ident(result)
                if alias_name:
                    aliases.append((alias_name, target, weak))
                if _decl_has_typeof(result):
                    # The declaration (e.g. musl's `extern __typeof(old) new
                    # ...`) is unparseable, so drop it entirely; the .weak/.set
                    # directives below make the alias resolve at link time.
                    _pop_to_boundary(result)
                    k = j + 1
                    while k < n and _spell(tokens[k]) != ";":
                        k += 1
                    i = k + 1
                    continue
                else:
                    # Declaration is parseable; just strip the attribute so the
                    # symbol stays declared and usable in this unit.
                    i = j + 1
                    continue
            else:
                # Generic (or weak-only) attribute: strip the tokens.
                i = j + 1
                continue

        result.append(t)
        i += 1

    return result, aliases


def _parse_attr(toks):
    """Scan attribute tokens for alias/weak; return (target_or_None, weak)."""
    target = None
    weak = False
    for k, t in enumerate(toks):
        nm = _norm(_ident(t))
        if nm == "weak":
            weak = True
        elif nm == "alias" and k + 2 < len(toks):
            # alias ( "target" )
            target = _strip_quotes(_spell(toks[k + 2]))
    return target, weak


def _strip_quotes(s):
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def _last_ident(result):
    for t in reversed(result):
        nm = _ident(t)
        if nm:
            return nm
    return None


def _decl_has_typeof(result):
    """Whether the in-progress declaration uses (un-parseable) typeof."""
    for t in reversed(result):
        if _spell(t) in (";", "{", "}"):
            break
        if _ident(t) in ("typeof", "__typeof", "__typeof__"):
            return True
    return False


def _pop_to_boundary(result):
    """Pop tokens off `result` until a declaration boundary is on top."""
    while result and _spell(result[-1]) not in (";", "{", "}"):
        result.pop()
