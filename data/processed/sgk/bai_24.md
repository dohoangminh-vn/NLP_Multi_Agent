# Bài 24. Xâu kí tự
_Nguồn: SGK Tin học 10 – Kết nối tri thức, tr.119–122_

## 1. XÂU LÀ MỘT DÃY CÁC KÍ TỰ (tr.119)

Quan sát các ví dụ sau để biết cấu trúc xâu kí tự, so sánh với danh sách để biết
sự khác nhau giữa xâu (string) và danh sách (list).
Hoạt động 1 Tìm hiểu cấu trúc của xâu kí tự
Ví dụ 1. Xâu kí tự và cách truy cập đến từng kí tự của xâu.
```python
>>> s = "Thời khoá biểu"
>>> len(s)
```
14
```python
>>> s[0]
```
'T'
```python
>>> s[10]
```
'b'
Một xâu kí tự được hiểu là một dãy các kí tự. Tương tự danh sách, ta có thể truy
cập từng kí tự của xâu thông qua chỉ số, chỉ số bắt đầu từ 0.
Ví dụ 2. Quan sát các lệnh sau để thấy sự khác nhau giữa xâu và danh sách.
```python
>>> d = ["a","b","c"]
>>> d[0] = "A"
>>> s = "abc"
>>> s[0] = "A"
```
Traceback (most recent call last):
```python
  File "<pyshell#15>", line 1, in <module>
    s[0] = "A"
```
TypeError: 'str' object does not support item assignment
Lệnh len() sẽ tính độ dài của xâu hay số lượng
các kı́ tự có trong xâu.
Có thể truy cập từng kı́ tự của xâu thông qua chỉ số.
Chỉ số bắt đầu từ 0.
Em đã biết dữ liệu xâu kı́ tự (gọi tắt là xâu) từ Bài 16 và chúng ta có thể tạo các
biến kiểu xâu kí tự theo nhiều cách như sau:
```python
>>> s = "Thời khoá biểu"
>>> xau = 'Hoa học trò'
>>> Cau_tho = """ Mình về mình có nhớ ta
```
Mười lăm năm ấy thiết tha mặn nồng"""
Liệu có lệnh nào trích ra từng kı́ tự của một xâu kı́ tự? Đếm số kı́ tự của một xâu?

## SAU BÀI NÀY EM SẼ (tr.119)

x Hiểu được xâu kí tự là kiểu dữ liệu cơ bản của Python.
xBiết và thực hiện được lệnh for để xử lí xâu kí tự.

## 1. XÂU LÀ MỘT DÃY CÁC KÍ TỰ (tr.119)

Python không cho phép thay đổi từng kí tự của một xâu. Điều này khác với danh sách.
Python không có kiểu dữ liệu kı́ tự. Kı́ tự chính là xâu có độ dài 1. Xâu rỗng được
định nghĩa như sau:
empty = ""
Xâu kí tự trong Python là dãy các kí tự Unicode. Xâu có thể được coi là
danh sách các kí tự nhưng không thay đổi từng kí tự của xâu. Truy cập
từng kí tự của xâu qua chỉ số, chỉ số từ 0 đến độ dài len() - 1.
1.  Các xâu kı́ tự sau có hợp lệ không?
a) "123&*()+-ABC"     b) "1010110&0101001"
c) "Tây Nguyên"      d) 11111111 = 256
2.  Mỗi xâu hợp lệ ở Câu 1 có độ dài bằng bao nhiêu?

## 2. LỆNH DUYỆT KÍ TỰ CỦA XÂU (tr.120)

Quan sát các lệnh sau để biết cách duyệt từng kí tự của xâu kı́ tự bằng lệnh for.
Có hai cách duyệt, theo chỉ số và theo phần tử của xâu kı́ tự.
```python
>>> s = "Thời khoá biểu"
>>> for i in range(len(s)):
  print(s[i],end = " ")
```
T h ờ i   k h o á   b i ể u
```python
>>> for ch in s:
  print(ch, end = " ")
```
T h ờ i   k h o á   b i ể u
Duyệt theo chỉ số với lệnh range().
Duyệt theo kı́ tự của xâu kı́ tự.
Hoạt động 2 Tìm hiểu lệnh duyệt từng kí tự của xâu
-  Cách thứ nhất, biến i lần lượt chạy theo chỉ số của xâu kı́ tự s, từ 0 đến len(s) - 1.
Kí tự tại chỉ số i là s[i].
-  Cách thứ hai duyệt theo từng kı́ tự của xâu s. Biến ch sẽ được gán lần lượt các kı́
tự của xâu s từ đầu đến cuối.
Chú ý: Từ khoá in, tuỳ trường hợp cụ thể, hoặc là toán tử lôgic dùng để kiểm tra
một giá trị có mặt hay không trong một vùng giá trị/danh sách/xâu, hoặc để chọn lần
lượt từng phần tử trong một vùng giá trị/danh sách/xâu.
```python
>>> "a" in "abcd"
```
True
```python
>>> "abc" in "abcd"
```
True
Có thể duyệt các kí tự của xâu bằng lệnh for tương tự như với danh sách.
s1 in s2 trả lại giá trị True nếu s1 là xâu con của s2.
1.  Sau khi thực hiện các lệnh sau, biến skq sẽ có giá trị bao nhiêu?
```python
>>> s = "81723"
>>> skq = ""
>>> for ch in s:
        if int(ch) % 2 != 0:
            skq = skq + ch
```
2. Cho s1 = "abc", s2 = "ababcabca". Các biểu thức lôgic sau cho kết quả là đúng
hay sai?
a) s1 in s2 b) s1 + s1 in s2
c) "abcabca" in s2 d) "abc123" in s2

## THỰC HÀNH (tr.121)

Các lệnh cơ bản làm việc với xâu kı́ tự
Nhiệm vụ 1. Viết chương trình nhập số tự nhiên n là số học sinh, sau đó nhập họ và
tên học sinh. Lưu họ và tên học sinh vào một danh sách. In danh sách ra màn hình,
mỗi họ tên trên một dòng.
Hướng dẫn. Chương trình có thể như sau:
```python
n = int(input("Nhập số học sinh trong lớp: "))
ds_lop = []
for i in range(n):
    hoten = input("Nhập họ tên học sinh thứ " + str(i+1)+": ")
    ds_lop.append(hoten)
print("Danh sách lớp học:")
for i in range(n):
    print(ds_lop[i])
```
Nhiệm vụ 2. Nhập một xâu kı́ tự S từ bàn phím rồi kiểm tra xem xâu S có chứa xâu
con "10" không.
Hướng dẫn. Cách 1. Nếu xâu S chứa xâu con "10" thì sẽ có chỉ số k mà S[k] = "1"
và S[k+1] = "0". Cách 2. Dùng toán tử in để kiểm tra xâu "10" có là xâu con của S.
Cách 1: Duyệt kí tự của xâu theo chỉ số.
```python
S = input("Nhập xâu kı́ tự bất kì: ")
kq = False
for i in range(len(S)-1):
    if S[i] == "1" and S[i+1] == "0":
        kq = True
        break
if kq:
    print("Xâu gốc có chứa xâu '10'")
else:
    print("Xâu gốc không chứa xâu '10'")
```
Cách 2: Sử dụng toán tử in.
```python
S = input("Nhập xâu kı́ tự bất kì: ")
s10 = "10"
if s10 in S:
    print("Xâu gốc có chứa xâu '10'")
else:
    print("Xâu gốc không chứa xâu '10'")
```

## LUYỆN TẬP (tr.122)

1.  Cho xâu S, viết đoạn lệnh trích ra xâu con của S bao gồm ba kí tự đầu tiên của S.
2. Viết chương trình kiểm tra xâu S có chứa chữ số không. Thông báo "S có chứa
chữ số" hoặc "S không chứa chữ số nào".

## VẬN DỤNG (tr.122)

1.  Cho hai xâu s1, s2. Viết đoạn chương trình chèn xâu s1 vào giữa s2, tại vị trí
len(s2)//2. In kết quả ra màn hình.
2. Viết chương trình nhập số học sinh và họ tên học sinh. Sau đó đếm xem trong
danh sách có bao nhiêu bạn tên là "Hương".
 Gợi ý: Sử dụng toán tử in để kiểm tra một xâu có là xâu con của một xâu khác.
