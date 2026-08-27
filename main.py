from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import signal
import time
from multiprocessing import get_context
from queue import Empty

import sitecustomize
import FreeSimpleGUI as sg

import MAIN_BASE as base
from CALIBRATION.calibration_screen_clock import run_calibration_clock
from CAMERA.camera_gstreamer import gstreamer_main
from CONNECTION.connection_main import create_connection_communication
from GPS.gps_connection import main as gps_main
from menu_configurations import Configurations
from CAPTURE.playback import playback_main
from CAPTURE.snapshot_playback import snapshot_playback_main


@dataclass
class RuntimeState:
    pending_playback_folder: str | None = None
    pending_snapshot_playback: dict | None = None
    recording_stop_pending: bool = False
    calibration_recording_deadline: float | None = None
    calibration_recording_root: str | None = None
    calibration_recording_folder: str | None = None
    pending_calibration_camera: dict | None = None
    calibration_clock_process: object | None = None
    calibration_clock_stop_event: object | None = None
    process_context: object | None = None


def _resolution(values):
    try:
        width = int(str(values.get("playback_width", "1280")).strip())
        height = int(str(values.get("playback_height", "720")).strip())
    except ValueError as error:
        raise ValueError("Playback width and height must be integers") from error
    if width <= 0 or height <= 0:
        raise ValueError("Playback width and height must be positive")
    return width, height


def _point_cutoff(values):
    try:
        cutoff = float(str(values.get("point_cutoff", "15")).strip())
    except ValueError as error:
        raise ValueError("Point cutoff must be a number in meters") from error
    if cutoff <= 0:
        raise ValueError("Point cutoff must be greater than zero")
    return cutoff


def _camera_latency_settings(values):
    try:
        pipeline_latency_ms = int(str(values.get("camera_pipeline_latency", "250")).strip())
        adjustment_ms = float(str(values.get("camera_latency_adjustment", "250")).strip())
    except ValueError as error:
        raise ValueError("Both camera latency values must be numeric") from error
    if pipeline_latency_ms < 0:
        raise ValueError("rtspsrc latency cannot be negative")
    return pipeline_latency_ms, adjustment_ms


def _camera_recording_interval(values):
    try:
        interval_ms = float(str(values.get("camera_recording_interval", "250")).strip())
    except ValueError as error:
        raise ValueError("Camera recording interval must be numeric") from error
    if interval_ms <= 0:
        raise ValueError("Camera recording interval must be greater than zero")
    return interval_ms


def _request_calibration_camera(
    values,
    config,
    runtime,
    send_radar,
    send_cam,
    send_playback,
    send_snapshot_playback,
):
    if config.calibration_camera:
        runtime.pending_calibration_camera = None
        runtime.calibration_recording_deadline = None
        runtime.calibration_recording_root = None
        if config.calibration_recording or runtime.calibration_recording_folder:
            send_cam.send(("record_stop", None))
        send_cam.send(("calibration_camera", {"active": False}))
        return

    try:
        pipeline_latency_ms, adjustment_ms = _camera_latency_settings(values)
        recording_interval_ms = _camera_recording_interval(values)
    except ValueError as error:
        config.show_calibration_error(str(error))
        return

    config.set_calibration_camera_pending()
    runtime.pending_calibration_camera = {
        "pipeline_latency_ms": pipeline_latency_ms,
        "latency_adjustment_ms": adjustment_ms,
        "recording_interval_ms": recording_interval_ms,
    }
    if config.recording or config.recording_pending:
        base._request_recording_stop(config, runtime, send_cam)
    if config.connected_radar:
        send_radar.send(("conn_radar", None))
    if config.playback_pending:
        runtime.pending_playback_folder = None
        config.change_playback({"active": False, "completed": False})
    if config.playback:
        send_playback.send(("playback_stop", None))
    if config.snapshot_playback_pending:
        runtime.pending_snapshot_playback = None
        config.change_snapshot_playback({"active": False, "completed": False})
    if config.snapshot_playback:
        send_snapshot_playback.send(("snapshot_playback_stop", None))
    _maybe_open_calibration_camera(config, runtime, send_cam)


def _maybe_open_calibration_camera(config, runtime, send_cam):
    payload = runtime.pending_calibration_camera
    if (
        payload is None
        or config.connected_radar
        or config.recording
        or config.recording_pending
        or config.playback
        or config.playback_pending
        or config.snapshot_playback
        or config.snapshot_playback_pending
    ):
        return
    runtime.pending_calibration_camera = None
    recording_interval_ms = payload.pop("recording_interval_ms")
    send_cam.send(("camera_latency_settings", payload))
    send_cam.send((
        "camera_recording_interval",
        {"interval_ms": recording_interval_ms},
    ))
    send_cam.send(("calibration_camera", {"active": True}))


