"""A CPython-`ast`-compatible facade over the rast PEG parser.

py2c.py is written against the standard library `ast` module: it calls
`ast.parse`, walks the tree with `ast.walk` / `ast.iter_child_nodes`, tests node
kinds with `isinstance(n, ast.Name)` &c., and reads standard fields (`.id`,
`.func`, `.body`, ...).  minipy has no `ast` module, so to run py2c on minipy we
reconstruct that surface here: rast parses the source into its own Node tree,
and `_conv` rewrites that tree into node objects whose classes and `_fields`
mirror CPython's.  The classes are real (so `isinstance` works by class id), and
`_fields` is stored per-instance because minipy has no class-level attributes.

Design constraints (all verified against the minipy backend):
  * no class-level attributes  -> every __init__ sets self._fields;
  * no setattr()               -> nodes are built by direct attribute writes,
                                   so NodeTransformer write-back is deferred;
  * getattr()/getattr(,default)/hasattr() work -> iter_fields/walk are generic.

This module runs under CPython too, which is how it is differentially tested
against the real `ast` module (see minast_test.py).
"""

# rast is the parser + Node type.  Under CPython these import directly; under
# minipy the same names are provided by the compile-time module linker.
from rast import parse_python, is_node


# ---------------------------------------------------------------------------
# Base class + node classes.  Each __init__ records self._fields (minipy has no
# class attributes) and the location quad, then its own fields.
# ---------------------------------------------------------------------------

class AST:
    def _init_loc(self):
        self.lineno = 0
        self.col_offset = 0
        self.end_lineno = 0
        self.end_col_offset = 0


class Module(AST):
    def __init__(self, body):
        self._fields = ("body", "type_ignores")
        self.body = body
        self.type_ignores = []


class FunctionDef(AST):
    def __init__(self, name, args, body, decorator_list, returns):
        self._fields = ("name", "args", "body", "decorator_list", "returns",
                        "type_comment", "type_params")
        self._init_loc()
        self.name = name
        self.args = args
        self.body = body
        self.decorator_list = decorator_list
        self.returns = returns
        self.type_comment = None
        self.type_params = []


class ClassDef(AST):
    def __init__(self, name, bases, keywords, body, decorator_list):
        self._fields = ("name", "bases", "keywords", "body", "decorator_list",
                        "type_params")
        self._init_loc()
        self.name = name
        self.bases = bases
        self.keywords = keywords
        self.body = body
        self.decorator_list = decorator_list
        self.type_params = []


class Return(AST):
    def __init__(self, value):
        self._fields = ("value",)
        self._init_loc()
        self.value = value


class Assign(AST):
    def __init__(self, targets, value):
        self._fields = ("targets", "value", "type_comment")
        self._init_loc()
        self.targets = targets
        self.value = value
        self.type_comment = None


class AugAssign(AST):
    def __init__(self, target, op, value):
        self._fields = ("target", "op", "value")
        self._init_loc()
        self.target = target
        self.op = op
        self.value = value


class AnnAssign(AST):
    def __init__(self, target, annotation, value, simple):
        self._fields = ("target", "annotation", "value", "simple")
        self._init_loc()
        self.target = target
        self.annotation = annotation
        self.value = value
        self.simple = simple


class For(AST):
    def __init__(self, target, itr, body, orelse):
        self._fields = ("target", "iter", "body", "orelse", "type_comment")
        self._init_loc()
        self.target = target
        self.iter = itr
        self.body = body
        self.orelse = orelse
        self.type_comment = None


class While(AST):
    def __init__(self, test, body, orelse):
        self._fields = ("test", "body", "orelse")
        self._init_loc()
        self.test = test
        self.body = body
        self.orelse = orelse


class If(AST):
    def __init__(self, test, body, orelse):
        self._fields = ("test", "body", "orelse")
        self._init_loc()
        self.test = test
        self.body = body
        self.orelse = orelse


class Expr(AST):
    def __init__(self, value):
        self._fields = ("value",)
        self._init_loc()
        self.value = value


class Pass(AST):
    def __init__(self):
        self._fields = ()
        self._init_loc()


class Break(AST):
    def __init__(self):
        self._fields = ()
        self._init_loc()


class Continue(AST):
    def __init__(self):
        self._fields = ()
        self._init_loc()


class Global(AST):
    def __init__(self, names):
        self._fields = ("names",)
        self._init_loc()
        self.names = names


class Nonlocal(AST):
    def __init__(self, names):
        self._fields = ("names",)
        self._init_loc()
        self.names = names


