words = ["the", "quick", "brown", "fox", "lazy", "dog", "runs", "fast"]
counts = {}
i = 0
while i < 100000:
    w = words[i % 8]
    if w in counts:
        counts[w] = counts[w] + 1
    else:
        counts[w] = 1
    i = i + 1
print(counts["the"] + counts["fox"] + counts["fast"])