def _start_calibration_clock(values, config, runtime):
    process = runtime.calibration_clock_process
    if process is not None and process.is_alive():
        return

    recording_root = None
    if config.calibration_camera and config.connected_cam and not config.calibration_recording:
        recording_root = Path(values.get("record_folder", "")).expanduser()
        if not recording_root.is_dir():
            config.show_calibration_error(
                "Select an existing recording destination before starting the clock"
            )
            return

    stop_event = runtime.process_context.Event()
    process = runtime.process_context.Process(
        target=run_calibration_clock,
        args=(stop_event,),
        name="calibration-clock",
    )
    process.start()
    runtime.calibration_clock_process = process
    runtime.calibration_clock_stop_event = stop_event
    config.change_calibration_clock(True)

    if not (config.calibration_camera and config.connected_cam):
        runtime.calibration_recording_deadline = None
        runtime.calibration_recording_root = None
        config.window["calibration_status"].update(
            "CLOCK ACTIVE — CAMERA 4 IS NOT OPEN, SO RECORDING WAS NOT SCHEDULED"
        )
        return
    if config.calibration_recording:
        runtime.calibration_recording_deadline = None
        runtime.calibration_recording_root = None
        config.window["calibration_status"].update(
            "CLOCK ACTIVE — CAMERA 4 IS ALREADY RECORDING"
        )
        return
    assert recording_root is not None
    runtime.calibration_recording_root = str(recording_root.resolve())
    runtime.calibration_recording_deadline = time.monotonic() + 3.0
    config.window["calibration_status"].update("CLOCK ACTIVE — RECORDING IN 3 SECONDS")


def _service_calibration(config, runtime, send_cam):
    process = runtime.calibration_clock_process
    if process is not None and not process.is_alive():
        process.join(timeout=0.1)
        runtime.calibration_clock_process = None
        runtime.calibration_clock_stop_event = None
        runtime.calibration_recording_deadline = None
        runtime.calibration_recording_root = None
        if config.calibration_recording or runtime.calibration_recording_folder:
            send_cam.send(("record_stop", None))
        config.change_calibration_clock(False)

    deadline = runtime.calibration_recording_deadline
    if deadline is None or time.monotonic() < deadline:
        return
    runtime.calibration_recording_deadline = None
    if not (config.calibration_camera and config.connected_cam):
        runtime.calibration_recording_root = None
        config.window["calibration_status"].update(
            "CLOCK ACTIVE — CAMERA 4 CLOSED BEFORE RECORDING"
        )
        return

    root = Path(runtime.calibration_recording_root or "").expanduser()
    runtime.calibration_recording_root = None
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        folder = root / f"calibration_camera_4_{timestamp}"
        folder.mkdir(parents=False, exist_ok=False)
    except OSError as error:
        config.show_calibration_error(f"Could not create calibration recording: {error}")
        return

    runtime.calibration_recording_folder = str(folder)
    send_cam.send((
        "record_start",
        {"folders": {4: str(folder)}, "calibration": True},
    ))


def _maybe_start_snapshot_playback(config, runtime, send_snapshot_playback):
    if (
        runtime.pending_snapshot_playback
        and not config.recording
        and not config.recording_pending
        and not config.connected_radar
        and not config.connected_cam
        and not config.snapshot_playback
    ):
        payload = runtime.pending_snapshot_playback
        runtime.pending_snapshot_playback = None
        send_snapshot_playback.send(("snapshot_playback_start", payload))


def _disconnect_live_for_snapshot_playback(
    config, runtime, send_radar, send_cam, send_snapshot_playback
):
    if config.connected_cam:
        send_cam.send(("conn_cam", None))
    if config.connected_radar:
        send_radar.send(("conn_radar", None))
    _maybe_start_snapshot_playback(config, runtime, send_snapshot_playback)


def _request_snapshot_playback(
    values, config, runtime, send_radar, send_cam, send_snapshot_playback
):
    if config.snapshot_playback:
        send_snapshot_playback.send(("snapshot_playback_stop", None))
        return

    folder = Path(values.get("snapshot_playback_folder", "")).expanduser()
    if not folder.is_dir() or not (folder / "recording.json").is_file():
        config.show_snapshot_playback_error(
            "Select a recording folder containing recording.json and camera images"
        )
        return
    try:
        width, height = _resolution(values)
    except ValueError as error:
        config.show_snapshot_playback_error(str(error))
        return

    runtime.pending_snapshot_playback = {
        "folder": str(folder.resolve()),
        "snapshot_folder": str(Path(values.get("snapshot_folder", "")).expanduser().resolve()),
        "width": width,
        "height": height,
    }
    config.set_snapshot_playback_pending()
    if config.recording or config.recording_pending:
        base._request_recording_stop(config, runtime, send_cam)
    else:
        _disconnect_live_for_snapshot_playback(
            config, runtime, send_radar, send_cam, send_snapshot_playback
        )


