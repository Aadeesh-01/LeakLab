# LeakLab – Two-Level Memory Leak Detection (Android)

The project detects memory leaks at two levels:

- **Level 1 – System-wide (Step 8):** a Python tool on the PC reads the memory of
  *every* process on the phone through `adb` and flags apps whose memory keeps growing.
  It answers **which** app leaks.
- **Level 2 – In-app (Steps 1–7):** a small Android app, **LeakLab**, contains controlled
  memory-leak scenarios, and we **extend LeakCanary** (open-source Android leak
  detector) with our own listener that reads its results and evaluates them. It
  answers **why** the app leaks.

The two features from the abstract (Level 2):

1. **Automatic leak detection** – find objects (Activities) that are still in memory
   after their lifecycle has ended (`onDestroy()` was called).
2. **Leak trace analysis** – show the *reference chain* from a GC root to the leaked
   object, i.e. *why* the garbage collector cannot free it.

---

## Key OS / memory concepts

| Concept | Meaning in this project |
|---|---|
| Garbage Collection (GC) | Android (ART) frees an object when nothing reachable refers to it. |
| GC root | Something that is always alive: static fields, running threads, local variables. |
| Strong reference | A normal field / list item / variable. Keeps the object alive. |
| Weak reference | Lets you look at an object **without** keeping it alive. |
| Memory leak | An object we no longer need that is still reachable from a GC root, so it is never freed. |
| Heap dump | A snapshot of every object in memory and the references between them (`.hprof` file). |

### How LeakCanary works (in 4 steps)

1. **Watch** – when an Activity's `onDestroy()` runs, LeakCanary keeps a *weak*
   reference to it.
2. **Check** – after 5 seconds it runs the GC. If the weak reference still points to
   the Activity → the object is *retained* (probable leak).
3. **Dump** – it takes a heap dump.
4. **Analyse** – it searches the heap (shortest path, like BFS) from the GC roots to
   the retained object. That path is the **leak trace**.

---

## Project structure

```
OS_PROject/
├── steps.md                          ← this file
├── OS_abstract.pdf
├── python_prototype/                 ← same idea in plain Python (for explaining the concept)
├── system_monitor/                   ← LEVEL 1: phone-wide memory monitor (Python + adb)
└── LeakLab/                          ← Android Studio project (open this folder)
    ├── build.gradle.kts, settings.gradle.kts
    └── app/
        ├── build.gradle.kts          ← adds the LeakCanary dependency
        └── src/main/
            ├── AndroidManifest.xml
            └── java/com/iiitv/leaklab/
                ├── LeakLabApp.kt     ← configures LeakCanary + plugs in our extension
                ├── LeakyObjects.kt   ← global objects: AppSingleton, EventBus, ScreenCache
                ├── MusicPlayer.kt    ← custom object watched with AppWatcher.objectWatcher
                ├── Scenarios.kt      ← the 7 test activities (5 leaky, 2 correct)
                ├── LeakReport.kt     ← OUR EXTENSION: reads leak traces, evaluates them
                ├── MainActivity.kt   ← dashboard screen
                └── Ui.kt             ← small helper for the coloured status chips
            └── res/                  ← layouts, colours (light + dark), shapes
```

---

## Step 1 – Create the project and add LeakCanary

- Android project, Kotlin, package `com.iiitv.leaklab`, minSdk 24.
- In `app/build.gradle.kts`:
  ```kotlin
  implementation("com.squareup.leakcanary:leakcanary-android:2.14")
  ```
- That's all LeakCanary needs: it installs itself automatically and starts watching
  every Activity (this is **Feature 1**, provided by the library).

## Step 2 – Create global "long-living" objects (`LeakyObjects.kt`)

A Kotlin `object` is stored in a static field, so it is a **GC root** and lives forever:

- `AppSingleton.lastActivity` – a static field
- `EventBus.listeners` – a list of listener callbacks
- `ScreenCache.screens` – a global HashMap

## Step 3 – Create the controlled leak scenarios (`Scenarios.kt`)

