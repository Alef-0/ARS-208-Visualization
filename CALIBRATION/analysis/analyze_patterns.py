#!/usr/bin/env python3
"""Compare causal timestamp corrections against cached optical marker readings.

From the repository root (no cameras, windows, builds or source changes):
  python3 CALIBRATION/analysis/analyze_patterns.py --prepare
  PYTHONNOUSERSITE=1 python3 CALIBRATION/analysis/analyze_patterns.py

Preparation uses the camera environment. Numerical analysis uses the installed
system NumPy/SciPy/Matplotlib environment. Outputs default to
recordings/calibration_pattern_analysis_20260903.
"""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "recordings" / "calibration_pattern_analysis_20260903"
NAMES = ("calibration_first", "calibration_second")
AUDIT = ROOT / "recordings/calibration_analysis_20260903"


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare():
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    sys.path.insert(0, str(ROOT))
    from CALIBRATION.marker_analysis import DisplayEvidence
    from analyze_calibration_recording_offset import load_epochs, normalize_ean13, normalize_timing_row
    output, provenance = {}, {}
    for name in NAMES:
        folder = ROOT / "recordings" / name
        raw = jsonl(folder / "camera_timestamps.jsonl")
        cached = jsonl(AUDIT / name / "raw_barcode_observations.jsonl")
        lookup = {r["camera_frame"]: r for r in cached}
        if len(lookup) != len(cached) or len(cached) != len(raw):
            raise ValueError(f"{name}: cached observations do not match the camera journal")
        if len({r["frame"] for r in raw}) != len(raw):
            raise ValueError(f"{name}: duplicate camera frame names")
        evidence, epochs = DisplayEvidence.load(folder), load_epochs(folder)
        rows = []
        for index, original in enumerate(raw):
            timing = normalize_timing_row(original, epochs)
            old = lookup[original["frame"]]
            path = (folder / original["frame"]).resolve()
            if not path.is_relative_to(folder.resolve()):
                raise ValueError(f"Image is outside the recording folder: {path}")
            if digest(path) != old["image_sha256"]:
                raise ValueError(f"Image changed since raw decoding: {path}")
            accepted = []
            for method in ("zbar", "opencv"):
                for symbol in old[method] + old["rejected_" + method]:
                    code = normalize_ean13(symbol["raw_code"], symbol["type"])
                    marker = evidence.lookup(code, round(timing["frame_monotonic_ns"] / 1e6)) if code else None
                    if (marker is not None and symbol.get("quality", 2) >= 2
                            and marker["marker_ns"] <= timing["received_monotonic_ns"]):
                        accepted.append({"index": marker["index"], "marker_ns": marker["marker_ns"],
                                         "corner": marker["corner"], "method": method})
            indices = sorted({s["index"] for s in accepted})
            row = {**timing, "row_index": index, "target_ms": None, "eligible": False,
                   "num_codes": len(indices), "corner": None, "agreement": False, "overlap": False,
                   "exclusion": "No decoded marker", "image_sha256": old["image_sha256"]}
            if indices:
                newest = max(accepted, key=lambda s: s["index"])
                display = evidence.marker_timing(newest["index"])
                coherent = indices[-1] - indices[0] <= 3
                latest = {m: max((s["index"] for s in accepted if s["method"] == m), default=None)
                          for m in ("zbar", "opencv")}
                row.update(target_ms=(timing["frame_monotonic_ns"] - newest["marker_ns"]) / 1e6,
                           marker_ns=newest["marker_ns"], corner=newest["corner"], display_index=newest["index"],
                           eligible=coherent and display["status"] == "clean",
                           exclusion="Wide conflicting generations" if not coherent else display["status"],
                           agreement=latest["zbar"] == latest["opencv"],
                           overlap=indices[-1] - indices[0] >= evidence.visible_frames,
                           display_issues=display["issue_codes"])
            rows.append(row)
        output[name] = rows
        paths = [folder / f for f in ("camera_timestamps.jsonl", "camera_timing_session.json", "display_timestamps.jsonl")]
        paths.append(AUDIT / name / "raw_barcode_observations.jsonl")
        provenance[name] = {str(p): digest(p) for p in paths}
        print(name, "verified original images:", len(rows), "eligible:", sum(r["eligible"] for r in rows), flush=True)
    (OUT / "prepared_frames.json").write_text(json.dumps(output) + "\n")
    (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")


def features(rows):
    import numpy as np
    n = len(rows)
    p = np.array([r["frame_monotonic_ns"] for r in rows], dtype=np.int64)
    receipt = np.array([r["received_monotonic_ns"] for r in rows], dtype=np.int64)
    delta = np.r_[1000 / 30, np.diff(p) / 1e6]
    age = (receipt - p) / 1e6
    receipt_delta = np.r_[1000 / 30, np.diff(receipt) / 1e6]
    ntp_lead = np.array([(r["reference_ntp_ns"] - r["media_unix_ns"]) / 1e6
                         if r["reference_ntp_ns"] is not None else np.nan for r in rows])
    f = {"pts_step_ms": delta, "receipt_age_ms": age, "receipt_step_ms": receipt_delta,
         "ntp_lead_ms": ntp_lead, "elapsed_s": (p - p[0]) / 1e9}
    for lag in range(1, 6):
        f[f"pts_step_lag{lag}_ms"] = np.r_[np.full(lag, 1000 / 30), delta[:-lag]]
    # A fixed-rate grid estimated only from preceding PTS values. No optical
    # labels or future packets enter it. Reset on stream changes or long gaps.
    for window in (15, 30, 60):
        phase = np.zeros(n)
        start = 0
        for i in range(n):
            if i and (rows[i]["stream_epoch"] != rows[i-1]["stream_epoch"] or delta[i] > 100 or delta[i] <= 0):
                start = i
            lo = max(start, i-window)
            if i > lo:
                j = np.arange(lo, i)
                phase[i] = np.median((p[i] - p[lo:i]) / 1e6 - (i-j) * (1000 / 30))
        f[f"past_grid{window}_ms"] = phase
    for window in (15, 30, 60):
        smooth = np.array([np.median(age[max(0, i-window):i]) if i else age[i] for i in range(n)])
        f[f"receipt_age_deviation{window}_ms"] = age - smooth
    return f


def fit_lad(x, y):
    """Exact least absolute deviations via linear programming, scaled inputs."""
    import numpy as np
    from scipy import sparse
    from scipy.optimize import linprog
    center = np.nanmedian(x, axis=0)
    filled = np.where(np.isfinite(x), x, center)
    scale = np.maximum(np.std(filled, axis=0), 1e-6)
    design = np.column_stack((np.ones(len(y)), (filled-center) / scale))
    p, n = design.shape[1], len(y)
    matrix = sparse.vstack((sparse.hstack((design, -sparse.eye(n))),
                            sparse.hstack((-design, -sparse.eye(n))))).tocsr()
    solved = linprog(np.r_[np.zeros(p), np.ones(n)], A_ub=matrix, b_ub=np.r_[y, -y],
                     bounds=[(None, None)]*p+[(0, None)]*n, method="highs")
    if not solved.success:
        raise RuntimeError(solved.message)
    slopes = solved.x[1:p] / scale
    return {"intercept_ms": float(solved.x[0] - center @ slopes), "slopes": slopes.tolist(),
            "fill_values": center.tolist()}


def predict(model, x):
    import numpy as np
    filled = np.where(np.isfinite(x), x, model["fill_values"])
    return model["intercept_ms"] + filled @ model["slopes"]


def metrics(y, estimate):
    import numpy as np
    error = y - estimate
    absolute = np.abs(error)
    return {"n": len(y), "mae_ms": float(np.mean(absolute)), "median_absolute_ms": float(np.median(absolute)),
            "p95_absolute_ms": float(np.percentile(absolute, 95)),
            "rmse_ms": float(np.sqrt(np.mean(error**2))), "bias_ms": float(np.mean(error)),
            "within_5ms_pct": float(100*np.mean(absolute <= 5)), "within_10ms_pct": float(100*np.mean(absolute <= 10))}


SPECS = {
    "constant_median": [],
    "pts_step": ["pts_step_ms"],
    "receipt_age": ["receipt_age_ms"],
    "pts_step_receipt_age": ["pts_step_ms", "receipt_age_ms"],
    "pts_step_receipt_step": ["pts_step_ms", "receipt_step_ms"],
    "pts_receipt_ntp": ["pts_step_ms", "receipt_age_ms", "receipt_step_ms", "ntp_lead_ms"],
    "ntp_lead": ["ntp_lead_ms"],
    "linear_elapsed": ["elapsed_s"],
    "pts_step_elapsed": ["pts_step_ms", "elapsed_s"],
    "pts_history3": ["pts_step_ms", "pts_step_lag1_ms", "pts_step_lag2_ms"],
    "pts_history6": ["pts_step_ms"] + [f"pts_step_lag{i}_ms" for i in range(1, 6)],
    "pts_history3_receipt": ["pts_step_ms", "pts_step_lag1_ms", "pts_step_lag2_ms", "receipt_age_ms"],
    "pts_history6_receipt": ["pts_step_ms"] + [f"pts_step_lag{i}_ms" for i in range(1, 6)] + ["receipt_age_ms"],
}
for _window in (15, 30, 60):
    SPECS[f"grid{_window}"] = [f"past_grid{_window}_ms"]
    SPECS[f"grid{_window}_receipt"] = [f"past_grid{_window}_ms", "receipt_age_ms"]
    SPECS[f"pts_step_receipt_deviation{_window}"] = ["pts_step_ms", f"receipt_age_deviation{_window}_ms"]


def design(f, fields):
    import numpy as np
    return np.column_stack([f[k] for k in fields]) if fields else np.empty((len(f["pts_step_ms"]), 0))


def fit(f, y, mask, fields):
    import numpy as np
    if not fields:
        return {"intercept_ms": float(np.median(y[mask])), "slopes": [], "fill_values": [], "features": []}
    return {**fit_lad(design(f, fields)[mask], y[mask]), "features": fields}


def block_bootstrap(y, baseline, candidate, positions, seed=9326):
    """Paired 1-second blocks, preserving short-range autocorrelation."""
    import numpy as np
    rng = np.random.default_rng(seed)
    difference = np.abs(y-baseline) - np.abs(y-candidate)
    keys = positions // 30
    blocks = [difference[keys == k] for k in np.unique(keys)]
    samples = [np.mean(np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))]))
               for _ in range(2000)]
    return {"mean_mae_improvement_ms": float(np.mean(difference)),
            "block_bootstrap_95pct_ms": np.percentile(samples, [2.5, 97.5]).tolist(),
            "block_rows": 30, "resamples": 2000, "seed": seed}


