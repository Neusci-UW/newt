"""Load, validate and save the acquisition config (TOML)."""
from __future__ import annotations

import json
import tomllib
import warnings
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import breadboard, simulation
from .colors import fill_colors
from .dsp import Calculation
from .units import whole

# USB-6003 limits (NI spec): one multiplexed ADC, 100 kS/s aggregate,
# 8 single-ended / 4 differential inputs, fixed +/-10 V range.
USB6003_MAX_AGGREGATE_RATE = 100_000
USB6003_RANGE = (-10.0, 10.0)

# Offered in the Sampling rate box; any other positive value can be typed in.
RATE_PRESETS = (100, 200, 500, 1000, 2000, 5000, 10000, 20000, 25000, 50000, 100000)
# When the rate is changed in the app, read about this much data at a time.
READ_INTERVAL_S = 0.05


def auto_chunk_size(rate: float) -> int:
    """Samples per channel per read for ~50 ms display latency at `rate`."""
    return max(1, round(rate * READ_INTERVAL_S))


def max_rate(n_channels: int) -> int:
    """Highest per-channel rate the USB-6003 allows with this many channels on."""
    return USB6003_MAX_AGGREGATE_RATE // max(1, n_channels)


@dataclass
class Channel:
    physical: str                 # e.g. "ai0"
    name: str = ""
    units: str = "V"
    scale: float = 1.0            # displayed = volts * scale + offset
    offset: float = 0.0
    range: tuple[float, float] = USB6003_RANGE
    enabled: bool = True
    color: str = ""               # "#rrggbb"; empty = next palette color
    width: float = 1.0            # trace thickness in px (display only)

    def to_units(self, volts):
        return volts * self.scale + self.offset


@dataclass
class Config:
    backend: str = "nidaqmx"      # "nidaqmx" | "multichannelio" | "simulated" | "audio"
    audio_device: str = ""        # audio backend: the input device's name ("" = the default one)
    device: str = "Dev1"
    sample_rate: float = 20000.0  # per channel; fast enough to see action potential shapes
    chunk_size: int = 1000        # samples per channel per read (50 ms at 20 kHz)
    terminal_config: str = "RSE"  # "RSE" | "DIFF"
    channels: list[Channel] = field(default_factory=list)        # switched on: acquired and shown
    spare_channels: list[Channel] = field(default_factory=list)  # switched off: kept so they can be switched back on
    window_s: float = 10.0
    data_dir: str = "data"
    # Stimulator (analog output)
    stim_output: str = "ao0"      # "ao0" | "ao1"
    stim_rate: float = 5000.0     # output samples per second (USB-6003 max 5,000)
    stim_max_v: float = 10.0      # safety limit: stimuli above this are refused (the USB-6003's range)
    stim_monitor: str = ""        # input that records the stimulus (wired from the output), or ""
    stim_units: str = "V"         # units the Stimulator shows levels in, e.g. "µA" behind an isolator
    stim_scale: float = 1.0       # stim_units per volt at the output; levels are still stored in volts
    # Simulated backend only: the cells it simulates and what each input records
    sim_network: simulation.Network = field(default_factory=simulation.default_network)
    sim_breadboard: breadboard.Board = field(default_factory=breadboard.default_board)
    # Calculated channels (filters, arithmetic: see dsp.py), shown and analyzed, never recorded
    calculations: list = field(default_factory=list)

    @property
    def aggregate_rate(self) -> float:
        return self.sample_rate * len(self.channels)

    def with_channels(self, all_channels: list[Channel], **changes) -> "Config":
        """A copy with a new channel list (on and off), plus any other field changes."""
        return replace(self, channels=[c for c in all_channels if c.enabled],
                       spare_channels=[c for c in all_channels if not c.enabled], **changes)


def valid_inputs(cfg: Config) -> list[str]:
    """Inputs available in the current wiring mode: in DIFF, ai4-ai7 are the minus inputs of ai0-ai3.
    An audio device's inputs are in1, in2, …"""
    if cfg.backend == "audio":
        from . import audio
        return audio.input_names(audio.max_inputs(cfg.audio_device))
    return [f"ai{i}" for i in range(4 if cfg.terminal_config == "DIFF" else 8)]


