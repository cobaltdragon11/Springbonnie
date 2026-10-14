# Springtrap hardware map (bench-tested 2026-10-13)

Pi 5 `springbonnie` + Adafruit DC/Stepper Motor HAT (I2C 0x60).

| Output | Part | How to run it |
|---|---|---|
| M1 | Right arm | Negative throttle = up/forward (inverted) |
| M2 | Left arm | Negative throttle = up/forward (inverted) |
| M3 | Torso rotation | Positive = left, negative = right |
| M4 | Head power supply | Hold at throttle -1.0 to power the head |
| GPIO 5 | Eyes | High = on (needs M4 at -1.0) |
| GPIO 6 | Jaw open | High = open (needs M4 at -1.0) |
| GPIO 12 | Head bobble | High = bobbling (needs M4 at -1.0) |

Head parts are on/off only, switched by one transistor each. Don't hold the jaw open for long.
The MAX98357A amp is not used; speech plays from the Mac over Bluetooth.
