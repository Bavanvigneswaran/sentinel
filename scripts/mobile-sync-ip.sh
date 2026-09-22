#!/usr/bin/env bash
# Compares the current network's IP against what's baked into
# frontend/mobile/.env (EXPO_PUBLIC_API_URL), and only rebuilds the APK when
# they actually differ. A hotspot's DHCP lease is not stable across sessions
# — see CLAUDE.md's Phase 10a/11 notes on the LAN IP changing per network —
# so this is meant to be run before every `make serve-lan` session with a
# physical device, not just once.
#
# Usage:
#   scripts/mobile-sync-ip.sh            # check + rebuild the APK if the IP moved
#   scripts/mobile-sync-ip.sh --dev-only # check + update .env only (for
#                                         #   mobile-go/mobile-android, which
#                                         #   read .env at bundle time, not
#                                         #   build time — no rebuild needed)
#   scripts/mobile-sync-ip.sh --check    # report only, change nothing
set -euo pipefail

# Belt-and-suspenders: the four SENTINEL_ANDROID_* signing vars normally load
# from ~/.zshrc, but this loads them directly too, in case this script runs
# in a shell that hasn't picked that up yet (a non-interactive shell, or one
# opened before the .zshrc line was added). Never printed, never logged.
[ -f "$HOME/.sentinel-android-keys.env" ] && source "$HOME/.sentinel-android-keys.env"

cd "$(dirname "$0")/.."

ENV_FILE="frontend/mobile/.env"
MODE="${1:-}"

if [ ! -f "$ENV_FILE" ]; then
    echo "error: $ENV_FILE not found" >&2
    exit 1
fi

# Same interface make serve-lan itself resolves against, so this always
# agrees with what that target would print.
iface=$(route -n get default 2>/dev/null | awk '/interface:/{print $2}')
current_ip=$(ipconfig getifaddr "$iface" 2>/dev/null || true)

if [ -z "$current_ip" ]; then
    echo "error: no network address on interface '$iface' right now." >&2
    echo "Join the hotspot (or wifi) and re-run this script." >&2
    exit 1
fi

baked_url=$(grep -E '^EXPO_PUBLIC_API_URL=' "$ENV_FILE" | tail -1 | cut -d= -f2-)
baked_ip=$(echo "$baked_url" | sed -E 's#^https?://##; s#:[0-9]+$##')

echo "Interface:        $iface"
echo "Current LAN IP:    $current_ip"
echo "Baked-in APK IP:   ${baked_ip:-<none set>}"
echo ""

if [ "$current_ip" = "$baked_ip" ]; then
    echo "✓ Already in sync — no update needed."
    exit 0
fi

echo "✗ Mismatch — the APK on the phone will not reach this backend as configured."

if [ "$MODE" = "--check" ]; then
    echo ""
    echo "Re-run without --check to update $ENV_FILE (and rebuild, unless --dev-only)."
    exit 1
fi

new_url="http://$current_ip:8000"
echo ""
echo "Updating $ENV_FILE -> EXPO_PUBLIC_API_URL=$new_url"
sed -i '' "s#^EXPO_PUBLIC_API_URL=.*#EXPO_PUBLIC_API_URL=$new_url#" "$ENV_FILE"

if [ "$MODE" = "--dev-only" ]; then
    echo ""
    echo "Done. This is enough for 'make mobile-go' / 'make mobile-android' —"
    echo "Metro inlines .env at bundle time. Restart Metro to pick it up."
    exit 0
fi

echo ""
echo "This IP is baked into the release APK at BUILD time, so a rebuild is"
echo "required (mobile-go/mobile-android would need only the .env edit above —"
echo "use --dev-only for those instead)."
echo ""
echo "Rebuilding: make mobile-prebuild && make mobile-apk"
make mobile-prebuild
make mobile-apk

echo ""
echo "Done. Reinstall the new APK on the phone, then start the backend on the"
echo "same address, e.g.:  make serve-lan"
