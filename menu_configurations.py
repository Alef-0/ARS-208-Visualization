import FreeSimpleGUI as sg

class Configurations:
    POWER = ["STANDARD", "-3dB Tx gain", "-6dB Tx gain", "-9dB Tx gain"]
    OUTPUT = ["NONE", "OBJECT", "CLUSTERS"]
    RCS = ["STANDARD", "HIGH SENSITIVITY"]
    FONT = ("Helvetica", 12) 

    def __init__(self):
        # Tem que colocar as configurações primeiro
        sg.set_options(font=self.FONT)
        sg.theme("Default")
        
        # Criar divisões especiais
        self.create_radar_division()
        self.create_options()
        self.create_filter_list()
        
        self.layout = [
        [self.FRAME],
        [sg.Push(), self.options, sg.Push()],
        [self.filter]
        ]

        self.window = sg.Window("Configurations Menu", self.layout, finalize=True)
        self.centralize_combos()
        self.connected = False

    def centralize_combos(self):
        """Just Centralizing things messing with tkinter under the hood"""
        self.window["RPW"].Widget.configure(justify="center")
        self.window["OUT"].Widget.configure(justify="center")
        self.window["RCS"].Widget.configure(justify="center")

    def create_radar_division(self):
        """Criando Tabs para ficar mais organizado"""
        
        parts = []
        names = ["", "LEFT", "MIDDLE", "RIGHT"]
        for i in range(1,4):
            curr_tab = sg.Column([
                [sg.Text(f"{names[i]}_{i}", justification="center", expand_x=True)],
                [sg.Text("Distance", expand_x=True, justification="left"),    sg.Text("XXX", key=f"DISTANCE_{i}", expand_x=True, justification="right")],
                [sg.Text("Radar", expand_x=True, justification="left"),       sg.Text("XXX", key=f"RPW_{i}", expand_x=True, justification="right")],
                [sg.Text("Output", expand_x=True, justification="left"),      sg.Text("XXX", key=f"OUT_{i}", expand_x=True, justification="right")],
                [sg.Text("RCS", expand_x=True, justification="left"),         sg.Text("XXX", key=f"RCS_{i}", expand_x=True, justification="right")],
                [sg.Text("Ext Info", expand_x=True, justification="left"),    sg.Text("XXX", key=f"EXT_{i}", expand_x=True, justification="right")],
            ]
            )
            parts.extend([sg.Push(), curr_tab, sg.Push(), sg.VSep()])
        parts.pop()

        
        self.FRAME = [sg.Frame("Real Time Configurations", [parts], expand_x=True, title_location=sg.TITLE_LOCATION_TOP)]

    def create_filter_list(self):
        self.dynprop = sg.Column([
            [sg.Push(), 
                sg.Checkbox("Moving", default=True),                sg.Button("", button_color="#FF0000", disabled=True), 
                sg.Checkbox("Stationary", default=True),            sg.Button("", button_color="#FF7B00", disabled=True), 
                sg.Checkbox("Oncoming", default=True),              sg.Button("", button_color="#FFE600", disabled=True), 
                sg.Checkbox("Stationary Candidate",default=True),   sg.Button("", button_color="#00FF00", disabled=True), 
            sg.Push()],
            [sg.Push(), 
                sg.Checkbox("Unknown",default=True),                sg.Button("", button_color="#0000FF", disabled=True), 
                sg.Checkbox("Crossing Stationary", default=True),   sg.Button("", button_color="#00FFFF", disabled=True), 
                sg.Checkbox("Crossing Moving", default=True),       sg.Button("", button_color="#8400FF", disabled=True), 
                sg.Checkbox("Stopped", default=True),               sg.Button("", button_color="#000000", disabled=True), 
            sg.Push()],
        ], justification="center")
        
        self.slider_pdh = sg.Column([
            [sg.Push(), sg.Text("PDH Confidence Limit"), sg.Push()],
            [sg.Slider((0, 7),disable_number_display=True, default_value=1, orientation='h', tick_interval=1, expand_x=True)],
            [sg.Text("[0 = 00, 1 = 25, 2 = 50, 3 = 75, 4 = 90, 5 = 99...]", expand_x=True, justification="center")]
        ], justification="center")

        self.cluster_ambg = sg.Column([
            [sg.Push(), sg.Text("Ambiguitity State"), sg.Push()],
            [sg.Push(), sg.Checkbox("Ambiguous") , sg.Checkbox("Staggered Ramp"), sg.Push()],
            [sg.Push(), sg.Checkbox("Unambiguous", default=True), sg.Checkbox("Stationary Candidates", default=True) ,sg.Push()]
        ], justification="center")

        self.invalid_state = sg.Column([
            [sg.Text("Cluster State", justification="center", expand_x=True)],
            [sg.Push(), 
                sg.Checkbox("0x0", default=True) , 
                sg.Checkbox("0x1"), 
                sg.Checkbox("0x2"), 
                sg.Checkbox("0x3"), 
                sg.Checkbox("0x4", default=True) , 
                sg.Checkbox("0x5", disabled=True), 
                sg.Checkbox("0x6"), 
                sg.Checkbox("0x7"), 
                sg.Checkbox("0x8", default=True) , 
            sg.Push()],
            [sg.Push(), 
                sg.Checkbox("0x9", default=True), 
                sg.Checkbox("0xA", default=True), 
                sg.Checkbox("0xB", default=True), 
                sg.Checkbox("0xC", default=True) , 
                sg.Checkbox("0xD", disabled=True), 
                sg.Checkbox("0xE"), 
                sg.Checkbox("0xF", default=True),
                sg.Checkbox("0x10", default=True) , 
                sg.Checkbox("0x11", default=True),  
            sg.Push()],
        ], expand_x=True)

        self.filter = sg.Frame("Filters for points", [
                [self.dynprop], 
                [sg.HorizontalSeparator()], 
                [self.slider_pdh, sg.VerticalSeparator(), self.cluster_ambg],
                [sg.HorizontalSeparator()],
                [self.invalid_state]
            ], title_location=sg.TITLE_LOCATION_TOP, expand_x=True)

    def create_options(self):
        self.choices = sg.Frame("Radar", [[
            sg.Radio("1", "choose", key="send_1"), sg.Radio("2", "choose", key="send_2"), 
            sg.Radio("3", "choose", key="send_3"),  sg.Radio("all", "choose", key="send_all", default=True)
        ]], 
        title_location=sg.TITLE_LOCATION_LEFT)

        self.options = sg.Frame("Options", [
            [sg.Checkbox("Max Distance", expand_x=True, key="CHECK_DISTANCE",default=True),  sg.Push(), sg.Slider((196, 260), 100, orientation="h", resolution=1, key="DISTANCE", size=(40, 20)), sg.Push()],
            [
                sg.Checkbox("Radar Power", expand_x=True, key="CHECK_RPW",default=True),        sg.Push(), sg.Combo(self.POWER, self.POWER[0], font=self.FONT, size=15, key="RPW", readonly=True), sg.Push(),
                sg.VerticalSeparator(),
                sg.Checkbox("Output Power", expand_x=True, key="CHECK_OUT",default=True),       sg.Push(), sg.Combo(self.OUTPUT, self.OUTPUT[2], font=self.FONT, size=15, key="OUT", readonly=True), sg.Push(),
            ],
            [
                sg.Checkbox("RCS Treshold", expand_x=True, key="CHECK_RCS",default=True),  sg.Combo(self.RCS, self.RCS[0], font=self.FONT, size=16, key="RCS", readonly=True), sg.Push(),
                sg.VerticalSeparator(),
                sg.Push(), sg.Checkbox("Send Quality", expand_x=True, key="CHECK_QUALITY",default=True), sg.Push()
            ],   
                [sg.Button("Send"), self.choices,  sg.VerticalSeparator(), sg.Button("Open conn", key="connection", button_color=("white", "red"), size=(10)), sg.Text("DISCONNECTED", key="status")
            ],
        ], title_location=sg.TITLE_LOCATION_TOP, pad=(0,20))

    def read(self):
        return self.window.read(10) # milliseconds
    
    def change_connection(self, connection):
        self.connected =  connection
        if self.connected:
                self.window['connection'].update("Close conn", button_color=("white", "green"))
        else:   self.window['connection'].update("Open conn", button_color=("white", "red"))
        self.window["status"].update("CONNECTED" if connection else "DISCONNECTED")

        # Clear radar
        dummy = ["XXX","XXX","XXX","XXX","XXX",]
        self.change_radar(1, dummy)
        self.change_radar(2, dummy)
        self.change_radar(3, dummy)
        
    def change_radar(self, radar, values):
        self.window[f'DISTANCE_{radar}'].update(values[0])
        self.window[f'RPW_{radar}'].update(values[1])
        self.window[f'OUT_{radar}'].update(values[2])
        self.window[f'RCS_{radar}'].update(values[3])
        self.window[f'EXT_{radar}'].update(values[4])

    def __del__(self): 
        self.window.close()
        print("DELETED WINDOW CONFIGURATION")