"""An 'a-flavour' Node. Its bare class name collides with node_b.Node; when a
single translation unit (app.py) references both, py2c's shadow-class registry
must emit a distinct forward typedef, struct body and TypeInfo for each."""


class Node:
    def __init__(self, tag: int, payload: int):
        self.tag = tag
        self.payload = payload

    def score(self) -> int:
        return self.tag * 10 + self.payload
