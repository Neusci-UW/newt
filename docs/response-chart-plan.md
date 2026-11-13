# Plan: Add chart menu by axis, and the Response chart

Status: design only, agreed in mock-ups on 12 November 2026. Nothing is coded yet.
Open item: the order of the Add chart menu (section 1).

## 1. Add chart menu, grouped by x axis

The Add chart menu (and the right-click "Add a chart here" menu on the board) groups the
charts by what runs along the x axis. Each chart's y axis is shown faintly, right-aligned
beside its name. The hover tooltip still asks the chart's question, and "(1 open)" counts stay.

Order (to be re-sorted; the y axis follows the dash):

```
Time in the trial (s)
  Response — signal
  Stimulus — signal
  Stimulus fit — signal
  Raster — trial
  Rate — spikes/s
Time from an event (ms)
  Stimulus-triggered rate — spikes/s
  Spike-triggered average — signal
  Waveforms — signal
  V — V
  dV/dt — V/s
Phase of the cycle (°)
  Stimulus cycle — signal
  Response by cycle — signal
  Spikes by cycle — cycle
  Spikes per phase — spikes/cycle
One signal against another
  Response against stimulus — response
  V against dV/dt — dV/dt
A spike property (count)
  AP sizes — events
  Intervals — count
  PCA map — PC2
One value per trial
  DataPad table — —
  Scatter — measurement
```

"Sorting spikes" goes away as a heading; its charts move under "A spike property".

## 2. Traces is renamed Response

- The chart kind and its default name become "Response".
- Saved boards, layouts and .newt files with a "Traces" chart open it as "Response", the same
  way "Blocks" already opens as "Trials".

## 3. Response leaves out the stimulus

- The channel set as the stimulus channel (Channels › Stimulus channel) isn't listed or drawn
  in Response. Changing that setting updates Response.
- When stimuli come from the notes (no stimulus channel), every channel is listed.
- The stimulus has its own Stimulus chart, which can sit above Response on the same time axis.
- Old layouts that had the stimulus ticked open without it.

## 4. Spikes on the Response chart

A new "Spikes" section in Response's settings:

- **Show the spike threshold** (check box): a red dashed line at the threshold, labeled with
  its level (e.g. "threshold 45.2 µV"). Dragging it sets the threshold, as on the Trials chart.
- **Mark each spike with a dot** (check box): a dot on each spike found, in its unit's color.
- **Dot size** (slider), as on the Trials chart.
- Both check boxes start off, and both turn on by themselves the first time spikes are found.
  After that each stays as set.
- The line and dots are drawn only on the channel the Spikes panel sorts (its V row when stacked;
  its own y axis when overlaid). A note under the boxes names that channel.
- **Highlighting units**: when only some units are ticked, their spikes are traced over the
  recording in their unit's color and the rest of the recording fades, as on the Trials chart.

## 5. The Trials chart is removed

- Response takes over its job (sections 3 and 4). It goes from the menu and from the code.
- Saved boards with a Trials chart open it as a Response chart showing the threshold and dots,
  with its channel ticked.

## 6. Measure on any chart

Goal: the Measure tool works on every chart, to fill the DataPad, with the toolbar's Time or
Phase setting choosing the kind of window.

Today a drag works only on charts with `link = "time"` (Time) or `"phase"` (Phase)
(`MeasureTool.unit_of_chart` in `measurecharts.py`); other charts ignore it.

Proposed, by x axis:

- **Time in the trial** (Response, Stimulus, Raster, Rate…): Time works as today. New: in
  Phase, a drag across one cycle makes a phase window that repeats in every cycle.
- **Time from an event** (Stimulus-triggered rate, Spike-triggered average, Waveforms, V,
  dV/dt): new "time from the event" windows in ms, e.g. 5–30 ms after each stimulus or around
  each spike, measured per event and averaged per trial. Phase doesn't apply.
  These windows show only on the event charts, not on time-in-trial charts.
- **Phase of the cycle**: Phase works as today. Time doesn't apply.
- **One signal against another** (Response against stimulus, V against dV/dt): a drag measures
  a range of the x value, e.g. the time spent with V above −20 mV.
