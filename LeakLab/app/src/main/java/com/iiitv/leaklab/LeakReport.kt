package com.iiitv.leaklab

import android.os.SystemClock
import leakcanary.EventListener
import leakcanary.EventListener.Event
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

    class Result(val traceCorrect: Boolean, val referenceChain: String)

    class Summary(
        val tested: Int,
        val correct: Int,
        val errors: Int,          // false positives + false negatives
        val leaksFound: Int,
        val tracesCorrect: Int,
    )

    // LeakCanary waits 5 s after onDestroy() before it calls an object "retained".
    private const val WATCH_DELAY_MS = 5_000L

    private val opened = ConcurrentHashMap.newKeySet<String>()
    private val closedAt = ConcurrentHashMap<String, Long>()     // class name -> time of onDestroy()
    private val results = ConcurrentHashMap<String, Result>()    // class name -> leak found
    @Volatile private var dumpStartedAt = 0L
    @Volatile private var lastAnalysedDumpAt = 0L

    @Volatile var progressText = "Tap \"Run all scenarios\" or open one scenario."
        private set
    @Volatile var progressPercent = -1       // -1 = no progress bar
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
        setProgress("Reset done. Global references cleared.")
    }

    private fun notifyChange() {
        onChange?.invoke()
    }

    // ---------------- Events from LeakCanary (background thread) ----------------

    override fun onEvent(event: Event) {
        when (event) {
            is Event.DumpingHeap -> {
                dumpStartedAt = SystemClock.uptimeMillis()
                setProgress("Dumping heap… (the app freezes for a moment)", 0)
            }
            is Event.HeapDump ->
                setProgress("Heap dumped in ${event.durationMillis} ms. Analysing…", 0)
            is Event.HeapAnalysisProgress -> {
                val step = event.step.name.lowercase().replace('_', ' ')
                setProgress("Analysing heap: $step", (event.progressPercent * 100).toInt())
            }
            is Event.HeapAnalysisDone.HeapAnalysisSucceeded -> {
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
        var count = 0
        for (leak in analysis.applicationLeaks) {
            for (trace in leak.leakTraces) {
                count++
                val className = trace.leakingObject.className
                val scenario = SCENARIOS.find { it.activityClass.name == className } ?: continue

                // Build a short chain like: EventBus.listeners -> ArrayList.elementData -> ...
                val chain = trace.referencePath.joinToString("") {
                    "\n  ↓ ${it.originObject.classSimpleName}.${it.referenceDisplayName}"
                }
                val text = "GC Root: ${trace.gcRootType.description}$chain" +
                    "\n  ↓\n${trace.leakingObject.classSimpleName}  ← LEAKING"

                val culprit = scenario.culprit
                val correct = culprit != null && trace.toString().contains(culprit)
                results[className] = Result(correct, text)
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

    fun summary(): Summary {
        var tested = 0
        var correct = 0
        var errors = 0
        var leaksFound = 0
        var tracesCorrect = 0
        for (s in SCENARIOS) {
            val status = statusOf(s)
            if (status != Status.LEAK && status != Status.NO_LEAK) continue
            tested++
            val detected = status == Status.LEAK
            if (detected == s.isRealLeak) correct++ else errors++
            if (detected && s.isRealLeak) {
                leaksFound++
                if (resultOf(s)?.traceCorrect == true) tracesCorrect++
            }
        }
        return Summary(tested, correct, errors, leaksFound, tracesCorrect)
    }
}
