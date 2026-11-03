# Feature ideas from other electrophysiology software

This is a list of features found in other acquisition, analysis and simulation programs
that could be worth adding here. It is limited to what helps an undergraduate teaching
lab. Each entry names where the idea comes from. Entries marked ✅ already exist in
neusci-daq-visualizer.

Programs referred to:

- **LabChart** (ADInstruments): the program this one replaces.
- **Spike2** (CED).
- **pClamp / Clampex / Clampfit** (Molecular Devices).
- **WinWCP and WinEDR** (Strathclyde, free).
- **AxoGraph**.
- **Open Ephys GUI**.
- **Intan RHX**.
- **Backyard Brains Spike Recorder**.
- **NEURON, Brian2 and NEST** (simulators).
- **SpikeInterface**.

## Already here

| Feature | Where it's from | Here |
|---|---|---|
| Continuous multichannel chart recording, with comments | LabChart, Spike2, WinEDR | ✅ Acquisition tab |
| Blocks, overlaid and stepped through | LabChart (blocks), Clampfit (sweeps) | ✅ Blocks panel; ↑ ↓ keys |
| Stimulator with units and pulse trains | LabChart Stimulator panel, Clampex | ✅ Stimulator tab |
| Protocols that step a stimulus setting, then line up and measure the responses | Clampex episodic protocols, LabChart Scope | ✅ Trials tab |
| Open and save LabChart `.mat` files | LabChart export | ✅ File menu |
| Calculated channels: digital filters (low-pass, high-pass, band-pass, band-stop, notch), smoothing, RMS, envelope, derivative, integral and arithmetic | LabChart Channel Calculations, Spike2 virtual channels | ✅ Calculations… |
| DataPad: a table of measurements in a time window, exported or copied | LabChart DataPad, Clampfit Statistics | ✅ Analysis › DataPad |
| Mean ± SD or SEM across trials, shaded on averages | Clampfit, AxoGraph | ✅ Spread, at the top of Analysis |
| Action potential detection (threshold or bimodal) and sorting (PCA + k-means) | Spike2 WaveMark, Backyard Brains, SpikeInterface | ✅ Analysis › Raw Data |
| Raster, rate across the session, ISI histogram, PSTH, spike-triggered average | Spike2, LabChart Spike Histogram module | ✅ Analysis |
| Blocks grouped by stimulus, as named pages | Spike2 and Clampfit only by hand | ✅ Blocks panel, Analysis › Show |
| Simulated neurons, with tunable synapses between them | NEURON, Brian2 (much simpler here) | ✅ Simulation tab |

## Suggested next

The top picks for a teaching lab, roughly in order:

1. **Audio monitor.** Play a channel through the speakers while recording.
   - From: Backyard Brains, Intan RHX, Open Ephys (Audio Monitor), LabChart.
   - Why: students find spikes by ear long before they find them on screen, and it's the classic "hear the neuron" moment.
   - How: `sounddevice` fed from the ring buffer.
2. **Spectrum view and spectrogram.** A power spectrum (Welch) of a selection or of the live signal, and a scrolling spectrogram.
   - From: LabChart Spectrum view, Spike2 power spectrum, Backyard Brains FFT.
   - Why: it shows 60 Hz hum (and confirms the notch worked), EMG frequency content, and oscillations.
3. **Scope view: live triggered sweeps with a running average.** Each stimulus starts a new sweep, overlaid, with the average building up as you watch.
   - From: LabChart Scope, Clampex.
   - Why: the response emerges from the noise as you watch, which teaches signal averaging.
   - How: the Trials tab does this after the fact; a live version would reuse its `cut()`.
4. **Cyclic and rate measurements as live channels.** Rate meter (spikes/s or beats/min), cyclic height, and period.
   - From: LabChart Channel Calculations (Cyclic Measurements).
   - Why: they're needed for heart-rate and breathing labs, and for firing rate against stimulus strength, live.
   - How: add `rate(x, threshold)` and `period(x, threshold)` to the formula language in `dsp.py`.
5. **Curve fitting.** Fit exponentials (time constants), Boltzmann curves (activation), linear I-V and Hill curves, then show the fit and its parameters.
   - From: Clampfit, AxoGraph, WinWCP.
   - Why: a membrane time constant from an exponential fit is a standard lab exercise.
   - How: `scipy.optimize.curve_fit` on a DataPad window or on the Trials curve.
6. **Two-point unit calibration.** Enter two known values, read the voltages, and get a scale and offset.
   - From: LabChart Units Conversion.
   - Why: force transducers, thermistors and pressure sensors in physiology labs need it.
   - How: the config already has scale and offset; this would be a dialog that fills them in.
