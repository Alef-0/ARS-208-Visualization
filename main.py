from dataclasses import dataclass
from pathlib import Path
import signal
from multiprocessing import get_context
from queue import Empty

import sitecustomize
import FreeSimpleGUI as sg

import MAIN_BASE as base
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


def _resolution(values):
    try:
        width = int(str(values.get("playback_width", "1280")).strip())
        height = int(str(values.get("playback_height", "720")).strip())
    except ValueError as error:
        raise ValueError("Playback width and height must be integers") from error
    if width <= 0 or height <= 0:
        raise ValueError("Playback width and height must be positive")
    return width, height


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
        send_playback.send(("playback_resolution", payload))
        send_snapshot_playback.send(("playback_resolution", payload))
        config.change_playback_resolution(width, height)
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
        base._shutdown(
            processes,
            (send_radar, send_cam, send_gps, send_playback, send_snapshot_playback),
            config,
            shutdown_event,
        )


if __name__ == "__main__":
    main()
