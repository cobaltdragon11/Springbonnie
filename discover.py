#!/usr/bin/env python3
"""
Springtrap setup helper.

It tests each output on the Motor HAT one at a time and asks you what moved, then helps you
set how far each part should go. At the end it writes config.toml for motion.py.

Run it on the Pi:
    ~/springtrap/venv/bin/python ~/springtrap/discover.py

Press Ctrl+C at any time to stop. Everything is switched off when it exits.
"""
import argparse
import math
import os
import shutil
import struct
import subprocess
import sys
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import motion  # noqa: E402

MOCK = False
WHAT = [
    ('jaw', 'the jaw'),
    ('pan', 'the head turning left and right'),
    ('tilt', 'the head tilting up and down'),
    ('eyes_x', 'the eyes looking left and right'),
    ('eyes_y', 'the eyes looking up and down'),
    ('gesture', 'something else (an arm, the body...)'),
]
NAMES = {'jaw': 'jaw', 'pan': 'head turn', 'tilt': 'head tilt', 'eyes_x': 'eyes left/right', 'eyes_y': 'eyes up/down', 'gesture': 'extra motor'}
LED_PINS = [12, 13, 16, 17, 22, 23, 24, 25, 26, 27, 5, 6, 4]   # skips I2C (2, 3), the speaker amp (18, 19, 21) and serial (14, 15)


def say(text=''):
    print(text, flush=True)


def ask(prompt, allowed=None, default=None):
    while True:
        try:
            a = input(prompt + ' ').strip().lower()
        except EOFError:
            a = ''
        if not a and default is not None:
            return default
        if allowed is None or a in allowed:
            return a
        say('   Please type one of: ' + ', '.join(allowed))


def yes(prompt, default='y'):
    return ask(prompt + (' [Y/n]' if default == 'y' else ' [y/N]'), ('y', 'n', 'yes', 'no'), default)[0] == 'y'


def what_moved(extra_buzz=False):
    say('   What moved?')
    for i, (_, label) in enumerate(WHAT, 1):
        say('     %d) %s' % (i, label))
    n = len(WHAT)
    say('     %d) nothing moved' % (n + 1))
    if extra_buzz:
        say('     %d) it buzzed or jerked but did not really move' % (n + 2))
    allowed = [str(i) for i in range(1, n + (3 if extra_buzz else 2))]
    a = int(ask('   Type a number:', allowed))
    if a <= n:
        return WHAT[a - 1][0]
    return 'nothing' if a == n + 1 else 'buzz'


def hat(address, servos=False):
    if MOCK:
        return motion.MockBoard()
    return motion.HatBoard(address, servos=servos)


# ---------------------------------------------------------------- steps

def check_hat():
    say('\n== Step 1: looking for the Motor HAT ==')
    if MOCK:
        say('   (pretend mode) found the Motor HAT at 0x60')
        return 0x60
    try:
        i2c = motion.open_i2c()
    except Exception as e:
        say("   Couldn't open I2C: %s" % e)
        say('   Turn I2C on with:  sudo raspi-config nonint do_i2c 0   then reboot and run this again.')
        sys.exit(1)
    while not i2c.try_lock():
        time.sleep(0.01)
    try:
        found = i2c.scan()
    finally:
        i2c.unlock()
    say('   Devices on I2C: ' + (', '.join('0x%02X' % a for a in found) or 'none'))
    if 0x60 in found:
        say('   Found the Motor HAT at 0x60.')
        return 0x60
    cands = [a for a in found if 0x40 <= a <= 0x7F and a != 0x70]
    if cands:
        say('   No board at the usual address 0x60, but there is one at 0x%02X (address jumpers may be soldered).' % cands[0])
        if yes('   Use 0x%02X?' % cands[0]):
            return cands[0]
    say("   The Motor HAT isn't answering. Check:")
    say('    - it is pushed fully down onto all 40 of the Pi\'s pins')
    say('    - I2C is on (sudo raspi-config nonint do_i2c 0, then reboot)')
    say('    - nothing soldered to the HAT is shorting the SDA/SCL pins')
    sys.exit(1)


