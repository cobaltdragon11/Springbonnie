#!/usr/bin/env bash
# Springtrap animatronic installer for Raspberry Pi 5 (Raspberry Pi OS).
# Run it from the unzipped folder:   bash ~/springtrap-pi/install.sh
# It is safe to run again; it only changes what isn't set up yet.
set -euo pipefail

say() { printf '\n\033[1;33m== %s ==\033[0m\n' "$*"; }
ok()  { printf '   %s\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user (without sudo). It will ask for your password when it needs it."
  exit 1
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIR="$HOME/springtrap"
REBOOT=0

say "Copying the program to $DIR"
mkdir -p "$DIR"
for f in motion.py discover.py README.md config.example.toml; do
  [ -f "$SRC/$f" ] && [ "$SRC" != "$DIR" ] && cp "$SRC/$f" "$DIR/"
done
ok "done"

say "Installing system packages (this can take a few minutes)"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip i2c-tools alsa-utils python3-lgpio python3-gpiozero >/dev/null
ok "done"

say "Turning on I2C (how the Pi talks to the Motor HAT)"
if [ "$(sudo raspi-config nonint get_i2c)" != "0" ]; then
  sudo raspi-config nonint do_i2c 0
  REBOOT=1
  ok "I2C turned on"
else
  ok "I2C was already on"
fi
sudo usermod -aG i2c,gpio,audio "$USER"

say "Turning on the speaker amp (MAX98357A)"
BOOTCFG=/boot/firmware/config.txt
[ -f "$BOOTCFG" ] || BOOTCFG=/boot/config.txt
if grep -q '^dtoverlay=max98357a' "$BOOTCFG"; then
  ok "already on"
else
  printf '\n# Springtrap: MAX98357A I2S speaker amp\ndtoverlay=max98357a\n' | sudo tee -a "$BOOTCFG" >/dev/null
  REBOOT=1
  ok "added to $BOOTCFG"
fi

say "Setting up Python (a private environment, so nothing clashes with the system)"
if [ ! -x "$DIR/venv/bin/python" ]; then
  python3 -m venv --system-site-packages "$DIR/venv"
fi
"$DIR/venv/bin/pip" install -q --upgrade adafruit-blinka adafruit-platformdetect adafruit-circuitpython-motorkit \
  adafruit-circuitpython-motor adafruit-circuitpython-pca9685 adafruit-extended-bus "websockets>=13"
# On the Pi 5 the old RPi.GPIO package gets in the way of Adafruit's libraries.
"$DIR/venv/bin/pip" uninstall -y -q RPi.GPIO >/dev/null 2>&1 || true
ok "done"

say "Giving the Pi a name the web page can find"
NAME="$(hostname)"
if [ "$NAME" = "raspberrypi" ]; then
  sudo raspi-config nonint do_hostname springtrap
  NAME=springtrap
  REBOOT=1
  ok "renamed to springtrap (springtrap.local on your network)"
else
  ok "keeping its name: $NAME (type $NAME.local into the web page)"
fi

say "Making it start by itself when the Pi turns on"
sudo tee /etc/systemd/system/springtrap.service >/dev/null <<EOF
[Unit]
Description=Springtrap animatronic motion
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/python $DIR/motion.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable -q springtrap
[ "$REBOOT" -eq 0 ] && sudo systemctl restart springtrap
ok "done"

say "Finished"
if [ "$REBOOT" -eq 1 ]; then
  echo "   1. Restart the Pi now:        sudo reboot"
  echo "   2. Wait a minute, connect again:  ssh $USER@$NAME.local"
  echo "   3. Run the setup helper:      ~/springtrap/venv/bin/python ~/springtrap/discover.py"
else
  echo "   Run the setup helper:  ~/springtrap/venv/bin/python ~/springtrap/discover.py"
fi
echo
echo "   Useful later:"
echo "     see what it's doing:     journalctl -u springtrap -f"
echo "     move every part once:    sudo systemctl stop springtrap; ~/springtrap/venv/bin/python ~/springtrap/motion.py --demo; sudo systemctl start springtrap"
