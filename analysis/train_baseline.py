#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 2 - Baseline pipeline cho EMG nhieu kenh (hien tai 5 kenh) (duong bao tu Muscle Sensor v3)
=========================================================================
Doc Dataset do logger/main.py tao ra, roi:

  1. QA tung trial  : mat goi (SEQ), timing, bao hoa 4095, kich hoat yeu, marker loi
  2. Tru nen        : median tung kenh trong doan Rest dau trial (truoc cue_on_us)
  3. Chuan hoa      : chia cho percentile 95 cua tung kenh theo session (--normalize)
  4. Cat cua so     : 200 ms, chong lan 50 %  (cua so o chuyen tiep bi bo, xem ben duoi)
  5. Dac trung      : 6 chi so x so kenh / cua so (30 dac trung voi 5 kenh)
                      (mean, std, min, max, slope, ty le mean giua cac kenh)
  6. Huan luyen     : SVM / Random Forest / MLP, CHIA THEO SESSION (leave-one-session-out)
                      hoac theo nguoi (--split subject)

Quy tac gan nhan (KHOA, de lap lai duoc):
  - Cac trial cu dong: nhan 'rest' = doan Rest truoc cue_on_us + doan sau hold_end_us + relax-margin
                       nhan <gesture> = doan [hold_start_us + hold-margin, hold_end_us]
  - Doan Cue (chuan bi), phan dau Hold (nguoi thu chua kip gong) va phan dau Relax
    (duong bao con giam) bi LOAI vi nhan khong chac chan.
  - Trial cu dong 'rest': toan bo trial la 'rest'.

Chay:
    python analysis/train_baseline.py                       # Dataset o goc repo
    python analysis/train_baseline.py --qa-only             # chi kiem tra chat luong
    python analysis/train_baseline.py --split subject       # leave-one-subject-out
    python analysis/train_baseline.py --leaky-demo          # so sanh voi chia ngau nhien (so ao)

Ket qua: analysis/results/{qa_report.csv, folds.csv, summary.csv, confusion_*.png}
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.model_selection import LeaveOneGroupOut, StratifiedKFold
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

U32 = 2 ** 32
ADC_MAX = 4095
TRIAL_LEN_S = 8.0
DROP_FLAGS = {"short", "packet_loss", "timing", "bad_markers"}   # trial bi loai khoi huan luyen


def feature_names(n_ch):
    return [f"{stat}_ch{c + 1}"
            for stat in ("mean", "std", "min", "max", "slope", "ratio")
            for c in range(n_ch)]


# --------------------------------------------------------------------------- #
# 1. Doc du lieu + QA
# --------------------------------------------------------------------------- #
def read_trial_csv(path):
    df = pd.read_csv(path, header=None, comment="#")
    if df.shape[1] < 3:
        raise ValueError(f"{os.path.basename(path)}: can it nhat 3 cot, co {df.shape[1]}")
    df = df.apply(pd.to_numeric, errors="coerce").dropna()
    seq = df.iloc[:, 0].to_numpy(np.int64)
    ts = df.iloc[:, 1].to_numpy(np.int64)
    X = df.iloc[:, 2:].to_numpy(float)          # so kenh = so cot - 2
    return seq, ts, X