class Import(AST):
    def __init__(self, names):
        self._fields = ("names",)
        self._init_loc()
        self.names = names


class ImportFrom(AST):
    def __init__(self, module, names, level):
        self._fields = ("module", "names", "level")
        self._init_loc()
        self.module = module
        self.names = names
        self.level = level


class alias(AST):
    def __init__(self, name, asname):
        self._fields = ("name", "asname")
        self.name = name
        self.asname = asname


# ---- expressions -----------------------------------------------------------

class BoolOp(AST):
    def __init__(self, op, values):
        self._fields = ("op", "values")
        self._init_loc()
        self.op = op
        self.values = values


class BinOp(AST):
    def __init__(self, left, op, right):
        self._fields = ("left", "op", "right")
        self._init_loc()
        self.left = left
        self.op = op
        self.right = right


class UnaryOp(AST):
    def __init__(self, op, operand):
        self._fields = ("op", "operand")
        self._init_loc()
        self.op = op
        self.operand = operand


class Compare(AST):
    def __init__(self, left, ops, comparators):
        self._fields = ("left", "ops", "comparators")
        self._init_loc()
        self.left = left
        self.ops = ops
        self.comparators = comparators


class Call(AST):
    def __init__(self, func, args, keywords):
        self._fields = ("func", "args", "keywords")
        self._init_loc()
        self.func = func
        self.args = args
        self.keywords = keywords


class keyword(AST):
    def __init__(self, arg, value):
        self._fields = ("arg", "value")
        self.arg = arg
        self.value = value


class IfExp(AST):
    def __init__(self, test, body, orelse):
        self._fields = ("test", "body", "orelse")
        self._init_loc()
        self.test = test
        self.body = body
        self.orelse = orelse


class Constant(AST):
    def __init__(self, value):
        self._fields = ("value", "kind")
        self._init_loc()
        self.value = value
        self.kind = None


class Name(AST):
    def __init__(self, id_, ctx):
        self._fields = ("id", "ctx")
        self._init_loc()
        self.id = id_
        self.ctx = ctx


class Attribute(AST):
    def __init__(self, value, attr, ctx):
        self._fields = ("value", "attr", "ctx")
        self._init_loc()
        self.value = value
        self.attr = attr
        self.ctx = ctx


class Subscript(AST):
    def __init__(self, value, slce, ctx):
        self._fields = ("value", "slice", "ctx")
        self._init_loc()
        self.value = value
        self.slice = slce
        self.ctx = ctx


class Starred(AST):
    def __init__(self, value, ctx):
        self._fields = ("value", "ctx")
        self._init_loc()
        self.value = value
        self.ctx = ctx


class Slice(AST):
    def __init__(self, lower, upper, step):
        self._fields = ("lower", "upper", "step")
        self._init_loc()
        self.lower = lower
        self.upper = upper
        self.step = step


class List(AST):
    def __init__(self, elts, ctx):
        self._fields = ("elts", "ctx")
        self._init_loc()
        self.elts = elts
        self.ctx = ctx


class Tuple(AST):
    def __init__(self, elts, ctx):
        self._fields = ("elts", "ctx")
        self._init_loc()
        self.elts = elts
        self.ctx = ctx


class Set(AST):
    def __init__(self, elts):
        self._fields = ("elts",)
        self._init_loc()
        self.elts = elts


class Dict(AST):
    def __init__(self, keys, values):
        self._fields = ("keys", "values")
        self._init_loc()
        self.keys = keys
        self.values = values


class comprehension(AST):
    def __init__(self, target, itr, ifs, is_async):
        self._fields = ("target", "iter", "ifs", "is_async")
        self.target = target
        self.iter = itr
        self.ifs = ifs
        self.is_async = is_async


class ListComp(AST):
    def __init__(self, elt, generators):
        self._fields = ("elt", "generators")
        self._init_loc()
        self.elt = elt
        self.generators = generators


class SetComp(AST):
    def __init__(self, elt, generators):
        self._fields = ("elt", "generators")
        self._init_loc()
        self.elt = elt
        self.generators = generators


class GeneratorExp(AST):
    def __init__(self, elt, generators):
        self._fields = ("elt", "generators")
        self._init_loc()
        self.elt = elt
        self.generators = generators


class DictComp(AST):
    def __init__(self, key, value, generators):
        self._fields = ("key", "value", "generators")
        self._init_loc()
        self.key = key
        self.value = value
        self.generators = generators


