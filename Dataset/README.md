# Dataset

Thư mục này do `logger/main.py` tự tạo. Cấu trúc:

```
Dataset/
└── S01/                          # Subject ID (dùng mã ẩn danh, không dùng tên thật)
    └── Se01_posA/                # Session ID + vị trí đeo băng (donning position)
        ├── metadata.json         # cấu hình thu + danh sách trial + marker thời gian
        └── raw/
            └── S01_Se01_power_grip_T01.csv
```

Mỗi file CSV có 1 dòng header (`WRITE_CSV_HEADER` trong logger) rồi các dòng dữ liệu:
`SEQ,TIME_US,CH1,CH2,CH3,CH4,CH5` (5 kênh). `train_baseline.py` đọc được cả hai dạng và tự bỏ qua trial có số kênh khác số kênh phổ biến nhất.

Trạng thái trial trong `metadata.json`: `complete` (đạt QC), `suspect` (file đổi tên `_suspect`, nên thu lại),
`aborted` (file đổi tên `_aborted`). Chỉ trial `complete` được dùng để huấn luyện.

Quy ước:
- Mỗi trial 8 s: Rest 2 s → Cue 1 s → Hold 3 s → Relax 2 s.
- `metadata.json` ghi `cue_on_us`, `hold_start_us`, `hold_end_us` theo `TIME_US` của ESP32.
- Chia train/test **theo session hoặc theo lần lặp**, không cắt cửa sổ ngẫu nhiên.
- Không trộn dữ liệu thu ở các cấu hình khác nhau (xem `docs/ACQUISITION_CONFIG.md`).
- Dữ liệu người tham gia: giữ repo ở chế độ **private**, dùng mã ẩn danh.
