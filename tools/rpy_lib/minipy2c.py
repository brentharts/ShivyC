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

    def emit(self, s):
        self.out = self.out + s

    def transpile(self, src):
        self.out = ""
        self.declared = []
        tree = parse_python(src)
        self.gen_block(tree)
        return self.out

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

    def gen_func(self, node):
        name = node.children[0].children[0]
        params = node.children[1]
        body = node.children[2]
        self.declared = []
        self.emit("long " + name + "(")
        ps = params.children
        i = 0
        while i < len(ps):
            if i > 0:
                self.emit(", ")
            pn = ps[i].children[0]
            self.declared.append(pn)
            self.emit("long " + pn)
            i = i + 1
        self.emit(") {\n")
        bs = self.body_stmts(body)
        j = 0
        while j < len(bs):
            self.gen_stmt(bs[j])
            j = j + 1
        self.emit("}\n")

    def gen_assign(self, node):
        target = node.children[0].children[0]
        self.emit("    ")
        if self.is_declared(target) == 0:
            self.declared.append(target)
            self.emit("long ")
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
            self.emit(node.children[0])
        elif nm == "NUMBER":
            self.emit(str(node.children[0]))
        elif nm == "__binary__":
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
        elif nm == "__call__":
            self.emit(node.children[0].children[0])
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


def transpile_source(src):
    t = Transpiler()
    return t.transpile(src)
