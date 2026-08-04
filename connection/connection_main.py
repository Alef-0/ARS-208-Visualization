from datetime import datetime
import queue
import signal

import cv2 as cv

from connection.connection_communication import Can_Connection
from connection.connection_packages import (
    Clusters_messages,
    Objects_messages,
    create_200_radar_configuration as c200,
    read_201_radar_state_extended as r201,
    read_60a_object_status as r60a,
    read_60b_object_general as r60b,
    read_60c_object_quality as r60c,
    read_60d_object_extended as r60d,
    read_60e_object_warning as r60e,
    read_701_cluster_list as r701,
    read_702_quality_info as r702,
)
from graph.graph_draw import Graph_radar
from graph.graph_filter import Filter_graph
from recording import RadarRecordingSession

RADAR_CHANNELS = (1, 2, 3)
STATUS_FRAME_TYPES = {0x600: "cluster", 0x60A: "object"}


def _put_status(pool, message, payload, *, critical=False):
    try:
        if critical:
            pool.put((message, payload), timeout=0.2)
        else:
            pool.put_nowait((message, payload))
    except queue.Full:
        pass


def _configuration_label(options, index):
    try:
        return options[index]
    except (IndexError, TypeError):
        return f"Unknown ({index})"


def treat_201_message(channel, payload, pool):
    (
        distance,
        radar_power,
        output_type,
        rcs_threshold,
        send_quality,
        send_ext_info,
        ctrl_relay,
        _,
    ) = r201(payload)
    values = {
        f"DISTANCE_{channel}": distance * 2,
        f"RPW_{channel}": _configuration_label(
            ("STANDARD", "-3db TX", "-6db TX", "-9db TX"), radar_power
        ),
        f"OUT_{channel}": _configuration_label(
            ("None", "Objects", "Clusters"), output_type
        ),
        f"RCS_{channel}": _configuration_label(
            ("Standard", "High Sensitivity"), rcs_threshold
        ),
        f"QUALITY_{channel}": ["Inactive", "Active"][send_quality],
        f"EXT_{channel}": ["Inactive", "Active"][send_ext_info],
        f"RELAY_{channel}": ["Inactive", "Active"][ctrl_relay],
    }
    _put_status(pool, "message_201", values)


def send_configuration_message(values, connection, save_nvm):
    data = c200(
        values["CHECK_DISTANCE"], int(values["DISTANCE"] / 2),
        values["CHECK_RPW"], ["STANDARD", "-3dB Tx gain", "-6dB Tx gain", "-9dB Tx gain"].index(values["RPW"]),
        values["CHECK_OUT"], ["NONE", "OBJECT", "CLUSTERS"].index(values["OUT"]),
        values["CHECK_RCS"], ["STANDARD", "HIGH SENSITIVITY"].index(values["RCS"]),
        True, int(bool(values["CHECK_QUALITY"])),
        save_nvm,
        ok_ext=True, ext_info=int(bool(values["CHECK_EXTENDED"])),
        ok_relay=True, ctrl_relay=int(bool(values["CHECK_RELAY"])),
    )
    for channel in RADAR_CHANNELS:
        if values.get(f"send_{channel}") or values.get("send_all"):
            message = connection.packet_struct.pack(8, 0, 0x200, 0, data.to_bytes(8, "big"), channel)
            connection.send_message(message)


def _stop_recording(recording, pool, recording_ready):
    recording_ready.clear()
    if not recording.active:
        return
    try:
        counts = recording.stop()
        _put_status(
            pool,
            "recording_state",
            {"active": False, "counts": counts},
            critical=True,
        )
    except Exception as error:
        _put_status(pool, "recording_error", str(error), critical=True)
        _put_status(
            pool,
            "recording_state",
            {"active": False, "counts": {}},
            critical=True,
        )


