#!/usr/bin/env python3
"""
Springtrap motion server for a Raspberry Pi with the Adafruit DC & Stepper Motor HAT.

The Springtrap voice box web page on your Mac connects to this over Wi-Fi and tells it
when he is talking (with the exact mouth movement for each sentence), where people are,
and when to laugh. This program turns that into movement of the jaw, head and eyes.

Usual use (the installer sets this up to start by itself when the Pi boots):
    ~/springtrap/venv/bin/python ~/springtrap/motion.py

Other ways to run it:
    motion.py --demo     move every part once, then stop (good after calibrating)
    motion.py --mock     pretend hardware, for trying it without the HAT
    motion.py --verbose  print every command from the web page
"""
import argparse
import asyncio
import json
import math
import os
import random
import signal
import sys
import threading
import time

try:
    import tomllib
except ModuleNotFoundError:  # Python older than 3.11
    tomllib = None

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = 1
SERVO_CHANNELS = (0, 1, 14, 15)   # the "xtra PWM" pins on the Motor HAT
ROLES = ('jaw', 'pan', 'tilt', 'eyes_x', 'eyes_y', 'glow', 'gesture', 'torso', 'arm_right', 'arm_left', 'bobble', 'headpower')


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


# ---------------------------------------------------------------- config

DEFAULT_MOTION = {
    'port': 8765,
    'jaw_lead': 0.06,          # seconds the jaw moves ahead of the sound (servos take a moment to get there)
    'jaw_delay': 0.0,          # extra seconds to wait before the jaw starts (if the jaw is early, raise this)
    'look_gain_x': 0.9,        # how far the head turns toward people (0..1 of its range)
    'look_gain_y': 0.6,
    'head_smooth': 0.25,       # seconds; lower = snappier head
    'twitches': True,          # random animatronic jerks while idle
    'twitch_every': [7, 18],   # seconds between twitches (random in this range)
    'idle_without_page': False,  # keep moving when the web page isn't connected
    'rest_after': 30,          # seconds without the page before he goes still
    'bobble_when_speaking': False,   # head bobble runs the whole time he talks (otherwise only on laugh, taunt, arrive, nod)
    'wave_arm': 'arm_right',   # which arm waves when someone leaves (arm_right or arm_left)
    'wave_cooldown': 15,       # seconds before he will wave again
    'wave_turn': 0.8,          # seconds the torso turns toward where the person left (and the same to come back)
    'wave_raise': 1.0,         # seconds the arm takes to rise (and the same to lower)
    'wave_count': 3,           # how many waves
    'wave_fall': 1.5,          # seconds the arm is left to drop under its own weight after the wave
    'wave_swing': 0.3,         # seconds for each down and up swing of the wave
    'torso_invert': False,     # flip if the torso turns away from where the person left
}


def load_config(path):
    cfg = {'board': {}, 'motion': dict(DEFAULT_MOTION), 'parts': []}
    if not os.path.exists(path):
        return cfg
    if tomllib is None:
        raise SystemExit('This needs Python 3.11 or newer to read %s' % path)
    with open(path, 'rb') as f:
        raw = tomllib.load(f)
    cfg['board'].update(raw.get('board', {}))
    cfg['motion'].update(raw.get('motion', {}))
    cfg['parts'] = list(raw.get('part', []))
    return cfg


# ---------------------------------------------------------------- hardware

def open_i2c():
    try:
        import board as _board   # Adafruit Blinka
        return _board.I2C()
    except Exception as first:
        try:
            from adafruit_extended_bus import ExtendedI2C
            return ExtendedI2C(1)
        except Exception:
            raise first


class HatBoard:
    """Adafruit DC & Stepper Motor HAT (a PCA9685 PWM chip plus two TB6612 motor drivers)."""

    def __init__(self, address=0x60, servos=False):
        from adafruit_motorkit import MotorKit
        # One PWM speed for the whole chip: servos need 50 Hz; motors also work at 50 Hz, just a bit noisier.
        self.freq = 50.0 if servos else 1600.0
        self.kit = MotorKit(i2c=open_i2c(), address=address, pwm_frequency=self.freq)
        self.desc = 'Motor HAT at 0x%02X' % address
        self.mock = False

    def servo(self, channel, min_pulse=600, max_pulse=2400, actuation=180):
        from adafruit_motor import servo
        if channel not in SERVO_CHANNELS:
            raise ValueError('servo channel must be one of the xtra PWM pins 0, 1, 14, 15 (got %s)' % channel)
        if self.freq > 100:
            raise ValueError('servos need the board at 50 Hz')
        return servo.Servo(self.kit._pca.channels[channel], actuation_range=actuation, min_pulse=min_pulse, max_pulse=max_pulse)

    def dc(self, n):
        return getattr(self.kit, 'motor%d' % n)

    def stepper(self, n):
        return getattr(self.kit, 'stepper%d' % n)

    def stepper_lib(self):
        from adafruit_motor import stepper
        return stepper

    def close(self):
        try:
            self.kit._pca.deinit()   # resets the chip: every output off
        except Exception:
            pass


