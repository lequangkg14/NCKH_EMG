# Changelog

## Logger v1.8
- Thêm nút **TEST PHẦN CỨNG** trong logger: chạy đúng timeline 8 s như trial thật với cử động nắm chặt tay
  (Rest → Cue → Hold → Relax), **không ghi CSV, không ghi `metadata.json`**
- Cuối test phân tích từng kênh (nền lúc nghỉ, mức gồng, tỉ lệ, nhiễu, bão hòa, kênh chết) và đường truyền
  (mất gói, tốc độ, timing), báo OK / CÓ CẢNH BÁO / CÓ LỖI kênh nào. Ngưỡng ở nhóm hằng `TEST_*` đầu `main.py`

## Logger v1.7.1
- QC không còn đánh dấu "kênh chết" (`dead_ch_*`) cho trial `rest`: lúc nghỉ không có hoạt động cơ nên không phân biệt được
  kênh chết với kênh yên. Kênh có max thấp vẫn được ghi vào `qc.low_ch_info`. Kênh chết thật vẫn bị bắt ở các trial cử động.

## Firmware v1.4 + Logger v1.7 + hw debug v1.4 (cấu hình thu chuẩn hiện tại: 5 kênh)
- Chuyển từ 6 kênh sang **5 kênh** vì cảm biến kênh 1 cũ bị hỏng (tụ cháy). Bộ lọc giữ nguyên như v1.3
  (1 kHz nội bộ, trung bình 20 ms, xuất 100 Hz)
- Chân ADC: GPIO 2, 4, 5, 6, 7 → CH1..CH5 (= CH2..CH6 cũ). Giao thức: `SEQ,TIME_US,CH1..CH5` (7 cột)
- Logger: `NUM_CH` lấy từ `ACQ_CONFIG["channels"]`; `ACQ_CONFIG` ghi thêm `channels` và `gpio_pins`;
  banner firmware phải là v1.4
- `analysis/train_baseline.py` không còn cố định 6 kênh: số kênh lấy từ file CSV (30 đặc trưng với 5 kênh),
  trial có số kênh khác bị bỏ qua
- Firmware tự chọn chân ADC theo board: ESP32-S3 (GPIO 2,4,5,6,7) hoặc ESP32 DevKit V1 (GPIO 32–36, chỉ để test); banner in `# Board=`,
  logger cảnh báo nếu board không phải ESP32-S3
- Bản 6 kênh (firmware v1.3, hw debug v1.3) chuyển vào `firmware/archive/`. **Không trộn dữ liệu 6 kênh với 5 kênh.**

## Logger v1.6 (đi cùng firmware v1.3)
- Dựa trên logger v1.5: QC từng trial (mất gói, reset board, tốc độ, số mẫu, kênh chết/bão hòa),
  START/abort an toàn, metadata ghi atomic, SEQ/TIME_US không tràn 32-bit
- Thích ứng firmware v1.3: 100 Hz (800 mẫu/trial), QC thêm kiểm tra timing (max_dt > 2 chu kỳ)
- Đọc banner firmware: cảnh báo nếu không phải v1.3 hoặc AcqRate/Window/OutRate lệch `ACQ_CONFIG`
- `metadata.json` ghi `acquisition`; `sampling_rate_hz` không ghi đè session cũ (chỉ cảnh báo khi lệch)

## v1.3 (6 kênh, đã thay bằng v1.4)
- Lấy mẫu nội bộ 1 kHz, trung bình trượt 20 ms, xuất 100 Hz
- Triệt nhiễu lưới 50 Hz và gợn 100 Hz (độ lệch chuẩn lúc nghỉ giảm ~10 lần so với v1.2)
- Chân ADC: GPIO 1, 2, 4, 5, 6, 7
- Tự đo thời gian đọc ADC khi khởi động, cảnh báo nếu sát chu kỳ
- Đếm overrun, đồng bộ lại thay vì đọc dồn
- Logger ghi cấu hình thu (`acquisition`) vào `metadata.json`

## v1.2
- Oversampling 8 mẫu/kênh, 200 Hz, đổi sang GPIO 1, 2, 4, 5, 6, 7
- Gợn 100 Hz của lưới điện vẫn còn (bị alias về sát Nyquist)

## v1.1
- Bản gốc: 500 Hz, đọc 1 mẫu/kênh, chân {5, 6, 7, 9, 10, 11}, không lọc
