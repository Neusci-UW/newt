"""The DataPad's table model (padmodel.py): what it measures again, and what it keeps."""
import numpy as np

from neusci_daq import padmodel as P
from test_analysis import _FakeSource


class _Counting(_FakeSource):
    """Notes each trial whose trace is read to be measured."""
    read: list

    def trace(self, i, channel):
        self.read.append(i)
        return super().trace(i, channel)


def test_unticking_a_trial_measures_no_other_trial_again():
    pad, src = P.Pad(), _Counting()
    src.read = []
    pad.columns = [P.Column("Vrest", channel="Vm", sel=P.Selection.seg("A")),
                   P.Column("V", channel="Vm", sel=P.Selection("B", "B", "last", 50))]
    full = pad.compute(src)
    assert sorted(set(src.read)) == [0, 1, 2]
    src.read.clear()
    tab = pad.compute(src, skip={1})                 # trial 2 unticked: its row is empty, the others as they were
    assert src.read == []
    assert np.isnan(tab.values["V"][1]) and tab.values["V"][0] == full.values["V"][0]
    back = pad.compute(src)                          # ticked again: the same numbers, and nothing read
    assert src.read == []
    assert np.array_equal(back.values["V"], full.values["V"]) and np.array_equal(back.values["Vrest"], full.values["Vrest"])
    pad.data_changed()                               # the recordings changed (a gain, the baseline): measured again
    again = pad.compute(src)
    assert sorted(set(src.read)) == [0, 1, 2] and np.array_equal(again.values["V"], full.values["V"])
