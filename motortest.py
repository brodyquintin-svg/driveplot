#!/usr/bin/env python3
"""Stepper wiring tester -- a standalone tool, independent of the plotter software.

Run it on the Raspberry Pi:

    python3 motortest.py                 # uses the default pins below
    python3 motortest.py --sim           # pretend, so you can try the menu with no hardware

It drives STEP/DIR/ENABLE directly, one motor at a time, so a failure here is
wiring, power or driver -- never the plotter's gear or screw settings.

Needs only the pigpio daemon:  sudo systemctl start pigpiod
"""
from __future__ import annotations

import argparse
import sys
import time

# Defaults match the plotter's "Pi pins" settings. Change them in the menu (option p).
PINS = {
    "A": {"step": 17, "dir": 27, "en": 22},
    "B": {"step": 23, "dir": 24, "en": 25},
}
ENABLE_ACTIVE_LOW = True      # A4988/DRV8825/TMC2209: LOW enables the driver
MICROSTEPS = 16               # MS1/MS2/MS3 all tied high on an A4988
FULL_STEPS = 200              # 1.8 deg motor
PULSE_US = 4                  # STEP high time; A4988 needs >= 1 us


# --------------------------------------------------------------------- driver
class Sim:
    """Stand-in for pigpio so the menu can be tried without a Pi."""
    connected = True

    def __init__(self):
        self.level = {}

    def set_mode(self, pin, mode):
        pass

    def write(self, pin, lvl):
        self.level[pin] = lvl

    def read(self, pin):
        return self.level.get(pin, 0)

    def stop(self):
        pass


class Driver:
    def __init__(self, simulate=False):
        self.sim = simulate
        if simulate:
            self.pi = Sim()
            print("Simulated run: nothing is actually being driven.\n")
            return
        try:
            import pigpio
        except ImportError:
            sys.exit("pigpio is not installed.  Try:  sudo apt install python3-pigpio\n"
                     "(or run with --sim to look around without hardware)")
        self.pi = pigpio.pi()
        if not self.pi.connected:
            sys.exit("Cannot reach the pigpio daemon.  Start it with:\n"
                     "    sudo systemctl start pigpiod")
        for m in PINS.values():
            for p in m.values():
                self.pi.set_mode(p, pigpio.OUTPUT)
        print("pigpio connected.\n")

    def enable(self, on: bool):
        lvl = (0 if ENABLE_ACTIVE_LOW else 1) if on else (1 if ENABLE_ACTIVE_LOW else 0)
        for m in PINS.values():
            self.pi.write(m["en"], lvl)

    def step_burst(self, motor: str, steps: int, rate_hz: float, forward: bool):
        """Send `steps` pulses to one motor. Blocks; Ctrl+C stops it."""
        p = PINS[motor]
        self.pi.write(p["dir"], 1 if forward else 0)
        time.sleep(0.001)                       # DIR setup time
        if self.sim:
            print(f"    (simulated {steps} pulses on GPIO {p['step']} at {rate_hz:.0f} steps/s)")
            return steps
        period = 1.0 / rate_hz
        high = PULSE_US / 1e6
        low = max(0.0, period - high)
        done = 0
        t = time.perf_counter()
        for _ in range(steps):
            self.pi.write(p["step"], 1)
            time.sleep(high)
            self.pi.write(p["step"], 0)
            done += 1
            t += period
            rest = t - time.perf_counter()
            if rest > 0:
                time.sleep(rest)
            elif rest < -0.25:                  # we fell behind; don't spiral
                t = time.perf_counter()
        _ = low
        return done

    def close(self):
        try:
            self.enable(False)
            for m in PINS.values():
                self.pi.write(m["step"], 0)
        except Exception:
            pass
        self.pi.stop()


# ----------------------------------------------------------------------- menu
def ask(prompt, default=None, cast=str):
    s = input(prompt).strip()
    if not s and default is not None:
        return default
    try:
        return cast(s)
    except ValueError:
        print("  Not a valid value; keeping", default)
        return default


def show_pins():
    print("\n  Current pins (BCM numbering):")
    for name, m in PINS.items():
        print(f"    Motor {name}:  STEP {m['step']:>2}   DIR {m['dir']:>2}   ENABLE {m['en']:>2}")
    print(f"    ENABLE is active-{'low' if ENABLE_ACTIVE_LOW else 'high'}, "
          f"{MICROSTEPS} microsteps -> {FULL_STEPS * MICROSTEPS} steps per revolution\n")


def edit_pins():
    global ENABLE_ACTIVE_LOW, MICROSTEPS
    for name, m in PINS.items():
        print(f"  Motor {name} (press Enter to keep the current value)")
        for key, label in (("step", "STEP"), ("dir", "DIR"), ("en", "ENABLE")):
            m[key] = ask(f"    {label} GPIO [{m[key]}]: ", m[key], int)
    MICROSTEPS = ask(f"  Microsteps [{MICROSTEPS}]: ", MICROSTEPS, int)
    ans = ask(f"  ENABLE active-low? y/n [{'y' if ENABLE_ACTIVE_LOW else 'n'}]: ",
              "y" if ENABLE_ACTIVE_LOW else "n")
    ENABLE_ACTIVE_LOW = ans.lower().startswith("y")
    show_pins()


