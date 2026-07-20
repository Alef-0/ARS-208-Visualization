import FreeSimpleGUI as sg

from ui.filter_schema import (
    AMBIGUITY_STATE_OPTIONS,
    DYNAMIC_PROPERTY_OPTIONS,
    INVALID_STATE_OPTIONS,
    PDH_KEY,
)


class Configurations:
    POWER = ["STANDARD", "-3dB Tx gain", "-6dB Tx gain", "-9dB Tx gain"]
    OUTPUT = ["NONE", "OBJECT", "CLUSTERS"]
    RCS = ["STANDARD", "HIGH SENSITIVITY"]
    FONT = ("Helvetica", 12)

    def __init__(self):
        sg.set_options(font=self.FONT)
        sg.theme("SystemDefaultForReal")
        self.create_radar_division()
        self.create_options()
        self.create_filter_list()
        self.layout = [[self.FRAME], [sg.Push(), self.options, sg.Push()], [self.filter]]
        self.window = sg.Window("Configurations Menu", self.layout, finalize=True)
        for element in self.window.element_list():
            if isinstance(element, sg.Combo):
                widget = element.Widget
                popdown = widget.tk.eval(f"ttk::combobox::PopdownWindow {widget}")
                widget.tk.call(f"{popdown}.f.l", "configure", "-font", ("Helvetica", 11))
                widget.tk.call(f"{popdown}.f.l", "configure", "-justify", "center")
        self.centralize_combos()
        self.connected_radar = False
        self.connected_cam = False

    def create_radar_division(self):
        gps = sg.Frame(
            "",
            [[
                sg.Text("0° 0' 0\" N, 0° 0' 0\" E", expand_x=True, key="gps_text"),
                sg.Button("OPEN GPS", key="conn_gps", button_color=("white", "green")),
                sg.Button("MAPS", key="gps_maps"),
            ]],
            title_location=sg.TITLE_LOCATION_RIGHT,
        )
        warning = [sg.Push(), sg.Text("CLUSTER + QUALITY FOR GRAPHS"), sg.Push(), gps, sg.Push()]
        columns = []
        names = ("LEFT", "MIDDLE", "RIGHT")
        letters = ("A", "B", "C")
        for channel, (name, letter) in enumerate(zip(names, letters), start=1):
            column = sg.Column([
                [sg.Text(f"{name} {letter}", justification="center", expand_x=True)],
                [sg.Text("Distance", expand_x=True), sg.Text("XXX", key=f"DISTANCE_{channel}", justification="right")],
                [sg.Text("Radar", expand_x=True), sg.Text("XXX", key=f"RPW_{channel}", justification="right")],
                [sg.Text("Output", expand_x=True), sg.Text("XXX", key=f"OUT_{channel}", justification="right")],
                [sg.Text("RCS", expand_x=True), sg.Text("XXX", key=f"RCS_{channel}", justification="right")],
                [sg.Text("Extra Info", expand_x=True), sg.Text("XXX", key=f"EXT_{channel}", justification="right")],
                [sg.Radio(f"Visualizar Grupo {letter}", "visu_radar", key=f"choose_{channel}", default=channel == 2, enable_events=True)],
            ])
            columns.extend([sg.Push(), column, sg.Push()])
            if channel != 3:
                columns.append(sg.VSep())
        self.FRAME = [sg.Frame("Real Time Configurations", [columns, warning], expand_x=True)]

    def create_options(self):
        choices = sg.Frame("Radar", [[
            sg.Radio("1", "choose", key="send_1"),
            sg.Radio("2", "choose", key="send_2"),
            sg.Radio("3", "choose", key="send_3"),
            sg.Radio("all", "choose", key="send_all", default=True),
        ]])
        column1 = sg.Column([
            [sg.Checkbox("Radar Power", key="CHECK_RPW", default=True), sg.Combo(self.POWER, self.POWER[3], key="RPW", readonly=True)],
            [sg.Checkbox("RCS Threshold", key="CHECK_RCS", default=True), sg.Combo(self.RCS, self.RCS[1], key="RCS", readonly=True)],
        ])
        column2 = sg.Column([
            [sg.Checkbox("Output Type", key="CHECK_OUT", default=True), sg.Combo(self.OUTPUT, self.OUTPUT[2], key="OUT", readonly=True)],
            [sg.Checkbox("Send Quality", key="CHECK_QUALITY", default=True)],
        ])
        self.options = sg.Frame("Options", [
            [sg.Checkbox("Max Distance", key="CHECK_DISTANCE", default=True), sg.Text("196", key="SLIDER_VAL"),
             sg.Slider((196, 260), 196, orientation="h", resolution=1, key="DISTANCE", disable_number_display=True, enable_events=True, expand_x=True)],
            [column1, sg.VSep(), column2],
            [sg.Button("Send"), choices, sg.VSep(),
             sg.Button("OPEN RADAR", key="conn_radar", button_color=("white", "green")),
             sg.Button("OPEN CAM", key="conn_cam", button_color=("white", "green"))],
            [sg.Button("SAVE in Non Volatile Memory", key="save_nvm", expand_x=True, button_color=("black", "white"))],
        ], expand_x=True)

    @staticmethod
    def _option_control(option, include_color=False):
        controls = [sg.Checkbox(option.label, key=option.key, default=option.default,
                                disabled=not option.enabled, enable_events=True)]
        if include_color:
            controls.append(sg.Button("", button_color=option.color, disabled=True))
        return controls

    def create_filter_list(self):
        dynamic_rows = []
        for start in (0, 4):
            row = [sg.Push()]
            for option in DYNAMIC_PROPERTY_OPTIONS[start:start + 4]:
                row.extend(self._option_control(option, include_color=True))
            row.append(sg.Push())
            dynamic_rows.append(row)
        dynamic = sg.Column(dynamic_rows, justification="center")

        pdh = sg.Column([
            [sg.Text("PDH0 - False Alarm Probability (zero is invalid)", justification="center")],
            [sg.Slider((1, 7), default_value=3, orientation="h", tick_interval=1,
                       disable_number_display=True, expand_x=True, enable_events=True, key=PDH_KEY)],
        ])

        ambiguity = sg.Column([
            [sg.Text("Ambiguity State", justification="center")],
            [*sum((self._option_control(option) for option in AMBIGUITY_STATE_OPTIONS[:2]), [])],
            [*sum((self._option_control(option) for option in AMBIGUITY_STATE_OPTIONS[2:]), [])],
        ], justification="center")

        invalid_rows = []
        for start in (0, 9):
            row = [sg.Push()]
            for option in INVALID_STATE_OPTIONS[start:start + 9]:
                row.extend(self._option_control(option))
            row.append(sg.Push())
            invalid_rows.append(row)
        invalid = sg.Column([[sg.Text("Cluster Invalid State", expand_x=True, justification="center")], *invalid_rows], expand_x=True)

        self.filter = sg.Frame("Filters for points", [
            [dynamic], [sg.HorizontalSeparator()], [pdh, sg.VSep(), ambiguity],
            [sg.HorizontalSeparator()], [invalid],
        ], expand_x=True)

    def centralize_combos(self):
        for key in ("RPW", "OUT", "RCS"):
            self.window[key].Widget.configure(justify="center")

    def read(self):
        return self.window.read(10)

    def change_connection_radar(self, connection):
        self.connected_radar = connection
        self.window["conn_radar"].update(
            "CLOSE RADAR" if connection else "OPEN RADAR",
            button_color=("white", "red" if connection else "green"),
        )
        for channel in range(1, 4):
            self.change_radar({f"{key}_{channel}": "XXX" for key in ("DISTANCE", "RPW", "OUT", "RCS", "EXT")})

    def change_connection_cam(self, connection):
        self.connected_cam = connection
        self.window["conn_cam"].update(
            "CLOSE CAM" if connection else "OPEN CAM",
            button_color=("white", "red" if connection else "green"),
        )

    def change_connection_gps(self, connection):
        self.window["conn_gps"].update(
            "CLOSE GPS" if connection else "OPEN GPS",
            button_color=("white", "red" if connection else "green"),
        )

    def change_radar(self, values):
        for key, value in values.items():
            self.window[key].update(value)
