package com.iiitv.leaklab

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import leakcanary.LeakCanary

class MainActivity : Activity() {

    companion object {
        // Time for all scenarios to open and close + LeakCanary's 5 s watch delay.
        private const val RUN_ALL_DUMP_DELAY_MS = 10_000L
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    private val cards = LinkedHashMap<Scenario, View>()
    private val expanded = mutableSetOf<Scenario>()
    private lateinit var statusText: TextView
    private lateinit var progressBar: ProgressBar

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        statusText = findViewById(R.id.statusText)
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
        val intents = SCENARIOS.map {
            Intent(this, it.activityClass).putExtra(ScenarioActivity.EXTRA_AUTO_CLOSE, true)
        }
        startActivities(intents.toTypedArray())
        LeakReport.setProgress("Running ${SCENARIOS.size} scenarios, then waiting for the 5 s watch delay…")
        // This lambda does not use the activity, so the delayed task cannot leak it.
        mainHandler.postDelayed({ LeakCanary.dumpHeap() }, RUN_ALL_DUMP_DELAY_MS)
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
                text += if (result.traceCorrect) "  ·  trace points to ${scenario.culprit}"
                else "  ·  trace misses ${scenario.culprit}"
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
    }
}
