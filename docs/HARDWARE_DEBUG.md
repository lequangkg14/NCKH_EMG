# Kiểm tra phần cứng trước khi thu dữ liệu

Sketch: `firmware/emg_hw_debug_v1_4/emg_hw_debug_v1_4.ino`
(cùng bộ lọc với logger v1.4/v1.3: 1 kHz nội bộ, trung bình 20 ms, xuất 100 Hz; chân GPIO 2, 4, 5, 6, 7 → CH1..CH5).

## Cách dùng
1. Nạp sketch, mở Serial Monitor (921600 baud). Mặc định ở chế độ MONITOR: mỗi giây in bảng 5 kênh
   (giá trị trung bình, min, max trong 1 s, thanh bar, kết luận OK / BAO HOA / KHONG TIN HIEU?).
2. Gõ lệnh vào Serial Monitor:
   | Lệnh | Việc |
   |---|---|
   | `m` | Monitor (bảng trạng thái mỗi giây) |
   | `p` | Xuất `CH1:..,...,CH5:..` để vẽ 5 kênh bằng Serial Plotter |
   | `1`–`5` | Vẽ 1 kênh: đường RAW (không lọc) và FILT (đã lọc) để thấy hiệu quả lọc nhiễu lưới |
   | `t` | Test có hướng dẫn ~13 s, ra bảng kết luận từng kênh |
   | `h` | Trợ giúp |
3. Serial Plotter không gửi được lệnh: đổi chế độ ở Serial Monitor rồi mở Plotter
   (nếu IDE không cho mở cùng lúc thì đóng Monitor trước). Nếu board reset mỗi lần mở cổng, đổi `DEFAULT_MODE` rồi nạp lại.

## Test có hướng dẫn (`t`)
Thả lỏng 3 s → đo nền 3 s → đếm ngược 3 s → gồng mạnh (nắm chặt) → đo 3 s. Kết luận từng kênh:
- `OK`
- `LOI: khong co tin hieu`: kiểm tra nguồn ±Vs, dây SIG/GND, GND chung với ESP32
- `LOI: khong dap ung`: tỉ lệ gồng/nền dưới 1.5, kiểm tra điện cực, tiếp xúc da, vị trí cảm biến, gain
- `CANH BAO: bao hoa`: giảm gain
- `CANH BAO: nhieu cao luc nghi`: kiểm tra đất, dây, điện cực tham chiếu
- `CANH BAO: nen cao`: giảm gain hoặc kiểm tra offset

Các ngưỡng nằm ở đầu sketch (`DEAD_MAX`, `SAT_LEVEL`, `REST_STD_MAX`, `BASE_MAX`, `RATIO_MIN`).
Cảm biến đặt ở cơ khác với nhóm cơ gồng khi nắm tay có thể báo "khong dap ung": dùng chế độ `1`–`5`
rồi gồng đúng nhóm cơ của kênh đó để kiểm tra riêng.

## Trước khi thu
Cả 5 kênh phải `OK` (hoặc chỉ cảnh báo đã hiểu nguyên nhân), `rate` ≈ 100 Hz và `overrun=0`.

## Dùng ESP32 DevKit V1 (ESP32 thường) để test
Sketch tự nhận loại board lúc biên dịch:
| Board | CH1 | CH2 | CH3 | CH4 | CH5 |
|---|---|---|---|---|---|
| ESP32-S3 (board chính) | GPIO2 | GPIO4 | GPIO5 | GPIO6 | GPIO7 |
| ESP32 DevKit V1 (chỉ để test) | GPIO32 | GPIO33 | GPIO34 | GPIO35 | GPIO36 (VP) |

Lưu ý khi dùng DevKit V1:
- Chỉ dùng các chân ADC1 (GPIO32–39). **Không** dùng GPIO5 làm ADC, **không** đụng GPIO6–11 (nối flash, làm board treo).
- Chọn board "ESP32 Dev Module" trong Arduino IDE. Board dùng chip cầu USB-UART (CP2102/CH340) nên không có tùy chọn
  "USB CDC On Boot"; Serial Monitor vẫn dùng 921600 baud. Mở cổng serial sẽ reset board nên banner luôn hiện.
- ADC của ESP32 thường kém tuyến tính ở gần 0 V và gần 3.1 V (nền lúc nghỉ ~0.1–0.4 V có thể bị đọc lệch hoặc về 0),
  nhiều nhiễu hơn S3. **Chỉ dùng để kiểm tra cảm biến, không thu dataset chính thức và không trộn với dữ liệu S3.**
  Logger sẽ hiện cảnh báo nếu firmware báo `Board=` khác ESP32-S3.
- Trước khi nối cảm biến: chỉ cắm USB, board không được nóng và đèn nguồn phải sáng rõ.

## Test phần cứng tích hợp trong logger (`logger/main.py` v1.8)
Nút **TEST PHẦN CỨNG** chạy đúng timeline 8 s như trial thật (thả lỏng 0–2 s, chuẩn bị 2–3 s, **nắm chặt tay 3–6 s**, thả lỏng 6–8 s)
nhưng **không lưu file** (không CSV, không metadata). Kết quả:
- **TẤT CẢ KÊNH OK – CÓ THỂ BẮT ĐẦU THU DATA** (nền sáng xanh)
- **OK, CÓ CẢNH BÁO: CHn** (vàng): kênh đáp ứng nhưng có bão hòa nhẹ, nhiễu cao lúc nghỉ hoặc nền cao
- **CÓ LỖI: CHn** (đỏ): kênh không có tín hiệu, không đáp ứng khi gồng, bão hòa nhiều; hoặc lỗi đường truyền (mất gói, sai tốc độ, trễ nhịp)

Tiêu chí kênh (đo bằng ADC count, sau bộ lọc 20 ms): nền = trung vị lúc nghỉ, mức gồng = trung bình trượt 0.5 s lớn nhất trong 3–6 s.
Kênh đạt khi mức gồng cao hơn nền ít nhất 150 count (và ≥ 1.4 lần nền nếu nền ≥ 100). Chỉnh ngưỡng ở nhóm `TEST_*` đầu `main.py`.
Nên chạy test trước mỗi session (sau khi đeo băng) và sau mỗi lần đổi dây hoặc cảm biến.
