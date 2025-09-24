import FreeSimpleGUI as sg

class Configurations:
    POWER = ["STANDARD", "-3dB Tx gain", "-6dB Tx gain", "-9dB Tx gain"]
    OUTPUT = ["NONE", "OBJECT", "CLUSTERS"]
    RCS = ["STANDARD", "HIGH SENSITIVITY"]
    FONT = ("Helvetica", 12) 

    def __init__(self):
        sg.set_options(font=self.FONT)
        sg.theme("DefaultNoMoreNagging")
        
        # Tem que fazer depois para criar as configurações
        self.create_radar_division()
        self.choices = sg.Frame("Radar", [[
            sg.Radio("1", "choose", key="send_1"), sg.Radio("2", "choose", key="send_2"), 
            sg.Radio("3", "choose", key="send_3"),  sg.Radio("all", "choose", key="send_all", default=True)
        ]], 
            title_location=sg.TITLE_LOCATION_LEFT)
        
        self.layout = [
        [self.FRAME],
        # O slider é os ranges que o standard aceita
        [sg.Checkbox("Max Distance", expand_x=True, key="CHECK_DISTANCE"),  sg.Push(), sg.Slider((196, 260), 100, orientation="h", resolution=1, key="DISTANCE", size=(40, 20)), sg.Push()],
        [sg.Checkbox("Radar Power", expand_x=True, key="CHECK_RPW"),        sg.Push(), sg.Combo(self.POWER, self.POWER[0], font=self.FONT, size=15, key="RPW", readonly=True), sg.Push()],
        [sg.Checkbox("Output Power", expand_x=True, key="CHECK_OUT"),       sg.Push(), sg.Combo(self.OUTPUT, self.OUTPUT[2], font=self.FONT, size=15, key="OUT", readonly=True), sg.Push()],
        [sg.Checkbox("RCS Treshold", expand_x=True, key="CHECK_RCS"),       sg.Push(), sg.Combo(self.RCS, self.RCS[1], font=self.FONT, size=17, key="RCS", readonly=True), sg.Push(),],
        [sg.Checkbox("Send Quality", expand_x=True, key="CHECK_QUALITY")],   
        [sg.Button("Send"), self.choices,  sg.Push(), sg.Button("Open conn", key="connection", button_color=("white", "red"), size=(10)), sg.Text("DISCONNECTED", key="status")]
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

    def read(self):
        return self.window.read(10) # milliseconds
    
    def change_connection(self, connection):
        self.connected =  connection
        if self.connected:
                self.window['connection'].update("Close conn", button_color=("white", "green"))
        else:   self.window['connection'].update("Open conn", button_color=("white", "red"))

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