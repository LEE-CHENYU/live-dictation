#!/bin/zsh
# Build "Live Dictation.app" into ~/Applications: compiled launcher (so macOS grants Microphone,
# Input Monitoring and Accessibility to the app, not to python), icon, ad-hoc signature.
# Usage: ./build_app.sh [path/to/python]   (default: the conda env "live_dictation")
set -euo pipefail
cd "${0:A:h}"
PROJECT=$PWD
PYTHON=${1:-$(conda run -n live_dictation python -c 'import sys; print(sys.executable)' 2>/dev/null || true)}
[[ -x $PYTHON ]] || { echo "Python not found; pass its path: ./build_app.sh /path/to/python3" >&2; exit 1; }
APP="$HOME/Applications/Live Dictation.app"
BUILD=$(mktemp -d)
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

sed -e "s|__PROJECT_DIR__|$PROJECT|" -e "s|__PYTHON__|$PYTHON|" macos-app/launcher.swift > "$BUILD/launcher.swift"
# The SDK may default to a newer macOS than the one installed; target 13 so it always launches.
swiftc -O -target "$(uname -m)-apple-macos13.0" "$BUILD/launcher.swift" -o "$APP/Contents/MacOS/live-dictation"

swift macos-app/icon.swift "$BUILD/icon.png"
mkdir -p "$BUILD/AppIcon.iconset"
for s in 16 32 128 256 512; do
  sips -z $s $s "$BUILD/icon.png" --out "$BUILD/AppIcon.iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$BUILD/icon.png" --out "$BUILD/AppIcon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$BUILD/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"
cp macos-app/Info.plist "$APP/Contents/Info.plist"
codesign --force --sign - "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP"
rm -rf "$BUILD"
echo "Built $APP"
echo "Start at login: System Settings > General > Login Items > +, or:"
echo "  osascript -e 'tell application \"System Events\" to make login item at end with properties {path:\"$APP\", hidden:true}'"