class arg(AST):
    def __init__(self, argname, annotation):
        self._fields = ("arg", "annotation", "type_comment")
        self._init_loc()
        self.arg = argname
        self.annotation = annotation
        self.type_comment = None


class arguments(AST):
    def __init__(self, posonlyargs, args, vararg, kwonlyargs, kw_defaults,
                 kwarg, defaults):
        self._fields = ("posonlyargs", "args", "vararg", "kwonlyargs",
                        "kw_defaults", "kwarg", "defaults")
        self.posonlyargs = posonlyargs
        self.args = args
        self.vararg = vararg
        self.kwonlyargs = kwonlyargs
        self.kw_defaults = kw_defaults
        self.kwarg = kwarg
        self.defaults = defaults


# ---- operator / context singleton classes (no fields; used via isinstance) --

class Load(AST):
    def __init__(self):
        self._fields = ()

class Store(AST):
    def __init__(self):
        self._fields = ()

class Del(AST):
    def __init__(self):
        self._fields = ()

class And(AST):
    def __init__(self):
        self._fields = ()

class Or(AST):
    def __init__(self):
        self._fields = ()

class Add(AST):
    def __init__(self):
        self._fields = ()

class Sub(AST):
    def __init__(self):
        self._fields = ()

class Mult(AST):
    def __init__(self):
        self._fields = ()

class Div(AST):
    def __init__(self):
        self._fields = ()

class FloorDiv(AST):
    def __init__(self):
        self._fields = ()

class Mod(AST):
    def __init__(self):
        self._fields = ()

class Pow(AST):
    def __init__(self):
        self._fields = ()

class LShift(AST):
    def __init__(self):
        self._fields = ()

class RShift(AST):
    def __init__(self):
        self._fields = ()

class BitOr(AST):
    def __init__(self):
        self._fields = ()

class BitXor(AST):
    def __init__(self):
        self._fields = ()

class BitAnd(AST):
    def __init__(self):
        self._fields = ()

class USub(AST):
    def __init__(self):
        self._fields = ()

class UAdd(AST):
    def __init__(self):
        self._fields = ()

class Invert(AST):
    def __init__(self):
        self._fields = ()

class Not(AST):
    def __init__(self):
        self._fields = ()

class Eq(AST):
    def __init__(self):
        self._fields = ()

class NotEq(AST):
    def __init__(self):
        self._fields = ()

class Lt(AST):
    def __init__(self):
        self._fields = ()

class LtE(AST):
    def __init__(self):
        self._fields = ()

class Gt(AST):
    def __init__(self):
        self._fields = ()

class GtE(AST):
    def __init__(self):
        self._fields = ()

class In(AST):
    def __init__(self):
        self._fields = ()

class NotIn(AST):
    def __init__(self):
        self._fields = ()

class Is(AST):
    def __init__(self):
        self._fields = ()

class IsNot(AST):
    def __init__(self):
        self._fields = ()


# ---------------------------------------------------------------------------
# Generic tree helpers (mirror ast.*), built on getattr + per-instance _fields.
# ---------------------------------------------------------------------------

def iter_fields(node):
    out = []
    for name in node._fields:
        if hasattr(node, name):
            out.append((name, getattr(node, name)))
    return out


def iter_child_nodes(node):
    out = []
    for pair in iter_fields(node):
        val = pair[1]
        if isinstance(val, AST):
            out.append(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, AST):
                    out.append(item)
    return out


def walk(node):
    out = []
    todo = [node]
    while todo:
        cur = todo.pop()
        out.append(cur)
        kids = iter_child_nodes(cur)
        for k in kids:
            todo.append(k)
    return out


def copy_location(new_node, old_node):
    new_node.lineno = getattr(old_node, "lineno", 0)
    new_node.col_offset = getattr(old_node, "col_offset", 0)
    new_node.end_lineno = getattr(old_node, "end_lineno", 0)
    new_node.end_col_offset = getattr(old_node, "end_col_offset", 0)
    return new_node


def fix_missing_locations(node):
    todo = [node]
    while todo:
        cur = todo.pop()
        if not hasattr(cur, "lineno"):
            cur.lineno = 0
            cur.col_offset = 0
            cur.end_lineno = 0
            cur.end_col_offset = 0
        for k in iter_child_nodes(cur):
            todo.append(k)
    return node


# ---------------------------------------------------------------------------
# Operator lookups: rast stores operators as leaf strings; CPython wants a
# singleton instance per operator on the node's .op / .ops.
# ---------------------------------------------------------------------------

_CMP_OPS = ("<", ">", "<=", ">=", "==", "!=", "<>", "in", "not in", "is",
            "is not")


