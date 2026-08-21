import math

import cv2 as cv
import numpy as np

from INTERFACE.filter_schema import DYNAMIC_PROPERTY_OPTIONS

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
    def __init__(self, distance_cutoff=MAX_VALUES):
        # Define graph parameters
        self.margin = 50
        self.graph_width = WIDTH - 2 * self.margin
        self.graph_height = HEIGHT - 2 * self.margin
        self.origin_x = WIDTH // 2
        self.origin_y = HEIGHT - self.margin
        self.x_min, self.x_max = -MAX_VALUES, MAX_VALUES
        self.y_min, self.y_max = 0, MAX_VALUES
        self.distance_cutoff = self._validated_cutoff(distance_cutoff)
        self.displayed_points = []
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
        self.base_image = self.create_base_image()
        self.base_image = self.create_details()

    def graph_to_pixel(self, x, y):
        pixel_x = int(self.origin_x + (x * self.graph_width) / (self.x_max - self.x_min))
        pixel_y = int(self.origin_y - (y * self.graph_height) / (self.y_max - self.y_min))
        return (pixel_x, pixel_y)

    def create_base_image(self):
        frame = np.ones((HEIGHT, WIDTH, 3), dtype=np.uint8) * 255

        # Draw border
        cv.rectangle(frame, (self.margin // 2, self.margin // 2),
                    (WIDTH - self.margin // 2, HEIGHT - self.margin // 2), (0, 0, 0), 2)

        # Draw X-axis
        cv.line(frame, (self.margin, self.origin_y), (WIDTH - self.margin, self.origin_y), (0, 0, 0), 2)
        # Draw Y-axis
        cv.line(frame, (self.origin_x, HEIGHT - self.margin), (self.origin_x, self.margin), (0, 0, 0), 2)

        # Draw grid lines (lighter)
        for x in range(self.x_min, self.x_max + 1):
            if x == 0:
                continue
            pixel_x, _ = self.graph_to_pixel(x, 0)
            cv.line(frame, (pixel_x, self.margin), (pixel_x, HEIGHT - self.margin), (200, 200, 200), 1)

        for y in range(self.y_min, self.y_max + 1):
            if y == 0:
                continue
            _, pixel_y = self.graph_to_pixel(0, y)
            cv.line(frame, (self.margin, pixel_y), (WIDTH - self.margin, pixel_y), (200, 200, 200), 1)

        # Draw arrows
        # X-axis arrow
        cv.arrowedLine(frame, (WIDTH - self.margin - 10, self.origin_y),
                        (WIDTH - self.margin, self.origin_y), (0, 0, 0), 2, tipLength=0.02)
        # Y-axis arrow
        cv.arrowedLine(frame, (self.origin_x, self.margin + 10),
                        (self.origin_x, self.margin), (0, 0, 0), 2, tipLength=0.02)

        # Draw tick marks and labels for X-axis
        for x in range(self.x_min, self.x_max + 1, 5):
            if x == 0:
                continue  # Skip origin
            pixel_x, pixel_y = self.graph_to_pixel(x, 0)
            cv.line(frame, (pixel_x, pixel_y - 5), (pixel_x, pixel_y + 5), (0, 0, 0), 1)
            cv.putText(frame, str(x), (pixel_x - 10, pixel_y + 20),
                        cv.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

        # Draw tick marks and labels for Y-axis
        for y in range(self.y_min, self.y_max + 1, 5):
            if y == 0:
                continue  # Skip origin
            pixel_x, pixel_y = self.graph_to_pixel(0, y)
            cv.line(frame, (pixel_x - 5, pixel_y), (pixel_x + 5, pixel_y), (0, 0, 0), 1)
            cv.putText(frame, str(y), (pixel_x - 25, pixel_y + 5),
                        cv.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

        # Draw origin label
        cv.putText(frame, "0", (self.origin_x - 15, self.origin_y + 20),
                    cv.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

        # Draw axis labels
        cv.putText(frame, "X", (WIDTH - self.margin + 5, self.origin_y + 5),
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
        for i in range(1, self.y_max + 1):
            self._draw_range_arc(new_img, i, (0, 0, 0), 1)

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

        print("========== RADAR POINT ==========")
        print(details)
        print("=================================")

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
