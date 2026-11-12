"""
Window (GUI) version of the two-level leak monitor.

Uses the same logic as phone_monitor.py (adb + dumpsys meminfo + LeakLab's logcat lines),
but shows the results in a window instead of the terminal.

Run:
    python monitor_gui.py
"""
import csv
import os
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk
from types import SimpleNamespace

import phone_monitor as pm

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

COLORS = {
    "LEAK?": "#d32f2f",
    "growing": "#e08a00",
    "stable": "#2e7d32",
    "measuring": "#8a90a0",
}


# ---------------- background sampling ----------------

class Worker(threading.Thread):
    """
    Takes one sample every `interval` seconds in the background and puts the result in
    a queue. A scan of the whole phone takes 5–10 s, so doing it in the window's own
    thread would freeze the window.
    """

    def __init__(self, adb, settings, results):
        super().__init__(daemon=True)
        self.adb = adb
        self.settings = settings          # app, device, interval, demo
        self.results = results
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        log_path = os.path.join(LOG_DIR, datetime.now().strftime("memory_%Y%m%d_%H%M%S.csv"))
        with open(log_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if self.settings.app:
                writer.writerow(["time", "app", "pid", "pss_kb", "java_kb", "native_kb",
                                 "activities", "views", "level2_leaks"])
            else:
                writer.writerow(["time", "process", "pid", "state", "pss_kb"])

            state = {}
            last_trigger = 0.0
            while not self.stop_event.is_set():
                started = time.time()
                try:
                    last_trigger = pm.maybe_run_demo(self.adb, self.settings, last_trigger)
                    if self.settings.app:
                        result = self.sample_app(state, writer)
                    else:
                        result = self.sample_all(state, writer)
                    f.flush()
                except pm.AdbError as e:
                    result = {"error": str(e)}
                self.results.put(result)
                self.stop_event.wait(max(0.0, self.settings.interval - (time.time() - started)))
        self.results.put({"stopped": log_path})

    def sample_all(self, state, writer):
        """Every process on the phone (same as phone_monitor.monitor_all)."""
        started = time.time()
        procs = pm.parse_all_processes(
            pm.adb_shell(self.adb, self.settings.device, "dumpsys", "-t", "60", "meminfo"))
        now = datetime.now().strftime("%H:%M:%S")
        history = state.setdefault("history", {})
        state["sample"] = state.get("sample", 0) + 1

        rows = []
        for name, (pid, kb, proc_state) in procs.items():
            h = history.get(name)
            if h is None or h["pid"] != pid:          # new process, or the app restarted
                h = history[name] = {"pid": pid, "values": []}
            h["values"].append(kb)
            writer.writerow([now, name, pid, proc_state, kb])
            v = h["values"]
            rows.append({"name": name, "state": proc_state, "values": v,
                         "verdict": pm.verdict(v), "growth": pm.baseline_growth(v) or 0})

        rank = {"LEAK?": 0, "growing": 1, "stable": 2, "measuring": 3}
        rows.sort(key=lambda r: (rank[r["verdict"]], -r["growth"], -r["values"][-1]))
        return {"mode": "all", "time": now, "sample": state["sample"], "rows": rows,
                "scan_s": time.time() - started}

    def sample_app(self, state, writer):
        """One app: Level 1 numbers + Level 2 findings (same as phone_monitor.monitor_app)."""
        app = self.settings.app
        info = pm.parse_app(pm.adb_shell(self.adb, self.settings.device, "dumpsys", "meminfo", app))
        now = datetime.now().strftime("%H:%M:%S")
        state["sample"] = state.get("sample", 0) + 1
        if info is None:
            return {"mode": "app", "time": now, "sample": state["sample"], "info": None}

        if info["pid"] != state.get("pid"):           # first sample, or the app restarted
            state["pid"] = info["pid"]
            state["history"] = {"pss": [], "java": [], "activities": []}
            state["level2"] = pm.new_level2_state()
        history = state["history"]
        for key in history:
            history[key].append(info[key])
        try:
            pm.read_level2(self.adb, self.settings.device, info["pid"], state["level2"])
        except pm.AdbError:
            pass                                      # keep what Level 2 reported so far
        leaks = list(state["level2"]["leaks"].values())
        writer.writerow([now, app, info["pid"], info["pss"], info["java"], info["native"],
                         info["activities"], info["views"], len(leaks)])
        return {"mode": "app", "time": now, "sample": state["sample"], "info": info,
                "history": {k: list(v) for k, v in history.items()},
                "summary": state["level2"]["summary"], "leaks": leaks,
                "verdict": pm.level1_verdict(history)}


# ---------------- the window ----------------

class MonitorApp:
    def __init__(self, root, adb):
        self.root = root
        self.adb = adb
        self.worker = None
        self.results = queue.Queue()

        root.title("LeakLab – Two-Level Leak Monitor")
        root.geometry("1050x680")
        root.minsize(900, 560)
        style = ttk.Style()
        style.configure("Treeview", rowheight=24)
        style.configure("Big.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Value.TLabel", font=("Segoe UI", 13, "bold"))

        self.build_controls()
        self.status = ttk.Label(root, text="Connect the phone (USB debugging on) and press Start.",
                                padding=(12, 4))
        self.status.pack(fill="x")
        self.all_frame = self.build_all_view()
        self.app_frame = self.build_app_view()
        self.show_view()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(200, self.poll_results)

    # ----- top bar -----

    def build_controls(self):
        bar = ttk.Frame(self.root, padding=(12, 10))
        bar.pack(fill="x")

        self.mode = tk.StringVar(value="all")
        ttk.Radiobutton(bar, text="All apps", variable=self.mode, value="all",
                        command=self.show_view).pack(side="left")
        ttk.Radiobutton(bar, text="One app:", variable=self.mode, value="app",
                        command=self.show_view).pack(side="left", padx=(12, 4))
        self.package = tk.StringVar(value=pm.DEMO_APP)
        ttk.Entry(bar, textvariable=self.package, width=26).pack(side="left")

        ttk.Label(bar, text="Every").pack(side="left", padx=(16, 4))
        self.interval = tk.IntVar(value=10)
        ttk.Spinbox(bar, from_=3, to=60, width=4, textvariable=self.interval).pack(side="left")
        ttk.Label(bar, text="s").pack(side="left", padx=(2, 0))

        self.demo_on = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Demo: press LeakLab's Run all every",
                        variable=self.demo_on).pack(side="left", padx=(16, 4))
        self.demo_s = tk.IntVar(value=30)
        ttk.Spinbox(bar, from_=15, to=300, width=4, textvariable=self.demo_s).pack(side="left")
        ttk.Label(bar, text="s").pack(side="left", padx=(2, 0))

        self.start_button = ttk.Button(bar, text="▶ Start", command=self.toggle)
        self.start_button.pack(side="right")

    # ----- "All apps" view -----

    def build_all_view(self):
        frame = ttk.Frame(self.root, padding=(12, 0, 12, 12))
        ttk.Label(frame, text=f"Rule: LEAK? = lowest memory grew by ≥ {pm.LEAK_MB} MB and "
                              f"≥ {pm.LEAK_PCT}% (first {pm.WINDOW} vs last {pm.WINDOW} samples). "
                              "Double-click an app to see why it leaks.",
                  foreground="#5f6475").pack(anchor="w", pady=(0, 6))

        columns = ("process", "state", "pss", "growth", "trend", "verdict")
        tree = ttk.Treeview(frame, columns=columns, show="headings")
        for col, title, width, anchor in [
            ("process", "Process", 330, "w"), ("state", "State", 110, "w"),
            ("pss", "PSS now", 90, "e"), ("growth", "Baseline Δ", 90, "e"),
            ("trend", "Trend", 190, "w"), ("verdict", "Verdict", 90, "w"),
        ]:
            tree.heading(col, text=title)
            tree.column(col, width=width, anchor=anchor)
        for verdict, color in COLORS.items():
            tree.tag_configure(verdict, foreground=color)
        tree.tag_configure("LEAK?", foreground=COLORS["LEAK?"], font=("Segoe UI", 9, "bold"))
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        tree.bind("<Double-1>", self.open_app_from_table)
        self.all_tree = tree
        return frame

    # ----- "One app" view -----

    def build_app_view(self):
        frame = ttk.Frame(self.root, padding=(12, 0, 12, 12))
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(0, weight=1)

        # Level 1
        level1 = ttk.LabelFrame(frame, text=" LEVEL 1 – what the OS sees from outside (dumpsys meminfo) ",
                                padding=10)
        level1.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.l1_values = {}
        for row, (key, label) in enumerate([("pid", "PID"), ("pss", "Total PSS"),
                                            ("java", "Java heap"), ("native", "Native heap"),
                                            ("activities", "Activities (screens in memory)"),
                                            ("views", "Views")]):
            ttk.Label(level1, text=label).grid(row=row, column=0, sticky="w", pady=1)
            value = ttk.Label(level1, text="–", style="Value.TLabel")
            value.grid(row=row, column=1, sticky="e", pady=1)
            self.l1_values[key] = value
        level1.columnconfigure(1, weight=1)
        self.pss_chart = self.make_chart(level1, 6, "Total PSS (MB)")
        self.act_chart = self.make_chart(level1, 8, "Activities still in memory")

        # Level 2
        level2 = ttk.LabelFrame(frame, text=" LEVEL 2 – what LeakCanary found inside the app (logcat) ",
                                padding=10)
        level2.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.l2_summary = ttk.Label(level2, text="no heap analysis reported yet")
        self.l2_summary.pack(anchor="w", pady=(0, 6))
        tree = ttk.Treeview(level2, columns=("screen", "cause"), show="headings")
        tree.heading("screen", text="Screen")
        tree.heading("cause", text="Cause (reference that keeps it alive)")
        tree.column("screen", width=190)
        tree.column("cause", width=250)
        tree.pack(fill="both", expand=True)
        self.l2_tree = tree

        # Result
        result = ttk.LabelFrame(frame, text=" RESULT ", padding=10)
        result.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Label(result, text="Which app (Level 1):").grid(row=0, column=0, sticky="w")
        self.which = ttk.Label(result, text="–", font=("Segoe UI", 10, "bold"))
        self.which.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(result, text="Why (Level 2):").grid(row=1, column=0, sticky="w")
        self.why = ttk.Label(result, text="–", font=("Segoe UI", 10, "bold"), wraplength=820)
        self.why.grid(row=1, column=1, sticky="w", padx=8)
        return frame

    def make_chart(self, parent, row, title):
        ttk.Label(parent, text=title, foreground="#5f6475").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(10, 2))
        canvas = tk.Canvas(parent, height=95, bg="white", highlightthickness=1,
                           highlightbackground="#d0d4de")
        canvas.grid(row=row + 1, column=0, columnspan=2, sticky="ew")
        return canvas

    @staticmethod
    def draw_chart(canvas, values, color):
        """Simple line chart of the last 40 samples."""
        canvas.delete("all")
        values = values[-40:]
        if not values:
            return
        w = max(canvas.winfo_width(), 50)
        h = max(canvas.winfo_height(), 50)
        pad = 12
        low, high = min(values), max(values)
        span = (high - low) or 1
        step = (w - 2 * pad) / max(len(values) - 1, 1)
        points = []
        for i, v in enumerate(values):
            points += [pad + i * step, h - pad - (v - low) / span * (h - 2 * pad)]
        if len(points) >= 4:
            canvas.create_line(*points, fill=color, width=2, smooth=True)
        canvas.create_oval(points[-2] - 3, points[-1] - 3, points[-2] + 3, points[-1] + 3,
                           fill=color, outline=color)
        canvas.create_text(pad, 4, anchor="nw", text=f"max {high:.4g}", fill="#8a90a0",
                           font=("Segoe UI", 8))
        canvas.create_text(pad, h - 2, anchor="sw", text=f"min {low:.4g}", fill="#8a90a0",
                           font=("Segoe UI", 8))

    # ----- actions -----

    def show_view(self):
        if self.mode.get() == "all":
            self.app_frame.pack_forget()
            self.all_frame.pack(fill="both", expand=True)
        else:
            self.all_frame.pack_forget()
            self.app_frame.pack(fill="both", expand=True)

    def toggle(self):
        if self.worker:
            self.stop()
        else:
            self.start()

    def start(self):
        try:
            settings = SimpleNamespace(
                app=self.package.get().strip() if self.mode.get() == "app" else None,
                device=None,
                interval=max(3, int(self.interval.get())),
                demo=int(self.demo_s.get()) if self.demo_on.get() else 0,
            )
        except (tk.TclError, ValueError):
            messagebox.showerror("LeakLab monitor", "Interval and demo seconds must be numbers.")
            return
        if self.mode.get() == "app" and not settings.app:
            messagebox.showerror("LeakLab monitor", "Enter a package name, e.g. com.iiitv.leaklab")
            return
        self.clear_views()
        self.worker = Worker(self.adb, settings, self.results)
        self.worker.start()
        self.start_button.configure(text="■ Stop")
        self.set_status("Scanning… (a full phone scan can take 5–10 s)")

    def stop(self):
        self.worker.stop()
        self.worker = None
        self.start_button.configure(text="▶ Start")
        self.set_status("Stopping…")

    def open_app_from_table(self, _event):
        selected = self.all_tree.focus()
        if not selected:
            return
        self.package.set(self.all_tree.item(selected, "values")[0])
        self.mode.set("app")
        if self.worker:
            self.stop()
        self.show_view()
        self.start()

    def on_close(self):
        if self.worker:
            self.worker.stop()
        self.root.destroy()

    def set_status(self, text, color="#1b1e28"):
        self.status.configure(text=text, foreground=color)

    def clear_views(self):
        self.all_tree.delete(*self.all_tree.get_children())
        self.l2_tree.delete(*self.l2_tree.get_children())
        for label in self.l1_values.values():
            label.configure(text="–")
        self.l2_summary.configure(text="no heap analysis reported yet")
        self.which.configure(text="–", foreground="#1b1e28")
        self.why.configure(text="–", foreground="#1b1e28")
        self.pss_chart.delete("all")
        self.act_chart.delete("all")

    # ----- results from the background thread -----

    def poll_results(self):
        try:
            while True:
                result = self.results.get_nowait()
                if "stopped" in result:
                    self.set_status(f"Stopped. Log saved to {result['stopped']}")
                elif not self.worker:
                    continue                       # late result from a stopped run
                elif "error" in result:
                    self.set_status(f"Phone not reachable: {result['error']}  –  check the cable, "
                                    "unlock the phone and allow USB debugging. Retrying…",
                                    COLORS["LEAK?"])
                elif result["mode"] == "all":
                    self.show_all(result)
                else:
                    self.show_app(result)
        except queue.Empty:
            pass
        self.root.after(200, self.poll_results)

    def demo_text(self):
        return f"   ·   Demo: Run all every {self.demo_s.get()} s" if self.demo_on.get() else ""

    def show_all(self, r):
        suspects = [row["name"] for row in r["rows"] if row["verdict"] == "LEAK?"]
        self.set_status(f"Sample {r['sample']} at {r['time']}   ·   {len(r['rows'])} processes, "
                        f"scan took {r['scan_s']:.1f} s   ·   suspected leaks: "
                        f"{', '.join(suspects) if suspects else 'none'}{self.demo_text()}",
                        COLORS["LEAK?"] if suspects else "#1b1e28")
        tree = self.all_tree
        tree.delete(*tree.get_children())
        for row in r["rows"]:
            v = row["values"]
            tree.insert("", "end", tags=(row["verdict"],), values=(
                row["name"], row["state"], pm.mb(v[-1]).strip(),
                pm.signed_mb(row["growth"]).strip(), pm.sparkline(v), row["verdict"]))

    def show_app(self, r):
        info = r["info"]
        if info is None:
            self.set_status(f"Sample {r['sample']} at {r['time']}   ·   "
                            f"{self.package.get()} is not running. Start it on the phone.",
                            COLORS["growing"])
            return
        self.set_status(f"Sample {r['sample']} at {r['time']}   ·   {self.package.get()}"
                        f"{self.demo_text()}")
        self.l1_values["pid"].configure(text=str(info["pid"]))
        for key in ("pss", "java", "native"):
            self.l1_values[key].configure(text=pm.mb(info[key]).strip())
        self.l1_values["activities"].configure(text=str(info["activities"]))
        self.l1_values["views"].configure(text=str(info["views"]))
        self.draw_chart(self.pss_chart, [kb / 1024 for kb in r["history"]["pss"]], "#3d5afe")
        self.draw_chart(self.act_chart, r["history"]["activities"], COLORS["LEAK?"])

        summary, leaks = r["summary"], r["leaks"]
        if summary is None:
            self.l2_summary.configure(text="no heap analysis reported yet")
        else:
            self.l2_summary.configure(
                text=f"{len(leaks)} leak(s) found   (last heap dump {summary.get('dump_ms', '?')} ms, "
                     f"analysis {summary.get('analysis_ms', '?')} ms)")
        self.l2_tree.delete(*self.l2_tree.get_children())
        for leak in leaks:
            self.l2_tree.insert("", "end", values=(leak["screen"], leak["cause"]))

        verdict = r["verdict"]
        self.which.configure(text=verdict, foreground=(
            COLORS["LEAK?"] if verdict.startswith("LEAK") else
            COLORS["measuring"] if verdict.startswith("measuring") else COLORS["stable"]))
        causes = sorted({leak["cause"] for leak in leaks})
        self.why.configure(text=", ".join(causes) if causes else "no leaks reported",
                           foreground=COLORS["LEAK?"] if causes else COLORS["measuring"])


def main():
    root = tk.Tk()
    try:
        adb = pm.find_adb()
    except SystemExit as e:
        messagebox.showerror("LeakLab monitor", str(e))
        return
    MonitorApp(root, adb)
    root.mainloop()


if __name__ == "__main__":
    main()
