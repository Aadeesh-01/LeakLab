# LeakLab – Two-Level Memory Leak Detection on Android

An Operating Systems lab project that detects memory leaks on an Android phone at two
levels:

| Level | Runs on | Sees | Answers |
|---|---|---|---|
| **1 – System-wide** ([`system_monitor/`](system_monitor/)) | PC, via `adb` | Memory of **every process** on the phone (OS accounting: `dumpsys meminfo`) | **Which** app is leaking? |
| **2 – In-app** ([`LeakLab/`](LeakLab/)) | Inside the app | The app's own heap and objects | **Why** is it leaking? (exact reference chain) |

Level 2 extends [LeakCanary](https://square.github.io/leakcanary/), Square's open-source
leak detector, to **automatically detect objects that stay in memory after their
lifecycle ends** and to **analyse the leak trace**: the chain of references that stops
the garbage collector from freeing them.

## Why two levels?

Memory leaks cause high memory use, slow apps and crashes. The OS can measure how much
memory every app uses, but because of **app sandboxing** (each app is a separate process
with its own private memory) it cannot look *inside* an app. So:

1. **Level 1** watches all apps from the outside and flags any app whose memory keeps
   growing.
2. **Level 2**, running inside the app, finds the exact object and the reference chain
   that causes the leak, so the developer knows what to fix.

The two levels are connected through Android's system log: LeakLab writes each finding
(with the tag `LeakLab`), and the PC monitor reads it with `adb logcat`, so one screen
shows both **which app** and **why**.

Measured on a real phone (moto g64 5G, Android 15):
- **Phone-wide:** LeakLab was flagged *LEAK?* at the top (+104 MB) among 204 processes.
- **Level 1 + Level 2:** its live screen count rose 8 → 13, and Level 2 named all 5 causes.

Measured on an Android emulator:
- **Level 1:** its live screen count climbed 5 → 7 → 9 over 3 test runs.
- **Level 2:** all 5 causes were listed (`EventBus.listeners`, `Thread.target`, …).
- **In the app:** Detection 7/7, Trace accuracy 5/5, 0 false results.

## Features

- **Automatic leak detection.** When an Activity is destroyed, LeakCanary watches it
  with a weak reference. If the Activity is still alive 5 s later (after a GC), it is
  reported as retained.
- **Leak trace analysis.** LeakCanary dumps the heap and finds the shortest path from a
  GC root to the leaked object.
- **Watching custom objects.** LeakCanary watches only Android objects by itself. Our
  `MusicPlayer` calls `AppWatcher.objectWatcher.expectWeaklyReachable(this, …)` in
  `release()`, so any object with its own lifecycle can be checked for leaks.
- **Our extension (`LeakReport`).** A LeakCanary `EventListener` that:
  - matches each leak trace to a controlled test scenario
  - checks that the trace contains the **exact culprit reference** (e.g. `EventBus.listeners`)
  - computes detection accuracy, false positives/negatives and trace accuracy
  - measures **detection time** (from `onDestroy()`/`release()` until the trace is ready),
    **heap dump time** and **analysis time**
  - shows LeakCanary's live progress (dumping, analysing %) in the app
  - exports everything as a plain-text report with the **Share report** button
- **Dashboard UI.** A "Run all" button, summary tiles, a status card for each scenario
  and a leak trace you can expand. Light and dark themes.

## Test scenarios

| Scenario | What the code does | Leak? | Expected culprit |
|---|---|---|---|
| `StaticFieldLeakActivity` | `AppSingleton.lastActivity = this` | Yes | `AppSingleton.lastActivity` |
| `ListenerLeakActivity` | Registers an EventBus listener, never unregisters | Yes | `EventBus.listeners` |
| `CacheLeakActivity` | Stores itself in a global HashMap | Yes | `ScreenCache.screens` |
| `ThreadLeakActivity` | A 10-minute thread holds the Activity | Yes | `Thread.target` |
| `PlayerLeakActivity` | **Custom object:** a `MusicPlayer` kept in a global list after `release()` | Yes | `PlayerRegistry.players` |
| `FixedListenerActivity` | Registers **and** unregisters the listener | No | – |
| `CleanActivity` | Nothing special | No | – |

Example leak trace (`ThreadLeakActivity`, from a real run):

```
GC Root: Thread object
  ↓ Thread.target
  ↓ ThreadLeakActivity$$ExternalSyntheticLambda0.f$0
ThreadLeakActivity  ← LEAKING
```

## How to run

**Requirements:** Android Studio, JDK 17, and an Android phone (with USB debugging
enabled) or an emulator running API 24 or higher.

1. Clone the repo and open the **`LeakLab`** folder in Android Studio.
2. Let Gradle sync, then press **Run**.
3. In the app, tap **▶ Run all scenarios** and wait about 15 s.
4. The cards and summary tiles update automatically. Tap **Show leak trace** on a
   leaking scenario to see the reference chain.

LeakCanary also installs a separate **Leaks** app on the device with its full
graphical leak traces.

Command-line build:

```bash
cd LeakLab
./gradlew assembleDebug        # Windows: gradlew assembleDebug
```

## Project structure

```
├── README.md
├── steps.md                    ← step-by-step implementation guide + viva notes
├── OS_abstract.pdf             ← project abstract
├── python_prototype/           ← the same idea in plain Python (for explaining the concept)
├── system_monitor/             ← LEVEL 1: phone-wide memory monitor (Python + adb)
│   ├── phone_monitor.py
│   └── README.md
└── LeakLab/                    ← LEVEL 2: Android Studio project
    └── app/src/main/
        ├── java/com/iiitv/leaklab/
        │   ├── LeakLabApp.kt   ← configures LeakCanary, registers our extension
        │   ├── LeakReport.kt   ← our extension: trace evaluation + live progress
        │   ├── Scenarios.kt    ← the 7 controlled test activities
        │   ├── LeakyObjects.kt ← global objects (AppSingleton, EventBus, ScreenCache)
        │   ├── MusicPlayer.kt  ← custom object watched with AppWatcher.objectWatcher
        │   ├── MainActivity.kt ← dashboard screen
        │   └── Ui.kt           ← status chip helper
        └── res/                ← layouts, colours (light/dark), shapes
```

## Python prototype

`python_prototype/` shows the same two ideas without Android. It uses `weakref` and
`gc` for detection and a BFS over object references for the leak trace:

```bash
cd python_prototype
python main.py
```

## Level 1 – Phone-wide monitor

With the phone connected over USB (USB debugging on):

```bash
cd system_monitor
python phone_monitor.py                            # every process on the phone
python phone_monitor.py --app com.iiitv.leaklab    # one app: which (Level 1) + why (Level 2)
python phone_monitor.py --app com.iiitv.leaklab --demo 45   # also press "Run all" every 45 s
python phone_monitor.py --demo 30                  # all processes + press "Run all" every 30 s
```

It shows each process's memory (PSS), state (Foreground / Visible / Cached …), a trend
line and a verdict (`stable` / `growing` / `LEAK?`). A process is flagged when even its
*lowest* memory keeps climbing, meaning the GC cannot free it. Results are saved as CSV
in `system_monitor/logs/`. See [system_monitor/README.md](system_monitor/README.md).

## OS concepts covered

- **Processes and memory:** PIDs, PSS vs. RSS, process states and OOM priority (Low
  Memory Killer), app sandboxing and process isolation.
- **Garbage collection:** GC roots, strong vs. weak references, heap dumps, the object
  lifecycle, threads as GC roots, and shortest-path (BFS) search over the object graph.

See [steps.md](steps.md) for a detailed explanation.

## Tech stack

Kotlin · Android SDK (minSdk 24, targetSdk 35) · LeakCanary 2.14 · Gradle 8.14 ·
Python 3 (standard library) · adb
