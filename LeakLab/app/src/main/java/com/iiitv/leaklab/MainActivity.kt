package com.iiitv.leaklab

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.SystemClock
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import leakcanary.LeakCanary

class MainActivity : Activity() {

    companion object {
        /** adb shell am start -n com.iiitv.leaklab/.MainActivity --ez run_all true */
        const val EXTRA_RUN_ALL = "run_all"
    }

    private val cards = LinkedHashMap<Scenario, View>()
    private val expanded = mutableSetOf<Scenario>()
    private lateinit var statusText: TextView
    private lateinit var progressBar: ProgressBar

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        statusText = findViewById(R.id.statusText)
        findViewById<TextView>(R.id.appInfoText).text =
            "Monitored app: ${LeakReport.appInfo ?: "unknown"}"
        progressBar = findViewById(R.id.progressBar)

        findViewById<Button>(R.id.runAllButton).setOnClickListener { runAllScenarios() }
        findViewById<Button>(R.id.analyseButton).setOnClickListener {
            LeakReport.setProgress("Heap dump requested…")
            LeakCanary.dumpHeap()
        }
        findViewById<Button>(R.id.resetButton).setOnClickListener {
            clearGlobalReferences()
            LeakReport.reset()
        }
        findViewById<Button>(R.id.shareButton).setOnClickListener { shareReport() }

        val list = findViewById<LinearLayout>(R.id.scenarioList)
        for (scenario in SCENARIOS) {
            val card = layoutInflater.inflate(R.layout.item_scenario, list, false)
            card.findViewById<TextView>(R.id.name).text = scenario.activityClass.simpleName
            card.findViewById<TextView>(R.id.description).text = scenario.description
            card.findViewById<Button>(R.id.openButton).setOnClickListener {
                startActivity(Intent(this, scenario.activityClass))
            }
            card.findViewById<Button>(R.id.traceButton).setOnClickListener {
                if (!expanded.remove(scenario)) expanded.add(scenario)
                bindCard(scenario, card)
            }
            list.addView(card)
            cards[scenario] = card
        }
        handlePcCommand(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handlePcCommand(intent)
    }

    /** Lets the PC start "Run all" over adb, for demos and automated testing. */
    private fun handlePcCommand(intent: Intent) {
        if (intent.getBooleanExtra(EXTRA_RUN_ALL, false)) {
            intent.removeExtra(EXTRA_RUN_ALL)          // don't run again after a screen rotation
            runAllScenarios()
        }
    }

    override fun onResume() {
        super.onResume()
        LeakReport.onChange = { runOnUiThread { render() } }   // register
        render()
    }

    override fun onPause() {
        LeakReport.onChange = null                                // unregister (no leak!)
        super.onPause()
    }

    /** Opens every scenario one after another (each closes itself), then analyses the heap once. */
    private fun runAllScenarios() {
        // Pause automatic dumps, so ONE dump at the end sees every leak of this run.
        LeakLabApp.setAutoHeapDump(false)
        val intents = SCENARIOS.map {
            Intent(this, it.activityClass).putExtra(ScenarioActivity.EXTRA_AUTO_CLOSE, true)
        }
        startActivities(intents.toTypedArray())
        LeakReport.setProgress("Running ${SCENARIOS.size} scenarios, then waiting for the 5 s watch delay…")
        LeakReport.dumpHeapWhenRunFinished(SystemClock.uptimeMillis())
    }

    /** Sends the text report to any app (WhatsApp, Gmail, Drive, Notes, …). */
    private fun shareReport() {
        val send = Intent(Intent.ACTION_SEND).apply {
            type = "text/plain"
            putExtra(Intent.EXTRA_SUBJECT, "LeakLab report")
            putExtra(Intent.EXTRA_TEXT, LeakReport.buildTextReport())
        }
        startActivity(Intent.createChooser(send, "Share report"))
    }

    private fun render() {
        statusText.text = LeakReport.progressText
        val percent = LeakReport.progressPercent
        progressBar.visibility = if (percent >= 0) View.VISIBLE else View.INVISIBLE
        progressBar.progress = percent

        val s = LeakReport.summary()
        setStat(R.id.statDetection, if (s.tested == 0) "–" else "${s.correct}/${s.tested}", "Detection")
        setStat(R.id.statTraces, if (s.leaksFound == 0) "–" else "${s.tracesCorrect}/${s.leaksFound}", "Trace accuracy")
        setStat(R.id.statErrors, "${s.errors}", "False +/−")
        setStat(R.id.statDetectTime, formatMs(s.avgDetectMs), "Avg detect time")
        setStat(R.id.statDumpTime, formatMs(LeakReport.lastDumpMs), "Heap dump")
        setStat(R.id.statAnalysisTime, formatMs(LeakReport.lastAnalysisMs), "Analysis")

        for ((scenario, card) in cards) bindCard(scenario, card)
    }

    private fun setStat(id: Int, value: String, label: String) {
        val tile = findViewById<View>(id)
        tile.findViewById<TextView>(R.id.statValue).text = value
        tile.findViewById<TextView>(R.id.statLabel).text = label
    }

    private fun bindCard(scenario: Scenario, card: View) {
        val status = LeakReport.statusOf(scenario)
        val result = LeakReport.resultOf(scenario)
        card.findViewById<TextView>(R.id.chip).showChip(status.label, getColor(status.colorRes))

        val verdict = card.findViewById<TextView>(R.id.verdict)
        if (status == LeakReport.Status.LEAK || status == LeakReport.Status.NO_LEAK) {
            val correct = (status == LeakReport.Status.LEAK) == scenario.isRealLeak
            var text = if (correct) "✓ Correct detection" else "✗ Wrong detection"
            if (result != null && scenario.isRealLeak) {
                text += if (result.traceCorrect) "\n✓ Trace contains ${scenario.culprit}"
                else "\n✗ Trace misses ${scenario.culprit}"
            }
            if (result != null && result.detectedAfterMs >= 0) {
                text += "\n⏱ Detected ${formatMs(result.detectedAfterMs)} after it was destroyed"
            }
            verdict.text = text
            val good = correct && (result == null || result.traceCorrect)
            verdict.setTextColor(getColor(if (good) R.color.status_ok else R.color.status_leak))
        } else {
            verdict.text = if (scenario.isRealLeak) "Expected: leak via ${scenario.culprit}"
            else "Expected: no leak"
            verdict.setTextColor(getColor(R.color.text_secondary))
        }

        val traceButton = card.findViewById<Button>(R.id.traceButton)
        val trace = card.findViewById<TextView>(R.id.trace)
        val showTrace = result != null && scenario in expanded
        traceButton.visibility = if (result != null) View.VISIBLE else View.GONE
        traceButton.text = if (showTrace) "Hide leak trace" else "Show leak trace"
        trace.visibility = if (showTrace) View.VISIBLE else View.GONE
        trace.text = result?.referenceChain

        val source = card.findViewById<TextView>(R.id.source)
        source.visibility = if (result != null) View.VISIBLE else View.GONE
        if (result != null) {
            val app = LeakReport.appInfo
            source.text = "App: ${app?.name} (${app?.packageName})\n" +
                "Screen: ${result.sourceScreen}\n" +
                "Leaking object: ${result.leakingObject}"
        }
    }
}
