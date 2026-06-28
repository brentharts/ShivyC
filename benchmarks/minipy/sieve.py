N = 100000
flags = [True] * N
count = 0
i = 2
while i < N:
    if flags[i]:
        count = count + 1
        j = i + i
        while j < N:
            flags[j] = False
            j = j + i
    i = i + 1
print(count)
