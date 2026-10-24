"""
Feature 1: automatic detection of objects that stay in memory after their lifecycle ends.

We keep only a WEAK reference to each destroyed object. A weak reference does not keep
the object alive, so after garbage collection:
    ref() is None      -> object was freed (good)
    ref() is not None  -> someone still holds a strong reference (LEAK)
"""
import gc
import weakref


class WatchedObject:
    def __init__(self, obj, description):
        self.ref = weakref.ref(obj)
        self.class_name = type(obj).__name__
        self.description = description


class ObjectWatcher:
    def __init__(self):
        self.watched = []

    def watch(self, obj, description=""):
        self.watched.append(WatchedObject(obj, description))

    def check_retained(self):
        """Run the GC and return the watched objects that are still alive."""
        gc.collect()
        retained = [w for w in self.watched if w.ref() is not None]
        self.watched = []
        return retained
