# TuxAmp bug fixes

Author: Shawn Rapaz. Coded with the help of Claude AI (Anthropic).

Every bug fixed so far, newest first. Details for each follow below.

| Version | Fixed |
|---|---|
| v0.1.7 | Double-clicking the playing song restarted it (now pauses / resumes) |
| v0.1.7 | 48 kHz radio stations: the decoded pieces drifted apart (found during development) |
| v0.1.7 | Off-air SHOUTcast mirrors stopped a station from playing (found during development) |
| v0.1.6 | EQ sliders and presets paused the music while casting |
| v0.1.6 | EQ / balance changes on a cast device could click |
| v0.1.5 | Preamp not real time; paused on release |
| v0.1.5 | Preamp did nothing while the EQ was off |
| v0.1.5 | Rare skipped piece of audio (click) while the EQ changed |
| v0.1.5 | Preamp change paused a cast device |
| v0.1.4 | Moving the preamp or an EQ slider paused the music |
| v0.1.4 | Balance was lost between pieces of audio (found during development) |
| v0.1.1 | Chromecast connection could break at random |
| v0.1.1 | Chromecast could start the track before its player was ready |
| v0.1.1 | README showed the wrong startup voice line |
| v0.1.0 | Crash (segmentation fault) clicking the equalizer or playlist |
| v0.1.0 | Window jumped around while being dragged |
| v0.1.0 | Drags stalled once the pointer left the window |
| v0.1.0 | Resizing changed only one window |
| v0.1.0 | Resizing was jerky and flickered |
| v0.1.0 | Resizing could nudge the window |
| v0.1.0 | Zooming could push the window off screen |
| v0.1.0 | Visualizer leaving full screen kept the full-screen size |

## v0.1.7 alpha (2026-10-17)

New in this release: SHOUTcast / Icecast internet radio, with an **SC** button on the
main window (see the README). The bugs below marked "found during development" were
found and fixed while building it, before release.

### Double-clicking the playing song in the playlist restarted it
- **Symptom:** double-clicking the song that was already playing started it again
  from the beginning.
- **Fix:** double-clicking the song that is playing now pauses it, and
  double-clicking it again resumes it from the same spot. Double-clicking any other
  song (or the current one when it is stopped) plays it from the start, as before.
  This also works while casting.
- **Tested:** with real mouse clicks on the playlist rows, a double-click on the
  playing song paused it (the position held still for a second), the next
  double-click resumed it from the same spot rather than the start, double-clicking
  another song played that song from the start, and double-clicking the stopped
  current song played it.

### 48 kHz radio stations: the decoded pieces drifted apart (found during development)
- **Symptom:** in testing, MP3 stations broadcast at 48 kHz (many European stations)
  had small jumps every half second.
- **Cause:** TuxAmp decodes a station in pieces of about half a second. At 44.1 kHz the
  pieces join exactly, but at 48 kHz the sound library also converts the sample rate,
  and its real timing differs very slightly from the nominal one, so a cut point worked
  out by arithmetic slipped by about 8 samples per piece.
- **Fix:** each piece is decoded with a little overlap, and TuxAmp finds the exact join
  by matching the overlapping audio, then blends over 64 samples.
- **Tested:** 44.1 kHz stations decode bit-for-bit identical to decoding the whole
  recording at once. For two 48 kHz stations the joins are as smooth as the music
  around them (no join stood out from normal music on a click detector).

### Off-air SHOUTcast mirrors stopped a station from playing (found during development)
- **Symptom:** some stations from the SHOUTcast directory failed with "too many
  redirects".
- **Cause:** a directory station lists several mirror servers. When one is off the air,
  SHOUTcast sends listeners to its web page instead of the audio, again and again.
- **Fix:** a redirect to a web page counts as "not on the air", and TuxAmp moves on to
  the station's next mirror (up to 12, within 25 seconds).