def load(path: str | Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    dev, disp, rec = raw.get("device", {}), raw.get("display", {}), raw.get("recording", {})
    st, sim = raw.get("stimulator", {}), raw.get("simulated", {})
    chans = []
    for c in raw.get("channels", []):
        ch = Channel(**{k: v for k, v in c.items() if k != "range"})
        if "range" in c:
            ch.range = tuple(c["range"])
        ch.name = ch.name or ch.physical
        chans.append(ch)
    fill_colors(chans)
    cfg = Config(
        backend=dev.get("backend", "nidaqmx"),
        audio_device=dev.get("audio_device", ""),
        device=dev.get("device", "Dev1"),
        sample_rate=float(dev.get("sample_rate", 20000)),
        chunk_size=int(dev.get("chunk_size", auto_chunk_size(float(dev.get("sample_rate", 20000))))),
        terminal_config=dev.get("terminal_config", "RSE").upper(),
        window_s=float(disp.get("window_s", 10)),
        data_dir=rec.get("directory", "data"),
        stim_output=str(st.get("output", "ao0")).lower(),
        stim_rate=float(st.get("rate", 5000)),
        stim_max_v=float(st.get("max_voltage", 10.0)),
        stim_monitor=str(st.get("monitor", "")).lower(),
        stim_units=str(st.get("units", "V")).strip() or "V",
        stim_scale=float(st.get("scale", 1.0)),
        sim_network=simulation.from_toml(sim),
        sim_breadboard=breadboard.from_toml(sim),
        calculations=[Calculation(str(c.get("name", "")).strip(), str(c.get("formula", "")).strip(),
                                  str(c.get("unit", "")).strip()) for c in raw.get("calculations", [])],
    ).with_channels(chans)
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    if not cfg.channels:
        raise ValueError("At least one channel must be on." if cfg.spare_channels
                         else "Config has no [[channels]].")
    if cfg.backend not in ("nidaqmx", "multichannelio", "simulated", "audio"):
        raise ValueError(f"Unknown backend {cfg.backend!r}")
    if cfg.terminal_config not in ("RSE", "DIFF"):
        raise ValueError("terminal_config must be RSE or DIFF (USB-600x has no NRSE).")
    if cfg.stim_output not in ("ao0", "ao1"):
        raise ValueError("[stimulator] output must be ao0 or ao1.")
    if not 0 < cfg.stim_rate <= 5000:
        raise ValueError("[stimulator] rate must be between 0 and 5,000 S/s (USB-6003 limit).")
    if not 0 < cfg.stim_max_v <= 10:
        raise ValueError("[stimulator] max_voltage must be between 0 and 10 V.")
    if not cfg.stim_scale > 0:
        raise ValueError("[stimulator] scale must be positive (units per volt).")
    if cfg.sample_rate <= 0:
        raise ValueError("Sample rate must be positive.")
    if cfg.backend != "audio" and cfg.aggregate_rate > USB6003_MAX_AGGREGATE_RATE:
        n = len(cfg.channels)
        raise ValueError(
            f"{n} ch × {whole(cfg.sample_rate)} = {cfg.aggregate_rate:,g} S/s, over the USB-6003's "
            f"{USB6003_MAX_AGGREGATE_RATE:,} S/s limit. With {n} channel(s), use at most "
            f"{whole(max_rate(n))} per channel.")
    allowed = valid_inputs(cfg)
    seen: dict[str, str] = {}
    names: set[str] = set()
    for ch in cfg.channels:
        if ch.physical not in allowed:
            raise ValueError(f"{ch.name}: {ch.physical} isn't available; use {allowed[0]}–{allowed[-1]}"
                             + ("." if cfg.backend == "audio" else f" in {cfg.terminal_config} mode."))
        if ch.physical in seen:
            raise ValueError(f"{seen[ch.physical]} and {ch.name} both use {ch.physical}. "
                             "Each input can only be one channel.")
        if not 0.5 <= float(ch.width) <= 20:
            raise ValueError(f"{ch.name}: line width must be between 0.5 and 20 px.")
        if not ch.name.strip():
            raise ValueError("Every channel needs a name.")
        if ch.name in names:
            raise ValueError(f"Two channels are named {ch.name!r}; give each its own name.")
        seen[ch.physical] = ch.name
        names.add(ch.name)
    simulation.validate(cfg.sim_network)
    names = {c.name for c in cfg.channels}
    seen_calc: set[str] = set()
    for c in cfg.calculations:
        if not c.name or not c.formula:
            raise ValueError("Every calculated channel needs a name and a formula.")
        if c.name in names or c.name in seen_calc:
            raise ValueError(f"{c.name!r} is already a channel's name: give the calculation another.")
        seen_calc.add(c.name)
    if cfg.backend in ("simulated", "audio"):
        return
    for ch in cfg.channels:
        if tuple(ch.range) != USB6003_RANGE:
            warnings.warn(f"{ch.name}: USB-6003 only supports +/-10 V; the driver will coerce "
                          f"{ch.range} to that. It does NOT add gain.")
    if cfg.backend == "multichannelio" and cfg.terminal_config != "RSE":
        raise ValueError("MultiChannelIO hardcodes RSE wiring; use the nidaqmx backend for DIFF.")
    if cfg.chunk_size / cfg.sample_rate > 0.5:
        warnings.warn("chunk_size/sample_rate > 0.5 s: display will update sluggishly.")


def _s(x: str) -> str:
    return json.dumps(x, ensure_ascii=False)  # JSON strings are valid TOML basic strings


def dumps(cfg: Config) -> str:
    """The config as TOML that `load` reads back, switched-off channels included."""
    lines = [
        "[device]",
        f"backend = {_s(cfg.backend)}",
        *([f"audio_device = {_s(cfg.audio_device)}"] if cfg.audio_device else []),
        f"device = {_s(cfg.device)}",
        f"sample_rate = {cfg.sample_rate:g}",
        f"chunk_size = {cfg.chunk_size}",
        f"terminal_config = {_s(cfg.terminal_config)}",
        "",
        "[display]",
        f"window_s = {cfg.window_s:g}",
        "",
        "[recording]",
        f"directory = {_s(cfg.data_dir)}",
        "",
        "[stimulator]",
        f"output = {_s(cfg.stim_output)}",
        f"rate = {cfg.stim_rate:g}",
        f"max_voltage = {cfg.stim_max_v:g}",
        f"monitor = {_s(cfg.stim_monitor)}",
    ]
    if cfg.stim_units != "V" or cfg.stim_scale != 1.0:
        lines += [f"units = {_s(cfg.stim_units)}", f"scale = {cfg.stim_scale:g}"]
    for c in cfg.calculations:
        lines += ["", "[[calculations]]", f"name = {_s(c.name)}", f"formula = {_s(c.formula)}", f"unit = {_s(c.unit)}"]
    if cfg.sim_network != simulation.default_network():      # only when it's been changed
        lines += ["", "[simulated]"] + simulation.to_toml_lines(cfg.sim_network)
    if cfg.sim_breadboard != breadboard.default_board():
        lines += breadboard.to_toml_lines(cfg.sim_breadboard)
    for c in cfg.channels + cfg.spare_channels:
        lines += ["", "[[channels]]",
                  f"physical = {_s(c.physical)}",
                  f"name = {_s(c.name)}",
                  f"units = {_s(c.units)}",
                  f"scale = {c.scale:g}"]
        if c.offset:
            lines.append(f"offset = {c.offset:g}")
        if tuple(c.range) != USB6003_RANGE:
            lines.append(f"range = [{c.range[0]:g}, {c.range[1]:g}]")
        if c.color:
            lines.append(f"color = {_s(c.color)}")
        if c.width != 1.0:
            lines.append(f"width = {c.width:g}")
        if not c.enabled:
            lines.append("enabled = false")
    return "\n".join(lines) + "\n"


def save(cfg: Config, path: str | Path) -> Path:
    path = Path(path)
    path.write_text(dumps(cfg), encoding="utf-8")
    return path
