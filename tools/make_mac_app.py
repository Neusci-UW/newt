"""Make NEWT.app: a small Mac app that starts NEWT from this folder's .venv, so the Dock
and the app switcher say NEWT and show its icon, instead of python3.12, and .newt files
open in it with a double-click. Mac only: Windows runs NEWT as before.

    .venv/bin/python tools/make_mac_app.py [--backend simulated] [--to ~/Applications]

The app points at this folder, so make it again if the folder moves.
"""
import argparse
import plistlib
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "src" / "neusci_daq" / "icons"


def icns(to: Path) -> None:
    """The app's icon, from the PNGs, with macOS's iconutil."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "newt.iconset"
        folder.mkdir()
        for size in (16, 32, 128, 256, 512):
            shutil.copy(ICONS / f"icon_{size}.png", folder / f"icon_{size}x{size}.png")
        for size in (16, 32, 128, 256):
            shutil.copy(ICONS / f"icon_{size * 2 if size < 256 else 512}.png", folder / f"icon_{size}x{size}@2x.png") \
                if (ICONS / f"icon_{size * 2}.png").exists() else None
        subprocess.run(["iconutil", "-c", "icns", str(folder), "-o", str(to)], check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", help="the backend to start with (default: the one in config.toml)")
    ap.add_argument("--to", default=str(ROOT), help="the folder to put NEWT.app in")
    args = ap.parse_args()
    app = Path(args.to).expanduser() / "NEWT.app"
    if app.exists():
        shutil.rmtree(app)
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir()
    icns(app / "Contents" / "Resources" / "newt.icns")
    with (app / "Contents" / "Info.plist").open("wb") as f:
        plistlib.dump({"CFBundleName": "NEWT", "CFBundleDisplayName": "NEWT", "CFBundleExecutable": "NEWT",
                       "CFBundleIdentifier": "edu.uw.neuroscience.newt", "CFBundleIconFile": "newt",
                       "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "1.0",
                       "NSHighResolutionCapable": True,
                       "CFBundleDocumentTypes": [{"CFBundleTypeName": "NEWT file", "CFBundleTypeRole": "Editor",
                                                  "LSHandlerRank": "Owner", "CFBundleTypeIconFile": "newt",
                                                  "LSItemContentTypes": ["edu.uw.neuroscience.newt.file"]}],
                       "UTExportedTypeDeclarations": [{"UTTypeIdentifier": "edu.uw.neuroscience.newt.file",
                                                       "UTTypeDescription": "NEWT file",
                                                       "UTTypeConformsTo": ["public.data", "public.zip-archive"],
                                                       "UTTypeIconFile": "newt",
                                                       "UTTypeTagSpecification": {"public.filename-extension": ["newt", "argus"]}}],
                       "NSCameraUsageDescription": "NEWT records video with each trial.",
                       "NSMicrophoneUsageDescription": "NEWT can record from an audio input."}, f)
    backend = f" --backend {shlex.quote(args.backend)}" if args.backend else ""
    run = app / "Contents" / "MacOS" / "NEWT"
    run.write_text(f"#!/bin/sh\ncd {shlex.quote(str(ROOT))}\n"
                   f"exec .venv/bin/python -m neusci_daq -c config.toml{backend} \"$@\"\n")
    run.chmod(0o755)
    print(f"Made {app}")


if __name__ == "__main__":
    main()
