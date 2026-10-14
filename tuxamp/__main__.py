import argparse
import os

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")


def main():
    p = argparse.ArgumentParser(prog="tuxamp", description="TuxAmp - a Winamp-style music player for Linux")
    p.add_argument("files", nargs="*", help="audio files, folders or .m3u/.pls playlists to play")
    p.add_argument("--scale", type=float, metavar="ZOOM", help="window zoom, 1 to 6, e.g. 2 or 1.5 (default: auto)")
    p.add_argument("--no-intro", action="store_true", help="skip the startup voice this time")
    p.add_argument("--framed", action="store_true", help="use normal window-manager decorations")
    args = p.parse_args()

    from .app import TuxAmp
    TuxAmp(args.files, scale=args.scale, framed=args.framed,
           intro=False if args.no_intro else None).run()


if __name__ == "__main__":
    main()
