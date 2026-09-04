# Sensor integrations

This package contains the hardware and network boundaries of the application:

- Camera RTSP/GStreamer connection and timestamp policy.
- Radar TCP gateway connection and Continental ARS40X CAN decoding.
- DVR GPS polling and Google Maps location handling.

The sensor workers receive commands from `main.py` through dedicated pipes and
return state through the shared status queue. Visualization, filtering,
recording, and playback are implemented in `processing/`.

See `camera/README.md` for the camera pipeline, `radar/README.md` for CAN
framing and radar message assembly, and `gps/README.md` for position polling.

## Files

| File | Responsibility |
| --- | --- |
| `camera/` | RTSP lifecycle, decoder selection, and PTS/NTP timestamps |
| `radar/` | TCP gateway, CAN decoding, frame assembly, configuration, and recording handoff |
| `gps/` | DVR position polling and map URL creation |
