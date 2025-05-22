from .thread_executor import ThreadExecutor
from .process_executor import ProcessExecutor
from .gevent_executor import GeventExecutor

_EXECUTOR_MAP = {
    'thread': ThreadExecutor,
    'process': ProcessExecutor,
    'gevent': GeventExecutor,
}

def get_executor(kind='thread', max_workers=None):
    kind = kind.lower()
    if kind not in _EXECUTOR_MAP:
        raise ValueError(f"Unknown executor kind: {kind}")
    return _EXECUTOR_MAP[kind](max_workers=max_workers)
