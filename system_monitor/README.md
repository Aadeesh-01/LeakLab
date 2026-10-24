# Level 1 – Phone-wide Memory Monitor

A Python tool that runs on the PC and watches the memory of **every process on an
Android phone**, using the OS's own memory accounting (`dumpsys meminfo`) over `adb`.
It flags apps whose memory keeps growing, which are likely to be leaking.

Level 1 finds **which app** is leaking. Level 2 (LeakCanary inside the app, see
`../LeakLab`) explains **why**.

## Requirements

- Python 3.8+ (standard library only, nothing to install)
- `adb` (comes with Android Studio; the script finds it automatically)
- A phone with **USB debugging** enabled, or an emulator

## Usage

```bash
cd system_monitor

python phone_monitor.py                            # all processes on the phone
python phone_monitor.py --app com.iiitv.leaklab    # one app in detail + Level 2 causes
python phone_monitor.py --interval 5 --samples 30  # every 5 s, stop after 30 samples
python phone_monitor.py --app com.iiitv.leaklab --demo 45   # also press "Run all" every 45 s
```

Options: `--interval` (seconds, default 10), `--samples` (0 = until Ctrl+C), `--top`
(rows shown, default 20), `--device` (adb serial if several phones are connected),
`--demo N` (LeakLab only: press "Run all scenarios" from the PC every N seconds).

To press "Run all" once from the PC:

```bash
adb shell am start -n com.iiitv.leaklab/.MainActivity --ez run_all true
```

## How Level 1 and Level 2 are connected

LeakLab (Level 2) writes every finding to Android's **system log (logcat)** with the tag
`LeakLab`:

```
I LeakLab : ANALYSIS leaks=5 dump_ms=2543 analysis_ms=3554
I LeakLab : LEAK object=com.iiitv.leaklab.ListenerLeakActivity cause=EventBus.listeners screen=ListenerLeakActivity
```

The monitor reads these lines with `adb logcat -d --pid=<pid> -s LeakLab:I` and shows
them next to the OS's memory numbers. Level 1 can't look inside the app's memory
(sandboxing), but it *can* read the system log over adb. LeakCanary reports each leak
only once, in the first heap dump after it happens, so the monitor collects the leaks
from all analyses since the app started (or since Reset was pressed).

Every sample is also written to `logs/memory_<date>_<time>.csv` for the report.

## How it decides "LEAK?"

Memory goes up and down normally, because the garbage collector frees objects from time
to time. So we look at the **lowest** value (the baseline), not the current value:

```
baseline growth = min(last 3 samples) − min(first 3 samples)
LEAK?   if baseline growth ≥ 5 MB  and  ≥ 10 % of the starting memory
```

If even the lowest point keeps climbing, the GC cannot free that memory.

In app mode it also watches the **Activities** count (screens alive in memory). If it
keeps rising after screens are closed, destroyed screens are being leaked.

## Measured results (Android 17 emulator)

**Phone-wide mode**, pressing Run all 4 times. LeakLab is flagged among 132 processes:

```
LEVEL 1 – PHONE-WIDE MEMORY MONITOR   15:31:23   sample 14   (132 processes, scan took 1.1 s)

Process                                       State           PSS now  Baseline Δ  Trend                 Verdict
-------------------------------------------------------------------------------------------------------------------
com.iiitv.leaklab                             Foreground     115.8 MB    +37.5 MB  ▁▁▁▂▅▅▃▆▇▆▇▄█▇        LEAK?
com.google.android.gms                        Visible        203.9 MB     +4.5 MB  ▁▁▁▁▂▃▆▂▂▄▆▆▇█        growing
com.google.android.inputmethod.latin          Perceptible     80.9 MB     +3.8 MB  ▁▁▅▅▅▆▇▇▇▇█▅▅▅        growing

Suspected leaks: com.iiitv.leaklab
Find out why:    python phone_monitor.py --app com.iiitv.leaklab
```

**App mode** (`--app com.iiitv.leaklab --demo 45`), 3 runs. Both levels on one screen:

```
TWO-LEVEL LEAK MONITOR: com.iiitv.leaklab   15:29:14   sample 34

LEVEL 1 – what the OS sees from outside (dumpsys meminfo), PID 5824
  Total PSS   :    72.4 MB   ▁▆█▆▇▁▁▁▁▁▁▄▂▅▇▇▁▁▁▁
  Java heap   :    26.2 MB   ▁▁█▆▇▁▁▁▁▁▁▃▁▁▇▇▁▁▁▁
  Native heap :    22.8 MB
  Activities  :       9      ▁▁▁▁▁▁▁▁▁▁▂█▃▃▃▃▃▃▃▃   (screens still in memory)

LEVEL 2 – what LeakCanary found inside the app (logcat)
  5 leak(s) found   (last heap dump 2543 ms, analysis 3554 ms)
  ThreadLeakActivity         cause: Thread.target
  StaticFieldLeakActivity    cause: AppSingleton.lastActivity
  PlayerLeakActivity         cause: PlayerRegistry.players
  CacheLeakActivity          cause: ScreenCache.screens
  ListenerLeakActivity       cause: EventBus.listeners

RESULT
  Which app (Level 1): LEAK? – closed screens (Activities) keep piling up in memory
  Why       (Level 2): AppSingleton.lastActivity, EventBus.listeners, PlayerRegistry.players, ScreenCache.screens, Thread.target
```

The Activity count went 5 → 7 → 9: each run leaves two more screens in memory (the
listener leak and the thread leak; the static-field and cache leaks replace their
previous screen). Every run's heap analysis found all 5 leaks.

## OS concepts

| Term | Meaning |
|---|---|
| **PID** | Every app runs as its own process with its own process ID |
| **PSS** (Proportional Set Size) | RAM used by a process, with shared pages divided among the processes sharing them. This is what we track. |
| **RSS** (Resident Set Size) | All RAM pages a process has mapped, counting shared pages fully |
| **OOM adjustment / State** | How important Android thinks the process is (Foreground, Visible, Cached…). Under memory pressure, the Low Memory Killer kills Cached processes first. |
| **Sandboxing** | Apps cannot read each other's memory, so Level 1 can see *how much* memory an app uses but not *what* is inside it. That is why Level 2 runs inside the app. |
