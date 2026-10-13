"""
EMG Logger - v1.8 (ban thu chinh thuc, 4 kenh, firmware v1.4)
============================================================
Doc du lieu EMG 4 kenh qua Serial (ESP32-S3) va luu thanh CSV theo
protocol da dong bang cua du an:

    SEQ,TIME_US,CH1,CH2,CH3,CH4

Timeline 1 trial (8s): Rest(2s) -> Cue(1s) -> Hold(3s) -> Relax(2s)
Marker (cue_on_us, hold_start_us, hold_end_us) lay theo TIME_US cua ESP32.

Nang cap so voi Sprint 1:
    [QC]  Kiem tra chat luong moi trial: mat goi (SEQ), reset board, dong loi,
          toc do lay mau, so mau, kenh chet / bao hoa (clipping).
          Trial dat -> "complete"; khong dat -> "suspect" (file doi ten _suspect).
    [SAFE] Khong cho START neu chua co du lieu; tu abort neu du lieu dung > 0.5s
          hoac mat ket noi giua trial. File abort doi ten _aborted.
    [META] Khong con entry trung khi ghi de; metadata.json ghi atomic, file hong
          duoc backup thay vi bi xoa; them qc, arm_side, notes, firmware_header,
          first/last_time_us, timeline, logger_version.
    [FIX] TIME_US/SEQ truyen qua Signal(object) - Signal(int) cu bi tran 32-bit
          (TIME_US > 2^31 ~ sau 35.8 phut ESP32 chay -> thanh so am).
    [UI]  Hien ten gesture lon trong luc thu; tu goi y so Trial tiep theo cho
          tung gesture; cap nhat label 1 lan/giay (khong 500 lan/giay);
          tick 10ms de marker sat thoi diem doi trang thai.
    [CSV] Them dong header (tat bang WRITE_CSV_HEADER = False).

Thich ung firmware v1.3/v1.4 (lay mau 1 kHz, trung binh 20 ms, xuat 100 Hz):
    [RATE] NOMINAL_RATE_HZ / EXPECTED_SAMPLES lay tu ACQ_CONFIG (100 Hz, 800 mau/trial)
           thay cho 500 Hz / 4000 mau cu.
    [QC]   Them kiem tra timing: max_dt giua 2 mau > 2 chu ky (firmware bi tre nhip).
    [FW]   Doc banner '#' cua firmware: canh bao neu khong phai v1.4 hoac AcqRate /
           Window / OutRate lech ACQ_CONFIG; hien canh bao WARNING cua firmware.
    [META] metadata.json ghi them 'acquisition' (ACQ_CONFIG); sampling_rate_hz KHONG ghi
           de session cu - neu lech cau hinh hien tai thi canh bao trong log.
    [PATH] DATASET_ROOT = <repo>/Dataset (khong phu thuoc thu muc chay lenh).

    [TEST] v1.8: nut "TEST PHAN CUNG": chay dung timeline 8 s nhu trial that voi cu dong nam chat tay
           (Rest -> Cue -> Hold -> Relax), KHONG ghi CSV/metadata. Cuoi test phan tich tung kenh
           (nen luc nghi, muc gong, ty le, nhieu, bao hoa, kenh chet) + duong truyen (mat goi, toc do,
           timing) va bao OK (co the thu data) / CANH BAO / LOI kenh nao.
    [QC]   v1.7.1: kenh "chet" (max < DEAD_CH_MAX) KHONG con bi danh dau o trial rest; ghi vao qc.low_ch_info.
    [CH]   v1.7: 4 kenh (NUM_CH lay tu ACQ_CONFIG["channels"]); dong serial hop le co NUM_CH + 2 cot.
           CH1..CH4 = GPIO 1,2,4,5.

Khong lam: chua ve realtime plot (PyQtGraph) - danh cho sprint sau.
"""

import sys
import os
import re
import statistics
import json
import time
from datetime import datetime

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QPushButton, QLineEdit, QSpinBox, QTextEdit,
    QGroupBox, QMessageBox, QProgressBar, QInputDialog
)

import serial
import serial.tools.list_ports

LOGGER_VERSION = "1.8"
BAUDRATE = 921600
# Dataset luon nam o goc repo (../Dataset), bat ke chay script tu thu muc nao
DATASET_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Dataset")
)

# Cau hinh thu CHUAN (khop firmware emg_4ch_logger_v1_4.ino) - ghi vao metadata.json
# Neu doi firmware/cau hinh thi sua o day va coi do la cau hinh moi.
ACQ_CONFIG = {
    "firmware": "emg_4ch_logger_v1_4",
    "channels": 4,              # 4 kenh
    "gpio_pins": [1, 2, 4, 5],   # CH1..CH4
    "acq_rate_hz": 1000,        # toc do lay mau ADC noi bo
    "boxcar_ms": 20,            # trung binh truot 20 ms (triet 50/100 Hz)
    "output_rate_hz": 100,      # toc do xuat ra serial
    "adc_attenuation": "11db",
    "adc_resolution_bits": 12,
    "serial_protocol": "SEQ,TIME_US,CH1..CH4",
}
FW_BANNER_VERSION = "v1.4"      # chuoi phai xuat hien trong dong '# ... READY ...' cua firmware

NUM_CH = ACQ_CONFIG["channels"]   # 4 kenh
NOMINAL_RATE_HZ = ACQ_CONFIG["output_rate_hz"]   # 100 Hz (toc do xuat cua firmware v1.4)
ADC_MAX = 4095
CSV_HEADER = "SEQ,TIME_US," + ",".join(f"CH{i + 1}" for i in range(NUM_CH))
WRITE_CSV_HEADER = True