def _binop_cls(op):
    if op == "+": return Add()
    if op == "-": return Sub()
    if op == "*": return Mult()
    if op == "/": return Div()
    if op == "//": return FloorDiv()
    if op == "%": return Mod()
    if op == "**": return Pow()
    if op == "|": return BitOr()
    if op == "^": return BitXor()
    if op == "&": return BitAnd()
    if op == "<<": return LShift()
    if op == ">>": return RShift()
    raise ValueError("unknown binop: " + op)


def _cmpop_cls(op):
    if op == "<": return Lt()
    if op == ">": return Gt()
    if op == "<=": return LtE()
    if op == ">=": return GtE()
    if op == "==": return Eq()
    if op == "!=": return NotEq()
    if op == "<>": return NotEq()
    if op == "in": return In()
    if op == "not in": return NotIn()
    if op == "is": return Is()
    if op == "is not": return IsNot()
    raise ValueError("unknown cmpop: " + op)


def _augop_cls(op):
    # op is like "+=" ; drop trailing "=" and reuse _binop_cls
    return _binop_cls(op[:-1])


def _is(node, nm):
    return is_node(node) and node.name == nm


def _leaf(node):
    # a rast NAME/NUMBER/STRING wraps a single leaf child; an empty string
    # literal ("") yields a childless STRING, so fall back to "".
    if node.children:
        return node.children[0]
    return ""


# ---------------------------------------------------------------------------
# Expression conversion.
# ---------------------------------------------------------------------------

def _conv(node):
    nm = node.name
    if nm == "NAME":
        v = _leaf(node)
        if v == "None":
            return Constant(None)
        if v == "True":
            return Constant(True)
        if v == "False":
            return Constant(False)
        return Name(v, Load())
    if nm == "NUMBER":
        return Constant(_leaf(node))
    if nm == "STRING":
        return Constant(_leaf(node))
    if nm == "__getattr__":
        return Attribute(_conv(node.children[0]), _leaf(node.children[1]),
                         Load())
    if nm == "__call__":
        return _conv_call(node)
    if nm == "__binary__":
        return _conv_binary(node)
    if nm == "__getitem__":
        return _conv_subscript(node)
    if nm == "or_test":
        return BoolOp(Or(), _conv_list(node.children))
    if nm == "and_test":
        return BoolOp(And(), _conv_list(node.children))
    if nm == "not_test":
        return UnaryOp(Not(), _conv(node.children[0]))
    if nm == "factor":
        return UnaryOp(_unaryop_cls(node.children[0]),
                       _conv(node.children[1]))
    if nm == "test":
        return _conv_ternary(node)
    if nm == "listmaker":
        return _conv_listmaker(node)
    if nm == "tuple":
        return Tuple(_conv_list(node.children), Load())
    if nm == "exprlist":
        return Tuple(_conv_list(node.children), Load())
    if nm == "testlist":
        return Tuple(_conv_list(node.children), Load())
    if nm == "STRINGS":
        s = ""
        for c in node.children:
            s = s + _leaf(c)
        return Constant(s)
    if nm == "generator":
        inner = node.children[0]
        if _is(inner, "testlist_comp"):
            return _conv(inner)
        return GeneratorExp(_conv(inner), _conv_comp_generators(node))
    if nm == "no_param":
        return Tuple([], Load())
    if nm == "testlist_comp":
        if _has(node, "list_for"):
            return GeneratorExp(_conv(node.children[0]),
                                _conv_comp_generators(node))
        return Tuple(_conv_list(node.children), Load())
    if nm == "listcomp_arg":
        has_for = False
        for c in node.children:
            if _is(c, "list_for"):
                has_for = True
        if has_for:
            return GeneratorExp(_conv(node.children[0]),
                                _conv_comp_generators(node))
        return _conv(node.children[0])
    if nm == "dictmaker":
        return _conv_dictmaker(node)
    if nm == "setmaker":
        return _conv_setmaker(node)
    if nm == "lambdef":
        raise ValueError("lambda not yet converted")
    raise ValueError("cannot convert expr node: " + nm)


def _unaryop_cls(op):
    if op == "-": return USub()
    if op == "+": return UAdd()
    if op == "~": return Invert()
    raise ValueError("unknown unaryop: " + op)


def _conv_list(children):
    out = []
    for c in children:
        if is_node(c):
            out.append(_conv(c))
    return out