class _MockServo:
    def __init__(self, rec, ch):
        self.rec, self.ch, self._a = rec, ch, None

    @property
    def angle(self):
        return self._a

    @angle.setter
    def angle(self, v):
        self._a = v
        self.rec.append(('servo', self.ch, None if v is None else round(v, 1), time.monotonic()))


class _MockDC:
    def __init__(self, rec, n):
        self.rec, self.n, self._t = rec, n, None

    @property
    def throttle(self):
        return self._t

    @throttle.setter
    def throttle(self, v):
        self._t = v
        self.rec.append(('dc', self.n, v, time.monotonic()))


class _MockStepper:
    def __init__(self, rec, n):
        self.rec, self.n, self.pos = rec, n, 0

    def onestep(self, direction=1, style=2):
        self.pos += 1 if direction == 1 else -1
        self.rec.append(('step', self.n, self.pos, time.monotonic()))

    def release(self):
        self.rec.append(('release', self.n, self.pos, time.monotonic()))


class _MockStepperLib:
    FORWARD, BACKWARD, SINGLE, DOUBLE, INTERLEAVE, MICROSTEP = 1, 2, 1, 2, 3, 4


class _MockGpio:
    def __init__(self, rec, pin):
        self.rec, self.pin, self.value = rec, pin, False

    def on(self):
        self.value = True
        self.rec.append(('gpio', self.pin, True, time.monotonic()))

    def off(self):
        self.value = False
        self.rec.append(('gpio', self.pin, False, time.monotonic()))

    def close(self):
        pass


class MockBoard:
    """Pretend hardware: records what would have been sent to the motors."""

    def __init__(self, reason=''):
        self.rec = []
        self.desc = 'pretend hardware' + (' (' + reason + ')' if reason else '')
        self.mock = True

    def servo(self, channel, *a, **k):
        if channel not in SERVO_CHANNELS:
            raise ValueError('servo channel must be one of the xtra PWM pins 0, 1, 14, 15 (got %s)' % channel)
        return _MockServo(self.rec, channel)

    def dc(self, n):
        return _MockDC(self.rec, n)

    def stepper(self, n):
        return _MockStepper(self.rec, n)

    def stepper_lib(self):
        return _MockStepperLib

    def close(self):
        pass


# ---------------------------------------------------------------- parts

class Part:
    kind = '?'

    def __init__(self, cfg):
        self.name = cfg.get('name') or cfg.get('role', 'part')
        self.role = cfg.get('role', 'gesture')
        if self.role not in ROLES:
            raise ValueError('%s: unknown role "%s" (use one of %s)' % (self.name, self.role, ', '.join(ROLES)))
        self.cfg = cfg

    def neutral(self):
        return 0.0

    def drive(self, pos, dt):
        pass

    def rest(self):
        """Go to the neutral position and stop holding (used when the page has gone away)."""
        self.release()

    def release(self):
        pass

    def info(self):
        return {'name': self.name, 'role': self.role, 'kind': self.kind}


