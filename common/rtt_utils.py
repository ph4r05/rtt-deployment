import os
import time
import logging
import signal
from filelock import Timeout, FileLock


logger = logging.getLogger(__name__)
EXPIRE_SECONDS_DEFAULT = 60 * 60 * 24


def try_remove(path):
    try:
        os.unlink(path)
    except:
        pass


def clean_log_files(log_root_dir, expire_seconds=EXPIRE_SECONDS_DEFAULT):
    cur_time = time.time()
    num_removed = 0
    size_removed = 0

    for root, dirs, files in os.walk(log_root_dir):
        for file in files:
            full_path = os.path.join(root, file)
            if not os.path.isfile(full_path):
                continue

            try:
                stat = os.stat(full_path)
                mtime = stat.st_mtime
                tdiff = cur_time - mtime
                if tdiff > expire_seconds:
                    logger.debug('Deleting expired file: %s, timediff: %s (%.2f h)' % (full_path, tdiff, tdiff/60/60))
                    os.remove(full_path)
                    num_removed += 1
                    size_removed += stat.st_size

            except Exception as e:
                logger.warning('Exception when analyzing %s' % full_path, e)

    return num_removed, size_removed


def get_associated_files(path):
    return [path + '.lock', path + '.lock.2', path + '.downloaded']


class FileLockerError(Exception):
    pass


class FileLocker(object):
    def __init__(self, path, acquire_timeout=60*60, lock_timeout=10, expire=120):
        self.path = path
        self.mlock_path = self.path + '.2'

        self.expire = expire
        self.lock_timeout = lock_timeout
        self.acquire_timeout = acquire_timeout

        self.primary_locker = FileLock(self.path, self.lock_timeout)

    def touch(self):
        try:
            with open(self.mlock_path, 'a'):
                try:
                    os.utime(self.mlock_path, None)  # => Set current time anyway
                except OSError:
                    pass
        except Exception as e:
            logger.error('Error touch the file', e)

    def mtime(self):
        try:
            return os.stat(self.mlock_path).st_mtime
        except:
            return 0

    def is_expired(self):
        mtime = self.mtime()
        return time.time() - mtime > self.expire

    def delete_timing(self):
        try:
            os.unlink(self.mlock_path)
        except:
            pass

    def release(self):
        self.delete_timing()
        self.primary_locker.release()

    def force_release(self):
        self.primary_locker.release(force=True)

    def acquire_try_once(self, _depth=0):
        if _depth > 0:
            logger.info("Acquire_try_once depth=%s" % _depth)
        if _depth > 2:
            return False

        # Try normal acquisition on the primary file
        try:
            self.primary_locker.acquire()
            self.touch()
            return True

        except Timeout:
            # Lock could not be acquired, check whether the timing file, whether
            # the locker is still alive. If not, force release and reacquire
            # to prevent starving on the deadly-locked resource.
            if self.is_expired():
                # Expired, release and force-acquire
                logger.info("Acquire timeout, timing file is expired, reacquire")
                self.force_release()

                # Try to re-acquire recursively
                return self.acquire_try_once(_depth + 1)

            else:
                return False

    def acquire(self, timeout=None):
        time_started = time.time()
        while True:
            res = self.acquire_try_once()
            if res:
                return True

            time_now = time.time()
            if timeout is not None and timeout < 0:
                continue
            if timeout is not None and timeout == 0:
                logger.info("Timeout, immediate")
                raise Timeout
            if timeout is None and time_now - time_started > self.acquire_timeout:
                logger.info("Timeout, self.acquire_timeout")
                raise Timeout
            if timeout is not None and timeout > 0 and time_now - time_started > timeout:
                logger.info("Timeout, defined")
                raise Timeout

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        self.release()


class GracefulKiller:
    def __init__(self):
        self.kill_now = False
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, signum, frame):
        self.kill_now = True

    def is_killed(self):
        return self.kill_now
