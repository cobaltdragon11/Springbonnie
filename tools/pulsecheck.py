#!/usr/bin/env python3
"""Hands-off wiring check: M1 twitches once, M2 twice, M3 three times, M4 four times.

Usage: venv/bin/python pulsecheck.py [rounds] [throttle]
Stop the springtrap service first (it holds the HAT).
"""
import sys
import time

from adafruit_motorkit import MotorKit

rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
power = float(sys.argv[2]) if len(sys.argv) > 2 else 0.6

kit = MotorKit(address=0x60)
motors = [kit.motor1, kit.motor2, kit.motor3, kit.motor4]


def pulse(m):
    m.throttle = power
    time.sleep(0.25)
    m.throttle = -power
    time.sleep(0.25)
    m.throttle = None  # coast
    time.sleep(0.5)


try:
    for r in range(1, rounds + 1):
        for n, m in enumerate(motors, 1):
            print('round %d: M%d x%d' % (r, n, n), flush=True)
            for _ in range(n):
                pulse(m)
            time.sleep(2.5)
        time.sleep(3)
finally:
    for m in motors:
        m.throttle = None
    print('done', flush=True)
