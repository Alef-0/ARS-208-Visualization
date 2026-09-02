# Processing and persistence

This package contains the parts that consume sensor data after acquisition:

- radar point filtering and visualization;
- PCD and JPEG recording;
- recording metadata and camera/radar association;
- manual paired snapshots;
- PCD reading;
- time-based recording playback and step-based snapshot playback.

See `recording/README.md` for persistent formats and queue behavior,
`visualization/README.md` for filtering and drawing, and `playback/README.md`
for the two playback modes.

## Files

| File | Responsibility |
| --- | --- |
| `visualization/` | Shared filter schema, filtering, and OpenCV radar plotting |
| `recording/` | PCD/JPEG persistence, metadata, readers, and manual snapshots |
| `playback/` | Time-based recording playback and step-based snapshot playback |
