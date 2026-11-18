"""Channel setup without a window: a typed sampling rate, a new channel's input, name and
color, the wiring check, and the setup for another input device. The Qt main window
(channelsetup.py, mediacontrol.py) and the HTML front end (web/rig.py) both use these."""
from __future__ import annotations

import copy
import dataclasses

from . import colors
from . import config as cfgmod
from .config import Channel, Config, valid_inputs

DIFF_INPUTS = ("ai0", "ai1", "ai2", "ai3")       # in DIFF, ai4–ai7 are their minus inputs


def parse_rate(text: str) -> float | None:
    """A sampling rate typed as "1000", "20k", "20 kHz", "1,000 Hz"; None if it isn't one."""
    t = text.lower().replace(",", "").replace("hz", "").strip()
    mult = 1.0
    if t.endswith("k"):
        t, mult = t[:-1].strip(), 1000.0
    try:
        v = float(t) * mult
    except ValueError:
        return None
    return v if v > 0 else None


def layout(channels: list[Channel]) -> list[tuple]:
    """What every trial in a session must share: each channel's input, name and units."""
    return [(c.physical, c.name, c.units) for c in channels]


def new_channel(cfg: Config, channels: list[Channel]) -> Channel | None:
    """A channel on the first free input, named Ch<n>, in the next palette color; None if
    every input is in use. `channels` are all of them, on and off."""
    used = {c.physical for c in channels}
    free = [p for p in valid_inputs(cfg) if p not in used]
    if not free:
        return None
    names = {c.name for c in channels}
    n = len(channels) + 1
    while f"Ch{n}" in names:
        n += 1
    return Channel(free[0], f"Ch{n}", color=colors.next_color(c.color for c in channels))


def diff_problem(channels: list[Channel]) -> str:
    """Why these channels can't use DIFF wiring ("" if they can)."""
    stuck = [c for c in channels if c.physical not in DIFF_INPUTS]
    if not stuck:
        return ""
    names = ", ".join(f"{c.name} ({c.physical})" for c in stuck)
    return (f"Can't switch to DIFF: {names} must move to ai0–ai3 or be removed "
            "first, because DIFF uses ai4–ai7 as the minus inputs.")


def daq_setup(cfg: Config) -> dict:
    """The DAQ's channel setup, remembered while an audio input is used, to come back to."""
    return {"channels": [dataclasses.asdict(c) for c in cfg.channels + cfg.spare_channels],
            "sample_rate": cfg.sample_rate, "stim_monitor": cfg.stim_monitor}


def for_input(cfg: Config, backend: str, device: str, saved: dict) -> Config:
    """A copy of `cfg` set up for another input device: an audio input's own channels and
    rate, or, coming back from one, the DAQ's setup remembered in `saved` (daq_setup)."""
    was = cfg.backend
    cfg = copy.deepcopy(cfg)
    cfg.backend, cfg.audio_device = backend, device
    if backend == "audio":
        from . import audio
        audio.use_audio_channels(cfg)
    elif was == "audio":
        chans = [Channel(**{k: (tuple(v) if k == "range" else v) for k, v in c.items()})
                 for c in saved.get("channels", [])] or [Channel("ai0", "Ch1"), Channel("ai1", "Ch2")]
        cfg.channels = [c for c in chans if c.enabled]
        cfg.spare_channels = [c for c in chans if not c.enabled]
        cfg.sample_rate = float(saved.get("sample_rate", 20000))
        cfg.chunk_size = cfgmod.auto_chunk_size(cfg.sample_rate)
        cfg.stim_monitor = saved.get("stim_monitor", cfg.stim_monitor)
        colors.fill_colors(cfg.channels + cfg.spare_channels)
    return cfg