- **One value at each x** (histograms: AP sizes, Intervals): a range drag, as elsewhere. The
  DataPad reports it per spike (in the range or not, and its value) or per trial (how many),
  depending on what the DataPad column asks for.
- **2-D point charts** (PCA map, Scatter): a new free-draw measure. Draw around the points of
  interest; the closed shape is the window, and it can be dragged to move it. The DataPad
  reports the points inside, per trial (count, and share of the trial's) or per point.

## 7. Spread shading on every mean

Whenever a chart draws a mean, it shades the spread chosen in Groups › Spread (None, ± SD or
± SEM), in the mean's color, as the stacked Response chart already does. All charts use the
same sample SD and SEM (`analysis.mean_spread`, via `spikecharts.draw_band`).

Already shaded: Response stacked (each group's average), Trials (moving to Response),
Stimulus-triggered rate (across stimuli), Response by cycle.

To add:

- **Response, overlaid layout**: the mean only today (`responsecharts.py:183`).
- **Rate**: the spread across trials in each bin. Also fix the late-bin bug: divide each bin
  by the number of trials that reach it (`analysis.block_rate`).
- **Stimulus cycle**: the mean of the cycles only (`responsecharts.py:654`); spread across cycles.
- **Response against stimulus**: shade around the mean loop, or leave it as a line (a loop's
  band is harder to read; decide when building).
- **Waveforms, V, dV/dt, V against dV/dt**: the spread **across spikes** of each unit (decided).
- **Spike-triggered average**: switch from its own population SD (ddof 0) to `mean_spread`, so
  its band matches the other charts.
- Check Spikes per phase, Spikes by cycle and Scatter, which weren't reviewed in detail.

## 8. Board color

- A **color swatch** beside the **Grid** button in the board's floating toolbar
  (`canvas.py:103`). The Grid button stays as it is.
- Clicking it opens the color picker to choose the board's one color (no presets).
  Right-clicking it offers **Default**, back to the current mode's `ground` color.
- The board gets its own color instead of the theme's `ground` token, which also colors the
  whole window, menus and dialogs (`board.py:658`, `style.py:20` and `:27`). The grid dots are
  drawn a little lighter than the chosen color in dark mode and a little darker in light mode,
  so they stay visible.
- Saved per computer (QSettings), for light and dark mode separately. Not saved with layouts;
  the grid's on/off stays with each layout as today.

## 9. Idea to explore: a sliding measure window (not decided)

A window of set width that steps across the trial, measured at each step, applied to each
trial, to each group's mean, or both. Much of this exists already: a window's "repeat every"
setting makes copies (each its own DataPad column), and the DataPad's Rows setting picks each
trial, each group's average, or mean ± SD. Try those first.

What the mock-up added on top, if the existing pieces aren't enough: Width, Step and From–to
settings; an "Apply to" choice (each trial / group means / both); and a "Measure over time"
chart plotting the value against the window's centre, with one DataPad row per step.

## Notes for building it

- Menu: `GROUPS` and `SIZE` in `canvas.py`; give each chart class a short `y` label and set the
  menu item's text to `"Name\ty label"` (Qt right-aligns the text after the tab).
  `_fill_add_menu` builds both menus.
- Rename and Trials conversion: `_restore` in `canvas.py` (next to the Blocks → Trials case).
- Response: `TracesChart` in `responsecharts.py` (`channels_changed`, `ticked`, `draw`).
  Override `stimulus_changed` to drop the stimulus channel and redraw.
- Threshold, dots, unit highlight: move them from `BlocksChart` in `spikecharts.py`
  (`threshold_changed`, `_thr_dragged`, `units_changed`, `_draw_spikes`, `_event_heights`).
  In overlaid mode, `MultiAxis.add` returns the view to put them in.
- Tests: about 60 lines in `tests/test_analysis.py` use the Trials chart, and several use
  "Traces" (also `test_newtfile.py`, `test_argusfile.py`, `test_layout.py`, and the layouts
  listed at `test_analysis.py:975`). The help text in `helpview.py:257` mentions the Trials chart.
- The "Align to" tooltip on Stimulus-triggered rate says trains start "where the Stimulus chart
  marks it"; the Stimulus chart only marks onsets from notes. Fix the wording while here.
