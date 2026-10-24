package com.iiitv.leaklab

import android.app.Application
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.os.SystemClock
import android.util.Log
import leakcanary.EventListener
import leakcanary.EventListener.Event
import leakcanary.LeakCanary
import shark.HeapAnalysisSuccess
import java.util.concurrent.ConcurrentHashMap

/**
 * Our extension to LeakCanary.
 *
 * LeakCanary calls onEvent() for everything it does (dumping the heap, analysis progress,
 * results). When an analysis finishes, we read every leak trace (the reference chain from
 * a GC root to the leaked object), match it to one of our scenarios and check whether the
 * chain contains the real culprit.
 */
object LeakReport : EventListener {

    enum class Status(val label: String, val colorRes: Int) {
        NOT_TESTED("NOT TESTED", R.color.status_neutral),
        WAITING("WAITING", R.color.status_waiting),
        LEAK("LEAK", R.color.status_leak),
        NO_LEAK("NO LEAK", R.color.status_ok),
    }

    class Result(
        val traceCorrect: Boolean,
        val referenceChain: String,
        val detectedAfterMs: Long,   // time from onDestroy() until the leak trace was ready
        val leakingObject: String,   // full class name, e.g. com.iiitv.leaklab.MusicPlayer
        val sourceScreen: String,    // the screen (Activity) where the leak was created
    )

    /**
     * The app LeakCanary is running in. LeakCanary lives INSIDE the app's own process, so it
     * can only see that app's heap – every Android app has its own process and memory
     * (sandboxing), which is why each app has to include LeakCanary itself.
     */
    class AppInfo(val name: String, val packageName: String, val version: String, val pid: Int) {
        override fun toString() = "$name ($packageName) v$version · PID $pid"
    }

    @Volatile var appInfo: AppInfo? = null
        private set

    /** Called once from LeakLabApp.onCreate(). */
    fun init(app: Application) {
        val pm = app.packageManager
        appInfo = AppInfo(
            name = app.applicationInfo.loadLabel(pm).toString(),
            packageName = app.packageName,
            version = pm.getPackageInfo(app.packageName, 0).versionName ?: "?",
            pid = Process.myPid(),
        )
    }

    class Summary(
        val tested: Int,
        val correct: Int,
        val errors: Int,          // false positives + false negatives
        val leaksFound: Int,
        val tracesCorrect: Int,
        val avgDetectMs: Long,    // -1 = no leak detected yet
    )

    // LeakCanary waits 5 s after onDestroy() before it calls an object "retained".
    private const val WATCH_DELAY_MS = 5_000L

    /** Logcat tag that the Level 1 PC monitor looks for. */
    const val LOG_TAG = "LeakLab"

    private val opened = ConcurrentHashMap.newKeySet<String>()
    private val closedAt = ConcurrentHashMap<String, Long>()     // class name -> time of onDestroy()
    private val results = ConcurrentHashMap<String, Result>()    // class name -> leak found
    @Volatile private var dumpStartedAt = 0L
    @Volatile private var lastAnalysedDumpAt = 0L

    @Volatile var progressText = "Tap \"Run all scenarios\" or open one scenario."
        private set
    @Volatile var progressPercent = -1       // -1 = no progress bar
        private set

    // Timing of the last heap dump / analysis (-1 = none yet)
    @Volatile var lastDumpMs = -1L
        private set
    @Volatile var lastAnalysisMs = -1L
        private set

    /**
     * Called whenever something changes. MainActivity sets it in onResume() and clears it in
     * onPause(). If it never cleared it, this global object would leak MainActivity!
     */
    @Volatile var onChange: (() -> Unit)? = null

    fun markOpened(className: String) {
        opened.add(className)
        closedAt.remove(className)
        results.remove(className)
        notifyChange()
    }

    fun markClosed(className: String) {
        closedAt[className] = SystemClock.uptimeMillis()
        notifyChange()
    }

    fun setProgress(text: String, percent: Int = -1) {
        progressText = text
        progressPercent = percent
        notifyChange()
    }

    fun reset() {
        opened.clear()
        closedAt.clear()
        results.clear()
        Log.i(LOG_TAG, "RESET")
        setProgress("Reset done. Global references cleared.")
    }

