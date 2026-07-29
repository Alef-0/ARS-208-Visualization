from pathlib import Path

import FreeSimpleGUI as sg

from interface.filter_schema import (
    AMBIGUITY_STATE_OPTIONS,
    DYNAMIC_PROPERTY_OPTIONS,
    INVALID_STATE_OPTIONS,
    PDH_KEY,
    RCS_KEY,
)


class Configurations:
    POWER = ["STANDARD", "-3dB Tx gain", "-6dB Tx gain", "-9dB Tx gain"]
    OUTPUT = ["NONE", "OBJECT", "CLUSTERS"]
    RCS = ["STANDARD", "HIGH SENSITIVITY"]
    FONT = ("Helvetica", 12)
    RADAR_LETTERS = {1: "A", 2: "B", 3: "C"}

    def __init__(self):
        sg.set_options(font=self.FONT)
        sg.theme("SystemDefaultForReal")
        self.connected_radar = False
        self.connected_cam = False
        self.recording = False
        self.recording_pending = False
        self.recording_counts = {}
        self.playback = False
        self.playback_pending = False
        self.create_radar_division()
        self.create_options()
        self.create_radar_control()
        self.layout = [[self.FRAME], [sg.Push(), self.options, sg.Push()], [self.radar_control]]
        self.window = sg.Window("Configurations Menu", self.layout, finalize=True)
        for element in self.window.element_list():
            if isinstance(element, sg.Combo):
                widget = element.Widget
                popdown = widget.tk.eval(f"ttk::combobox::PopdownWindow {widget}")
                widget.tk.call(f"{popdown}.f.l", "configure", "-font", ("Helvetica", 11))
                widget.tk.call(f"{popdown}.f.l", "configure", "-justify", "center")
        self.centralize_combos()
        self._refresh_mode_controls()

    def create_radar_division(self):
        gps = sg.Frame(
            "",
            [[
                sg.Text("0° 0' 0\" N, 0° 0' 0\" E", expand_x=True, key="gps_text"),
                sg.Button("OPEN GPS", key="conn_gps", button_color=("white", "green")),
                sg.Button("MAPS", key="gps_maps"),
            ]],
            title_location=sg.TITLE_LOCATION_TOP,
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
        self.FRAME = [sg.Frame(
            "Real Time Configurations",
            [columns, warning],
            expand_x=True,
            title_location=sg.TITLE_LOCATION_TOP,
        )]

    def create_options(self):
        choices = sg.Frame(
            "Radar",
            [[
                sg.Radio("1", "choose", key="send_1"),
                sg.Radio("2", "choose", key="send_2"),
                sg.Radio("3", "choose", key="send_3"),
                sg.Radio("all", "choose", key="send_all", default=True),
            ]],
            title_location=sg.TITLE_LOCATION_TOP,
        )
        column1 = sg.Column([
            [
                sg.Checkbox("Radar Power", key="CHECK_RPW", default=True),
                sg.Push(),
                sg.Combo(self.POWER, self.POWER[3], key="RPW", readonly=True),
            ],
            [
                sg.Checkbox("RCS Threshold", key="CHECK_RCS", default=True),
                sg.Push(),
                sg.Combo(self.RCS, self.RCS[1], key="RCS", readonly=True),
            ],
            [sg.Button("Send", expand_x=True), choices],
            [
                sg.Button(
                    "SAVE in Non Volatile Memory",
                    key="save_nvm",
                    expand_x=True,
                    button_color=("black", "white"),
                )
            ],
        ], expand_x=True)
        column2 = sg.Column([
            [
                sg.Checkbox("Output Type", key="CHECK_OUT", default=True),
                sg.Push(),
                sg.Combo(self.OUTPUT, self.OUTPUT[2], key="OUT", readonly=True, size=(15, 1)),
            ],
            [sg.Push(), sg.Checkbox("Send Quality", key="CHECK_QUALITY", default=True), sg.Push()],
            [
                sg.Button(
                    "OPEN RADAR",
                    key="conn_radar",
                    expand_x=True,
                    button_color=("white", "green"),
                )
            ],
            [
                sg.Button(
                    "OPEN CAM",
                    key="conn_cam",
                    expand_x=True,
                    button_color=("white", "green"),
                )
            ],
        ], expand_x=True)
        self.options = sg.Frame("Options", [
            [
                sg.Checkbox("Max Distance", key="CHECK_DISTANCE", default=True),
                sg.Text("196", key="SLIDER_VAL"),
                sg.Slider(
                    (196, 260),
                    196,
                    orientation="h",
                    resolution=1,
                    key="DISTANCE",
                    disable_number_display=True,
                    enable_events=True,
                    expand_x=True,
                ),
            ],
            [column1, sg.VSep(), column2],
        ], expand_x=True, title_location=sg.TITLE_LOCATION_TOP)

    @staticmethod
    def _option_control(option, include_color=False):
        controls = [sg.Checkbox(option.label, key=option.key, default=option.default,
                                disabled=not option.enabled, enable_events=True)]
        if include_color:
            controls.append(sg.Button("", button_color=option.color, disabled=True))
        return controls

    def _create_filter_layout(self):
        dynamic_rows = []
        for start in (0, 4):
            row = [sg.Push()]
            for option in DYNAMIC_PROPERTY_OPTIONS[start:start + 4]:
                row.extend(self._option_control(option, include_color=True))
            row.append(sg.Push())
            dynamic_rows.append(row)
        dynamic = sg.Column(dynamic_rows, justification="center")

        pdh = sg.Column([
            [sg.Text("PDH0 - False Alarm Probability (zero is invalid)", expand_x=True, justification="center")],
            [sg.Slider((1, 7), default_value=3, orientation="h", tick_interval=1,
                       disable_number_display=True, expand_x=True, enable_events=True, key=PDH_KEY)],
        ])
        rcs = sg.Column([
            [sg.Text("Minimum RCS (dBm²)", expand_x=True, justification="center")],
            [sg.Slider((-64.0, 63.5), default_value=-20.0, orientation="h", resolution=0.5,
                       expand_x=True, enable_events=True, key=RCS_KEY, disable_number_display=True)],
            [sg.Push(), sg.Text("-20.0", key="RCS_FILTER_VALUE"), sg.Push()],
        ], expand_x=True)

        ambiguity = sg.Column([
            [sg.Text("Ambiguity State", justification="center", expand_x=True)],
            [sg.Push(), *sum((self._option_control(option) for option in AMBIGUITY_STATE_OPTIONS[:2]), []), sg.Push()],
            [sg.Push(), *sum((self._option_control(option) for option in AMBIGUITY_STATE_OPTIONS[2:]), []), sg.Push()],
        ], justification="center")

        invalid_rows = []
        for start in range(0, len(INVALID_STATE_OPTIONS), 6):
            row = [sg.Push()]
            for option in INVALID_STATE_OPTIONS[start:start + 6]:
                row.extend(self._option_control(option))
            row.append(sg.Push())
            invalid_rows.append(row)

        invalid = sg.Column(
            [[sg.Text("Cluster Invalid State", expand_x=True, justification="center")], *invalid_rows],
            expand_x=True,
        )
        return [
            [dynamic],
            [sg.HorizontalSeparator()],
            [pdh, sg.VSep(), rcs],
            [sg.HorizontalSeparator()],
            [ambiguity, sg.VSep(), invalid],
        ]

    @staticmethod
    def _create_record_layout():
        radar_choices = [sg.Push(), sg.Text("Record radars:")]
        for channel, letter in ((1, "A"), (2, "B"), (3, "C")):
            radar_choices.append(sg.Checkbox(letter, key=f"record_radar_{channel}", default=True))
        radar_choices.append(sg.Push())
        return [
            [
                sg.Text("Destination folder"),
                sg.Input(default_text=str(Path.cwd()), key="record_folder", expand_x=True),
                sg.FolderBrowse("SELECT", key="record_browse", target="record_folder"),
            ],
            radar_choices,
            [sg.Button(
                "START RECORDING",
                key="record_toggle",
                expand_x=True,
                button_color=("white", "green"),
                disabled=True,
            )],
            [sg.Text("IDLE", key="record_status", expand_x=True, justification="center")],
            [sg.HorizontalSeparator()],
            [
                sg.Text("Playback folder"),
                sg.Input(default_text=str(Path.cwd()), key="playback_folder", expand_x=True),
                sg.FolderBrowse("SELECT", key="playback_browse", target="playback_folder"),
            ],
            [sg.Button(
                "PLAY RECORDING",
                key="playback_toggle",
                expand_x=True,
                button_color=("white", "green"),
            )],
            [sg.Text("IDLE", key="playback_status", expand_x=True, justification="center")],
        ]

    def create_radar_control(self):
        tabs = [[
            sg.Tab("Filters", self._create_filter_layout()),
            sg.Tab("Record", self._create_record_layout()),
        ]]
        self.radar_control = sg.Frame(
            "Radar Control",
            [[sg.TabGroup(tabs, expand_x=True)]],
            expand_x=True,
            title_location=sg.TITLE_LOCATION_TOP,
        )

    def centralize_combos(self):
        for key in ("RPW", "OUT", "RCS"):
            self.window[key].Widget.configure(justify="center")

    def read(self):
        return self.window.read(10)

    def _refresh_mode_controls(self):
        live_blocked = self.playback or self.playback_pending
        recording_inputs_disabled = self.recording or self.recording_pending or live_blocked
        for key in ("record_folder", "record_browse", "record_radar_1", "record_radar_2", "record_radar_3"):
            self.window[key].update(disabled=recording_inputs_disabled)

        if self.recording:
            record_disabled = False
        else:
            record_disabled = self.recording_pending or live_blocked or not self.connected_radar
        self.window["record_toggle"].update(disabled=record_disabled)

        playback_inputs_disabled = self.recording or self.recording_pending or self.playback or self.playback_pending
        for key in ("playback_folder", "playback_browse"):
            self.window[key].update(disabled=playback_inputs_disabled)
        self.window["playback_toggle"].update(
            disabled=self.recording or self.recording_pending or self.playback_pending,
        )
        for key in ("conn_radar", "conn_cam"):
            self.window[key].update(disabled=live_blocked)

    def change_connection_radar(self, connection):
        self.connected_radar = connection
        self.window["conn_radar"].update(
            "CLOSE RADAR" if connection else "OPEN RADAR",
            button_color=("white", "red" if connection else "green"),
        )
        self._refresh_mode_controls()
        for channel in range(1, 4):
            self.change_radar({f"{key}_{channel}": "XXX" for key in ("DISTANCE", "RPW", "OUT", "RCS", "EXT")})

    def change_connection_cam(self, connection):
        self.connected_cam = connection
        self.window["conn_cam"].update(
            "CLOSE CAM" if connection else "OPEN CAM",
            button_color=("white", "red" if connection else "green"),
        )
        self._refresh_mode_controls()

    def change_connection_gps(self, connection):
        self.window["conn_gps"].update(
            "CLOSE GPS" if connection else "OPEN GPS",
            button_color=("white", "red" if connection else "green"),
        )

    def set_recording_pending(self, starting):
        self.recording_pending = True
        self.window["record_toggle"].update(
            "STARTING..." if starting else "STOPPING...",
            disabled=True,
        )
        self._refresh_mode_controls()

    def change_recording(self, payload):
        self.recording = bool(payload.get("active"))
        self.recording_pending = False
        self.recording_counts = dict(payload.get("counts", {}))
        self.window["record_toggle"].update(
            "STOP RECORDING" if self.recording else "START RECORDING",
            button_color=("white", "red" if self.recording else "green"),
        )
        if self.recording:
            letters = ", ".join(self.RADAR_LETTERS[int(channel)] for channel in sorted(payload.get("folders", {})))
            self.window["record_status"].update(f"RECORDING: {letters}")
        else:
            self._update_recording_count_text("SAVED" if self.recording_counts else "IDLE")
        self._refresh_mode_controls()

    def change_recording_progress(self, payload):
        self.recording_counts[payload["channel"]] = payload["count"]
        self._update_recording_count_text("RECORDING")

    def show_recording_error(self, message):
        self.change_recording({"active": False, "counts": {}})
        sg.popup_error(message, title="Recording error")

    def set_playback_pending(self):
        self.playback_pending = True
        self.window["playback_toggle"].update("PREPARING...", disabled=True)
        self.window["playback_status"].update("STOPPING LIVE MONITORING")
        self._refresh_mode_controls()

    def change_playback(self, payload):
        self.playback = bool(payload.get("active"))
        self.playback_pending = False
        self.window["playback_toggle"].update(
            "STOP PLAYBACK" if self.playback else "PLAY RECORDING",
            button_color=("white", "red" if self.playback else "green"),
        )
        if self.playback:
            total = payload.get("total", 0)
            self.window["playback_status"].update(f"PLAYING 0 / {total}")
        elif payload.get("completed"):
            self.window["playback_status"].update("COMPLETED")
        else:
            self.window["playback_status"].update("IDLE")
        self._refresh_mode_controls()

    def change_playback_progress(self, payload):
        self.window["playback_status"].update(
            f"PLAYING {payload['current']} / {payload['total']} — {payload['file']}"
        )

    def show_playback_error(self, message):
        self.change_playback({"active": False, "completed": False})
        sg.popup_error(message, title="Playback error")

    def show_camera_recording_error(self, message):
        sg.popup_error(message, title="Camera snapshot error")

    def _update_recording_count_text(self, prefix):
        if not self.recording_counts:
            self.window["record_status"].update(prefix)
            return
        counts = " | ".join(
            f"{self.RADAR_LETTERS[channel]}: {count}"
            for channel, count in sorted(self.recording_counts.items())
        )
        self.window["record_status"].update(f"{prefix} — {counts}")

    def change_radar(self, values):
        for key, value in values.items():
            self.window[key].update(value)
