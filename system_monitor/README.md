# Level 1 – Phone-wide Memory Monitor

A Python tool that runs on the PC and watches the memory of **every process on an
Android phone**, using the OS's own memory accounting (`dumpsys meminfo`) over `adb`.
It flags apps whose memory keeps growing, which are likely to be leaking.

Level 1 finds **which app** is leaking. Level 2 (LeakCanary inside the app, see
`../LeakLab`) explains **why**.

## Requirements

- Python 3.8+ (standard library only, nothing to install)
- `adb` (comes with Android Studio; the script finds it automatically)
- A phone with **USB debugging** or **Wireless debugging** enabled, or an emulator

## Connecting the phone

The monitor talks to the phone through `adb`, so adb has to see the phone first. Turn on
**Developer options** by tapping *Settings → About phone → Build number* 7 times. Then
connect the phone in one of two ways.

### Option A: USB cable

1. Open **Settings → System → Developer options** and turn on **USB debugging**.
2. Plug in the cable. Pull down the notifications, tap *Charging this device via USB*
   and choose **File transfer**. You can also set **Default USB configuration → File
   transfer** in Developer options.
3. Tap **Allow** on the *Allow USB debugging?* prompt. Tick *Always allow from this
   computer*.

If the phone only charges and does not appear under *This PC* in File Explorer, the
cable is probably **charging-only** and has no data wires. Use the phone's original
cable or another port, or use Option B.

### Option B: Wireless debugging (no cable, Android 11+)

The phone and the laptop must be on the **same Wi-Fi** network.

1. Open **Developer options → Wireless debugging** and turn it on.
2. Tap **Pair device with pairing code**. Keep that popup open: the code expires
   when it closes.
3. On the laptop, enter the IP:port and 6-digit code shown in the popup:

   ```powershell
   $adb = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
   & $adb pair 192.168.1.20:37045 123456     # popup's IP:port + pairing code
   & $adb devices                            # usually connects by itself after pairing
   ```

4. If `adb devices` is still empty, connect using the IP:port shown on the **main**
   Wireless debugging screen. This port is different from the pairing port:

   ```powershell
   & $adb connect 192.168.1.20:41583
   ```

Pairing is needed only once per laptop. Next time, turn on Wireless debugging and run
`adb devices`. If the phone changes Wi-Fi or restarts, connect again (step 4, which
uses a new port each time).

### Check the connection

```powershell
& $adb devices
```

You should see a line ending in `device`. `unauthorized` means you need to tap **Allow**
on the phone. An empty list means the phone is not connected: check the cable or Wi-Fi.

When you have finished testing, turn **USB debugging** and **Wireless debugging** off
again.

## Usage

```bash
cd system_monitor

python phone_monitor.py                            # all processes on the phone
python phone_monitor.py --app com.iiitv.leaklab    # one app in detail + Level 2 causes
python phone_monitor.py --interval 5 --samples 30  # every 5 s, stop after 30 samples
python phone_monitor.py --demo 30                  # all processes + press LeakLab's "Run all" every 30 s
python phone_monitor.py --app com.iiitv.leaklab --demo 45   # one app + "Run all" every 45 s
```

Options: `--interval` (seconds, default 10), `--samples` (0 = until Ctrl+C), `--top`
(rows shown, default 20), `--device` (adb serial if several phones are connected),
`--demo N` (press LeakLab's "Run all scenarios" from the PC every N seconds; works in
both modes).

If the phone disconnects (cable, Wi-Fi, screen lock, "Allow USB debugging?" prompt), the
monitor shows *Phone not reachable – retrying…* and continues by itself once the phone is
back.

On a busy phone (about 200 processes), building the memory report can take more than
Android's default 10 s limit, and the report then gets cut off. The monitor therefore
runs `dumpsys -t 60 meminfo`, which allows up to 60 s, so every process gets its real
state.

## Window (GUI) version

```bash
python monitor_gui.py
```

The same monitor in a window, made with Tkinter, which comes with Python, so there is
nothing to install:

- **Top bar:** choose *All apps* or *One app* (package name), the sampling interval, and
  optionally *Demo: press LeakLab's Run all every N s*. Then press **Start**.
- **All apps:** a table of every process with its state, memory, baseline growth, trend
  and verdict. Suspects are shown in red. **Double-click** an app to open it in detail.
- **One app:** Level 1 numbers with live charts of memory and of screens still in memory,
  Level 2's table of leaks and their causes, and a **RESULT** box with *which* and *why*.

`monitor_gui.py` only draws the window. All the measuring logic is reused from
`phone_monitor.py`. The scanning runs in a background thread, because one phone-wide
scan takes 5–10 s and would otherwise freeze the window. Every run is also saved to
`logs/*.csv`.

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
(sandboxing), but it *can* read the system log over adb.

Two details matter:
- LeakCanary reports each leak only **once**, in the first heap dump after it happens.
  So the monitor collects the leaks from all analyses since the app started (or since
  Reset was pressed).
- The system log is a small **ring buffer**. On a real phone with ~200 processes, old
  lines are overwritten within seconds. So the monitor **remembers** every finding it
  has already read, instead of relying on the lines staying in the log.

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

**Level 1 is only a screening step.** It sees *how much* memory an app uses, not *what*
is in it, so an app that is legitimately filling a cache also looks like it is growing.
Such apps are "suspects", not proven leaks. Only Level 2, running inside the app, can
confirm a leak and name its cause.

## Measured results: real phone (moto g64 5G, Android 15)

**Phone-wide mode** with `--demo 30`, about 2 minutes, **204 processes** (a scan takes
5–10 s on the phone):

```
Process                                       State           PSS now  Baseline Δ  Trend                 Verdict
-------------------------------------------------------------------------------------------------------------------
com.iiitv.leaklab                             Foreground     195.6 MB   +104.1 MB  ▁▂▃▂▃▄▅▄▄█▄▅▆         LEAK?
com.google.android.gms                        Visible        223.0 MB    +33.6 MB  ▂▁▄▄▃▇▇▇▇▇█▇▇         LEAK?
com.motorola.freeform                         Perceptible     57.2 MB     +6.9 MB  ▆▁▁▆▆▇▇▇▇▇▇▇█         LEAK?
com.android.vending                           Cached          95.6 MB     +5.9 MB  █▇▁▂▃▂▂▂▂▂▂▂▂         growing
```

LeakLab is at the top with 3× the growth of anything else. Google Play Services and
Motorola's freeform service also grew past the threshold during the test. They were
reacting to LeakLab being opened again and again, and they have no Level 2 inside, so
they stay *suspects*. This shows why the second level is needed.

**App mode** with `--demo 45`: LeakLab's live screens rose 8 → 13, and Level 2 named
all 5 causes (`AppSingleton.lastActivity`, `EventBus.listeners`,
`PlayerRegistry.players`, `ScreenCache.screens`, `Thread.target`).

## Measured results: Android 17 emulator

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
