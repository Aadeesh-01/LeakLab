"""
Feature 2: leak trace analysis.

Starting from the GC roots, we do a Breadth-First Search over strong references
until we reach the leaked object. BFS gives the SHORTEST reference chain, which is
the chain that prevents the object from being garbage collected.
"""
import types
from collections import deque

# Objects of these types cannot hold references to our app objects, so we skip them.
SKIP_TYPES = (str, bytes, bytearray, int, float, bool, type(None),
              types.ModuleType, types.FunctionType, types.BuiltinFunctionType)


def children(obj):
    """Yield (label, child) for every strong reference held by obj."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield f"[{key!r}]", value
    elif isinstance(obj, (list, tuple, set)):
        for i, value in enumerate(obj):
            yield f"[{i}]", value
    elif isinstance(obj, types.MethodType):
        yield f".__self__  (owner of bound method {obj.__name__})", obj.__self__
    elif isinstance(obj, type):
        for name, value in vars(obj).items():
            if not name.startswith("__"):
                yield f".{name}  (static field)", value
    elif hasattr(obj, "__dict__"):
        for name, value in vars(obj).items():
            if not name.startswith("__"):
                yield f".{name}", value


def find_leak_trace(target, roots):
    """Return the path [(label, obj), ...] from a GC root to target, or None.

    roots: dict {root_name: root_object}
    """
    parent = {}    # id(obj) -> (id(parent_obj), label)
    objects = {}   # id(obj) -> obj
    queue = deque()

    for name, root in roots.items():
        parent[id(root)] = (None, f"GC Root: {name}")
        objects[id(root)] = root
        queue.append(root)

    while queue:
        obj = queue.popleft()
        if obj is target:
            # Walk back through the parents to build the path.
            path = []
            current = id(obj)
            while current is not None:
                parent_id, label = parent[current]
                path.append((label, objects[current]))
                current = parent_id
            return list(reversed(path))

        for label, child in children(obj):
            if isinstance(child, SKIP_TYPES) or id(child) in parent:
                continue
            parent[id(child)] = (id(obj), label)
            objects[id(child)] = child
            queue.append(child)

    return None


def describe(obj):
    if isinstance(obj, types.ModuleType):
        return f"module '{obj.__name__}'"
    if isinstance(obj, type):
        return f"class {obj.__name__}"
    if isinstance(obj, (list, dict, tuple, set)):
        return f"{type(obj).__name__} ({len(obj)} items)"
    if isinstance(obj, types.MethodType):
        return f"bound method {type(obj.__self__).__name__}.{obj.__name__}"
    return f"{type(obj).__name__} instance"


def format_trace(path, reason):
    lines = [f"  {path[0][0]}  ->  {describe(path[0][1])}"]
    for i, (label, obj) in enumerate(path[1:], start=1):
        lines.append("     |")
        lines.append(f"     |  references {label}")
        lines.append("     v")
        text = f"  {describe(obj)}"
        if i == len(path) - 1:
            text += f"   <-- LEAKING ({reason})"
        lines.append(text)
    return "\n".join(lines)