| Activity | What it does | Real leak? | Expected culprit in trace |
|---|---|---|---|
| `StaticFieldLeakActivity` | `AppSingleton.lastActivity = this` | Yes | `AppSingleton.lastActivity` |
| `ListenerLeakActivity` | registers a listener on `EventBus`, never unregisters | Yes | `EventBus.listeners` |
| `CacheLeakActivity` | `ScreenCache.screens["..."] = this` | Yes | `ScreenCache.screens` |
| `ThreadLeakActivity` | starts a 10-minute thread that uses the activity | Yes | `Thread.target` |
| `PlayerLeakActivity` | custom `MusicPlayer` stays in `PlayerRegistry` after `release()` | Yes | `PlayerRegistry.players` |
| `FixedListenerActivity` | registers **and unregisters** in `onDestroy()` | No | – |
| `CleanActivity` | nothing special | No | – |

All of them extend `ScenarioActivity`, which just shows a description and a Close
button (Close calls `finish()` → `onDestroy()`).

### Watching a custom object (`MusicPlayer.kt`)

LeakCanary only watches Android objects (Activities, Fragments, …) by itself. To detect
leaks in **our own** objects, `MusicPlayer.release()` tells LeakCanary to watch it:

```kotlin
AppWatcher.objectWatcher.expectWeaklyReachable(this, "MusicPlayer.release() was called")
```

`PlayerLeakActivity` releases the player in `onDestroy()` but leaves it in the global
`PlayerRegistry.players` list, so LeakCanary reports the `MusicPlayer` as leaking.
`Scenario.watchedClass` tells `LeakReport` that this scenario's leaking object is a
`MusicPlayer`, not the activity.

## Step 4 – Configure LeakCanary (`LeakLabApp.kt`)

```kotlin
LeakCanary.config = LeakCanary.config.copy(
    retainedVisibleThreshold = 1,                              // react to every leak
    eventListeners = LeakCanary.config.eventListeners + LeakReport // our extension
)
```
By default LeakCanary waits until 5 objects are retained before dumping the heap;
we lower it to 1 so every scenario is analysed.

## Step 5 – Our extension: leak trace evaluation (`LeakReport.kt`)

`LeakReport` implements LeakCanary's `EventListener`. When the event
`HeapAnalysisSucceeded` arrives:

1. Loop over every leak and every **leak trace**.
2. `trace.leakingObject.className` → which scenario leaked.
3. `trace.referencePath` → the reference chain. We turn it into short text like
   `EventBus.listeners -> ArrayList.elementData -> ... -> ListenerLeakActivity`.
4. Check whether the chain contains the exact culprit reference, written as
   `Class.field` (e.g. `EventBus.listeners`) → **trace correct?** We compare each link
   (`originObject.classSimpleName + "." + referenceName`), not just search the text, so a
   trace cannot be marked correct by accident.
5. **Timing:** detection time = (analysis finished) − (`onDestroy()` time). Heap dump time
   and analysis time come from LeakCanary's `HeapDump` and `HeapAnalysisSucceeded` events.

`buildReport()` then calculates:
- **Detection accuracy** – correct leak / no-leak decisions out of scenarios tested
- **False positives** – leak reported where there was none
- **False negatives** – real leak that was missed
- **Trace accuracy** – leak traces that point to the real cause
- **Avg detect time**, **Heap dump time**, **Analysis time**

## Step 6 – Main screen (`MainActivity.kt` + `res/layout/`)

- **Summary tiles** – Detection accuracy, Trace accuracy, False +/−.
- **Live status** – our `LeakReport` also listens to LeakCanary's progress events
  (`DumpingHeap`, `HeapDump`, `HeapAnalysisProgress`) and shows them with a progress bar.
- **Run all scenarios** – opens all 7 scenarios one after another (each closes itself
  after 0.3 s), waits for the 5 s watch delay and then calls `LeakCanary.dumpHeap()` once.
- **Analyse heap now** / **Reset** (Reset clears the global references = "fixes" the leaks).
- **Share report** – sends the summary, timings and every leak trace as plain text
  (WhatsApp, Gmail, Drive, …), ready to paste into the lab report.