def _fill_arglist(arglist, args, kwargs):
    for a in arglist.children:
        if _is(a, "keyword_arg"):
            kwargs.append(keyword(_leaf(a.children[0]), _conv(a.children[1])))
        elif _is(a, "star_arg"):
            args.append(Starred(_conv(a.children[0]), Load()))
        elif _is(a, "remaining_args"):
            args.append(Starred(_conv(a.children[0]), Load()))
        elif _is(a, "kwargs"):
            kwargs.append(keyword(None, _conv(a.children[0])))
        elif is_node(a):
            args.append(_conv(a))


def _conv_call(node):
    func = _conv(node.children[0])
    args = []
    kwargs = []
    if len(node.children) > 1:
        _fill_arglist(node.children[1], args, kwargs)
    return Call(func, args, kwargs)


def _conv_binary(node):
    op = node.children[0]
    if _in_tuple(op, _CMP_OPS):
        return _conv_compare(node)
    return BinOp(_conv(node.children[1]), _binop_cls(op),
                 _conv(node.children[2]))


def _in_tuple(x, tup):
    for t in tup:
        if t == x:
            return True
    return False


def _rev(xs):
    out = []
    i = len(xs) - 1
    while i >= 0:
        out.append(xs[i])
        i = i - 1
    return out


def _conv_compare(node):
    # flatten the left-nested chain a<b<=c (nested __binary__) into one Compare
    ops = []
    comps = []
    cur = node
    base = None
    while True:
        op = cur.children[0]
        ops.append(op)
        comps.append(cur.children[2])
        left = cur.children[1]
        if _is(left, "__binary__") and _in_tuple(left.children[0], _CMP_OPS):
            cur = left
        else:
            base = left
            break
    ops = _rev(ops)
    comps = _rev(comps)
    op_objs = []
    for o in ops:
        op_objs.append(_cmpop_cls(o))
    comp_objs = []
    for c in comps:
        comp_objs.append(_conv(c))
    return Compare(_conv(base), op_objs, comp_objs)


def _conv_subscript(node):
    val = _conv(node.children[0])
    subs = []
    i = 1
    while i < len(node.children):
        c = node.children[i]
        if is_node(c) and c.name == "subscript":
            inner = c.children[0]
            if _is(inner, "slice"):
                subs.append(_conv_slice(inner))
            else:
                subs.append(_conv(inner))
        i = i + 1
    if len(subs) == 1:
        return Subscript(val, subs[0], Load())
    return Subscript(val, Tuple(subs, Load()), Load())


def _conv_slice(node):
    lower = None
    upper = None
    step = None
    for part in node.children:
        if _is(part, "start") and part.children:
            lower = _conv(part.children[0])
        elif _is(part, "stop") and part.children:
            upper = _conv(part.children[0])
        elif _is(part, "step") and part.children:
            step = _conv(part.children[0])
    return Slice(lower, upper, step)


def _conv_ternary(node):
    # test := or_test "if" or_test "else" test  ->  body if test else orelse
    body = _conv(node.children[0])
    test = _conv(node.children[1])
    orelse = _conv(node.children[2])
    return IfExp(test, body, orelse)


def _has(node, nm):
    for c in node.children:
        if is_node(c) and c.name == nm:
            return True
    return False


def _conv_listmaker(node):
    if len(node.children) == 1 and _is(node.children[0], "listcomp"):
        lc = node.children[0]
        return ListComp(_conv(lc.children[0]), _conv_comp_generators(lc))
    if _has(node, "list_for"):
        return ListComp(_conv(node.children[0]), _conv_comp_generators(node))
    return List(_conv_list(node.children), Load())


def _conv_setmaker(node):
    kids = []
    for c in node.children:
        if is_node(c):
            kids.append(c)
    if len(kids) == 1 and _is(kids[0], "listcomp"):
        lc = kids[0]
        return SetComp(_conv(lc.children[0]), _conv_comp_generators(lc))
    if _has(node, "list_for"):
        return SetComp(_conv(kids[0]), _conv_comp_generators(node))
    out = []
    for c in kids:
        out.append(_conv(c))
    return Set(out)


def _conv_dictmaker(node):
    if len(node.children) == 1 and _is(node.children[0], "dictcomp"):
        dc = node.children[0]
        return DictComp(_conv(dc.children[0]), _conv(dc.children[1]),
                        _conv_comp_generators(dc))
    if _has(node, "list_for"):
        return DictComp(_conv(node.children[0]), _conv(node.children[1]),
                        _conv_comp_generators(node))
    keys = []
    vals = []
    for pair in node.children:
        keys.append(_conv(pair.children[0]))
        vals.append(_conv(pair.children[1]))
    return Dict(keys, vals)


