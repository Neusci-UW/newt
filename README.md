
# Argus

Application designed to visualize recordings from data acquisition systems for students within the University of Washington's undergraduate neuroscience program. Designed for and tested with NI USB-6003 from National Instruments using the NIDAQmx driver. 

## Why/what?
  
The UW undergraduate neuroscience program has used an A-M Systems 3000 AC/DC Differential Amplifier, a Humbug 60 Hz Noise Eliminator, and an AD Instruments PowerLab 4/26 Digitizer in concordance with LabChart 7 to record and analyze signals recorded from different model organisms. By doing so, students can learn about different information processing modalities in these model organisms to build neurobiology fundamentals. However, this system is dated and difficult to use on different operating systems. Argus is a flexible Python-based tool to transform signals into a computer-interpretable form, visualize signals, and enables efficient data analysis. This is done by replacing the PowerLab NI USB-6003 and LabChart dependence with Argus.

## Install
Requires Python 3.11+. For recording, install the
[NI-DAQmx driver](https://www.ni.com/en/support/downloads/drivers/download.ni-daq-mx.html) first.
 
```
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -e ".[nidaqmx,dev]"
```
 
## Run
```
neusci-daq -c config.toml                        # with the DAQ
neusci-daq -c config.toml --backend simulated    # without hardware
```
Edit `config.toml` to set the device, channels, sample rate and scaling.
 
To try review mode with sample data: `python examples/make_demo_session.py demo`, then
**File › Open…** the `demo` folder.

## Features

- **Acquisition**:
  - live multichannel recording, with comments;
  - a Blocks panel that sorts recordings into pages by stimulus;
  - overlaid blocks and channels.
- **Stimulator and Protocols**:
  - stimuli and pulse trains, with units;
  - protocols that step a setting and measure each response.
- **Analysis channels** (**+ Analysis channel**, like LabChart's channel calculations): pick a source
  channel and a calculation. Analysis channels can build on each other, and **Calculations…** shows
  them all as formulas.
  - Cyclic measurements: rate meter (per second or per minute), period, cyclic height and event count.
  - Filters: low-pass, high-pass, band-pass, band-stop and notch.
  - Moving windows: smoothing, RMS and envelope.
  - Derivative, integral and arithmetic: for example `bandpass(Ch1, 300, 3000)` or `Ch1 - Ch2`.
- **Analysis**:
  - One canvas of charts: open any chart from Add chart, snap it beside or under another, stack it as a tab, or pull it onto a second screen. Spike Sorting, Response and Waveforms are built-in layouts; save your own, and lock one for a class.
  - Any chart can be open more than once, each copy with its own name, settings and units. Pin a copy to chosen trials while the others follow the selection. The ⚙ on a chart opens its settings in a window, with a ? button for an explanation and glossary.
  - Only the browser stays beside the charts, with a check box on every group and trial to hide it (colour maps spread across what's left) and groups you can drag or sort by frequency or amplitude: pick a group to see its average, or drag across trials to see those trials. What every chart shares opens in windows: Channels has the stimulus and analyzed channels, and Groups has Spread and the colours of groups and trials (editable, or from a colour map).
  - An amplifier gain per channel to divide by, and a fit that cleans up a noisy or coarse recorded stimulus, both applied as data is read: the recordings never change. Trials save the stimulus settings sent, so the known waveform can be lined up with a recording.
  - Finding and sorting action potentials, with each unit's tick, colour and opacity.
  - The spikes found, the units and DataPad's rows are saved with the session, beside its recordings.
  - Every chart has + and − to zoom and arrows to move, by each axis, while the mouse is over it.
  - Spike charts: overlays and averages, with ± SD or SEM across trials; the stimulus; raster, rate, a PCA map of the units, waveforms, and size and ISI histograms.
  - Response charts: channels and derivatives, PSTH and spike-triggered averages, and stimulus cycles: the repeats overlaid by phase, with a raster per cycle, a phase histogram with vector strength, and a loop of response against stimulus. Charts against time can swap their axes for draggable scale bars.
  - Waveform charts: the action potentials as a phase plot of V against dV/dt, and against time.
- **Saving**: File › Save puts everything in one `.argus` file: the recordings, groups, analysis, layouts and chart settings, the tabs and windows open, the stimulus and protocol, and the simulated network. Opening it (or double-clicking it, with `Argus.app` on a Mac) puts Argus back as it was. Argus still records into a working folder, and Save packs it; saving again only adds what changed. Argus asks before closing if anything has changed since. File › Open Session Folder… and Save Session Folder As… still work with plain folders.
- **DataPad**: a table of measurements (mean, peak, area, slope, frequency, spike rate…) per trial, per stimulus average, or as mean ± SD/SEM per stimulus, exported as CSV or copied to a spreadsheet.
- **Simulation**: a network of up to 10 integrate-and-fire cells with tunable synapses, to practise without an animal.
- **Membrane potential** (under Simulation): ions as particles crossing a membrane through selective
  channels, so the Nernst and GHK potentials and the charge layers on the membrane emerge on their own.
  Tunable concentrations and permeabilities, a scripted action potential, the GHK derivation and a
  GHK calculator. Needs `pip install -e ".[web]"` to show inside the app; otherwise it opens in the
  web browser. For computers without internet, run `python tools/fetch_mathjax.py` once so its
  equations render offline.
- **Files**: opens and saves LabChart `.mat` exports.
- **USB audio and video** (`pip install -e ".[audio]"` for audio):
  - **Setup › Input device** switches to a USB audio interface, a SpikerBox-style device or a
    microphone as the input. Its inputs become channels in1, in2, and so on.
  - **Audio/Video › Listen to** plays a channel through the speakers live, to hear spikes.
  - **Audio/Video › Play** (Space) plays the shown part of a recorded block, with a moving cursor.
  - **Audio/Video › Camera panel** shows a USB camera and records a video with each block. In review,
    the video plays back in step with the traces, and clicking a plot shows that moment.
  - macOS asks once for permission to use the microphone and camera.

[docs/feature-ideas.md](docs/feature-ideas.md) lists features from LabChart, Spike2, pClamp and
other programs that could come next.

## Who

Made for and by those within UW's neuroscience major!
