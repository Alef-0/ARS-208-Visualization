#!/usr/bin/env python3
"""Audit these recordings without changing images or application settings.

Requires the existing OpenCV, NumPy, Pygame, installations.
The newest DECODED marker is a diagnostic, not a verified exposure timestamp.
Run from any directory; reports default to recordings/calibration_analysis_20260903.
"""

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import sys

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = ROOT / "recordings" / "calibration_analysis_20260903"
sys.path.insert(0, str(ROOT))

import cv2 as cv
import numpy as np

def initialize_analysis():
    # Plotting saved reports needs no camera/display modules or Pygame.
    global DisplayEvidence, load_epochs, load_manifest, normalize_ean13
    global normalize_timing_row, summarize, write_csv
    global OpenCVReader, Undistorter
    from calibration.decoding.opencv import OpenCVReader
    from calibration.decoding.markers import DisplayEvidence
    from calibration.decoding.geometry import Undistorter
    from calibration.analysis.recording import (
        load_epochs, load_manifest, normalize_ean13, normalize_timing_row,
        summarize, write_csv,
    )


def matched(symbols, evidence, reference_ms, received_ns=None):
    result, rejected = [], []
    for symbol in symbols:
        code = normalize_ean13(symbol["raw_code"], symbol["type"])
        row = evidence.lookup(code, reference_ms) if code else None
        reason = ("checksum_or_journal_mismatch" if row is None else
                  "marker_generated_after_camera_receipt" if received_ns is not None and row["marker_ns"] > received_ns else None)
        if reason:
            rejected.append({**symbol, "rejection_reason": reason})
        else:
            result.append({**symbol, "code": code, "index": row["index"],
                           "corner": row["corner"], "marker_ns": row["marker_ns"]})
    return result, rejected


def metrics(rows, fields):
    return {field: summarize(row.get(field) for row in rows) for field in fields}


def residual(values, correction):
    v = np.asarray(values) - correction
    return {"count": len(v), "correction_ms": correction,
            "bias_ms": float(np.mean(v)), "mae_ms": float(np.mean(abs(v))),
            "rmse_ms": float(np.sqrt(np.mean(v*v))),
            "p95_absolute_ms": float(np.percentile(abs(v), 95))} if len(v) else None


def compare_intrinsics(path, output, detector):
    """Small paired sensitivity check, not a validation of the camera calibration."""
    undistorter = Undistorter(path)
    matrix, distortion = undistorter.matrix, undistorter.distortion
    _, new_matrix, _ = undistorter.geometry((1920, 1080))
    reports, recovery = [], []
    for name in ["calibration_first", "calibration_second"]:
        directory = ROOT/"recordings"/name
        evidence, epochs = DisplayEvidence.load(directory), load_epochs(directory)
        frames = []
        for raw in load_manifest(directory)[::50]:
            row = normalize_timing_row(raw, epochs)
            image = cv.imread(str(directory/row["camera_frame"]))
            entry = {"frame": row["camera_frame"]}
            for method, pixels in [("raw", image), ("undistorted", undistorter.image(image))]:
                symbols = detector.decode(pixels)
                good, _ = matched(symbols, evidence, round(row["frame_monotonic_ns"]/1e6), row["received_monotonic_ns"])
                entry[method] = sorted({s["index"] for s in good})
            frames.append(entry)
        reports.append({"recording": name, "intrinsics_file": str(path), "K": matrix.tolist(),
                        "distortion": distortion.tolist(), "newK": new_matrix.tolist(), "sample_step": 50,
                        "assumption": "Provided coefficients apply to these 1920x1080 camera images; calibration resolution/model identity is not included in the supplied JSON.",
                        "frames": frames, "summary": {
                            "raw_decoded": sum(bool(r["raw"]) for r in frames),
                            "corrected_decoded": sum(bool(r["undistorted"]) for r in frames),
                            "same_newest": sum(bool(r["raw"]) and bool(r["undistorted"]) and r["raw"][-1] == r["undistorted"][-1] for r in frames),
                            "corrected_finds_newer": sum(bool(r["undistorted"]) and (not r["raw"] or r["undistorted"][-1] > r["raw"][-1]) for r in frames),
                            "raw_finds_newer": sum(bool(r["raw"]) and (not r["undistorted"] or r["raw"][-1] > r["undistorted"][-1]) for r in frames),
                        }})
        observations = [json.loads(line) for line in (output/name/"raw_barcode_observations.jsonl").read_text().splitlines()]
        manifest = {row["frame"]: row for row in load_manifest(directory)}
        for failed in (row for row in observations if not row["decoded_count"]):
            row = normalize_timing_row(manifest[failed["camera_frame"]], epochs)
            image = cv.imread(str(directory/row["camera_frame"]))
            corrected = undistorter.image(image)
            symbols = detector.decode(corrected)
            good, bad = matched(symbols, evidence, round(row["frame_monotonic_ns"]/1e6), row["received_monotonic_ns"])
            recovery.append({"recording": name, "frame": row["camera_frame"],
                             "accepted_codes": good, "rejected_codes": bad,
                             "note": "Recovery only; excluded from the unwarped-image statistics."})
    (output/"intrinsics_comparison.json").write_text(json.dumps(reports, indent=2)+"\n")
    (output/"failed_frame_correction_check.json").write_text(json.dumps(recovery, indent=2)+"\n")