    private fun notifyChange() {
        onChange?.invoke()
    }

    // ---------------- "Run all": one heap dump at the right moment ----------------

    private val mainHandler = Handler(Looper.getMainLooper())
    private const val RUN_TIMEOUT_MS = 60_000L

    /**
     * Waits until every scenario of this run has closed, then waits LeakCanary's 5 s watch
     * delay so all leaks count as "retained", and then takes ONE heap dump. A fixed timer
     * is not enough: on a slow phone the screens can take longer to close.
     */
    fun dumpHeapWhenRunFinished(runStartedAt: Long) {
        val closeTimes = SCENARIOS.map { closedAt[it.activityClass.name] ?: 0L }
        val allClosed = closeTimes.all { it >= runStartedAt }
        val timedOut = SystemClock.uptimeMillis() - runStartedAt > RUN_TIMEOUT_MS
        if (!allClosed && !timedOut) {
            mainHandler.postDelayed({ dumpHeapWhenRunFinished(runStartedAt) }, 500)
            return
        }
        mainHandler.postAtTime({
            LeakCanary.dumpHeap()
            LeakLabApp.setAutoHeapDump(true)
        }, closeTimes.max() + WATCH_DELAY_MS + 1_000)
    }

    // ---------------- Events from LeakCanary (background thread) ----------------

    override fun onEvent(event: Event) {
        when (event) {
            is Event.DumpingHeap -> {
                dumpStartedAt = SystemClock.uptimeMillis()
                setProgress("Dumping heap… (the app freezes for a moment)", 0)
            }
            is Event.HeapDump -> {
                lastDumpMs = event.durationMillis
                setProgress("Heap dumped in ${event.durationMillis} ms. Analysing…", 0)
            }
            is Event.HeapAnalysisProgress -> {
                val step = event.step.name.lowercase().replace('_', ' ')
                setProgress("Analysing heap: $step", (event.progressPercent * 100).toInt())
            }
            is Event.HeapAnalysisDone.HeapAnalysisSucceeded -> {
                lastAnalysisMs = event.heapAnalysis.analysisDurationMillis
                val leakCount = collectLeaks(event.heapAnalysis)
                lastAnalysedDumpAt = dumpStartedAt
                setProgress("Analysis done: $leakCount leaking object(s) found in the heap.")
            }
            is Event.HeapAnalysisDone.HeapAnalysisFailed ->
                setProgress("Analysis failed: ${event.heapAnalysis.exception.message}")
            else -> {}
        }
    }

    private fun collectLeaks(analysis: HeapAnalysisSuccess): Int {
        val now = SystemClock.uptimeMillis()
        val leaks = analysis.applicationLeaks

        // Bridge to Level 1: write the findings to the system log (logcat).
        // The PC monitor reads them with `adb logcat`, so it can show WHY next to WHICH app.
        Log.i(LOG_TAG, "ANALYSIS leaks=${leaks.sumOf { it.leakTraces.size }} " +
            "dump_ms=$lastDumpMs analysis_ms=${analysis.analysisDurationMillis}")

        var count = 0
        for (leak in leaks) {
            for (trace in leak.leakTraces) {
                count++
                val leakingClass = trace.leakingObject.className
                val scenario = SCENARIOS.find { it.watchedClass.name == leakingClass }
                // leak.shortDescription = LeakCanary's suspected cause, e.g. "EventBus.listeners"
                Log.i(LOG_TAG, "LEAK object=$leakingClass cause=${leak.shortDescription} " +
                    "screen=${scenario?.activityClass?.simpleName ?: "-"}")
                if (scenario == null) continue
                val key = scenario.activityClass.name

                // Build a short chain like: EventBus.listeners -> ArrayList.elementData -> ...
                val chain = trace.referencePath.joinToString("") {
                    "\n  ↓ ${it.originObject.classSimpleName}.${it.referenceDisplayName}"
                }
                val text = "GC Root: ${trace.gcRootType.description}$chain" +
                    "\n  ↓\n${trace.leakingObject.classSimpleName}  ← LEAKING"

                // Correct only if the exact culprit reference (e.g. "EventBus.listeners")
                // is one of the links in the chain.
                val correct = trace.referencePath.any {
                    "${it.originObject.classSimpleName}.${it.referenceName}" == scenario.culprit
                }

                // Keep the first detection time if this leak was already reported earlier.
                val closed = closedAt[key]
                val detectedAfter = results[key]?.detectedAfterMs
                    ?: if (closed != null) now - closed else -1L
                results[key] = Result(
                    correct, text, detectedAfter,
                    leakingObject = leakingClass,
                    sourceScreen = scenario.activityClass.simpleName,
                )
            }
        }
        return count
    }

