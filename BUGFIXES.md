# TuxAmp bug fixes

Author: Shawn Rapaz. Coded with the help of Claude AI (Anthropic).

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
