package com.iiitv.leaklab

import android.app.Activity

// App-wide objects. A Kotlin `object` lives for the whole lifetime of the app
// (it is held in a static field), so anything it points to can never be freed.

object AppSingleton {
    var lastActivity: Activity? = null
}

object EventBus {
    val listeners = mutableListOf<(String) -> Unit>()

    fun register(listener: (String) -> Unit) {
        listeners.add(listener)
    }

    fun unregister(listener: (String) -> Unit) {
        listeners.remove(listener)
    }
}

object ScreenCache {
    val screens = HashMap<String, Activity>()
}

/** "Fixes" the leaks by clearing the global references. (A running thread cannot be cleared.) */
fun clearGlobalReferences() {
    AppSingleton.lastActivity = null
    EventBus.listeners.clear()
    ScreenCache.screens.clear()
    PlayerRegistry.players.clear()
}