def _handle_gui_event(
    event, values, config, runtime,
    send_radar, send_cam, send_gps, send_playback, send_snapshot_playback,
    shutdown_event,
):
    if event == "snapshot_playback_toggle":
        _request_snapshot_playback(
            values, config, runtime, send_radar, send_cam, send_snapshot_playback
        )
        return
    if event == "snapshot_playback_pause":
        send_snapshot_playback.send(("snapshot_playback_pause", None))
        return
    if event == "snapshot_playback_previous":
        send_snapshot_playback.send(("snapshot_playback_previous", None))
        return
    if event == "snapshot_playback_next":
        send_snapshot_playback.send(("snapshot_playback_next", None))
        return
    if event == "snapshot_playback_snapshot":
        folder = Path(values.get("snapshot_folder", "")).expanduser()
        if not folder.is_dir():
            config.show_snapshot_playback_snapshot_error(
                "Select an existing snapshot destination folder"
            )
            return
        send_snapshot_playback.send((
            "snapshot_playback_snapshot",
            {"folder": str(folder.resolve())},
        ))
        return
    if event == "playback_resolution_apply":
        try:
            width, height = _resolution(values)
        except ValueError as error:
            sg.popup_error(str(error), title="Playback resolution error")
            return
        payload = {"width": width, "height": height}
        send_cam.send(("playback_resolution", payload))
        send_playback.send(("playback_resolution", payload))
        send_snapshot_playback.send(("playback_resolution", payload))
        config.change_playback_resolution(width, height)
        return
    if event == "point_cutoff_apply":
        try:
            cutoff = _point_cutoff(values)
        except ValueError as error:
            sg.popup_error(str(error), title="Point cutoff error")
            return
        payload = {"distance": cutoff}
        send_radar.send(("point_cutoff", payload))
        send_playback.send(("point_cutoff", payload))
        send_snapshot_playback.send(("point_cutoff", payload))
        config.change_point_cutoff(cutoff)
        return
    if event == "calibration_latency_apply":
        try:
            pipeline_latency_ms, adjustment_ms = _camera_latency_settings(values)
        except ValueError as error:
            config.show_calibration_error(str(error))
            return
        payload = {
            "pipeline_latency_ms": pipeline_latency_ms,
            "latency_adjustment_ms": adjustment_ms,
        }
        send_cam.send(("camera_latency_settings", payload))
        send_radar.send(("camera_latency_adjustment", payload))
        send_snapshot_playback.send(("camera_latency_adjustment", payload))
        return
    if event == "calibration_camera_toggle":
        _request_calibration_camera(
            values,
            config,
            runtime,
            send_radar,
            send_cam,
            send_playback,
            send_snapshot_playback,
        )
        return
    if event == "recording_interval_apply":
        try:
            interval_ms = _camera_recording_interval(values)
        except ValueError as error:
            config.show_calibration_error(str(error))
            return
        send_cam.send((
            "camera_recording_interval",
            {"interval_ms": interval_ms},
        ))
        return
    if event == "calibration_clock_start":
        _start_calibration_clock(values, config, runtime)
        return

    base._handle_gui_event(
        event, values, config, runtime,
        send_radar, send_cam, send_gps, send_playback, shutdown_event,
    )
    if isinstance(event, str) and event.startswith("filter"):
        send_snapshot_playback.send((event, values))