class ServoPart(Part):
    """A hobby servo on one of the xtra PWM pins. Positions: jaw 0 (closed) to 1 (open); others -1 to 1."""
    kind = 'servo'

    def __init__(self, cfg, board):
        super().__init__(cfg)
        self.ch = int(cfg['channel'])
        self.actuation = cfg.get('actuation', 180)
        self.dev = board.servo(self.ch, cfg.get('min_pulse', 600), cfg.get('max_pulse', 2400), self.actuation)
        if self.role == 'jaw':
            self.a_closed, self.a_open = float(cfg['closed']), float(cfg['open'])
        else:
            self.a_min, self.a_center, self.a_max = float(cfg['min']), float(cfg['center']), float(cfg['max'])
        default_speed = {'jaw': 700, 'eyes_x': 500, 'eyes_y': 500}.get(self.role, 220)
        self.speed = float(cfg.get('speed', default_speed))   # degrees per second
        self.cur = None
        self.written = None

    def angle_for(self, pos):
        if self.role == 'jaw':
            return self.a_closed + (self.a_open - self.a_closed) * clamp(pos, 0.0, 1.0)
        pos = clamp(pos, -1.0, 1.0)
        if pos >= 0:
            return self.a_center + (self.a_max - self.a_center) * pos
        return self.a_center + (self.a_center - self.a_min) * pos

    def drive(self, pos, dt):
        target = self.angle_for(pos)
        if self.cur is None:
            self.cur = target
        else:
            step = self.speed * dt
            self.cur += clamp(target - self.cur, -step, step)
        if self.written is None or abs(self.cur - self.written) >= 0.4:
            self.dev.angle = clamp(self.cur, 0, self.actuation)
            self.written = self.cur

    def rest(self):
        self.cur = None
        self.drive(self.neutral(), 1.0)
        time.sleep(0.4)
        self.release()

    def release(self):
        try:
            self.dev.angle = None   # stop sending pulses so it doesn't buzz or heat up
        except Exception:
            pass
        self.written = None


class StepperPart(Part):
    """A stepper on M1+M2 (stepper 1) or M3+M4 (stepper 2). Counts steps from where it started (centre)."""
    kind = 'stepper'

    def __init__(self, cfg, board):
        super().__init__(cfg)
        self.dev = board.stepper(int(cfg['stepper']))
        lib = board.stepper_lib()
        self.fwd, self.back = lib.FORWARD, lib.BACKWARD
        self.style = {'single': lib.SINGLE, 'double': lib.DOUBLE, 'interleave': lib.INTERLEAVE}[cfg.get('style', 'double')]
        self.range = int(cfg.get('range_steps', 50))       # steps from centre to the far left/right (or closed to open)
        self.sps = float(cfg.get('steps_per_sec', 150))
        self.invert = bool(cfg.get('invert', False))
        self.hold = bool(cfg.get('hold', True))
        self.at = 0
        self.target = 0
        self.last_move = time.monotonic()
        self.released = True
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True, name='stepper-' + self.name)
        self.thread.start()

    def drive(self, pos, dt):
        if self.role == 'jaw':
            self.target = int(round(clamp(pos, 0.0, 1.0) * self.range))
        else:
            self.target = int(round(clamp(pos, -1.0, 1.0) * self.range))

    def _run(self):
        while self.running:
            t = self.target
            if t != self.at:
                d = 1 if t > self.at else -1
                fwd = (d > 0) != self.invert
                try:
                    self.dev.onestep(direction=self.fwd if fwd else self.back, style=self.style)
                except Exception as e:
                    log('stepper %s error: %s' % (self.name, e))
                    time.sleep(0.5)
                self.at += d
                self.released = False
                self.last_move = time.monotonic()
                time.sleep(1.0 / self.sps)
            else:
                if not self.hold and not self.released and time.monotonic() - self.last_move > 1.5:
                    self._release()
                time.sleep(0.01)

    def _release(self):
        try:
            self.dev.release()
        except Exception:
            pass
        self.released = True

    def rest(self):
        self.target = 0

    def release(self):
        self.target = 0
        end = time.monotonic() + 4
        while self.at != 0 and time.monotonic() < end:
            time.sleep(0.02)
        self.running = False
        self._release()


class DCPart(Part):
    """A DC motor on M1-M4. As a jaw: it pulls the jaw open and a spring (or gravity) closes it.
    As a gesture motor: it gives short pulses when he laughs or twitches."""
    kind = 'dc'

    def __init__(self, cfg, board):
        super().__init__(cfg)
        self.dev = board.dc(int(cfg['motor']))
        self.max = clamp(float(cfg.get('max_throttle', 0.6)), 0.0, 1.0)
        self.invert = bool(cfg.get('invert', False))
        self.brake = bool(cfg.get('brake', False))      # hold still when off (otherwise it coasts, so a spring can close the jaw)
        self.max_on = float(cfg.get('max_on', 2.0))     # never run longer than this in one go
        self.cool = float(cfg.get('cool_down', 1.0))    # then rest for this long
        self.on_since = None
        self.cool_until = 0.0
        self.out = None

    def neutral(self):
        return 0.0

    def drive(self, pos, dt):
        now = time.monotonic()
        if self.role == 'jaw':
            thr = self.max * clamp(pos, 0.0, 1.0) if pos > 0.08 else 0.0
        else:
            thr = self.max * clamp(pos, -1.0, 1.0) if abs(pos) > 0.08 else 0.0
        if thr != 0.0:
            if now < self.cool_until:
                thr = 0.0
            else:
                if self.on_since is None:
                    self.on_since = now
                elif now - self.on_since > self.max_on:
                    self.cool_until = now + self.cool
                    self.on_since = None
                    thr = 0.0
        else:
            self.on_since = None
        if self.invert:
            thr = -thr
        thr = round(thr, 2)
        if thr != self.out:
            self.dev.throttle = thr if thr != 0.0 else (0.0 if self.brake else None)
            self.out = thr

    def release(self):
        try:
            self.dev.throttle = None   # coast
        except Exception:
            pass
        self.out = None


