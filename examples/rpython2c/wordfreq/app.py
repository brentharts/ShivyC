"""wordfreq - realistic dict usage: frequency counting and grouping.

Two of the most common real-world dict patterns: the `d[k] = d.get(k, 0) + 1`
counter, and grouping items into a dict whose values are lists
(`groups.setdefault(key, []).append(item)`-style, written out longhand here).
"""


def count_words(text: str):
    counts = {}
    for w in text.split(" "):
        counts[w] = counts.get(w, 0) + 1
    return counts


def group_by_length(words):
    groups = {}
    for w in words:
        n = len(w)
        if n not in groups:
            groups[n] = []
        groups[n].append(w)
    return groups


def main() -> int:
    text = "the cat sat on the mat the cat ran fast"
    counts = count_words(text)
    total = 0
    total += counts["the"] * 100             # 3 -> 300
    total += counts["cat"] * 10              # 2 -> +20 (320)
    total += counts["sat"]                   # 1 -> +1  (321)

    # most frequent count
    best = 0
    for w in counts.keys():
        if counts[w] > best:
            best = counts[w]
    total += best                            # 3 -> +3 (324)

    words = ["a", "bb", "cc", "ddd", "e", "ff", "ggg"]
    groups = group_by_length(words)
    total += len(groups[1]) * 10             # ['a','e'] -> 2 -> +20 (344)
    total += len(groups[2])                  # ['bb','cc','ff'] -> 3 -> +3 (347)
    total += len(groups[3])                  # ['ddd','ggg'] -> 2 -> +2 (349)

    return total % 256                       # 349 % 256 = 93


if __name__ == "__main__":
    import sys
    sys.exit(main())