- **Scenario cards** – status chip (NOT TESTED / WAITING / LEAK / NO LEAK), verdict,
  and a "Show leak trace" button with the reference chain.
- The screen updates itself: `MainActivity` registers `LeakReport.onChange` in
  `onResume()` and **unregisters it in `onPause()`** – the same fix as
  `FixedListenerActivity`, otherwise our own UI would leak.
- Light and dark theme (`values/colors.xml`, `values-night/colors.xml`).

## Performance choices

| Change | Where | Effect |
|---|---|---|
| `computeRetainedHeapSize = false` | `LeakLabApp.kt` | Faster heap analysis (skips the "Retaining X kB" calculation) |
| One heap dump for all scenarios | "Run all" button | Each dump freezes the app for a moment, so we do one instead of six |
| No manual "Refresh" | `LeakReport.onChange` | UI updates only when something actually changes |
| Configuration cache, build cache, parallel | `gradle.properties` | Faster Gradle builds |

## Step 7 – Run and evaluate

1. Open the `LeakLab` folder in Android Studio and let Gradle sync.
2. Run on a phone (USB debugging on) or an emulator (Device Manager → create a
   Pixel device with an API 35 image).
3. Tap **Run all scenarios** (or open one scenario and tap **Close**).
4. Wait ~15 s and watch the status line: "Dumping heap…" → "Analysing heap…" → "Analysis done".
   LeakCanary waits at least 60 s between automatic dumps, so if a single scenario
   stays on WAITING, tap **Analyse heap now**.
5. The cards and tiles update by themselves – you should get:
   - all 5 leaking scenarios detected, with correct traces
   - the 2 correct scenarios **not** detected
   - Detection accuracy 7/7, Trace accuracy 5/5, plus the timing tiles
6. You can also open the separate **"Leaks"** app icon that LeakCanary installs to
   see its full graphical leak traces.

Build from the command line (optional): `cd LeakLab` then `gradlew assembleDebug`.

## Step 8 – Level 1: phone-wide monitor (`system_monitor/phone_monitor.py`)

**Why:** Android sandboxing gives every app its own process and private memory, so
LeakCanary (inside one app) cannot see other apps. The OS itself, however, keeps track
of how much memory *every* process uses, and `adb` lets us read that from the PC.

1. **Read the OS's numbers.** `adb shell dumpsys meminfo` lists every process with its
   PID and **PSS** (RAM used, with shared pages split fairly). The "by OOM adjustment"
   part gives each process's state: Foreground, Visible, Cached, …
2. **Parse the text.** We use a regular expression on lines like
   `92,595K: com.iiitv.leaklab (pid 11527 / activities)`.
3. **Keep a history** per process: one sample every 10 s. If the PID changes, the app
   restarted, so its history starts again.
4. **Decide.** Memory goes up and down because of GC, so compare the *lowest* values:
   `growth = min(last 3) − min(first 3)`. **LEAK?** if growth ≥ 5 MB and ≥ 10 %.
5. **Show** a live table (sorted with suspects first) with a trend line, and save every
   sample to `logs/*.csv` for graphs in the report.
6. **App mode** (`--app com.iiitv.leaklab`) reads `dumpsys meminfo <package>`: Java
   heap, native heap and the **number of live Activities**. If the Activity count keeps
   rising after screens are closed, screens are leaking.
7. **Bridge to Level 2.** `LeakReport` writes every finding to Android's system log
   (logcat) with the tag `LeakLab`, e.g.
   `LEAK object=…ListenerLeakActivity cause=EventBus.listeners screen=ListenerLeakActivity`.
   The cause is `leak.shortDescription`, LeakCanary's suspected reference. In app mode the
   monitor reads these lines with `adb logcat -d --pid=<pid> -s LeakLab:I` and prints
   **which app** (Level 1) and **why** (Level 2) on one screen.
