# Springtrap animatronic: Raspberry Pi side

Your Mac runs the Springtrap voice box (the brain, his voice and the camera). This Pi moves his
body. They talk over Wi-Fi: the web page tells the Pi exactly how his mouth moves for every
sentence, where people are, and when he laughs.

## What's in here

| File | What it does |
|---|---|
| `install.sh` | One-time setup: turns on I2C and the speaker amp, installs everything, starts on boot |
| `discover.py` | Setup helper: tests each motor output, asks what moved, lets you set the limits |
| `motion.py` | The program that moves him (runs by itself in the background after install) |
| `config.example.toml` | Every setting explained, if you want to edit `config.toml` by hand |

## Hardware

- **Adafruit DC & Stepper Motor HAT** on the Pi's 40 pins. Motor power (5-12V) goes into the green
  "5-12V Motor Power" terminal. It drives 4 DC motors (M1-M4) or 2 steppers (M1+M2, M3+M4).
- **Servos** plug their signal wire into the **xtra PWM** pins 0, 1, 14 or 15. Their red and black
  wires need a separate **5V supply** (a 5V 3A+ adapter or a UBEC), with its ground joined to the Pi's
  ground. Don't power servos from the Pi's own 5V pin: they can make it restart.
- **Pi power**: use the official 27W USB-C supply (5V 5A) for the Pi 5.
- **MAX98357A amp**: BCLK to GPIO18, LRC to GPIO19, DIN to GPIO21, Vin to 5V, GND to GND, speaker
  on + and -. The installer turns it on (it's for playing his voice from inside him later).

## Setup

1. Copy this folder to the Pi and run the installer:
   ```
   scp ~/Downloads/springtrap-pi.zip YOURNAME@springtrap.local:~
   ssh YOURNAME@springtrap.local
   python3 -m zipfile -e springtrap-pi.zip . && bash springtrap-pi/install.sh
   ```
2. Restart when it asks (`sudo reboot`), connect again, then run the setup helper:
   ```
   ~/springtrap/venv/bin/python ~/springtrap/discover.py
   ```
3. On the Mac, open the voice box, go to the **Body** tab, type the Pi's name
   (`springtrap.local`) and press **Connect**.

## Handy commands (on the Pi)

```
journalctl -u springtrap -f                         # watch what it's doing (Ctrl+C to stop watching)
sudo systemctl restart springtrap                   # restart it after editing config.toml
sudo systemctl stop springtrap                      # stop it (it starts again on the next boot)
~/springtrap/venv/bin/python ~/springtrap/motion.py --demo   # move every part once (stop the service first)
nano ~/springtrap/config.toml                       # edit limits and settings by hand
```

## If something's wrong

- **"Couldn't reach the Motor HAT"**: press the HAT fully onto all 40 pins; check `i2cdetect -y 1`
  shows `60`. If not, run `sudo raspi-config nonint do_i2c 0` and reboot.
- **A motor goes the wrong way**: run `discover.py` again, or set `invert = true` for that part.
- **Servos jitter or the Pi restarts when they move**: the servos need their own 5V supply.
- **The jaw lags behind his voice**: raise `jaw_lead` in `config.toml` (try 0.1), then restart.
- **The web page can't connect**: the Mac and Pi must be on the same Wi-Fi. Try the Pi's IP address
  instead of the name (`hostname -I` on the Pi shows it). If Chrome asks to allow access to devices
  on your local network, choose Allow.
