"""
Level 1 – System-wide memory leak monitor for an Android phone.

Runs on the PC and reads the OS's own memory accounting through adb:
    adb shell dumpsys meminfo            -> memory (PSS) of EVERY process on the phone
    adb shell dumpsys meminfo <package>  -> details of one app (Java heap, Activities, ...)

Idea: memory normally goes up and down because the garbage collector frees objects.
If even the LOWEST value keeps going up, the GC cannot free that memory -> likely leak.
This tells us WHICH app leaks. Level 2 (LeakCanary inside the app) tells us WHY.

Usage:
    python phone_monitor.py                            # watch all apps
    python phone_monitor.py --app com.iiitv.leaklab    # watch one app in detail
    python phone_monitor.py --interval 5 --samples 30  # options
    python phone_monitor.py --demo 30                  # all apps + press LeakLab's Run all every 30 s
    python phone_monitor.py --app com.iiitv.leaklab --demo 30   # one app + Run all every 30 s
"""
import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

# A process is a suspected leak if its baseline grew by at least LEAK_MB and LEAK_PCT.
LEAK_MB = 5
LEAK_PCT = 10
WINDOW = 3                       # compare the first 3 samples with the last 3
MIN_SAMPLES = 2 * WINDOW

SPARK = " ▁▂▃▄▅▆▇█"
PROC_LINE = re.compile(r"^\s*([\d,]+)K: (.+?) \(pid (\d+)")
CATEGORY_LINE = re.compile(r"^\s*[\d,]+K: ([^()]+)$")      # e.g. "92,595K: Foreground"

# Lines written by LeakLab (Level 2) to logcat with the tag "LeakLab", e.g.
#   LEAK object=com.iiitv.leaklab.ListenerLeakActivity cause=EventBus.listeners screen=ListenerLeakActivity
LEVEL2_LINE = re.compile(r"\sLeakLab\s*: (.*)$")
LEVEL2_LEAK = re.compile(r"LEAK object=(?P<object>\S+) cause=(?P<cause>.*) screen=(?P<screen>\S+)$")


# ---------------- talking to the phone ----------------

