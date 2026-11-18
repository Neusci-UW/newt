"""Newt's HTML front end: pages in Newt's look (the Lessons'), drawn by the system's web view
(pywebview), fed by the same plain-Python core as the Qt app. So far: the live acquisition
view. Run: python -m neusci_daq.web --run

  __main__.py   the window (or --browser / --serve)
  server.py     a local web server: the page, a binary frame stream, commands (keyed)
  live.py       the live view's Python half: backend, ring buffer, min/max frames
  static/       newt.css (the shared look), fonts/ (IBM Plex, local), vendor/ (uPlot),
                one .html + .js per page
"""
