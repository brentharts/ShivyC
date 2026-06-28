x = [0.0, 1.0, 2.0]
y = [0.0, 0.5, 1.5]
vx = [0.0, 0.0, 0.0]
vy = [0.0, 0.0, 0.0]
m = [1.0, 2.0, 3.0]
dt = 0.001
step = 0
while step < 60000:
    i = 0
    while i < 3:
        fx = 0.0
        fy = 0.0
        j = 0
        while j < 3:
            if i != j:
                dx = x[j] - x[i]
                dy = y[j] - y[i]
                d2 = dx * dx + dy * dy + 0.01
                inv = 1.0 / (d2 * d2)
                fx = fx + m[j] * dx * inv
                fy = fy + m[j] * dy * inv
            j = j + 1
        vx[i] = vx[i] + dt * fx
        vy[i] = vy[i] + dt * fy
        i = i + 1
    i = 0
    while i < 3:
        x[i] = x[i] + dt * vx[i]
        y[i] = y[i] + dt * vy[i]
        i = i + 1
    step = step + 1
print(int((x[0] + y[2] + x[1]) * 1000000))
