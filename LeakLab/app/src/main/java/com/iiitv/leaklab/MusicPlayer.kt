package com.iiitv.leaklab

import leakcanary.AppWatcher

/**
 * A custom object with its own lifecycle (create -> release).
 *
 * LeakCanary only watches Android objects (Activities, Fragments, ...) on its own.
 * Here WE tell LeakCanary to watch our object: once release() is called, nothing should
 * use the player any more, so it must be garbage collected. If it is still in memory
 * 5 seconds later, LeakCanary reports it as a leak.
 */
class MusicPlayer(val song: String) {
    private val audioBuffer = ByteArray(512 * 1024)   // pretend decoded audio (512 kB)

    fun release() {
        AppWatcher.objectWatcher.expectWeaklyReachable(this, "MusicPlayer.release() was called")
    }
}

/** Global "recently played" list – a GC root that keeps released players alive. */
object PlayerRegistry {
    val players = mutableListOf<MusicPlayer>()
}
