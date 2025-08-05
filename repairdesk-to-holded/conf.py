import bridge
from datetime import timedelta, datetime
import threading
import json
from server import shared_dict, shared_lock


TIME_BETWEEN_LOOPS = 60
exit_event = threading.Event()


def run_sync(exit_event):
    # global shared_lock, shared_dict

    while True:
        start = datetime.now()

        with shared_lock:
            shared_dict["state"] = "running"

        bridge.sync_new_invoices()
        bridge.sync_unpaid_invoices()

        end = datetime.now()

        with shared_lock:
            shared_dict["last_run"] = (end - start).total_seconds()
            shared_dict["state"] = "waiting for next loop"
            shared_dict["next_loop"] = (end + timedelta(seconds=TIME_BETWEEN_LOOPS)).timestamp()

        if exit_event.wait(timeout=TIME_BETWEEN_LOOPS):
            break


def on_starting(server):
    global exit_event

    sync_worker = threading.Thread(target=run_sync, args=(exit_event,))
    sync_worker.start()


def on_exit(server):
    global exit_event

    exit_event.set()


def post_worker_init(worker):
    import atexit
    from multiprocessing.util import _exit_function

    atexit.unregister(_exit_function)
    worker.log.info("worker post_worker_init done, (pid: {})".format(worker.pid))
