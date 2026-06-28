class Node:
    def __init__(self, val):
        self.val = val
        self.left = None
        self.right = None

def insert(root, val):
    node = root
    while True:
        if val < node.val:
            if node.left is None:
                node.left = Node(val)
                return
            node = node.left
        else:
            if node.right is None:
                node.right = Node(val)
                return
            node = node.right

def total(node):
    if node is None:
        return 0
    return node.val + total(node.left) + total(node.right)

root = Node(500)
seed = 1
i = 0
while i < 5000:
    seed = (seed * 1103515245 + 12345) % 2147483648
    insert(root, seed % 1000)
    i = i + 1
print(total(root))
