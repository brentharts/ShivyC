"""Multi-file rpython program exercising ambiguous classes + POD/OBJ dispatch:

    python3 -m shivyc.main app.py node_a.py node_b.py -o app

node_a.Node and node_b.Node share a bare name, so app.c must keep two distinct
struct layouts (shadow typedefs/bodies) for them. Both are POD classes (no base,
no polymorphism): py2c propagates that POD decision across the module boundary,
so app.c lays each struct out with NO Obj header and calls their methods
*directly* (no vtable) -- matching how node_a.c / node_b.c emit them. Field reads
then land at the right offsets. Deterministic exit: 45."""
import node_a
import node_b


def main() -> int:
    a = node_a.Node(2, 5)
    b1 = node_b.Node("x", 3)
    b2 = node_b.Node("y", 4)

    total = 0
    total += a.score()                  # direct call -> 2*10 + 5 = 25
    total += b1.bump(4)                 # direct call -> 3 + 4    =  7
                                        #   (also sets b1.cached = [7])

    # field reads against both same-named-but-distinct POD layouts
    total += a.tag                      # node_a.Node.tag    =  2
    total += b1.weight                  # node_b.Node.weight =  7  (post-bump)
    total += b2.weight                  # node_b.Node.weight =  4

    return total                        # 25 + 7 + 2 + 7 + 4 = 45
