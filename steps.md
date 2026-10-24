# LeakLab – Memory Leak Detection with LeakCanary (Android)

We build a small Android app, **LeakLab**, that contains controlled memory-leak
scenarios, and we **extend LeakCanary** (open-source Android leak detector) with our own
listener that reads its results and evaluates them.

The two features from the abstract:

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
└── LeakLab/                          ← Android Studio project (open this folder)
    ├── build.gradle.kts, settings.gradle.kts
    └── app/
        ├── build.gradle.kts          ← adds the LeakCanary dependency
        └── src/main/
            ├── AndroidManifest.xml
            └── java/com/iiitv/leaklab/
                ├── LeakLabApp.kt     ← configures LeakCanary + plugs in our extension
                ├── LeakyObjects.kt   ← global objects: AppSingleton, EventBus, ScreenCache
                ├── Scenarios.kt      ← the 6 test activities (4 leaky, 2 correct)
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
| `StaticFieldLeakActivity` | `AppSingleton.lastActivity = this` | Yes | `lastActivity` |
| `ListenerLeakActivity` | registers a listener on `EventBus`, never unregisters | Yes | `listeners` |
| `CacheLeakActivity` | `ScreenCache.screens["..."] = this` | Yes | `screens` |
| `ThreadLeakActivity` | starts a 10-minute thread that uses the activity | Yes | `Thread` |
| `FixedListenerActivity` | registers **and unregisters** in `onDestroy()` | No | – |
| `CleanActivity` | nothing special | No | – |

All of them extend `ScenarioActivity`, which just shows a description and a Close
button (Close calls `finish()` → `onDestroy()`).

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
4. Check whether the trace contains the scenario's expected culprit → **trace correct?**

`buildReport()` then calculates:
- **Detection accuracy** – correct leak / no-leak decisions out of scenarios tested
- **False positives** – leak reported where there was none
- **False negatives** – real leak that was missed
- **Trace accuracy** – leak traces that point to the real cause

## Step 6 – Main screen (`MainActivity.kt` + `res/layout/`)

- **Summary tiles** – Detection accuracy, Trace accuracy, False +/−.
- **Live status** – our `LeakReport` also listens to LeakCanary's progress events
  (`DumpingHeap`, `HeapDump`, `HeapAnalysisProgress`) and shows them with a progress bar.
- **Run all scenarios** – opens all 6 scenarios one after another (each closes itself
  after 0.3 s), waits for the 5 s watch delay and then calls `LeakCanary.dumpHeap()` once.
- **Analyse heap now** / **Reset** (Reset clears the global references = "fixes" the leaks).
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
   - all 4 leaking scenarios detected, with correct traces
   - the 2 correct scenarios **not** detected
   - Detection accuracy 6/6, Trace accuracy 4/4
6. You can also open the separate **"Leaks"** app icon that LeakCanary installs to
   see its full graphical leak traces.

Build from the command line (optional): `cd LeakLab` then `gradlew assembleDebug`.

## Step 8 – Viva points

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