def analyze():
    import numpy as np
    data = json.loads((OUT / "prepared_frames.json").read_text())
    fs = {n: features(data[n]) for n in NAMES}
    # Check causality directly: truncating all later packets cannot change an
    # already available feature, including rolling-grid and lagged features.
    for n in NAMES:
        for stop in (17, 120, len(data[n])//2):
            prefix = features(data[n][:stop])
            for key in prefix:
                np.testing.assert_allclose(prefix[key], fs[n][key][:stop], equal_nan=True)
    ys = {n: np.array([r["target_ms"] if r["target_ms"] is not None else np.nan for r in data[n]]) for n in NAMES}
    eligible = {n: np.array([r["eligible"] for r in data[n]]) for n in NAMES}
    first, second = NAMES
    size = len(data[first])
    cut60, cut80 = int(size*.6), int(size*.8)
    pos = np.arange(size)
    train = eligible[first] & (pos < cut60)
    validation = eligible[first] & (pos >= cut60) & (pos < cut80)
    train_final = eligible[first] & (pos < cut80)
    test = eligible[first] & (pos >= cut80)
    splits = {"first_validation": (first, validation), "first_test": (first, test),
              "second_external": (second, eligible[second])}
    models, predictions, scores = {}, {}, {}
    for name, fields in {"fixed109": [], **SPECS}.items():
        early = fit(fs[first], ys[first], train, fields)
        model = fit(fs[first], ys[first], train_final, fields)
        if name == "fixed109":
            early["intercept_ms"] = model["intercept_ms"] = 109.
        models[name] = model
        predictions[name] = {n: predict(model, design(fs[n], fields)) for n in NAMES}
        scores[name] = {}
        for split, (n, mask) in splits.items():
            prediction = predict(early, design(fs[n], fields)) if split == "first_validation" else predictions[name][n]
            scores[name][split] = metrics(ys[n][mask], prediction[mask])
    ranked = sorted(SPECS, key=lambda k: scores[k]["first_validation"]["mae_ms"])
    winner = ranked[0]
    # Sensitivity checks keep exactly the same predictions and prechosen model.
    sensitivity = {}
    for n, mask in ((first, test), (second, eligible[second])):
        sensitivity[n] = {}
        for group, flag in (("all", np.ones(len(mask), bool)),
                            ("both_decoders_agree", np.array([r["agreement"] for r in data[n]])),
                            ("four_codes", np.array([r["num_codes"] == 4 for r in data[n]])),
                            ("no_detected_overlap", np.array([not r["overlap"] for r in data[n]]))):
            subset = mask & flag
            if subset.any():
                sensitivity[n][group] = {k: metrics(ys[n][subset], predictions[k][n][subset])
                                         for k in ("fixed109", "constant_median", "pts_step", winner)}
    # Subsequent barcode observations are deliberately NOT predictors. A rolling
    # optical median is reported separately as calibration-only, strictly lagged.
    adaptive = {}
    for n in NAMES:
        adaptive[n] = {}
        for window in (30, 120):
            history, outputs, targets = [], [], []
            for i, row in enumerate(data[n]):
                candidate = float(np.median(history[-window:])) if history else 109.
                if row["eligible"]:
                    if len(history) >= window:
                        outputs.append(candidate)
                        targets.append(ys[n][i])
                    history.append(ys[n][i])
            adaptive[n][f"past_{window}_optical_offsets"] = metrics(np.array(targets), np.array(outputs))
    pattern = {}
    for n in NAMES:
        mask = eligible[n]
        pattern[n] = {"eligible": int(mask.sum()), "excluded": int((~mask).sum()), "groups": {}}
        for key, group in (("pts_step_rounded10_ms", np.round(fs[n]["pts_step_ms"] / 10) * 10),
                           ("quadrant", np.array([r["corner"] if r["corner"] is not None else -1 for r in data[n]])),
                           ("number_of_codes", np.array([r["num_codes"] for r in data[n]]))):
            pattern[n]["groups"][key] = {str(value): {"n": int((mask & (group == value)).sum()),
                "median_offset_ms": float(np.median(ys[n][mask & (group == value)]))}
                for value in np.unique(group[mask]) if (mask & (group == value)).sum() >= 10}
        pattern[n]["correlations"] = {}
        for key, column in fs[n].items():
            ok = mask & np.isfinite(column)
            pattern[n]["correlations"][key] = float(np.corrcoef(column[ok], ys[n][ok])[0, 1])
    bootstrap = {}
    for n, mask in ((first, test), (second, eligible[second])):
        bootstrap[n] = {k: block_bootstrap(ys[n][mask], predictions[k][n][mask], predictions[winner][n][mask], np.flatnonzero(mask))
                        for k in ("fixed109", "constant_median")}
    report = {"target": "Host-anchored PTS minus newest journal-matched decoded marker; software reference, not physical exposure truth",
              "split": {"first_train_rows": [0, cut60], "first_validation_rows": [cut60, cut80],
                        "refit_rows": [0, cut80], "first_test_rows": [cut80, size],
                        "second": "external evaluation only; never used to fit coefficients or select winner"},
              "winner_by_first_validation_mae": winner, "validation_ranking": ranked,
              "models_refit_first80pct": models, "metrics": scores,
              "sensitivity": sensitivity, "bootstrap": bootstrap, "patterns": pattern,
              "calibration_only_optical_history": adaptive}
    # Also use all of the first calibration to predict the second. The second
    # never supplies coefficients. Ranking these external results is exploratory
    # model comparison and requires a new recording for independent confirmation.
    transfer_models, transfer_predictions, transfer_scores = {}, {}, {}
    for name, fields in {"fixed109": [], **SPECS}.items():
        model = fit(fs[first], ys[first], eligible[first], fields)
        if name == "fixed109":
            model["intercept_ms"] = 109.
        transfer_models[name] = model
        transfer_predictions[name] = predict(model, design(fs[second], fields))
        transfer_scores[name] = metrics(ys[second][eligible[second]], transfer_predictions[name][eligible[second]])
    transfer_winner = min(SPECS, key=lambda k: transfer_scores[k]["mae_ms"])
    transfer_bootstrap = {k: block_bootstrap(ys[second][eligible[second]],
        transfer_predictions[k][eligible[second]], transfer_predictions[transfer_winner][eligible[second]],
        np.flatnonzero(eligible[second])) for k in ("fixed109", "constant_median", "pts_step_receipt_age")}
    transfer_groups = {}
    for group, flag in (("both_decoders_agree", np.array([r["agreement"] for r in data[second]])),
                        ("four_codes", np.array([r["num_codes"] == 4 for r in data[second]])),
                        ("three_codes", np.array([r["num_codes"] == 3 for r in data[second]])),
                        ("one_or_two_codes", np.array([r["num_codes"] <= 2 for r in data[second]]))):
        mask = eligible[second] & flag
        transfer_groups[group] = {k: metrics(ys[second][mask], transfer_predictions[k][mask])
                                 for k in ("fixed109", "constant_median", "pts_step_receipt_age", transfer_winner)}
    f_continuous = {**fs[second], "elapsed_s": fs[second]["elapsed_s"] +
                    (data[second][0]["frame_monotonic_ns"]-data[first][0]["frame_monotonic_ns"])/1e9}
    extrapolated = predict(models[winner], design(f_continuous, models[winner]["features"]))
    report["first_full_to_second"] = {"models": transfer_models, "metrics": transfer_scores,
        "lowest_observed_external_mae": transfer_winner, "bootstrap": transfer_bootstrap,
        "sensitivity": transfer_groups,
        "selection_caveat": "External ranking was inspected; a third recording is needed to independently confirm the chosen model.",
        "drift_model_with_continuous_stream_clock": metrics(ys[second][eligible[second]], extrapolated[eligible[second]])}
    (OUT / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    with (OUT / "frame_predictions.csv").open("w") as handle:
        fields = ["recording", "camera_frame", "row_index", "eligible", "target_ms", "corner", "num_codes", "agreement", "exclusion"]
        fields += list(fs[first]) + ["correction_" + k + "_ms" for k in predictions]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for n in NAMES:
            for i, row in enumerate(data[n]):
                record = {k: row.get(k) for k in fields[:9]}
                record["recording"] = n
                record.update({k: float(v[i]) for k, v in fs[n].items()})
                record.update({"correction_" + k + "_ms": float(predictions[k][n][i]) for k in predictions})
                writer.writerow(record)
    with (OUT / "second_transfer_predictions.csv").open("w") as handle:
        fields = ["camera_frame", "eligible", "exclusion", "target_ms", "pts_step_ms", "receipt_age_ms"] + list(transfer_predictions)
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for i, row in enumerate(data[second]):
            writer.writerow({"camera_frame": row["camera_frame"], "eligible": row["eligible"],
                             "exclusion": row["exclusion"], "target_ms": row["target_ms"],
                             "pts_step_ms": fs[second]["pts_step_ms"][i], "receipt_age_ms": fs[second]["receipt_age_ms"][i],
                             **{k: float(v[i]) for k, v in transfer_predictions.items()}})
    plot(data, ys, fs, predictions, report, test, eligible[second])
    plot_transfer(data, ys, fs, eligible, transfer_predictions, report)
    print("Selected using first validation only:", winner)
    print("Refit coefficients:", json.dumps(models[winner]))
    for k in ("fixed109", "constant_median", "pts_step", "pts_step_receipt_age", winner):
        print(k, {s: {m: round(scores[k][s][m], 3) for m in ("mae_ms", "p95_absolute_ms", "bias_ms")} for s in splits})
    print("Lowest observed external MAE after full first calibration:", transfer_winner, transfer_scores[transfer_winner])


def plot_transfer(data, ys, fs, eligible, predictions, report):
    import numpy as np
    import matplotlib.pyplot as plt
    second = NAMES[1]
    winner = report["first_full_to_second"]["lowest_observed_external_mae"]
    mask = eligible[second]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.4), layout="constrained")
    for j, n in enumerate(NAMES):
        step = np.round(fs[n]["pts_step_ms"]/10)*10
        values = [ys[n][eligible[n] & (step == s)] for s in (20, 40, 60)]
        medians = [np.median(v) for v in values]
        axes[0].plot((20, 40, 60), medians, "o-", color=("#147a88", "#bd5e2f")[j], label=n.replace("calibration_", "Recording "))
    axes[0].set(xlabel="PTS step since previous message (ms)", ylabel="Median decoded-marker offset (ms)",
                title="The PTS-step pattern repeats")
    axes[0].set_xticks((20, 40, 60))
    axes[0].grid(alpha=.2)
    axes[0].legend()
    for k, color, label in (("fixed109", "#a54559", "Fixed 109 ms"),
                            ("constant_median", "#8a7e31", "Calibrated constant"),
                            ("pts_step_receipt_age", "#9ca9b3", "Two timing fields"),
                            (winner, "#147a88", "Six PTS steps + receipt age")):
        error = np.sort(np.abs(ys[second][mask]-predictions[k][mask]))
        axes[1].plot(error, np.arange(1,len(error)+1)/len(error)*100, color=color, label=label)
    axes[1].set(xlabel="Absolute residual against decoded marker (ms)", ylabel="Frames within residual (%)",
                title="Second recording; coefficients from first only", xlim=(0, 35), ylim=(0, 100))
    axes[1].grid(alpha=.2)
    axes[1].legend(fontsize=9)
    fig.suptitle("Message timing predicts part of the offset — software marker reference", fontsize=13)
    fig.savefig(OUT / "pattern_comparison.png", dpi=170)
    fig.savefig(OUT / "pattern_comparison.svg")
    plt.close(fig)