def find_adb():
    adb = shutil.which("adb")
    if adb:
        return adb
    for root in (os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk")):
        if root:
            path = os.path.join(root, "platform-tools", "adb.exe" if os.name == "nt" else "adb")
            if os.path.exists(path):
                return path
    sys.exit("adb not found. Install Android platform-tools or add adb to PATH.")


class AdbError(Exception):
    """The phone could not be reached (cable unplugged, phone locked, debugging not allowed…)."""


def adb_run(adb, device, *args):
    cmd = [adb] + (["-s", device] if device else []) + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise AdbError(result.stderr.strip() or "is the phone connected?")
    return result.stdout


def show_adb_problem(error):
    clear_screen()
    print(f"Phone not reachable: {error}")
    print("Check the USB cable, unlock the phone and allow USB debugging. Retrying…")
    print("\nPress Ctrl+C to stop.")


def adb_shell(adb, device, *args):
    return adb_run(adb, device, "shell", *args)


def new_level2_state():
    return {"summary": None, "leaks": {}}      # leaks: leaking class -> latest report


def read_level2(adb, device, pid, state):
    """
    Adds the findings that Level 2 (LeakCanary inside the app) wrote to the system log
    (logcat) to `state`.
    - The system log is a small ring buffer: on a busy phone old lines are overwritten
      within seconds, so we REMEMBER everything we have already seen.
    - LeakCanary reports each leak only once (in the first heap dump after it happened),
      so we keep the leaks from all analyses until the app is reset or restarted.
    """
    text = adb_run(adb, device, "logcat", "-d", f"--pid={pid}", "-s", "LeakLab:I")
    messages = [m[1] for m in map(LEVEL2_LINE.search, text.splitlines()) if m]
    if "RESET" in messages:                            # Reset pressed in the app
        messages = messages[len(messages) - messages[::-1].index("RESET"):]
        state.update(new_level2_state())
    for msg in messages:
        if msg.startswith("ANALYSIS"):
            state["summary"] = dict(re.findall(r"(\w+)=(\S+)", msg))
        else:
            m = LEVEL2_LEAK.match(msg)
            if m:
                state["leaks"][m["object"]] = m.groupdict()


# ---------------- parsing dumpsys output ----------------

def parse_all_processes(text):
    """Return {process name: (pid, pss_kb, state)} from `dumpsys meminfo`."""
    section, category = None, None
    pss, states = {}, {}
    for line in text.splitlines():
        if line.startswith("Total "):
            section = line.strip().rstrip(":")
            continue
        m = PROC_LINE.match(line)
        if section == "Total PSS by process" and m:
            pss[m[2]] = (int(m[3]), int(m[1].replace(",", "")))
        elif section == "Total PSS by OOM adjustment":
            if m:
                states[m[2]] = category
            else:
                c = CATEGORY_LINE.match(line)
                if c:
                    category = c[1].strip()          # e.g. Foreground, Visible, Cached
    return {name: (pid, kb, states.get(name, "?")) for name, (pid, kb) in pss.items()}


def parse_app(text):
    """Return details of one app from `dumpsys meminfo <package>`, or None if not running."""
    pid = re.search(r"MEMINFO in pid (\d+)", text)
    if not pid:
        return None

    def num(pattern):
        m = re.search(pattern, text)
        return int(m[1]) if m else 0

    return {
        "pid": int(pid[1]),
        "pss": num(r"TOTAL PSS:\s+(\d+)") or num(r"\n\s+TOTAL\s+(\d+)"),
        "java": num(r"Java Heap:\s+(\d+)"),
        "native": num(r"Native Heap:\s+(\d+)"),
        "activities": num(r"Activities:\s+(\d+)"),
        "views": num(r"Views:\s+(\d+)"),
    }


# ---------------- leak detection ----------------

def baseline_growth(values):
    """How much the LOWEST value grew: min(last samples) - min(first samples)."""
    if len(values) < MIN_SAMPLES:
        return None
    return min(values[-WINDOW:]) - min(values[:WINDOW])


def verdict(values):
    growth = baseline_growth(values)
    if growth is None:
        return "measuring"
    start = min(values[:WINDOW])
    if growth >= LEAK_MB * 1024 and growth >= start * LEAK_PCT / 100:
        return "LEAK?"
    if growth > 1024:
        return "growing"
    return "stable"


def sparkline(values, width=20):
    values = values[-width:]
    low, high = min(values), max(values)
    if high == low:
        return SPARK[1] * len(values)
    return "".join(SPARK[1 + int((v - low) / (high - low) * (len(SPARK) - 2))] for v in values)


def mb(kb):
    return f"{kb / 1024:7.1f} MB"


def signed_mb(kb):
    return f"{kb / 1024:+7.1f} MB"


# ---------------- demo: press LeakLab's "Run all" from the PC ----------------

DEMO_APP = "com.iiitv.leaklab"


def start_run_all(adb, args):
    adb_shell(adb, args.device, "am", "start", "-n", f"{args.app or DEMO_APP}/.MainActivity",
              "--ez", "run_all", "true")


def maybe_run_demo(adb, args, last_trigger):
    """--demo N: press "Run all scenarios" every N seconds. Returns the last trigger time."""
    now = time.time()
    if args.demo and now - last_trigger >= args.demo:
        start_run_all(adb, args)
        return now
    return last_trigger


def demo_line(args):
    return (f"Demo: pressing \"Run all\" in {args.app or DEMO_APP} every {args.demo:g} s\n"
            if args.demo else "")


# ---------------- mode 1: all apps ----------------

def monitor_all(adb, args, writer):
    history = {}      # name -> {"pid": int, "values": [pss_kb, ...]}
    sample = 0
    last_trigger = 0.0
    while args.samples == 0 or sample < args.samples:
        started = time.time()
        try:
            last_trigger = maybe_run_demo(adb, args, last_trigger)
            procs = parse_all_processes(adb_shell(adb, args.device, "dumpsys", "meminfo"))
        except AdbError as e:
            show_adb_problem(e)
            wait(started, args.interval)
            continue
        sample += 1
        now = datetime.now().strftime("%H:%M:%S")

        for name, (pid, kb, state) in procs.items():
            h = history.get(name)
            if h is None or h["pid"] != pid:          # new process, or the app restarted
                h = history[name] = {"pid": pid, "values": []}
            h["values"].append(kb)
            h["state"] = state
            writer.writerow([now, name, pid, state, kb])

        rows = []
        for name in procs:
            h = history[name]
            v = h["values"]
            rows.append((name, h["state"], v, verdict(v), baseline_growth(v) or 0))
        rank = {"LEAK?": 0, "growing": 1, "stable": 2, "measuring": 3}
        rows.sort(key=lambda r: (rank[r[3]], -r[4], -r[2][-1]))

        clear_screen()
        print(f"LEVEL 1 – PHONE-WIDE MEMORY MONITOR   {now}   sample {sample}   "
              f"({len(procs)} processes, scan took {time.time() - started:.1f} s)")
        print(f"Rule: LEAK? = lowest memory grew by ≥ {LEAK_MB} MB and ≥ {LEAK_PCT}% "
              f"(first {WINDOW} vs last {WINDOW} samples)")
        print(demo_line(args))
        print(f"{'Process':<45} {'State':<12} {'PSS now':>10} {'Baseline Δ':>11}  "
              f"{'Trend':<20}  Verdict")
        print("-" * 115)
        for name, state, v, verd, growth in rows[:args.top]:
            print(f"{name[:45]:<45} {state[:12]:<12} {mb(v[-1]):>10} {signed_mb(growth):>11}  "
                  f"{sparkline(v):<20}  {verd}")
        suspects = [r[0] for r in rows if r[3] == "LEAK?"]
        print("\nSuspected leaks: " + (", ".join(suspects) if suspects else "none"))
        if suspects:
            print(f"Find out why:    python phone_monitor.py --app {suspects[0]}")
        print("Press Ctrl+C to stop.")
        wait(started, args.interval)


# ---------------- mode 2: one app in detail ----------------

def level1_verdict(history):
    acts = history["activities"]
    if len(acts) < MIN_SAMPLES:
        return f"measuring… ({len(acts)}/{MIN_SAMPLES} samples)"
    if min(acts[-WINDOW:]) > min(acts[:WINDOW]):
        return "LEAK? – closed screens (Activities) keep piling up in memory"
    if verdict(history["pss"]) == "LEAK?":
        return "LEAK? – the lowest memory value keeps growing"
    return "no leak – memory and screen count are not growing"


def monitor_app(adb, args, writer):
    history = {"pss": [], "java": [], "activities": []}
    level2 = new_level2_state()
    pid = None
    sample = 0
    last_trigger = 0.0
    while args.samples == 0 or sample < args.samples:
        started = time.time()
        try:
            last_trigger = maybe_run_demo(adb, args, last_trigger)
            info = parse_app(adb_shell(adb, args.device, "dumpsys", "meminfo", args.app))
        except AdbError as e:
            show_adb_problem(e)
            wait(started, args.interval)
            continue
        sample += 1
        now = datetime.now().strftime("%H:%M:%S")
        clear_screen()
        print(f"TWO-LEVEL LEAK MONITOR: {args.app}   {now}   sample {sample}")
        print(demo_line(args))

        if info is None:
            print("App is not running. Start it on the phone.")
            wait(started, args.interval)
            continue
        if info["pid"] != pid:                        # first sample, or the app restarted
            pid = info["pid"]
            history = {key: [] for key in history}
            level2 = new_level2_state()
        for key in history:
            history[key].append(info[key])
        try:
            read_level2(adb, args.device, pid, level2)
        except AdbError:
            pass                                      # keep what Level 2 reported so far
        leaks = list(level2["leaks"].values())
        writer.writerow([now, args.app, pid, info["pss"], info["java"], info["native"],
                         info["activities"], info["views"], len(leaks)])

        print(f"LEVEL 1 – what the OS sees from outside (dumpsys meminfo), PID {pid}")
        print(f"  Total PSS   : {mb(info['pss'])}   {sparkline(history['pss'])}")
        print(f"  Java heap   : {mb(info['java'])}   {sparkline(history['java'])}")
        print(f"  Native heap : {mb(info['native'])}")
        print(f"  Activities  : {info['activities']:>7}      {sparkline(history['activities'])}"
              f"   (screens still in memory)")
        print(f"  Views       : {info['views']:>7}")

        print("\nLEVEL 2 – what LeakCanary found inside the app (logcat)")
        summary = level2["summary"]
        if summary is None:
            print("  no heap analysis reported yet")
        else:
            print(f"  {len(leaks)} leak(s) found   (last heap dump {summary.get('dump_ms', '?')} ms,"
                  f" analysis {summary.get('analysis_ms', '?')} ms)")
            for leak in leaks:
                print(f"  {leak['screen']:<26} cause: {leak['cause']}")

        causes = sorted({leak["cause"] for leak in leaks})
        print("\nRESULT")
        print(f"  Which app (Level 1): {level1_verdict(history)}")
        print(f"  Why       (Level 2): {', '.join(causes) if causes else 'no leaks reported'}")
        print("\nPress Ctrl+C to stop.")
        wait(started, args.interval)


# ---------------- helpers ----------------

def clear_screen():
    print("\033[2J\033[H", end="")


def wait(started, interval):
    time.sleep(max(0.0, interval - (time.time() - started)))


def main():
    parser = argparse.ArgumentParser(description="Level 1 phone-wide memory leak monitor")
    parser.add_argument("--app", help="watch one app in detail, e.g. com.iiitv.leaklab")
    parser.add_argument("--interval", type=float, default=10, help="seconds between samples")
    parser.add_argument("--samples", type=int, default=0, help="stop after N samples (0 = forever)")
    parser.add_argument("--top", type=int, default=20, help="rows to show in all-apps mode")
    parser.add_argument("--device", help="adb serial, if more than one phone is connected")
    parser.add_argument("--demo", type=float, default=0, metavar="SECONDS",
                        help="press LeakLab's 'Run all scenarios' from the PC every N seconds "
                             "(works in both modes)")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")      # for the ▁▂▃ trend characters on Windows
    if os.name == "nt":
        os.system("")                                  # enable colour/clear codes in the console

    adb = find_adb()
    os.makedirs("logs", exist_ok=True)
    log_path = os.path.join("logs", datetime.now().strftime("memory_%Y%m%d_%H%M%S.csv"))
    with open(log_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        try:
            if args.app:
                writer.writerow(["time", "app", "pid", "pss_kb", "java_kb", "native_kb",
                                 "activities", "views", "level2_leaks"])
                monitor_app(adb, args, writer)
            else:
                writer.writerow(["time", "process", "pid", "state", "pss_kb"])
                monitor_all(adb, args, writer)
        except KeyboardInterrupt:
            pass
    print(f"\nLog saved to {log_path}")


if __name__ == "__main__":
    main()
