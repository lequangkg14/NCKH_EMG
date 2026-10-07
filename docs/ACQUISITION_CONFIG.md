# Cấu hình thu chuẩn (đã khóa)

Mọi session chính thức phải dùng đúng cấu hình này. Nếu đổi bất kỳ mục nào,
coi đó là **cấu hình mới**, ghi vào `docs/CHANGELOG.md` và `ACQ_CONFIG` trong `logger/main.py`.

## Firmware
- `firmware/emg_5ch_logger_v1_4/emg_5ch_logger_v1_4.ino` (bộ lọc giữ nguyên như v1.3)
- ADC nội ESP32-S3: 12 bit, suy hao 11 dB (~0–3.1 V), chỉ dùng ADC1
- Lấy mẫu nội bộ 1 kHz, trung bình trượt 20 ms (triệt 50/100/150 Hz), xuất 100 Hz
- Giao thức serial (đã đóng băng): `SEQ,TIME_US,CH1,CH2,CH3,CH4,CH5`

## Cảm biến và đấu dây
- 5 cảm biến Muscle Sensor v3 (cảm biến kênh 1 của bản 6 kênh bị hỏng, đã loại), đầu ra là đường bao chỉnh lưu + làm mượt (~2 Hz), không phải EMG thô
- Chân ADC: GPIO 2, 4, 5, 6, 7 → CH1..CH5 (tương ứng CH2..CH6 của bản 6 kênh cũ)
- Board chính để thu dataset: ESP32-S3 N16R8. ESP32 DevKit V1 (chân GPIO 32–36) chỉ dùng để test phần cứng, xem `docs/HARDWARE_DEBUG.md`
- Nguồn: 2 pin 9 V cho mỗi nhóm cảm biến (hai nhóm, mỗi nhóm tối đa 3 cảm biến)
- Đất hình sao: điểm giữa hai bộ pin và GND của ESP32 về cùng một điểm; mỗi cảm biến một dây GND riêng;
  GND cạnh SIG của từng cảm biến xoắn cùng dây SIG
- Chỉ nối điểm giữa với GND ESP32; không bao giờ nối +Vs/-Vs vào ESP32

## Trước mỗi session
1. Kiểm tra điện áp pin, đo SIG lúc nghỉ và gồng (phải dưới ~3 V)
2. Mở Serial Monitor sau khi nạp firmware, đọc các dòng `#` (không được có `WARNING`)
3. Thu 1 trial nghỉ hoàn toàn để kiểm tra nền nhiễu trước khi thu cử động

## Dấu hiệu dữ liệu tốt (kinh nghiệm từ lần thu thử)
- Bước thời gian đúng 10 ms, `SEQ` liên tục
- Độ lệch chuẩn lúc nghỉ vài chục count trở xuống, không có đỉnh phổ 50/100 Hz
- Không có mẫu bằng 0 hoặc 4095 khi gồng bình thường