7. **XY view.** Plot one channel against another, for example a loop or a phase plot of V against dV/dt.
   - From: LabChart XY view, Clampfit.
   - Why: phase plots make action potential shape differences obvious.
8. **Membrane / seal test.** Apply a small step and read off input resistance, capacitance and time constant.
   - From: Clampex Membrane Test, WinWCP Seal Test.
   - Why: needed for intracellular or patch labs; the simulator could fake it first.
9. **Template matching for spike sorting.** Click a few spikes to make a template, then catch the rest by shape, and drift is tracked.
   - From: Spike2 WaveMark, AxoGraph template events, WinEDR.
   - Why: it's more intuitive for students than PCA, and handles overlapping units better.
10. **Leak subtraction (P/N).** Scaled small pulses are used to subtract passive currents.
    - From: Clampex, WinWCP.
    - Why: only needed if voltage clamp labs are added.

## More ideas, by area

### Acquisition

- **Triggered recording with pre-trigger time**: record only around a threshold crossing or TTL, keeping some time before it. (LabChart, Spike2, Open Ephys)
- **Keyboard event markers**: number keys drop typed or coloured markers, such as "drug on" and "drug off", which then appear in analysis. (Spike2 keyboard markers, LabChart comments)
- **Impedance check** of electrodes before recording, only if the hardware supports it. (Intan RHX)
- **Synchronized video**, for behaviour alongside the recording. (LabChart Video Capture, Open Ephys)
- **Closed-loop stimulation**: stimulate when a spike or threshold crossing is detected. (Open Ephys, Spike2 sequencer)
- **Live spike detection display**: the newest spikes overlaid in a small scope beside the chart. (Intan RHX Spike Scope, Open Ephys Spike Viewer)
- **Saved experiment settings ("settings files")**: one file per lab exercise that sets channels, ranges, filters, stimulator and calculations together. (LabChart settings files, Clampex protocols)

### Analysis

- **Auto- and cross-correlograms** between units, or between spikes and stimulus. (Spike2, SpikeInterface)
- **Phase histogram** of spikes relative to an oscillation, such as the stimulus sine. (Spike2)
- **Event-triggered averages of any channel on DataPad**: amplitude and latency per stimulus. (LabChart Peak Analysis)
- **Peak analysis**: find every peak or response and tabulate its height, width at half-height, rise time and decay. (LabChart Peak Analysis module, Clampfit event detection)
- **Latency to first spike** after each stimulus, plotted against stimulus strength. (Spike2 scripts, Clampfit)
- **Sorting quality metrics**: ISI violations (refractory period), SNR and isolation distance per unit. (SpikeInterface, Phy)
- **Heart-rate variability and ECG intervals** (PR, QRS, QT) for the human physiology labs. (LabChart HRV and ECG Analysis modules)
- **Burst detection** using ISI thresholds, with burst rate and duration. (Spike2, NeuroExplorer)
- **Statistics across groups**: t-tests or ANOVA on DataPad columns, run in place. (Clampfit, AxoGraph)

### Data and sharing

- **NWB export** (Neurodata Without Borders), the standard format for sharing neurophysiology. (Open Ephys, SpikeInterface)
- **ABF and WCP import**, to open Axon and WinWCP files for comparison. (Clampfit, WinWCP)
- **A notebook export**: a Jupyter notebook that reloads the session and repeats the DataPad measurements, so students can take the analysis further in Python.
- **Figure export**: a plot saved as SVG or PDF at publication size, with scale bars instead of axes. (Clampfit, AxoGraph)

### Simulation and teaching

- **Hodgkin–Huxley neuron mode**: real Na⁺ and K⁺ channels, so students can block them (TTX, TEA) and see the action potential change. (NEURON, HHsim, MetaNeuron)
- **Voltage-clamp simulation**: step the voltage and see the currents; it teaches what a clamp does. (NEURON, HHsim)
- **Cable and propagation**: a compound action potential along a simulated nerve, with conduction velocity measured between two electrodes. It matches the earthworm and frog nerve labs. (NEURON)
- **Synaptic plasticity** (paired-pulse facilitation, depression, LTP) in the network. (Brian2, NEST)
- **Guided lab exercises**: a side panel with steps, questions and "check your measurement". (LabChart Lab Tutor / Guided Experiments)
- **Noise sources in the simulator**: 60 Hz hum, electrode drift and movement artifacts, so students can practise filtering. Some of this already exists.
