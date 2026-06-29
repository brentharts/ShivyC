words = ["alpha", "beta", "gamma", "delta", "epsilon"]
acc = ""
i = 0
total = 0
while i < 80000:
    w = words[i % 5]
    acc = w.upper() + "-" + w[::-1]
    parts = acc.split("-")
    total = total + len(parts[0]) + len(parts[1])
    if acc.find("A") >= 0:
        total = total + 1
    i = i + 1
print(total)
