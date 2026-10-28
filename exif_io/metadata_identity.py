"""Worker-resolved path aliases with an I/O-free lookup for Qt consumers."""
from collections import OrderedDict
import os
import threading

_paths = OrderedDict()
_lock = threading.RLock()
_LIMIT = 40000


def lexical_key(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def path_key(path):
    key = lexical_key(path)
    with _lock:
        return _paths.get(key, key)


def cached_path(path):
    """Preserve the caller's spelling until a worker has resolved its alias."""
    with _lock:
        return _paths.get(lexical_key(path), os.fspath(path))


def register_alias(original, canonical):
    original, canonical = lexical_key(original), lexical_key(canonical)
    with _lock:
        _paths[original] = canonical
        _paths[canonical] = canonical
        _paths.move_to_end(original)
        while len(_paths) > _LIMIT:
            _paths.popitem(last=False)


def resolve_path(path, *, refresh=False):
    """Worker only: resolve Windows 8.3 names/junctions and POSIX aliases."""
    original = lexical_key(path)
    with _lock:
        known = _paths.get(original)
    if known is not None and not refresh:
        return original, known
    canonical = lexical_key(os.path.realpath(os.path.abspath(os.fspath(path))))
    register_alias(original, canonical)
    return original, canonical
