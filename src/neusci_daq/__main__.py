import argparse
import sys

from . import config


def main(argv=None):
    ap = argparse.ArgumentParser(prog="neusci-daq", description=__doc__)
    ap.add_argument("file", nargs="?", help="an .newt file (or session folder, or .mat) to open")
    ap.add_argument("-c", "--config", default="config.toml")
    ap.add_argument("--backend", choices=["nidaqmx", "multichannelio", "simulated", "audio"],
                    help="override the backend in the config")
    ap.add_argument("--audio-device", help="audio backend: the input device (name, or part of it)")
    ap.add_argument("--list-devices", action="store_true", help="list NI and audio devices and exit")
    args = ap.parse_args(argv)

    if args.list_devices:
        try:
            import nidaqmx.system
            for d in nidaqmx.system.System.local().devices:
                print(f"{d.name}: {d.product_type}  AI: {[c.name for c in d.ai_physical_chans]}")
        except Exception as e:
            print(f"NI-DAQmx: {e}")
        from . import audio
        for d in audio.devices("input"):
            print(f"audio input: {d['name']} ({d['channels']} in, {d['rate']:,.0f} Hz)")
        return 0

    cfg = config.load(args.config)
    if args.audio_device is not None:
        cfg.audio_device = args.audio_device
    if args.backend:
        cfg.backend = args.backend
        if cfg.backend == "audio":
            from . import audio
            audio.use_audio_channels(cfg)
        config.validate(cfg)
    from .gui import run
    return run(cfg, config_path=args.config, open_path=args.file)


if __name__ == "__main__":
    sys.exit(main())
