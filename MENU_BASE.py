from pathlib import Path

import FreeSimpleGUI as sg

from INTERFACE.filter_schema import (
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
        self.snapshot_pending = False
        self.snapshot_request_id = None
        self.playback = False
        self.playback_pending = False
        self.create_radar_division()
        self.create_options()
        self.create_radar_control()
        self.create_filters()
        self.layout = [[self.FRAME], [self.radar_control], [self.filters]]
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
        received_messages = sg.Column(
            [
                [
                    sg.Text(
                        "MESSAGES: --",
                        key="received_messages_1",
                        expand_x=True,
                        justification="center",
                    )
                ],
                [
                    sg.Text(
                        "",
                        key="received_messages_2",
                        expand_x=True,
                        justification="center",
                    )
                ],
            ],
            expand_x=True,
        )
        message_status = [
            sg.Push(),
            received_messages,
            sg.Push(),
            gps,
            sg.Push(),
        ]
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
            [columns, message_status],
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
            [sg.Push(), sg.Text("-20.0", key="RCS_FILTER_VALUE"), sg.Push()]
        ], expand_x=True)

        return [
            [dynamic],
            [sg.HorizontalSeparator()],
            [pdh, sg.VSep(), rcs],
        ]

    def _create_cluster_filter_layout(self):
        ambiguity = sg.Column([
            [sg.Text("Ambiguity State", justification="center", expand_x=True)],
            [sg.Push(), *sum((self._option_control(option) for option in AMBIGUITY_STATE_OPTIONS[:2]), []), sg.Push()],
            [sg.Push(), *sum((self._option_control(option) for option in AMBIGUITY_STATE_OPTIONS[2:]), []), sg.Push()],
        ], justification="center", expand_x=True, vertical_alignment="top")

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
            vertical_alignment="top",
        )
        return [[
            ambiguity,
            sg.VSep(),
            invalid,
        ]]

    @staticmethod
    def _create_record_layout():
        recording_root = Path.cwd() / "recordings"
        recording_root.mkdir(exist_ok=True)
        radar_choices = [sg.Text("Group:")]
        for channel, letter in ((1, "A"), (2, "B"), (3, "C")):
            radar_choices.append(
                sg.Checkbox(letter, key=f"record_radar_{channel}", default=False)
            )
        return [
            [
                sg.Text("Destination folder"),
                sg.Input(
                    default_text=str(recording_root),
                    key="record_folder",
                    expand_x=True,
                ),
                sg.FolderBrowse("SELECT", key="record_browse", target="record_folder"),
            ],
            [
                *radar_choices,
                sg.Push(),
                sg.Text("IDLE", key="record_status", justification="center"),
                sg.Text(
                    "RADAR CLOSED | CAMERA CLOSED",
                    key="record_devices",
                    justification="center",
                ),
                sg.Push(),
                sg.Button(
                    "START RECORDING",
                    key="record_toggle",
                    button_color=("white", "green"),
                    disabled=True,
                ),
            ],
            [sg.HorizontalSeparator()],
            [
                sg.Text("Playback folder"),
                sg.Input(default_text=str(Path.cwd()), key="playback_folder", expand_x=True),
                sg.FolderBrowse("SELECT", key="playback_browse", target="playback_folder"),
            ],
            [
                sg.Push(),
                sg.Text("IDLE", key="playback_status", justification="center"),
                sg.Text(
                    "RADAR CLOSED | CAMERA CLOSED",
                    key="playback_devices",
                    justification="center",
                ),
                sg.Push(),
                sg.Button(
                    "START",
                    key="playback_toggle",
                    button_color=("white", "green"),
                ),
                sg.Button("STOP", key="playback_stop", disabled=True),
                sg.Button("RESTART", key="playback_restart", disabled=True),
                sg.Button("-5 s", key="playback_previous_5s", disabled=True),
                sg.Button("+5 s", key="playback_next_5s", disabled=True),
            ],
        ]

    @staticmethod
    def _create_snapshot_layout():
        snapshot_root = Path.cwd() / "snapshots"
        snapshot_root.mkdir(exist_ok=True)
        group_controls = [sg.Text("Group:")]
        for channel, letter in ((1, "A"), (2, "B"), (3, "C")):
            group_controls.append(
                sg.Radio(
                    letter,
                    "snapshot_group",
                    key=f"snapshot_group_{channel}",
                    default=channel == 2,
                )
            )
        return [
            [
                sg.Text("Destination folder"),
                sg.Input(
                    default_text=str(snapshot_root),
                    key="snapshot_folder",
                    expand_x=True,
                ),
                sg.FolderBrowse(
                    "SELECT",
                    key="snapshot_browse",
                    target="snapshot_folder",
                ),
            ],
            [
                *group_controls,
                sg.Push(),
                sg.Text("IDLE", key="snapshot_status", justification="center"),
                sg.Push(),
                sg.Button(
                    "CAPTURE SNAPSHOT",
                    key="snapshot_capture",
                    button_color=("white", "green"),
                    disabled=True,
                ),
            ],
            [
                sg.Text(
                    "Use an empty-of-JSON folder or a recording folder for the selected group.",
                    expand_x=True,
                    justification="center",
                )
            ],
        ]

    def create_radar_control(self):
        tabs = [[
            sg.Tab("Configurations", self.options),
            sg.Tab("Record", self._create_record_layout()),
            sg.Tab("Snapshots", self._create_snapshot_layout()),
        ]]
        self.radar_control = sg.Frame(
            "Radar Control",
            [[sg.TabGroup(tabs, expand_x=True)]],
            expand_x=True,
            title_location=sg.TITLE_LOCATION_TOP,
        )

    def create_filters(self):
        tabs = [[
            sg.Tab("Basic", self._create_filter_layout()),
            sg.Tab("Cluster Options", self._create_cluster_filter_layout()),
        ]]
        self.filters = sg.Frame(
            "Filters",
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
            record_disabled = (
                self.recording_pending
                or self.snapshot_pending
                or live_blocked
            )
        self.window["record_toggle"].update(disabled=record_disabled)

        snapshot_inputs_disabled = (
            self.snapshot_pending
            or self.recording
            or self.recording_pending
            or live_blocked
        )
        for key in (
            "snapshot_folder",
            "snapshot_browse",
            "snapshot_group_1",
            "snapshot_group_2",
            "snapshot_group_3",
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
        )
        for key in ("playback_folder", "playback_browse"):
            self.window[key].update(disabled=playback_inputs_disabled)
        self.window["playback_toggle"].update(
            disabled=(
                self.recording
                or self.recording_pending
                or self.snapshot_pending
                or self.playback
                or self.playback_pending
            ),
        )
        transport_disabled = not self.playback
        for key in (
            "playback_stop",
            "playback_restart",
            "playback_previous_5s",
            "playback_next_5s",
        ):
            self.window[key].update(disabled=transport_disabled)
        for key in ("conn_radar", "conn_cam"):
            self.window[key].update(disabled=live_blocked or self.snapshot_pending)

    def _update_record_device_status(self):
        status = " | ".join((
            f"RADAR {'OPEN' if self.connected_radar else 'CLOSED'}",
            f"CAMERA {'OPEN' if self.connected_cam else 'CLOSED'}",
        ))
        self.window["record_devices"].update(status)
        self.window["playback_devices"].update(status)

    def change_connection_radar(self, connection):
        self.connected_radar = connection
        self.window["conn_radar"].update(
            "CLOSE RADAR" if connection else "OPEN RADAR",
            button_color=("white", "red" if connection else "green"),
        )
        self._update_record_device_status()
        self._refresh_mode_controls()
        if not connection:
            self.change_received_messages(())
        for channel in range(1, 4):
            self.change_radar({
                f"{key}_{channel}": "XXX"
                for key in ("DISTANCE", "RPW", "OUT", "RCS", "QUALITY", "EXT", "RELAY")
            })

    def change_connection_cam(self, connection):
        self.connected_cam = connection
        self.window["conn_cam"].update(
            "CLOSE CAM" if connection else "OPEN CAM",
            button_color=("white", "red" if connection else "green"),
        )
        self._update_record_device_status()
        self._refresh_mode_controls()

    def change_connection_gps(self, connection):
        self.window["conn_gps"].update(
            "CLOSE GPS" if connection else "OPEN GPS",
            button_color=("white", "red" if connection else "green"),
        )

    def change_received_messages(self, message_ids):
        messages = [f"0x{message_id:03X}" for message_id in message_ids]
        split_at = (len(messages) + 1) // 2
        first_line = ", ".join(messages[:split_at]) or "--"
        second_line = ", ".join(messages[split_at:])
        self.window["received_messages_1"].update(f"MESSAGES: {first_line}")
        self.window["received_messages_2"].update(second_line)

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
            letters = ", ".join(
                self.RADAR_LETTERS[int(channel)]
                for channel in sorted(payload.get("folders", {}))
            )
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

    def set_snapshot_pending(self, request_id, channel):
        self.snapshot_pending = True
        self.snapshot_request_id = request_id
        self.window["snapshot_status"].update(
            f"CAPTURING GROUP {self.RADAR_LETTERS[channel]}..."
        )
        self.window["snapshot_capture"].update("CAPTURING...", disabled=True)
        self._refresh_mode_controls()

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
            f"SAVED {payload['group']}: {payload['point_cloud']} + {payload['camera_frame']}"
        )
        self._refresh_mode_controls()

    def show_snapshot_error(self, message):
        self.snapshot_pending = False
        self.snapshot_request_id = None
        self.window["snapshot_capture"].update(
            "CAPTURE SNAPSHOT",
            button_color=("white", "green"),
        )
        self.window["snapshot_status"].update("FAILED")
        self._refresh_mode_controls()
        sg.popup_error(message, title="Snapshot error")

    def set_playback_pending(self):
        self.playback_pending = True
        self.window["playback_toggle"].update("PREPARING...", disabled=True)
        self.window["playback_status"].update("STOPPING LIVE MONITORING")
        self._refresh_mode_controls()

    def change_playback(self, payload):
        self.playback = bool(payload.get("active"))
        self.playback_pending = False
        self.window["playback_toggle"].update(
            "START",
            button_color=("white", "green"),
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
