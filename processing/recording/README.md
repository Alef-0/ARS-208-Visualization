# Capture and recording

This package defines the persistent radar/camera recording format and the
workers that create, read, and copy paired observations.

## Files

- `point_cloud_recorder.py` writes cluster or object frames as PCD and manages
  per-group recording sessions.
- `camera_snapshot_recorder.py` writes asynchronous JPEG sequences and detailed
  calibration timing evidence.
- `point_cloud_reader.py` restores supported PCD schemas as typed radar points.
- `manual_snapshot.py` appends one radar/image pair to a compatible folder.
- `__init__.py` exports the main recorder, reader, and snapshot classes.

## Normal recording folders

Each selected radar group gets its own folder named
`recording_<A|B|C>_<timestamp>`. A folder may contain:

| File | Meaning |
| --- | --- |
| `frame_NNNNNN.pcd` | One complete cluster or object radar frame |
| `camera_NNNNNN.jpg` | One camera frame; its number follows the camera sequence |
| `recording.json` | Ordered radar records plus optional paired camera fields |
| `timestamps.json` | Compatibility mapping from PCD filename to radar time |

`recording.json` records these fields for each radar frame:

```json
{
  "point_cloud": "frame_000001.pcd",
  "recorded_at": "ISO-8601 radar time",
  "frame_type": "cluster or object",
  "camera_frame": "camera_000001.jpg or null",
  "camera_recorded_at": "ISO-8601 camera time or null",
  "camera_delay_ms": 109.0,
  "synchronization_error_ms": 0.0
}
```

JSON updates use a temporary file followed by replacement so readers do not
normally observe a partially written document. Metadata is flushed at most
about once per second during recording and forced at shutdown.

## PCD schemas

Cluster frames contain ID, longitudinal/lateral distance and velocity, dynamic
property, RCS, false-alarm category, ambiguity state, and invalid-state code.

Current object frames add RMS/quality values, measurement state, probability
of existence, acceleration, object class, orientation, size, and collision
region flags. `PointCloudReader` also accepts the earlier object schema that
ends after probability of existence. Missing floating values are stored as
NaN; missing integer qualities use `0xFFFFFFFF`.

## Camera-to-radar association

Camera frames and radar frames are produced independently. When the radar
recorder receives a saved camera notification, it computes:

```text
target radar time = camera captured time - configured camera delay
synchronization error = chosen radar time - target radar time
```

The camera frame is attached to the closest radar record that does not already
have a camera frame. Pending camera notifications wait until a new-enough radar
frame exists, or are resolved against available records when recording stops.

The default delay is 109 ms. Metadata stores the actual value used so that a
recording remains interpretable if the setting changes later.

## Queue and failure behavior

Radar PCD writing uses a bounded queue of 64 frames per selected group. A full
queue becomes a recording error and stops the session; radar frames are not
silently discarded.

The JPEG writer uses a bounded queue of eight selected frames. A full queue
drops that image, increments an explicit counter, and reports a warning while
the recording continues. PTS-estimated upstream losses and invalid-timestamp
rejections are counted separately.

## Manual snapshots

`ManualSnapshotWriter` creates or appends a synchronized PCD/JPEG pair using
the normal `recording.json` and `timestamps.json` format. The destination must:

- already exist;
- contain both metadata files or neither one;
- contain no unrelated JSON files.

Indexes are chosen after scanning both existing files and metadata, so gaps do
not cause older pairs to be overwritten. If writing fails, newly created PCD
and JPEG files are removed before the error is returned.

In live capture, the radar worker keeps three seconds of complete frames and
selects the one nearest the delayed camera time. A residual error over 500 ms
rejects the snapshot.

## Playback loading

`load_recording_entries()` prefers `recording.json`, supplements it from
`timestamps.json`, and finally discovers unreferenced PCD and `camera_*` image
files. Missing files are skipped, and file modification time is the fallback
when metadata has no timestamp. Entries are sorted by effective recorded time.

Normal playback follows the original intervals, draws any PCD frame, displays
any image, and supports restart or five-second seeks. Mixed and single-modality
entries are allowed.

Snapshot playback can require paired PCD/image entries. It supports automatic
advance, pause, previous/next stepping, live filter changes, and saving the
currently displayed pair to a snapshot destination.

## Calibration-only files

A channel-4 calibration folder contains JPEGs plus:

| File | Purpose |
| --- | --- |
| `camera_timestamps.jsonl` | Append-only timing journal, one object per saved frame |
| `camera_timestamps.json` | Materialized JSON array of the journal |
| `camera_recording_summary.json` | Start/stop information and saved/lost counters |

Each timing row retains raw PTS, pipeline running time, PTS-derived time,
camera NTP, host receipt, hybrid attempted capture time, the configured latency
adjustment, corrected time, and save time. The JSONL file is the more resilient
record if a run ends before the final JSON array is materialized.

## Compatibility rules

- Keep `recording.json` and `timestamps.json` readable together; manual
  snapshots and both playback modes depend on them.
- Keep support for cluster, legacy-object, and current-object PCD schemas unless
  old recordings are intentionally retired.
- Do not treat the numeric suffixes of `frame_*.pcd` and `camera_*.jpg` as proof
  of synchronization. Use `recording.json`.
- Preserve raw times and the applied delay when changing synchronization logic;
  derived associations alone cannot be recalculated later.