- **Radio tested** (a simulated SHOUTcast server, plus real stations over the internet):
  - The SC button opens the window; with no saved stations it shows the directory's
    top 199 stations.
  - Typing a station address and pressing Enter saved it and played it. The station's
    name, the song titles, and 128 kbps / 44 kHz appeared in the display; the clock ran
    in real time; the visualizer worked.
  - Double-clicking the station in the playlist paused it (the time held still), and
    double-clicking again resumed from the same spot.
  - The station dropped the connection: TuxAmp reconnected and played on with no gap.
  - Stop hung up; Play tuned in again. No underruns; the recorded audio had no gaps.
  - Casting a station sent the station's own address to the device as a live stream;
    stopping the cast tuned in again on the computer.
  - A station that is down showed "CANNOT PLAY ...: CONNECTION REFUSED" and TuxAmp
    stayed on it.
  - Searching the real directory for "jazz" found 200 stations; SAVE kept one. The
    first station in the results refused connections, and the second played.
  - Real stations played: SHOUTcast (HUBU.FM, Antenne Bayern via the directory),
    Icecast over https (NPO Radio 1, 48 kHz), and with ffmpeg also AAC+ (via the
    directory), AAC (SomaFM) and SRF 1.
  - Saved stations and station playlist entries survive a restart.
  - All earlier tests (casting, EQ, local playback, double-click) still pass.

## v0.1.6 alpha (2026-10-15)

### EQ sliders and presets paused the music while casting
- **Symptom:** while casting to a smart device, moving an EQ band slider or picking a
  preset made the music stop for a moment, then carry on.
