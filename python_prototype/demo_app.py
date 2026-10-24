"""
A tiny simulated "Android app".

Activities have a lifecycle (on_create -> on_destroy). The global objects below live
for the whole app lifetime, so anything they point to can never be garbage collected.
"""


# ---------------- Base "framework" classes ----------------

class Activity:
    def __init__(self):
        self.name = type(self).__name__
        self.bitmap = bytearray(1_000_000)   # pretend screen image (~1 MB)

    def on_create(self):
        pass

    def on_destroy(self):
        pass

    def on_event(self, event):
        print(f"{self.name} got event: {event}")


class ActivityManager:
    """Plays the role of the Android framework: runs the lifecycle and
    automatically tells the watcher whenever an activity is destroyed."""

    def __init__(self, watcher):
        self.watcher = watcher

    def launch(self, activity):
        activity.on_create()
        return activity

    def destroy(self, activity):
        activity.on_destroy()
        self.watcher.watch(activity, f"{activity.name} received on_destroy()")


# ---------------- App-wide (global) objects ----------------

class EventBus:
    def __init__(self):
        self.listeners = []

    def register(self, listener):
        self.listeners.append(listener)

    def unregister(self, listener):
        self.listeners.remove(listener)


class AppSingleton:
    last_activity = None          # static field


class BackgroundTask:
    def __init__(self, owner):
        self.owner = owner        # the activity that started the task


BUS = EventBus()
IMAGE_CACHE = {}
PENDING_TASKS = []


def reset_app_state():
    BUS.listeners.clear()
    IMAGE_CACHE.clear()
    PENDING_TASKS.clear()
    AppSingleton.last_activity = None


# ---------------- Activities (one per test scenario) ----------------

class ProfileActivity(Activity):
    """LEAK: stores itself in a static field."""
    def on_create(self):
        AppSingleton.last_activity = self


class ChatActivity(Activity):
    """LEAK: registers a listener but forgets to unregister it."""
    def on_create(self):
        BUS.register(self.on_event)


class GalleryActivity(Activity):
    """LEAK: puts itself in a global cache."""
    def on_create(self):
        IMAGE_CACHE["gallery_screen"] = self


class DownloadActivity(Activity):
    """LEAK: a background task that never finishes keeps a reference to it."""
    def on_create(self):
        PENDING_TASKS.append(BackgroundTask(self))


class SettingsActivity(Activity):
    """NO LEAK: registers a listener and correctly unregisters it."""
    def on_create(self):
        BUS.register(self.on_event)

    def on_destroy(self):
        BUS.unregister(self.on_event)


class AboutActivity(Activity):
    """NO LEAK: does not hand out any references."""
    pass
