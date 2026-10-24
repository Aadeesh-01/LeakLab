package com.iiitv.leaklab

import android.app.Application
import leakcanary.LeakCanary

class LeakLabApp : Application() {

    companion object {
        // By default LeakCanary waits for 5 retained objects before dumping the heap.
        // We want it to react to every single leak.
        private const val AUTO_DUMP_THRESHOLD = 1
        private const val NO_AUTO_DUMP = 1000

        /**
         * Pauses LeakCanary's automatic heap dumps (used while "Run all" is running).
         * After a dump, LeakCanary stops watching every object it was watching, even ones
         * whose 5 s watch period was not over yet, so those leaks would never be reported.
         */
        fun setAutoHeapDump(enabled: Boolean) {
            LeakCanary.config = LeakCanary.config.copy(
                retainedVisibleThreshold = if (enabled) AUTO_DUMP_THRESHOLD else NO_AUTO_DUMP,
            )
        }
    }

    override fun onCreate() {
        super.onCreate()
        LeakReport.init(this)
        LeakCanary.config = LeakCanary.config.copy(
            retainedVisibleThreshold = AUTO_DUMP_THRESHOLD,
            // Performance: skip computing "Retaining X kB" for every object.
            // This makes the heap analysis noticeably faster.
            computeRetainedHeapSize = false,
            // Plug in our extension so we get every analysis result.
            eventListeners = LeakCanary.config.eventListeners + LeakReport,
        )
    }
}
