def generate_code_colors():
    transformed = []
    COLORS_ORIGINAL = [
    "#FF0000", "#FF7B00", "#FFE600", "#00FF00",
    "#0000FF","#00FFFF", "#8400FF","#000000",
    ]
    for colors in COLORS_ORIGINAL:
        r = int(colors[1:3], 16)
        g = int(colors[3:5], 16)
        b = int(colors[5:7], 16)
        transformed.append((b,g,r))
    return transformed

import FreeSimpleGUI as sg

class Filter_graph:
    def __init__(self, values : dict):
        # Garantia que os filtros estão em ordem
        filter_keys = [key for key in list(values.keys()) 
                       if (type(key) == str and "filter" in key)]
        # Criar a ordem de tudo
        self.dyn_order = filter_keys[0:8]
        self.phd_order = filter_keys[8]
        self.ambig_order = filter_keys[9:13]
        self.cluster_order = filter_keys[13:]
        
        # Criar os valores para filtrar
        self.dyn = []; self.phd = None; self.ambig = []; self.cluster = []
        
        for keys in self.dyn_order:     self.dyn.append(values[keys])
        self.phd = values[self.phd_order]
        for keys in self.ambig_order:   self.ambig.append(values[keys])
        for keys in self.cluster_order: self.cluster.append(values[keys])

    def update_values(self, event : str, values : dict):
        # Ver qual o tipo
        if "dyn" in event: self.dyn[self.dyn_order.index(event)] = values[event]
        self.phd[event] = values[event]
        if "ambg" in event: self.ambig[self.ambig_order.index(event)] = values[event]
        if "inv" in event: self.cluster[self.cluster_order.index(event)] = values[event]