- **Cause:** a cast device downloads the music ahead of where it is playing. TuxAmp
  sent it the whole song as fast as the device would take it, with the EQ already
  applied, so the only way to change the EQ was to send the stream again from the
  current spot. The device then stopped, buffered and restarted: the pause. (The
  preamp was already fixed in v0.1.5, by using the device's volume.)
- **Fix:** TuxAmp now sends the music at playback speed, only about 3 seconds ahead of
  the device, and applies the EQ (and balance) as each piece is sent. An EQ change is
  simply in the next piece the device gets, so the music never stops. It fades in on
  the device about 3 seconds after the change, because that is how much the device
  holds in its buffer. The stream is never re-sent for an EQ change. At the start of a
  song the device gets a 4-second head start (more if it waits longer before playing).
  If a device asks for a part of the song far ahead (some do, to look at the file),
  that part is sent at once.

### EQ / balance changes on a cast device could click
- **Symptom:** found while testing the fix above. A preset change could make a click
  on the device.
- **Cause:** once the stream was sent at playback speed, it went out in tiny pieces
  (a quarter of a millisecond), and the 12 ms fade between the old and new EQ was cut
  short at the end of each piece.
- **Fix:** the music for the device is made in whole 23 ms pieces (each made once and
  kept, however it is sent), so every change fades in fully, and the stream goes out
  in 0.2-second blocks.

- **Tested (simulated Chromecast and DLNA TV, a 4-minute song):**
  - Dragged an EQ band slider through the real slider code, picked the Classical,
    Full Bass and Live presets, paused for 5 seconds, resumed, then reset to flat. The
    stream was re-sent **0 times** and the device kept playing throughout.
  - Each change reached the device 2.8 to 3.0 seconds later.
  - The stream stayed 2.5 to 4.4 seconds ahead of the device and never ran short,
    including across the pause.
  - The device received exactly the same audio as an offline render (biggest sample
    step 757 in both), so there are no clicks or jumps. Before the second fix the
    preset changes made jumps of up to 13,566.
  - All 17 casting checks pass on both devices: cast, pause/resume, seek, volume, EQ,
    next track, back to this computer, stop, and recovery from a failing device.
  - Preamp while casting: the device volume still follows within 20 ms, with no
    re-send.
  - On this computer: dragging four EQ band sliders and picking seven presets, with
    the audio output recorded, showed no gaps, no jumps and no underruns. All the
    local playback tests pass, and the EQ still matches the original within 1 sample
    unit.

## v0.1.5 alpha (2026-10-15)

### The preamp still wasn't real time, and paused on release
- **Symptom:** dragging the preamp up or down didn't change the sound while dragging;
  the change (with a pause) came only after letting go.
- **What we found:** testing the real slider, with the real audio output recorded,
  showed four separate causes:
  1. **EQ off:** the preamp is part of the equalizer, so while the EQ was switched off
     the preamp did nothing at all, however far it was moved. (In v0.1.3 and earlier,
     letting go then also restarted playback: the pause.)
  2. **Lag:** with the EQ on, v0.1.4 followed the slider about 0.2 s behind, because the
     sound was prepared in 93 ms pieces with one more piece waiting behind it.
  3. **Skipped audio:** at the moment one piece ended, the mixer briefly reported the
     channel as empty while it started the next piece. If TuxAmp checked at that exact
     moment, it started another piece on top, so one piece was skipped (a click). It was
     rare, but more likely while the EQ was changing.
  4. **Casting:** an EQ change was only sent to the device on release, by re-sending the
     stream, which made the device pause and buffer.
- **Fix:**
  1. Grabbing the preamp or any EQ slider while the EQ is off switches it on (the
     display shows "EQUALIZER: ON").
  2. Pieces are now 23 ms, and changes fade in over 12 ms. TuxAmp also lets its audio
     thread run more often (every 1 ms), so the next piece is always ready in time,
     even with the full-screen visualizer running.
  3. The channel only counts as empty if it looks empty on two checks in a row.
  4. While casting, the preamp now changes the device's volume, which happens at once
     with no pause. Only EQ *band* changes still re-send the stream on release.
- **Tested:**
  - Dragged the real preamp slider (down 12 dB, hold, up 24 dB) with the EQ on and
    with it off, recording the real audio output: the level followed the slider the
    whole way, with no jumps, clicks or underruns, and no pause on release.
  - Delay from a slider move to the change being heard: 76 ms on average (110 ms at
    most), down from about 200 ms.
  - 8 seconds of preamp sweeping with the full-screen visualizer drawing 60 frames a
    second: no underruns and no gaps (smaller 512-frame pieces also passed, so there
    is at least a 2x safety margin).
  - Casting (simulated Chromecast and DLNA TV): dragging the preamp set the device's
    volume within 20 ms and did not re-send the stream; all 15 casting checks pass.
  - Pause, seek, balance, next track, EQ accuracy (matches the old EQ within 1 sample
    unit) all pass.

## v0.1.4 alpha (2026-10-15)

### Moving the preamp or an EQ slider made the music pause
- **Symptom:** sliding the preamp up or down (or any EQ band) briefly stopped the
  music, and it could happen again and again while you kept adjusting the slider.
- **Cause:** every EQ change re-filtered the *whole song* in the background and then
  stopped and restarted playback with the new version. Each restart dropped the sound
  for a moment while the sound card refilled, and could skip audio. A song in memory
  was also stored twice (with and without the EQ).
- **Fix:** TuxAmp now plays songs in short pieces (about 93 ms) and applies the EQ
  and preamp to each piece just before it is queued. A slider change is heard within
  a fraction of a second, cross-faded from the old setting so it never clicks, and
  playback is never stopped or restarted. Balance works the same way. While casting,
  the change is sent to the device once you let go of the slider (a cast device
  buffers ahead, so it needs the stream re-sent).
- **Tested:** recorded TuxAmp's real audio output while dragging the preamp in six
  steps with pauses, like a real drag. The old version restarted playback twice
  (two jumps in the waveform); the new one played straight through with no gaps or
  jumps, and each step faded to exactly the expected level. The new live EQ matches
  the old whole-song EQ to within 1 sample unit. The slowest main-loop frame went
  from 15 ms to 4 ms.

### Balance was lost between pieces of audio (found during development)
- **Symptom:** with the new piece-by-piece playback, the balance slider stopped
  working: the sound went back to the centre.
- **Cause:** the sound library forgets a channel's left/right setting every time a
  piece of audio ends.
- **Fix:** the balance is applied to the audio itself, in each piece, and fades
  smoothly when it changes.

## v0.1.3 alpha (2026-10-15)

No bug fixes in this release. It adds a Cast button to the main window.

## v0.1.2 alpha (2026-10-15)

No bug fixes in this release. It updates the README to feature streaming to smart
devices.

## v0.1.1 alpha (2026-10-15)

New in this release: casting to Chromecast / Google Cast and DLNA devices on the
local network (see the README).

### Chromecast connection could break at random
- **Symptom:** while casting to a Chromecast, the connection sometimes dropped right
  after starting a track, showing "CAST FAILED: CONNECTION TO THE DEVICE WAS LOST".
  In testing it happened about one time in three.
- **Cause:** the connection to a Chromecast is encrypted (TLS). TuxAmp read from it on
  one thread while sending commands on another, and the encryption library does not
  allow that: the two collided and garbled the data ("bad record MAC").
- **Fix:** a single thread now owns the connection and does all reading and sending;
  other parts of TuxAmp hand it messages through a queue.
- **Tested:** 8 full runs each against a simulated Chromecast and a simulated DLNA TV
  passed every check: casting, pause/resume, seek, volume, EQ change, next track,
  switching back to the computer, stop, and recovery from a failing device.

### Chromecast could start the track before its player was ready
- **Symptom:** in testing, casting to a Chromecast that was showing something else
  could fail to start the track.
- **Cause:** the Chromecast's first answer to "start the media player" can still
  describe the app it was running before.
- **Fix:** TuxAmp waits until the Chromecast reports that its media player is
  actually running before sending the track.

### README showed the wrong startup voice line
- **Fix:** it now says "TuxAmp, it really whips the penguin's ass!".

## v0.1.0 alpha (2026-10-14)

First release. These bugs were found and fixed during development.

### Crash when clicking the equalizer or playlist (Segmentation fault)
- **Symptom:** clicking the equalizer or playlist title bar closed TuxAmp with
  "Segmentation fault (core dumped)".
- **Cause:** a pygame bug. pygame keeps an internal pointer to its window
  object without holding on to it, so once Python freed that object, the next
  window event read freed memory.
- **Fix:** TuxAmp keeps every window object alive for the whole session.

### Window jumped around while being dragged
- **Symptom:** dragging TuxAmp by a title bar made the window jump around
  instead of following the mouse. It drifted away from the pointer, worst when
  the mouse moved fast.
- **Cause:** each step of the drag worked out the new position from where X
  *said* the window was. X answers late, especially when the display is
  forwarded over a network, so every step built on an out-of-date position and
  the errors added up.
- **Fix:** during a drag TuxAmp reads the mouse's position on the desktop
  directly, every frame (60 fps while dragging), and places the window at its
  starting position plus exactly how far the mouse has moved. It also remembers
  where it put the window instead of asking X. The window follows the mouse 1:1
  at any speed.
- **Tested:** a fast 240-pixel drag moved the window exactly 240 pixels, and
  dragging back returned it to its starting point.

### Drags stalled once the pointer left the window
- **Symptom:** dragging the bottom-right corner stopped following the mouse,
  and the drag sometimes stayed active after the button was released.
- **Cause:** once the pointer left the window, X stopped sending mouse events.
- **Fix:** TuxAmp reads the mouse position directly during the drag and keeps
  receiving mouse events while the pointer is outside the window.

### Resizing changed only one window
- **Symptom:** dragging the bottom-right corner resized only the playlist,
  leaving the main window and equalizer at their old size.
- **Fix:** the bottom-right corner (of whichever window is at the bottom) zooms
  the **main window, equalizer and playlist together**. They always keep the
  same width and stay lined up. The zoom is continuous, from 1x up to what fits
  on screen. When you let go next to a whole size (2x, 3x...) it settles on
  exactly that size, where the pixels are sharpest. In-between sizes are drawn by
  scaling up to the next whole size and then smoothly down, so they stay crisp.
  The Size 1x/2x/3x menu items and the D button also set the zoom.

### Resizing was jerky and flickered
- **Symptom:** while dragging the corner, the window jumped in big steps and
  flickered.
- **Cause 1:** the size could only jump between 1x, 2x and 3x.
- **Cause 2:** every step rebuilt the whole window with pygame's `set_mode()`,
  which took 100-160 ms. That is fewer than 10 updates a second.
- **Cause 3:** on every size change X wiped the window to its background colour
  before TuxAmp could redraw it. That blank frame is the flicker.
- **Fix:** the size follows the mouse pixel by pixel. Each step resizes the
  existing window directly and redraws at once in the same frame, which takes
  about 5 ms per step (measured). TuxAmp also tells X to keep the old picture
  pinned to the top-left corner and not paint a background
  (`NorthWestGravity`, no background pixmap), so nothing blank is ever shown.
- **Tested:** in a 20-step corner drag, TuxAmp followed all 20 mouse steps,
  each resize taking 4-6 ms. The window kept its position and aspect ratio.
  Button clicks still land correctly at fractional sizes such as 2.37x.

### Resizing could nudge the window
- **Symptom:** resizing could shift the whole window.
- **Cause:** after each resize step TuxAmp re-applied the window position it
  read back from X, which could be out of date.
- **Fix:** TuxAmp re-applies the position it tracks itself, so the window stays
  put while it is being resized.

### Zooming could push the window off screen
- **Fix:** the zoom stops growing at the bottom and right edges of the screen.

### Visualizer leaving full screen kept the full-screen size
- **Fix:** TuxAmp leaves full screen before restoring its normal window.
