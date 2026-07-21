from connection.connection_packages import Clusters_messages, Objects_messages
from interface.filter_schema import DYNAMIC_COLORS_BGR, PDH_KEY, parse_filter_key


class Filter_graph:
    def __init__(self, values: dict):
        self.enabled_values = {
            "dynamic_property": set(),
            "ambiguity_state": set(),
            "invalid_state": set(),
        }
        self.pdh_max = int(values.get(PDH_KEY, 3))
        self._load(values)

    def _load(self, values: dict) -> None:
        for key, enabled in values.items():
            parsed = parse_filter_key(key)
            if parsed is None:
                continue
            field, value = parsed
            if field == "pdh":
                self.pdh_max = int(enabled)
            elif field in self.enabled_values and isinstance(value, int) and enabled:
                self.enabled_values[field].add(value)

    def update_values(self, event: str, values: dict) -> None:
        parsed = parse_filter_key(event)
        if parsed is None:
            return
        field, value = parsed
        if field == "pdh":
            self.pdh_max = int(values[event])
            return
        if field not in self.enabled_values or not isinstance(value, int):
            return
        selected = self.enabled_values[field]
        if values[event]:
            selected.add(value)
        else:
            selected.discard(value)

    def allowed(self, dyn: int, pdh: int, ambg: int, inv: int) -> bool:
        return (
            dyn in self.enabled_values["dynamic_property"]
            and 0 < pdh <= self.pdh_max
            and ambg in self.enabled_values["ambiguity_state"]
            and inv in self.enabled_values["invalid_state"]
        )

    def filter_points(self, messages: Clusters_messages):
        all_x, all_y, colors = [], [], []
        for point in messages.snapshot():
            if not self.allowed(
                point.dynamic_property,
                point.pdh,
                point.ambiguity_state,
                point.invalid_flag,
            ):
                continue
            all_x.append(point.dist_latitude)
            all_y.append(point.dist_long)
            colors.append(DYNAMIC_COLORS_BGR[point.dynamic_property])
        return all_x, all_y, colors

    def filter_objects(self, messages: Objects_messages):
        all_x, all_y, colors = [], [], []
        enabled_dynamic = self.enabled_values["dynamic_property"]
        for obj in messages.snapshot():
            if obj.dynamic_property not in enabled_dynamic:
                continue
            all_x.append(obj.dist_latitude)
            all_y.append(obj.dist_long)
            colors.append(DYNAMIC_COLORS_BGR[obj.dynamic_property])
        return all_x, all_y, colors
