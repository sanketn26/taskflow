
from .process_executor import ProcessExecutor
from .thread_executor import ThreadExecutor
from .thread_process_executor import ThreadProcessExecutor

_EXECUTOR_MAP = {
    "thread": ThreadExecutor,
    "thread+process": ThreadProcessExecutor,
    "greenlet": None,  # Placeholder for greenlet executor
    "greenlet+process": None,  # Placeholder for greenlet+process executor
}

def get_executor(kind="thread", max_workers=None, max_processes=None):
    kind = kind.lower()
    if kind not in _EXECUTOR_MAP:
        raise ValueError(f"Unknown executor kind: {kind}")
    if kind == "greenlet":
        from .gevent_executor import GeventExecutor
        return GeventExecutor(max_workers=max_workers)
    elif kind == "greenlet+process":
        from .gevent_process_executor import GeventProcessExecutor
        return GeventProcessExecutor(max_workers=max_workers, max_processes=max_processes)
    ExecutorClass = _EXECUTOR_MAP[kind]
    if kind == "thread+process":
        return ExecutorClass(max_workers=max_workers, max_processes=max_processes)
    else:
        return ExecutorClass(max_workers=max_workers)
