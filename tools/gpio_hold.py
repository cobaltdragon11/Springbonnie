import signal, sys, time
from gpiozero import OutputDevice
pins = [int(p) for p in sys.argv[2:]] or [5, 6, 12]
limit = float(sys.argv[1]) if len(sys.argv) > 1 else 600
devs = [OutputDevice(p, initial_value=False) for p in pins]
def stop(*_):
    for d in devs: d.off(); d.close()
    print('GPIO off', flush=True); sys.exit(0)
signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
for d in devs: d.on()
print('GPIO high:', pins, flush=True)
time.sleep(limit); stop()
