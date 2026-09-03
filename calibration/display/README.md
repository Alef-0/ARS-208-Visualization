# Camera timestamp display

`clock.py` owns one persistent Pygame display surface. The
number of retained barcodes is configurable from **1 to 4**, default **3**.
Positions still advance clockwise through all four quadrants (top-left,
top-right, bottom-right, bottom-left); this changes history length, not FPS or
the number of positions. Expired quadrants, including their timestamp text,
are cleared to the dark background. A four-pixel pure-white underline below
the newest time identifies the current marker. It sits outside the barcode
and its quiet zones. At 1920×1080, horizontal/vertical panel gaps are now
48/64 pixels (previously 32/32), retaining eight-pixel barcode modules.
The geometry journal records `indicator_style: underline` and each underline
rectangle. Old journals containing outlines remain readable.

With 3 markers, the next quadrant is always empty after startup, providing
evidence of a partly drawn next marker or recording artifacts. With 1 or 2,
there are respectively 3 or 2 empty quadrants; with 4, none are empty. The
**Visible barcodes** control in the Calibration tab applies to the next run
and is disabled while the clock is active. Standalone callers can pass
`--visible-frames 3`; Python callers use `visible_frames=3`.

With the calibration window focused, press **P** to pause or resume. Pausing
holds the exact canvas, timestamps, empty quadrants and white underline without
drawing or flipping; take the screenshot while it is held. Controls remain
responsive, including Q/Escape and the application's stop request. Camera
capture/recording continues independently. On resume, the display starts a
fresh timing schedule without counting the intentional pause as missed periods
or trying to catch up. The first resumed interval has no previous comparison.
This is an inspection aid: a held timestamp is not a valid live delay reading.

`ean13.py` precomputes digit/guard bar rectangles for each panel size and merges
contiguous dark modules. Only the replacement quadrant, expired quadrant (if
any), and previous underline are modified. EAN-13 still encodes 12 digits of monotonic **milliseconds** plus
its check digit; labels show grouped seconds and three fractional digits.

## Presentation and pacing

There is no rendering worker, prefetch queue, or alternate fullscreen canvas.
The single-threaded loop waits on an absolute monotonic deadline, samples time
immediately before drawing the new marker, and calls `pygame.display.flip()`
once. A Pygame wait handles the coarse delay; only the last sub-millisecond tail
spins. `pygame.time.Clock.tick(0)` records cadence without adding another limiter.
The render budget adapts to the rolling 95th-percentile draw duration plus a
0.75 ms submission margin, bounded to half a period. Expired deadlines are
advanced on the existing grid and reported, not rendered in a catch-up burst.

Default cadence is 60 Hz. Plausible blocking flip intervals refine the nominal
period and phase; a missed 33 ms interval never silently changes 60 Hz to 30 Hz.
Use `--refresh-hz` for another monitor rate. This inference does **not** prove
vsync or identify physical refresh rate independently of the software limiter.

The chosen presentation API is `flip()`. In Pygame 2.6.1's `SCALED` renderer,
`display.update(rect)` calls `pg_flip` before examining its rectangle argument,
so it cannot reduce the texture upload. Dirty-region **drawing** is the saving.
The obsolete `DOUBLEBUF` flag was removed; in Pygame 2 it applies only to OpenGL.
Vsync remains requested via `SCALED` and `vsync=1`, not guaranteed.

Sources: [Pygame display reference](https://www.pygame.org/docs/ref/display.html),
[Pygame 2.6.1 display implementation](https://github.com/pygame/pygame/blob/2.6.1/src_c/display.c#L1498),
[Pygame Clock reference](https://www.pygame.org/docs/ref/time.html).

## Recording and timing evidence

The GUI creates the recording destination before launching the display.
`display_timestamps.jsonl` begins immediately; camera JPEG capture begins after
three seconds. Closing the display flushes its journal and stops capture, but
does not close camera 4. Closing before three seconds can leave a display-only
folder, intentionally preserved as evidence.

The journal contains a geometry/session header, one row per presented marker,
and a final summary. Rows retain:

- clockwise sequence index and quadrant;
- raw monotonic timestamp sampled before drawing (`marker_ns`);
- intended deadline, pre-flip submission and flip-return observations;
- observed interval, estimated period and rendering budget;
- late submissions, missed-period candidates and irregular intervals.

New frame rows also split missed-period candidates into `skipped_before_render`
and `missed_after_submit`; their sum remains `skipped_periods`.
When a timing problem is observed, the recorder appends a `kind: "timing_event"`
row after the frame row. It records the detection time, the current update's
raw timing and issue codes, and `affected_display_indices` linking the update
to its preceding marker. The preceding marker may have remained outlined too
long while waiting for this update. The event records signed submission/return
lateness and any excess flip interval, all in nanoseconds. Long and short
irregular intervals are distinguished. These are potential associations, not
proof that every image of either marker was exposed during the problem.

Event rows do not increment the marker counter. They use the existing buffered
journal, outside drawing/presentation, and retain only the previous frame in
memory. Resume boundaries are catalogued separately as `resumed_after_pause`;
the intentional pause is not counted as an irregular or missed display period.
The final summary includes `timing_events` and `closed_at_monotonic_ns`.

The session header also records `visible_frames`, so analysis applies the same
history length. Older v2 journals without that field are interpreted as four
visible markers, matching the first persistent-canvas implementation.

Pause/resume transitions are flushed as `kind: "pause"` rows containing
`paused`, `monotonic_ns`, and `last_frame_index` (-1 before the first marker).
They do not increment the presented-marker counter. The first marker after
resume carries `resumed_after_pause: true`.

The final console summary has four counters:

- **Presented N markers**: completed marker flip calls, including warm-up but
  excluding the initial blank display; not camera frames or verified panel refreshes.
- **Missed-period candidates**: scheduled periods skipped before rendering plus
  periods estimated missed from a late flip return. These are software estimates,
  not confirmed monitor or camera frame drops.
- **Irregular intervals**: successive flip returns outside 75%-125% of the
  estimated period (about 12.5-20.83 ms at 60 Hz). Both too-short and too-long
  intervals count; the first marker has no previous interval to compare.
- **Late submissions**: calls submitted to `flip()` after their target deadline.

The counters overlap: one slow update may contribute to all three timing
warnings, so do not add them to calculate unique lost frames. “Flip timing is
not a physical scanout measurement” means the program timestamps API calls,
not when an individual monitor row emitted light or when the camera exposed it.

Buffered journal writes occur after the flip, outside the sample/draw/present
critical section. I/O stalls are still possible and are reflected in timing
warnings. These are display-side observations, not camera-frame drop counts.

The payload is **not** physical light-emission time. A logical buffer swap
cannot eliminate panel scanout or camera rolling exposure. The offset still
includes residual submission-to-light latency and up to one millisecond of
payload quantization. Do not silently apply a measured flip-return delay as a
physical scanout correction or automatically change the camera's 109 ms offset.

## Standalone use

```bash
python3 -m calibration.display.clock --refresh-hz 60 \
  --journal /path/to/new-display-timestamps.jsonl
```

`--width`, `--height` and `--windowed` remain available. The journal is optional
for display-only use and is never overwritten. For new recording analysis,
keep it as `display_timestamps.jsonl` beside the camera timing files. Press
P to pause/resume, or Escape/Q to close. Automated tests use off-screen generated pixels and fake
processes; the user must verify the actual monitor underline/rotation and camera
readability, then validate timing using a real recording.

See [decoding and selection rules](../decoding/README.md) for interpretation.
