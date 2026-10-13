"""
Load dữ liệu EMG (EMG Logger v1.8) + trích đặc trưng + train ANN (Keras).

Cấu trúc dữ liệu:
    Dataset/Subject_XX/Session_YY_posZ/metadata.json
    Dataset/Subject_XX/Session_YY_posZ/raw/S01_Se02_rest_T01.csv   (SEQ,TIME_US,CH1..CHn)
Cũng chạy được với thư mục "phẳng" (CSV + metadata.json cùng chỗ), và với CSV
không có metadata (nhãn lấy từ tên file: S01_Se01_<gesture>_T01.csv).

Cài đặt: pip install numpy pandas scikit-learn tensorflow matplotlib
Chạy:    python train_ann_emg.py
"""
import glob, json, os, re
import numpy as np
import pandas as pd

# ===================== CẤU HÌNH =====================
DATA_DIR      = "Dataset"                         # thư mục gốc (quét đệ quy)
CHANNELS      = ["CH1", "CH2", "CH3", "CH4"]      # đổi thành CH1..CH5 nếu dùng 5 kênh
FS            = 100                               # Hz (output firmware v1.4)
WIN_MS        = 1000                              # cửa sổ
STRIDE_MS     = 100                               # bước trượt
HOLD_TRIM_MS  = 200                               # cắt đầu/cuối đoạn Hold (bỏ transient onset/offset)
NORM          = "rest_subtract"                   # "rest_subtract" | "none"
DEFAULT_TIMELINE = {"rest_end": 2000, "cue_end": 3000, "hold_end": 6000, "trial_end": 8000}
SEED          = 42
# ====================================================

WIN    = int(FS * WIN_MS / 1000)
STRIDE = max(1, int(FS * STRIDE_MS / 1000))
WRAP   = 2 ** 32        # micros() của ESP32 tràn mỗi ~71.6 phút -> phải xử lý


def features(w):
    """w: (WIN, n_ch) -> vector đặc trưng (envelope đã chỉnh lưu nên bỏ ZC/SSC)."""
    mean, std = w.mean(0), w.std(0)
    rms = np.sqrt((w ** 2).mean(0))
    wl = np.abs(np.diff(w, axis=0)).sum(0)
    t = np.arange(len(w)) - len(w) / 2
    slope = (t[:, None] * (w - mean)).sum(0) / (t ** 2).sum()
    return np.concatenate([mean, std, rms, w.max(0), w.min(0), wl, slope])


def read_trial(csv_path):
    df = pd.read_csv(csv_path, comment="#")
    if "TIME_US" not in df.columns:   # file không có header
        df = pd.read_csv(csv_path, comment="#", header=None,
                         names=["SEQ", "TIME_US"] + [f"CH{i+1}" for i in range(df.shape[1] - 2)])
    # thời gian tương đối (ms), an toàn khi micros() tràn 2^32
    df["t_ms"] = ((df["TIME_US"] - df["TIME_US"].iloc[0]) % WRAP) / 1000.0
    return df


def trial_windows(df, timeline):
    """Trả về (các cửa sổ đặc trưng trong đoạn Hold, baseline từ đoạn Rest)."""
    sig = df[CHANNELS].to_numpy(float)
    t = df["t_ms"].to_numpy()
    base = sig[t < timeline["rest_end"]].mean(0)
    if NORM == "rest_subtract":
        sig = sig - base
    lo = timeline["cue_end"] + HOLD_TRIM_MS
    hi = timeline["hold_end"] - HOLD_TRIM_MS
    seg = sig[(t >= lo) & (t <= hi)]
    return [features(seg[s:s + WIN]) for s in range(0, len(seg) - WIN + 1, STRIDE)]


def iter_trials():
    """Sinh (csv_path, gesture, group_id, trial_no, timeline). Chỉ lấy trial status=complete."""
    metas = glob.glob(os.path.join(DATA_DIR, "**", "metadata.json"), recursive=True)
    seen = set()
    for mp in sorted(metas):
        meta = json.load(open(mp, encoding="utf-8"))
        base_dir = os.path.dirname(mp)
        timeline = {**DEFAULT_TIMELINE, **meta.get("timeline_ms", {})}
        for tr in meta.get("trials", []):
            if tr.get("status", "complete") != "complete":
                print(f"  bỏ qua {tr['file']} (status={tr.get('status')}, {tr.get('qc', {}).get('problems')})")
                continue
            name = os.path.basename(tr["file"])
            cands = [os.path.join(base_dir, tr["file"]), os.path.join(base_dir, name),
                     os.path.join(base_dir, "raw", name)]
            fp = next((c for c in cands if os.path.exists(c)), None)
            if fp:
                seen.add(os.path.abspath(fp))
                yield fp, tr["gesture"], f"{meta['subject_id']}_{meta['session_id']}", tr["trial"], timeline
    # CSV không có metadata -> đoán từ tên file
    for fp in sorted(glob.glob(os.path.join(DATA_DIR, "**", "*.csv"), recursive=True)):
        if os.path.abspath(fp) in seen or "suspect" in fp:
            continue
        m = re.match(r"(S\d+)_(Se\d+)_(.+)_T(\d+)", os.path.basename(fp))
        if m:
            yield fp, m.group(3), f"{m.group(1)}_{m.group(2)}", int(m.group(4)), DEFAULT_TIMELINE


