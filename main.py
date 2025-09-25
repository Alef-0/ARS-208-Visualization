from menu_configurations import Configurations
from connection_communication import Can_Connection, can_data
import FreeSimpleGUI as sg
from time import sleep
import re

from connection_packages import read_201_radar_state as r201
from connection_packages import read_701_cluster_list as r701
from connection_packages import read_702_quality_info as r702
from connection_packages import create_200_radar_configuration as c200
from connection_packages import Clusters_messages

from graph_filter import Filter_graph
from graph_draw import Graph_radar

def send_configuration_message(dic : sg.Window, connection : Can_Connection):
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

    data = c200(*values)
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
    
if __name__ == "__main__":
    font = ("Helvetica", 12) 
    sg.set_options(font=font)

    config = Configurations()
    connection = Can_Connection()
    event, values = config.read()
    filter = Filter_graph(values)
    message_collection = Clusters_messages()
    graph = Graph_radar()
    
    # teste_id_701, teste_id_702 = [], []

    while True:
        event, values = config.read()
        if      event == sg.WINDOW_CLOSED: break

        # Parte do Menu
        match event:
            case "connection": 
                connection.change_connection()
                config.change_connection(connection.connected)
            case "Send":
                if config.connected: send_configuration_message(values, connection)
            case s if re.match(r"^filter", s):
                filter.update_values(event, values)
            case sg.TIMEOUT_EVENT: pass
            case _: pass
        
        # Parte da conexão
        if (not connection.connected): continue
        # print("CHEGOU AQUI")
        connection.read_chunk()
        while (connection.can_create_can()):
            message  = connection.create_package()
        # Tratar da Conexão
            if message.canId == 0x201: threat_201_message(message.canChannel, message.canData, config)
            if message.canChannel != 2: continue # Filtrar mensagens do filtro 2
            match message.canId:
                # case 0x201: threat_201_message(message.canChannel, message.canData, config) # Deactivated for filtering id = 2        
                case 0x600: 
                    # print(message_collection.dyn, len(message_collection.dyn))
                    x, y, colors = filter.filter_points(message_collection)
                    graph.show_points(x,y, colors)
                    
                    message_collection.clear()
                    print("----------------------")
                    # if teste_id_701 and teste_id_702: print(max(teste_id_701), max(teste_id_702))
                    # teste_id_701.clear(); teste_id_702.clear()
                case 0x701:
                    things = r701(message.canData); #teste_id_701.append(things[0])
                    message_collection.fill_701(things)
                case 0x702:
                    things = r702(message.canData); #teste_id_702.append(things[0])
                    message_collection.fill_702(things)