def _conv_comp_generators(comp):
    # comp children: elt, then a run of list_for / list_if (dictcomp: key,val,...)
    gens = []
    cur = None
    for c in comp.children:
        if _is(c, "list_for"):
            target = _store(_conv(c.children[0]))
            itr = _conv(c.children[1])
            cur = comprehension(target, itr, [], 0)
            gens.append(cur)
        elif _is(c, "list_if"):
            cur.ifs.append(_conv(c.children[0]))
    return gens


# ---------------------------------------------------------------------------
# Statement conversion.
# ---------------------------------------------------------------------------

def _store(t):
    if isinstance(t, Name) or isinstance(t, Attribute) \
            or isinstance(t, Subscript) or isinstance(t, Starred):
        t.ctx = Store()
    elif isinstance(t, Tuple) or isinstance(t, List):
        t.ctx = Store()
        for e in t.elts:
            _store(e)
    return t


def _delctx(t):
    if isinstance(t, Name) or isinstance(t, Attribute) \
            or isinstance(t, Subscript):
        t.ctx = Del()
    elif isinstance(t, Tuple) or isinstance(t, List):
        for e in t.elts:
            _delctx(e)
    return t


def _conv_arguments(pnode):
    posonly = []
    args = []
    defaults = []
    vararg = None
    kwarg = None
    if pnode is not None:
        for p in pnode.children:
            if p.name == "NAME":
                args.append(arg(_leaf(p), None))
            elif p.name == "fpdef_opt":
                name = _leaf(p.children[0])
                ann = None
                dflt = None
                j = 1
                while j < len(p.children):
                    extra = p.children[j]
                    if extra.name == "annotation":
                        ann = _conv(extra.children[0])
                    else:
                        dflt = _conv(extra)
                    j = j + 1
                args.append(arg(name, ann))
                if dflt is not None:
                    defaults.append(dflt)
            elif p.name == "remaining_args":
                vararg = arg(_leaf(p.children[0]), None)
            elif p.name == "kwargs":
                kwarg = arg(_leaf(p.children[0]), None)
    return arguments(posonly, args, vararg, [], [], kwarg, defaults)


def _skip_noise(nm):
    return nm == "EMPTY_LINE" or nm == "comment"


def _suite(children, start):
    out = []
    i = start
    while i < len(children):
        c = children[i]
        if is_node(c):
            if c.name == "suite":
                for sc in c.children:
                    if is_node(sc) and not _skip_noise(sc.name):
                        out.append(_conv_stmt(sc))
            elif c.name != "elseblock" and not _skip_noise(c.name):
                out.append(_conv_stmt(c))
        i = i + 1
    return out


def _find_elseblock(children):
    for c in children:
        if is_node(c) and c.name == "elseblock":
            return _suite(c.children, 0)
    return []


def _dotted(node):
    # dotted_name -> "a.b.c" ; a bare NAME -> "a"
    if node.name == "NAME":
        return _leaf(node)
    parts = []
    for c in node.children:
        parts.append(_leaf(c))
    return ".".join(parts)