def find_audio_card():
    try:
        out = subprocess.run(['aplay', '-l'], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if line.startswith('card ') and any(k in line.lower() for k in ('max98357', 'hifiberry', 'voicehat', 'i2s')):
            return line.split(':')[1].split('[')[0].strip()
    return None


def speaker_test():
    say('\n== Step 2: the speaker amp (MAX98357A) ==')
    if MOCK:
        say('   (pretend mode) skipped')
        return None
    card = find_audio_card()
    if not card:
        say("   The Pi doesn't see the speaker amp yet. The installer turns it on; it needs a reboot first.")
        say('   (Skipping. This is only for playing his voice from inside him later.)')
        return None
    if not yes('   Play a short test sound through the speaker?'):
        return card
    path = '/tmp/springtrap-test.wav'
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        frames = b''.join(struct.pack('<h', int(9000 * math.sin(2 * math.pi * (110 + 40 * (i / 22050)) * i / 22050) * (1 - i / 22050)))
                          for i in range(22050))
        w.writeframes(frames)
    subprocess.run(['aplay', '-q', '-D', 'plughw:CARD=%s' % card, path], timeout=10)
    if yes('   Did you hear a low hum from the speaker?'):
        say('   The speaker works.')
    else:
        say('   No sound: check the speaker wires on the amp\'s + and - terminals, and that the amp\'s Vin and GND are connected.')
    return card


def test_dc(address):
    say('\n== Step 3: motor outputs M1 to M4 ==')
    say('   Each output will run briefly one way and then the other. Watch your animatronic.')
    say('   (The motor power supply on the "5-12V Motor Power" terminal must be switched on.)')
    found = {}
    for n in (1, 2, 3, 4):
        a = ask('\n   Test M%d? Press Enter to run it, or type s to skip:' % n, ('', 's'), default='')
        if a == 's':
            continue
        b = hat(address)
        m = b.dc(n)
        try:
            m.throttle = 0.5
            time.sleep(0.3)
            m.throttle = None
            time.sleep(0.4)
            m.throttle = -0.5
            time.sleep(0.3)
        finally:
            m.throttle = None
            b.close()
        found[n] = what_moved(extra_buzz=True)
    return found


def test_steppers(address, dc_found):
    pairs = {1: (1, 2), 2: (3, 4)}
    found = {}
    for s, (a, b2) in pairs.items():
        if dc_found.get(a) not in ('nothing', 'buzz', None) or dc_found.get(b2) not in ('nothing', 'buzz', None):
            continue
        if not any(dc_found.get(x) == 'buzz' for x in (a, b2)) and not yes('\n   Is a stepper motor (4 or 5 wires) connected across M%d and M%d?' % (a, b2), 'n'):
            continue
        say('\n== Stepper test on M%d + M%d ==' % (a, b2))
        ask('   It will step forward a little, then back. Press Enter to start:', default='')
        b = hat(address)
        st = b.stepper(s)
        lib = b.stepper_lib()
        try:
            for d in (lib.FORWARD, lib.BACKWARD):
                for _ in range(30):
                    st.onestep(direction=d, style=lib.DOUBLE)
                    time.sleep(0.01)
                time.sleep(0.3)
        finally:
            st.release()
            b.close()
        found[s] = what_moved()
    return found


def test_servos(address):
    say('\n== Step 4: servo pins 0, 1, 14 and 15 ("xtra PWM") ==')
    say('   Servos on these pins need their red and brown/black wires on a separate 5V supply')
    say('   (with its ground joined to the Pi\'s ground). The pin only carries the signal.')
    if not yes('   Do you have any servos plugged into those pins?', 'n'):
        return {}
    found = {}
    for ch in motion.SERVO_CHANNELS:
        a = ask('\n   Test servo pin %d? Press Enter, or type s to skip:' % ch, ('', 's'), default='')
        if a == 's':
            continue
        b = hat(address, servos=True)
        sv = b.servo(ch)
        try:
            for ang in (90, 80, 100, 90):
                sv.angle = ang
                time.sleep(0.35)
        finally:
            sv.angle = None
            b.close()
        found[ch] = what_moved()
    return found


def test_leds():
    say('\n== Step 5: LED eyes (optional) ==')
    if not yes('   Does he have LED eyes wired to the Pi?', 'n'):
        return None
    if MOCK:
        return 12
    from gpiozero import LED
    for pin in LED_PINS:
        a = ask('   Blink pin GPIO%d? Press Enter, s to skip it, or q to stop looking:' % pin, ('', 's', 'q'), default='')
        if a == 'q':
            return None
        if a == 's':
            continue
        try:
            led = LED(pin)
        except Exception as e:
            say('   GPIO%d is busy (%s), skipping.' % (pin, e))
            continue
        try:
            for _ in range(3):
                led.on()
                time.sleep(0.3)
                led.off()
                time.sleep(0.3)
        finally:
            led.close()
        if yes('   Did the eyes blink?', 'n'):
            return pin
    say('   No eyes found on the usual pins.')
    return None


# ---------------------------------------------------------------- calibration

def nudge_servo(sv, start, label):
    ang = float(start)
    sv.angle = ang
    say('\n   Move it to: %s' % label)
    say('   Type + or - (2 degrees), ++ or -- (10 degrees), or an angle like 95. Type ok when it is right.')
    while True:
        a = ask('   [%3.0f deg]' % ang)
        if a == 'ok':
            return ang
        if a in ('+', '-', '++', '--'):
            ang += {'+': 2, '-': -2, '++': 10, '--': -10}[a]
        else:
            try:
                ang = float(a)
            except ValueError:
                say('   Type +, -, ++, --, a number, or ok.')
                continue
        ang = motion.clamp(ang, 0, 180)
        sv.angle = ang


SERVO_STEPS = {
    'jaw': [('closed', 'the jaw CLOSED'), ('open', 'the jaw OPEN (as wide as it should talk)')],
    'pan': [('center', 'looking straight ahead'), ('min', 'turned to HIS left (your right, as you face him), as far as it should go'),
            ('max', 'turned to HIS right (your left, as you face him), as far as it should go')],
    'tilt': [('center', 'looking straight ahead'), ('max', 'looking UP as far as it should go'), ('min', 'looking DOWN as far as it should go')],
    'eyes_x': [('center', 'eyes looking straight ahead'), ('min', 'eyes looking to HIS left'), ('max', 'eyes looking to HIS right')],
    'eyes_y': [('center', 'eyes looking straight ahead'), ('max', 'eyes looking UP'), ('min', 'eyes looking DOWN')],
    'gesture': [('center', 'its resting position'), ('min', 'one end of its movement'), ('max', 'the other end')],
}


def calibrate_servo(address, ch, role):
    say('\n== Setting up the %s (servo pin %d) ==' % (NAMES[role], ch))
    say('   Keep fingers clear.')
    b = hat(address, servos=True)
    sv = b.servo(ch)
    out = {'role': role, 'kind': 'servo', 'channel': ch}
    try:
        last = 90
        for key, label in SERVO_STEPS[role]:
            last = nudge_servo(sv, last, label)
            out[key] = round(last)
        if role == 'jaw':
            # check the jaw opening won't strain: go back and forth once
            for a in (out['closed'], out['open'], out['closed']):
                sv.angle = a
                time.sleep(0.4)
        else:
            sv.angle = out['center']
            time.sleep(0.4)
    finally:
        sv.angle = None
        b.close()
    return out


def calibrate_stepper(address, s, role):
    say('\n== Setting up the %s (stepper on %s) ==' % (NAMES[role], 'M1+M2' if s == 1 else 'M3+M4'))
    if role == 'jaw':
        say('   Move the jaw to CLOSED by hand (with the motor power off), then switch the power on.')
    else:
        say('   With the motor power OFF, move it by hand to the middle (looking straight ahead), then switch the power on.')
    ask('   Press Enter when ready:', default='')
    b = hat(address)
    st = b.stepper(s)
    lib = b.stepper_lib()
    pos = 0

    def go(n):
        nonlocal pos
        d = lib.FORWARD if n > 0 else lib.BACKWARD
        for _ in range(abs(n)):
            st.onestep(direction=d, style=lib.DOUBLE)
            time.sleep(0.008)
        pos += n

    def find(label):
        say('\n   Move it to: %s' % label)
        say('   Type + or - (5 steps), ++ or -- (25 steps). Type ok when it is right.')
        while True:
            a = ask('   [step %d]' % pos)
            if a == 'ok':
                return pos
            if a in ('+', '-', '++', '--'):
                go({'+': 5, '-': -5, '++': 25, '--': -25}[a])
            else:
                say('   Type +, -, ++, -- or ok.')

    out = {'role': role, 'kind': 'stepper', 'stepper': s, 'style': 'double', 'steps_per_sec': 150}
    try:
        if role == 'jaw':
            o = find('the jaw OPEN')
            out['range_steps'], out['invert'] = abs(o), o < 0
            go(-pos)
        else:
            at = {}
            for key, label in SERVO_STEPS.get(role, SERVO_STEPS['gesture'])[1:]:
                at[key] = find(label)
                go(-pos)
            out['range_steps'] = max(5, min(abs(at['min']), abs(at['max'])))
            out['invert'] = at['min'] > 0      # the program's "minus" side is his left / down
    finally:
        st.release()
        b.close()
    return out


def calibrate_dc(address, n, role):
    say('\n== Setting up the %s (motor M%d) ==' % (NAMES[role], n))
    out = {'role': role, 'kind': 'dc', 'motor': n, 'max_throttle': 0.6, 'max_on': 2.0}
    b = hat(address)
    m = b.dc(n)
    try:
        if role == 'jaw':
            ask('   The motor will pull for a moment. Press Enter:', default='')
            m.throttle = 0.6
            time.sleep(0.35)
            m.throttle = None
            a = ask('   Did the jaw  1) open  2) close  3) not move?', ('1', '2', '3'))
            if a == '2':
                out['invert'] = True
            elif a == '3':
                say('   Try a stronger pull, or check the wiring. Keeping the default for now.')
            sign = -1 if out.get('invert') else 1
            say('\n   Now three pulls, gentle, medium and strong.')
            for i, thr in enumerate((0.4, 0.6, 0.85), 1):
                ask('   Pull %d: press Enter:' % i, default='')
                m.throttle = sign * thr
                time.sleep(0.4)
                m.throttle = None
                time.sleep(0.5)
            a = ask('   Which opened it well without slamming? 1, 2 or 3:', ('1', '2', '3'))
            out['max_throttle'] = (0.4, 0.6, 0.85)[int(a) - 1]
        else:
            say('   This motor will twitch when he laughs or glitches.')
    finally:
        m.throttle = None
        b.close()
    return out


# ---------------------------------------------------------------- config file

def toml_value(v):
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        return str(v)
    return '"%s"' % str(v).replace('"', "'")


def write_config(path, address, parts, audio_card):
    if os.path.exists(path):
        shutil.copy(path, path + '.bak')
    lines = [
        '# Springtrap animatronic setup, written by discover.py on %s.' % time.strftime('%Y-%m-%d %H:%M'),
        '# You can edit this by hand. Run discover.py again to start over (the old file is kept as config.toml.bak).',
        '',
        '[board]',
        'address = 0x%02X   # the Motor HAT\'s I2C address' % address,
        '',
        '[motion]',
        'port = 8765',
        'jaw_lead = 0.06        # seconds the jaw moves ahead of the sound; raise if the jaw lags behind his voice',
        'jaw_delay = 0.0        # raise if the jaw moves too early',
        'look_gain_x = 0.9      # how far he turns toward people (0 to 1)',
        'look_gain_y = 0.6',
        'head_smooth = 0.25     # lower = snappier head',
        'twitches = true        # random animatronic jerks while idle',
        'twitch_every = [7, 18]',
        'idle_without_page = false  # true = keep moving even when the web page is closed',
        'rest_after = 30',
        '',
    ]
    if audio_card:
        lines += ['[audio]', 'card = %s   # the speaker amp, for playing his voice from inside him' % toml_value(audio_card), '']
    for p in parts:
        lines.append('[[part]]')
        lines.append('name = %s' % toml_value(p.get('name') or NAMES.get(p['role'], p['role'])))
        for k in ('role', 'kind', 'channel', 'motor', 'stepper', 'pin', 'closed', 'open', 'min', 'center', 'max',
                  'range_steps', 'steps_per_sec', 'style', 'invert', 'max_throttle', 'max_on'):
            if k in p:
                lines.append('%s = %s' % (k, toml_value(p[k])))
        lines.append('')
    with open(path, 'w') as f:
        f.write('\n'.join(lines))


def service_active(check='is-active'):
    if MOCK or not shutil.which('systemctl'):
        return False
    return subprocess.run(['systemctl', check, '--quiet', 'springtrap'], timeout=5).returncode == 0


def main():
    global MOCK
    ap = argparse.ArgumentParser(description='Springtrap setup helper')
    ap.add_argument('--mock', action='store_true', help='pretend hardware (for trying it out)')
    ap.add_argument('--config', default=os.path.join(HERE, 'config.toml'))
    args = ap.parse_args()
    MOCK = args.mock

    say('Springtrap setup helper')
    say('=======================')
    say('This tests each motor output one at a time and asks what moved.')
    say('Keep fingers clear of the jaw and head. Press Ctrl+C at any time to stop.')

    restart = False
    if service_active():
        say('\nThe motion program is running in the background; pausing it while we test.')
        subprocess.run(['sudo', 'systemctl', 'stop', 'springtrap'])
        restart = True
    try:
        address = check_hat()
        card = speaker_test()
        dc = test_dc(address)
        st = test_steppers(address, dc)
        sv = test_servos(address)
        led = test_leds()

        parts, used = [], set()
        say('\n== What I found ==')
        for n, role in sorted(dc.items()):
            if role in [r for r, _ in WHAT]:
                say('   M%d: %s (DC motor)' % (n, dict(WHAT)[role]))
        for s, role in sorted(st.items()):
            if role in [r for r, _ in WHAT]:
                say('   M%d+M%d: %s (stepper)' % (s * 2 - 1, s * 2, dict(WHAT)[role]))
        for ch, role in sorted(sv.items()):
            if role in [r for r, _ in WHAT]:
                say('   servo pin %d: %s' % (ch, dict(WHAT)[role]))
        if led:
            say('   LED eyes: GPIO%d' % led)

        for ch, role in sorted(sv.items()):
            if role in dict(WHAT) and role not in used:
                parts.append(calibrate_servo(address, ch, role))
                used.add(role)
        for s, role in sorted(st.items()):
            if role in dict(WHAT) and role not in used:
                parts.append(calibrate_stepper(address, s, role))
                used.add(role)
        for n, role in sorted(dc.items()):
            if role in dict(WHAT) and (role not in used or role == 'gesture'):
                parts.append(calibrate_dc(address, n, role))
                used.add(role)
        if led:
            parts.append({'role': 'glow', 'kind': 'led', 'pin': led, 'name': 'eye glow'})

        if not parts:
            say('\nNothing moved, so there is nothing to set up yet. Check the motor power supply and wiring, then run this again.')
            return
        write_config(args.config, address, parts, card)
        say('\nSaved %s' % args.config)
        if yes('\nRun a quick demo of every part now?'):
            cmd = [sys.executable, os.path.join(HERE, 'motion.py'), '--demo', '--config', args.config] + (['--mock'] if MOCK else [])
            subprocess.run(cmd)
        say('\nAll set. The web page will find him at  %s.local  (Body tab).' % os.uname().nodename)
    except KeyboardInterrupt:
        say('\nStopped. Everything is switched off.')
    finally:
        if restart or service_active('is-enabled'):
            say('Starting the motion program again in the background.')
            subprocess.run(['sudo', 'systemctl', 'restart', 'springtrap'])


if __name__ == '__main__':
    main()
