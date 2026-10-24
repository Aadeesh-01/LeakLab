package com.iiitv.leaklab

import android.widget.TextView

/** 850 -> "850 ms", 7230 -> "7.2 s", negative -> "–" (not measured yet). */
fun formatMs(ms: Long) = when {
    ms < 0 -> "–"
    ms < 1000 -> "$ms ms"
    else -> "%.1f s".format(ms / 1000.0)
}

/** Shows a small rounded label: coloured text on a light tint of the same colour. */
fun TextView.showChip(label: String, color: Int) {
    text = label
    setTextColor(color)
    background.mutate().setTint((color and 0x00FFFFFF) or 0x2E000000)
}
