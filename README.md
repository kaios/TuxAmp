# TuxAmp

A Winamp 2.x–style music player for Linux. It has the classic main window,
the 10-band graphic equalizer, and the playlist editor, all drawn pixel-for-pixel at
Winamp's native 275px width, and smoothly zoomable to any size. It can also
stream your music to Chromecast, Google/Nest speakers and DLNA smart TVs on your network.


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
- **Cast / stream to smart devices** on your network (Chromecast, Google/Nest speakers,
  smart TVs, Sonos, Kodi). See [Casting to smart devices](#casting-to-smart-devices) below.
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

## Casting to smart devices

TuxAmp can stream what it is playing to a speaker or TV on your home network, and you
keep controlling everything from TuxAmp.

### Supported devices

| Kind | Examples |
|------|----------|
| **Google Cast** | Chromecast, Google Home / Nest speakers and displays, speaker groups, Android TV / Google TV (e.g. NVIDIA SHIELD), TVs and soundbars with Chromecast built in |
| **DLNA / UPnP renderers** | Most smart TVs (Samsung, LG, Sony, ...), Sonos, Kodi, many AV receivers |

### How to cast

1. Right-click TuxAmp and choose **Cast to device...**, or press **Ctrl+Shift+C**.
2. TuxAmp searches your network for about 3 seconds, and devices appear in the menu as
   they are found (for example *Living room (Chromecast)*). **Search again** refreshes
   the list.
3. Click a device. If a song is playing, it moves to the device and carries on from the
   same spot. The title display shows `[CAST: device name]`.
4. To bring the sound back, open the same menu and choose **This computer**. Playback
   carries on from the same spot on your PC.

### While casting

- **Play, pause, stop, seek, previous / next** all control the device, and the playlist
  moves on to the next track by itself.
- **Volume slider:** sets the device's own volume. TuxAmp only changes it once you move
  the slider, so starting a cast never makes the device suddenly louder or quieter.
- **Equalizer and balance** are applied to what the device plays.
- **Visualizers** (the small one and TuxDrop) stay in time with the device.
- **Every format TuxAmp plays can be cast**, including FLAC, Opus, OGG and tracker
  modules, because TuxAmp sends the decoded audio, not the original file.
- Closing TuxAmp stops playback on the device and returns it to its home screen.

### How it works

TuxAmp finds Google Cast devices with mDNS and DLNA devices with SSDP. It then runs a
small built-in web server, and the device fetches the music from it as a WAV stream at
CD quality (44.1 kHz, 16-bit). No internet connection, account or extra software is
needed; everything stays on your local network.

### Good to know / troubleshooting

- Your computer and the device must be on the **same network** (same Wi-Fi / router).
- **No devices found?** Some routers block device discovery between Wi-Fi and wired
  connections, or on "guest" networks. Try **Search again** after a few seconds.
- **Firewall:** the device downloads the music from your computer on **TCP port 47900**.
  If you use a firewall (for example `ufw`), allow it from your network:
  `sudo ufw allow from 192.168.0.0/16 to any port 47900 proto tcp`
- **Seeking or changing the EQ** restarts the stream, so expect a second or two of
  buffering on the device.
- **Casting takes over** whatever the device was playing.
- If the device stops answering, TuxAmp shows *CAST FAILED* and carries on playing on
  your computer.

## Credits

TuxAmp v0.1.1 alpha, released 2026-10-15, by **Shawn Rapaz**.
See `BUGFIXES.md` for what each release fixed.
Coded with the help of Claude AI (Anthropic).

An independent tribute to Nullsoft's Winamp 2.x and its MilkDrop/AVS visualizers;
it contains no Winamp code or artwork. See `CREDITS` for bundled third-party software.

## Building the .deb

```sh
./build-deb.sh      # -> tuxamp_0.1.1-alpha_amd64.deb (needs PyInstaller, pygame, numpy)
```
