# Playback

This package presents previously saved radar and camera observations.

- `playback.py` reads recording metadata and advances through a recording by
  its saved timestamps.
- `snapshot_playback.py` provides paused pair inspection, previous/next
  navigation, and copying the current pair.

The file readers and persistent recording schemas live in
`processing/recording/`.