class LedPart(Part):
    """LED eyes on a Pi pin (through a transistor). Brightness follows his mood and voice."""
    kind = 'led'

    def __init__(self, cfg, board):
        super().__init__(cfg)
        self.pin = int(cfg['pin'])
        self.max = clamp(float(cfg.get('brightness', 1.0)), 0.0, 1.0)
        if board.mock:
            self.dev = None
        else:
            from gpiozero import PWMLED
            self.dev = PWMLED(self.pin)
        self.out = None

    def drive(self, pos, dt):
        v = round(clamp(pos, 0.0, 1.0) * self.max, 2)
        if v != self.out and self.dev is not None:
            self.dev.value = v
        self.out = v

    def rest(self):
        self.drive(0.0, 1.0)

    def release(self):
        if self.dev is not None:
            try:
                self.dev.off()
                self.dev.close()
            except Exception:
                pass


class GpioPart(Part):
    """An on/off actuator switched by a transistor on a Pi pin (eyes, jaw, head bobble).
    It is on while its position is above `threshold` (with a little hysteresis so it doesn't chatter).
    The head's supply (a DC part on M4 at -1.0) must be running for it to do anything."""
    kind = 'gpio'

    def __init__(self, cfg, board):
        super().__init__(cfg)
        self.pin = int(cfg['pin'])
        self.on_above = float(cfg.get('threshold', 0.5))
        self.off_below = self.on_above * float(cfg.get('hysteresis', 0.6))
        self.max_on = float(cfg.get('max_on', 0))          # 0 = no limit; otherwise never stay on longer than this
        self.cool = float(cfg.get('cool_down', 0.5))
        if board.mock:
            self.dev = _MockGpio(board.rec, self.pin)
        else:
            from gpiozero import OutputDevice
            self.dev = OutputDevice(self.pin, active_high=not cfg.get('invert', False), initial_value=False)
        self.out = False
        self.on_since = None
        self.cool_until = 0.0

    def drive(self, pos, dt):
        now = time.monotonic()
        want = pos > (self.off_below if self.out else self.on_above)
        if want and now < self.cool_until:
            want = False
        if want and self.max_on > 0:
            if self.on_since is None:
                self.on_since = now
            elif now - self.on_since > self.max_on:
                self.cool_until = now + self.cool
                want = False
        if not want:
            self.on_since = None
        if want != self.out:
            self.dev.on() if want else self.dev.off()
            self.out = want

    def rest(self):
        self.dev.off()
        self.out = False
        self.on_since = None

    def release(self):
        try:
            self.dev.off()
            self.dev.close()
        except Exception:
            pass
        self.out = False


KINDS = {'servo': ServoPart, 'stepper': StepperPart, 'dc': DCPart, 'led': LedPart, 'gpio': GpioPart}


def build_parts(cfg, board):
    parts, errors = [], []
    for pc in cfg['parts']:
        try:
            kind = pc.get('kind')
            if kind not in KINDS:
                raise ValueError('%s: unknown kind "%s"' % (pc.get('name', '?'), kind))
            parts.append(KINDS[kind](pc, board))
        except Exception as e:
            errors.append(str(e))
            log('could not set up a part:', e)
    return parts, errors


# ---------------------------------------------------------------- behaviour

