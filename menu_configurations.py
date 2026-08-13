from pathlib import Path

import FreeSimpleGUI as sg

from MENU_BASE import Configurations as BaseConfigurations


class Configurations(BaseConfigurations):
    def __init__(self):
        self.snapshot_playback = False
        self.snapshot_playback_pending = False
        self.snapshot_playback_paused = False
        self.playback_width = 1280
        self.playback_height = 720
        super().__init__()

    def create_radar_division(self):
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
                [sg.Text("Quality", expand_x=True), sg.Text("XXX", key=f"QUALITY_{channel}", justification="right")],
                [sg.Text("Extended Info", expand_x=True), sg.Text("XXX", key=f"EXT_{channel}", justification="right")],
                [sg.Text("Control Relay", expand_x=True), sg.Text("XXX", key=f"RELAY_{channel}", justification="right")],
                [sg.Radio(f"Visualizar Grupo {letter}", "visu_radar", key=f"choose_{channel}", default=channel == 2, enable_events=True)],
            ])
            columns.extend([sg.Push(), column, sg.Push()])
            if channel != 3:
                columns.append(sg.VSep())

        self.FRAME = [sg.Frame(
            "Real Time Configurations",
            [
                columns,
                [
                    sg.Push(),
                    sg.Text(
                        "MESSAGES: --",
                        key="received_messages",
                        expand_x=True,
                        justification="center",
                    ),
                    sg.Push(),
                ],
            ],
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
            [
                sg.Push(),
                sg.Checkbox("Quality", key="CHECK_QUALITY", default=True),
                sg.Checkbox("Extended Info", key="CHECK_EXTENDED", default=True),
                sg.Checkbox("Control Relay", key="CHECK_RELAY", default=True),
                sg.Push(),
            ],
            [
                sg.Button(
                    "OPEN RADAR",
                    key="conn_radar",
                    expand_x=True,
                    button_color=("white", "green"),
                ),
                sg.VSep(),
                sg.Button(
                    "OPEN GPS",
                    key="conn_gps",
                    button_color=("white", "green"),
                ),
                sg.Button("MAPS", key="gps_maps"),
            ],
            [
                sg.Button(
                    "OPEN CAM",
                    key="conn_cam",
                    expand_x=True,
                    button_color=("white", "green"),
                ),
                sg.VSep(),
                sg.Text(
                    "0° 0' 0\" N, 0° 0' 0\" E",
                    key="gps_text",
                    expand_x=True,
                    justification="center",
                ),
            ],
        ], expand_x=True)
        self.options = [
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
        ]

    @staticmethod
    def _create_snapshot_layout():
        layout = BaseConfigurations._create_snapshot_layout()[:-1]
        layout.extend([
            [sg.HorizontalSeparator()],
            [
                sg.Text("Playback folder"),
                sg.Input(default_text=str(Path.cwd()), key="snapshot_playback_folder", expand_x=True),
                sg.FolderBrowse("SELECT", key="snapshot_playback_browse", target="snapshot_playback_folder"),
            ],
            [
                sg.Push(),
                sg.Button(
                    "START PLAYBACK",
                    key="snapshot_playback_toggle",
                    button_color=("white", "green"),
                ),
                sg.VSep(),
                sg.Button("PREVIOUS", key="snapshot_playback_previous", disabled=True),
                sg.Button("PAUSE", key="snapshot_playback_pause", disabled=True),
                sg.Button("NEXT", key="snapshot_playback_next", disabled=True),
                sg.Button("SNAPSHOT CURRENT", key="snapshot_playback_snapshot", disabled=True),
                sg.Push(),
            ],
            [
                sg.Text(
                    "",
                    key="snapshot_playback_status",
                    expand_x=True,
                    justification="center",
                    pad=(0, 0),
                )
            ],
        ])
        return layout

    @staticmethod
    def _create_playback_image_layout():
        return [
            [
                sg.Push(),
                sg.Text("Playback image resolution"),
                sg.Input("1280", key="playback_width", size=(8, 1), justification="right"),
                sg.Text("×"),
                sg.Input("720", key="playback_height", size=(8, 1), justification="right"),
                sg.Button("APPLY", key="playback_resolution_apply"),
                sg.Push(),
            ],
            [
                sg.Text(
                    "Default: 1280 × 720. This only resizes playback display images; recorded and snapshot images remain unchanged.",
                    expand_x=True,
                    justification="center",
                )
            ],
            [sg.Push(), sg.Text("1280 × 720", key="playback_resolution_status"), sg.Push()],
        ]

    def create_radar_control(self):
        tabs = [[
            sg.Tab("Configurations", self.options),
            sg.Tab("Record", self._create_record_layout()),
            sg.Tab("Snapshots", self._create_snapshot_layout()),
            sg.Tab("Playback Image", self._create_playback_image_layout()),
        ]]
        self.radar_control = sg.Frame(
            "Radar Control",
            [[sg.TabGroup(tabs, expand_x=True, pad=(0, 0))]],
            expand_x=True,
            title_location=sg.TITLE_LOCATION_TOP,
            pad=(0, 0),
        )

    def _refresh_mode_controls(self):
        live_blocked = (
            self.playback
            or self.playback_pending
            or self.snapshot_playback
            or self.snapshot_playback_pending
        )
        recording_inputs_disabled = self.recording or self.recording_pending or live_blocked
        for key in ("record_folder", "record_browse", "record_radar_1", "record_radar_2", "record_radar_3"):
            self.window[key].update(disabled=recording_inputs_disabled)

        record_disabled = (
            self.recording_pending
            or self.snapshot_pending
            or live_blocked
            or (not self.recording and not self.connected_radar)
        )
        self.window["record_toggle"].update(disabled=record_disabled)

        snapshot_inputs_disabled = (
            self.snapshot_pending
            or self.recording
            or self.recording_pending
            or live_blocked
        )
        for key in (
            "snapshot_folder", "snapshot_browse",
            "snapshot_group_1", "snapshot_group_2", "snapshot_group_3",
        ):
            self.window[key].update(disabled=snapshot_inputs_disabled)
        self.window["snapshot_capture"].update(
            disabled=(
                snapshot_inputs_disabled
                or not self.connected_radar
                or not self.connected_cam
            )
        )

        playback_inputs_disabled = (
            self.recording
            or self.recording_pending
            or self.snapshot_pending
            or self.playback
            or self.playback_pending
            or self.snapshot_playback
            or self.snapshot_playback_pending
        )
        for key in ("playback_folder", "playback_browse"):
            self.window[key].update(disabled=playback_inputs_disabled)
        self.window["playback_toggle"].update(
            disabled=(
                self.recording
                or self.recording_pending
                or self.snapshot_pending
                or self.playback_pending
                or self.snapshot_playback
                or self.snapshot_playback_pending
            )
        )

        snapshot_playback_inputs_disabled = (
            self.recording
            or self.recording_pending
            or self.snapshot_pending
            or self.playback
            or self.playback_pending
            or self.snapshot_playback
            or self.snapshot_playback_pending
        )
        for key in ("snapshot_playback_folder", "snapshot_playback_browse"):
            self.window[key].update(disabled=snapshot_playback_inputs_disabled)
        self.window["snapshot_playback_toggle"].update(
            disabled=(
                self.recording
                or self.recording_pending
                or self.snapshot_pending
                or self.playback
                or self.playback_pending
                or self.snapshot_playback_pending
            )
        )
        transport_disabled = not self.snapshot_playback
        for key in (
            "snapshot_playback_previous",
            "snapshot_playback_pause",
            "snapshot_playback_next",
            "snapshot_playback_snapshot",
        ):
            self.window[key].update(disabled=transport_disabled)

        for key in ("conn_radar", "conn_cam"):
            self.window[key].update(disabled=live_blocked or self.snapshot_pending)

    def change_received_messages(self, message_ids):
        messages = ", ".join(f"0x{message_id:03X}" for message_id in message_ids) or "--"
        self.window["received_messages"].update(f"MESSAGES: {messages}")

    def change_snapshot_saved(self, payload):
        if (
            self.snapshot_request_id is not None
            and payload.get("request_id") != self.snapshot_request_id
        ):
            return
        self.snapshot_pending = False
        self.snapshot_request_id = None
        self.window["snapshot_capture"].update(
            "CAPTURE SNAPSHOT",
            button_color=("white", "green"),
        )
        self.window["snapshot_status"].update(
            f"SAVED: {payload['point_cloud']} + {payload['camera_frame']}"
        )
        self._refresh_mode_controls()

    def set_snapshot_playback_pending(self):
        self.snapshot_playback_pending = True
        self.window["snapshot_playback_toggle"].update("PREPARING...", disabled=True)
        self.window["snapshot_playback_status"].update("STOPPING LIVE MONITORING")
        self._refresh_mode_controls()

    def change_snapshot_playback(self, payload):
        self.snapshot_playback = bool(payload.get("active"))
        self.snapshot_playback_pending = False
        self.snapshot_playback_paused = bool(payload.get("paused", False))
        self.window["snapshot_playback_toggle"].update(
            "STOP PLAYBACK" if self.snapshot_playback else "START PLAYBACK",
            button_color=("white", "red" if self.snapshot_playback else "green"),
        )
        self.window["snapshot_playback_pause"].update(
            "PLAY" if self.snapshot_playback_paused else "PAUSE"
        )
        if self.snapshot_playback:
            current = payload.get("current", 1)
            total = payload.get("total", 0)
            state = "PAUSED" if self.snapshot_playback_paused else "PLAYING"
            self.window["snapshot_playback_status"].update(f"{state} {current} / {total}")
        elif payload.get("completed"):
            self.window["snapshot_playback_status"].update("COMPLETED")
        else:
            self.window["snapshot_playback_status"].update("")
        self._refresh_mode_controls()

    def change_snapshot_playback_progress(self, payload):
        state = "PAUSED" if self.snapshot_playback_paused else "PLAYING"
        self.window["snapshot_playback_status"].update(
            f"{state} {payload['current']} / {payload['total']} — {payload['file']} + {payload['image']}"
        )

    def change_snapshot_playback_pause(self, payload):
        self.snapshot_playback_paused = bool(payload.get("paused"))
        self.window["snapshot_playback_pause"].update(
            "PLAY" if self.snapshot_playback_paused else "PAUSE"
        )
        self._refresh_mode_controls()

    def show_snapshot_playback_error(self, message):
        self.change_snapshot_playback({"active": False, "completed": False})
        sg.popup_error(message, title="Snapshot playback error")

    def show_snapshot_playback_snapshot_saved(self, payload):
        self.window["snapshot_playback_status"].update(
            f"SAVED: {payload['point_cloud']} + {payload['camera_frame']}"
        )

    def show_snapshot_playback_snapshot_error(self, message):
        sg.popup_error(message, title="Playback snapshot error")

    def change_playback_resolution(self, width, height):
        self.playback_width = width
        self.playback_height = height
        self.window["playback_resolution_status"].update(f"{width} × {height}")
