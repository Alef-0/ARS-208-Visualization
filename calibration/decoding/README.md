# Barcode decoding and timing selection

Manual panel selection is the default before full-recording analysis. The fixed
regions, learned fallback, and current-indicator checks all use OpenCV.
See [marking and inspection](../inspection/README.md) for the workflow.


OpenCV is the only barcode decoder. The ZBar wrapper, custom bit/scanline
decoder and unused rendering wrapper have been removed. UPC-A results regain
their leading zero; only valid EAN-13 checksums with exact journal matches
and causal marker times are usable.

Before manual marking, the viewer learns barcode quadrilaterals from successful journal-matched reads
in each image variant, keeping a rolling median of up to 24 observations per
corner. It decodes three separate interior bands using OpenCV's `decodeWithType`.
This avoids searching for a combined top/bottom rectangle on every retry and
keeps borders and time text out of regional decoding. It learns positions anew
per recording, resets on seeking or confirmed movement in multiple panels,
and expires regions after 60 analyzed frames without fresh location support.
Oversized merged boxes cannot replace a stable panel location. Regions carry
no previous payload: unreadable pixels remain unreadable.

Grayscale and local contrast enhancement (CLAHE) are enabled together by
default. **Binary retry** adds local Otsu thresholding on learned bands;
it is off by default because it added no codes in the `calibration_third`
sensitivity sample. Whole-frame Otsu was substantially worse. OpenCV already
performs binarization inside its barcode decoder; extra thresholding can lose
useful grayscale information. See the [OpenCV barcode implementation guide](https://github.com/opencv/opencv/blob/4.x/doc/tutorials/others/barcode_detect_and_decode.markdown).

**Undistortion alpha** now uses `getOptimalNewCameraMatrix`, with 0 as the
default and 0.25, 0.5, 0.75 and 1 available. Changing alpha reloads decoding
state and updates both image and overlay geometry. With manual panels, points
are mapped to the new alpha and decoding uses their undistorted regions.
Before manual marking, **Also decode undistorted**
combines evidence from the original image and the selected alpha; it does not
blend pixels, average timestamps, or pick an alpha separately for each frame.
The [OpenCV calibration reference](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html)
documents the alpha crop/field-of-view tradeoff. Calibration resolution must
match the recording; files without `image_size` are explicitly treated as an
assumption, not verified camera calibration.

Different valid generations in a single panel, including disagreement between
image variants, block offset selection. Additional decoded codes can expose
camera artifacts previously missed; more reads do not mean more trustworthy
timing samples. The viewer treats old outline recordings as provisional,
ignoring their borders as evidence of which marker is current.

For stricter selection, `markers.py` registers the screen and reads
the current indicator separately from barcode pixels. New layouts use closed
barcode-panel contours for registration; legacy outlines remain supported by
the CLI analyzer. Geometry is verified against journal quadrants, not the
camera image midpoint. Three OpenCV bands replace custom scanline parsing;
differing valid values remain transition evidence, never majority-voted away.

The analyzer labels each accepted camera frame:

- `direct`: the current indicated marker decoded successfully;
- `inferred_one_period`: the current code was unreadable, but its immediate
  predecessor decoded, the journal sequence agrees, and timing is stable.
  Exactly one measured median flip interval is added to the predecessor's
  decoded millisecond value. At least eight clean intervals are required.

No two-period guesses are made. A one-marker history has no predecessor to
use if its only code is unreadable. Missing/multiple current indicators, mixed generations,
timing gaps and unknown payloads are excluded with reasons and observations in
the JSON report. Direct and inferred distributions are also reported separately.
These safeguards are conservative heuristics, not proof of exposure time.
For both direct and inferred readings, analysis checks the selected marker's
arrival **and its following replacement**. A normally presented marker A is
excluded if update B has missed-period candidates, a late submission, an
irregular interval, or a resume boundary. This closes the case where A remained
on screen too long but only B's journal entry recorded the delay. Raw successive
flip-return times are also checked against 75%-125% of the replacement update's
period, even when the boolean irregular flag is absent or false. Older journals
can use the recorded interval and measured median period instead.

All images selecting the affected marker are conservatively excluded, including
ones that might have been exposed before the delay. Camera receipt/PTS time is
not used to guess which side of the delay an exposure belongs to. No missed
period is added to the decoded timestamp as a correction. An absent following
update (including the final marker of a cleanly closed journal), or unavailable
replacement interval/period, produces an `unknown` assessment and excludes the
measurement. Closing the journal does not establish when the last marker stopped
emitting light. The one-period fallback also checks its source predecessor.

Each assessed image retains `display_timing`: `clean`, `suspect`, or `unknown`,
specific `issue_codes`, the selected marker and replacement timing, and a
flip-return-based `hold_interval_proxy_ns` / `excess_hold_proxy_ns`. For example,
`replacement_irregular_interval_long` identifies a potentially overlong hold;
`marker_late_submission` identifies a late arrival. Images that cannot be
registered/selected are `not_assessed`, never silently marked clean.

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

Automatic registration assumes an upright, non-mirrored view with resolvable
panel geometry and current indicator. Glare, lens distortion, motion blur, severe perspective
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

Shared modules: `opencv.py` reads codes, `markers.py` matches journal evidence
and selects the current marker, `regions.py` validates saved manual selections,
and `geometry.py` handles lens correction and coordinate mapping.