class Engine:
    """Turns what the web page says into a position for every part, 50 times a second."""

    def __init__(self, parts, motion):
        self.parts = parts
        self.m = motion
        self.lock = threading.Lock()
        now = time.monotonic()
        self.state, self.state_t = 'idle', now
        self.trackless = False           # the page is using a voice that can't send mouth tracks
        self.look = None                # (x, y, time)
        self.tracks = []                # [(start, fps, values)]
        self.last_track = 0.0
        self.live = (0.0, 0.0)          # (value, time)
        self.gesture = None             # (name, start, duration)
        self.tests = {}                 # role -> (pos, until)
        self.routine = []               # scripted moves: (role, start, end, pos)
        self.routine_end = 0.0
        self.last_wave = -1e9
        self.seen_x = None              # where the person was when we last saw them
        self.clients = 0
        self.alone_since = now
        self.resting = False
        self.jaw = 0.0
        self.head = [0.0, 0.0]
        self.eyes = [0.0, 0.0]
        self.glow = 0.0
        self.wander = (0.0, 0.0)
        self.next_wander = now + 2
        self.dart = (0.0, 0.0)
        self.next_dart = now + 1
        self.twitch = None              # (dx, dy, until)
        self.next_twitch = now + random.uniform(*self.m['twitch_every'])
        self.verbose = False

    # -- messages from the page
    def handle(self, msg):
        t = msg.get('type')
        now = time.monotonic()
        if self.verbose and t not in ('look', 'jaw'):
            log('page:', json.dumps(msg)[:200])
        with self.lock:
            if t == 'jaw_track':
                vals = [clamp(float(v), 0.0, 1.0) for v in msg.get('values', [])][:3000]
                fps = clamp(float(msg.get('fps', 50)), 5, 200)
                start = now + clamp(float(msg.get('delay', 0)), -0.5, 2.0) + self.m['jaw_delay']
                self.tracks = [tr for tr in self.tracks if tr[0] + len(tr[2]) / tr[1] > now] + [(start, fps, vals)]
                self.last_track = now
            elif t == 'jaw_stop':
                self.tracks = []
                self.live = (0.0, 0.0)
            elif t == 'jaw':
                self.live = (clamp(float(msg.get('v', 0)), 0.0, 1.0), now)
            elif t == 'look':
                x = msg.get('x')
                if x is None:
                    if self.look is not None and now - self.look[2] < 2.5:
                        self._maybe_wave(now, self.look[0])   # someone was there and has gone
                    self.look = None
                else:
                    self.seen_x = float(x)
                    self.look = (clamp(float(x), -1.0, 1.0), clamp(float(msg.get('y', 0) or 0), -1.0, 1.0), now)
            elif t == 'state':
                s = str(msg.get('state', 'idle'))
                self.trackless = bool(msg.get('trackless', False))
                if s != self.state:
                    self.state, self.state_t = s, now
                    if s != 'speaking':
                        self.tracks = [tr for tr in self.tracks if tr[0] + len(tr[2]) / tr[1] > now]
            elif t == 'gesture':
                name = str(msg.get('name', ''))
                dur = {'laugh': 2.2, 'taunt': 2.5, 'arrive': 0.6, 'leave': 1.5, 'nod': 1.4, 'center': 2.0, 'twitch': 0.5}.get(name, 1.0)
                dur = clamp(float(msg.get('duration', dur)), 0.2, 8.0)
                self.gesture = (name, now, dur)
                if name == 'leave':
                    self._maybe_wave(now, self.look[0] if self.look else self.seen_x)
                if name == 'twitch':
                    self._start_twitch(now)
            elif t == 'test':
                role = str(msg.get('role', ''))
                if role == 'all':
                    self.tests = {}
                else:
                    self.tests[role] = (float(msg.get('pos', 0)), now + clamp(float(msg.get('hold', 1.5)), 0.2, 10))
            elif t == 'ping':
                return {'type': 'pong', 't': msg.get('t')}
        return None

    def join(self):
        with self.lock:
            self.clients += 1
            self.resting = False

    def leave(self):
        with self.lock:
            self.clients = max(0, self.clients - 1)
            if self.clients == 0:
                self.alone_since = time.monotonic()
                self.tracks, self.look, self.state = [], None, 'idle'

    def _start_twitch(self, now):
        self.twitch = (random.choice((-1, 1)) * random.uniform(0.12, 0.25), random.uniform(-0.12, 0.12), now + random.uniform(0.08, 0.16))
        self.next_twitch = now + random.uniform(*self.m['twitch_every'])

    def _maybe_wave(self, now, x):
        """Someone left: turn the torso toward where they went and wave one arm, then undo it all.
        Every move has an equal and opposite move, so the body ends where it started."""
        m = self.m
        roles = {p.role for p in self.parts}
        arm = m['wave_arm']
        torso_on, arm_on = 'torso' in roles, arm in roles
        if not (torso_on or arm_on):
            return
        if now < self.routine_end or now - self.last_wave < m['wave_cooldown']:
            return
        turn, rise, n = float(m['wave_turn']), float(m['wave_raise']), int(m['wave_count'])
        sw = float(m['wave_swing'])
        if x is None:
            x = random.choice((-1.0, 1.0))
        s = 1.0 if x < 0 else -1.0          # positive torso throttle turns toward his left (x = -1)
        if m['torso_invert']:
            s = -s
        r, a = [], 0.0
        if torso_on:
            r.append(('torso', 0.0, turn, s))
            a = 0.3
        if arm_on:
            r.append((arm, a, a + rise, -1.0))          # raise (negative throttle = up)
            a += rise + 0.03
            for i in range(n):                          # wave: a little down, a little up
                dip = sw * 0.5 if i == 0 else sw              # the first dip is short: the arm still has momentum from the raise
                r.append((arm, a, a + dip, 1.0))
                a += dip + 0.03
                r.append((arm, a, a + sw * 1.15, -1.0))   # a little longer going up: gravity helped the dip
                a += sw * 1.15 + 0.03
            a += 0.3
            a += float(m['wave_fall'])                  # lower: motor off, the arm drops under its own weight
        if torso_on:
            a = max(a, turn) + 0.2
            r.append(('torso', a, a + turn, -s))        # turn back
            a += turn
        self.routine = [(role, now + t0, now + t1, v) for role, t0, t1, v in r]
        self.routine_end = now + a
        self.last_wave = now
        log('someone left: waving (%s) and turning the torso' % arm)

    # -- positions
    def _jaw_target(self, now):
        v, active = 0.0, False
        lead = self.m['jaw_lead']
        for start, fps, vals in self.tracks:
            i = (now + lead - start) * fps
            if 0 <= i < len(vals) - 1:
                k = int(i)
                f = i - k
                v = max(v, vals[k] * (1 - f) + vals[k + 1] * f)
                active = True
        if not active and now - self.live[1] < 0.25:
            v, active = self.live[0], True
        if not active and self.state == 'speaking' and self.trackless and now - self.state_t < 60:
            # a voice that sends no mouth track (the browser's own voices): a natural-looking chatter
            ph = now - self.state_t
            v = clamp(0.35 + 0.3 * math.sin(ph * 13.0) + 0.25 * math.sin(ph * 5.3 + 1.0), 0.0, 1.0)
        g = self.gesture
        if g and g[0] == 'laugh' and now - g[1] < g[2]:
            ph = now - g[1]
            v = max(v, 0.25 + 0.55 * abs(math.sin(ph * math.pi * 4.2)))
        return v

    def tick(self, now, dt):
        with self.lock:
            m = self.m
            g = self.gesture
            if g and now - g[1] >= g[2]:
                self.gesture = g = None
            gname = g[0] if g else ''
            gph = now - g[1] if g else 0.0

            # jaw: opens quickly, closes a little slower, like a real mouth
            jt = self._jaw_target(now)
            a = 1 - math.exp(-dt / (0.02 if jt > self.jaw else 0.05))
            self.jaw += (jt - self.jaw) * a
            jaw = self.jaw if self.jaw > 0.03 else 0.0

            # head: follow the nearest person, otherwise look around slowly
            if self.look and now - self.look[2] < 2.5:
                tx, ty = self.look[0] * m['look_gain_x'], self.look[1] * m['look_gain_y']
            else:
                if now > self.next_wander:
                    self.wander = (random.uniform(-0.4, 0.4), random.uniform(-0.15, 0.2))
                    self.next_wander = now + random.uniform(3, 8)
                tx, ty = self.wander
            tau = m['head_smooth']
            if self.state == 'thinking':
                ty -= 0.15
                tx += 0.12 * math.sin((now - self.state_t) * 0.9)
            if self.state == 'speaking':
                ty += 0.05 * jaw
            if gname == 'laugh':
                ty += 0.35
                tx += 0.08 * math.sin(gph * math.pi * 2 * 3)
            elif gname == 'taunt':
                ty -= 0.2
                tau *= 2.5
            elif gname == 'arrive':
                tau = 0.07
            elif gname == 'nod':
                ty += 0.25 * math.sin(gph * math.pi * 2 * 1.4)
            elif gname == 'center':
                tx, ty = 0.0, 0.0
            # twitch: a sudden mechanical jerk now and then
            if m['twitches'] and self.state in ('idle', 'listening') and not g and now > self.next_twitch:
                self._start_twitch(now)
            if self.twitch:
                if now < self.twitch[2]:
                    tx += self.twitch[0]
                    ty += self.twitch[1]
                    tau = 0.03
                else:
                    self.twitch = None
            k = 1 - math.exp(-dt / max(tau, 0.01))
            self.head[0] += (clamp(tx, -1, 1) - self.head[0]) * k
            self.head[1] += (clamp(ty, -1, 1) - self.head[1]) * k

            # eyes: quicker than the head, with little darting glances when no one is there
            if self.look and now - self.look[2] < 2.5:
                ex, ey = self.look[0], self.look[1]
            else:
                if now > self.next_dart:
                    self.dart = (random.uniform(-0.7, 0.7), random.uniform(-0.3, 0.3))
                    self.next_dart = now + random.uniform(0.8, 3.0)
                ex, ey = self.dart
            k2 = 1 - math.exp(-dt / 0.04)
            self.eyes[0] += (ex - self.eyes[0]) * k2
            self.eyes[1] += (ey - self.eyes[1]) * k2

            # eye glow: dim when alone, brighter with someone there, flickering with his voice
            present = bool(self.look and now - self.look[2] < 2.5)
            gl = 0.25 + (0.35 if present else 0.0) + 0.4 * jaw
            if self.twitch or gname == 'laugh':
                gl *= random.uniform(0.3, 1.0)
            self.glow += (clamp(gl, 0, 1) - self.glow) * (1 - math.exp(-dt / 0.05))

            pos = {'jaw': jaw, 'pan': self.head[0], 'tilt': self.head[1], 'eyes_x': self.eyes[0], 'eyes_y': self.eyes[1], 'glow': self.glow}
            # gesture motors: short shakes when he laughs or twitches
            gm = 0.0
            if gname == 'laugh':
                gm = 1.0 if int(gph * 6) % 2 == 0 else -1.0
            elif self.twitch:
                gm = 1.0
            pos['gesture'] = gm
            # head: M4 supplies the actuators; the bobble runs on the livelier gestures
            pos['headpower'] = -1.0
            pos['bobble'] = 1.0 if (gname in ('laugh', 'taunt', 'arrive', 'nod') or (m['bobble_when_speaking'] and self.state == 'speaking')) else 0.0

            if self.routine:
                if now >= self.routine_end:
                    self.routine = []
                for role, t0, t1, v in self.routine:
                    if t0 <= now < t1:
                        pos[role] = v
            for role, (p, until) in list(self.tests.items()):
                if now < until:
                    pos[role] = p
                else:
                    del self.tests[role]

            alone = self.clients == 0 and not m['idle_without_page'] and now - self.alone_since > m['rest_after']
        if alone:
            if not self.resting:
                log('No web page connected for a while, so he rests. He wakes when the page connects.')
                self.rest_all()
                self.resting = True
            return
        for p in self.parts:
            try:
                p.drive(pos.get(p.role, 0.0), dt)
            except Exception as e:
                log('%s: %s' % (p.name, e))

    def rest_all(self):
        for p in self.parts:
            try:
                p.rest()
            except Exception:
                pass