# Gesture mac dinh (8 gesture goc o dau, giu nguyen thu tu de khong lech du lieu cu)
DEFAULT_GESTURES = [
    "rest",
    "power_grip",
    "hand_open",
    "wrist_flexion",
    "wrist_extension",
    "pinch",
    "lateral_pinch",
    "pronation_supination",
    # --- moi them ---
    "tripod_grip",           # kep 3 ngon (cai + tro + giua)
    "index_point",           # chi bang ngon tro, cac ngon con lai nam
    "thumb_flexion",         # gap ngon cai
    "thumb_extension",       # duoi ngon cai
    "pronation",             # up long ban tay (rieng le)
    "supination",            # ngua long ban tay (rieng le)
    "ulnar_deviation",       # nghieng co tay ve phia ngon ut
    "radial_deviation",      # nghieng co tay ve phia ngon cai
    "cylindrical_grip",      # cam vat tru (chai, coc)
    "spherical_grip",        # cam vat cau (qua bong)
]
# Gesture do nguoi dung tu them qua nut "+" luu o file nay (canh main.py)
CUSTOM_GESTURES_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "custom_gestures.json"
)
MAX_GESTURE_LEN = 40

# Timeline (ms) - moc thoi gian TUYET DOI tinh tu luc bam Start
T_REST_END = 2000       # 0 -> 2s   : Rest
T_CUE_END = 3000        # 2 -> 3s   : Cue ("Get ready")   -> marker CUE_ON tai 2s
T_HOLD_END = 6000       # 3 -> 6s   : Hold (giu gesture)  -> marker HOLD_START tai 3s
T_TRIAL_END = 8000      # 6 -> 8s   : Relax               -> marker HOLD_END tai 6s

# ---------------- Nguong QC (chinh o day neu can) ----------------
EXPECTED_SAMPLES = NOMINAL_RATE_HZ * T_TRIAL_END // 1000   # 800
RATE_TOL = 0.05          # toc do do duoc phai nam trong 100Hz +-5%
COUNT_TOL = 0.05         # so mau trong trial phai nam trong 800 +-5%
MAX_LOST = 0             # so goi mat toi da cho phep / trial
MAX_DT_FACTOR = 2.0      # khoang cach 2 mau > 2 chu ky (20 ms) -> firmware tre nhip
CLIP_LEVEL = 4090        # >= muc nay coi la bao hoa ADC
MAX_CLIP_FRAC = 0.2      # kenh bao hoa > 20% so mau -> loi
DEAD_CH_MAX = 20         # kenh co max < 20 trong ca trial -> kenh chet (CHI xet voi trial KHONG phai "rest":
                         # luc nghi tin hieu khong co hoat dong nen khong phan biet duoc kenh chet voi kenh yen)
STALE_S = 0.5            # khong co mau moi > 0.5s -> coi la mat du lieu