    // ---------------- Queries used by the UI ----------------

    fun resultOf(scenario: Scenario): Result? = results[scenario.activityClass.name]

    fun statusOf(scenario: Scenario): Status {
        val name = scenario.activityClass.name
        if (results.containsKey(name)) return Status.LEAK
        if (name !in opened) return Status.NOT_TESTED
        val closed = closedAt[name] ?: return Status.WAITING
        // Only trust "no leak" if a heap dump was analysed after the watch delay had passed.
        return if (lastAnalysedDumpAt > closed + WATCH_DELAY_MS) Status.NO_LEAK else Status.WAITING
    }

    /** Plain-text report for the Share button (e.g. to paste into the lab report). */
    fun buildTextReport(): String {
        val s = summary()
        val sb = StringBuilder()
        sb.appendLine("LeakLab – Memory Leak Detection Report")
        sb.appendLine("App    : ${appInfo ?: "unknown"}")
        sb.appendLine("Device : ${Build.MANUFACTURER} ${Build.MODEL}, Android ${Build.VERSION.RELEASE}")
        sb.appendLine()
        sb.appendLine("SUMMARY")
        sb.appendLine("Detection accuracy : ${s.correct}/${s.tested}")
        sb.appendLine("Trace accuracy     : ${s.tracesCorrect}/${s.leaksFound}")
        sb.appendLine("False +/-          : ${s.errors}")
        sb.appendLine("Avg detect time    : ${formatMs(s.avgDetectMs)}")
        sb.appendLine("Last heap dump     : ${formatMs(lastDumpMs)}")
        sb.appendLine("Last analysis      : ${formatMs(lastAnalysisMs)}")

        sb.appendLine()
        sb.appendLine("SCENARIOS")
        for (scenario in SCENARIOS) {
            val status = statusOf(scenario)
            val result = resultOf(scenario)
            sb.appendLine()
            sb.appendLine(scenario.activityClass.simpleName)
            sb.appendLine("  Expected : " +
                if (scenario.isRealLeak) "leak via ${scenario.culprit}" else "no leak")
            sb.appendLine("  Status   : ${status.label}")
            if (result != null) {
                sb.appendLine("  Source   : ${appInfo?.name} → ${result.sourceScreen}")
                sb.appendLine("  Leaking  : ${result.leakingObject}")
                sb.appendLine("  Trace    : " + if (result.traceCorrect) "correct" else "WRONG")
                sb.appendLine("  Detected : ${formatMs(result.detectedAfterMs)} after destroy")
                result.referenceChain.lines().forEach { sb.appendLine("    $it") }
            }
        }
        return sb.toString()
    }

    fun summary(): Summary {
        var tested = 0
        var correct = 0
        var errors = 0
        var leaksFound = 0
        var tracesCorrect = 0
        val detectTimes = mutableListOf<Long>()
        for (s in SCENARIOS) {
            val status = statusOf(s)
            if (status != Status.LEAK && status != Status.NO_LEAK) continue
            tested++
            val detected = status == Status.LEAK
            if (detected == s.isRealLeak) correct++ else errors++
            if (detected && s.isRealLeak) {
                leaksFound++
                val result = resultOf(s)
                if (result?.traceCorrect == true) tracesCorrect++
                if (result != null && result.detectedAfterMs >= 0) detectTimes.add(result.detectedAfterMs)
            }
        }
        val avgDetect = if (detectTimes.isEmpty()) -1L else detectTimes.average().toLong()
        return Summary(tested, correct, errors, leaksFound, tracesCorrect, avgDetect)
    }
}