def run_loop(engine, stop):
    dt = 1 / 50
    last = time.monotonic()
    while not stop.is_set():
        now = time.monotonic()
        engine.tick(now, min(now - last, 0.1))
        last = now
        time.sleep(max(0.0, dt - (time.monotonic() - now)))


# ---------------------------------------------------------------- server

async def serve(engine, port, status):
    from websockets.asyncio.server import serve as ws_serve

    async def handler(ws):
        engine.join()
        log('Web page connected from %s' % (ws.remote_address[0] if ws.remote_address else '?'))
        try:
            await ws.send(json.dumps(status()))
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                reply = engine.handle(msg)
                if reply:
                    await ws.send(json.dumps(reply))
        except Exception as e:
            if engine.verbose:
                log('connection closed:', e)
        finally:
            engine.leave()
            log('Web page disconnected')

    async with ws_serve(handler, '0.0.0.0', port, max_size=2 ** 20, ping_interval=20, ping_timeout=20):
        log('Listening for the web page on port %d' % port)
        await asyncio.Future()


def demo(engine):
    """Move each part so you can check the calibration."""
    steps = [
        ('jaw opens and closes', lambda: [engine.handle({'type': 'test', 'role': 'jaw', 'pos': 1, 'hold': 0.4}), time.sleep(0.6),
                                          engine.handle({'type': 'test', 'role': 'jaw', 'pos': 0, 'hold': 0.4}), time.sleep(0.6)]),
        ('looks to his left', lambda: [engine.handle({'type': 'look', 'x': -1, 'y': 0}), time.sleep(1.5)]),
        ('looks to his right', lambda: [engine.handle({'type': 'look', 'x': 1, 'y': 0}), time.sleep(1.5)]),
        ('looks up', lambda: [engine.handle({'type': 'look', 'x': 0, 'y': 1}), time.sleep(1.2)]),
        ('looks down', lambda: [engine.handle({'type': 'look', 'x': 0, 'y': -1}), time.sleep(1.2)]),
        ('laughs', lambda: [engine.handle({'type': 'look', 'x': 0, 'y': 0}), engine.handle({'type': 'gesture', 'name': 'laugh'}), time.sleep(2.4)]),
        ('twitches', lambda: [engine.handle({'type': 'gesture', 'name': 'twitch'}), time.sleep(0.8)]),
        ('says a line', lambda: [engine.handle({'type': 'jaw_track', 'fps': 50, 'values': [abs(math.sin(i / 50 * 7)) * (0.4 + 0.6 * abs(math.sin(i / 50 * 1.7))) for i in range(150)]}), time.sleep(3.2)]),
        ('back to the middle', lambda: [engine.handle({'type': 'look', 'x': 0, 'y': 0}), time.sleep(1.2)]),
    ]
    for label, fn in steps:
        log('demo:', label)
        fn()


