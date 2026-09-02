# Radar connection and CAN decoding

This package talks to the network CAN gateway, decodes Continental ARS40X
messages into radar frames, sends configuration commands, and feeds live
display and recording.

## Transport

`connection_communication.py` connects to `192.168.1.101:2323` over TCP. The
gateway stream is split into fixed 23-byte records using the little-endian
layout `<BBIQ8sB>`:

| Field | Meaning in this code |
| --- | --- |
| byte 0 | CAN data length |
| byte 1 | gateway flags |
| bytes 2-5 | CAN ID |
| bytes 6-13 | gateway timestamp |
| bytes 14-21 | eight-byte CAN payload |
| byte 22 | radar channel |

Channels 1-3 map to groups A-C. The gateway timestamp is decoded and displayed
by `can_data.__repr__()`, but the current radar frame recorder timestamps a new
frame with host wall time when its status packet is handled.

## Message modules

- `message_common.py` validates eight-byte CAN payloads and defines the missing
  integer-quality sentinel.
- `cluster_messages.py` decodes `0x701` cluster general data and `0x702`
  cluster quality data, merging packets by cluster ID.
- `object_messages.py` decodes the `0x60A` object status and `0x60B`-`0x60E`
  object detail packets, merging them by object ID.
- `connection_packages.py` re-exports the message API for older imports and
  encodes `0x200` configuration commands / decodes `0x201` radar state.
- `connection_main.py` owns the radar worker, frame boundaries, plot updates,
  recording, snapshots, and worker commands.

## Frame assembly

`0x600` marks the start of a cluster frame and `0x60A` marks the start of an
object frame. When the next start marker arrives for a channel, the previous
frame is considered complete. The worker then:

1. plots it if that channel is selected;
2. copies it into a three-second history for snapshot matching;
3. queues it to the corresponding PCD recorder if recording is ready;
4. clears the per-channel message accumulator.

Cluster quality packets and object detail packets may arrive separately, so
their accumulator classes merge all fields by target ID before `snapshot()`
returns an immutable copy. A point/object without general position data is not
included in the completed frame.

## Configuration

The GUI can send maximum distance, transmit power, output type, RCS threshold,
quality data, extended data, and control-relay settings to one group or all
three. The same command can be sent as a runtime change or with the non-volatile
save flag.

There is a deliberate 500 ms delay after each gateway send because the source
comment says the Vector interface needs time to register the packet.

## Recording and snapshots

One `RadarRecordingSession` owns one PCD recorder per selected channel. Radar
frames are submitted only after the first complete post-start frame boundary,
which avoids writing an accumulator that began before recording was requested.

For a manual snapshot, the camera supplies encoded JPEG bytes and its
timestamp. The radar worker subtracts the configured camera delay, searches
the selected channel's recent complete frames, rejects a residual over 500 ms,
and asks `ManualSnapshotWriter` to persist the pair.

## Current verification boundary

Tests cover packet bit layouts, data scaling, field merging, configuration
compatibility, PCD persistence, and snapshot selection with synthetic packets.
They do not verify the actual gateway framing, message order, channel mapping,
or whether host receipt time is preferable to the gateway timestamp on the
installed hardware.
