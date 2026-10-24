package com.iiitv.leaklab

import android.app.Application
import leakcanary.LeakCanary

class LeakLabApp : Application() {
    override fun onCreate() {
        super.onCreate()
        LeakCanary.config = LeakCanary.config.copy(
            // By default LeakCanary waits for 5 retained objects before dumping the heap.
            // We want it to react to every single leak.
            retainedVisibleThreshold = 1,
            // Performance: skip computing "Retaining X kB" for every object.
            // This makes the heap analysis noticeably faster.
            computeRetainedHeapSize = false,
            // Plug in our extension so we get every analysis result.
            eventListeners = LeakCanary.config.eventListeners + LeakReport,
        )
    }
}
