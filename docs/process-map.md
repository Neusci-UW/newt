# NEWT process map

Mermaid diagrams (render on GitHub or in any Mermaid viewer). Module names are files in `src/neusci_daq/`.

## 1. Big picture: from signal to figure

```mermaid
flowchart LR
    subgraph Sources
        DAQ[NI USB-6003<br/>backends.py]
        SIM[Simulated network<br/>simulation.py]
        AUD[USB audio<br/>audio.py]
        CAM[USB camera / AmScope<br/>video.py, amscope.py]
        MAT[LabChart .mat<br/>matfile.py]
        FILE[.newt / .argus<br/>newtfile.py, argusfile.py]
    end
    subgraph Acquire
        CFG[config.toml<br/>config.py]
        LIVE[Live view<br/>liveview.py, ringbuffer.py]
        REC[Recorder<br/>recorder.py]
        STIM[Stimulator + Protocols<br/>stim*, protocols*]
    end
    subgraph Store
        H5[block_NNN.h5<br/>trialfile.py]
        SESS[Session folder<br/>session.py, sessionfiles.py]
        STORE[trialstore.py]
        AF[analysis / groups / comments files<br/>analysisfile.py, groupsfile.py]
    end
    subgraph Analyse
        REV[Review + Trials browser]
        ANA[Analysis whiteboard<br/>canvas.py, board.py]
    end
    subgraph Out
        MATX[.mat export<br/>export.py]
        NEWTX[.newt save]
        FIG[Figure<br/>figure.py]
        DP[DataPad / fits<br/>datapad.py, fitting.py]
    end
    CFG --> DAQ & SIM & AUD
    DAQ & SIM & AUD -->|Chunks via queues| LIVE
    DAQ & SIM & AUD -->|Chunks| REC
    CAM --> REC
    STIM -->|ao0 / ao1 waveform<br/>stimout.py| DAQ
    REC --> H5 --> SESS --> STORE
    MAT & FILE --> STORE
    STORE --> REV --> ANA
    AF <--> ANA
    ANA --> DP & FIG
    SESS --> MATX & NEWTX
```

## 2. Startup and window structure

```mermaid
flowchart TD
    CLI["newt -c config.toml [--backend simulated]<br/>__main__.py"] --> GUI[gui.py: main window]
    GUI --> CFGL[config.py: load + validate TOML]
    GUI --> BK[backends.py: start acquisition thread]
    GUI --> STY[style.py, theme.py, colors.py]
    GUI --> TABS[tabs.py: top tabs]
    TABS --> ACQ[Acquisition]
    TABS --> ANAT[Analysis]
    TABS --> SIMT["Data Simulation<br/>(--backend simulated only)"]
    TABS --> LES[Lessons<br/>submodule: lessons/]
    ACQ --> A1[Live / Review<br/>liveview, reviewview]
    ACQ --> A2[Stimulator<br/>stimwindow]
    ACQ --> A3[Protocols<br/>protocolspage]
    ANAT --> N1[Whiteboard<br/>analysispage, canvas, board]
    SIMT --> S1[Neural network<br/>simpage]
    SIMT --> S2[Breadboard<br/>breadpage, breadboard]
    SIMT --> S3[Membrane potential<br/>membranepage]
```

Main-window helpers split out of `gui.py`: `mainparts` (shared widgets/modes), `channelsetup` + `channels`, `commentcontrol` + `comments`, `mediacontrol`, `stimcontrol`, `sessionfiles`.

## 3. Recording a trial

```mermaid
sequenceDiagram
    actor U as Student
    participant G as gui.py
    participant B as backend thread
    participant L as liveview
    participant R as recorder.py
    participant T as trialfile.py (HDF5)
    U->>G: Record
    G->>R: start(block)
    loop every chunk
        B-->>L: Chunk (ring buffer, draw)
        B-->>R: Chunk (background thread)
        R->>T: append samples
    end
    U->>G: comment (commentcontrol)
    G->>T: store comment with time
    U->>G: Fire / Repeat
    G->>B: stimout.py waveform (ao0/ao1, same clock)
    U->>G: Stop
    R->>T: close block_NNN.h5
    G->>G: Trials browser refreshes (trialstore)
```

## 4. Protocol run (stepping a stimulus setting)

```mermaid
flowchart LR
    P[protocols.py: pick 1-2 settings to step] --> ST[stim.py: build waveform per step]
    ST --> OUT[stimout.py: fire]
    OUT --> RECB[record block]
    RECB --> MEAS[measure each response<br/>measure.py]
    MEAS --> CH[Protocols tab chart<br/>protocolspage.py]
    RECB --> TB[Trials browser groups by stimulus]
```

## 5. Analysis pipeline

```mermaid
flowchart TD
    TS[trialstore.py: all trials] --> TBR[trialsbrowser.py: groups<br/>groupsfile.py]
    TS --> CALC["Analysis channels<br/>analysischannel.py + dsp.py<br/>(filters, rate, formulas)"]
    CALC --> SF[spikefinder.py + analysis.py<br/>threshold, find + sort spikes]
    SF --> RF[repeatfinder.py: stimulus cycles]
    TBR --> CV[canvas.py: Add chart menu]
    SF --> CV
    RF --> CV
    subgraph Charts[chartbase.py subclasses]
        C1[Response charts<br/>responsecharts.py]
        C2[Spike charts: raster, rate<br/>spikecharts.py]
        C3[Waveform / phase plot<br/>waveformview.py]
        C4[Measure charts<br/>measurecharts.py]
    end
    CV --> C1 & C2 & C3 & C4
    SP[sidepanels.py] --- CV
    C4 --> DPD[datapad.py] --> FIT[fitting.py]
    CV --> FG[figure.py: box-select to figure]
    CV --> HV[helpview.py: ? buttons]
    CV <--> AFILE[analysisfile.py: saved beside session]
```

## 6. Files and formats

```mermaid
flowchart LR
    DIR[Session folder] --> H[block_NNN.h5<br/>one trial each]
    DIR --> PJ[pages.json: groups]
    DIR --> AJ[analysis work<br/>analysisfile.py]
    DIR -->|Save| NEWT[.newt single file]
    DIR -->|Export| MATF[LabChart .mat]
    NEWT -->|Open| DIR
    ARGUS[.argus legacy] -->|Open| DIR
    MATF2[LabChart .mat] -->|Open for review| DIR
```

## 7. Tooling, tests, docs

| Area | Where |
|---|---|
| Entry points | `newt`, `neusci-daq` → `__main__:main` (pyproject.toml) |
| Mac app bundle | `tools/make_mac_app.py` → `NEWT.app` |
| Demo data | `examples/make_demo_session.py` |
| Tests | `tests/` (run with `.venv/bin/python`) |
| Docs | `docs/feature-ideas.md`, `docs/response-chart-plan.md` |
| Lessons | `src/neusci_daq/lessons` (submodule, Neusci-UW/lessons) |
