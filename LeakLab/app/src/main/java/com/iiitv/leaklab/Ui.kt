package com.iiitv.leaklab

import android.widget.TextView

/** Shows a small rounded label: coloured text on a light tint of the same colour. */
fun TextView.showChip(label: String, color: Int) {
    text = label
    setTextColor(color)
    background.mutate().setTint((color and 0x00FFFFFF) or 0x2E000000)
}
