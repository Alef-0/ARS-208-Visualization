from menu_configurations import Configurations
from connection.connection_communication import Can_Connection, can_data
import FreeSimpleGUI as sg
from time import sleep
import re

from connection.connection_packages import read_201_radar_state as r201
from connection.connection_packages import read_701_cluster_list as r701
from connection.connection_packages import read_702_quality_info as r702
from connection.connection_packages import create_200_radar_configuration as c200
from connection.connection_packages import Clusters_messages

from graph.graph_filter import Filter_graph
from graph.graph_draw import Graph_radar

from camera.camera_main import camera_start

from multiprocessing import Process, Queue, Pipe
from multiprocessing.connection import Connection

from connection.connection_main import create_connection_communication

def send_configuration_message(dic : sg.Window, connection : Can_Connection, save_volatile):
    values = []
    values.append(dic['CHECK_DISTANCE'])
    values.append(int(dic["DISTANCE"] / 2) )
    values.append(dic["CHECK_RPW"])
    values.append(["STANDARD", "-3dB Tx gain", "-6dB Tx gain", "-9dB Tx gain"].index(dic['RPW']))
    values.append(dic["CHECK_OUT"])
    values.append(["NONE", "OBJECT", "CLUSTERS"].index(dic["OUT"]))
    values.append(dic["CHECK_RCS"])
    values.append(["STANDARD", "HIGH SENSITIVITY"].index(dic['RCS']))
    values.append(1)
    values.append(dic['CHECK_QUALITY'])

    data = c200(*values, save_volatile)
    print("Enviando algo")
    # Long way
    if dic['send_1'] or dic['send_all']:
        message = connection.packet_struct.pack(8, 0, 0x200, 0, data.to_bytes(8), 1)
        connection.send_message(message)
    if dic['send_2'] or dic['send_all']:
        message = connection.packet_struct.pack(8, 0, 0x200, 0, data.to_bytes(8), 2)
        connection.send_message(message)
    if dic['send_3'] or dic['send_all']:
        message = connection.packet_struct.pack(8, 0, 0x200, 0, data.to_bytes(8), 3)
        connection.send_message(message)

def threat_201_message(channel, bytes, config : Configurations):
    MaxDistanceCfg, RadarPowerCfg, OutputTypeCfg, RCS_Treshold, SendQualityCfg, _ = r201(bytes)
    
    distance = MaxDistanceCfg * 2
    radar = ["STANDARD", "-3db TX", "-6db TX", "-9db TX"][RadarPowerCfg]
    output = ["None", "Objects", "Clusters"][OutputTypeCfg]
    rcs = ["Standard", "High Sensitivity"][RCS_Treshold]
    quality = ["No", "Ok"][SendQualityCfg]

    config.change_radar(channel, [distance, radar, output, rcs, quality])

def check_popup():
    layout = [
        [sg.Text("Digite [Alohomora] para confirmar salvar permanentemente nos radares!", justification="center")],
        [sg.Input("", key="passwd", expand_x=True, justification="center")],
        [sg.Push(),sg.Ok(), sg.Cancel(), sg.Push()]
    ]
    window = sg.Window("PASSWORD", layout)
    result = False
    while True:
        events, values = window.read()
        match events:
            case sg.WIN_CLOSED: return False
            case "Ok": result = (values['passwd'] == "Alohomora"); break
            case "Cancel": break
    
    window.close()
    return result
  
if __name__ == "__main__":
    font = ("Helvetica", 12) 
    sg.set_options(font=font)

    all_queue = Queue(5)
    receive_conn, send_conn = Pipe()
    config = Configurations()
    event, values = config.read()
    
    conn_process = Process(target=create_connection_communication, args=(values, receive_conn, all_queue), daemon=True)
    conn_process.start()

    # camera_process = Process(target=camera_start, args=(), daemon=True)
    # camera_process.start()

    while True:
        try: event, values = config.read()
        except KeyboardInterrupt: break
        finally: 
            if event == sg.WINDOW_CLOSED: break
        
        # Parte do Menu
        match event:
            case "connection": 
                send_conn.send((event, None))
            case "Send":
                if config.connected: send_conn.send((event, values))
                config.window["save_nvm"].update(button_color=("black", "white"))
            case s if re.match(r"^filter", s):
                send_conn.send((event, values))
            case sg.TIMEOUT_EVENT: pass
            case "save_nvm": 
                if config.connected:
                    result = check_popup()
                    if result:  config.window["save_nvm"].update(button_color=("white", "green"))
                    else:       config.window["save_nvm"].update(button_color=("white", "red"))
                    send_conn.send((event, values))
            case s if re.match(r"^visu_radar_choose", s):
                radar_choice = int(event[-1])
                send_conn.send((event, radar_choice))
            case _: print(event)        
        if event != sg.TIMEOUT_EVENT: print(event)

        # See events
        if not all_queue.empty():
            message, values = all_queue.get()
            # print("VALUES ON POOL:", message, values)
            match message:
                case "message_201": config.change_radar(values)
                case "change_connection": config.change_connection(values)