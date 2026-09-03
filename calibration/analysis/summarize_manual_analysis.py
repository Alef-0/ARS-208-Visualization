"""Summarize saved regular analyses without reopening images or accepting extra frames."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from calibration.decoding.markers import DisplayEvidence
from calibration.analysis.recording import load_epochs, load_manifest, normalize_timing_row, summarize


def summarize_analysis(path):
    path = Path(path).resolve()
    report = json.loads(path.read_text())
    if not report.get("manual_regions"):
        raise ValueError("This summary requires an analysis with manually marked panels")
    folder = Path(report["recording_directory"])
    evidence = DisplayEvidence.load(folder)
    epoch = load_epochs(folder)
    timing = [normalize_timing_row(r, epoch) for r in load_manifest(folder)]
    selected = {r["camera_frame"]: r for r in report["frames"] + report["excluded_frames"]}
    frames = []
    for row in timing:
        item = selected[row["camera_frame"]]
        observations = item.get("observations", item.get("barcode_observations", []))
        decoded, once = {}, set()
        for o in observations:
            marker = evidence.lookup(o["code"], round(row["frame_monotonic_ns"]/1e6))
            if (marker is None or marker["corner"] != o["corner"] or
                    (row["received_monotonic_ns"] is not None and marker["marker_ns"] > row["received_monotonic_ns"])):
                continue
            once.add(o["corner"])
            if o.get("display_index") is not None:
                decoded[marker["index"]] = marker
        indices = sorted(decoded)
        newest = decoded[indices[-1]] if indices else None
        frames.append({
            "camera_frame": row["camera_frame"],
            "elapsed_s": (row["frame_monotonic_ns"]-timing[0]["frame_monotonic_ns"])/1e9,
            "corners_one_band": sorted(once), "corners_two_bands": sorted({i % 4 for i in indices}),
            "display_indices": indices, "indicators": item.get("outlined_corners", []),
            "reason": item.get("reason"),
            "incompatible_with_logged_history": bool(indices and indices[-1]-indices[0] >= evidence.visible_frames),
            "newest_timing_clean": bool(newest and evidence.marker_timing(newest["index"])["status"] == "clean"),
            "newest_decoded_pts_offset_ms": (row["frame_monotonic_ns"]-newest["marker_ns"])/1e6 if newest else None,
            "newest_decoded_receipt_offset_ms": ((row["received_monotonic_ns"]-newest["marker_ns"])/1e6
                if newest and row["received_monotonic_ns"] is not None else None),
        })

    def coverage(rows):
        return {
            "frames": len(rows),
            "frames_with_codes": sum(bool(r["display_indices"]) for r in rows),
            "corners_one_band_tl_tr_br_bl": [sum(c in r["corners_one_band"] for r in rows) for c in range(4)],
            "corners_two_bands_tl_tr_br_bl": [sum(c in r["corners_two_bands"] for r in rows) for c in range(4)],
            "four_corners_two_bands": sum(len(r["corners_two_bands"]) == 4 for r in rows),
            "incompatible_with_logged_history": sum(r["incompatible_with_logged_history"] for r in rows),
            "indicator_count_distribution": dict(Counter(len(r["indicators"]) for r in rows)),
        }

    compatible = [r for r in frames if not r["incompatible_with_logged_history"] and r["newest_timing_clean"]]
    n = len(frames)
    return {
        "recording": str(folder), "source_report": str(path),
        "source_report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "analysis_alpha": report.get("analysis_undistortion_alpha", report["manual_regions"]["alpha"]),
        "visible_history": evidence.visible_frames, "coverage": coverage(frames),
        "coverage_by_thirds": [coverage(frames[n*i//3:n*(i+1)//3]) for i in range(3)],
        "accepted_frames": report["counts"]["accepted_frames"],
        "exclusion_reasons": report["exclusion_reason_counts"],
        "accepted_offset_metrics": report["metrics"]["pts_screen_offset_ms"],
        "diagnostic_only": {
            "meaning": "Newest decoded payload, not a verified current marker or exposure calibration. No correction is inferred.",
            "pts_minus_newest_decoded_ms": summarize(r["newest_decoded_pts_offset_ms"] for r in frames),
            "receipt_minus_newest_decoded_ms": summarize(r["newest_decoded_receipt_offset_ms"] for r in frames),
            "compatible_clean_newest_ms": summarize(r["newest_decoded_pts_offset_ms"] for r in compatible),
        },
        "frames": frames,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    results = [summarize_analysis(path) for path in args.reports]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2)+"\n")
    for r in results:
        print(json.dumps({k: r[k] for k in ("recording", "analysis_alpha", "coverage", "accepted_frames", "diagnostic_only")}, indent=2))


if __name__ == "__main__":
    main()