def load_dataset():
    X, y, groups, trial_ids = [], [], [], []
    for fp, gesture, sess, trial_no, timeline in iter_trials():
        wins = trial_windows(read_trial(fp), timeline)
        X += wins; y += [gesture] * len(wins)
        groups += [sess] * len(wins); trial_ids += [f"{sess}_{gesture}_T{trial_no:02d}"] * len(wins)
    if not X:
        raise FileNotFoundError(f"Không load được trial nào trong '{DATA_DIR}'")
    return np.array(X), np.array(y), np.array(groups), np.array(trial_ids)


def split(y, groups, trial_ids):
    """Tách theo session nếu có >=4 session, ngược lại tách theo TRIAL (không bao giờ tách theo window)."""
    sessions = sorted(set(groups))
    if len(sessions) >= 4:
        test_s = sessions[-max(1, round(len(sessions) * 0.2)):]
        te = np.isin(groups, test_s)
        print("Split theo session, test =", test_s)
    else:
        print(f"[!] Chỉ có {len(sessions)} session -> split theo trial (~20% trial/lớp làm test)."
              " Kết quả lạc quan hơn so với test trên session mới.")
        te = np.zeros(len(y), bool)
        for g in np.unique(y):
            tids = sorted(set(trial_ids[y == g]))
            n_te = max(1, round(len(tids) * 0.2)) if len(tids) > 1 else 0
            te |= np.isin(trial_ids, tids[-n_te:] if n_te else [])
    return ~te, te


def main():
    import matplotlib.pyplot as plt
    import tensorflow as tf
    from tensorflow import keras
    from tensorflow.keras import layers
    from sklearn.preprocessing import StandardScaler, LabelEncoder
    from sklearn.metrics import classification_report, confusion_matrix, ConfusionMatrixDisplay
    from sklearn.utils.class_weight import compute_class_weight
    np.random.seed(SEED); tf.random.set_seed(SEED)

    X, y_raw, groups, trial_ids = load_dataset()
    print(f"\nTổng: {len(X)} cửa sổ | {X.shape[1]} đặc trưng | {len(set(trial_ids))} trial | "
          f"{len(set(groups))} session")
    print(pd.Series(y_raw).value_counts().to_string())

    le = LabelEncoder(); y = le.fit_transform(y_raw)
    tr, te = split(y_raw, groups, trial_ids)
    scaler = StandardScaler().fit(X[tr])                 # fit chỉ trên train
    Xtr, Xte, ytr, yte = scaler.transform(X[tr]), scaler.transform(X[te]), y[tr], y[te]

    cw = dict(enumerate(compute_class_weight("balanced", classes=np.unique(ytr), y=ytr)))
    model = keras.Sequential([
        layers.Input(shape=(Xtr.shape[1],)),
        layers.Dense(64, activation="relu"), layers.BatchNormalization(), layers.Dropout(0.3),
        layers.Dense(32, activation="relu"), layers.Dropout(0.2),
        layers.Dense(len(le.classes_), activation="softmax")])
    model.compile(optimizer=keras.optimizers.Adam(1e-3),
                  loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    hist = model.fit(Xtr, ytr, validation_split=0.15, epochs=200, batch_size=64, class_weight=cw,
                     verbose=2, callbacks=[keras.callbacks.EarlyStopping(patience=20, restore_best_weights=True),
                                           keras.callbacks.ReduceLROnPlateau(patience=8, factor=0.5)])

    pred = model.predict(Xte, verbose=0).argmax(1)
    print(f"\nTest accuracy: {(pred == yte).mean():.3f}")
    labels = np.arange(len(le.classes_))
    print(classification_report(yte, pred, labels=labels, target_names=le.classes_, zero_division=0))

    fig, ax = plt.subplots(1, 2, figsize=(12, 5))
    ax[0].plot(hist.history["loss"], label="train"); ax[0].plot(hist.history["val_loss"], label="val")
    ax[0].set_title("Loss"); ax[0].legend()
    ConfusionMatrixDisplay(confusion_matrix(yte, pred, labels=labels), display_labels=le.classes_).plot(
        ax=ax[1], xticks_rotation=45, colorbar=False)
    plt.tight_layout(); plt.savefig("ann_results.png", dpi=150)

    model.save("emg_ann.keras")
    np.savez("emg_preproc.npz", mean=scaler.mean_, scale=scaler.scale_, classes=le.classes_)
    print("Đã lưu: emg_ann.keras, emg_preproc.npz, ann_results.png")


if __name__ == "__main__":
    main()
