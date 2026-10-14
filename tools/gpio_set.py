import signal, sys, time
from gpiozero import OutputDevice
# usage: gpio_set.py <secs> 5=1 6=0 12=0
limit = float(sys.argv[1])
devs = [(OutputDevice(int(a.split('=')[0]), initial_value=False), a.split('=')[1] == '1') for a in sys.argv[2:]]
def stop(*_): sys.exit(0)
signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
for d, on in devs:
    d.on() if on else d.off()
print('set:', ' '.join(sys.argv[2:]), flush=True)
time.sleep(limit)