def plot(data, ys, fs, predictions, report, test, secondmask):
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    winner = report["winner_by_first_validation_mae"]
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), layout="constrained")
    colors = ("#147a88", "#bd5e2f")
    for j, (n, mask) in enumerate(zip(NAMES, (test, secondmask))):
        x = fs[n]["elapsed_s"][mask]
        y = ys[n][mask]
        ax = axes[0, j]
        ax.scatter(x, y, s=7, alpha=.4, color=colors[j], label="Decoded marker offset")
        ax.plot(x, predictions[winner][n][mask], color="#252d38", lw=.7, alpha=.75, label="Predicted correction")
        ax.axhline(109, color="#a54559", ls="--", label="Fixed 109 ms")
        ax.set(title="First: held-out final 20%" if j == 0 else "Second: entire held-out recording",
               xlabel="Seconds into recording", ylabel="Correction / marker offset (ms)")
        ax.legend(fontsize=8)
        ax = axes[1, j]
        for k, color, label in (("fixed109", "#a54559", "Fixed 109 ms"),
                                ("constant_median", "#8a7e31", "Trained constant"),
                                (winner, colors[j], "Selected timing model")):
            absolute = np.sort(np.abs(y-predictions[k][n][mask]))
            ax.plot(absolute, np.arange(1, len(absolute)+1)/len(absolute)*100, color=color, label=label)
        ax.set(xlabel="Absolute residual against decoded marker (ms)", ylabel="Frames within residual (%)", xlim=(0, 45), ylim=(0, 100))
        ax.grid(alpha=.2)
        ax.legend(fontsize=8)
    fig.suptitle("Causal timestamp predictors — software barcode reference, not verified exposure time", fontsize=13)
    fig.savefig(OUT / "comparison.png", dpi=170)
    fig.savefig(OUT / "comparison.svg")
    plt.close(fig)


def main():
    global OUT, AUDIT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="Verify cached reads and original image hashes; rebuild timing inputs")
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--audit-dir", type=Path, default=AUDIT, help="Directory holding the raw barcode audit")
    args = parser.parse_args()
    OUT, AUDIT = args.output_dir.expanduser().resolve(), args.audit_dir.expanduser().resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.prepare:
        prepare()
    else:
        analyze()


if __name__ == "__main__":
    main()