# ---------------- Test phan cung (nut TEST) ----------------
TEST_GESTURE = "power_grip"   # cu dong dung de test: nam chat tay (khong luu file)
TEST_BASE_FROM_S = 0.5       # bo 0.5 s dau khi tinh nen (on dinh sau khi bam nut)
TEST_SMOOTH_N = max(2, NOMINAL_RATE_HZ // 2)   # cua so trung binh 0.5 s de do muc gong
TEST_MIN_DELTA = 150         # gong - nen < 150 count -> kenh khong dap ung
TEST_MIN_RATIO = 1.4         # gong/nen < 1.4 -> khong dap ung (chi xet khi nen >= TEST_RATIO_BASE_MIN)
TEST_RATIO_BASE_MIN = 100
TEST_REST_STD_MAX = 60.0     # do lech chuan luc nghi > 60 -> nhieu cao (canh bao)
TEST_BASE_MAX = 1500.0       # nen luc nghi > 1500 -> nen cao (canh bao)


def analyze_hw_test(t, X, cue, hs, he, clip=None, n_ch=None):
    """Phan tich tung kenh sau test nam chat tay.

    t: list thoi gian (giay) tinh tu mau dau;  X: list tuple n_ch gia tri;
    cue/hs/he: moc (giay) CUE_ON, HOLD_START, HOLD_END;  clip: so mau >= CLIP_LEVEL moi kenh.
    Tra ve list dict: ch, base, rest_std, level, delta, ratio, hold_max, all_max, status, msg.
    status: "OK" | "WARN" | "FAIL".
    """
    n_ch = n_ch or (len(X[0]) if X else 0)
    clip = clip or [0] * n_ch
    rest_idx = [i for i, ti in enumerate(t) if TEST_BASE_FROM_S <= ti < cue]
    hold_idx = [i for i, ti in enumerate(t) if hs <= ti <= he + 0.5]
    res = []
    for c in range(n_ch):
        col = [row[c] for row in X]
        rest = [col[i] for i in rest_idx]
        base = statistics.median(rest) if rest else 0.0
        rstd = statistics.pstdev(rest) if len(rest) > 1 else 0.0
        seg = [col[i] for i in hold_idx]
        w = TEST_SMOOTH_N
        level = 0.0
        if len(seg) >= w:
            run = sum(seg[:w])
            level = run / w
            for k in range(w, len(seg)):
                run += seg[k] - seg[k - w]
                level = max(level, run / w)
        elif seg:
            level = sum(seg) / len(seg)
        hold_max = max(seg) if seg else 0
        all_max = max(col) if col else 0
        delta = level - base
        ratio = level / max(base, 1.0)
        n = len(col)

        if all_max < DEAD_CH_MAX:
            status, msg = "FAIL", "KHONG CO TIN HIEU (kiem tra nguon +-Vs, day SIG/GND, noi chung GND)"
        elif delta < TEST_MIN_DELTA or (base >= TEST_RATIO_BASE_MIN and ratio < TEST_MIN_RATIO):
            status, msg = "FAIL", "KHONG DAP UNG khi gong (kiem tra dien cuc/tiep xuc da/vi tri cam bien/gain)"
        elif clip[c] > MAX_CLIP_FRAC * n:
            status, msg = "FAIL", "BAO HOA nhieu - giam gain"
        elif clip[c] > 0:
            status, msg = "WARN", "co mau bao hoa khi gong - nen giam gain"
        elif rstd > TEST_REST_STD_MAX:
            status, msg = "WARN", "nhieu cao luc nghi (kiem tra dat/day/dien cuc)"
        elif base > TEST_BASE_MAX:
            status, msg = "WARN", "nen luc nghi cao - giam gain/kiem tra offset"
        else:
            status, msg = "OK", "OK"
        res.append(dict(ch=c + 1, base=base, rest_std=rstd, level=level, delta=delta,
                        ratio=ratio, hold_max=hold_max, all_max=all_max, status=status, msg=msg))
    return res


def summarize_hw_test(results, transport_problems):
    """Tra ve (level, title, text); level: "ok" | "warn" | "fail"."""
    lines = []
    for r in results:
        lines.append(
            f"CH{r['ch']}: nen {r['base']:.0f}, gong {r['level']:.0f} "
            f"(+{r['delta']:.0f}, x{r['ratio']:.1f}) - {r['msg']}"
        )
    fails = [r for r in results if r["status"] == "FAIL"]
    warns = [r for r in results if r["status"] == "WARN"]
    extra = []
    if transport_problems:
        extra.append("Duong truyen: " + ", ".join(transport_problems)
                     + " (kiem tra cap USB/firmware/toc do lay mau)")
    all_unresponsive = (len(results) > 0 and len(fails) == len(results)
                        and all("KHONG DAP UNG" in r["msg"] for r in fails))
    if all_unresponsive:
        extra.append("Khong kenh nao dap ung: co the ban chua nam du chat/dung luc. Thu lai.")

    if fails or transport_problems:
        names = ", ".join(f"CH{r['ch']}" for r in fails) or "duong truyen"
        level, title = "fail", f"CO LOI: {names}"
    elif warns:
        names = ", ".join(f"CH{r['ch']}" for r in warns)
        level, title = "warn", f"OK, CO CANH BAO: {names}"
    else:
        level = "ok"
        title = f"TAT CA {len(results)} KENH OK - CO THE BAT DAU THU DATA"
    text = title + "\n\n" + "\n".join(lines + extra)
    return level, title, text


def clean_name(s):
    """Chi giu ky tu an toan cho ten thu muc / file."""
    return re.sub(r"[^A-Za-z0-9_\-]", "_", s.strip())


def normalize_gesture(s):
    """Chuan hoa ten gesture: chu thuong, chi gom a-z 0-9 _ -  (an toan cho ten file,
    va khong chua khoang trang de regex _suggest_trial hoat dong dung)."""
    name = clean_name(s).strip("_-").lower()
    name = re.sub(r"_+", "_", name)
    return name[:MAX_GESTURE_LEN]


def load_custom_gestures():
    """Doc gesture tu them tu CUSTOM_GESTURES_FILE. Loi/hong file -> tra list rong."""
    try:
        with open(CUSTOM_GESTURES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    if isinstance(data, list):
        for g in data:
            if isinstance(g, str):
                n = normalize_gesture(g)
                if n and n not in DEFAULT_GESTURES and n not in out:
                    out.append(n)
    return out


def save_custom_gestures(custom):
    """Ghi atomic (file tam roi doi ten) de khong hong file neu crash giua chung."""
    tmp = CUSTOM_GESTURES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(custom, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CUSTOM_GESTURES_FILE)


class SerialReaderThread(QThread):
    """Doc lien tuc tu Serial, khong block UI. Parse dung protocol:
    SEQ,TIME_US,CH1,CH2,CH3,CH4

    sample_received phat 1 tuple (seq, time_us, (ch1..ch4), raw_line).
    Dung Signal(object) vi TIME_US la uint32 - Signal(int) chi 32-bit co dau.
    """
    sample_received = Signal(object)
    header_received = Signal(str)     # cac dong '# ...' (banner firmware)
    error_occurred = Signal(str)

    def __init__(self, port, baudrate=BAUDRATE):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self._running = False
        self._ser = None
        self.bad_lines = 0            # dem dong sai protocol / ngoai khoang ADC

    def run(self):
        try:
            self._ser = serial.Serial(self.port, self.baudrate, timeout=1)
        except Exception as e:
            self.error_occurred.emit(f"Khong mo duoc port {self.port}: {e}")
            return

        self._running = True
        self._ser.reset_input_buffer()

        while self._running:
            try:
                raw = self._ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                if line.startswith("#"):
                    self.header_received.emit(line)
                    continue
                parts = line.split(",")
                if len(parts) != NUM_CH + 2:
                    self.bad_lines += 1
                    continue
                vals = [int(x) for x in parts]
                seq, t_us = vals[0], vals[1]
                ch = tuple(vals[2:])
                if seq < 0 or t_us < 0 or any(v < 0 or v > ADC_MAX for v in ch):
                    self.bad_lines += 1
                    continue
                self.sample_received.emit((seq, t_us, ch, line))
            except ValueError:
                self.bad_lines += 1
                continue
            except Exception as e:
                self.error_occurred.emit(f"Loi doc serial: {e}")
                break

    def stop(self):
        self._running = False
        self.wait(1500)
        if self._ser and self._ser.is_open:
            self._ser.close()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"EMG Logger v{LOGGER_VERSION}")
        self.resize(820, 780)

        self.reader_thread = None
        self.csv_file = None
        self.recording = False
        self.trial_active = False
        self.test_mode = False          # True khi dang chay TEST PHAN CUNG (khong ghi file)
        self._test_samples = []
        self.trial_start_ms = None
        self.latest_time_us = None
        self._last_sample_mono = None
        self.markers = {}
        self.sample_count_total = 0
        self.sample_count_trial = 0
        self._rate_window_count = 0
        self._current_state = "idle"
        self.firmware_header = []
        self._bad_at_start = 0
        self.current_filepath = None
        self.current_session_dir = None
        self.current_meta = {}
        self._qc_reset()

        self.state_timer = QTimer(self)
        self.state_timer.setInterval(10)
        self.state_timer.timeout.connect(self._tick)

        self.rate_timer = QTimer(self)
        self.rate_timer.setInterval(1000)
        self.rate_timer.timeout.connect(self._update_status)

        self._build_ui()
        self._refresh_ports()
        self._suggest_trial()
        self._log(f"Dataset root: {os.path.abspath(DATASET_ROOT)}")

    # ---------------- UI ----------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # --- Serial connection group ---
        conn_group = QGroupBox("Ket noi Serial")
        conn_layout = QHBoxLayout(conn_group)
        self.port_combo = QComboBox()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_ports)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._toggle_connection)
        conn_layout.addWidget(QLabel("Port:"))
        conn_layout.addWidget(self.port_combo, 1)
        conn_layout.addWidget(refresh_btn)
        conn_layout.addWidget(QLabel(f"Baud: {BAUDRATE}"))
        conn_layout.addWidget(self.connect_btn)
        layout.addWidget(conn_group)

        # --- Session/trial labeling group ---
        label_group = QGroupBox("Thong tin ghi")
        grid = QGridLayout(label_group)

        self.subject_edit = QLineEdit("S01")
        self.session_edit = QLineEdit("Se01")
        self.position_edit = QLineEdit("A")
        self.custom_gestures = load_custom_gestures()
        self.gesture_combo = QComboBox()
        self.gesture_combo.addItems(DEFAULT_GESTURES + self.custom_gestures)
        self.add_gesture_btn = QPushButton("+")
        self.add_gesture_btn.setFixedWidth(32)
        self.add_gesture_btn.setToolTip("Them gesture moi (luu vao custom_gestures.json)")
        self.add_gesture_btn.clicked.connect(self._add_gesture)
        gesture_box = QWidget()
        gesture_row = QHBoxLayout(gesture_box)
        gesture_row.setContentsMargins(0, 0, 0, 0)
        gesture_row.addWidget(self.gesture_combo, 1)
        gesture_row.addWidget(self.add_gesture_btn)
        self.trial_spin = QSpinBox()
        self.trial_spin.setRange(1, 999)
        self.trial_spin.setValue(1)
        self.arm_combo = QComboBox()
        self.arm_combo.addItems(["right", "left"])
        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("Ghi chu (vd: da lau da, tay do mo hoi, ...)")

        grid.addWidget(QLabel("Subject ID:"), 0, 0)
        grid.addWidget(self.subject_edit, 0, 1)
        grid.addWidget(QLabel("Session ID:"), 0, 2)
        grid.addWidget(self.session_edit, 0, 3)
        grid.addWidget(QLabel("Donning pos:"), 1, 0)
        grid.addWidget(self.position_edit, 1, 1)
        grid.addWidget(QLabel("Gesture:"), 1, 2)
        grid.addWidget(gesture_box, 1, 3)
        grid.addWidget(QLabel("Trial #:"), 2, 0)
        grid.addWidget(self.trial_spin, 2, 1)
        grid.addWidget(QLabel("Arm side:"), 2, 2)
        grid.addWidget(self.arm_combo, 2, 3)
        grid.addWidget(QLabel("Notes:"), 3, 0)
        grid.addWidget(self.notes_edit, 3, 1, 1, 3)
        layout.addWidget(label_group)

        # tu goi y so trial tiep theo khi doi subject/session/pos/gesture
        self.subject_edit.textChanged.connect(self._suggest_trial)
        self.session_edit.textChanged.connect(self._suggest_trial)
        self.position_edit.textChanged.connect(self._suggest_trial)
        self.gesture_combo.currentIndexChanged.connect(self._suggest_trial)

        # --- Trial control group ---
        trial_group = QGroupBox("Trial (8s: Rest 2s -> Cue 1s -> Hold 3s -> Relax 2s)")
        trial_layout = QVBoxLayout(trial_group)

        self.state_label = QLabel("IDLE")
        self.state_label.setAlignment(Qt.AlignCenter)
        self._set_state_label("IDLE", "#eceff1")
        self.progress = QProgressBar()
        self.progress.setRange(0, T_TRIAL_END)
        self.progress.setValue(0)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("START")
        self.start_btn.setStyleSheet("font-size: 16px; padding: 8px;")
        self.start_btn.clicked.connect(self._start_trial)
        self.stop_btn = QPushButton("STOP")
        self.stop_btn.setStyleSheet("font-size: 16px; padding: 8px;")
        self.stop_btn.clicked.connect(lambda: self._abort_trial("user"))
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)

        self.test_btn = QPushButton("TEST PHAN CUNG (nam chat tay, KHONG luu file)")
        self.test_btn.setStyleSheet("font-size: 14px; padding: 6px;")
        self.test_btn.setToolTip(
            "Chay dung 8 s nhu trial that voi cu dong nam chat tay, kiem tra 4 kenh + duong truyen.\n"
            "Khong ghi CSV, khong ghi metadata.")
        self.test_btn.clicked.connect(self._start_test)

        trial_layout.addWidget(self.state_label)
        trial_layout.addWidget(self.progress)
        trial_layout.addLayout(btn_row)
        trial_layout.addWidget(self.test_btn)
        layout.addWidget(trial_group)

        # --- Status group ---
        status_group = QGroupBox("Trang thai")
        status_layout = QHBoxLayout(status_group)
        self.sample_count_label = QLabel("Samples: 0")
        self.rate_label = QLabel("Rate: -- Hz")
        self.lost_label = QLabel("Lost: 0")
        self.file_label = QLabel("File: --")
        status_layout.addWidget(self.sample_count_label)
        status_layout.addWidget(self.rate_label)
        status_layout.addWidget(self.lost_label)
        status_layout.addWidget(self.file_label, 1)
        layout.addWidget(status_group)

        # --- Log ---
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        layout.addWidget(self.log_edit, 1)

    def _set_state_label(self, text, color):
        self.state_label.setText(text)
        self.state_label.setStyleSheet(
            f"font-size: 28px; font-weight: bold; padding: 12px; background:{color};"
        )

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_edit.append(f"[{ts}] {msg}")

    # ---------------- Serial connect ----------------
    def _refresh_ports(self):
        self.port_combo.clear()
        ports = serial.tools.list_ports.comports()
        for p in ports:
            self.port_combo.addItem(f"{p.device} - {p.description}", p.device)
        if not ports:
            self._log("Khong tim thay COM port nao.")

    def _toggle_connection(self):
        if self.reader_thread is None:
            port = self.port_combo.currentData()
            if not port:
                QMessageBox.warning(self, "Loi", "Chua chon COM port.")
                return
            self.firmware_header = []
            self.latest_time_us = None
            self._last_sample_mono = None
            self.reader_thread = SerialReaderThread(port, BAUDRATE)
            self.reader_thread.sample_received.connect(self._on_sample)
            self.reader_thread.header_received.connect(self._on_header)
            self.reader_thread.error_occurred.connect(self._on_serial_error)
            self.reader_thread.start()
            self.connect_btn.setText("Disconnect")
            self.rate_timer.start()
            self._log(f"Da ket noi {port} @ {BAUDRATE} baud.")
        else:
            self._disconnect()

    def _disconnect(self):
        if self.trial_active:
            self._abort_trial("disconnected")
        if self.reader_thread:
            self.reader_thread.stop()
            self.reader_thread = None
        self.rate_timer.stop()
        self.connect_btn.setText("Connect")
        self._log("Da ngat ket noi.")

    def _on_serial_error(self, msg):
        self._log(f"LOI: {msg}")
        self._disconnect()
        QMessageBox.critical(self, "Loi Serial", msg)

    def _on_header(self, line):
        if line not in self.firmware_header and len(self.firmware_header) < 20:
            self.firmware_header.append(line)
            self._log(f"FW: {line}")
            self._check_firmware_line(line)

    def _check_firmware_line(self, line):
        """Doi chieu banner firmware v1.4 voi ACQ_CONFIG; chi canh bao, khong chan."""
        if "READY" in line and FW_BANNER_VERSION not in line:
            self._log(f"CANH BAO: firmware khong phai {FW_BANNER_VERSION} ({line}). "
                      f"Nap lai {ACQ_CONFIG['firmware']}.ino.")
        for key, cfg_key in (("AcqRate_Hz", "acq_rate_hz"), ("Window_ms", "boxcar_ms"),
                             ("OutRate_Hz", "output_rate_hz")):
            m = re.search(rf"{key}=(\d+)", line)
            if m and int(m.group(1)) != ACQ_CONFIG[cfg_key]:
                self._log(f"CANH BAO: firmware bao {key}={m.group(1)} nhung ACQ_CONFIG "
                          f"{cfg_key}={ACQ_CONFIG[cfg_key]}. Kiem tra firmware / ACQ_CONFIG.")
        m = re.search(r"Board=(.+)", line)
        if m and "ESP32-S3" not in m.group(1):
            self._log(f"CANH BAO: board la '{m.group(1).strip()}' - chi de TEST, "
                      "khong dung de thu dataset chinh thuc (ADC khac ESP32-S3).")
        if "WARNING" in line:
            self._log(f"CANH BAO TU FIRMWARE: {line}")

    # ---------------- QC ----------------
    def _qc_reset(self):
        self.qc = dict(
            n=0, first_seq=None, last_seq=None, lost=0, resets=0,
            first_t=None, last_t=None, max_dt=0,
            mn=[ADC_MAX] * NUM_CH, mx=[0] * NUM_CH, clip=[0] * NUM_CH,
        )

    def _qc_update(self, seq, t_us, ch):
        q = self.qc
        if q["last_seq"] is None:
            q["first_seq"] = seq
            q["first_t"] = t_us
        else:
            if seq <= q["last_seq"]:
                q["resets"] += 1                      # board reset / SEQ nhay lui
            else:
                q["lost"] += seq - q["last_seq"] - 1  # thieu goi
            dt = (t_us - q["last_t"]) & 0xFFFFFFFF
            if dt > q["max_dt"]:
                q["max_dt"] = dt
        q["last_seq"] = seq
        q["last_t"] = t_us
        q["n"] += 1
        for i, v in enumerate(ch):
            if v < q["mn"][i]:
                q["mn"][i] = v
            if v > q["mx"][i]:
                q["mx"][i] = v
            if v >= CLIP_LEVEL:
                q["clip"][i] += 1

    def _qc_finalize(self):
        """Tra ve (qc_dict, problems[]). problems rong = trial dat."""
        q = self.qc
        n = q["n"]
        dur = 0.0
        if n > 1:
            dur = ((q["last_t"] - q["first_t"]) & 0xFFFFFFFF) / 1e6
        rate = (n - 1) / dur if dur > 0 else 0.0
        bad_lines = 0
        if self.reader_thread is not None:
            bad_lines = self.reader_thread.bad_lines - self._bad_at_start

        problems = []
        if n == 0:
            problems.append("no_data")
        else:
            if q["lost"] > MAX_LOST:
                problems.append(f"lost_{q['lost']}")
            if q["resets"]:
                problems.append("seq_reset")
            if bad_lines:
                problems.append(f"bad_lines_{bad_lines}")
            if abs(rate - NOMINAL_RATE_HZ) > RATE_TOL * NOMINAL_RATE_HZ:
                problems.append(f"rate_{rate:.0f}Hz")
            if q["max_dt"] > MAX_DT_FACTOR * 1e6 / NOMINAL_RATE_HZ:
                problems.append(f"timing_maxdt_{q['max_dt']}us")
            if not (EXPECTED_SAMPLES * (1 - COUNT_TOL) <= n
                    <= EXPECTED_SAMPLES * (1 + COUNT_TOL)):
                problems.append(f"count_{n}")
            dead = [i + 1 for i in range(NUM_CH) if q["mx"][i] < DEAD_CH_MAX]
            sat = [i + 1 for i in range(NUM_CH) if q["clip"][i] > MAX_CLIP_FRAC * n]
            # Trial "rest": khong co hoat dong co -> kenh co max thap la binh thuong, chi ghi thong tin
            # (kenh chet that se bi bat o cac trial cu dong khac, vi cu dong phai lam kenh do len cao)
            is_rest = self.current_meta.get("gesture") == "rest"
            if dead and not is_rest:
                problems.append("dead_ch_" + "-".join(map(str, dead)))
            if sat:
                problems.append("saturated_ch_" + "-".join(map(str, sat)))

        qc = {
            "n_samples": n,
            "expected_samples": EXPECTED_SAMPLES,
            "rate_hz": round(rate, 1),
            "duration_s": round(dur, 3),
            "lost_packets": q["lost"],
            "seq_resets": q["resets"],
            "bad_lines": bad_lines,
            "max_dt_us": q["max_dt"],
            "clip_count": list(q["clip"]),
            "ch_min": list(q["mn"]) if n else None,
            "ch_max": list(q["mx"]) if n else None,
            "low_ch_info": [i + 1 for i in range(NUM_CH) if n and q["mx"][i] < DEAD_CH_MAX],
            "problems": problems,
        }
        return qc, problems

    # ---------------- Sample handling ----------------
    def _on_sample(self, payload):
        seq, t_us, ch, raw_line = payload
        self.latest_time_us = t_us
        self._last_sample_mono = time.monotonic()
        self.sample_count_total += 1
        self._rate_window_count += 1

        if self.recording and self.csv_file:
            self.csv_file.write(raw_line + "\n")
            self.sample_count_trial += 1
            self._qc_update(seq, t_us, ch)
        elif self.test_mode:
            # TEST: chi giu trong RAM, khong ghi file
            self._test_samples.append((t_us, tuple(ch)))
            self.sample_count_trial += 1
            self._qc_update(seq, t_us, ch)

    def _update_status(self):
        rate = self._rate_window_count
        self._rate_window_count = 0
        self.rate_label.setText(f"Rate: {rate} Hz")
        low = rate < NOMINAL_RATE_HZ * 0.9
        self.rate_label.setStyleSheet("color: #c62828; font-weight: bold;" if low else "")
        self.sample_count_label.setText(
            f"Samples: {self.sample_count_total} (trial: {self.sample_count_trial})"
        )
        self.lost_label.setText(f"Lost: {self.qc['lost']}")

    # ---------------- Them gesture ----------------
    def _add_gesture(self):
        if self.trial_active:
            QMessageBox.information(self, "Dang thu", "Khong them gesture khi dang thu trial.")
            return
        text, ok = QInputDialog.getText(
            self, "Them gesture",
            "Ten gesture moi (vd: thumb_up, ok_sign):\n"
            "Chi dung chu/so/_/-, khoang trang se thanh '_'.")
        if not ok:
            return
        name = normalize_gesture(text.replace(" ", "_"))
        if not name:
            QMessageBox.warning(self, "Loi", "Ten gesture khong hop le.")
            return
        idx = self.gesture_combo.findText(name)
        if idx >= 0:                      # da co -> chi chon lai, khong them trung
            self.gesture_combo.setCurrentIndex(idx)
            self._log(f"Gesture '{name}' da co san, chon lai.")
            return
        self.custom_gestures.append(name)
        try:
            save_custom_gestures(self.custom_gestures)
        except OSError as e:
            self._log(f"Them '{name}' vao combo nhung KHONG luu duoc file ({e}); "
                      f"se mat khi tat chuong trinh.")
        else:
            self._log(f"Da them gesture '{name}' -> {CUSTOM_GESTURES_FILE}")
        self.gesture_combo.addItem(name)
        self.gesture_combo.setCurrentIndex(self.gesture_combo.count() - 1)

    # ---------------- Trial numbering ----------------
    def _suggest_trial(self, *_):
        """Dat Trial # = so tiep theo chua co file 'complete' cho gesture nay."""
        if self.trial_active:
            return
        subject = clean_name(self.subject_edit.text())
        session = clean_name(self.session_edit.text())
        position = clean_name(self.position_edit.text())
        if not (subject and session):
            return
        gesture = self.gesture_combo.currentText()
        raw_dir = os.path.join(DATASET_ROOT, subject, f"{session}_pos{position}", "raw")
        n = 0
        if os.path.isdir(raw_dir):
            pat = re.compile(
                rf"^{re.escape(subject)}_{re.escape(session)}_{re.escape(gesture)}_T(\d+)\.csv$"
            )
            for f in os.listdir(raw_dir):
                m = pat.match(f)
                if m:
                    n = max(n, int(m.group(1)))
        self.trial_spin.setValue(min(n + 1, self.trial_spin.maximum()))

    # ---------------- Trial state machine ----------------
    def _start_trial(self):
        if self.reader_thread is None:
            QMessageBox.warning(self, "Loi", "Chua ket noi Serial.")
            return
        if self.trial_active:
            return
        if (self._last_sample_mono is None
                or time.monotonic() - self._last_sample_mono > STALE_S):
            QMessageBox.warning(
                self, "Khong co du lieu",
                "Da ket noi nhung chua nhan duoc mau EMG nao (hoac dang bi dung).\n"
                "Kiem tra firmware / cap USB / baudrate roi thu lai."
            )
            return

        subject = clean_name(self.subject_edit.text())
        session = clean_name(self.session_edit.text())
        position = clean_name(self.position_edit.text())
        gesture = self.gesture_combo.currentText()
        trial_num = self.trial_spin.value()

        if not subject or not session or not position:
            QMessageBox.warning(self, "Loi", "Thieu Subject ID / Session ID / Donning pos.")
            return

        session_dir = os.path.join(DATASET_ROOT, subject, f"{session}_pos{position}")
        raw_dir = os.path.join(session_dir, "raw")
        os.makedirs(raw_dir, exist_ok=True)

        filename = f"{subject}_{session}_{gesture}_T{trial_num:02d}.csv"
        filepath = os.path.join(raw_dir, filename)

        if os.path.exists(filepath):
            resp = QMessageBox.question(
                self, "File da ton tai",
                f"{filename} da ton tai. Ghi de?",
                QMessageBox.Yes | QMessageBox.No
            )
            if resp != QMessageBox.Yes:
                return

        try:
            self.csv_file = open(filepath, "w", newline="")
        except OSError as e:
            QMessageBox.critical(self, "Loi", f"Khong tao duoc file:\n{e}")
            return
        if WRITE_CSV_HEADER:
            self.csv_file.write(CSV_HEADER + "\n")

        self.current_filepath = filepath
        self.current_session_dir = session_dir
        self.current_meta = {
            "subject_id": subject,
            "session_id": session,
            "donning_position": position,
            "arm_side": self.arm_combo.currentText(),
            "notes": self.notes_edit.text().strip(),
            "gesture": gesture,
            "trial": trial_num,
            "file": os.path.relpath(filepath, session_dir).replace("\\", "/"),
        }

        self.markers = {}
        self.sample_count_trial = 0
        self._qc_reset()
        self._bad_at_start = self.reader_thread.bad_lines
        self.recording = True
        self.trial_active = True
        self.trial_start_ms = time.monotonic() * 1000.0
        self._current_state = "rest"

        self.start_btn.setEnabled(False)
        self.test_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.file_label.setText(f"File: {filepath}")
        self._set_state_label(f"REST\n{gesture}", "#cfd8dc")
        self._log(f"Bat dau trial: {filename}")
        self.state_timer.start()

    # ---------------- TEST PHAN CUNG (khong luu file) ----------------
    def _start_test(self):
        if self.reader_thread is None:
            QMessageBox.warning(self, "Loi", "Chua ket noi Serial.")
            return
        if self.trial_active:
            return
        if (self._last_sample_mono is None
                or time.monotonic() - self._last_sample_mono > STALE_S):
            QMessageBox.warning(
                self, "Khong co du lieu",
                "Da ket noi nhung chua nhan duoc mau EMG nao (hoac dang bi dung).\n"
                "Kiem tra firmware / cap USB / baudrate roi thu lai."
            )
            return
        QMessageBox.information(
            self, "Test phan cung",
            "Test chay 8 giay giong trial that, KHONG luu file.\n\n"
            "- 0-2 s: THA LONG hoan toan\n- 2-3 s: chuan bi\n"
            "- 3-6 s: NAM CHAT TAY het suc va giu\n- 6-8 s: tha long\n\n"
            "Bam OK de bat dau."
        )
        self.test_mode = True
        self._test_samples = []
        self.current_meta = {"gesture": TEST_GESTURE}
        self.current_filepath = None
        self.markers = {}
        self.sample_count_trial = 0
        self._qc_reset()
        self._bad_at_start = self.reader_thread.bad_lines
        self.recording = False
        self.trial_active = True
        self.trial_start_ms = time.monotonic() * 1000.0
        self._current_state = "rest"
        self.start_btn.setEnabled(False)
        self.test_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.file_label.setText("File: (TEST - khong luu)")
        self._set_state_label("TEST\nTHA LONG", "#cfd8dc")
        self._log("Bat dau TEST PHAN CUNG (khong luu file).")
        self.state_timer.start()

    def _finish_test(self):
        self._stop_recording()
        self.test_mode = False
        qc, problems = self._qc_finalize()
        samples, self._test_samples = self._test_samples, []

        self.progress.setValue(T_TRIAL_END)
        self.start_btn.setEnabled(True)
        self.test_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.file_label.setText("File: --")

        m = self.markers
        if (len(samples) < 20 or None in (m.get("cue_on_us"), m.get("hold_start_us"),
                                          m.get("hold_end_us"))):
            self._set_state_label("TEST LOI - KHONG DU DATA", "#ff8a65")
            self._log("TEST: khong du du lieu de danh gia.")
            self._show_test_result("fail", "KHONG DU DU LIEU",
                                   "Khong nhan du mau EMG trong luc test. Kiem tra ket noi roi thu lai.")
            return

        t0 = samples[0][0]
        rel = lambda u: ((u - t0) & 0xFFFFFFFF) / 1e6
        t = [rel(u) for u, _ in samples]
        X = [c for _, c in samples]
        results = analyze_hw_test(t, X, rel(m["cue_on_us"]), rel(m["hold_start_us"]),
                                  rel(m["hold_end_us"]), clip=self.qc["clip"], n_ch=NUM_CH)
        transport = [p for p in problems
                     if not p.startswith(("dead_ch_", "saturated_ch_"))]
        level, title, text = summarize_hw_test(results, transport)

        colors = {"ok": "#a5d6a7", "warn": "#fff59d", "fail": "#ef9a9a"}
        self._set_state_label("TEST OK\nCO THE THU DATA" if level == "ok"
                              else ("TEST: CANH BAO" if level == "warn" else "TEST: CO LOI"),
                              colors[level])
        for line in text.splitlines():
            self._log("TEST | " + line if line else "TEST |")
        self._show_test_result(level, title, text)

    def _show_test_result(self, level, title, text):
        box = {"ok": QMessageBox.information, "warn": QMessageBox.warning,
               "fail": QMessageBox.critical}[level]
        box(self, "Ket qua test phan cung", text)

    def _tick(self):
        now = time.monotonic()
        if self._last_sample_mono is None or now - self._last_sample_mono > STALE_S:
            self._abort_trial("data_stalled")
            return

        elapsed = now * 1000.0 - self.trial_start_ms
        elapsed = min(elapsed, T_TRIAL_END)
        self.progress.setValue(int(elapsed))

        if elapsed < T_REST_END:
            new_state = "rest"
        elif elapsed < T_CUE_END:
            new_state = "cue"
        elif elapsed < T_HOLD_END:
            new_state = "hold"
        elif elapsed < T_TRIAL_END:
            new_state = "relax"
        else:
            if self.test_mode:
                self._finish_test()
            else:
                self._finish_trial()
            return

        if new_state != self._current_state:
            self._on_state_change(new_state)
            self._current_state = new_state

    def _on_state_change(self, new_state):
        colors = {"cue": "#fff59d", "hold": "#ef9a9a", "relax": "#c8e6c9"}
        labels = {"cue": "GET READY", "hold": "HOLD", "relax": "RELAX"}
        if self.test_mode:
            labels = {"cue": "CHUAN BI NAM CHAT", "hold": "NAM CHAT TAY!", "relax": "THA LONG"}
            if new_state in labels:
                self._set_state_label(f"TEST\n{labels[new_state]}", colors[new_state])
        elif new_state in labels:
            self._set_state_label(
                f"{labels[new_state]}\n{self.current_meta['gesture']}", colors[new_state]
            )

        # ghi marker theo TIME_US thuc te tu ESP32, khong dung wall-clock
        if new_state == "cue":
            self.markers["cue_on_us"] = self.latest_time_us
        elif new_state == "hold":
            self.markers["hold_start_us"] = self.latest_time_us
        elif new_state == "relax":
            self.markers["hold_end_us"] = self.latest_time_us

    def _stop_recording(self):
        self.state_timer.stop()
        if self.csv_file:
            self.csv_file.close()
            self.csv_file = None
        self.recording = False
        self.trial_active = False

    def _rename_current(self, suffix):
        """Doi ten file trial hien tai thanh ..._T01_<suffix>.csv, cap nhat meta['file']."""
        base, ext = os.path.splitext(self.current_filepath)
        new_path = f"{base}_{suffix}{ext}"
        try:
            os.replace(self.current_filepath, new_path)
        except OSError as e:
            self._log(f"Khong doi ten duoc file ({e}); giu nguyen ten.")
            return
        self.current_filepath = new_path
        self.current_meta["file"] = os.path.relpath(
            new_path, self.current_session_dir).replace("\\", "/")

    def _fill_meta_from_qc(self, qc):
        self.current_meta.update(self.markers)
        self.current_meta["sample_count"] = self.sample_count_trial
        self.current_meta["first_time_us"] = self.qc["first_t"]
        self.current_meta["last_time_us"] = self.qc["last_t"]
        self.current_meta["first_seq"] = self.qc["first_seq"]
        self.current_meta["qc"] = qc

    def _finish_trial(self):
        self._stop_recording()
        qc, problems = self._qc_finalize()
        status = "complete" if not problems else "suspect"
        if status == "suspect":
            self._rename_current("suspect")

        self._fill_meta_from_qc(qc)
        self.current_meta["status"] = status
        self._append_metadata(self.current_session_dir, self.current_meta)

        self.progress.setValue(T_TRIAL_END)
        if status == "complete":
            self._set_state_label("DONE", "#90caf9")
            self._log(
                f"Hoan tat trial ({self.sample_count_trial} mau, "
                f"{qc['rate_hz']} Hz, lost {qc['lost_packets']}) -> {self.current_filepath}"
            )
        else:
            self._set_state_label("SUSPECT - THU LAI", "#ffb74d")
            self._log(
                f"TRIAL NGHI NGO: {', '.join(problems)} -> {self.current_filepath}. "
                f"Nen thu lai trial nay."
            )

        self.start_btn.setEnabled(True)
        self.test_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._suggest_trial()

    def _abort_trial(self, reason="user"):
        if not self.trial_active:
            return
        if self.test_mode:                       # TEST bi huy: khong co file nao de doi ten/ghi
            self._stop_recording()
            self.test_mode = False
            self._test_samples = []
            text = "DATA LOST" if reason in ("data_stalled", "disconnected") else "TEST BI HUY"
            self._set_state_label(text, "#ff8a65")
            self._log(f"Test phan cung bi huy ({reason}). Khong luu gi.")
            self.start_btn.setEnabled(True)
            self.test_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.file_label.setText("File: --")
            return
        self._stop_recording()
        qc, _ = self._qc_finalize()
        self._rename_current("aborted")

        self._fill_meta_from_qc(qc)
        self.current_meta["status"] = "aborted"
        self.current_meta["abort_reason"] = reason
        self._append_metadata(self.current_session_dir, self.current_meta)

        text = "DATA LOST" if reason in ("data_stalled", "disconnected") else "ABORTED"
        self._set_state_label(text, "#ff8a65")
        self._log(f"Trial bi huy ({reason}) -> {self.current_filepath}")

        self.start_btn.setEnabled(True)
        self.test_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._suggest_trial()

    # ---------------- metadata.json ----------------
    def _append_metadata(self, session_dir, t):
        meta_path = os.path.join(session_dir, "metadata.json")
        data = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                # KHONG xoa am tham: backup file hong roi moi tao moi
                backup = f"{meta_path}.corrupt-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                try:
                    os.replace(meta_path, backup)
                    self._log(f"metadata.json hong ({e}) -> backup: {backup}")
                except OSError:
                    self._log(f"metadata.json hong ({e}) va khong backup duoc!")
                data = {}

        data.setdefault("subject_id", t["subject_id"])
        data.setdefault("session_id", t["session_id"])
        data.setdefault("donning_position", t["donning_position"])
        data.setdefault("arm_side", t["arm_side"])
        # danh dinh; do thuc te: trials[].qc.rate_hz. KHONG ghi de session cu.
        data.setdefault("sampling_rate_hz", NOMINAL_RATE_HZ)
        data.setdefault("acquisition", dict(ACQ_CONFIG))
        if (data.get("sampling_rate_hz") != NOMINAL_RATE_HZ
                or data.get("acquisition") != ACQ_CONFIG):
            self._log(
                "CANH BAO: metadata.json cua session nay co cau hinh khac "
                "ACQ_CONFIG hien tai. Kiem tra lai (khong tu ghi de)."
            )
        data.setdefault("baseline_calibration", {})
        data.setdefault("trials", [])
        data["logger_version"] = LOGGER_VERSION
        data["serial_baud"] = BAUDRATE
        data["csv_header"] = WRITE_CSV_HEADER
        data["channel_names"] = [f"CH{i + 1}" for i in range(NUM_CH)]
        data["timeline_ms"] = {
            "rest_end": T_REST_END, "cue_end": T_CUE_END,
            "hold_end": T_HOLD_END, "trial_end": T_TRIAL_END,
        }
        data["firmware_header"] = list(self.firmware_header)
        data["date"] = datetime.now().strftime("%Y-%m-%d")

        # bo entry cu cung file (ghi de) de khong bi trung
        data["trials"] = [x for x in data["trials"] if x.get("file") != t["file"]]
        data["trials"].append({
            "file": t["file"],
            "gesture": t["gesture"],
            "trial": t["trial"],
            "arm_side": t["arm_side"],
            "notes": t["notes"],
            "cue_on_us": t.get("cue_on_us"),
            "hold_start_us": t.get("hold_start_us"),
            "hold_end_us": t.get("hold_end_us"),
            "first_time_us": t.get("first_time_us"),
            "last_time_us": t.get("last_time_us"),
            "first_seq": t.get("first_seq"),
            "sample_count": t.get("sample_count"),
            "status": t.get("status"),
            "abort_reason": t.get("abort_reason"),
            "qc": t.get("qc"),
        })

        # ghi atomic: ghi file tam roi doi ten, tranh hong file neu crash giua chung
        tmp_path = meta_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, meta_path)

    def closeEvent(self, event):
        if self.trial_active:
            self._abort_trial("window_closed")
        self._disconnect()
        event.accept()


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()