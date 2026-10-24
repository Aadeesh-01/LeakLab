"""
Runs controlled memory-leak scenarios on the demo app and evaluates the detector.

Usage:
    python main.py                 # run all scenarios
    python main.py ChatActivity    # run one scenario
"""
import sys
import tracemalloc

import demo_app
from analyzer import find_leak_trace, format_trace
from watcher import ObjectWatcher

# (activity class, is it really leaking?, word that must appear in the leak trace)
SCENARIOS = [
    (demo_app.ProfileActivity,  True,  "last_activity"),
    (demo_app.ChatActivity,     True,  "listeners"),
    (demo_app.GalleryActivity,  True,  "IMAGE_CACHE"),
    (demo_app.DownloadActivity, True,  "PENDING_TASKS"),
    (demo_app.SettingsActivity, False, None),
    (demo_app.AboutActivity,    False, None),
]

GC_ROOTS = {"globals of demo_app": demo_app}


def run_scenario(activity_class, expected_leak, culprit):
    print("=" * 70)
    print(f"Scenario: {activity_class.__name__}")
    print(f"  {activity_class.__doc__.strip()}")
    print("-" * 70)

    demo_app.reset_app_state()
    watcher = ObjectWatcher()
    manager = demo_app.ActivityManager(watcher)

    memory_before = tracemalloc.get_traced_memory()[0]
    activity = manager.launch(activity_class())
    manager.destroy(activity)
    del activity                      # our own variable must not keep it alive

    retained = watcher.check_retained()
    memory_after = tracemalloc.get_traced_memory()[0]
    print(f"Memory statistics: {(memory_after - memory_before) / 1024:.0f} KB "
          f"still in use after destroy")

    detected = len(retained) > 0
    trace_correct = None

    if not detected:
        print("Result: no leak, the activity was garbage collected.")
    for w in retained:
        print(f"Result: LEAK DETECTED -> {w.class_name} is still in memory\n")
        path = find_leak_trace(w.ref(), GC_ROOTS)
        if path is None:
            print("  Could not find a reference chain from the GC roots.")
            trace_correct = False
        else:
            print(format_trace(path, w.description))
            trace_correct = any(culprit in label for label, _ in path) if culprit else False

    print()
    return {
        "name": activity_class.__name__,
        "expected": expected_leak,
        "detected": detected,
        "trace_correct": trace_correct,
    }


def print_evaluation(results):
    print("=" * 70)
    print("EVALUATION")
    print("=" * 70)
    print(f"{'Scenario':<20}{'Real leak':<12}{'Detected':<12}{'Trace correct':<14}")
    print("-" * 58)
    for r in results:
        trace = "-" if r["trace_correct"] is None else ("yes" if r["trace_correct"] else "NO")
        print(f"{r['name']:<20}{'yes' if r['expected'] else 'no':<12}"
              f"{'yes' if r['detected'] else 'no':<12}{trace:<14}")

    correct = sum(r["expected"] == r["detected"] for r in results)
    false_pos = sum(r["detected"] and not r["expected"] for r in results)
    false_neg = sum(r["expected"] and not r["detected"] for r in results)
    leaks_found = [r for r in results if r["detected"] and r["expected"]]
    traces_ok = sum(bool(r["trace_correct"]) for r in leaks_found)

    print("-" * 58)
    print(f"Detection accuracy : {correct}/{len(results)}")
    print(f"False positives    : {false_pos}")
    print(f"False negatives    : {false_neg}")
    print(f"Trace accuracy     : {traces_ok}/{len(leaks_found)} traces point to the real cause")


def main():
    tracemalloc.start()
    scenarios = SCENARIOS
    if len(sys.argv) > 1:
        scenarios = [s for s in SCENARIOS if s[0].__name__ == sys.argv[1]]
        if not scenarios:
            print("Unknown scenario. Choose one of:",
                  ", ".join(s[0].__name__ for s in SCENARIOS))
            return

    results = [run_scenario(*s) for s in scenarios]
    print_evaluation(results)


if __name__ == "__main__":
    main()
