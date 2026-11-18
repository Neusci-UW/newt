"""The ? help's words: a short explanation of an Analysis tool, and a glossary of its terms.
Plain data, shared by the Qt app (helpview.py adds a picture to each) and the HTML front end."""

TOPICS = {
    "spike_sorting": {
        "title": "How Find action potentials works",
        "text": "NEWT finds every place the trace passes the <b>threshold</b> and cuts out a short <b>waveform</b> "
                "around each peak. Spikes from the same cell look alike, so it reduces each waveform to its 3 "
                "<b>principal components</b>, which turns each spike into a point, and <b>k-means</b> groups "
                "nearby points into <b>units</b>.",
        "warning": "Picking a number of units always gives you that many groups (or fewer if one is tiny), even "
                   "if the spikes are all one cell. Check that the units really differ in Waveforms and AP sizes.",
        "glossary": (
            ("Baseline", "The trace's middle level (its median). Thresholds are measured from here."),
            ("Noise SD", "How much the trace wobbles without spikes. 5 SD means 5 times that wobble."),
            ("Threshold", "How far from the baseline the trace must go to count as an event."),
            ("Method", "Manual: you set the threshold. Bimodal: NEWT puts it between noise and spikes."),
            ("Direction", "Which way spikes go from the baseline: down, up or either."),
            ("Biphasic", "Keep only events that also swing the other way, as real spikes do."),
            ("Dead time", "After an event, no other can start for this long."),
            ("Waveform before / after", "How much of each spike to cut out around its peak."),
            ("Units", "How many groups to sort spikes into. Auto picks the number that separates best."),
            ("Unit", "A group of spikes with the same shape, likely from one cell."),
            ("Principal components", "The main ways the spike shapes differ. NEWT uses the first 3."),
            ("k-means", "Groups points around k centers (the × marks), each point joining its nearest."),
        ),
    },
    "comparison": {
        "title": "How Response works",
        "text": "<b>Response</b> shows the chosen trials' channels on one time axis, as voltage or its rate of "
                "change: a picked group's average, or picked trials' trials. On the channel Spikes sorts it can "
                "show the threshold and a dot on each spike. "
                "On the triggered charts, the chosen trials line up on a <b>trigger</b> and are averaged: what's random "
                "cancels and what follows the trigger stays. The <b>stimulus-triggered rate</b> asks how spiking "
                "changes after a stimulus, and the <b>spike-triggered average</b> asks what a channel does around "
                "each spike.",
        "warning": "Both averages need action potentials, so find them first. They use the units ticked, unless "
                   "a chart has its own.",
        "glossary": (
            ("dV/dt, d²V/dt²", "How fast the voltage changes, and how fast that changes."),
            ("Smooth for d/dt", "Averages the trace first, since differentiating amplifies noise."),
            ("Trigger", "The moment each trace is lined up on: time 0."),
            ("Bin", "The width of each bar in the stimulus-triggered rate."),
            ("Before / after", "How much to show on each side of the trigger."),
            ("Spread", "SD shows how much trials vary; SEM how sure the average is."),
        ),
    },
    "cycles": {
        "title": "How Stimulus cycles work",
        "text": "With a repeating stimulus, NEWT cuts the chosen trials into single <b>cycles</b> and overlays them "
                "by stimulus <b>phase</b> instead of time. If the trial saved what was sent, it lines that up with the "
                "recording; otherwise it matches the stimulus's shape against itself, then uses its rises through its "
                "middle level. Or give the <b>repeat length</b>, or mark one on the Response or Stimulus chart. Picked "
                "groups show their average and picked "
                "trials their own, with every cycle behind it colored from first to last.",
        "warning": "A fixed delay lands at a different phase at each frequency: 5 ms is 18° at 10 Hz but 90° at 50 Hz. "
                   "Cycles are stretched to line up, so compare timing between frequencies with care.",
        "glossary": (
            ("Cycle", "One repeat of the stimulus, from one rise through its middle to the next."),
            ("Phase", "How far through a cycle: 0° at the rise, 180° halfway, 360° at the next rise."),
            ("Repeat length", "How long one repeat is. 0 lets NEWT find it."),
            ("Left out", "Repeats too unlike the rest to use, counted in the Stimulus title."),
            ("Skip first", "Leaves out the first cycles of each trial, while the response settles."),
            ("Spikes per phase", "A histogram of when in the cycle spikes happen, per cycle."),
            ("Vector strength", "How tightly spikes lock to one phase: 1 is exact, near 0 is none."),
            ("Response against stimulus", "A loop: open if the response lags, a line if it doesn't."),
        ),
    },
    "waveforms": {
        "title": "How Waveforms works",
        "text": "This shows the shape of the <b>action potentials</b> found. The <b>phase plot</b> "
                "puts V against <b>dV/dt</b>, so each spike becomes a loop: time drops out, and differences in how "
                "fast a spike rises and falls stand out. Thick lines are each unit's average.",
        "warning": "With an extracellular recording this is the shape of the recorded signal, not the membrane "
                   "voltage. Find action potentials first.",
        "glossary": (
            ("dV/dt", "How fast the voltage changes, at each moment of the spike."),
            ("Phase plot", "V against dV/dt: the spike as a loop, with no time axis."),
            ("Smooth for dV/dt", "Averages the spike first, since differentiating amplifies noise."),
            ("Each spike", "Draws single spikes behind the average, up to 150 of each unit."),
            ("Unit", "A group of spikes with the same shape, likely from one cell."),
        ),
    },
}
