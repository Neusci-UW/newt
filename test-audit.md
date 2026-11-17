# Test audit — 2026-11-17

Full suite: 474 tests, `.venv/bin/python -m pytest`, with a 60 s per-test limit and a per-test time/memory log.

## Result

| | Before the fix | After |
|---|---|---|
| Wall time | did not finish (stalled at test 174 after 2 h) | **13.8 min** |
| Peak memory | 7.8 GB and climbing | **1.0 GB** |
| Hung tests | 1+ | **0** |
| Outcome | — | 341 passed, **130 failed**, 3 skipped |

## 1. Leak (fixed, uncommitted)

Test windows were closed but never deleted. Each test left a whole MainWindow behind (plots, pages, timers), so memory grew ~35 MB per test and every test got slower than the last (4.5 s alone → 36 s at test 170).

Fix: an autouse `_free_windows` fixture in `tests/conftest.py` deletes leftover MainWindows after every test. A first version that deleted *all* top-level widgets segfaulted; MainWindow-only is safe.

## 2. Where the time goes now

| Test time | Tests |
|---|---|
| < 0.5 s | 181 |
| 0.5–2 s | 152 |
| 2–5 s | 107 |
| > 5 s | 34 |

| File | Tests | Seconds |
|---|---|---|
| test_analysis.py | 120 | 390 |
| test_layout.py | 58 | 123 |
| test_trials.py | 48 | 41 |
| test_stim.py | 37 | 41 |
| test_session.py | 15 | 40 |
| test_comments.py | 18 | 33 |

Slowest single tests: `test_layout::test_one_toggle_switches_window_and_plots` 12.3 s, `test_newtfile::test_save_reopens_newt_as_it_was` 8.7 s, the parametrized `test_layout` fit tests 7–8 s each (10 of them, 55 s total).

Candidates for speed (not done):
- **test_analysis.py** is half the run. Most tests build a full MainWindow and simulate a recording; a module-scoped recorded session they could share (read-only) would cut most of it.
- **`pump(app, s)`** waits fixed wall-clock time. Swapping fixed waits for "wait until X" (qtbot.waitUntil-style) would remove idle seconds across the suite.
- **test_layout fit tests** run every theme × font scale. Light/dark rarely changes geometry; dropping dark from the size checks would halve them.

## 3. Failures (130) — not yet triaged

Same failures appear with and without the fix, so the leak didn't cause them. By file: analysis 61, layout 28, stim 19, trials 10, session 4, comments 3, one each in sim, setup, newtfile, membrane, argusfile.

Most look like **tests behind the code** (renamed or moved UI), not app bugs:
- `StimPage` has no `p_amp`, `s_amp`, `s_dur`, `shape`, `out_tabs` (10 tests) — the Stimulus page was reworked.
- `KeyError: 'trials'` (6), `SettingsPanel` has no `groups`, `MainWindow` has no `save_argus`.
- Expected names differ: tab list `['acq','simple']` vs `['acq','stim']`, stimulus names `'20Hz 1V bursts'` vs `'Sine 20 Hz …'`, group names `'Baseline …'` vs `'Pulse 1 V …'`.
- Layout: `WindowBox` assertions (6) — something no longer fits the 1024 px / short-screen checks; worth a real look, as it may be a genuine layout regression.

Possibly real bugs:
- `IndexError: list index out of range` (13) — mostly in test_analysis; needs a look.
- `test_comments::test_axis_restarts_at_zero_when_recording_starts` — axis reads 9.15 s where ~0.3 s is expected after Record.
- `DID NOT RAISE ValueError`, `max() iterable argument is empty`, several `NoneType` attribute errors.

## Next steps to choose from
1. Commit the leak fix.
2. Triage the 130 failures file by file: update stale tests, fix real bugs.
3. Speed work from section 2.
