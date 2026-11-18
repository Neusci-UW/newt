"""The Neural network simulator's Python half (the Qt app's simpage.py): the cells the
simulated backend records, the synapses between them, which cell each input records,
and the cells the stimulus drives. See simulation.py for the model.

A change replaces live.cfg.sim_network with a new network, all at once, and the
backend's thread takes it from its next chunk: Record shows it straight away, while
recording too. Edits are checked together, as the Qt page checks its tables: the page
sends every edit not yet taken along with a new one, so threshold can go below reset
once reset has gone lower too.
"""
from __future__ import annotations

import threading
from dataclasses import asdict

from .. import simulation as sim
from ..units import _same_kind, parse_quantity
from . import live as live_mod

_CELL_UNITS = {attr: (head, unit) for head, attr, unit, _ in sim.CELL_COLUMNS}
_REC_UNITS = {attr: (head, unit) for head, attr, unit, _ in sim.REC_COLUMNS if attr not in ("cell", "kind")}
_NET_UNITS = {"synapse_delay_ms": ("Synaptic delay", "ms", 0, 100),
              "synapse_tau_ms": ("Synaptic time constant", "ms", 0.1, 100)}
KINDS = [k for _, k in sim.KINDS]


def _number(text, unit: str, what: str) -> float:
    text = str(text).replace("−", "-").strip()
    try:
        return parse_quantity(text, _same_kind(unit))[0] if unit else float(text)
    except ValueError:
        raise ValueError(f"{what} must be a number" + (f", optionally with a unit (e.g. 5 {unit})."
                                                       if unit else ".")) from None


class Network:
    def __init__(self, live):
        self.live = live
        self._lock = threading.Lock()

    @property
    def net(self) -> sim.Network:
        return self.live.cfg.sim_network

    # -- what the page shows --------------------------------------------------------
    def state(self) -> dict:
        net, cfg = self.net, self.live.cfg
        cells = [{k: v for k, v in asdict(c).items() if k != "synapses"} for c in net.cells]
        inputs = []
        for ch in cfg.channels:
            rec = sim.recording_for(net, ch.physical)
            inputs.append({"physical": ch.physical, "name": ch.name, "monitor": ch.physical == cfg.stim_monitor,
                           **{k: v for k, v in asdict(rec).items() if k != "physical"}})
        return {
            "cells": cells, "weights": net.weights().tolist(),
            "synapse_delay_ms": net.synapse_delay_ms, "synapse_tau_ms": net.synapse_tau_ms,
            "inputs": inputs, "max_cells": sim.MAX_CELLS,
            "cell_columns": sim.CELL_COLUMNS, "rec_columns": sim.REC_COLUMNS, "kinds": sim.KINDS,
            **self.rates(),
        }

    def rates(self) -> dict:
        """Each cell's firing over the last 10 s (spikes/s), while acquiring."""
        be = self.live.backend
        running = bool(be.running)
        return {"running": running, "rates": be.rates(10.0) if running and hasattr(be, "rates") else None}

    # -- commands -------------------------------------------------------------------
    def command(self, d: dict) -> None:
        cmd = d.get("cmd")
        with self._lock:
            if cmd == "set":
                net = sim.copy_network(self.net)
                for e in d.get("edits", []):
                    self._edit(net, e)
                try:
                    sim.validate(net)
                except ValueError as e:      # its words are the config file's: "[simulated] cell 1: ..."
                    text = str(e).removeprefix("[simulated] ")
                    raise ValueError(text[:1].upper() + text[1:]) from None
            elif cmd == "cells":
                net = sim.copy_network(self.net)
                sim.resize(net, int(d["n"]))
            elif cmd == "reset":             # the network Newt starts with, recordings and all
                net = live_mod.make_cfg().sim_network
            else:
                raise ValueError(f"Unknown command {cmd!r}")
            # A new object, so the acquisition thread picks up the change all at once.
            self.live.cfg.sim_network = net

    def _edit(self, net: sim.Network, e: dict) -> None:
        n, value = len(net.cells), e.get("value", "")
        if "cell" in e:
            i, key = int(e["cell"]), e.get("key")
            if not 0 <= i < n or key not in _CELL_UNITS:
                raise ValueError("There is no such cell setting.")
            head, unit = _CELL_UNITS[key]
            setattr(net.cells[i], key, _number(value, unit, f"Cell {i + 1}: {head.lower()}"))
        elif "syn" in e:
            i, j = (int(x) for x in e["syn"])
            if i == j or not (0 <= i < n and 0 <= j < n):
                raise ValueError("A cell has no synapse onto itself.")
            w = net.weights()
            w[i, j] = _number(value, "mV", f"Synapse {i + 1}→{j + 1}")
            net.set_weights(w)
        elif "net" in e:
            key = e["net"]
            if key not in _NET_UNITS:
                raise ValueError("There is no such network setting.")
            head, unit, lo, hi = _NET_UNITS[key]
            v = _number(value, unit, head)
            if not lo <= v <= hi:
                raise ValueError(f"{head} must be {lo:g} to {hi:g} {unit}.")
            setattr(net, key, v)
        elif "input" in e:
            phys, key = str(e["input"]), e.get("key")
            if phys == self.live.cfg.stim_monitor:
                raise ValueError(f"{phys} records the stimulus.")
            rec = next((r for r in net.recordings if r.physical == phys), None)
            if rec is None:
                rec = sim.default_recording(phys, n)
                net.recordings.append(rec)
            if key == "cell":
                rec.cell = max(sim.NO_CELL, min(int(value), n - 1))
            elif key == "kind":
                if value not in KINDS:
                    raise ValueError("A recording is intracellular or extracellular.")
                rec.kind = value
            elif key in _REC_UNITS:
                head, unit = _REC_UNITS[key]
                setattr(rec, key, _number(value, unit, f"{phys} {head.lower()}"))
            else:
                raise ValueError("There is no such recording setting.")
        else:
            raise ValueError("Unknown edit.")
