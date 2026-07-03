# type(x).__name__ -- how py2c dispatches on AST nodes ("st_" + type(node).__name__).
# For linked modules (minast), the class name carries a $mod$ prefix internally;
# __name__ strips it so dispatch matches CPython's real ast. Node access is by
# index (not ast.walk) to stay independent of traversal order.
import ast

tree = ast.parse("a = 1\nb = a + f(a, 2)\n")
print(type(tree).__name__)
s0 = tree.body[0]
s1 = tree.body[1]
print("st_" + type(s0).__name__)
print("st_" + type(s1).__name__)
print("ex_" + type(s1.value).__name__)
print("ex_" + type(s1.value.right).__name__)
print(type(s0.targets[0]).__name__)
print(type(s0.value).__name__)


class Widget:
    pass


class Gadget(Widget):
    pass


print(type(Widget()).__name__)
print(type(Gadget()).__name__)
print(type(Widget()).__name__ == "Widget")
