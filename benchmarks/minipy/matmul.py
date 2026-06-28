n = 28
A = []
B = []
i = 0
while i < n:
    rowa = []
    rowb = []
    j = 0
    while j < n:
        rowa.append((i * 7 + j) % 11)
        rowb.append((i + j * 5) % 13)
        j = j + 1
    A.append(rowa)
    B.append(rowb)
    i = i + 1
C = 0
i = 0
while i < n:
    j = 0
    while j < n:
        s = 0
        k = 0
        while k < n:
            s = s + A[i][k] * B[k][j]
            k = k + 1
        C = C + s % 7
        j = j + 1
    i = i + 1
print(C)
