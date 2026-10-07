# NCKH — Cánh tay giả điều khiển bằng EMG

Hệ thống thu dữ liệu EMG 5 kênh bằng ESP32-S3 để huấn luyện mô hình nhận dạng 8 cử động bàn tay,
phục vụ cánh tay giả cho người cụt dưới khuỷu.

## Kiến trúc hai giai đoạn
- **Phase 1 — Thu dữ liệu:** 5 cảm biến → ADC nội ESP32-S3 → USB Serial → Logger (Python) → CSV
- **Phase 2 — Xử lý AI (offline):** tiền xử lý, cửa sổ trượt, trích đặc trưng, huấn luyện, triển khai lên ESP32

Chi tiết cấu hình thu đã khóa: [`docs/ACQUISITION_CONFIG.md`](docs/ACQUISITION_CONFIG.md)

## Cấu trúc thư mục
```
firmware/   Firmware ESP32-S3: emg_5ch_logger_v1_4 (thu dữ liệu), emg_hw_debug_v1_4 (kiểm tra phần cứng); bản 6 kênh cũ trong archive/
logger/     EMG Logger (Python/PySide6): ghi CSV + metadata.json
Dataset/    Dữ liệu thu (xem Dataset/README.md)
analysis/   Phase 2: QA, tiền xử lý, đặc trưng, huấn luyện (train_baseline.py)
docs/       Cấu hình thu, changelog, báo cáo
```

## Chạy nhanh
1. Mở `firmware/emg_5ch_logger_v1_4/emg_5ch_logger_v1_4.ino` bằng Arduino IDE
   (board ESP32-S3, USB CDC On Boot: Enabled, PSRAM: OPI) và nạp lên board.
2. Cài thư viện và chạy logger:
   ```
   pip install -r logger/requirements.txt
   python logger/main.py
   ```
3. Chọn cổng COM → Connect → bấm **TEST PHẦN CỨNG** (nắm chặt tay theo hướng dẫn, không lưu file) để kiểm tra 5 kênh.
   Khi báo "TẤT CẢ KÊNH OK" mới điền Subject/Session/Donning/Gesture → START.

## Huấn luyện baseline (Phase 2)
```
pip install -r analysis/requirements.txt
python analysis/train_baseline.py --qa-only     # kiểm tra chất lượng từng trial
python analysis/train_baseline.py               # SVM + Random Forest, chia theo session
```
Kết quả nằm trong `analysis/results/` (qa_report.csv, folds.csv, summary.csv, ma trận nhầm lẫn).
Cần tối thiểu 2 session (hoặc 2 người với `--split subject`).

## Quy ước dữ liệu
- Giao thức serial: `SEQ,TIME_US,CH1,CH2,CH3,CH4,CH5` (đã đóng băng)
- Chia train/test theo session hoặc theo lần lặp, không cắt cửa sổ ngẫu nhiên
- Trừ nền theo từng kênh (từ phần Rest đầu trial) và chuẩn hóa theo MVC ở Phase 2
- Cảm biến xuất đường bao nên đặc trưng nên dựa trên mức trung bình, độ dốc, min/max/độ lệch chuẩn trong cửa sổ

## Lưu ý
- Repo này chứa dữ liệu người tham gia: để **private**, dùng mã ẩn danh.
- Không commit token, mật khẩu hay khóa API.
