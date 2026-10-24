# LeakLab – Memory Leak Detection with LeakCanary

An Android app (Kotlin) built for an Operating Systems lab project. It extends
[LeakCanary](https://square.github.io/leakcanary/), Square's open-source leak detector,
to **automatically detect objects that stay in memory after their lifecycle ends** and to
**analyse the leak trace**: the chain of references that stops the garbage collector
from freeing them.

## Why

Memory leaks cause high memory use, slow apps and crashes. Memory graphs only show
*that* memory keeps growing. A leak trace shows *which reference* is responsible, so the
developer knows exactly what to fix.

## Features

- **Automatic leak detection.** When an Activity is destroyed, LeakCanary watches it
  with a weak reference. If the Activity is still alive 5 s later (after a GC), it is
  reported as retained.
- **Leak trace analysis.** LeakCanary dumps the heap and finds the shortest path from a
  GC root to the leaked object.
- **Our extension (`LeakReport`).** A LeakCanary `EventListener` that:
  - matches each leak trace to a controlled test scenario
  - checks whether the trace points to the real cause
  - computes detection accuracy, false positives/negatives and trace accuracy
  - shows LeakCanary's live progress (dumping, analysing %) in the app
- **Dashboard UI.** A "Run all" button, summary tiles, a status card for each scenario
  and a leak trace you can expand. Light and dark themes.

## Test scenarios

| Scenario | What the code does | Leak? | Expected culprit |
|---|---|---|---|
| `StaticFieldLeakActivity` | `AppSingleton.lastActivity = this` | Yes | `lastActivity` |
| `ListenerLeakActivity` | Registers an EventBus listener, never unregisters | Yes | `listeners` |
| `CacheLeakActivity` | Stores itself in a global HashMap | Yes | `screens` |
| `ThreadLeakActivity` | A 10-minute thread holds the Activity | Yes | `Thread` |
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
└── LeakLab/                    ← Android Studio project
    └── app/src/main/
        ├── java/com/iiitv/leaklab/
        │   ├── LeakLabApp.kt   ← configures LeakCanary, registers our extension
        │   ├── LeakReport.kt   ← our extension: trace evaluation + live progress
        │   ├── Scenarios.kt    ← the 6 controlled test activities
        │   ├── LeakyObjects.kt ← global objects (AppSingleton, EventBus, ScreenCache)
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

## OS concepts covered

Garbage collection, GC roots, strong vs. weak references, heap dumps, the object
lifecycle, threads as GC roots, and shortest-path (BFS) search over the object graph.
See [steps.md](steps.md) for a detailed explanation.

## Tech stack

Kotlin · Android SDK (minSdk 24, targetSdk 35) · LeakCanary 2.14 · Gradle 8.14