def turn(drv, motor, revs, rate, forward):
    steps = int(FULL_STEPS * MICROSTEPS * revs)
    print(f"  Motor {motor}: {revs:g} turn(s) {'forward' if forward else 'backward'} "
          f"at {rate:.0f} steps/s. Ctrl+C stops it.")
    drv.enable(True)
    try:
        drv.step_burst(motor, steps, rate, forward)
        print("  Done.\n")
    except KeyboardInterrupt:
        print("\n  Stopped.\n")


MENU = """
================  stepper wiring test  ================
  1  Motor A -- one turn forward
  2  Motor A -- one turn backward
  3  Motor B -- one turn forward
  4  Motor B -- one turn backward
  5  Slow single steps (feel each one)
  6  Hold test -- energise, so you can feel the shaft lock
  7  Release -- de-energise both motors
  8  Blink one pin -- check the Pi's output with a meter or LED
  p  Change pins / microsteps
  s  Change step rate
  h  What the results mean
  q  Quit
"""

HELP = """
  Nothing moves and the shaft turns freely by hand
      No motor power, or the driver is held in reset.
      Measure VMOT at the driver (should be your 12 V).
      RESET and SLEEP must be tied together AND to VDD -- a floating RESET
      keeps an A4988 asleep and it will ignore every pulse.
      Check ENABLE: with option 6 the shaft should lock.

  Shaft locks (option 6) but will not turn
      The driver has power and is enabled, so STEP is not arriving, or the
      current limit is at zero. Turn the small potentiometer clockwise a
      little and retry. Use option 8 to confirm the Pi is toggling STEP.

  Buzzing, vibrating or twitching instead of turning
      The coil pairs are wrong. 1A/1B must be the two ends of ONE coil and
      2A/2B the other. With the motor unplugged, two wires of the same coil
      read about 1.5 ohm; two from different coils read open.

  Turns but is weak, stalls, or skips
      Current limit too low, step rate too high, or the supply is sagging.
      Lower the rate with option s and try again.

  Turns the wrong way
      Normal. Either swap one coil pair's two wires, or turn on the matching
      Invert setting in the plotter's Setup tab.

  Both motors behave the same way
      Suspect something shared: the supply, the ground between driver and Pi,
      or the ENABLE wiring.
"""


def main():
    ap = argparse.ArgumentParser(description="Test stepper wiring on a Raspberry Pi.")
    ap.add_argument("--sim", action="store_true", help="pretend; no GPIO is touched")
    ap.add_argument("--rate", type=float, default=800.0, help="steps per second (default 800)")
    a = ap.parse_args()

    print(__doc__.split("Needs only")[0])
    drv = Driver(simulate=a.sim)
    rate = a.rate
    show_pins()
    print("  Keep fingers clear of the gears. Option 7 switches the motors off.")

    try:
        while True:
            print(MENU)
            c = input(f"  choice (step rate {rate:.0f}/s) > ").strip().lower()
            if c == "q":
                break
            elif c == "1":
                turn(drv, "A", 1, rate, True)
            elif c == "2":
                turn(drv, "A", 1, rate, False)
            elif c == "3":
                turn(drv, "B", 1, rate, True)
            elif c == "4":
                turn(drv, "B", 1, rate, False)
            elif c == "5":
                m = ask("  Which motor, A or B? [A]: ", "A").upper()
                if m not in PINS:
                    print("  No such motor.\n")
                    continue
                n = ask("  How many steps? [20]: ", 20, int)
                print(f"  Motor {m}: {n} steps at 2 per second. You should feel each one.")
                drv.enable(True)
                try:
                    for i in range(n):
                        drv.step_burst(m, 1, 2.0, True)
                        print(f"    step {i + 1}/{n}", end="\r", flush=True)
                        time.sleep(0.5)
                    print("\n  Done.\n")
                except KeyboardInterrupt:
                    print("\n  Stopped.\n")
            elif c == "6":
                drv.enable(True)
                print("  Both motors energised. Try turning a shaft by hand: it should resist.")
                print("  If it spins freely, the driver has no power or ENABLE is not reaching it.\n")
            elif c == "7":
                drv.enable(False)
                print("  Motors released. The shafts should now turn freely.\n")
            elif c == "8":
                p = ask("  Which GPIO number to blink? ", None, int)
                if p is None:
                    continue
                print(f"  Toggling GPIO {p} once a second for 10 s. A meter should swing 0 to 3.3 V.")
                try:
                    import pigpio
                    if not drv.sim:
                        drv.pi.set_mode(p, pigpio.OUTPUT)
                except ImportError:
                    pass
                try:
                    for i in range(10):
                        drv.pi.write(p, i % 2)
                        print(f"    {'HIGH' if i % 2 else 'LOW '}", end="\r", flush=True)
                        time.sleep(1)
                    drv.pi.write(p, 0)
                    print("\n  Done.\n")
                except KeyboardInterrupt:
                    drv.pi.write(p, 0)
                    print("\n  Stopped.\n")
            elif c == "p":
                edit_pins()
            elif c == "s":
                rate = ask(f"  Step rate in steps per second [{rate:.0f}]: ", rate, float)
                if rate < 1:
                    rate = 1.0
                print(f"  Now {rate:.0f} steps/s. One revolution takes "
                      f"{FULL_STEPS * MICROSTEPS / rate:.1f} s.\n")
            elif c == "h":
                print(HELP)
            else:
                print("  Unknown choice.\n")
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        drv.close()
        print("Motors off. Bye.")


if __name__ == "__main__":
    main()
