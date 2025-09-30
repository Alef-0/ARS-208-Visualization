from connection.connection_packages import read_201_radar_state as r201
from connection.connection_packages import read_701_cluster_list as r701
from connection.connection_packages import read_702_quality_info as r702
from connection.connection_packages import create_200_radar_configuration as c200
from connection.connection_packages import Clusters_messages

from connection.connection_communication import Can_Connection, can_data
from graph.graph_filter import Filter_graph
from graph.graph_draw import Graph_radar

from multiprocessing.connection import Connection
from multiprocess.synchronize import Event
from multiprocessing import Process, Manager



def threat_201_message(channel, bytes):
    MaxDistanceCfg, RadarPowerCfg, OutputTypeCfg, RCS_Treshold, SendQualityCfg, _ = r201(bytes)
    
    distance = MaxDistanceCfg * 2
    radar = ["STANDARD", "-3db TX", "-6db TX", "-9db TX"][RadarPowerCfg]
    output = ["None", "Objects", "Clusters"][OutputTypeCfg]
    rcs = ["Standard", "High Sensitivity"][RCS_Treshold]
    quality = ["No", "Ok"][SendQualityCfg]

    # Mudar para o dicionário real

    # self.window[f'DISTANCE_{radar}'].update(values[0])
    # self.window[f'RPW_{radar}'].update(values[1])
    # self.window[f'OUT_{radar}'].update(values[2])
    # self.window[f'RCS_{radar}'].update(values[3])
    # self.window[f'EXT_{radar}'].update(values[4])


def create_connection_communication(values : dict, pipe : Connection):
    with Manager() as manager:
        shared_dict = manager.dict()


    radar_choice = [int(x[-1]) for x in values.keys() if (x.startswith("visu_radar_choose") and values[x] == True)][0] # Só deveria haver 1
    
    connection = Can_Connection()
    message_collection = Clusters_messages()
    graph = Graph_radar()
    filter = Filter_graph(values)

    while True:
        poll = pipe.poll(0)
        if poll: 
            event = pipe.recv()
            match event:
                case "connection": connection.change_connection()
                case _: pass
        
        # Working normally
        if ( not connection.connected): continue
        connection.read_chunk()
        while (connection.can_create_can()):
            message  = connection.create_package()
        # Tratar da Conexão
            if message.canId == 0x201: threat_201_message(message.canChannel, message.canData)
            if message.canChannel != radar_choice: continue 
            match message.canId:
                # case 0x201: threat_201_message(message.canChannel, message.canData, config) # Deactivated for filtering id = 2        
                case 0x600: 
                    # print(message_collection.dyn, len(message_collection.dyn))
                    x, y, colors = filter.filter_points(message_collection)
                    graph.show_points(x,y, colors) 
                    message_collection.clear()
                    # if teste_id_701 and teste_id_702: print(max(teste_id_701), max(teste_id_702))
                    # teste_id_701.clear(); teste_id_702.clear()
                case 0x701:
                    things = r701(message.canData); #teste_id_701.append(things[0])
                    message_collection.fill_701(things)
                case 0x702:
                    things = r702(message.canData); #teste_id_702.append(things[0])
                    message_collection.fill_702(things)