def create_connection_communication(initial_values, pipe, pool, shutdown_event):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, lambda *_: shutdown_event.set())
    radar_choice = next(
        int(key.rsplit("_", 1)[1])
        for key, selected in initial_values.items()
        if isinstance(key, str) and key.startswith("choose_") and selected
    )

    connection = Can_Connection()
    cluster_messages = {channel: Clusters_messages() for channel in RADAR_CHANNELS}
    object_messages = {channel: Objects_messages() for channel in RADAR_CHANNELS}
    frame_types: dict[int, str] = {}
    frame_timestamps: dict[int, datetime] = {}
    recording_ready: dict[int, bool] = {}
    received_message_ids: set[int] = set()
    graph = Graph_radar()
    filters = Filter_graph(initial_values)

    def report_progress(channel, count):
        _put_status(pool, "recording_progress", {"channel": channel, "count": count})

    def report_received_message(can_id):
        if can_id in received_message_ids:
            return
        received_message_ids.add(can_id)
        _put_status(pool, "received_messages", tuple(sorted(received_message_ids)))

    recording = RadarRecordingSession(report_progress)

    def frame_messages(channel, frame_type):
        return cluster_messages[channel] if frame_type == "cluster" else object_messages[channel]

    def begin_frame(channel, frame_type, recorded_at, status=None):
        previous_type = frame_types.get(channel)
        if previous_type is not None:
            previous_messages = frame_messages(channel, previous_type)
            if channel == radar_choice:
                if previous_type == "cluster":
                    x, y, colors = filters.filter_points(previous_messages)
                else:
                    x, y, colors = filters.filter_objects(previous_messages)
                graph.show_points(x, y, colors)
            if recording_ready.get(channel, False):
                recording.submit(
                    channel,
                    previous_messages.snapshot(),
                    frame_timestamps[channel],
                    frame_type=previous_type,
                )
            previous_messages.clear()

        current_messages = frame_messages(channel, frame_type)
        current_messages.clear()
        if frame_type == "object":
            current_messages.fill_60a(status)
        frame_types[channel] = frame_type
        frame_timestamps[channel] = recorded_at
        if channel in recording.channels:
            recording_ready[channel] = True

    try:
        while not shutdown_event.is_set():
            try:
                while pipe.poll():
                    event, values = pipe.recv()
                    if event == "STOP":
                        shutdown_event.set()
                    elif event == "conn_radar":
                        connection.change_connection()
                        received_message_ids.clear()
                        _put_status(pool, "received_messages", ())
                        _put_status(pool, "change_radar", connection.connected, critical=True)
                        cv.destroyAllWindows()
                        if not connection.connected:
                            _stop_recording(recording, pool, recording_ready)
                    elif event == "Send" and connection.connected:
                        send_configuration_message(values, connection, False)
                    elif event == "save_nvm" and connection.connected:
                        send_configuration_message(values, connection, True)
                    elif event == "choose":
                        radar_choice = values
                    elif event == "record_start":
                        if not connection.connected:
                            _put_status(
                                pool,
                                "recording_error",
                                "Connect the radar before starting a recording",
                                critical=True,
                            )
                            continue
                        try:
                            folders = recording.start(values["folder"], values["channels"])
                            recording_ready.clear()
                            recording_ready.update({channel: False for channel in recording.channels})
                            _put_status(
                                pool,
                                "recording_state",
                                {"active": True, "folders": folders, "counts": {}},
                                critical=True,
                            )
                        except Exception as error:
                            _put_status(pool, "recording_error", str(error), critical=True)
                    elif event == "record_camera":
                        captured_at = values.get("captured_at", "")
                        for channel, filename in values.get("files", {}).items():
                            recording.add_camera_snapshot(
                                int(channel),
                                filename,
                                captured_at,
                            )
                    elif event == "record_stop":
                        _stop_recording(recording, pool, recording_ready)
                    elif isinstance(event, str) and event.startswith("filter"):
                        filters.update_values(event, values)
            except (EOFError, OSError):
                shutdown_event.set()

            recording_error = recording.poll_error()
            if recording_error is not None:
                _stop_recording(recording, pool, recording_ready)

            if not connection.connected:
                shutdown_event.wait(0.01)
                continue

            connection.read_chunk()
            while connection.can_create_can():
                message = connection.create_package()
                channel = message.canChannel
                if channel not in cluster_messages:
                    continue

                report_received_message(message.canId)
                if message.canId == 0x201:
                    treat_201_message(channel, message.canData, pool)

                frame_type = STATUS_FRAME_TYPES.get(message.canId)
                if frame_type is not None:
                    status = r60a(message.canData) if frame_type == "object" else None
                    begin_frame(
                        channel,
                        frame_type,
                        datetime.now().astimezone(),
                        status,
                    )
                elif frame_types.get(channel) == "cluster":
                    if message.canId == 0x701:
                        cluster_messages[channel].fill_701(r701(message.canData))
                    elif message.canId == 0x702:
                        cluster_messages[channel].fill_702(r702(message.canData))
                elif frame_types.get(channel) == "object":
                    if message.canId == 0x60B:
                        object_messages[channel].fill_60b(r60b(message.canData))
                    elif message.canId == 0x60C:
                        object_messages[channel].fill_60c(r60c(message.canData))
                    elif message.canId == 0x60D:
                        object_messages[channel].fill_60d(r60d(message.canData))
                    elif message.canId == 0x60E:
                        object_messages[channel].fill_60e(r60e(message.canData))
    finally:
        _stop_recording(recording, pool, recording_ready)
        if connection.sock:
            connection.sock.close()
        cv.destroyAllWindows()
