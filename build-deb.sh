#!/bin/sh
# Builds a self-contained, compiled TuxAmp .deb (Python + pygame + numpy bundled
# by PyInstaller, so the target machine needs no Python packages).
#
#   ./build-deb.sh            -> tuxamp_0.1.0-alpha_amd64.deb
set -e

VERSION="0.1.0~alpha"          # Debian version ("~" sorts the alpha before the final 0.1.0)
FILE_VERSION="0.1.0-alpha"
ARCH="$(dpkg --print-architecture)"
DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD="$DIR/build"
PKG="$BUILD/pkg"
OUT="$DIR/tuxamp_${FILE_VERSION}_${ARCH}.deb"

cd "$DIR"
rm -rf "$BUILD"
mkdir -p "$BUILD"

echo ">> Compiling with PyInstaller"
PYGAME_HIDE_SUPPORT_PROMPT=1 python3 -m PyInstaller --noconfirm --clean --log-level WARN \
    --name tuxamp --windowed --onedir \
    --distpath "$BUILD/dist" --workpath "$BUILD/work" --specpath "$BUILD" \
    --collect-submodules tuxamp \
    --add-data "$DIR/tuxamp/assets:tuxamp/assets" \
    "$DIR/tuxamp.py"

echo ">> Assembling package tree"
mkdir -p "$PKG/DEBIAN" "$PKG/opt" "$PKG/usr/bin" "$PKG/usr/share/applications" \
         "$PKG/usr/share/doc/tuxamp"
cp -a "$BUILD/dist/tuxamp" "$PKG/opt/tuxamp"
ln -s /opt/tuxamp/tuxamp "$PKG/usr/bin/tuxamp"

# icons: Tux holding a lightning bolt, drawn by tuxamp/icon.py
ICON_SIZES="16 22 24 32 48 64 128 256 512"
for sz in $ICON_SIZES; do
    mkdir -p "$PKG/usr/share/icons/hicolor/${sz}x${sz}/apps"
done
SDL_VIDEODRIVER=dummy PYGAME_HIDE_SUPPORT_PROMPT=1 python3 - "$PKG" "$DIR" $ICON_SIZES <<'EOF'
import sys, pygame
pkg, src, sizes = sys.argv[1], sys.argv[2], [int(a) for a in sys.argv[3:]]
sys.path.insert(0, src)
pygame.display.init()
from tuxamp import icon
for sz in sizes:
    pygame.image.save(icon.render(sz), f"{pkg}/usr/share/icons/hicolor/{sz}x{sz}/apps/tuxamp.png")
EOF

cat > "$PKG/usr/share/applications/tuxamp.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=TuxAmp (alpha)
GenericName=Music Player
Comment=Winamp-style music player for Linux
Exec=tuxamp %F
Icon=tuxamp
Terminal=false
Categories=AudioVideo;Audio;Player;
MimeType=audio/mpeg;audio/x-wav;audio/wav;audio/ogg;audio/flac;audio/x-flac;audio/opus;audio/x-mod;audio/midi;audio/x-mpegurl;audio/x-scpls;
EOF

cp "$DIR/README.md" "$PKG/usr/share/doc/tuxamp/README.md"
gzip -9n -c "$DIR/debian-changelog" > "$PKG/usr/share/doc/tuxamp/changelog.Debian.gz"
cp "$DIR/CREDITS" "$PKG/usr/share/doc/tuxamp/copyright"
cp "$DIR/BUGFIXES.md" "$PKG/usr/share/doc/tuxamp/BUGFIXES.md"

SIZE_KB="$(du -sk "$PKG" | cut -f1)"
cat > "$PKG/DEBIAN/control" <<EOF
Package: tuxamp
Version: $VERSION
Section: sound
Priority: optional
Architecture: $ARCH
Depends: libc6 (>= 2.31)
Installed-Size: $SIZE_KB
Maintainer: Shawn Rapaz <shawnrapaz@gmail.com>
Description: TuxAmp (alpha) - Winamp-style music player for Linux
 A recreation of the classic Winamp 2.x player: main window with LCD time
 display and spectrum analyzer, 10-band equalizer and playlist editor, all
 smoothly zoomable together. Includes TuxDrop, a full-screen,
 music-reactive visualizer in the spirit of Winamp's MilkDrop and AVS
 plugins. Can cast to Chromecast / Google Cast and DLNA devices
 (smart TVs, speakers) on the local network.
 Plays MP3, WAV, OGG, FLAC, Opus, AIFF and tracker module files.
 .
 Version v0.1.0 alpha, released 2026-10-14 by Shawn Rapaz.
 Coded with the help of Claude AI (Anthropic).
 .
 Self-contained: bundles its own Python runtime, pygame and numpy.
EOF

cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
exit 0
EOF
cp "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"

echo ">> Building .deb"
fakeroot dpkg-deb --build -Zxz "$PKG" "$OUT" >/dev/null
echo ">> Done: $OUT"
