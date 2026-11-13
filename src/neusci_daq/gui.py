"""Chart-recorder window (pyqtgraph) with live and review modes.

Live mode: acquire, display, record blocks, add comments.
Review mode: open a saved session and scroll through its blocks.

File menu:
  New        start a blank session (asks first if the current one is unsaved)
  Open…      review a saved session
  Open .mat File…  review a LabChart export (or one of ours); the file isn't changed
  Append…    keep recording new blocks into a saved session
  Save       first time: choose a name and location; afterwards: update the .mat
  Save As…   save a copy under a new name and continue with the copy
  Export…    write the session's blocks to a .mat file anywhere
  Recent Sessions

Setup menu:
  Input wiring   RSE (8 inputs vs AI GND) or DIFF (4 inputs, aiN minus ai(N+4))
  Input device   the NI DAQ, the simulation, or a USB audio input (audio.py)

Audio/Video menu: listen to a channel, play the shown part of a recorded block (Space),
the output device, and the Camera panel (video.py): a USB camera, recorded with each
block and played back in step in review.

The toolbar's Sampling rate box sets samples per second per channel. The
status bar always shows the wiring mode.

Recording always writes blocks to disk immediately. Until a session is saved,
its blocks live in <data_dir>/unsaved/<session>/, so nothing is lost if the
program crashes or someone chooses Don't Save.
"""
from __future__ import annotations

import queue
import sys
import time
from pathlib import Path

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import config as cfgmod
from . import simulation as sim
from . import style, theme
from .backends import Backend, make_backend
from .channels import ChannelRow, ChannelStrip, ColorButton
from .comments import CommentsPanel
from .config import Config
from .plotzoom import AxisGroup, TraceCurve
from .recorder import Recorder
from .stimout import make_output
from .analysispage import AnalysisPage
from .trialsbrowser import TrialsBrowser
from .trialstore import TrialStore
from .ringbuffer import RingBuffer
from .simpage import SimOffPage, SimPage
from .breadpage import BreadboardPage
from .membranepage import AP_PAGE, RC_PAGE, MembranePage   # imports QtWebEngine, which must come before the QApplication
from .stimwindow import StimPage
from .protocolspage import ProtocolsPage
from .stimuluspage import StimulusPage
from .tabs import ONE, SIDE, STACKED, TabArea, layout_icon
from . import colors
from .channelsetup import ChannelSetupMixin
from .commentcontrol import CommentsMixin
from .codeview import ShowTheCode
from .liveview import LiveMixin
from .mainparts import LIVE, REVIEW, WINDOW_MAX, WindowBox, _CommentEdit  # noqa: F401 (LIVE, REVIEW: for importers)
from .mediacontrol import MediaMixin
from .reviewview import ReviewMixin
from .sessionfiles import SessionFilesMixin
from .stimcontrol import StimulatorMixin
from .units import whole


