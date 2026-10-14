import signal, sys, time
from gpiozero import OutputDevice
limit = float(sys.argv[1]) if len(sys.argv) > 1 else 600
devs = [OutputDevice(p, initial_value=False) for p in (5, 6, 12)]
def stop(*_): sys.exit(0)
signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
print('GPIO 5, 6, 12 driven low', flush=True)
time.sleep(limit)