def main():
    ap = argparse.ArgumentParser(description='Springtrap motion server')
    ap.add_argument('--config', default=os.path.join(HERE, 'config.toml'))
    ap.add_argument('--mock', action='store_true', help='pretend hardware')
    ap.add_argument('--port', type=int)
    ap.add_argument('--verbose', action='store_true')
    ap.add_argument('--demo', action='store_true', help='move every part once and exit')
    args = ap.parse_args()

    cfg = load_config(args.config)
    port = args.port or int(cfg['motion'].get('port', 8765))
    if not cfg['parts']:
        log('No parts set up yet. Run the setup helper first:  ~/springtrap/venv/bin/python ~/springtrap/discover.py')

    errors = []
    uses_servos = any(p.get('kind') == 'servo' for p in cfg['parts'])
    if args.mock:
        board = MockBoard()
    else:
        try:
            board = HatBoard(int(cfg['board'].get('address', 0x60)), servos=uses_servos)
        except Exception as e:
            msg = ("Couldn't reach the Motor HAT (%s). Check it is pressed fully onto the Pi's pins and that I2C is on "
                   "(sudo raspi-config nonint do_i2c 0, then reboot). Running with pretend hardware for now." % e)
            log(msg)
            errors.append(msg)
            board = MockBoard('Motor HAT not found')
    parts, perr = build_parts(cfg, board)
    errors += perr
    engine = Engine(parts, cfg['motion'])
    engine.verbose = args.verbose
    log('%s, parts: %s' % (board.desc, ', '.join('%s (%s %s)' % (p.name, p.kind, p.role) for p in parts) or 'none'))

    def status():
        return {'type': 'status', 'ok': not errors and not board.mock or args.mock, 'version': VERSION, 'board': board.desc,
                'mock': board.mock, 'parts': [p.info() for p in parts], 'errors': errors[:5]}

    stop = threading.Event()
    loop_thread = threading.Thread(target=run_loop, args=(engine, stop), daemon=True)
    loop_thread.start()

    def shutdown():
        stop.set()
        loop_thread.join(timeout=1)
        log('Stopping: centring and releasing the motors')
        engine.rest_all()
        time.sleep(0.3)
        for p in parts:
            p.release()
        board.close()

    if args.demo:
        engine.join()
        try:
            demo(engine)
        finally:
            shutdown()
        return

    async def runner():
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, task.cancel)
        try:
            await serve(engine, port, status)
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(runner())
    except OSError as e:
        log("Couldn't listen on port %d (%s). Is motion.py already running? (sudo systemctl stop springtrap)" % (port, e))
    finally:
        shutdown()


if __name__ == '__main__':
    main()