class MainWindow(LiveMixin, ChannelSetupMixin, SessionFilesMixin, ReviewMixin, StimulatorMixin, CommentsMixin,
                 MediaMixin, QtWidgets.QMainWindow):
    """The window. Its parts are in their own modules: live view (liveview.py), channels
    (channelsetup.py), sessions and files (sessionfiles.py), review (reviewview.py), the
    stimulator (stimcontrol.py), comments (commentcontrol.py), and audio, video and devices
    (mediacontrol.py). Here: building the window, its menus and tabs, the theme, closing."""

    def __init__(self, cfg: Config, settings: QtCore.QSettings | None = None,
                 config_path: str | Path | None = None):
        super().__init__()
        self.cfg = cfg   # shared with the backend and recorder; channel changes update it in place
        self.config_path = Path(config_path) if config_path else None
        self.settings = settings or QtCore.QSettings("neusci", "neusci-daq-visualizer")
        self.store = TrialStore()    # recorded blocks, read once for review, Blocks and Analysis
        self._axis_group = AxisGroup()   # the plots' left axes, one width, so the plots line up
        self.backend: Backend = make_backend(cfg)
        self.display_q: queue.Queue = self.backend.subscribe()
        self.recorder = Recorder(self.backend)
        self.markers: list[tuple[float, list[pg.InfiniteLine]]] = []
        self.mode = LIVE
        # Live session: each Record press is one block, like LabChart.
        self.session_dir: Path | None = None
        self.session_saved = False   # False while it lives in data/unsaved/
        self.newt_path: Path | None = None       # the .newt file this session was saved in or opened from
        self._newt_folder: Path | None = None    # ...for this session folder
        self._newt_files: dict | None = None     # what the folder held when it was saved (newtfile.files_of)
        self.blocks: list[Path] = []
        self._exported_n = 0
        # Review session.
        self.review_dir: Path | None = None
        self._review_source: Path | None = None   # the .mat an imported session came from
        self._live_state = None              # the live session, while browsing its own blocks
        self._overlay_sel: list[int] = []    # blocks overlaid on the review plots (2 or more)
        self._mirrors: list = []             # other channels' copies in the first plot (Overlay channels)
        self._calcs: list = []               # (Calculation, Formula or None, problem) for the plots shown
        self.calc_ring: RingBuffer | None = None   # live calculated values, beside the ring buffer
        self._plot_names: list[str] = []     # what each plot shows: channels, then calculations
        self._overlay_items: list = []
        self.review_blocks: list[Path] = []
        self.review_block: dict | None = None
        self.review_color_btns: list[ColorButton] = []
        # "Recolor recorded blocks?" answer remembered for this session: None (ask), True, False.
        self._recolor_policy: bool | None = None
        self._live_comments_dirty = False    # a saved session's .mat needs re-exporting
        self._review_mat_stale = False
        self._shown_comments: list[dict] = []
        # Live time axis: stream time shown as 0. It's the start of the block
        # being (or last) recorded, so live times match the saved comment times.
        self._axis_zero = 0.0
        self._block_on_screen = False      # is the last block's data in this run?
        self._popup = None
        # The channel setup the user edits in the strips next to each plot.
        colors.fill_colors(cfg.channels + cfg.spare_channels)
        self.rows = [ChannelRow(c) for c in cfg.channels + cfg.spare_channels]

        # Stimulator
        self.stim_out = make_output(cfg, self.backend)
        self.stimulus = self._saved_stimulus()
        self.stimulus2: Stimulus | None = self._saved_stimulus("stimulus2")   # on the other output
        self.stim_repeat_s = float(self.settings.value("stim_repeat_s", 1.0))
        # What pressing Start does to the stimulator: "" nothing, "once", or "repeat".
        on_start = self.settings.value("stim_on_start", "")
        self.stim_on_start = on_start if on_start in ("", "once", "repeat") else ""
        self.repeating = False
        self.stim_timer = QtCore.QTimer(self, timeout=self._repeat_fire)
        self._compact_strips = False     # True while Acquisition shares the screen (split view)

        mode = self.settings.value("plot_theme", theme.DARK)
        self.plot_mode = mode if mode in theme.THEMES else theme.DARK
        # Off: each plot keeps its voltage range (fitted once when it first gets data).
        self.auto_scale_y = self.settings.value("auto_scale_y", True, type=bool)
        mode = self.settings.value("trace_mode", "mean")
        TraceCurve.mode = mode if mode in ("mean", "peak") else "mean"
        self._unfitted: set = set()      # plots still waiting for that first fit
        self._build_ui()
        self._build_menu()
        self._build_live_plots()
        self._apply_app_theme()
        self._alloc_buffers()
        self.timer = QtCore.QTimer(self, interval=33, timeout=self._refresh)
        self._rate_busy = False
        self._set_mode(LIVE)
        self._sync_setup_controls()
        self._fill_recorded()
        open_ = self.settings.value("comments_open", True, type=bool)
        self.btn_comments.setChecked(open_)
        self.comments_panel.setVisible(open_)
        self.refresh_comments()

    # ======================================================================
    # UI construction
    # ======================================================================
    def _build_ui(self):
        tb = self.addToolBar("Controls")
        tb.setMovable(False)
        self.btn_run = QtWidgets.QPushButton("Start", checkable=True)
        self.btn_run.setObjectName("start")
        self.btn_run.toggled.connect(self._toggle_run)
        self.btn_rec = QtWidgets.QPushButton("Record", checkable=True, enabled=False)
        self.btn_rec.setObjectName("record")
        self.btn_rec.setIcon(style.dot_icon(style.RECORDING))
        self.btn_rec.toggled.connect(self._toggle_rec)
        # Editable: pick a preset or type any rate, then press Enter.
        self.rate_box = QtWidgets.QComboBox(editable=True)
        self.rate_box.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.rate_box.addItems([whole(r) for r in cfgmod.RATE_PRESETS])
        self.rate_box.setMinimumContentsLength(8)
        self.rate_box.lineEdit().editingFinished.connect(self._rate_entered)
        self.rate_box.activated.connect(lambda *_: self._rate_entered())
        self.window_box = WindowBox(minimum=0.01, maximum=WINDOW_MAX, value=self.cfg.window_s,
                                    suffix=" s", decimals=4)    # 4: wheel zoom at 0.01 s needs 0.0135
        self.window_box.setToolTip("How many seconds the plots show")
        self.window_box.setMinimumWidth(76)
        self.window_box.valueChanged.connect(self._window_changed)
        self._window_wanted = None               # Window before the data's length cut it short
        self._fitting_window = False
        # Only usable while recording, because that's when a comment can be saved.
        self.comment_edit = _CommentEdit()
        self.comment_edit.setAccessibleName("Comment")
        self.comment_edit.setToolTip("Shortcut: press C to jump here")
        self.comment_edit.setMinimumWidth(90)
        self.comment_edit.returnPressed.connect(self._add_comment)
        self._set_comment_box(False)
        self.block_combo = QtWidgets.QComboBox()
        self.block_combo.setMinimumContentsLength(18)
        self.block_combo.currentIndexChanged.connect(self._show_review_block)
        self.btn_live = QtWidgets.QPushButton("Back to Live")
        self.btn_live.clicked.connect(self.back_to_live)
        self.btn_comments = QtWidgets.QPushButton("Comments (0)", checkable=True)
        self.btn_comments.setToolTip("Show or hide this trial's comments")
        self.btn_comments.toggled.connect(self._toggle_comments)

        # Toolbar groups: shown per mode (live/review) and per visible tab.
        A = lambda w: tb.addWidget(w)
        self.toolbar = tb
        self.lbl_rate, self.lbl_window = QtWidgets.QLabel("Sampling rate"), QtWidgets.QLabel("Window")
        self.rate_box.setToolTip("Sampling rate: samples per second, per channel")
        self._short_rec = False          # "0:12" instead of "Stop recording · 0:12" when space is short
        self._tb_live = [A(self.btn_run), A(self.btn_rec), tb.addSeparator(),
                         A(self.lbl_rate), A(self.rate_box)]
        self._act_lbl_rate = self._tb_live[3]
        # The block shown is picked in the Blocks panel (↑ ↓); block_combo just holds which.
        self._tb_review = []
        self._tb_acq = [A(self.lbl_window), A(self.window_box)]
        self._act_lbl_window = self._tb_acq[0]
        self._tb_acq_live = [tb.addSeparator(), A(self.comment_edit)]
        self._tb_review.append(A(self.btn_live))
        spacer = QtWidgets.QWidget()            # fills the space when the comment box is hidden
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
        self._tb_spacer = A(spacer)
        # Shown on every tab while a stimulus is going, so it can always be stopped.
        self.stim_chip = QtWidgets.QWidget()
        self.stim_chip.setObjectName("stimChip")
        self.stim_chip.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        cl = QtWidgets.QHBoxLayout(self.stim_chip)
        cl.setContentsMargins(10, 2, 4, 2)
        cl.setSpacing(6)
        dot = QtWidgets.QLabel()
        dot.setPixmap(style.dot_icon(style.START).pixmap(10, 10))
        self.stim_chip_text = QtWidgets.QLabel("Stimulating")
        self.stim_chip_text.setObjectName("stimChipText")
        self.btn_chip_stop = QtWidgets.QPushButton("Stop")
        self.btn_chip_stop.setToolTip("Stop the stimulus and set the output to 0 V (Esc)")
        self.btn_chip_stop.clicked.connect(self.stop_stimulus)
        cl.addWidget(dot); cl.addWidget(self.stim_chip_text); cl.addWidget(self.btn_chip_stop)
        self._tb_chip = A(self.stim_chip)
        # Split view: one tab, side by side, or top and bottom.
        self.split_group = QtWidgets.QWidget()
        self.split_group.setObjectName("splitGroup")
        self.split_group.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        sl = QtWidgets.QHBoxLayout(self.split_group)
        sl.setContentsMargins(2, 2, 2, 2)
        sl.setSpacing(2)
        self.split_buttons = {}
        group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)
        for mode, tip in ((ONE, "One tab"), (SIDE, "Split: side by side"), (STACKED, "Split: top and bottom")):
            b = QtWidgets.QToolButton(checkable=True)
            b.setToolTip(tip)
            b.setAccessibleName(tip)
            b.clicked.connect(lambda _=False, mode=mode: self.tabs.set_layout(mode))
            group.addButton(b)
            sl.addWidget(b)
            self.split_buttons[mode] = b
        self.split_buttons[ONE].setChecked(True)
        self.btn_theme = QtWidgets.QToolButton()
        self.btn_theme.setAutoRaise(True)
        self.btn_theme.setIconSize(QtCore.QSize(20, 20))
        self.btn_theme.clicked.connect(self.toggle_plot_theme)
        # Split buttons and the light/dark toggle sit at the right end of the toolbar,
        # on the same row as Start and Record, and show on every tab.
        self.tab_corner = QtWidgets.QWidget()
        cl2 = QtWidgets.QHBoxLayout(self.tab_corner)
        cl2.setContentsMargins(0, 0, 0, 0)
        cl2.setSpacing(6)
        cl2.addWidget(self.split_group)
        cl2.addWidget(self.btn_theme)
        self.logo = QtWidgets.QLabel()
        self.logo.setPixmap(app_icon().pixmap(QtCore.QSize(26, 26)))
        self.logo.setToolTip("NEWT")
        cl2.addWidget(self.logo)
        self._tb_corner = A(self.tab_corner)
        self._update_theme_button()
        sc = QtGui.QShortcut(QtGui.QKeySequence("C"), self)
        sc.activated.connect(self._focus_comment)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+F"), self, activated=lambda: self.fire_stimulus())
        QtGui.QShortcut(QtGui.QKeySequence("Esc"), self, activated=self._escape)
        # Text boxes keep their own arrow keys (Qt lets them claim the key first).
        # ← → step Analysis's block; ↑ ↓ step through the Acquisition Blocks panel.
        QtGui.QShortcut(QtGui.QKeySequence("Left"), self, activated=lambda: self.analysis_step(-1))
        QtGui.QShortcut(QtGui.QKeySequence("Right"), self, activated=lambda: self.analysis_step(1))
        QtGui.QShortcut(QtGui.QKeySequence("Up"), self, activated=lambda: self.step_block(-1))
        QtGui.QShortcut(QtGui.QKeySequence("Down"), self, activated=lambda: self.step_block(1))

        central = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.plot_area = QtWidgets.QWidget()
        self.plot_layout = QtWidgets.QVBoxLayout(self.plot_area)
        self.plot_layout.setContentsMargins(0, 0, 0, 0)
        self.plot_layout.setSpacing(2)
        self.plots, self.curves, self.strips = [], [], []
        self.plot_widgets, self.curve_colors, self.curve_widths = [], [], []
        self.row_widgets = []

        self.channel_bar = QtWidgets.QWidget()
        cb = QtWidgets.QHBoxLayout(self.channel_bar)
        cb.setContentsMargins(4, 2, 4, 2)
        self.btn_add_ch = QtWidgets.QPushButton("+ Add channel")
        self.btn_add_ch.clicked.connect(lambda: QtCore.QTimer.singleShot(0, self.add_channel))
        self.btn_save_setup = QtWidgets.QPushButton("Save channel setup…")
        self.btn_save_setup.setToolTip("Save these channels to a config file to reuse later")
        self.btn_save_setup.clicked.connect(self.save_setup_dialog)
        cb.addWidget(self.btn_add_ch)
        cb.addWidget(self.btn_save_setup)
        cb.addStretch(1)
        # Bottom of the Acquisition page: channel buttons (live only) on the left,
        # Comments on the right (live and review). It lives here rather than in the
        # toolbar so it's on screen whenever Acquisition is, even in a split.
        self.bottom_bar = QtWidgets.QWidget()
        bb = QtWidgets.QHBoxLayout(self.bottom_bar)
        bb.setContentsMargins(0, 0, 4, 0)
        bb.setSpacing(0)
        bb.addWidget(self.channel_bar, 1)
        bb.addStretch(0)
        self.btn_calc = QtWidgets.QPushButton("ƒ Analysis channels")
        self.btn_calc.setToolTip("Channels calculated from the others, like LabChart's channel calculations: "
                                 "digital filters, smoothing, RMS, derivative, integral, arithmetic, and "
                                 "cyclic measurements (rate meter, period, cyclic height, event count)")
        menu = QtWidgets.QMenu(self.btn_calc)
        self.act_add_calc = menu.addAction("Add analysis channel…", lambda: self.analysis_channel_dialog())
        self.act_add_calc.setToolTip("Pick a source channel and a calculation; it can use other analysis "
                                     "channels too")
        self.btn_calc.setMenu(menu)
        bb.addWidget(self.btn_calc)
        bb.addSpacing(6)
        self.btn_overlay_ch = QtWidgets.QPushButton("Overlay channels", checkable=True)
        self.btn_overlay_ch.setToolTip("Draw every channel in one plot, on the same time and voltage axes")
        self.btn_overlay_ch.setChecked(self.settings.value("overlay_channels", False, type=bool))
        self.btn_overlay_ch.toggled.connect(self.set_channel_overlay)
        bb.addWidget(self.btn_overlay_ch)
        bb.addSpacing(6)
        bb.addWidget(self.btn_comments)

        self.scroll = QtWidgets.QScrollBar(QtCore.Qt.Orientation.Horizontal)
        self.scroll.valueChanged.connect(self._scrolled)
        self.comments_panel = CommentsPanel()
        self.comments_panel.edited.connect(self.edit_comment)
        self.comments_panel.deleted.connect(self.delete_comment)
        self.comments_panel.jump.connect(self.jump_to)
        self.comments_panel.closed.connect(lambda: self.btn_comments.setChecked(False))
        # Blocks, always on screen: Live and every block recorded (or, in review, the
        # session's blocks), on their stimulus pages. Clicking one shows it.
        self.blocks_panel = TrialsBrowser()
        self.blocks_panel.activated.connect(self._activate)
        self.blocks_panel.selection.connect(self.overlay_blocks)
        self.blocks_panel.rename_requested.connect(self._rename_dialog)
        self.blocks_panel.move_requested.connect(
            lambda i, page: self.move_block(i, page) if page else self._new_page_dialog(i))
        self.blocks_panel.stim_channel_picked.connect(self.set_stim_channel)
        self.blocks_panel.reset_requested.connect(self._reset_pages_dialog)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.blocks_panel)
        row.addWidget(self.plot_area, 1)
        row.addWidget(self.comments_panel)
        from .video import CameraPanel
        self.camera_panel = CameraPanel()
        self.camera_panel.closed.connect(lambda: self.show_camera(False))
        self.camera_panel.hide()
        row.addWidget(self.camera_panel)
        lay.addLayout(row, 1)
        lay.addWidget(self.bottom_bar)
        lay.addWidget(self.scroll)
        self.stim_page = StimPage(self)
        self.trials_page = ProtocolsPage(self)
        self.stimulus_page = StimulusPage(self, self.stim_page, self.trials_page)   # the two, as one page
        self.analysis_page = AnalysisPage(self)
        self.blocks_panel.color_requested.connect(self.analysis_page.pick_color)
        self.blocks_panel.color_cleared.connect(lambda name: self.analysis_page.set_page_color(name, None))
        # What the fake recording contains: only with the simulated backend.
        self.sim_page = SimPage(self) if self.cfg.backend == "simulated" else None
        self.board_page = BreadboardPage(self) if self.cfg.backend == "simulated" else None
        # Data Simulation always shows: with a real input device, a note on switching to the simulators
        self.simoff_page = None if self.sim_page else SimOffPage(self)
        # Ions crossing a membrane, Nernst and GHK: a teaching page, always available.
        self.membrane_page = MembranePage(self)
        # The membrane as an RC circuit, then the same charging done by ions.
        self.rc_page = MembranePage(self, page=RC_PAGE, what="RC circuit")
        # A spike travelling along an axon, using both lessons above.
        self.ap_page = MembranePage(self, page=AP_PAGE, what="action potential")
        placeholder = lambda title, text: self._placeholder(title, text)
        # Record and Stimulus (the stimulator and its protocols) are sub-pages of Acquisition. Either half of
        # a split can show any page, two of Acquisition's included.
        self.tabs = TabArea([
            ("acq", "Record", central),
            ("stim", "Stimulus", self.stimulus_page),
            ("analysis", "Analysis", self.analysis_page),
        ] + ([("sim", "Neural network simulator", self.sim_page)] if self.sim_page else [])
          + ([("board", "Breadboard simulator", self.board_page)] if self.board_page else [])
          + ([("simoff", "Simulators", self.simoff_page)] if self.simoff_page else [])
          + [("membrane", "Membrane potential", self.membrane_page),
             ("rc", "RC circuit", self.rc_page),
             ("ap", "Action potential", self.ap_page)], groups=[
            ("acquisition", "Acquisition", ["acq", "stim"]),
            ("analysis", "Analysis", ["analysis"]),
            ("datasim", "Data Simulation", ["sim", "board", "simoff"]),   # data sources Acquisition can record
            ("lessons", "Lessons", ["membrane", "rc", "ap"]),
        ])
        self.tabs.changed.connect(self._tabs_changed)
        self.setCentralWidget(self.tabs)

        self.status = QtWidgets.QLabel()
        self.statusBar().addPermanentWidget(self.status)
        # Always visible, so a wrong wiring mode can't go unnoticed.
        self.wiring_label = QtWidgets.QLabel()
        self.wiring_label.setToolTip("Input wiring mode. Change it under Setup → Input wiring.")
        self.statusBar().addPermanentWidget(self.wiring_label)

    def _build_menu(self):
        m = self.menuBar().addMenu("&File")
        self.act_new = m.addAction("&New", self.new_session)
        self.act_new.setShortcut(QtGui.QKeySequence.StandardKey.New)
        self.act_open = m.addAction("&Open…", self.open_file_dialog)
        self.act_open.setShortcut(QtGui.QKeySequence.StandardKey.Open)
        self.act_open_folder = m.addAction("Open Session &Folder…", self.open_session_dialog)
        self.act_open_mat = m.addAction("Open .&mat File…", self.open_mat_dialog)
        self.act_open_mat.setToolTip("Review a LabChart export, or a .mat this app exported. "
                                     "The file itself is never changed.")
        self.act_append = m.addAction("&Append…", self.append_session_dialog)
        m.addSeparator()
        self.act_save = m.addAction("&Save", self.save)
        self.act_save.setShortcut(QtGui.QKeySequence.StandardKey.Save)
        self.act_save.setToolTip("Save everything in one .newt file, which reopens NEWT as it is now")
        self.act_save_as = m.addAction("Save &As…", self.save_file_dialog)
        self.act_save_as.setShortcut(QtGui.QKeySequence("Ctrl+Shift+S"))
        self.act_save_folder = m.addAction("Save Session F&older As…", self.save_as_dialog)
        self.act_export = m.addAction("&Export…", self.export_dialog)
        self.act_export.setShortcut(QtGui.QKeySequence("Ctrl+E"))
        m.addSeparator()
        self.recent_menu = m.addMenu("&Recent Sessions")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        m.aboutToShow.connect(self._update_menu_state)

        s = self.menuBar().addMenu("&Setup")
        self.wiring_menu = s.addMenu("Input &wiring")
        group = QtGui.QActionGroup(self)
        self.act_wiring = {}
        for mode, text, tip in (
                ("RSE", "RSE — up to 8 inputs (aiN vs AI GND)",
                 "Each input is measured against AI GND. Every input you use must have a "
                 "connection to AI GND, or it picks up mains hum."),
                ("DIFF", "DIFF — up to 4 inputs (aiN minus ai(N+4))",
                 "Each input is measured against its partner: ai0−ai4, ai1−ai5, ai2−ai6, ai3−ai7. "
                 "Rejects hum common to both wires.")):
            a = self.wiring_menu.addAction(text, lambda mode=mode: self.apply_setup(terminal_config=mode))
            a.setCheckable(True)
            a.setToolTip(tip)
            group.addAction(a)
            self.act_wiring[mode] = a
        self.wiring_menu.setToolTipsVisible(True)
        self.input_menu = s.addMenu("Input &device")
        self.input_menu.setToolTipsVisible(True)
        self.input_menu.aboutToShow.connect(self._fill_input_menu)

        av = self.menuBar().addMenu("&Audio/Video")
        self.listen_menu = av.addMenu("&Listen to")
        self.listen_menu.setToolTipsVisible(True)
        self.listen_menu.aboutToShow.connect(self._fill_listen_menu)
        self.act_play = av.addAction("&Play the shown part", self.toggle_playback)
        self.act_play.setShortcut(QtGui.QKeySequence("Space"))
        self.act_play.setToolTip("Review: play the channel listened to (or the first) from the left of the "
                                 "plots, with a cursor, and the trial's video in step. Space stops it")
        self.output_menu = av.addMenu("&Output device")
        self.output_menu.aboutToShow.connect(self._fill_output_menu)
        av.addSeparator()
        self.act_camera = av.addAction("&Camera panel", lambda: self.show_camera(self.act_camera.isChecked()))
        self.act_camera.setCheckable(True)
        av.setToolTipsVisible(True)
        self._listen = ""                      # the channel listened to ("" = none)
        self._audio_out = self.settings.value("audio_output", "")
        self._monitor = None
        self._player = None
        self._cursor_lines: list = []
        self._play_start = 0.0
        self._play_timer = QtCore.QTimer(self, interval=30, timeout=self._play_tick)

        v = self.menuBar().addMenu("&View")
        self.act_auto_y = v.addAction("&Auto-scale voltage")
        self.act_auto_y.setCheckable(True)
        self.act_auto_y.setChecked(self.auto_scale_y)
        self.act_auto_y.toggled.connect(self.set_auto_scale_y)
        self.act_auto_y.setToolTip("On: each plot's voltage axis keeps resizing to fit its trace. "
                                   "Off: it stays where it is (Cmd+scroll to zoom it, or Ctrl+drag a box; Ctrl+click goes back).")
        self.act_separate_y = v.addAction("Overlaid channels on &separate axes")
        self.act_separate_y.setCheckable(True)
        self.act_separate_y.setChecked(self.settings.value("overlay_separate_axes", True, type=bool))
        self.act_separate_y.toggled.connect(self.set_overlay_axes)
        self.act_separate_y.setToolTip("With Overlay channels: each channel gets its own y axis (in its "
                                       "color) and is scaled to fill the plot. Off: one shared axis.")
        tm = v.addMenu("&Zoomed-out traces")
        group = QtGui.QActionGroup(self)
        self.act_trace_mode = {}
        for key, text, tip in (
                ("mean", "&Average (smooth)",
                 "Each pixel shows the average of the samples it covers, a moving average. "
                 "Smooth, but spikes shrink when zoomed out."),
                ("peak", "&Min and max (keeps spikes)",
                 "Each pixel shows the lowest and highest sample it covers, so spikes stay visible.")):
            a = tm.addAction(text, lambda key=key: self.set_trace_mode(key))
            a.setCheckable(True)
            a.setChecked(TraceCurve.mode == key)
            a.setToolTip(tip)
            group.addAction(a)
            self.act_trace_mode[key] = a
        tm.setToolTipsVisible(True)
        v.setToolTipsVisible(True)

    # -- dark / light plots ------------------------------------------------
    def _set_curve_color(self, k: int, color: str) -> None:
        """Draw trace k in `color` (adjusted for contrast on the current background)."""
        self.curve_colors[k] = color
        self.curves[k].setPen(pg.mkPen(colors.display_color(color, self.plot_mode), width=self.curve_widths[k]))

    def _channel_width(self, strip: ChannelStrip, width: float) -> None:
        self.set_channel_width(self.rows.index(strip.row), width)

    def set_channel_width(self, index: int, width: float) -> None:
        """Change a channel's line thickness (display only; allowed while recording)."""
        from .channels import LINE_MAX, LINE_MIN
        width = min(LINE_MAX, max(LINE_MIN, float(width)))
        ch = self.rows[index].ch
        ch.width = width
        if ch in self.cfg.channels:
            k = self.cfg.channels.index(ch)
            if self.mode == LIVE and k < len(self.curves):
                self.curve_widths[k] = width
                self._set_curve_color(k, self.curve_colors[k])

    def _style_plot(self, pw: pg.PlotWidget) -> None:
        th = theme.THEMES[self.plot_mode]
        pw.setBackground(th.background)
        for name in ("left", "bottom"):
            ax = pw.getPlotItem().getAxis(name)
            ax.setPen(th.foreground)
            ax.setTextPen(th.foreground)

    def _update_theme_button(self) -> None:
        ink = style.tokens(self._app_mode())["ink"]
        self.btn_theme.setIcon(theme.toggle_icon(self.plot_mode, ink))
        self.btn_theme.setToolTip("Dark mode: click for light mode" if self.plot_mode == theme.DARK
                                  else "Light mode: click for dark mode")
        for mode, b in getattr(self, "split_buttons", {}).items():
            b.setIcon(layout_icon(mode, ink))

    def _app_mode(self) -> str:
        return style.DARK if self.plot_mode == theme.DARK else style.LIGHT

    def _apply_app_theme(self) -> None:
        """The window's own colors (the plots are styled separately)."""
        mode = self._app_mode()
        style.restyle(mode)
        self.comments_panel.link_color = style.tokens(mode)["link"]
        self.stim_page.apply_theme(mode)
        if hasattr(self, "trials_page"):
            self.trials_page.apply_theme(mode)
        if getattr(self, "sim_page", None):
            self.sim_page.apply_theme(mode)
        if getattr(self, "board_page", None):
            self.board_page.apply_theme(mode)
        if getattr(self, "membrane_page", None):
            self.membrane_page.apply_theme(mode)
        if getattr(self, "rc_page", None):
            self.rc_page.apply_theme(mode)
        if getattr(self, "ap_page", None):
            self.ap_page.apply_theme(mode)
        if hasattr(self, "analysis_page"):
            self.analysis_page.apply_theme(mode)
        self._update_theme_button()
        self.refresh_comments(redraw_markers=False)

    def toggle_plot_theme(self) -> None:
        self.set_plot_theme(theme.LIGHT if self.plot_mode == theme.DARK else theme.DARK)

    def set_plot_theme(self, mode: str) -> None:
        """Switch the whole app, plots included, between dark and light. Remembered on this computer."""
        self.plot_mode = mode
        self.settings.setValue("plot_theme", mode)
        for pw in self.plot_widgets:
            self._style_plot(pw)
        for k, color in enumerate(self.curve_colors):
            self._set_curve_color(k, color)
        if self._multi is not None:
            self._multi.recolor()
        mc = theme.THEMES[mode].marker
        for _, lines in self.markers:
            for ln in lines:
                ln.setPen(pg.mkPen(mc, style=QtCore.Qt.PenStyle.DashLine))
                if getattr(ln, "label", None) is not None:   # only the top plot's line has one
                    ln.label.setColor(mc)
        self._apply_app_theme()

    # -- tabs ----------------------------------------------------------------
    def _placeholder(self, title: str, text: str) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.addStretch(1)
        for t, big in ((title, True), (text, False)):
            lab = QtWidgets.QLabel(t)
            lab.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            if big:
                f = lab.font(); f.setPointSizeF(f.pointSizeF() * 1.4); f.setBold(True); lab.setFont(f)
            v.addWidget(lab)
        v.addStretch(1)
        return w

    def _tabs_changed(self):
        for mode, b in self.split_buttons.items():
            b.setChecked(mode == self.tabs.layout_mode)
        compact = self.tabs.shares_screen("acq")
        if compact != self._compact_strips:
            self._compact_strips = compact
            for rw in self.row_widgets:
                rw.set_compact(compact)
        self._update_toolbar()

    def _update_toolbar(self):
        """Show the controls that apply to what's on screen."""
        if not hasattr(self, "tabs"):
            return
        live, acq = self.mode == LIVE, self.tabs.is_visible("acq")
        for a in self._tb_live:
            a.setVisible(live)
        for a in self._tb_review:
            a.setVisible(not live and acq)
        for a in self._tb_acq:
            a.setVisible(acq)
        for a in self._tb_acq_live:
            a.setVisible(live and acq)
        self._tb_spacer.setVisible(not (live and acq))
        self._update_stim_chip()
        self._fit_toolbar()

    def _update_stim_chip(self):
        busy = self.stim_out is not None and self.stim_out.busy
        protocol = hasattr(self, "trials_page") and self.trials_page.running
        on = self.repeating or busy or protocol
        if protocol:
            self.stim_chip_text.setText(self.trials_page.chip_text())
            self.stim_chip.setToolTip("A protocol is running (Acquisition › Stimulus). Stop ends it.")
        elif on:
            self.stim_chip_text.setText("Stimulating")      # the interval is in the tooltip
            self.stim_chip.setToolTip(f"Repeating every {self.stim_repeat_s:g} s" if self.repeating
                                      else "A stimulus is being sent")
        if self._tb_chip.isVisible() != on:
            self._tb_chip.setVisible(on)
            self._fit_toolbar()

    def closeEvent(self, ev):
        if self.btn_rec.isChecked():
            self.btn_rec.setChecked(False)
        if not self._resolve_unsaved():
            ev.ignore()
            return
        self.trials_page.shutdown()          # stops a protocol; deletes its plots now
        self.stop_playback()
        if self._monitor is not None:
            self._monitor.stop()
        self.camera_panel.shutdown()
        self.recorder.stop()
        self.stim_timer.stop()
        if self.stim_out is not None:
            self.stim_out.stop()             # output to 0 V
        self.backend.stop()
        self._flush_review_mat()
        if self.session_saved:
            self._autoexport_live()
        self.recorder.close()   # end the writer thread before Qt and h5py shut down
        self.timer.stop()       # it draws on the plots, which are deleted next
        # Delete the plots now, in order. Left to Python's garbage collector, a
        # pyqtgraph plot can be freed while Qt still has a layout pass queued for
        # it, which crashes Qt (seen when windows are opened and closed repeatedly).
        self._clear_plot_area()
        self.stim_page.preview.setParent(None)
        self.stim_page.preview.deleteLater()
        super().closeEvent(ev)