def _conv_stmt(node):
    nm = node.name
    if nm == "single_if":
        return If(_conv(node.children[0]), _suite(node.children, 1), [])
    if nm == "regular_assign":
        n = len(node.children)
        value = _conv(node.children[n - 1])
        targets = []
        i = 0
        while i < n - 1:
            targets.append(_store(_conv(node.children[i])))
            i = i + 1
        return Assign(targets, value)
    if nm == "aug_assign":
        target = _store(_conv(node.children[0]))
        op = _augop_cls(node.children[1].children[0])
        return AugAssign(target, op, _conv(node.children[2]))
    if nm == "ann_assign":
        target = _store(_conv(node.children[0]))
        annotation = _conv(node.children[1])
        value = None
        if len(node.children) > 2 and node.children[2].children:
            value = _conv(node.children[2].children[0])
        return AnnAssign(target, annotation, value, 1)
    if nm == "return_stmt":
        value = None
        if node.children:
            value = _conv(node.children[0])
        return Return(value)
    if nm == "if_stmt":
        return _conv_if(node, 0)
    if nm == "for_stmt":
        target = _store(_conv(node.children[0]))
        itr = _conv(node.children[1])
        body = _suite(node.children, 2)
        orelse = _find_elseblock(node.children)
        return For(target, itr, body, orelse)
    if nm == "while_stmt":
        test = _conv(node.children[0])
        body = _suite(node.children, 1)
        orelse = _find_elseblock(node.children)
        return While(test, body, orelse)
    if nm == "funcdef":
        name = _leaf(node.children[0])
        params = _conv_arguments(node.children[1])
        returns = None
        rnode = node.children[2]
        if rnode.children:
            returns = _conv(rnode.children[0])
        body = _suite(node.children, 3)
        return FunctionDef(name, params, body, [], returns)
    if nm == "classdef":
        name = _leaf(node.children[0])
        bases = []
        pnode = node.children[1]
        if pnode.children:
            first = pnode.children[0]
            if _is(first, "testlist"):
                for b in first.children:
                    if is_node(b):
                        bases.append(_conv(b))
            else:
                for b in pnode.children:
                    if is_node(b):
                        bases.append(_conv(b))
        body = _suite(node.children, 2)
        return ClassDef(name, bases, [], body, [])
    if nm == "import_names":
        names = []
        for c in node.children:
            if not is_node(c):
                continue
            if c.name == "dotted_as_name":
                names.append(alias(_dotted(c.children[0]),
                                   _leaf(c.children[1])))
            else:
                names.append(alias(_dotted(c), None))
        return Import(names)
    if nm == "import_from":
        module = _dotted(node.children[0])
        names = []
        asnames = node.children[1]
        for c in asnames.children:
            if not is_node(c):
                continue
            if c.name == "import_as_name":
                names.append(alias(_leaf(c.children[0]), _leaf(c.children[1])))
            elif c.name == "NAME":
                names.append(alias(_leaf(c), None))
        return ImportFrom(module, names, 0)
    if nm == "pass_stmt":
        return Pass()
    if nm == "break_stmt":
        return Break()
    if nm == "continue_stmt":
        return Continue()
    if nm == "global_stmt":
        names = []
        for c in node.children:
            if is_node(c) and c.name == "NAME":
                names.append(_leaf(c))
        return Global(names)
    if nm == "nonlocal_stmt":
        names = []
        for c in node.children:
            if is_node(c) and c.name == "NAME":
                names.append(_leaf(c))
        return Nonlocal(names)
    if nm == "raise_stmt":
        exc = None
        cause = None
        if node.children:
            exc = _conv(node.children[0])
        if len(node.children) > 1:
            cause = _conv(node.children[1])
        return _raise(exc, cause)
    if nm == "assert_stmt":
        test = _conv(node.children[0])
        msg = None
        i = 1
        while i < len(node.children):
            if is_node(node.children[i]):
                msg = _conv(node.children[i])
            i = i + 1
        return _assert(test, msg)
    if nm == "del_stmt":
        targets = []
        el = node.children[0]
        for c in el.children:
            if is_node(c):
                targets.append(_delctx(_conv(c)))
        return _delete(targets)
    if nm == "decorated":
        return _conv_decorated(node)
    if nm == "try_stmt":
        return _conv_try(node)
    if nm == "with_stmt":
        return _conv_with(node)
    # fall through: a bare expression used as a statement
    return Expr(_conv(node))


def _conv_if(node, i):
    clauses = node.children
    si = clauses[i]
    body = _suite(si.children, 1)
    orelse = []
    if i + 1 < len(clauses):
        nxt = clauses[i + 1]
        if _is(nxt.children[0], "gen_true"):
            orelse = _suite(nxt.children, 1)
        else:
            orelse = [_conv_if(node, i + 1)]
    return If(_conv(si.children[0]), body, orelse)


# Raise/Assert/Delete kept as thin builders (classes defined lazily below to
# keep the common node classes grouped above).
def _raise(exc, cause):
    return Raise(exc, cause)


def _assert(test, msg):
    return Assert(test, msg)


def _delete(targets):
    return Delete(targets)


class Raise(AST):
    def __init__(self, exc, cause):
        self._fields = ("exc", "cause")
        self._init_loc()
        self.exc = exc
        self.cause = cause


class Assert(AST):
    def __init__(self, test, msg):
        self._fields = ("test", "msg")
        self._init_loc()
        self.test = test
        self.msg = msg


class Delete(AST):
    def __init__(self, targets):
        self._fields = ("targets",)
        self._init_loc()
        self.targets = targets


class Try(AST):
    def __init__(self, body, handlers, orelse, finalbody):
        self._fields = ("body", "handlers", "orelse", "finalbody")
        self._init_loc()
        self.body = body
        self.handlers = handlers
        self.orelse = orelse
        self.finalbody = finalbody


