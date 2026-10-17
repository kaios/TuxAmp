# TuxAmp

A Winamp 2.x–style music player for Linux. It has the classic main window,
the 10-band graphic equalizer, and the playlist editor, all drawn pixel-for-pixel at
Winamp's native 275px width, and smoothly zoomable to any size. It plays **SHOUTcast and
Icecast internet radio**, and it can stream your music to Chromecast, Google/Nest speakers
and DLNA smart TVs on your network.

**What's new in v0.1.7:**
- **SHOUTcast internet radio.** The new **SC** button on the main window opens the
  SHOUTcast window: browse the SHOUTcast directory's top stations, search it, save your
  favourite stations, or add any station by its address. See
  [SHOUTcast / internet radio](#shoutcast--internet-radio).
- **Double-click to pause.** Double-clicking the song that is playing in the playlist
  pauses it, and double-clicking it again resumes it, instead of restarting it.

v0.1.6 made the EQ seamless while casting: moving an EQ band slider or picking a preset
while you cast to a smart device no longer pauses the music.


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
Internet radio: **SHOUTcast / Icecast** MP3 streams (AAC, Ogg and HLS streams too when
ffmpeg is installed).

## Features

- Big LCD time display (click it to toggle elapsed/remaining), scrolling title marquee,
  kbps / kHz / mono / stereo readouts
- Spectrum analyzer with falling peaks, or an oscilloscope. Click the visualizer to switch modes.
- Seek bar, volume, and stereo balance
- Working 10-band EQ with preamp, a response-curve display, and the classic presets
  (Rock, Pop, Dance, Full Bass, ...). The EQ is applied in real time: the sound follows
  the preamp, a band slider or a preset while you drag or pick it (about 0.1 s behind),
  never pauses or skips, and the visualizers show the EQ'd sound. Grabbing a slider
  while the EQ is off switches it on. While casting, EQ changes never pause the music
  either.
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
  plus the ADD / REM / SEL / MISC / LIST menus and mini transport. Double-click a song
  to play it; double-click the song that is playing to pause it, and again to resume
  from the same spot.
- Shuffle and repeat; window-shade mode (double-click the title bar)
- Drag & drop files or folders onto the window
- **SHOUTcast / Icecast internet radio**: browse and search the SHOUTcast directory, save
  stations, add any station address; song titles from the station scroll in the display.
  See [SHOUTcast / internet radio](#shoutcast--internet-radio) below.
- **Cast / stream to smart devices** on your network (Chromecast, Google/Nest speakers,
  smart TVs, Sonos, Kodi). See [Casting / streaming to smart devices](#casting--streaming-to-smart-devices) below.
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
| Alt+S | Toggle SHOUTcast window | Ctrl+L | Add a station address (URL) |
| Enter | Play selected | Del | Remove selected |
| Ctrl+A | Select all | Ctrl+Q | Quit |
| Ctrl+Shift+C | Cast to device | K | TuxDrop visualizer |

Right-click anywhere for the main menu. The **Cast button** (the screen-with-waves icon
next to SC, EQ and PL) opens the cast menu; **SC** opens and closes the SHOUTcast window.
Clutterbar: **O** = menu, **D** = cycle size,
**V** = TuxDrop full-screen visualizer, **I** = file info.
Click the small visualizer to switch spectrum / oscilloscope / off.

## SHOUTcast / internet radio

TuxAmp plays SHOUTcast and Icecast radio stations, like Winamp did.

### The SHOUTcast window

Click **SC** on the main window (next to EQ and PL), or press **Alt+S**, to open and
close it.

- **TOP:** the SHOUTcast directory's most-listened stations right now. This is what you
  see the first time, before you have saved any stations.
- **Search:** type words in the box (a station name, a genre such as *jazz*, or an
  artist) and press **Enter** or **SEARCH**.
- **Play a station:** double-click it, or select it and press **PLAY**. It is added to
  the playlist and starts playing.
- **MINE:** your saved stations. Select a station in TOP or a search and press **SAVE** to
  keep it; select one in MINE and press **DEL** to remove it. Right-clicking a station
  does the same.
- **Add a station by its address:** paste or type the address (for example
  `http://ice1.somafm.com/groovesalad-128-mp3`) in the box and press **Enter** or
  **ADD URL**. It is saved to MINE and starts playing. **Ctrl+L**, or **ADD > Add URL** in
  the playlist, takes you straight to the box (with the address already filled in if
  one is on the clipboard). You can also drag a station link from a web browser onto
  TuxAmp, or open a `.pls` / `.m3u` station file.

Each list shows the station's name, bit rate and format. The station you are listening
to is shown in white.

### While a station plays

- The display scrolls the song now playing (sent by the station), followed by the
  station's name, and shows the station's bit rate and sample rate.
- **Pause** (or double-click the station in the playlist) pauses it; resuming carries on
  where you paused, for up to two minutes. After that TuxAmp catches up with the live
  broadcast. **Stop** hangs up; **Play** tunes in again.
- The **EQ, balance and visualizers** all work on radio.
- If the station's connection drops, TuxAmp reconnects by itself and keeps playing.
- If a station can't be played, the display says why (for example *connection
  refused*), and TuxAmp stays on it rather than skipping ahead. Directory listings are
  not always up to date, so try another station.
- **Casting** a station sends the station's own address to the device, which tunes in
  by itself. TuxAmp's EQ is not applied to a cast station.

### Station formats

- **MP3 stations** (most SHOUTcast stations) play with nothing else installed.
- **AAC / AAC+, Ogg and HLS stations** need **ffmpeg**: `sudo apt install ffmpeg`. Without
  it, those stations are shown in darker green in the list, and playing one tells you
  that ffmpeg is needed.

## Casting / streaming to smart devices

TuxAmp can stream what it is playing to a speaker or TV on your home network, and you
keep controlling everything from TuxAmp.

### Supported devices

| Kind | Examples |
|------|----------|
| **Google Cast** | Chromecast, Google Home / Nest speakers and displays, speaker groups, Android TV / Google TV (e.g. NVIDIA SHIELD), TVs and soundbars with Chromecast built in |
| **DLNA / UPnP renderers** | Most smart TVs (Samsung, LG, Sony, ...), Sonos, Kodi, many AV receivers |

### How to cast

1. Click the **Cast button** on the main window: the screen-with-waves icon to the
   left of the SC, EQ and PL buttons. You can also right-click and choose
   **Cast to device...**, or press **Ctrl+Shift+C**.
2. TuxAmp searches your network for about 3 seconds, and devices appear in the menu as
   they are found (for example *Living room (Chromecast)*). **Search again** refreshes
   the list.
3. Click a device. If a song is playing, it moves to the device and carries on from the
   same spot. The Cast button's light turns on (it blinks while connecting and
   buffering), and the title display shows `[CAST: device name]`.
4. To bring the sound back, click the Cast button again and choose **This computer**. Playback
   carries on from the same spot on your PC.

### While casting

- **Play, pause, stop, seek, previous / next** all control the device, and the playlist
  moves on to the next track by itself.
- **Volume slider:** sets the device's own volume. TuxAmp only changes it once you move
  the slider, so starting a cast never makes the device suddenly louder or quieter
  (unless the EQ is on with the preamp away from 0 dB; see below).
- **Preamp:** while casting, the preamp works through the device's volume, so it is
  heard instantly with no pause. It can turn the device down as far as you like, but it
  can't make it louder than the device's maximum.
- **EQ bands, presets and balance** are applied to what the device plays, with no pause
  and no buffering. TuxAmp sends the music only about 3 seconds ahead of the device and
  applies the EQ as it sends, so a change fades in on the device about 3 seconds later.
  (A device's own buffer means it can't be instant; the preamp is, because it uses the
  device's volume.)
- **Visualizers** (the small one and TuxDrop) stay in time with the device.
- **Every format TuxAmp plays can be cast**, including FLAC, Opus, OGG and tracker
  modules, because TuxAmp sends the decoded audio, not the original file.
- Closing TuxAmp stops playback on the device and returns it to its home screen.

### How it works

TuxAmp finds Google Cast devices with mDNS and DLNA devices with SSDP. It then runs a
small built-in web server, and the device fetches the music from it as a WAV stream at
CD quality (44.1 kHz, 16-bit). The stream is sent at playback speed, a few seconds ahead
of the device, so EQ changes reach it without restarting the stream. No internet connection, account or extra software is
needed; everything stays on your local network.

### Good to know / troubleshooting

- Your computer and the device must be on the **same network** (same Wi-Fi / router).
- **No devices found?** Some routers block device discovery between Wi-Fi and wired
  connections, or on "guest" networks. Try **Search again** after a few seconds.
- **Firewall:** the device downloads the music from your computer on **TCP port 47900**.
  If you use a firewall (for example `ufw`), allow it from your network:
  `sudo ufw allow from 192.168.0.0/16 to any port 47900 proto tcp`
- **Seeking** restarts the stream to the device, so expect a second or two of buffering
  there. EQ sliders, presets, the preamp, volume and balance never do.
- **Casting takes over** whatever the device was playing.
- If the device stops answering, TuxAmp shows *CAST FAILED* and carries on playing on
  your computer.

## Credits

TuxAmp v0.1.7 alpha, released 2026-10-17, by **Shawn Rapaz**.
See `BUGFIXES.md` for what each release fixed.
Coded with the help of Claude AI (Anthropic).

An independent tribute to Nullsoft's Winamp 2.x and its MilkDrop/AVS visualizers;
it contains no Winamp code or artwork. See `CREDITS` for bundled third-party software.

## Building the .deb

```sh
./build-deb.sh      # -> tuxamp_0.1.7-alpha_amd64.deb (needs PyInstaller, pygame, numpy)
```
