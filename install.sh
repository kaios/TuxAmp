#!/bin/sh
# Installs a "TuxAmp" entry into your desktop's application menu.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
python3 -c "import pygame, numpy" 2>/dev/null || pip3 install --user -r "$DIR/requirements.txt"
mkdir -p "$HOME/.local/share/applications"
# app-menu icon: Tux holding a lightning bolt, drawn by tuxamp/icon.py
for sz in 48 128 256; do mkdir -p "$HOME/.local/share/icons/hicolor/${sz}x${sz}/apps"; done
SDL_VIDEODRIVER=dummy PYGAME_HIDE_SUPPORT_PROMPT=1 python3 -c "
import sys, pygame; sys.path.insert(0, '$DIR'); pygame.display.init()
from tuxamp import icon
for sz in (48, 128, 256):
    pygame.image.save(icon.render(sz), '$HOME/.local/share/icons/hicolor/%dx%d/apps/tuxamp.png' % (sz, sz))
"
sed "s|TUXAMP_DIR|$DIR|" "$DIR/tuxamp.desktop" > "$HOME/.local/share/applications/tuxamp.desktop"
chmod +x "$DIR/tuxamp.py"
echo "TuxAmp installed to your applications menu. Run directly with: $DIR/tuxamp.py"
