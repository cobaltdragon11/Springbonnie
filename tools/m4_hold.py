import signal, sys, time
from adafruit_motorkit import MotorKit
m = MotorKit(address=0x60).motor4
level = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
limit = float(sys.argv[2]) if len(sys.argv) > 2 else 600
def stop(*_):
    m.throttle = None
    print('M4 off', flush=True)
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
m.throttle = level
print('M4 on at', level, flush=True)
time.sleep(limit)
stop()