def load_trials(root, hold_margin, relax_margin):
    trials, skipped = [], []
    metas = sorted(glob.glob(os.path.join(root, "*", "*", "metadata.json")))
    if not metas:
        sys.exit(f"Khong thay metadata.json nao trong {root}")

    for meta_path in metas:
        sdir = os.path.dirname(meta_path)
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        subject = meta.get("subject_id", "?")
        session = meta.get("session_id", "?")
        position = meta.get("donning_position", "")
        group = f"{subject}/{session}{position}"
        fs = meta.get("sampling_rate_hz") or meta.get("acquisition", {}).get("output_rate_hz") or 100

        # metadata co the co nhieu muc cho cung 1 file (ghi de) -> lay muc cuoi
        latest = {}
        for t in meta.get("trials", []):
            latest[t.get("file")] = t

        for fname, t in latest.items():
            tag = f"{group}:{fname}"
            if t.get("status") != "complete":
                skipped.append((tag, f"status={t.get('status')}"))
                continue
            path = os.path.join(sdir, fname)
            if not os.path.exists(path):
                skipped.append((tag, "khong thay file CSV"))
                continue
            try:
                seq, ts, X = read_trial_csv(path)
            except Exception as e:  # noqa: BLE001
                skipped.append((tag, f"loi doc: {e}"))
                continue
            if len(X) < 20:
                skipped.append((tag, "qua it mau"))
                continue

            dts = np.diff(ts) % U32
            t_rel = np.concatenate([[0], np.cumsum(dts)]) / 1e6

            def rel(m):
                return None if m is None else ((int(m) - int(ts[0])) % U32) / 1e6

            cue, hs, he = rel(t.get("cue_on_us")), rel(t.get("hold_start_us")), rel(t.get("hold_end_us"))
            gesture = t.get("gesture", "?")
            dur = float(t_rel[-1])

            flags = []
            if len(X) < 0.95 * fs * TRIAL_LEN_S:
                flags.append("short")
            if int((np.diff(seq) != 1).sum()) > 0:
                flags.append("packet_loss")
            if dts.size and dts.max() / 1e3 > 2.0 * 1000.0 / fs:
                flags.append("timing")
            if float((X >= ADC_MAX).mean()) > 0.01:
                flags.append("saturated")
            if gesture != "rest" and not (cue and hs and he and 0 < cue < hs < he <= dur + 0.05):
                flags.append("bad_markers")
            if gesture == "rest" and cue is None:
                cue = 2.0   # trial rest khong can marker chinh xac
            if cue is None or (t_rel < cue).sum() < 10:
                flags.append("bad_markers")

            base = np.median(X[t_rel < (cue if cue else 2.0)], axis=0)
            ratio_med = np.nan
            if gesture != "rest" and "bad_markers" not in flags:
                m = (t_rel >= hs + hold_margin) & (t_rel <= he)
                if m.sum() >= 5:
                    ratio_med = float(np.median(X[m].mean(0) / np.maximum(base, 1.0)))
                    if ratio_med < 1.5:
                        flags.append("weak_activation")

            trials.append(dict(
                group=group, subject=subject, session=session, gesture=gesture,
                trial=t.get("trial"), file=fname, fs=fs, t=t_rel, X=X,
                cue=cue, hs=hs, he=he, base=base, flags=flags,
                qa=dict(group=group, file=fname, gesture=gesture, n=len(X),
                        dur_s=round(dur, 3), seq_gaps=int((np.diff(seq) != 1).sum()),
                        dt_max_ms=round(float(dts.max() / 1e3), 2) if dts.size else np.nan,
                        sat_frac=round(float((X >= ADC_MAX).mean()), 4),
                        rest_std=round(float(np.mean(X[t_rel < (cue if cue else 2.0)].std(0))), 1),
                        hold_rest_ratio=round(ratio_med, 2) if ratio_med == ratio_med else np.nan,
                        flags=";".join(flags)),
            ))
    # Chi giu cac trial co cung so kenh (so kenh pho bien nhat); trial khac so kenh bi bo qua
    if trials:
        n_ch = Counter(t["X"].shape[1] for t in trials).most_common(1)[0][0]
        for t in [t for t in trials if t["X"].shape[1] != n_ch]:
            skipped.append((f"{t['group']}:{t['file']}", f"so kenh {t['X'].shape[1]} khac {n_ch}"))
        trials = [t for t in trials if t["X"].shape[1] == n_ch]
    return trials, skipped


# --------------------------------------------------------------------------- #
# 2. Tru nen + chuan hoa theo session
# --------------------------------------------------------------------------- #
def normalize(trials, mode):
    for tr in trials:
        tr["Xb"] = tr["X"] - tr["base"]
    if mode == "none":
        for tr in trials:
            tr["Xn"] = tr["Xb"]
        return {}
    scales = {}
    for g in sorted({tr["group"] for tr in trials}):
        allx = np.vstack([tr["Xb"] for tr in trials if tr["group"] == g])
        scales[g] = np.maximum(np.percentile(allx, 95, axis=0), 1.0)   # khong dung nhan
    for tr in trials:
        tr["Xn"] = tr["Xb"] / scales[tr["group"]]
    return scales


# --------------------------------------------------------------------------- #
# 3. Cua so + dac trung
# --------------------------------------------------------------------------- #
def label_spans(tr, hold_margin, relax_margin):
    end = float(tr["t"][-1]) + 1e-9
    if tr["gesture"] == "rest":
        return [(0.0, end, "rest")]
    return [(0.0, tr["cue"], "rest"),
            (tr["hs"] + hold_margin, tr["he"], tr["gesture"]),
            (tr["he"] + relax_margin, end, "rest")]


