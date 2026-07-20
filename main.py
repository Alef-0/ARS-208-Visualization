import re
import signal
from multiprocessing import Event, Pipe, Process, Queue
from queue import Empty

import FreeSimpleGUI as sg

from camera.camera_gstreamer import gstreamer_main
from connection.connection_main import create_connection_communication
from gps.gps_connection import main as gps_main
from menu_configurations import Configurations


def check_popup():
    layout = [
        [sg.Text("Digite [Alohomora] para confirmar salvar permanentemente nos radares!", justification="center")],
        [sg.Input("", key="passwd", expand_x=True, justification="center")],
        [sg.Push(), sg.Ok(), sg.Cancel(), sg.Push()],
    ]
    window = sg.Window("PASSWORD", layout)
    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, "Cancel"):  return False
            if event == "Ok":                       return values["passwd"] == "Alohomora"
    finally:
        window.close()

def _join_processes(processes, timeout=3.0):
    # Really overkill?
    for process in processes: process.join(timeout)
    for process in processes: 
        if process.is_alive(): process.terminate()
    for process in processes: process.join()

def _handle_gui_event(event, values, config, send_radar, send_cam, send_gps, shutdown_event):
    if event == sg.WINDOW_CLOSED: shutdown_event.set(); return

    match event:
        case "Send":
            if config.connected_radar: send_radar.send((event, values))
            config.window["save_nvm"].update(button_color=("black", "white"))
        case "save_nvm":
            if config.connected_radar and check_popup():
                config.window["save_nvm"].update(button_color=("white", "green"))
                send_radar.send((event, values))
        case key if isinstance(key, str) and key.startswith("filter"):
            send_radar.send((event, values))
        case key if isinstance(key, str) and re.match(r"^choose_", key):
            choice = int(event.rsplit("_", 1)[1])
            send_radar.send(("choose", choice))
            send_cam.send(("choose", choice))
        case key if isinstance(key, str) and re.match(r"^conn_", key):
            target = {
                "conn_radar": send_radar,
                "conn_cam": send_cam,
                "conn_gps": send_gps,
            }.get(event)
            if target: target.send((event, None))
        case "gps_maps": send_gps.send((event, None))
        case "DISTANCE": config.window["SLIDER_VAL"].update(int(values["DISTANCE"]))

def _apply_status_message(message, payload, config):
    match message:
        case "message_201":     config.change_radar(payload)
        case "change_radar":    config.change_connection_radar(payload)
        case "change_cam":      config.change_connection_cam(payload)
        case "gps_text":        config.window[message].update(payload)
        case "conn_gps":        config.change_connection_gps(payload)

def _drain_status_queue(all_queue, config):
    while True:
        try: message, payload = all_queue.get_nowait()
        except Empty: return
        _apply_status_message(message, payload, config)

def _run_event_loop(config, all_queue, send_radar, send_cam, send_gps, shutdown_event):
    while not shutdown_event.is_set():
        event, values = config.read()
        _handle_gui_event( event, values, config, send_radar, send_cam, send_gps, shutdown_event,)
        _drain_status_queue(all_queue, config)

def _shutdown(processes, pipes, config, shutdown_event):
    shutdown_event.set()
    for pipe in pipes:
        try:
            pipe.send(("STOP", None))
            pipe.close()
        except (BrokenPipeError, EOFError, OSError):
            pass
    _join_processes(processes)
    config.window.close()

def main():
    shutdown_event = Event()

    def signal_handler(_sig, _frame):
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    sg.set_options(font=("Helvetica", 12))
    all_queue = Queue(5)

    receive_radar, send_radar = Pipe()
    receive_cam, send_cam = Pipe()
    receive_gps, send_gps = Pipe()

    config = Configurations()
    _, values = config.read()

    processes = [
        Process(target=create_connection_communication, args=(values, receive_radar, all_queue, shutdown_event)),
        Process(target=gstreamer_main, args=(receive_cam, all_queue, shutdown_event)),
        Process(target=gps_main, args=(receive_gps, all_queue, shutdown_event)),
    ]
    for process in processes:
        process.start()

    try:
        _run_event_loop( config, all_queue, send_radar, send_cam, send_gps, shutdown_event,)
    finally:
        _shutdown(processes, (send_radar, send_cam, send_gps), config, shutdown_event,)

if __name__ == "__main__":
    main()
