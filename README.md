# TuxAmp

A Winamp 2.x–style music player for Linux. It has the classic main window,
the 10-band graphic equalizer, and the playlist editor, all drawn pixel-for-pixel at
Winamp's native 275px width, and smoothly zoomable to any size.


## Run

```sh
./tuxamp.py                       # restores your last playlist
./tuxamp.py song.mp3 ~/Music      # play files / folders / .m3u / .pls
./tuxamp.py --scale 3             # bigger window
./tuxamp.py --framed              # use window-manager borders instead of the skin's title bar
./install.sh                      # add TuxAmp to your desktop app menu
```

Requirements: Python 3, `pygame` 2.x, `numpy` (`pip install -r requirements.txt`).

## Formats

Anything SDL_mixer can decode: **MP3, WAV, OGG Vorbis, FLAC, Opus, AIFF, VOC,
MOD/XM/IT/S3M**, and MIDI (needs a soundfont/timidity). Playlists: **M3U/M3U8/PLS**.

## Features

- Big LCD time display (click it to toggle elapsed/remaining), scrolling title marquee,
  kbps / kHz / mono / stereo readouts
- Spectrum analyzer with falling peaks, or an oscilloscope. Click the visualizer to switch modes.
- Seek bar, volume, and stereo balance
- Working 10-band EQ with preamp, a response-curve display, and the classic presets
  (Rock, Pop, Dance, Full Bass, ...)
- **TuxDrop full-screen visualizer**, in the spirit of MilkDrop / AVS: feedback-warp
  effects, beat detection, 9 presets that cycle automatically
  (Hyperspace Tunnel, Black Hole Bloom, Kaleidoscope Dreams, Neon Rain, Plasma Pulse,
  Starfield Warp, Spiral Galaxy, Electric Flower, AVS Pulse Grid).
  Start it with **K**, **Ctrl+Shift+K**, the clutterbar **V**, or by double-clicking the small
  visualizer. Inside: Esc exit, Space/Backspace next/previous preset, L lock preset,
  F or double-click toggles full screen / window, Z X C V B still control playback.
- Smooth zoom: drag the bottom-right corner to scale the main window, equalizer and
  playlist together, to any size from 1x up to what fits on screen (Size 1x/2x/3x in the menu)
- Move the window by dragging any title bar; it follows the mouse exactly
- Playlist editor: multi-select, sort, randomize, crop, save/load M3U,
  plus the ADD / REM / SEL / MISC / LIST menus and mini transport
- Shuffle and repeat; window-shade mode (double-click the title bar)
- Drag & drop files or folders onto the window
- **Cast to smart devices** on your network: Chromecast, Google / Nest speakers and
  displays, Android TV / Google TV, TVs with Chromecast built in, and DLNA renderers
  (most smart TVs, Sonos, Kodi). Right-click > **Cast to device...** or **Ctrl+Shift+C**,
  then pick a device; pick **This computer** to bring the sound back. Play, pause, stop,
  seek, next/previous and the volume slider all control the device. The EQ and balance
  are applied to what the device plays, and the visualizers follow it.
- Startup voice: "TuxAmp, it really whips the penguin's ass!" (click the lightning-bolt logo to
  hear it again; turn it off in the right-click menu or with `--no-intro`)
- Remembers volume, EQ, playlist, and window layout in `~/.config/tuxamp/config.json`

## Keyboard (Winamp-compatible)

| Key | Action | Key | Action |
|-----|--------|-----|--------|
| Z | Previous | X | Play |
| C / Space | Pause | V | Stop |
| B | Next | L | Open file(s) |
| S | Shuffle | R | Repeat |
| ← / → | Seek 5s | ↑ / ↓ | Volume (or move selection in the playlist) |
| Alt+G | Toggle EQ | Alt+E | Toggle playlist |
| Enter | Play selected | Del | Remove selected |
| Ctrl+A | Select all | Ctrl+Q | Quit |
| Ctrl+Shift+C | Cast to device | K | TuxDrop visualizer |

Right-click anywhere for the main menu. Clutterbar: **O** = menu, **D** = cycle size,
**V** = TuxDrop full-screen visualizer, **I** = file info.
Click the small visualizer to switch spectrum / oscilloscope / off.

## Casting notes

- The computer and the device must be on the same network. TuxAmp finds devices with
  mDNS (Chromecast) and SSDP (DLNA), and the device fetches the audio from TuxAmp over
  HTTP on port 47900. If you run a firewall, allow incoming TCP 47900 from your network.
- Tracks are sent as WAV with your EQ applied, so every format TuxAmp plays can be cast.
  Seeking or changing the EQ restarts the stream, so expect a second or two of buffering.
- The volume slider sets the device's own volume, but only once you move it, so starting
  a cast never changes how loud the device is.
- Casting to a device takes over whatever it was playing.

## Credits

TuxAmp v0.1.0 alpha, released 2026-10-14, by **Shawn Rapaz**.
See `BUGFIXES.md` for what each release fixed.
Coded with the help of Claude AI (Anthropic).

An independent tribute to Nullsoft's Winamp 2.x and its MilkDrop/AVS visualizers;
it contains no Winamp code or artwork. See `CREDITS` for bundled third-party software.

## Building the .deb

```sh
./build-deb.sh      # -> tuxamp_0.1.0-alpha_amd64.deb (needs PyInstaller, pygame, numpy)
```
