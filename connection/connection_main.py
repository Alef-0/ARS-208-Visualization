import signal

import cv2 as cv

from connection.connection_communication import Can_Connection
from connection.connection_packages import (
    Clusters_messages,
    create_200_radar_configuration as c200,
    read_201_radar_state as r201,
    read_701_cluster_list as r701,
    read_702_quality_info as r702,
)
from graph.graph_draw import Graph_radar
from graph.graph_filter import Filter_graph


def treat_201_message(channel, payload, pool):
    distance, radar_power, output_type, rcs_threshold, send_quality, _ = r201(payload)
    values = {
        f"DISTANCE_{channel}": distance * 2,
        f"RPW_{channel}": ["STANDARD", "-3db TX", "-6db TX", "-9db TX"][radar_power],
        f"OUT_{channel}": ["None", "Objects", "Clusters"][output_type],
        f"RCS_{channel}": ["Standard", "High Sensitivity"][rcs_threshold],
        f"EXT_{channel}": ["No", "Ok"][send_quality],
    }
    pool.put(("message_201", values))


def send_configuration_message(values, connection, save_nvm):
    data = c200(
        values["CHECK_DISTANCE"], int(values["DISTANCE"] / 2),
        values["CHECK_RPW"], ["STANDARD", "-3dB Tx gain", "-6dB Tx gain", "-9dB Tx gain"].index(values["RPW"]),
        values["CHECK_OUT"], ["NONE", "OBJECT", "CLUSTERS"].index(values["OUT"]),
        values["CHECK_RCS"], ["STANDARD", "HIGH SENSITIVITY"].index(values["RCS"]),
        values["CHECK_QUALITY"], 1, save_nvm,
    )
    for channel in range(1, 4):
        if values.get(f"send_{channel}") or values.get("send_all"):
            message = connection.packet_struct.pack(8, 0, 0x200, 0, data.to_bytes(8, "big"), channel)
            connection.send_message(message)


def create_connection_communication(initial_values, pipe, pool, shutdown_event):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, lambda *_: shutdown_event.set())
    radar_choice = next(
        int(key.rsplit("_", 1)[1])
        for key, selected in initial_values.items()
        if isinstance(key, str) and key.startswith("choose_") and selected
    )

    connection = Can_Connection()
    messages = Clusters_messages()
    graph = Graph_radar()
    filters = Filter_graph(initial_values)

    try:
        while not shutdown_event.is_set():
            while pipe.poll():
                event, values = pipe.recv()
                if event == "STOP": shutdown_event.set()
                elif event == "conn_radar":
                    connection.change_connection()
                    pool.put(("change_radar", connection.connected))
                    cv.destroyAllWindows()
                elif event == "Send" and connection.connected:
                    send_configuration_message(values, connection, False)
                elif event == "save_nvm" and connection.connected:
                    send_configuration_message(values, connection, True)
                elif event == "choose":
                    radar_choice = values
                elif isinstance(event, str) and event.startswith("filter"):
                    filters.update_values(event, values)
            if not connection.connected:
                shutdown_event.wait(0.01)
                continue
            connection.read_chunk()
            while connection.can_create_can():
                message = connection.create_package()
                if message.canId == 0x201:
                    treat_201_message(message.canChannel, message.canData, pool)
                if message.canChannel != radar_choice:
                    continue
                if message.canId == 0x600:
                    x, y, colors = filters.filter_points(messages)
                    graph.show_points(x, y, colors)
                    messages.clear()
                elif message.canId == 0x701:
                    messages.fill_701(r701(message.canData))
                elif message.canId == 0x702:
                    messages.fill_702(r702(message.canData))
    finally:
        if connection.sock: connection.sock.close()
        cv.destroyAllWindows()
