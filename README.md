
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
- **Stimulator and Trials**:
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
  - A Blocks panel beside every sub-tab: pick a page to see its average, or drag across blocks to see those trials; it also holds the stimulus and analyzed channels, Spread, and the colours of pages and trials (editable, or from a colour map).
  - Every sub-tab has Charts checkboxes to show just the charts you want, and a ? button with an explanation and glossary. Spike Sorting and Response can swap their axes for draggable scale bars.
  - A gain per channel, and a fit that cleans up a noisy or coarse recorded stimulus, both applied as data is read: the recordings never change.
  - Spike Sorting: overlays and averages, with the stimulus above, with ± SD or SEM across trials; finding and sorting action potentials (tick the units to show, on every sub-tab); raster, rate, a PCA map of the units, waveforms, and size and ISI histograms.
  - Response: the chosen blocks' channels and derivatives, PSTH and spike-triggered averages, and stimulus cycles: the repeats of any repeating stimulus overlaid by phase, with a raster per cycle, a phase histogram with vector strength, and a loop of response against stimulus.
  - Waveforms: the action potentials as a phase plot of V against dV/dt, and against time.
  - DataPad: a table of measurements (mean, peak, area, slope, frequency, spike rate…) per block, per stimulus average, or as mean ± SD/SEM per stimulus, exported as CSV or copied to a spreadsheet.
- **Simulation**: a network of up to 10 integrate-and-fire cells with tunable synapses, to practise without an animal.
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