def window_features(w, fs):
    mean, std = w.mean(0), w.std(0)
    x = np.arange(len(w)) / fs
    xc = x - x.mean()
    slope = (xc[:, None] * (w - mean)).sum(0) / (xc ** 2).sum()
    pos = np.clip(mean, 0, None)
    ratio = pos / (pos.sum() + 0.05)
    return np.concatenate([mean, std, w.min(0), w.max(0), slope, ratio])


def build_windows(trials, win_ms, overlap, hold_margin, relax_margin):
    feats, ys, groups, subjects, files = [], [], [], [], []
    for tr in trials:
        fs = tr["fs"]
        win = max(2, int(round(win_ms / 1000.0 * fs)))
        step = max(1, int(round(win * (1.0 - overlap))))
        spans = label_spans(tr, hold_margin, relax_margin)
        t, Xn = tr["t"], tr["Xn"]
        for i in range(0, len(Xn) - win + 1, step):
            t0, t1 = t[i], t[i + win - 1]
            lab = next((l for s, e, l in spans if t0 >= s and t1 <= e), None)
            if lab is None:
                continue
            feats.append(window_features(Xn[i:i + win], fs))
            ys.append(lab)
            groups.append(tr["group"])
            subjects.append(tr["subject"])
            files.append(tr["file"])
    return (np.array(feats), np.array(ys), np.array(groups),
            np.array(subjects), np.array(files))


# --------------------------------------------------------------------------- #
# 4. Huan luyen + danh gia
# --------------------------------------------------------------------------- #
def make_models(names):
    zoo = {
        "svm": lambda: make_pipeline(StandardScaler(),
                                     SVC(C=10, gamma="scale", class_weight="balanced")),
        "rf": lambda: RandomForestClassifier(n_estimators=200, random_state=0,
                                             class_weight="balanced", n_jobs=-1),
        "mlp": lambda: make_pipeline(StandardScaler(),
                                     MLPClassifier((64, 32), max_iter=500, random_state=0)),
    }
    return {n: zoo[n] for n in names}


def plot_confusion(cm, classes, path, title):
    cmn = cm / np.maximum(cm.sum(1, keepdims=True), 1)
    fig, ax = plt.subplots(figsize=(1 + 0.8 * len(classes), 1 + 0.7 * len(classes)))
    ax.imshow(cmn, vmin=0, vmax=1, cmap="Blues")
    ax.set_xticks(range(len(classes)), classes, rotation=45, ha="right")
    ax.set_yticks(range(len(classes)), classes)
    for i in range(len(classes)):
        for j in range(len(classes)):
            ax.text(j, i, f"{cmn[i, j]:.2f}", ha="center", va="center",
                    color="white" if cmn[i, j] > 0.5 else "black", fontsize=8)
    ax.set_xlabel("Du doan")
    ax.set_ylabel("That")
    ax.set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def evaluate(models, X, y, groups, classes, out_dir):
    fold_rows, summary_rows = [], []
    for name, mk in models.items():
        y_true, y_pred = [], []
        for tr_idx, te_idx in LeaveOneGroupOut().split(X, y, groups):
            m = mk()
            m.fit(X[tr_idx], y[tr_idx])
            p = m.predict(X[te_idx])
            y_true.append(y[te_idx])
            y_pred.append(p)
            fold_rows.append(dict(model=name, held_out=groups[te_idx][0], n_test=len(te_idx),
                                  accuracy=accuracy_score(y[te_idx], p),
                                  macro_f1=f1_score(y[te_idx], p, average="macro",
                                                    labels=np.unique(y[te_idx]), zero_division=0)))
        yt, yp = np.concatenate(y_true), np.concatenate(y_pred)
        accs = [r["accuracy"] for r in fold_rows if r["model"] == name]
        summary_rows.append(dict(model=name, folds=len(accs),
                                 acc_mean=np.mean(accs), acc_std=np.std(accs),
                                 acc_pooled=accuracy_score(yt, yp),
                                 macro_f1_pooled=f1_score(yt, yp, average="macro",
                                                          labels=classes, zero_division=0)))
        cm = confusion_matrix(yt, yp, labels=classes)
        pd.DataFrame(cm, index=classes, columns=classes).to_csv(
            os.path.join(out_dir, f"confusion_{name}.csv"))
        plot_confusion(cm, list(classes), os.path.join(out_dir, f"confusion_{name}.png"),
                       f"{name.upper()} - held-out theo nhom")
    folds, summary = pd.DataFrame(fold_rows), pd.DataFrame(summary_rows)
    folds.to_csv(os.path.join(out_dir, "folds.csv"), index=False)
    summary.to_csv(os.path.join(out_dir, "summary.csv"), index=False)
    return folds, summary


