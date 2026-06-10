#!/usr/bin/env bash
# setup-permissions.sh — NOPASSWD sudoers + group memberships for pimesh-gui
#
# The cat #3 shell-outs (display_ops, system_ops, hardware_ops) need
# root-level access to a handful of commands. Rather than make the kiosk
# user a global sudoer, we drop a single sudoers fragment under
# /etc/sudoers.d/ that whitelists exactly what the GUI calls.
#
# Usage: sudo bash scripts/setup-permissions.sh [--uninstall]
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓ $*${NC}"; }
skip() { echo -e "${YELLOW}  ~ $*${NC}"; }
err()  { echo -e "${RED}  ✗ $*${NC}"; }

PIMESH_USER="${PIMESH_USER:-pimesh}"
SUDOERS_PATH="/etc/sudoers.d/pimesh-gui"

if [[ $EUID -ne 0 ]]; then
  err "Esegui come root: sudo bash $0"
  exit 1
fi

if [[ "${1:-}" == "--uninstall" ]]; then
  echo "▶ Rimozione permessi pimesh-gui..."
  rm -f "$SUDOERS_PATH"
  ok "Sudoers fragment rimosso: $SUDOERS_PATH"
  exit 0
fi

if ! id "$PIMESH_USER" &>/dev/null; then
  err "Utente $PIMESH_USER inesistente. Lancia prima setup-display.sh."
  exit 1
fi

echo "▶ Scrittura $SUDOERS_PATH..."

# NOTE: paths below are intentionally absolute so an attacker can't shadow
# any of them via $PATH. visudo would reject the file otherwise too.
cat > "$SUDOERS_PATH" <<EOF
# Allow the pimesh kiosk user to run the small set of system commands the
# GUI calls without prompting for a password. Installed by
# scripts/setup-permissions.sh — remove with --uninstall.
$PIMESH_USER ALL=(root) NOPASSWD: /usr/bin/systemctl reboot
$PIMESH_USER ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff
$PIMESH_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart pimesh-gui
$PIMESH_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart meshtasticd
$PIMESH_USER ALL=(root) NOPASSWD: /usr/bin/tee /sys/class/backlight/*/brightness
$PIMESH_USER ALL=(root) NOPASSWD: /usr/bin/tee /boot/firmware/config.txt
$PIMESH_USER ALL=(root) NOPASSWD: /usr/bin/tee /boot/config.txt
$PIMESH_USER ALL=(root) NOPASSWD: /usr/sbin/hwclock --show --utc
# USB auto-mount (usb_storage.py): source restricted to USB block devices,
# target to the /media prefix with a leading safe character so the kiosk user
# cannot mount over arbitrary paths (in sudoers a bare * crosses whitespace).
$PIMESH_USER ALL=(root) NOPASSWD: /usr/bin/mount -o rw\,noexec\,nodev\,nosuid /dev/sd[a-z] /media/[a-zA-Z0-9_-]*
$PIMESH_USER ALL=(root) NOPASSWD: /usr/bin/mount -o rw\,noexec\,nodev\,nosuid /dev/sd[a-z][0-9] /media/[a-zA-Z0-9_-]*
$PIMESH_USER ALL=(root) NOPASSWD: /usr/bin/umount /media/[a-zA-Z0-9_-]*
EOF
chmod 0440 "$SUDOERS_PATH"

# Validate before leaving — visudo -c reads sudoers + every drop-in.
if ! visudo -c -q -f "$SUDOERS_PATH"; then
  err "visudo rifiuta il file: rimuovo per sicurezza"
  rm -f "$SUDOERS_PATH"
  exit 1
fi
ok "Regole sudoers installate e validate"

# i2c bus and gpio chip access via group membership (no sudo needed).
for grp in i2c gpio dialout; do
  if ! getent group "$grp" >/dev/null; then
    skip "Gruppo $grp non esiste (i2c/gpio non abilitati nel kernel?)"
    continue
  fi
  if groups "$PIMESH_USER" | grep -qw "$grp"; then
    skip "$PIMESH_USER già in $grp"
  else
    usermod -aG "$grp" "$PIMESH_USER"
    ok "$PIMESH_USER aggiunto al gruppo $grp"
  fi
done

echo
echo "========================================"
echo -e "  ${GREEN}Permessi installati!${NC}"
echo
echo "  L'utente $PIMESH_USER può ora:"
echo "    • Riavviare / spegnere il sistema (status bar)"
echo "    • Cambiare la rotazione del display (config.txt)"
echo "    • Scrivere brightness in /sys/class/backlight/*/"
echo "    • Montare / smontare USB (script usb_storage)"
echo "    • Leggere l'orologio RTC (hwclock --show)"
echo "    • Parlare su bus I2C e GPIO (gruppi i2c, gpio, dialout)"
echo
echo "  Per rimuovere: sudo bash $0 --uninstall"
echo "========================================"