ICON_DIR = Path(__file__).with_name("icons")


def app_icon() -> QtGui.QIcon:
    """The app's icon, in several sizes so it stays sharp from taskbar to title bar."""
    icon = QtGui.QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        f = ICON_DIR / f"icon_{size}.png"
        if f.exists():
            icon.addFile(str(f), QtCore.QSize(size, size))
    return icon


SPLASH_S = 3.0              # the logo shows for at least this long at start-up
_WINDOWS: list = []        # windows opened by Setup › Input device, kept alive


class _FileOpener(QtCore.QObject):
    """Files macOS asks NEWT to open (a double-clicked .newt file), kept until the window
    is ready."""

    def __init__(self, app):
        super().__init__(app)
        self.window, self.waiting = None, []
        app.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.Type.FileOpen and ev.file():
            if self.window is None:
                self.waiting.append(ev.file())
            else:
                self.window.open_requested(ev.file())
            return True
        return False

    def ready(self, window) -> None:
        self.window = window
        for path in self.waiting:
            window.open_requested(path)
        self.waiting = []


def run(cfg: Config, config_path: str | Path | None = None, open_path: str | None = None):
    app = pg.mkQApp("NEWT")
    opener = _FileOpener(app)
    ShowTheCode(app)                                   # Show the Code… in every right-click menu
    # pyqtgraph sets its own icon and Windows taskbar ID in mkQApp; replace both
    # before the window opens, or the taskbar keeps showing pyqtgraph's icon.
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("neusci.daq.visualizer")
    app.setWindowIcon(app_icon())
    splash = QtWidgets.QSplashScreen(QtGui.QPixmap(str(ICON_DIR / "logo.png")).scaledToWidth(
        420, QtCore.Qt.TransformationMode.SmoothTransformation))
    splash.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
    splash.show()
    app.processEvents()                                # the logo, while the window is made
    shown = time.monotonic()
    saved = QtCore.QSettings("neusci", "neusci-daq-visualizer").value("plot_theme", theme.DARK)
    style.apply(app, style.DARK if saved == theme.DARK else style.LIGHT)
    w = MainWindow(cfg, config_path=config_path)
    w.resize(1300, 200 + 180 * len(cfg.channels))    # the size it goes back to if un-maximized
    w.showMaximized()                                  # open filling the screen
    if open_path:
        opener.waiting.append(str(Path(open_path).resolve()))
    QtCore.QTimer.singleShot(0, lambda: opener.ready(w))
    left = SPLASH_S - (time.monotonic() - shown)       # long enough to see, on a fast computer too
    QtCore.QTimer.singleShot(max(0, int(left * 1000)), lambda: splash.finish(w))
    return app.exec()
