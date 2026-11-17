from neusci_daq import groupsfile as G

AUTO = [("Pulse 1 V", [0, 1]), ("Pulse 2 V", [2, 3]), ("Pulse 4 V", [4]), ("Wave", [5])]
STEMS = [f"block{i}" for i in range(6)]


def names(d):
    return [(n, b) for n, b in G.apply(AUTO, STEMS, d)]


def test_groups_combine_and_split_back():
    d = G.combine(AUTO, G.load(None), ["Pulse 4 V", "Wave"])
    assert names(d) == [("Pulse 1 V", [0, 1]), ("Pulse 2 V", [2, 3]), ("Pulse 4 V + Wave", [4, 5])]
    assert G.kinds(AUTO, d) == {"Pulse 4 V + Wave": "combined"}
    d = G.combine(AUTO, d, ["Pulse 1 V", "Pulse 4 V + Wave"], "Most")      # a combined group joins another
    assert names(d) == [("Most", [0, 1, 4, 5]), ("Pulse 2 V", [2, 3])]
    assert G.auto_page(AUTO, 5, d) == "Most"
    d = G.split(AUTO, d, "Most")
    assert names(d) == names(G.load(None))                               # each under its stimulus's name
    assert G.combine(AUTO, d, ["Pulse 1 V"]) == d                        # one group: nothing to combine


def test_a_moved_trial_goes_with_its_group_when_combined():
    d = G.move(AUTO, STEMS, G.load(None), 0, "Wave")
    d = G.combine(AUTO, d, ["Wave", "Pulse 4 V"])
    assert names(d) == [("Pulse 1 V", [1]), ("Pulse 2 V", [2, 3]), ("Wave + Pulse 4 V", [0, 4, 5])]


def test_the_stimuli_the_map_joins_are_one_group_until_kept():
    d = {**G.load(None), "merged": [["Pulse 1 V", "Pulse 2 V"]], "alike": {**G.ALIKE, "feats": ["amp"], "n": 3}}
    assert names(d)[0] == ("Pulse 1 V + Pulse 2 V", [0, 1, 2, 3])
    assert G.kinds(AUTO, d) == {"Pulse 1 V + Pulse 2 V": "alike"}
    assert G.split(AUTO, d, "Pulse 1 V + Pulse 2 V") == d                # the map's: Keep first
    renamed = G.rename(AUTO, d, "Pulse 1 V + Pulse 2 V", "Small")
    assert names(renamed)[0] == ("Small", [0, 1, 2, 3])
    kept = G.keep(AUTO, d)
    assert kept["merged"] == [] and kept["alike"] == {**G.ALIKE, "feats": ["amp"]}    # measures stay ticked
    assert names(kept) == names(d) and G.kinds(AUTO, kept) == {"Pulse 1 V + Pulse 2 V": "combined"}
    assert names(G.split(AUTO, kept, "Pulse 1 V + Pulse 2 V")) == names(G.load(None))


def test_groups_sort_by_several_measures_each_way():
    shown = [("a", [0]), ("b", [1]), ("c", [2]), ("d", [3])]
    m = {0: {"amp": 1, "len": 5}, 1: {"amp": 2, "len": 1}, 2: {"amp": 1, "len": 9}, 3: {}}
    order = lambda sorts: [n for n, _ in G.arrange(shown, {"sorts": sorts}, lambda b: m[b[0]])]
    assert order([["amp", 1], ["len", -1]]) == ["c", "a", "b", "d"]       # ties by the next; none last
    assert order([["amp", -1], ["len", 1]]) == ["b", "a", "c", "d"]
    assert order([]) == ["a", "b", "c", "d"]


def test_old_sorts_still_load(tmp_path):
    (tmp_path / G.FILE).write_text('{"sort": "amplitude"}')
    assert G.load(tmp_path)["sorts"] == [["amp", 1]]
    (tmp_path / G.FILE).write_text('{"sort": "amplitude", "sorts": []}')
    assert G.load(tmp_path)["sorts"] == []
