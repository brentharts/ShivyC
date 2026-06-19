"""End-to-end check of recently added rpython features:

  * @staticmethod invoked on the class name   (Counter.classify(...))
  * set.add / dict.get / dict subscript / len over containers
  * .clear() on a dict, a set, and a list      (the pyclear runtime helper)

Deterministic: main() returns 10 under both CPython and ShivyCX.
"""


class Counter:
    def __init__(self):
        self.seen = {}
        self.tags = set()

    @staticmethod
    def classify(n: int) -> int:
        if n > 0:
            return 1
        return 0

    def record(self, tag: str):
        self.tags.add(tag)
        self.seen[tag] = self.seen.get(tag, 0) + 1

    def reset(self):
        self.seen.clear()
        self.tags.clear()


def main() -> int:
    total = 0
    total += Counter.classify(5)        # static call -> 1
    total += Counter.classify(-2)       #             -> 0  (total 1)

    c = Counter()
    c.record("a")
    c.record("a")
    c.record("b")
    total += len(c.tags)                # 2 distinct  -> 3
    total += c.seen["a"]                # recorded 2x -> 5
    total += len(c.seen)                # 2 keys      -> 7

    c.reset()                           # dict + set .clear()
    total += len(c.tags) + len(c.seen)  # emptied     -> 7

    nums = [1, 2, 3]
    total += len(nums)                  #             -> 10
    nums.clear()                        # list .clear()
    total += len(nums)                  # emptied     -> 10

    return total


if __name__ == "__main__":
    import sys
    sys.exit(main())
