# Visualization and filtering

This package filters and renders radar observations.

- `filter_schema.py` defines the shared filter fields, defaults, and value
  normalization used by the interface and radar worker.
- `graph_filter.py` applies range, quality, state, and classification filters
  to cluster and object observations.
- `graph_draw.py` renders the accepted points and radar context with OpenCV.
The package does not acquire sensor data or save recordings. Those concerns
remain in `sensors/radar/` and `processing/recording/` respectively.


Calibration image inspection is documented in
[calibration/inspection](../../calibration/inspection/README.md).
