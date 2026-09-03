"""Calibration file suggestions shared by GUI and standalone tools."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def suggested_intrinsics(folder=None):
    candidates = [Path(folder).expanduser() / "intrinsic_coefficients.json"] if folder else []
    candidates.extend([
        PROJECT_ROOT / "content" / "intrinsic_coefficients.json",
        PROJECT_ROOT.parent / "Segcom Sincronização GERAL" / "Extrinsic" / "intrinsic_coefficients.json",
    ])
    return next((str(path) for path in candidates if path.is_file()), "")