class ExceptHandler(AST):
    def __init__(self, etype, name, body):
        self._fields = ("type", "name", "body")
        self._init_loc()
        self.type = etype
        self.name = name
        self.body = body


class With(AST):
    def __init__(self, items, body):
        self._fields = ("items", "body", "type_comment")
        self._init_loc()
        self.items = items
        self.body = body
        self.type_comment = None


class withitem(AST):
    def __init__(self, context_expr, optional_vars):
        self._fields = ("context_expr", "optional_vars")
        self.context_expr = context_expr
        self.optional_vars = optional_vars


# ---------------------------------------------------------------------------
# try / with conversion.  Both rast forms are leaf-laden: try_stmt delimits its
# sections with an except_clauses node plus bare 'else'/'finally' leaves, and
# with_stmt lists its items before a bare ':' leaf.  A small state machine walks
# the children and routes statements into the right bucket.
# ---------------------------------------------------------------------------

def _flatten_stmt(c):
    if c.name == "suite":
        out = []
        for sc in c.children:
            if is_node(sc) and not _skip_noise(sc.name):
                out.append(_conv_stmt(sc))
        return out
    if _skip_noise(c.name):
        return []
    return [_conv_stmt(c)]


def _conv_excepthandler(ec):
    etype = None
    ename = None
    exc = ec.children[0]
    if is_node(exc) and exc.name == "exception":
        if len(exc.children) >= 1:
            etype = _conv(exc.children[0])
        if len(exc.children) >= 2:
            ename = _leaf(exc.children[1])
    body = []
    i = 1
    while i < len(ec.children):
        c = ec.children[i]
        if is_node(c):
            for s in _flatten_stmt(c):
                body.append(s)
        i = i + 1
    return ExceptHandler(etype, ename, body)


def _conv_try(node):
    body = []
    handlers = []
    orelse = []
    finalbody = []
    section = "body"
    for c in node.children:
        if is_node(c):
            if c.name == "except_clauses":
                for ec in c.children:
                    if is_node(ec):
                        handlers.append(_conv_excepthandler(ec))
            else:
                stmts = _flatten_stmt(c)
                if section == "body":
                    for s in stmts:
                        body.append(s)
                elif section == "else":
                    for s in stmts:
                        orelse.append(s)
                else:
                    for s in stmts:
                        finalbody.append(s)
        else:
            if c == "else":
                section = "else"
            elif c == "finally":
                section = "finally"
    return Try(body, handlers, orelse, finalbody)


def _conv_with(node):
    items = []
    body = []
    seen_colon = False
    for c in node.children:
        if not is_node(c):
            if c == ":":
                seen_colon = True
            continue
        if not seen_colon:
            if c.name == "with_item":
                ctx_expr = _conv(c.children[0])
                optvars = None
                if len(c.children) >= 3:
                    optvars = _store(_conv(c.children[2]))
                items.append(withitem(ctx_expr, optvars))
            else:
                items.append(withitem(_conv(c), None))
        else:
            for s in _flatten_stmt(c):
                body.append(s)
    return With(items, body)


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Decorators.  rast wraps a decorated def/class in `decorated` -> [decorators,
# funcdef|classdef]; each `decorator` is a bare name, a dotted attribute, or a
# call.  We rebuild the decorator expression and attach it to decorator_list.
# ---------------------------------------------------------------------------

def _dotted_expr(node):
    if node.name == "NAME":
        return _conv(node)
    expr = _conv(node.children[0])
    i = 1
    while i < len(node.children):
        expr = Attribute(expr, _leaf(node.children[i]), Load())
        i = i + 1
    return expr


def _conv_decorator(dec):
    head = dec.children[0]
    if is_node(head) and head.name == "dotted_name":
        func = _dotted_expr(head)
    else:
        func = _conv(head)
    if len(dec.children) >= 2 and _is(dec.children[1], "arglist"):
        args = []
        kwargs = []
        _fill_arglist(dec.children[1], args, kwargs)
        return Call(func, args, kwargs)
    return func


def _conv_decorated(node):
    decs = []
    dnode = node.children[0]
    for d in dnode.children:
        if is_node(d) and d.name == "decorator":
            decs.append(_conv_decorator(d))
    inner = node.children[1]
    result = _conv_stmt(inner)
    result.decorator_list = decs
    return result


def parse(source):
    tree = parse_python(source)
    body = []
    if _is(tree, "And"):
        for c in tree.children:
            if is_node(c) and not _skip_noise(c.name):
                body.append(_conv_stmt(c))
    else:
        body.append(_conv_stmt(tree))
    return Module(body)
