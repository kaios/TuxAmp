# TuxAmp bug fixes

Author: Shawn Rapaz. Coded with the help of Claude AI (Anthropic).

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