8. **Run from the PC.** `MainActivity` (launch mode `singleTop`) runs "Run all" when it
   receives the extra `run_all`:
   `adb shell am start -n com.iiitv.leaklab/.MainActivity --ez run_all true`.
   The monitor's `--demo N` option sends this every N seconds.

Each LeakLab test screen holds a 2 MB "screen image" (like a real bitmap), so a leaked
screen is big enough to show up in the OS numbers.

### Getting one heap dump with all the leaks

After a heap dump, LeakCanary stops watching **every** object it was watching, even
objects still inside their 5 s watch period, and those are never reported. "Run all"
closes 7 screens within a few seconds, so an automatic dump triggered by the first leak
would lose the others. Therefore "Run all":
1. pauses automatic dumps (`LeakLabApp.setAutoHeapDump(false)`),
2. waits until **every** scenario screen has closed (a fixed timer failed on a slow
   first run),
3. waits LeakCanary's 5 s watch delay, then calls `LeakCanary.dumpHeap()` once and turns
   automatic dumps back on.

Result: every run's analysis contains all 5 leaks.

**Run it:**
```bash
cd system_monitor
python phone_monitor.py                                         # phone-wide: which app?
python phone_monitor.py --app com.iiitv.leaklab --demo 45       # one app: which + why
```
Measured on an emulator:
- **Phone-wide:** LeakLab was flagged *LEAK?* (+37.5 MB baseline) among 132 processes.
- **App mode:** the Activity count went 5 → 7 → 9 over 3 runs, and Level 2 listed all 5
  causes. The app showed Detection 7/7, Trace accuracy 5/5, and 0 false results.

## Step 9 – Viva points

- *Why does a static field cause a leak?* Static fields are GC roots, so whatever
  they point to is always reachable.
- *Why does the listener leak?* The lambda uses `title`, so it holds a reference to the
  Activity, and the EventBus (a GC root) holds the lambda.
- *Why does the thread leak?* A running thread is a GC root; its Runnable holds the
  Activity until the thread finishes (10 min).
- *How do you fix a leak?* Break **one** link in the chain – e.g. `FixedListenerActivity`
  unregisters its listener in `onDestroy()`.
- *Why is this better than memory statistics?* Memory graphs only show that memory
  grows; the leak trace shows **which reference** to remove.
- *What did we add to LeakCanary?* An `EventListener` extension that collects every
  leak trace, maps it to its test scenario, and measures detection and trace accuracy.
- *Why weak references?* If the watcher held a strong reference, the watcher itself
  would keep the Activity alive.
- *Why two levels?* Level 1 sees every app but only from the outside (how much memory).
  Level 2 sees inside one app (which object, which reference). The OS's process
  isolation is the reason neither can do both.
- *What is PSS?* Proportional Set Size: the RAM a process uses, where pages shared with
  other processes (e.g. system libraries) are divided among them. It's fairer than RSS,
  which counts shared pages fully for every process.
- *Why compare the lowest values?* Memory normally rises and falls when the GC runs. If
  even the minimum keeps rising, the GC cannot free that memory, which is a leak.
- *What do the states (Foreground, Cached …) mean?* They are the OOM priority. When RAM
  is low, Android's Low Memory Killer kills Cached processes first, so a leaking app is
  killed and restarted more often.
- *How do the two levels talk to each other?* Through Android's system log (logcat): the
  app writes its findings with the tag `LeakLab`, and the PC reads them with
  `adb logcat`. The PC can read the log, but not the app's memory.
- *Why does "Run all" pause automatic heap dumps?* After a dump, LeakCanary forgets all
  watched objects, including ones still in their 5 s watch period. Pausing and taking
  one dump after every screen has closed makes sure no leak is lost.
- *Can the in-app part (LeakCanary) find leaks in other apps (WhatsApp, Chrome…)?* No. Every Android app runs
  in its **own process with its own memory** (app sandboxing / process isolation), so
  one app cannot read another app's heap. That's why LeakCanary is a library added
  *inside* each app. LeakLab shows which app it monitors (name, package, PID) and,
  for every leak, the screen it came from and the leaking object's class.