def leaky_demo(models, X, y):
    print("\n[LEAKY DEMO] Chia NGAU NHIEN theo cua so (SO AO, chi de minh hoa, KHONG dung de bao cao):")
    for name, mk in models.items():
        accs = []
        for tr_idx, te_idx in StratifiedKFold(5, shuffle=True, random_state=0).split(X, y):
            m = mk()
            m.fit(X[tr_idx], y[tr_idx])
            accs.append(accuracy_score(y[te_idx], m.predict(X[te_idx])))
        print(f"  {name:4s}: acc = {np.mean(accs):.3f}")


# --------------------------------------------------------------------------- #
def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=os.path.join(here, "..", "Dataset"))
    ap.add_argument("--out", default=os.path.join(here, "results"))
    ap.add_argument("--win-ms", type=float, default=200)
    ap.add_argument("--overlap", type=float, default=0.5)
    ap.add_argument("--hold-margin", type=float, default=0.5, help="bo nhan s dau Hold (giay)")
    ap.add_argument("--relax-margin", type=float, default=1.0, help="bo s dau Relax (giay)")
    ap.add_argument("--normalize", choices=["session_p95", "none"], default="session_p95")
    ap.add_argument("--split", choices=["session", "subject"], default="session")
    ap.add_argument("--models", default="svm,rf", help="svm,rf,mlp")
    ap.add_argument("--keep-flagged", action="store_true", help="khong loai trial bi co (flag)")
    ap.add_argument("--qa-only", action="store_true")
    ap.add_argument("--leaky-demo", action="store_true")
    args = ap.parse_args()

    root = os.path.normpath(args.dataset)
    os.makedirs(args.out, exist_ok=True)

    trials, skipped = load_trials(root, args.hold_margin, args.relax_margin)
    qa = pd.DataFrame([t["qa"] for t in trials])
    qa.to_csv(os.path.join(args.out, "qa_report.csv"), index=False)

    print(f"Dataset: {root}")
    print(f"  Trial doc duoc: {len(trials)}   bo qua: {len(skipped)}")
    for tag, why in skipped:
        print(f"    - {tag}: {why}")
    flagged = qa[qa["flags"] != ""]
    if len(flagged):
        print("  Trial co co (flag):")
        for _, r in flagged.iterrows():
            print(f"    - {r['group']}/{r['file']}: {r['flags']}")
    print(f"  QA chi tiet: {os.path.join(args.out, 'qa_report.csv')}")
    if args.qa_only:
        return

    kept = [t for t in trials if args.keep_flagged or not (set(t["flags"]) & DROP_FLAGS)]
    print(f"  Dung de huan luyen: {len(kept)} trial (loai {len(trials) - len(kept)})")
    normalize(kept, args.normalize)

    X, y, g_sess, g_subj, _ = build_windows(kept, args.win_ms, args.overlap,
                                            args.hold_margin, args.relax_margin)
    if len(X) == 0:
        sys.exit("Khong tao duoc cua so nao (kiem tra marker/nhan).")
    classes = np.array(sorted(set(y)))
    print(f"  So kenh: {kept[0]['X'].shape[1]}   Cua so: {len(X)}   dac trung: {X.shape[1]}   lop: {len(classes)}")
    counts = pd.Series(y).value_counts().sort_index()
    print("  So cua so moi lop:", {str(k): int(v) for k, v in counts.items()})

    groups = g_sess if args.split == "session" else g_subj
    n_groups = len(set(groups))
    if n_groups < 2:
        sys.exit(f"\nCan it nhat 2 {'session' if args.split == 'session' else 'nguoi'} "
                 f"de chia train/test (hien co {n_groups}). Thu them du lieu.")
    print(f"  Chia: leave-one-{args.split}-out, {n_groups} nhom: {sorted(str(x) for x in set(groups))}")

    models = make_models([m.strip() for m in args.models.split(",") if m.strip()])
    folds, summary = evaluate(models, X, y, groups, classes, args.out)

    pd.set_option("display.width", 140)
    print("\n=== Ket qua theo nhom held-out ===")
    print(folds.round(3).to_string(index=False))
    print("\n=== Tong ket ===")
    print(summary.round(3).to_string(index=False))
    print(f"\nAnh ma tran nham lan va CSV: {args.out}")

    if args.leaky_demo:
        leaky_demo(models, X, y)


if __name__ == "__main__":
    main()
