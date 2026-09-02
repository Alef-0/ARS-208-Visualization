# Camera timestamp display

`calibration_screen_clock.py` owns one persistent Pygame display surface. The
number of retained barcodes is configurable from **1 to 4**, default **3**.
Positions still advance clockwise through all four quadrants (top-left,
top-right, bottom-right, bottom-left); this changes history length, not FPS or
the number of positions. Expired quadrants, including their timestamp text,
are cleared to the dark background. A four-pixel pure-white outline moves to
the newest barcode. The outline is separated from its quiet zones by a dark gap.

With 3 markers, the next quadrant is always empty after startup, providing
evidence of a partly drawn next marker or recording artifacts. With 1 or 2,
there are respectively 3 or 2 empty quadrants; with 4, none are empty. The
**Visible barcodes** control in the Calibration tab applies to the next run
and is disabled while the clock is active. Standalone callers can pass
`--visible-frames 3`; Python callers use `visible_frames=3`.

With the calibration window focused, press **P** to pause or resume. Pausing
holds the exact canvas, timestamps, empty quadrants and white outline without
drawing or flipping; take the screenshot while it is held. Controls remain
responsive, including Q/Escape and the application's stop request. Camera
capture/recording continues independently. On resume, the display starts a
fresh timing schedule without counting the intentional pause as missed periods
or trying to catch up. The first resumed interval has no previous comparison.
This is an inspection aid: a held timestamp is not a valid live delay reading.

`ean13.py` precomputes digit/guard bar rectangles for each panel size and merges
contiguous dark modules. Only the replacement quadrant, expired quadrant (if
any), and previous outline are modified. EAN-13 still encodes 12 digits of monotonic **milliseconds** plus
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

## Analysis

`marker_analysis.py` registers a complete rectangular outline to one of the
four saved layouts, validates that decoded timestamps belong to the journal's
correct quadrants, and retains camera-coordinate barcode locations. It does
not assume that the camera image center is the monitor center. Once registered,
it reuses the screen-plane transform. Several scanlines independently validate
EAN guards, digit parity and checksum; differing valid values are rejected as
transition evidence rather than majority-voted away.

The analyzer labels each accepted camera frame:

- `direct`: the outlined marker decoded successfully;
- `inferred_one_period`: the outlined code was unreadable, but its immediate
  predecessor decoded, the journal sequence agrees, and timing is stable.
  Exactly one measured median flip interval is added to the predecessor's
  decoded millisecond value. At least eight clean intervals are required.

No two-period guesses are made. A one-marker history has no predecessor to
use if its only code is unreadable. Missing/multiple outlines, mixed generations,
timing gaps and unknown payloads are excluded with reasons and observations in
the JSON report. Direct and inferred distributions are also reported separately.
These safeguards are conservative heuristics, not proof of exposure time.
Markers held for a pause are excluded as `display_marker_held_for_pause`, even
when seen just before the pause: the same optical payload cannot distinguish
that original presentation from the held image. The first resumed marker is
also excluded from timing selection, and a held marker cannot supply the
one-period fallback. Earlier journals without pause records remain supported.
The report identifies the next quadrant and the quadrants expected to be blank.
Bright fragments occupying over 2% of a blank quadrant's sampled interior are
flagged as `expected_blank_quadrant_has_content`, even if they do not form a
valid barcode. The threshold is relative to the decoded panels' dark/light
levels; this is an artifact-detection heuristic requiring camera validation.

Automatic registration assumes an upright, non-mirrored view with a complete,
resolvable white outline. Glare, lens distortion, motion blur, severe perspective
or a cropped monitor may prevent it. Supply the **monitor's four outer pixel
corners** in a camera image, in top-left, top-right, bottom-right, bottom-left
order, when automatic registration fails:

```bash
python3 analyze_calibration_recording_offset.py /path/to/recording \
  --screen-corners TLx TLy TRx TRy BRx BRy BLx BLy
```

Replace those eight placeholders with measured numeric coordinates. This
explicit mapping also permits camera roll. If the camera moves, one fixed
mapping is no longer valid. Old recordings without a display journal keep the
historical OpenCV freshest-code behavior, explicitly labeled `legacy_freshest`.

## Standalone use

```bash
python3 CALIBRATION/calibration_screen_clock.py --refresh-hz 60 \
  --journal /path/to/new-display-timestamps.jsonl
```

`--width`, `--height` and `--windowed` remain available. The journal is optional
for display-only use and is never overwritten. For new recording analysis,
keep it as `display_timestamps.jsonl` beside the camera timing files. Press
P to pause/resume, or Escape/Q to close. Automated tests use off-screen generated pixels and fake
processes; the user must verify the actual monitor outline/rotation and camera
readability, then validate timing using a real recording.
