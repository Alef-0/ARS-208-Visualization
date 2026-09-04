import math
from typing import Iterable

from sensors.radar.connection_packages import (
    Clusters_messages,
    MISSING_QUALITY,
    Objects_messages,
    RadarObject,
    RadarPoint,
)
from processing.visualization.filter_schema import DYNAMIC_COLORS_BGR, PDH_KEY, RCS_KEY, parse_filter_key


UNKNOWN_DYNAMIC_COLOR_BGR = (128, 128, 128)


class Filter_graph:
    def __init__(self, values: dict):
        self.enabled_values = {
            "dynamic_property": set(),
            "ambiguity_state": set(),
            "invalid_state": set(),
        }
        self.pdh_max = int(values.get(PDH_KEY, 3))
        self.rcs_min = float(values.get(RCS_KEY, -20.0))
        self.last_points = ()
        self._load(values)

    def _load(self, values: dict) -> None:
        for key, enabled in values.items():
            parsed = parse_filter_key(key)
            if parsed is None:
                continue
            field, value = parsed
            if field == "pdh":
                self.pdh_max = int(enabled)
            elif field == "rcs":
                self.rcs_min = float(enabled)
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
        if field == "rcs":
            self.rcs_min = float(values[event])
            return
        if field not in self.enabled_values or not isinstance(value, int):
            return
        selected = self.enabled_values[field]
        if values[event]:
            selected.add(value)
        else:
            selected.discard(value)

    @staticmethod
    def _is_missing(value) -> bool:
        if value in (None, MISSING_QUALITY):
            return True
        return isinstance(value, float) and math.isnan(value)

    @classmethod
    def _optional_selection_allowed(cls, value, selected: set[int]) -> bool:
        return cls._is_missing(value) or not selected or value in selected

    def _dynamic_allowed(self, dynamic_property: int | None) -> bool:
        return self._optional_selection_allowed(
            dynamic_property,
            self.enabled_values["dynamic_property"],
        )

    def _rcs_allowed(self, rcs: float | None) -> bool:
        return self._is_missing(rcs) or rcs >= self.rcs_min

    def _color(self, dynamic_property: int | None):
        if self._is_missing(dynamic_property):
            return UNKNOWN_DYNAMIC_COLOR_BGR
        try:
            return DYNAMIC_COLORS_BGR[dynamic_property]
        except (IndexError, TypeError):
            return UNKNOWN_DYNAMIC_COLOR_BGR

    @staticmethod
    def _coordinates_available(x, y) -> bool:
        try:
            return math.isfinite(x) and math.isfinite(y)
        except TypeError:
            return False

    def allowed(self, dyn: int | None, pdh: int, ambg: int, inv: int, rcs: float | None) -> bool:
        pdh_allowed = self._is_missing(pdh) or 0 < pdh <= self.pdh_max
        return (
            self._dynamic_allowed(dyn)
            and self._rcs_allowed(rcs)
            and pdh_allowed
            and self._optional_selection_allowed(
                ambg,
                self.enabled_values["ambiguity_state"],
            )
            and self._optional_selection_allowed(
                inv,
                self.enabled_values["invalid_state"],
            )
        )

    def filter_point_sequence(self, points: Iterable[RadarPoint]):
        all_x, all_y, colors, displayed = [], [], [], []
        for point in points:
            if not self._coordinates_available(point.dist_latitude, point.dist_long):
                continue
            if not self.allowed(
                point.dynamic_property,
                point.pdh,
                point.ambiguity_state,
                point.invalid_flag,
                point.rcs,
            ):
                continue
            all_x.append(point.dist_latitude)
            all_y.append(point.dist_long)
            colors.append(self._color(point.dynamic_property))
            displayed.append(point)
        self.last_points = tuple(displayed)
        return all_x, all_y, colors

    def filter_object_sequence(self, objects: Iterable[RadarObject]):
        all_x, all_y, colors, displayed = [], [], [], []
        for obj in objects:
            if not self._coordinates_available(obj.dist_latitude, obj.dist_long):
                continue
            if not self._dynamic_allowed(obj.dynamic_property) or not self._rcs_allowed(obj.rcs):
                continue
            all_x.append(obj.dist_latitude)
            all_y.append(obj.dist_long)
            colors.append(self._color(obj.dynamic_property))
            displayed.append(obj)
        self.last_points = tuple(displayed)
        return all_x, all_y, colors

    def filter_points(self, messages: Clusters_messages):
        return self.filter_point_sequence(messages.snapshot())

    def filter_objects(self, messages: Objects_messages):
        return self.filter_object_sequence(messages.snapshot())
