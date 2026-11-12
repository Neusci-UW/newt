"""Sending a stimulus waveform out of the analog outputs (ao0, ao1, or both).

Each stimulus is one hardware-timed, finite output task, run on a worker
thread so the window stays responsive. Recording keeps running on its own
task at the same time. With two outputs, both are channels of the one task,
so they play from the same clock, sample for sample.

Safety: the waveform always ends at 0 V (stim.py), and on Stop, on errors
and when the program closes the output is set to 0 V.

Timing: the output and the recording are separate tasks, so their start
times differ by a few milliseconds. For exact timing, wire the output to a
spare input and record it (Stimulator > Record stimulus on).
"""
from __future__ import annotations

import threading
import time

import numpy as np

from .config import Config


class StimOutput:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.error: BaseException | None = None
        self.fired = 0                          # stimuli started (for tests and the status bar)
        self.last_channels: list[str] = []      # the outputs the last stimulus went to
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def fire(self, wave: np.ndarray, rate: float, channels: list[str] | None = None) -> None:
        """Start sending `wave`: 1-D for one output (the configured one, or channels[0]), or
        one row per output in `channels` (stim.combine makes it). Raises RuntimeError if the
        previous stimulus is still going."""
        if self.busy:
            raise RuntimeError("The previous stimulus is still running.")
        wave = np.asarray(wave, float)
        channels = list(channels or [self.cfg.stim_output])
        if (wave.ndim == 2) != (len(channels) > 1) or (wave.ndim == 2 and wave.shape[0] != len(channels)):
            raise ValueError("One row of the waveform per output.")
        self.error = None
        self._stop.clear()
        self.fired += 1
        self.last_channels = channels
        self._thread = threading.Thread(target=self._run, args=(wave, float(rate), channels),
                                        daemon=True, name="daq-stim")
        self._thread.start()

    def stop(self) -> None:
        """Stop the current stimulus (if any) and set the output to 0 V."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        try:
            self.zero()
        except Exception as e:      # reported, but never blocks stopping
            self.error = e

    def _run(self, wave, rate, channels):
        try:
            self._send(wave, rate, channels)
        except BaseException as e:
            self.error = e
            try:
                self.zero()
            except Exception:
                pass

    def _send(self, wave, rate, channels): raise NotImplementedError
    def zero(self) -> None: ...


class NidaqmxOutput(StimOutput):
    """Hardware-timed finite output with NI's `nidaqmx` package."""

    def _channels(self, task, channels):
        for ch in channels:
            task.ao_channels.add_ao_voltage_chan(f"{self.cfg.device}/{ch}", min_val=-10.0, max_val=10.0)

    def _send(self, wave, rate, channels):
        import nidaqmx
        from nidaqmx.constants import AcquisitionType
        task = nidaqmx.Task()
        n = wave.shape[-1]
        try:
            self._channels(task, channels)
            if n < 2:
                first = wave[..., 0] if n else np.zeros(wave.shape[:-1])
                task.write(first.tolist() if wave.ndim == 2 else float(first), auto_start=True)
                return
            task.timing.cfg_samp_clk_timing(rate=rate, sample_mode=AcquisitionType.FINITE,
                                            samps_per_chan=n)
            task.write(wave, auto_start=False)          # 2-D: one row per channel, in order
            task.start()
            deadline = time.monotonic() + n / rate + 5.0
            while not task.is_task_done():
                if self._stop.is_set():
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError("The stimulus didn't finish in time.")
                time.sleep(0.005)
        finally:
            stopped_early = self._stop.is_set()
            try:
                task.stop()
            finally:
                task.close()
            if stopped_early:
                self.zero()

    def zero(self) -> None:
        """Both outputs to 0 V: whichever were used, nothing is left on."""
        import nidaqmx
        from .stim import AO_CHANNELS
        with nidaqmx.Task() as task:
            self._channels(task, AO_CHANNELS)
            task.write([0.0] * len(AO_CHANNELS), auto_start=True)


class SimulatedOutput(StimOutput):
    """For trying the stimulator without hardware: the waveform appears on the
    monitor input of the simulated recording, and the other inputs get the EPSPs
    (and extra spikes) set on the Neural network simulator page."""

    def __init__(self, cfg: Config, backend):
        super().__init__(cfg)
        self.backend = backend

    def _send(self, wave, rate, channels):
        if wave.ndim == 2:          # the network takes one stimulus: the configured output's
            row = channels.index(self.cfg.stim_output) if self.cfg.stim_output in channels else 0
            wave = wave[row]
        self.backend.inject(self.cfg.stim_monitor, wave, rate)
        end = time.monotonic() + len(wave) / rate
        while time.monotonic() < end:
            if self._stop.is_set():
                self.backend.inject(self.cfg.stim_monitor, np.zeros(1), rate)
                return
            time.sleep(0.005)

    def zero(self) -> None:
        pass


def make_output(cfg: Config, backend) -> StimOutput | None:
    """The output for this backend, or None if it can't send stimuli."""
    if cfg.backend == "nidaqmx":
        return NidaqmxOutput(cfg)
    if cfg.backend == "simulated":
        return SimulatedOutput(cfg, backend)
    return None