def _apply_status_message(
    message, payload, config, runtime,
    send_radar, send_cam, send_playback, send_snapshot_playback,
):
    if message == "snapshot_playback_state":
        config.change_snapshot_playback(payload)
        _maybe_open_calibration_camera(config, runtime, send_cam)
        return
    if message == "snapshot_playback_progress":
        config.change_snapshot_playback_progress(payload)
        return
    if message == "snapshot_playback_error":
        runtime.pending_snapshot_playback = None
        config.show_snapshot_playback_error(payload)
        return
    if message == "snapshot_playback_snapshot_saved":
        config.show_snapshot_playback_snapshot_saved(payload)
        return
    if message == "snapshot_playback_snapshot_error":
        config.show_snapshot_playback_snapshot_error(payload)
        return
    if message == "playback_error" and isinstance(payload, dict):
        payload = payload.get("message", "Playback failed")
    if message == "calibration_camera_state":
        config.change_calibration_camera(payload.get("active"))
        if not payload.get("active"):
            runtime.calibration_recording_deadline = None
            runtime.calibration_recording_root = None
        return
    if message == "calibration_recording_state":
        state = dict(payload)
        state["folder"] = runtime.calibration_recording_folder or ""
        config.change_calibration_recording(state)
        if not state.get("active"):
            runtime.calibration_recording_folder = None
        return
    if message == "camera_latency_state":
        config.change_calibration_latencies(
            payload["pipeline_latency_ms"],
            payload["latency_adjustment_ms"],
        )
        return
    if message == "camera_latency_error":
        config.show_calibration_error(payload)
        return
    if message == "camera_recording_interval_state":
        config.change_recording_interval(payload["interval_ms"])
        return
    if message == "camera_recording_interval_error":
        config.show_calibration_error(payload)
        return
    if message == "camera_snapshot" and payload.get("calibration"):
        return

    base._apply_status_message(
        message, payload, config, runtime,
        send_radar, send_cam, send_playback,
    )

    if message in ("change_radar", "change_cam"):
        _maybe_start_snapshot_playback(config, runtime, send_snapshot_playback)
    elif message == "recording_state" and not payload.get("active"):
        if runtime.pending_snapshot_playback:
            _disconnect_live_for_snapshot_playback(
                config, runtime, send_radar, send_cam, send_snapshot_playback
            )
    if message in ("change_radar", "recording_state", "playback_state"):
        _maybe_open_calibration_camera(config, runtime, send_cam)


def _drain_status_queue(
    all_queue, config, runtime,
    send_radar, send_cam, send_playback, send_snapshot_playback,
):
    while True:
        try:
            message, payload = all_queue.get_nowait()
        except Empty:
            return
        _apply_status_message(
            message, payload, config, runtime,
            send_radar, send_cam, send_playback, send_snapshot_playback,
        )


def _run_event_loop(
    config, all_queue, runtime,
    send_radar, send_cam, send_gps, send_playback, send_snapshot_playback,
    shutdown_event,
):
    while not shutdown_event.is_set():
        event, values = config.read()
        _handle_gui_event(
            event, values, config, runtime,
            send_radar, send_cam, send_gps, send_playback, send_snapshot_playback,
            shutdown_event,
        )
        _drain_status_queue(
            all_queue, config, runtime,
            send_radar, send_cam, send_playback, send_snapshot_playback,
        )
        _service_calibration(config, runtime, send_cam)


def _stop_calibration_clock(runtime):
    process = runtime.calibration_clock_process
    if process is None:
        return
    if runtime.calibration_clock_stop_event is not None:
        runtime.calibration_clock_stop_event.set()
    process.join(timeout=2.0)
    if process.is_alive():
        process.terminate()
        process.join(timeout=1.0)


def main():
    process_context = get_context("spawn")
    shutdown_event = process_context.Event()

    def signal_handler(_sig, _frame):
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    sg.set_options(font=("Helvetica", 12))
    all_queue = process_context.Queue(128)

    receive_radar, send_radar = process_context.Pipe()
    receive_cam, send_cam = process_context.Pipe()
    receive_gps, send_gps = process_context.Pipe()
    receive_playback, send_playback = process_context.Pipe()
    receive_snapshot_playback, send_snapshot_playback = process_context.Pipe()

    config = Configurations()
    _, values = config.read()
    runtime = RuntimeState()
    runtime.process_context = process_context

    processes = [
        process_context.Process(
            target=create_connection_communication,
            args=(values, receive_radar, all_queue, shutdown_event),
        ),
        process_context.Process(
            target=gstreamer_main,
            args=(receive_cam, all_queue, shutdown_event),
        ),
        process_context.Process(
            target=gps_main,
            args=(receive_gps, all_queue, shutdown_event),
        ),
        process_context.Process(
            target=playback_main,
            args=(receive_playback, all_queue, shutdown_event, values),
        ),
        process_context.Process(
            target=snapshot_playback_main,
            args=(receive_snapshot_playback, all_queue, shutdown_event, values),
        ),
    ]
    for process in processes:
        process.start()

    try:
        _run_event_loop(
            config, all_queue, runtime,
            send_radar, send_cam, send_gps, send_playback, send_snapshot_playback,
            shutdown_event,
        )
    finally:
        _stop_calibration_clock(runtime)
        base._shutdown(
            processes,
            (send_radar, send_cam, send_gps, send_playback, send_snapshot_playback),
            config,
            shutdown_event,
        )


if __name__ == "__main__":
    main()
