class Account:
    def __init__(self, balance):
        self.balance = balance
    def deposit(self, amt):
        self.balance = self.balance + amt
    def withdraw(self, amt):
        self.balance = self.balance - amt

accts = []
i = 0
while i < 200:
    accts.append(Account(i))
    i = i + 1
total = 0
step = 0
while step < 4000:
    i = 0
    while i < 200:
        a = accts[i]
        a.deposit(10)
        a.withdraw(3)
        total = total + a.balance
        i = i + 1
    step = step + 1
print(total)