def plot_results(reports, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = ["#177E89", "#B55726"]
    fig, axes = plt.subplots(2, 2, figsize=(13, 8.5), layout="constrained")
    for idx, ((report, rows), color) in enumerate(zip(reports, colors)):
        ax = axes[0, idx]
        values = [r for r in rows if r["decoded_count"] and r["eligible_for_optical_diagnostics"]]
        ax.scatter([r["elapsed_s"] for r in values], [r["freshest_pts_offset_ms"] for r in values],
                   s=5, alpha=.4, color=color, rasterized=True)
        ax.axhline(109, color="#555555", ls="--", label="Current correction: 109 ms")
        ax.set(title=Path(report["recording"]).name, xlabel="Seconds into recording",
               ylabel="PTS minus newest decoded marker (ms)", ylim=(50, 185))
        ax.legend(fontsize=8)
    groups = [("all_decoded_diagnostic_only", "All coherent decoded frames"),
              ("four_corners_decoded_diagnostic_only", "Four corners decoded (known overlap)"),
              ("no_detected_overlap_and_clean_diagnostic_only", "No decoded overlap + clean software timing")]
    ax = axes[1, 0]
    for idx, ((report, _), color) in enumerate(zip(reports, colors)):
        for y, (group, _) in enumerate(groups):
            m = report["optical_diagnostic_metrics"][group]["freshest_pts_offset_ms"]
            median = m["median"]
            ax.errorbar(median, y+(idx-.5)*.19,
                        xerr=[[median-m["p05"]], [m["p95"]-median]],
                        fmt="o", color=color, capsize=3, label=Path(report["recording"]).name if y == 0 else None)
    ax.axvline(109, color="#555555", ls="--")
    ax.set(yticks=range(len(groups)), yticklabels=[label for _, label in groups],
           xlabel="Median and central 90% of offsets (ms)", title="Marker-selection sensitivity")
    ax.invert_yaxis()
    ax.legend(fontsize=8)
    ax = axes[1, 1]
    for idx, ((report, _), color) in enumerate(zip(reports, colors)):
        directory = Path(report["recording"])
        journal = [json.loads(line) for line in (directory/"display_timestamps.jsonl").read_text().splitlines()]
        intervals = [r["interval_ns"]/1e6 for r in journal if r.get("kind") == "frame" and r.get("interval_ns")]
        ax.hist(intervals, bins=np.arange(8, 80, 1), histtype="step", lw=1.5,
                color=color, label=directory.name)
    ax.axvline(1000/60, color="#555555", ls="--", label="60 Hz period")
    ax.set(xlabel="Display flip-return interval (ms)", ylabel="Display updates (log scale)",
           title="Display pacing; these are software observations", yscale="log")
    ax.legend(fontsize=8)
    for ax in axes.flat:
        ax.grid(alpha=.2)
    fig.suptitle("Calibration recordings — diagnostics, not validated exposure calibration", fontsize=15)
    fig.savefig(output/"diagnostic_summary.png", dpi=160)
    fig.savefig(output/"diagnostic_summary.svg")
    plt.close(fig)


def audit(recording, output, step, detector, reuse=False):
    evidence = DisplayEvidence.load(recording)
    originals = load_manifest(recording)
    names = [row["frame"] for row in originals]
    if len(names) != len(set(names)) or any(not (recording/name).is_file() for name in names):
        raise ValueError("Duplicate manifest names or missing images")
    epochs = load_epochs(recording)
    normalized = [normalize_timing_row(row, epochs) for row in originals]
    cache_path = output/recording.name/"raw_barcode_observations.jsonl"
    cached = {r["camera_frame"]: r for line in cache_path.read_text().splitlines()
              if (r := json.loads(line))} if reuse else {}
    result = []
    for number in range(0, len(normalized), step):
        row = normalized[number]
        if reuse:
            original = cached[row["camera_frame"]]
            csymbols = original["opencv"] + original["rejected_opencv"]
        else:
            image = cv.imread(str(recording / row["camera_frame"]))
            if image is None:
                raise ValueError("Unreadable image: " + row["camera_frame"])
            csymbols = detector.decode(image)
        reference_ms = round(row["frame_monotonic_ns"] / 1e6)
        oc, orej = matched(csymbols, evidence, reference_ms, row["received_monotonic_ns"])
        by_index = {s["index"]: s for s in oc}
        indices = sorted(by_index)
        obs = {"camera_frame": row["camera_frame"],
               "image_sha256": hashlib.sha256((recording/row["camera_frame"]).read_bytes()).hexdigest(),
               "elapsed_s": (row["frame_monotonic_ns"]-normalized[0]["frame_monotonic_ns"])/1e9,
               "frame_monotonic_ns": row["frame_monotonic_ns"],
               "opencv": oc, "rejected_opencv": orej,
               "decoded_indices": indices, "decoded_count": len(indices),
               "strict_exposure_calibration_accepted": False}
        if reuse and obs["image_sha256"] != original["image_sha256"]:
            raise ValueError("Original image changed since decoding: " + row["camera_frame"])
        if indices:
            latest = by_index[indices[-1]]
            timing = evidence.marker_timing(latest["index"])
            obs.update(
                newest_decoded_index=latest["index"], newest_decoded_corner=latest["corner"],
                newest_decoded_marker_ns=latest["marker_ns"],
                decoded_span_ms=(latest["marker_ns"]-by_index[indices[0]]["marker_ns"])/1e6,
                index_span=indices[-1]-indices[0],
                eligible_for_optical_diagnostics=indices[-1]-indices[0] <= 3,
                decoded_corner_count=len({s["corner"] for s in by_index.values()}),
                incompatible_with_single_display_state=indices[-1]-indices[0] >= evidence.visible_frames,
                freshest_pts_offset_ms=(row["frame_monotonic_ns"]-latest["marker_ns"])/1e6,
                freshest_receipt_offset_ms=(row["received_monotonic_ns"]-latest["marker_ns"])/1e6,
                freshest_timing_status=timing["status"], freshest_timing_issues=timing["issue_codes"],
            )
            for engine, decoded in (("opencv", oc),):
                if decoded:
                    obs[engine+"_freshest_pts_offset_ms"] = (
                        row["frame_monotonic_ns"]-max(s["marker_ns"] for s in decoded))/1e6
        result.append(obs)
        if len(result) % 200 == 0:
            print(recording.name, "processed", len(result), "images", flush=True)

    fields = ["freshest_pts_offset_ms", "freshest_receipt_offset_ms", "decoded_span_ms",
              "opencv_freshest_pts_offset_ms"]
    optical = [r for r in result if r["decoded_count"]]
    coherent = [r for r in optical if r["eligible_for_optical_diagnostics"]]
    groups = {
        "all_decoded_diagnostic_only": coherent,
        "four_corners_decoded_diagnostic_only": [r for r in coherent if r["decoded_corner_count"] == 4],
        "clean_software_timing_diagnostic_only": [r for r in coherent if r["freshest_timing_status"] == "clean"],
        "no_detected_overlap_and_clean_diagnostic_only": [r for r in coherent if not r["incompatible_with_single_display_state"] and r["freshest_timing_status"] == "clean"],
    }
    timing_rows = []
    for raw, row in zip(originals, normalized):
        ntp = row["reference_ntp_ns"]
        timing_rows.append({
            "receipt_media_ms": (row["received_monotonic_ns"]-row["frame_monotonic_ns"])/1e6,
            "writer_after_receipt_ms": (raw["saved_unix_ns"]-row["received_unix_ns"])/1e6,
            "ntp_media_ms": (ntp-row["media_unix_ns"])/1e6 if ntp is not None else None,
            "system_clock_error_ms": row["system_clock_error_ns"]/1e6,
            "pts_step_ms": None,
        })
    for i in range(1, len(timing_rows)):
        timing_rows[i]["pts_step_ms"] = (normalized[i]["pts_ns"]-normalized[i-1]["pts_ns"])/1e6
    report = {
        "recording": str(recording), "sample_step": step,
        "method": "Unwarped original pixels; OpenCV EAN checks; exact payload-to-journal lookup.",
        "limitation": "Newest decoded marker may not be the actual newest marker. No newest outline or physical exposure is verified. Optical offsets are exploratory, not calibration recommendations.",
        "optical_filters": "Reject codes generated after camera receipt. Exclude whole images spanning more than four consecutive marker indices from offset statistics (wide temporal mixing or false decoding). Every observation and rejection is retained.",
        "images": len(normalized), "images_audited": len(result), "images_decoded": len(optical),
        "decoder_frame_counts": {e: sum(bool(r[e]) for r in result) for e in ["opencv"]},
        "decoded_count_histogram": dict(collections.Counter(r["decoded_count"] for r in result)),
        "frames_excluded_wide_marker_sequence": len(optical)-len(coherent),
        "causality_rejected_symbols": sum(s.get("rejection_reason") == "marker_generated_after_camera_receipt" for r in result for e in ["rejected_opencv"] for s in r[e]),
        "incompatible_single_state_frames": sum(r.get("incompatible_with_single_display_state", False) for r in coherent),
        "four_corner_frames": sum(r.get("decoded_corner_count") == 4 for r in coherent),
        "software_clean_optical_frames": len(groups["clean_software_timing_diagnostic_only"]),
        "pts_span_s": (normalized[-1]["pts_ns"]-normalized[0]["pts_ns"])/1e9,
        "pts_average_fps": (len(normalized)-1)*1e9/(normalized[-1]["pts_ns"]-normalized[0]["pts_ns"]),
        "missing_ntp": sum(r["reference_ntp_ns"] is None for r in normalized),
        "timing_metrics": metrics(timing_rows, list(timing_rows[0])),
        "display_totals": evidence.timing_catalog()["totals"],
        "display_interval_ms": summarize(r["interval_ns"]/1e6 for r in evidence.frames if r.get("interval_ns")),
        "display_marker_to_flip_return_ms": summarize((r["flip_return_ns"]-r["marker_ns"])/1e6 for r in evidence.frames),
        "optical_diagnostic_metrics": {k: metrics(v, fields) for k, v in groups.items()},
        "time_quartiles_diagnostic_only": [metrics([r for r in coherent if low <= r["elapsed_s"] < high], fields)
                 for low, high in zip(np.linspace(0, (normalized[-1]["frame_monotonic_ns"]-normalized[0]["frame_monotonic_ns"])/1e9+1e-6, 5)[:-1],
                                      np.linspace(0, (normalized[-1]["frame_monotonic_ns"]-normalized[0]["frame_monotonic_ns"])/1e9+1e-6, 5)[1:])],
        "recording_summary": json.loads((recording/"camera_recording_summary.json").read_text()),
        "session": json.loads((recording/"camera_timing_session.json").read_text()),
    }
    (output/recording.name).mkdir(parents=True, exist_ok=True)
    destination = output/recording.name
    with (destination/"raw_barcode_observations.jsonl").open("w") as handle:
        for row in result:
            handle.write(json.dumps(row)+"\n")
    write_csv(destination/"raw_barcode_frames.csv", [{k:v for k,v in row.items() if k not in {"opencv", "rejected_opencv"}} for row in result])
    (destination/"evidence_summary.json").write_text(json.dumps(report, indent=2)+"\n")
    print(recording.name, "decoded", len(optical), "/", len(result),
          "incompatible single state", report["incompatible_single_state_frames"], flush=True)
    return report, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-step", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--reuse-observations", action="store_true", help="Reassess saved decoder outputs after verifying original image hashes")
    parser.add_argument("--intrinsics", type=Path, help="Optionally run a 55-image paired correction sensitivity check")
    parser.add_argument("--plots-only", action="store_true", help="Plot previously saved results; requires a compatible Matplotlib/NumPy environment")
    args = parser.parse_args()
    if args.sample_step < 1:
        parser.error("sample-step must be positive")
    if args.plots_only:
        reports = []
        for name in ["calibration_first", "calibration_second"]:
            directory = args.output_dir/name
            reports.append((json.loads((directory/"evidence_summary.json").read_text()),
                            [json.loads(line) for line in (directory/"raw_barcode_observations.jsonl").read_text().splitlines()]))
        plot_results(reports, args.output_dir)
        return
    initialize_analysis()
    cv.setNumThreads(2)
    detector = OpenCVReader()
    reports = []
    try:
        for name in ["calibration_first", "calibration_second"]:
            reports.append(audit(ROOT/"recordings"/name, args.output_dir, args.sample_step, detector, args.reuse_observations))
        if args.intrinsics:
            compare_intrinsics(args.intrinsics, args.output_dir, detector)
    finally:
        detector.close()
    first = [r["freshest_pts_offset_ms"] for r in reports[0][1] if r["decoded_count"] and r["eligible_for_optical_diagnostics"] and r["freshest_timing_status"] == "clean"]
    second = [r["freshest_pts_offset_ms"] for r in reports[1][1] if r["decoded_count"] and r["eligible_for_optical_diagnostics"] and r["freshest_timing_status"] == "clean"]
    if first and second:
        comparison = {"warning": "Diagnostic marker selection only; holdout does not establish physical exposure accuracy.",
                      "first_run_median_applied_to_second": residual(second, float(np.median(first))),
                      "109_ms_applied_to_first": residual(first, 109),
                      "109_ms_applied_to_second": residual(second, 109)}
        (args.output_dir/"diagnostic_holdout.json").write_text(json.dumps(comparison, indent=2)+"\n")


if __name__ == "__main__":
    main()
