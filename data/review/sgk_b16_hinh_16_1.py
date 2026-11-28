# Nhập từ bàn phím dãy n số và tính tổng.
n = int(input("Nhập số tự nhiên n: "))
s = 0
for i in range(n):
    a = int(input("Nhập số thứ "+str(i+1)+":"))
    s = s + a
print("Tổng các số đã nhập là:",s)
