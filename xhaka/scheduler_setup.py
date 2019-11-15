import os
import fcntl
import atexit

__all__ = ['is_predefined_crontask_lck']

_crontask_lck = open(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 'predefined_cron_tasks.lck'),
    "wb"
)

try:
    fcntl.flock(_crontask_lck, fcntl.LOCK_EX | fcntl.LOCK_NB)
    is_predefined_crontask_lck = True
except OSError:
    is_predefined_crontask_lck = False


def _unlock():
    fcntl.flock(_crontask_lck, fcntl.LOCK_UN)
    _crontask_lck.close()


atexit.register(_unlock)
