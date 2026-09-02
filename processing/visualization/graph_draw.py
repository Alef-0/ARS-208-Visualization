import math

import cv2 as cv
import numpy as np

from processing.visualization.filter_schema import DYNAMIC_PROPERTY_OPTIONS

# Frame dimensions
WIDTH = 800
HEIGHT = 600

MAX_VALUES = 15
DYNAMIC_PROPERTY_LABELS = {
    option.value: option.label for option in DYNAMIC_PROPERTY_OPTIONS
}

# Parameters
# argv[1] = Camera Topic to subscribe


class Graph_radar():
    def __init__(
        self,
        distance_cutoff=MAX_VALUES,
        width=WIDTH,
        height=HEIGHT,
        x_range=MAX_VALUES,
        y_range=MAX_VALUES,
    ):
        self.margin = 50
        self.width, self.height = self._validated_resolution(width, height)
        self.x_range, self.y_range = self._validated_range(x_range, y_range)
        self._update_geometry()
        self.distance_cutoff = self._validated_cutoff(distance_cutoff)
        self.displayed_points = []
        self._refresh_base_image()

    @staticmethod
    def _validated_resolution(width, height):
        width, height = int(width), int(height)
        if width <= 2 * 50 or height <= 2 * 50:
            raise ValueError("Graph width and height must be greater than 100 pixels")
        return width, height

    @staticmethod
    def _validated_range(x_range, y_range):
        x_range, y_range = float(x_range), float(y_range)
        if (
            not math.isfinite(x_range)
            or not math.isfinite(y_range)
            or x_range <= 0
            or y_range <= 0
        ):
            raise ValueError("Graph X and Y ranges must be positive numbers")
        return x_range, y_range

    def _update_geometry(self):
        self.graph_width = self.width - 2 * self.margin
        self.graph_height = self.height - 2 * self.margin
        self.origin_x = self.width // 2
        self.origin_y = self.height - self.margin
        self.x_min, self.x_max = -self.x_range, self.x_range
        self.y_min, self.y_max = 0.0, self.y_range

    def _refresh_base_image(self):
        self.base_image = self.create_base_image()
        self.base_image = self.create_details()

    @staticmethod
    def _validated_cutoff(value):
        cutoff = float(value)
        if not math.isfinite(cutoff) or cutoff <= 0:
            raise ValueError("Point distance cutoff must be a positive number")
        return cutoff

    def set_distance_cutoff(self, value):
        self.distance_cutoff = self._validated_cutoff(value)
        self._refresh_base_image()

    def set_resolution(self, width, height):
        self.width, self.height = self._validated_resolution(width, height)
        self._update_geometry()
        self._refresh_base_image()

    def set_range(self, x_range, y_range):
        self.x_range, self.y_range = self._validated_range(x_range, y_range)
        self._update_geometry()
        self._refresh_base_image()

    def graph_to_pixel(self, x, y):
        pixel_x = int(self.origin_x + (x * self.graph_width) / (self.x_max - self.x_min))
        pixel_y = int(self.origin_y - (y * self.graph_height) / (self.y_max - self.y_min))
        return (pixel_x, pixel_y)

    @staticmethod
    def _tick_step(limit, target_count):
        raw_step = float(limit) / target_count
        magnitude = 10 ** math.floor(math.log10(raw_step))
        normalized = raw_step / magnitude
        factor = next(value for value in (1, 2, 5, 10) if normalized <= value)
        return factor * magnitude

    @staticmethod
    def _positive_ticks(limit, target_count):
        step = Graph_radar._tick_step(limit, target_count)
        count = int(math.floor(limit / step + 1e-9))
        return [step * index for index in range(1, count + 1)]

    @staticmethod
    def _tick_label(value):
        return f"{value:g}"

    def create_base_image(self):
        frame = np.ones((self.height, self.width, 3), dtype=np.uint8) * 255

        # Draw border
        cv.rectangle(frame, (self.margin // 2, self.margin // 2),
                    (self.width - self.margin // 2, self.height - self.margin // 2), (0, 0, 0), 2)

        # Draw X-axis
        cv.line(frame, (self.margin, self.origin_y), (self.width - self.margin, self.origin_y), (0, 0, 0), 2)
        # Draw Y-axis
        cv.line(frame, (self.origin_x, self.height - self.margin), (self.origin_x, self.margin), (0, 0, 0), 2)

        # Draw grid lines (lighter)
        x_grid = self._positive_ticks(self.x_range, 15)
        for x in [*[-value for value in reversed(x_grid)], *x_grid]:
            pixel_x, _ = self.graph_to_pixel(x, 0)
            cv.line(frame, (pixel_x, self.margin), (pixel_x, self.height - self.margin), (200, 200, 200), 1)

        for y in self._positive_ticks(self.y_range, 15):
            _, pixel_y = self.graph_to_pixel(0, y)
            cv.line(frame, (self.margin, pixel_y), (self.width - self.margin, pixel_y), (200, 200, 200), 1)

        # Draw arrows
        # X-axis arrow
        cv.arrowedLine(frame, (self.width - self.margin - 10, self.origin_y),
                        (self.width - self.margin, self.origin_y), (0, 0, 0), 2, tipLength=0.02)
        # Y-axis arrow
        cv.arrowedLine(frame, (self.origin_x, self.margin + 10),
                        (self.origin_x, self.margin), (0, 0, 0), 2, tipLength=0.02)

        # Draw tick marks and labels for X-axis
        x_ticks = self._positive_ticks(self.x_range, 4)
        for x in [*[-value for value in reversed(x_ticks)], *x_ticks]:
            pixel_x, pixel_y = self.graph_to_pixel(x, 0)
            cv.line(frame, (pixel_x, pixel_y - 5), (pixel_x, pixel_y + 5), (0, 0, 0), 1)
            cv.putText(frame, self._tick_label(x), (pixel_x - 10, pixel_y + 20),
                        cv.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

        # Draw tick marks and labels for Y-axis
        for y in self._positive_ticks(self.y_range, 4):
            pixel_x, pixel_y = self.graph_to_pixel(0, y)
            cv.line(frame, (pixel_x - 5, pixel_y), (pixel_x + 5, pixel_y), (0, 0, 0), 1)
            cv.putText(frame, self._tick_label(y), (pixel_x - 25, pixel_y + 5),
                        cv.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

        # Draw origin label
        cv.putText(frame, "0", (self.origin_x - 15, self.origin_y + 20),
                    cv.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

        # Draw axis labels
        cv.putText(frame, "X", (self.width - self.margin + 5, self.origin_y + 5),
                    cv.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
        cv.putText(frame, "Y", (self.origin_x - 5, self.margin - 5),
                    cv.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)

        # Display the frame
        return frame

    def _draw_range_arc(self, image, distance, color, thickness):
        x, y = self.graph_to_pixel(distance, distance)
        center = self.graph_to_pixel(0, 0)
        axes = (x - center[0], center[1] - y)
        if axes[0] > 0 and axes[1] > 0:
            cv.ellipse(image, center, axes, 0, 270 - 60, 270 + 60, color, thickness)

    def create_details(self):
        new_img = self.base_image.copy()
        center = self.graph_to_pixel(0, 0)

        angle_rad = math.radians(30)
        slope = math.tan(angle_rad)

        # Compute endpoints for ±60° diagonals
        end_right = self.graph_to_pixel(self.x_max, self.x_max * slope)
        end_left = self.graph_to_pixel(self.x_min, -self.x_min * slope)
        cv.line(new_img, center, end_left, (0, 0, 0), 1)
        cv.line(new_img, center, end_right, (0, 0, 0), 1)

        # Draw the semicircles
        for distance in self._positive_ticks(self.y_range, 15):
            self._draw_range_arc(new_img, distance, (0, 0, 0), 1)

        if self.distance_cutoff <= self.y_max:
            self._draw_range_arc(new_img, self.distance_cutoff, (0, 0, 255), 2)

        return new_img

    @staticmethod
    def _dynamic_class(point):
        dynamic_property = getattr(point, "dynamic_property", None)
        if dynamic_property is None:
            return "N/A"
        return DYNAMIC_PROPERTY_LABELS.get(
            dynamic_property,
            f"UNKNOWN_{dynamic_property}",
        )

    @staticmethod
    def _object_class(point):
        class_name = getattr(point, "object_class_name", None)
        return class_name or None

    @staticmethod
    def _format_rcs(point):
        rcs = getattr(point, "rcs", None)
        if rcs is None:
            return "N/A"
        try:
            return f"{float(rcs):.1f} dBm²"
        except (TypeError, ValueError):
            return str(rcs)

    def _on_mouse(self, event, pixel_x, pixel_y, _flags, _param):
        if event != cv.EVENT_LBUTTONDOWN:
            return
        if not self.displayed_points:
            print()
            return

        closest = min(
            self.displayed_points,
            key=lambda item: (
                (item["pixel"][0] - pixel_x) ** 2
                + (item["pixel"][1] - pixel_y) ** 2
            ),
        )
        point = closest["point"]
        details = (
            f"x={closest['x']:.2f} m | y={closest['y']:.2f} m | "
            f"class={self._dynamic_class(point)} | rcs={self._format_rcs(point)}"
        )
        object_class = self._object_class(point)
        if object_class:
            details += f" | object_class={object_class}"

        print(f"[RADAR POINT] {details}")

    def show_points(self, x_group, y_group, colors, points=None):
        new_img = self.base_image.copy()
        point_group = tuple(points) if points is not None else ()
        self.displayed_points = []

        for index, (x, y, color) in enumerate(zip(x_group, y_group, colors)):
            if math.hypot(x, y) > self.distance_cutoff:
                continue
            pixel = self.graph_to_pixel(x, y)
            cv.circle(new_img, pixel, 4, color, -1)
            point = point_group[index] if index < len(point_group) else None
            self.displayed_points.append({
                "pixel": pixel,
                "x": x,
                "y": y,
                "point": point,
            })

        cv.namedWindow("RADAR")
        cv.setMouseCallback("RADAR", self._on_mouse)
        cv.imshow("RADAR", new_img)
        cv.waitKey(1)

    def close(self):
        cv.destroyAllWindows()
        cv.waitKey(1)
