# minipy2c.py -- a *mini* Python-to-C transpiler, the real-world companion to
# rast.py. rast.py turns Python source into a pymetaterp Node tree; minipy2c.py
# walks that tree and emits C, the same shape of job the full tools/py2c.py does.
#
# It is written in the minipy subset and is meant to be concatenated AFTER
# rast.py (so parse_python / is_node / Node are already defined) -- minipy has no
# imports. Together, `rast.py + minipy2c.py` parse and transpile real Python and
# are the "true" minipy benchmark: classes, methods, deep recursion, dict/list
# work and heavy string building, end to end.

class Transpiler:
    def __init__(self):
        self.out = ""
        self.declared = []          # names already declared in the current scope
        self.cur_class = ""         # enclosing class name while emitting a method
        self.tv_names = []          # parallel arrays: local name -> C type, used to
        self.tv_types = []          # tell `long` arithmetic from `char*` concatenation
        self.need_str = 0           # a string concat was emitted -> prepend the helper

    def emit(self, s):
        self.out = self.out + s

    def transpile(self, src):
        return self.transpile_tree(parse_python(src))

    # Transpile an already-parsed tree. Parsing (the PEG step) is the expensive,
    # memory-heavy part under minipy's free-once arena, and a given source always
    # parses to the same tree, so callers that transpile one program repeatedly
    # parse it once and reuse the tree here.
    def transpile_tree(self, tree):
        self.out = ""
        self.declared = []
        self.tv_names = []
        self.tv_types = []
        self.need_str = 0
        self.gen_block(tree)
        if self.need_str == 1:
            return self.str_prelude() + self.out
        return self.out

    # ---- local type tracking (long vs char*) ----------------------------
    def set_vtype(self, name, ty):
        i = 0
        while i < len(self.tv_names):
            if self.tv_names[i] == name:
                self.tv_types[i] = ty
                return
            i = i + 1
        self.tv_names.append(name)
        self.tv_types.append(ty)

    def vtype(self, name):
        i = 0
        while i < len(self.tv_names):
            if self.tv_names[i] == name:
                return self.tv_types[i]
            i = i + 1
        return "long"

    # Static C type of an expression: "char*" for strings/string-typed names and
    # `a + b` where either side is a string; "long" otherwise. Used to pick
    # arithmetic vs concatenation and to declare locals with the right type.
    def type_of(self, node):
        if not is_node(node):
            return "long"
        nm = node.name
        if nm == "STRING":
            return "char*"
        if nm == "NAME":
            n = node.children[0]
            if n == "True" or n == "False" or n == "None":
                return "long"
            return self.vtype(n)
        if nm == "__binary__":
            if node.children[0] == "+":
                lt = self.type_of(node.children[1])
                rt = self.type_of(node.children[2])
                if lt == "char*" or rt == "char*":
                    return "char*"
            return "long"
        return "long"

    # Emit a C string literal from decoded content (the parser has already
    # resolved escapes), re-escaping the C-significant characters.
    def c_string(self, s):
        out = "\""
        i = 0
        while i < len(s):
            c = s[i]
            if c == "\\":
                out = out + "\\\\"
            elif c == "\"":
                out = out + "\\\""
            elif c == "\n":
                out = out + "\\n"
            elif c == "\t":
                out = out + "\\t"
            elif c == "\r":
                out = out + "\\r"
            else:
                out = out + c
            i = i + 1
        return out + "\""

    def str_prelude(self):
        return ("#include <stdlib.h>\n"
                "#include <string.h>\n"
                "static char* _pystr_cat(const char* a, const char* b) {\n"
                "    long la = strlen(a);\n"
                "    long lb = strlen(b);\n"
                "    char* r = (char*)malloc(la + lb + 1);\n"
                "    memcpy(r, a, la);\n"
                "    memcpy(r + la, b, lb);\n"
                "    r[la + lb] = 0;\n"
                "    return r;\n"
                "}\n")

    # `suite` and `And` wrap a list of statements; anything else is a lone stmt.
    def body_stmts(self, node):
        if node.name == "suite" or node.name == "And":
            return node.children
        one = []
        one.append(node)
        return one

    def gen_block(self, node):
        stmts = self.body_stmts(node)
        i = 0
        while i < len(stmts):
            self.gen_stmt(stmts[i])
            i = i + 1

    def is_declared(self, name):
        i = 0
        while i < len(self.declared):
            if self.declared[i] == name:
                return 1
            i = i + 1
        return 0

    def gen_stmt(self, node):
        if not is_node(node):
            return
        nm = node.name
        if nm == "funcdef":
            self.gen_func(node)
        elif nm == "classdef":
            self.gen_class(node)
        elif nm == "regular_assign":
            self.gen_assign(node)
        elif nm == "return_stmt":
            self.emit("    return ")
            self.gen_expr(node.children[0])
            self.emit(";\n")
        elif nm == "if_stmt":
            self.gen_if(node)
        elif nm == "single_if":
            # a bare `if` with no elif/else parses as a lone single_if,
            # not wrapped in if_stmt; treat it as a one-branch if.
            one = []
            one.append(node)
            self.gen_if_branches(one)
        elif nm == "aug_assign":
            self.gen_aug_assign(node)
        elif nm == "for_stmt":
            self.gen_for(node)
        elif nm == "break_stmt":
            self.emit("    break;\n")
        elif nm == "continue_stmt":
            self.emit("    continue;\n")
        elif nm == "while_stmt":
            self.gen_while(node)
        elif nm == "__call__":
            self.emit("    ")
            self.gen_expr(node)
            self.emit(";\n")
        elif nm == "And" or nm == "suite":
            self.gen_block(node)
        else:
            self.emit("    /* stmt:" + nm + " */\n")

    # ---- annotation-driven typing ---------------------------------------
    # rast now parses `x: "int"` / `-> "char*"`. A parameter is either a bare
    # NAME (untyped -> long) or `fpdef_opt(NAME, annotation(STRING), ...)`; a
    # funcdef carries a `returns` node (empty when unannotated). We map the
    # annotation string to a C type, defaulting to `long` for anything we do not
    # model yet, so untyped code behaves exactly as before.
    def map_ctype(self, a):
        if a == "int":
            return "long"
        if a == "long":
            return "long"
        if a == "char*":
            return "char*"
        if a == "str":
            return "char*"
        if a == "float":
            return "double"
        if a == "double":
            return "double"
        if a == "bool":
            return "long"
        if a == "void":
            return "void"
        return "long"

    def param_name(self, p):
        if p.name == "fpdef_opt":
            return p.children[0].children[0]
        return p.children[0]

    def annot_of(self, p):
        # the annotation type string of a param, or "" when unannotated
        if p.name != "fpdef_opt":
            return ""
        ch = p.children
        k = 0
        while k < len(ch):
            c = ch[k]
            if is_node(c) and c.name == "annotation":
                return c.children[0].children[0]
            k = k + 1
        return ""

    def param_ctype(self, p):
        a = self.annot_of(p)
        if a == "":
            return "long"
        return self.map_ctype(a)

    def ret_ctype(self, returns, has_ret):
        # returns: the funcdef's `returns` child (empty node when unannotated)
        if is_node(returns) and len(returns.children) > 0:
            st = returns.children[0]
            if is_node(st) and st.name == "STRING":
                return self.map_ctype(st.children[0])
        if has_ret == 1:
            return "long"
        return "void"

    def gen_func(self, node):
        name = node.children[0].children[0]
        params = node.children[1]
        returns = node.children[2]
        body = node.children[3]
        self.declared = []
        self.tv_names = []
        self.tv_types = []
        self.emit(self.ret_ctype(returns, 1) + " " + name + "(")
        ps = params.children
        i = 0
        while i < len(ps):
            if i > 0:
                self.emit(", ")
            pn = self.param_name(ps[i])
            ct = self.param_ctype(ps[i])
            self.declared.append(pn)
            self.set_vtype(pn, ct)
            self.emit(ct + " " + pn)
            i = i + 1
        self.emit(") {\n")
        bs = self.body_stmts(body)
        j = 0
        while j < len(bs):
            self.gen_stmt(bs[j])
            j = j + 1
        self.emit("}\n")

    # ---- classes ---------------------------------------------------------
    # A class becomes a C struct plus one function per method (the receiver is
    # passed explicitly as `ClassName* self`). Fields are discovered from every
    # `self.X = ...` assignment in the class body; each is a `long` slot. Method
    # bodies read/write them as `self->X`, and a call `self.m(...)` lowers to
    # `ClassName_m(self, ...)`.
    def method_defs(self, suite):
        # The funcdefs directly inside a class suite.
        out = []
        stmts = self.body_stmts(suite)
        i = 0
        while i < len(stmts):
            s = stmts[i]
            if is_node(s) and s.name == "funcdef":
                out.append(s)
            i = i + 1
        return out

    def collect_fields(self, methods):
        # Field names in first-assignment order across all methods: any target
        # of the form `self.NAME = ...`.
        fields = []
        mi = 0
        while mi < len(methods):
            body = methods[mi].children[3]
            stmts = self.body_stmts(body)
            si = 0
            while si < len(stmts):
                self.scan_fields(stmts[si], fields)
                si = si + 1
            mi = mi + 1
        return fields

    def scan_fields(self, node, fields):
        # Walk one statement, appending any newly seen `self.X` assignment field.
        if not is_node(node):
            return
        if node.name == "regular_assign":
            tgt = node.children[0]
            if is_node(tgt) and tgt.name == "__getattr__":
                base = tgt.children[0]
                if is_node(base) and base.name == "NAME" and base.children[0] == "self":
                    fn = tgt.children[1].children[0]
                    if self.contains(fields, fn) == 0:
                        fields.append(fn)
        # recurse into nested blocks (if/while/for bodies) so fields assigned
        # under control flow are still declared.
        ch = node.children
        k = 0
        while k < len(ch):
            if is_node(ch[k]):
                self.scan_fields(ch[k], fields)
            k = k + 1

    def contains(self, xs, v):
        i = 0
        while i < len(xs):
            if xs[i] == v:
                return 1
            i = i + 1
        return 0

    def has_return(self, node):
        # True if the subtree contains a return_stmt (so a method returns long,
        # otherwise void).
        if not is_node(node):
            return 0
        if node.name == "return_stmt":
            return 1
        ch = node.children
        i = 0
        while i < len(ch):
            if self.has_return(ch[i]) == 1:
                return 1
            i = i + 1
        return 0

    def gen_class(self, node):
        name = node.children[0].children[0]
        suite = node.children[2]
        methods = self.method_defs(suite)
        fields = self.collect_fields(methods)
        # struct definition
        self.emit("typedef struct " + name + " {")
        fi = 0
        while fi < len(fields):
            self.emit(" long " + fields[fi] + ";")
            fi = fi + 1
        self.emit(" } " + name + ";\n")
        # methods
        self.cur_class = name
        mi = 0
        while mi < len(methods):
            self.gen_method(name, methods[mi])
            mi = mi + 1
        self.cur_class = ""

    def gen_method(self, cls, node):
        mname = node.children[0].children[0]
        params = node.children[1].children      # params[0] is `self`
        returns = node.children[2]
        body = node.children[3]
        self.declared = []
        self.tv_names = []
        self.tv_types = []
        self.emit(self.ret_ctype(returns, self.has_return(body)) + " ")
        self.emit(cls + "_" + mname + "(" + cls + "* self")
        i = 1                                    # skip self
        while i < len(params):
            pn = self.param_name(params[i])
            ct = self.param_ctype(params[i])
            self.declared.append(pn)
            self.set_vtype(pn, ct)
            self.emit(", " + ct + " " + pn)
            i = i + 1
        self.emit(") {\n")
        bs = self.body_stmts(body)
        j = 0
        while j < len(bs):
            self.gen_stmt(bs[j])
            j = j + 1
        self.emit("}\n")

    def gen_assign(self, node):
        tgt = node.children[0]
        self.emit("    ")
        if tgt.name == "__getattr__":
            # `obj.field = value` -> `obj->field = value;` (fields live in the
            # struct, so there is no `long` declaration here).
            self.gen_expr(tgt)
            self.emit(" = ")
            self.gen_expr(node.children[1])
            self.emit(";\n")
            return
        target = tgt.children[0]
        if self.is_declared(target) == 0:
            self.declared.append(target)
            ty = self.type_of(node.children[1])
            self.set_vtype(target, ty)
            self.emit(ty + " ")
        self.emit(target + " = ")
        self.gen_expr(node.children[1])
        self.emit(";\n")

    def gen_if(self, node):
        self.gen_if_branches(node.children)

    def gen_if_branches(self, branches):
        i = 0
        while i < len(branches):
            si = branches[i]
            cond = si.children[0]
            body = si.children[1]
            if cond.name == "gen_true":
                self.emit("    else {\n")
            else:
                if i == 0:
                    self.emit("    if (")
                else:
                    self.emit("    else if (")
                self.gen_expr(cond)
                self.emit(") {\n")
            bs = self.body_stmts(body)
            j = 0
            while j < len(bs):
                self.gen_stmt(bs[j])
                j = j + 1
            self.emit("    }\n")
            i = i + 1

    def gen_aug_assign(self, node):
        # aug_assign(NAME target, operation('+='), expr) -> `target += expr;`
        target = node.children[0].children[0]
        op = node.children[1].children[0]
        self.emit("    " + target + " " + op + " ")
        self.gen_expr(node.children[2])
        self.emit(";\n")

    def gen_for(self, node):
        # for VAR in range(...): BODY  ->  a counted C for loop.
        # children: [NAME loop-var, __call__(range, arglist), body]
        var = node.children[0].children[0]
        call = node.children[1]
        args = call.children[1].children
        self.declared.append(var)
        self.emit("    for (long " + var + " = ")
        if len(args) == 1:
            self.emit("0")
        else:
            self.gen_expr(args[0])
        self.emit("; " + var + " < ")
        if len(args) == 1:
            self.gen_expr(args[0])
        else:
            self.gen_expr(args[1])
        self.emit("; " + var + " = " + var + " + 1) {\n")
        bs = self.body_stmts(node.children[2])
        j = 0
        while j < len(bs):
            self.gen_stmt(bs[j])
            j = j + 1
        self.emit("    }\n")

    def gen_while(self, node):
        self.emit("    while (")
        self.gen_expr(node.children[0])
        self.emit(") {\n")
        bs = self.body_stmts(node.children[1])
        j = 0
        while j < len(bs):
            self.gen_stmt(bs[j])
            j = j + 1
        self.emit("    }\n")

    def gen_expr(self, node):
        if not is_node(node):
            self.emit(str(node))
            return
        nm = node.name
        if nm == "NAME":
            nv = node.children[0]
            if nv == "True":
                self.emit("1")
            elif nv == "False":
                self.emit("0")
            elif nv == "None":
                self.emit("0")
            else:
                self.emit(nv)
        elif nm == "NUMBER":
            self.emit(str(node.children[0]))
        elif nm == "STRING":
            self.emit(self.c_string(node.children[0]))
        elif nm == "__binary__":
            if node.children[0] == "+" and self.type_of(node) == "char*":
                # string concatenation -> runtime helper (see str_prelude)
                self.need_str = 1
                self.emit("_pystr_cat(")
                self.gen_expr(node.children[1])
                self.emit(", ")
                self.gen_expr(node.children[2])
                self.emit(")")
            else:
                self.emit("(")
                self.gen_expr(node.children[1])
                self.emit(" " + node.children[0] + " ")
                self.gen_expr(node.children[2])
                self.emit(")")
        elif nm == "factor":
            # unary op: factor(LEAF op, operand)
            self.emit("(" + node.children[0])
            self.gen_expr(node.children[1])
            self.emit(")")
        elif nm == "and_test":
            self.emit("(")
            self.gen_expr(node.children[0])
            self.emit(" && ")
            self.gen_expr(node.children[1])
            self.emit(")")
        elif nm == "or_test":
            self.emit("(")
            self.gen_expr(node.children[0])
            self.emit(" || ")
            self.gen_expr(node.children[1])
            self.emit(")")
        elif nm == "not_test":
            self.emit("(!")
            self.gen_expr(node.children[0])
            self.emit(")")
        elif nm == "__getattr__":
            # obj.field -> obj->field  (self.n -> self->n)
            base = node.children[0]
            field = node.children[1].children[0]
            if is_node(base) and base.name == "NAME":
                self.emit(base.children[0] + "->" + field)
            else:
                self.emit("(")
                self.gen_expr(base)
                self.emit(")->" + field)
        elif nm == "__call__":
            callee = node.children[0]
            if is_node(callee) and callee.name == "__getattr__":
                self.gen_method_call(node, callee)
            else:
                self.emit(callee.children[0])
                self.emit("(")
                if len(node.children) > 1:
                    args = node.children[1].children
                    i = 0
                    while i < len(args):
                        if i > 0:
                            self.emit(", ")
                        self.gen_expr(args[i])
                        i = i + 1
                self.emit(")")
        else:
            self.emit("0")

    def gen_method_call(self, node, callee):
        # self.m(args) -> ClassName_m(self, args). A call on any other receiver
        # needs its struct type, which we do not infer yet, so it is left as a
        # placeholder (0) rather than emitting an unresolved name.
        base = callee.children[0]
        meth = callee.children[1].children[0]
        if is_node(base) and base.name == "NAME" and base.children[0] == "self":
            self.emit(self.cur_class + "_" + meth + "(self")
            if len(node.children) > 1:
                args = node.children[1].children
                i = 0
                while i < len(args):
                    self.emit(", ")
                    self.gen_expr(args[i])
                    i = i + 1
            self.emit(")")
        else:
            self.emit("0")


def transpile_source(src):
    t = Transpiler()
    return t.transpile(src)
