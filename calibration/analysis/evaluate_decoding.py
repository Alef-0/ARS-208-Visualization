#!/usr/bin/env python3
"""Compare OpenCV preprocessing, learned regions and undistortion on saved frames.

No windows or hardware are opened. Input recordings are never modified.
Example: python3 -m calibration.analysis.evaluate_decoding recordings/calibration_third
    --intrinsics /path/to/intrinsic_coefficients.json --step 25 --alphas 0 .25 .5 .75 1
    --output-dir recordings/calibration_third_analysis
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time

import cv2 as cv
import numpy as np

from calibration.decoding.opencv import OpenCVReader, RegionDecoder, normalized_code
from calibration.decoding.markers import DisplayEvidence
from calibration.decoding.regions import ManualRegions
from calibration.analysis.recording import load_epochs, load_manifest, normalize_timing_row
from calibration.decoding.geometry import Undistorter


def matched(symbols, evidence, timing):
    accepted, rejected = [], []
    for symbol in symbols:
        code = normalized_code(symbol["raw_code"], symbol["type"])
        row = evidence.lookup(code, round(timing["frame_monotonic_ns"]/1e6)) if code else None
        if row is None or row["marker_ns"] > timing["received_monotonic_ns"]:
            rejected.append(symbol)
        elif symbol.get("region_corner", row["corner"]) == row["corner"]:
            accepted.append({**symbol, "code": code, "display_index": row["index"],
                             "corner": row["corner"]})
    return accepted, rejected


def totals(records):
    modes = list(records[0]["codes"]) if records else []
    return {mode: {"frames_with_codes": sum(bool(r["codes"][mode]) for r in records),
                   "frame_code_pairs": sum(len(r["codes"][mode]) for r in records),
                   "frames_by_corner_tl_tr_br_bl": [sum(any(i % 4 == c for i in r["codes"][mode])
                                                       for r in records) for c in range(4)],
                   "conflicting_panel_frames": sum(mode in r["conflicts"] for r in records)}
            for mode in modes}


def evaluate(folder, output, intrinsics=None, step=25, alphas=(0,), binary=False):
    if step < 1 or any(not np.isfinite(a) or not 0 <= a <= 1 for a in alphas):
        raise ValueError("Step must be positive and alphas between 0 and 1")
    folder, output = Path(folder).resolve(), Path(output).resolve()
    if folder == output:
        raise ValueError("Use a separate output directory to preserve recording evidence")
    evidence = DisplayEvidence.load(folder)
    if evidence is None:
        raise ValueError("A display timing journal is required")
    epoch = load_epochs(folder)
    rows = [normalize_timing_row(r, epoch) for r in load_manifest(folder)]
    names = [r["camera_frame"] for r in rows]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate camera image names")
    if any(r["frame_monotonic_ns"] is None or r["received_monotonic_ns"] is None for r in rows):
        raise ValueError("Monotonic frame and receipt times are required")
    manual = ManualRegions.load(folder, intrinsics)
    if manual and intrinsics is None:
        intrinsics = manual.intrinsics_path
    corrected = {f"alpha_{a:g}": Undistorter(intrinsics, alpha=a) for a in alphas} if intrinsics else {}
    manual_quads = {name: [(i, q[[3, 0, 1, 2]]) for i, q in enumerate(
        manual.for_undistorter(model, manual.size))] for name, model in corrected.items()} if manual else {}
    decoders = {name: RegionDecoder(evidence, contrast=True, binary=binary)
                for name in ("original", *corrected)}
    reader = OpenCVReader()
    clahe = cv.createCLAHE(2, (8, 8))
    records, spatial = [], {name: [] for name in decoders}
    started = time.monotonic()
    output.mkdir(parents=True, exist_ok=True)
    with (output / "observations.jsonl").open("w", encoding="utf-8") as handle:
        for number, index in enumerate(range(0, len(rows), step)):
            timing = rows[index]
            path = (folder/timing["camera_frame"]).resolve()
            if not path.is_relative_to(folder):
                raise ValueError("Camera image path is outside the recording")
            frame = cv.imread(str(path))
            if frame is None:
                raise ValueError("Unreadable camera image: " + str(path))
            gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
            symbols = {
                "baseline": reader.decode(gray),
                "full_frame_otsu": reader.decode(cv.threshold(gray, 0, 255, cv.THRESH_BINARY | cv.THRESH_OTSU)[1]),
                "full_frame_contrast": reader.decode(clahe.apply(gray)),
            }
            for name, decoder in decoders.items():
                pixels = corrected[name].image(frame) if name in corrected else frame
                symbols[name] = decoder.decode(pixels, round(timing["frame_monotonic_ns"]/1e6),
                                                timing["received_monotonic_ns"], number)
                spatial[name].append({str(c): q.tolist() for c, q in decoder.regions()})
                if name in manual_quads:
                    gray_manual = cv.cvtColor(pixels, cv.COLOR_BGR2GRAY)
                    prefix = "manual_" + name
                    symbols[prefix+"_gray"] = reader.decode_regions(gray_manual, manual_quads[name])
                    symbols[prefix+"_contrast"] = reader.decode_regions(clahe.apply(gray_manual), manual_quads[name])
                    symbols[prefix] = symbols[prefix+"_gray"] + symbols[prefix+"_contrast"]
                    if binary:
                        symbols[prefix+"_otsu"] = reader.decode_regions(gray_manual, manual_quads[name], binary=True)
            observations, rejected, codes, conflicts = {}, {}, {}, []
            for name, values in symbols.items():
                good, bad = matched(values, evidence, timing)
                observations[name], rejected[name] = good, bad
                codes[name] = sorted({s["display_index"] for s in good})
                by_corner = {}
                for s in good:
                    by_corner.setdefault(s["corner"], set()).add(s["display_index"])
                if any(len(values) > 1 for values in by_corner.values()):
                    conflicts.append(name)
            codes["combined"] = sorted({i for name in decoders for i in codes[name]})
            if len(codes["combined"]) != len({i % 4 for i in codes["combined"]}):
                conflicts.append("combined")
            record = {"camera_frame": timing["camera_frame"], "frame_index": index,
                      "image_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "codes": codes, "conflicts": conflicts}
            handle.write(json.dumps({**record, "observations": observations, "rejected": rejected})+"\n")
            records.append(record)
            if (number+1) % 100 == 0:
                print(f"Processed {number+1} images in {time.monotonic()-started:.1f}s", flush=True)
    split = max(1, len(records)*2//3)
    geometry = {}
    for name, samples in spatial.items():
        geometry[name] = {"resets": decoders[name].resets, "corners": {}}
        for corner in range(4):
            quads = np.array([s[str(corner)] for s in samples if str(corner) in s])
            if quads.size:
                centers = quads.mean(axis=1)
                geometry[name]["corners"][str(corner)] = {
                    "supported_frames": len(quads), "median_quad": np.median(quads, axis=0).tolist(),
                    "center_p05": np.percentile(centers, 5, axis=0).tolist(),
                    "center_p95": np.percentile(centers, 95, axis=0).tolist()}
    comparisons = {}
    for name in corrected:
        counts = Counter()
        for row in records:
            a, b = row["codes"]["original"], row["codes"][name]
            counts["same_newest" if a and b and a[-1] == b[-1] else
                   "original_newer" if a and (not b or a[-1] > b[-1]) else
                   "undistorted_newer" if b else "neither"] += 1
            counts["original_only"] += bool(a and not b)
            counts["undistorted_only"] += bool(b and not a)
        comparisons[name] = dict(counts)
    report = {"recording": str(folder), "opencv": cv.__version__, "images": len(rows),
              "sample_step": step, "evaluated": len(records), "alphas": list(alphas) if intrinsics else [],
              "binary_retry": binary, "elapsed_seconds": time.monotonic()-started,
              "intrinsics": str(intrinsics) if intrinsics else None,
              "manual_regions": manual.data if manual else None,
              "intrinsics_resolution_assumed": bool(corrected and not next(iter(corrected.values())).calibration_size),
              "limitation": "Counts measure journal-matched optical coverage, not timestamp accuracy. "
                            "A newest decoded code is not necessarily the current marker. "
                            "Conflicting generations and display timing problems must be excluded from offset estimates.",
              "metrics": totals(records), "first_two_thirds": totals(records[:split]),
              "last_third": totals(records[split:]), "paired_variants": comparisons,
              "learned_geometry": geometry, "frames": records}
    sources = [Path(__file__), Path(__file__).parents[1]/"decoding/opencv.py",
               Path(__file__).parents[1]/"decoding/geometry.py"]
    report["source_sha256"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    report["input_sha256"] = {name: hashlib.sha256((folder/name).read_bytes()).hexdigest()
                              for name in ("display_timestamps.jsonl", "camera_timestamps.jsonl")}
    if intrinsics:
        report["intrinsics_sha256"] = hashlib.sha256(Path(intrinsics).read_bytes()).hexdigest()
    (output / "summary.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("evaluated", "metrics", "paired_variants", "elapsed_seconds")}, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--intrinsics", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--step", type=int, default=25)
    parser.add_argument("--alphas", type=float, nargs="+", default=[0])
    parser.add_argument("--binary", action="store_true", help="Also try local Otsu on learned regions")
    args = parser.parse_args()
    cv.setNumThreads(2)
    evaluate(args.recording, args.output_dir, args.intrinsics, args.step, args.alphas, args.binary)


if __name__ == "__main__":
    main()
