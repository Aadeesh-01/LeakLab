package com.iiitv.leaklab

import android.app.Activity
import android.os.Bundle
import android.widget.Button
import android.widget.TextView

/**
 * One controlled test case.
 * culprit       = the reference ("Class.field") that really causes the leak. The leak trace
 *                 is correct only if this exact reference is part of the reference chain.
 * watchedClass  = the object that should be freed (the activity itself, or a custom object).
 */
data class Scenario(
    val activityClass: Class<out Activity>,
    val description: String,
    val code: String,
    val why: String,
    val isRealLeak: Boolean,
    val culprit: String?,
    val watchedClass: Class<*> = activityClass,
)

val SCENARIOS = listOf(
    Scenario(
        StaticFieldLeakActivity::class.java,
        "Stores itself in a static field",
        "AppSingleton.lastActivity = this",
        "AppSingleton is a Kotlin object, stored in a static field. Static fields are GC roots, " +
            "so the activity stays reachable forever.",
        true, "AppSingleton.lastActivity",
    ),
    Scenario(
        ListenerLeakActivity::class.java,
        "Registers a listener, never unregisters",
        "EventBus.register { event -> title = event }\n// no unregister in onDestroy()",
        "The lambda uses `title`, so it holds a reference to the activity. " +
            "EventBus (a GC root) keeps the lambda in its listeners list.",
        true, "EventBus.listeners",
    ),
    Scenario(
        CacheLeakActivity::class.java,
        "Puts itself in a global cache",
        "ScreenCache.screens[\"cache_screen\"] = this",
        "The global HashMap is never cleared, so the activity can never be freed.",
        true, "ScreenCache.screens",
    ),
    Scenario(
        ThreadLeakActivity::class.java,
        "Long-running thread uses the activity",
        "Thread {\n    Thread.sleep(10 minutes)\n    runOnUiThread { title = \"done\" }\n}.start()",
        "A running thread is a GC root. Its Runnable (the lambda) holds the activity " +
            "until the thread finishes, 10 minutes later.",
        true, "Thread.target",
    ),
    Scenario(
        PlayerLeakActivity::class.java,
        "Custom object kept after release()",
        "player.release()   // we ask LeakCanary to watch it\n" +
            "PlayerRegistry.players.add(player)   // but it is still stored",
        "Not an activity: our own MusicPlayer object. After release() it should be freed, " +
            "but the global \"recently played\" list still holds it.",
        true, "PlayerRegistry.players",
        watchedClass = MusicPlayer::class.java,
    ),
    Scenario(
        FixedListenerActivity::class.java,
        "Registers AND unregisters a listener",
        "onCreate:  EventBus.register(listener)\nonDestroy: EventBus.unregister(listener)",
        "Unregistering in onDestroy() breaks the reference chain, so the GC can free the activity.",
        false, null,
    ),
    Scenario(
        CleanActivity::class.java,
        "Does nothing special",
        "// no global references",
        "Nothing outside the activity points to it, so it is freed right after onDestroy().",
        false, null,
    ),
)

fun scenarioFor(activityClass: Class<*>) = SCENARIOS.first { it.activityClass == activityClass }

/** Common screen for every scenario: shows what the code does and a Close button. */
open class ScenarioActivity : Activity() {

    // Pretend screen image (~2 MB), like the bitmaps real screens hold. A leaked screen
    // keeps it in memory too, so the leak is big enough for the Level 1 OS monitor to see.
    // fill() touches every page so the OS really counts it as used memory.
    private val screenImage = ByteArray(2 * 1024 * 1024).also { it.fill(1) }

    companion object {
        /** Set by "Run all": the screen closes itself right after it appears. */
        const val EXTRA_AUTO_CLOSE = "auto_close"
        private const val AUTO_CLOSE_DELAY_MS = 300L
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        LeakReport.markOpened(javaClass.name)

        val scenario = scenarioFor(javaClass)
        setContentView(R.layout.activity_scenario)
        findViewById<TextView>(R.id.chip).showChip(
            if (scenario.isRealLeak) "LEAKY CODE" else "CORRECT CODE",
            getColor(if (scenario.isRealLeak) R.color.status_leak else R.color.status_ok),
        )
        findViewById<TextView>(R.id.name).text = javaClass.simpleName
        findViewById<TextView>(R.id.description).text = scenario.description
        findViewById<TextView>(R.id.code).text = scenario.code
        findViewById<TextView>(R.id.why).text = scenario.why
        findViewById<Button>(R.id.closeButton).setOnClickListener { finish() }
    }

    override fun onResume() {
        super.onResume()
        if (intent.getBooleanExtra(EXTRA_AUTO_CLOSE, false)) {
            window.decorView.postDelayed({ finish() }, AUTO_CLOSE_DELAY_MS)
        }
    }

    override fun onDestroy() {
        LeakReport.markClosed(javaClass.name)
        super.onDestroy()
    }
}

// ---------------- LEAKING scenarios ----------------

class StaticFieldLeakActivity : ScenarioActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppSingleton.lastActivity = this
    }
}

class ListenerLeakActivity : ScenarioActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // The lambda uses `title`, so it keeps a reference to this activity.
        EventBus.register { event -> title = event }
    }
}

class CacheLeakActivity : ScenarioActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ScreenCache.screens["cache_screen"] = this
    }
}

class ThreadLeakActivity : ScenarioActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Thread {
            Thread.sleep(10 * 60 * 1000L)          // pretend a slow download (10 minutes)
            runOnUiThread { title = "Download done" }
        }.start()
    }
}

class PlayerLeakActivity : ScenarioActivity() {
    private val player = MusicPlayer("song.mp3")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        PlayerRegistry.players.add(player)             // "recently played" – never removed
    }

    override fun onDestroy() {
        player.release()                               // lifecycle of the player ends here
        super.onDestroy()
    }
}

// ---------------- NON-leaking scenarios ----------------

class FixedListenerActivity : ScenarioActivity() {
    private val listener: (String) -> Unit = { event -> title = event }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        EventBus.register(listener)
    }

    override fun onDestroy() {
        EventBus.unregister(listener)                  // the fix: break the reference chain
        super.onDestroy()
    }
}

class CleanActivity : ScenarioActivity()
