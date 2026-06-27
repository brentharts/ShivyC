"""rpy_heapq -- a restricted-Python (rpython) port of the stdlib `heapq`.

A binary min-heap over an unboxed `list[float]`, ported from CPython's `heapq`
(the same algorithm micropython-lib ships). py2c lowers the sift loops to tight
C over a `double*`; `heappush`/`heappop`/`heapify` mutate the heap in place
through the typed-list pointer, and `heappop` uses the typed-list `pop()` to
shrink it. Matches CPython's `heapq` element-for-element for the supported
`float` subset, so a transpiled program can be cross-checked against it.

API:
    heappush(heap, item)   -> push item, keep heap invariant
    heappop(heap)          -> pop and return the smallest item
    heapify(x)             -> rearrange x into a heap in place
    heapreplace(heap, item)-> pop-then-push (return old min), one sift
    heappushpop(heap, item)-> push-then-pop, fast when item <= min
"""


def _siftdown(heap: "list[float]", startpos: "int", pos: "int") -> None:
    newitem = heap[pos]
    while pos > startpos:
        parentpos = (pos - 1) >> 1
        parent = heap[parentpos]
        if newitem < parent:
            heap[pos] = parent
            pos = parentpos
        else:
            break
    heap[pos] = newitem


def _siftup(heap: "list[float]", pos: "int") -> None:
    endpos = len(heap)
    startpos = pos
    newitem = heap[pos]
    childpos = 2 * pos + 1
    while childpos < endpos:
        rightpos = childpos + 1
        if rightpos < endpos and not (heap[childpos] < heap[rightpos]):
            childpos = rightpos
        heap[pos] = heap[childpos]
        pos = childpos
        childpos = 2 * pos + 1
    heap[pos] = newitem
    _siftdown(heap, startpos, pos)


def heappush(heap: "list[float]", item: "float") -> None:
    heap.append(item)
    _siftdown(heap, 0, len(heap) - 1)


def heappop(heap: "list[float]") -> "float":
    lastelt = heap.pop()              # raises on empty, like CPython
    if len(heap) > 0:
        returnitem = heap[0]
        heap[0] = lastelt
        _siftup(heap, 0)
        return returnitem
    return lastelt


def heapreplace(heap: "list[float]", item: "float") -> "float":
    returnitem = heap[0]
    heap[0] = item
    _siftup(heap, 0)
    return returnitem


def heappushpop(heap: "list[float]", item: "float") -> "float":
    # explicit swap rather than `item, heap[0] = heap[0], item`: a tuple-swap
    # with a typed-list element subscript does not lower cleanly today.
    if len(heap) > 0 and heap[0] < item:
        tmp = heap[0]
        heap[0] = item
        item = tmp
        _siftup(heap, 0)
    return item


def heapify(x: "list[float]") -> None:
    n = len(x)
    i = n // 2 - 1
    while i >= 0:
        _siftup(x, i)
        i = i - 1
