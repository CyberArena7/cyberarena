import bridge
from datetime import timedelta, datetime
import threading
import schedule
import logging
import json
from server import shared_dict, shared_lock


TIME_BETWEEN_LOOPS = 60
exit_event = threading.Event()
logger = logging.getLogger(__name__)


def run_sync(exit_event: threading.Event):
    # New invoices only
    schedule.every(1).minutes.do(bridge.sync_new_invoices, exit_event=exit_event)

    # Every 30 minuts check the day
    schedule.every(30).minutes.do(
        bridge.sync_last_invoices,
        exit_event=exit_event,
        time_before=timedelta(seconds=0),  # Seconds = 0 because RepairDesk truncates to current day
    )

    # Weekdays daily job
    schedule.every().monday.at("08:00").do(
        bridge.sync_last_invoices, exit_event=exit_event, time_before=timedelta(weeks=1)
    )
    schedule.every().tuesday.at("08:00").do(
        bridge.sync_last_invoices, exit_event=exit_event, time_before=timedelta(weeks=1)
    )
    schedule.every().wednesday.at("08:00").do(
        bridge.sync_last_invoices, exit_event=exit_event, time_before=timedelta(weeks=1)
    )
    schedule.every().thursday.at("08:00").do(
        bridge.sync_last_invoices, exit_event=exit_event, time_before=timedelta(weeks=1)
    )
    schedule.every().friday.at("08:00").do(
        bridge.sync_last_invoices, exit_event=exit_event, time_before=timedelta(weeks=1)
    )
    schedule.every().saturday.at("08:00").do(
        bridge.sync_last_invoices, exit_event=exit_event, time_before=timedelta(weeks=1)
    )

    # Sunday check 4 months
    schedule.every().sunday.at("08:00").do(
        bridge.sync_last_invoices, exit_event=exit_event, time_before=timedelta(days=30 * 4)
    )

    while True:
        start = datetime.now()

        with shared_lock:
            shared_dict["state"] = "running"

        try:
            schedule.run_pending()
        except Exception as e:
            logger.error("{}".format(e))
            with shared_lock:
                shared_dict["state"] = "failed"

        end = datetime.now()

        with shared_lock:
            shared_dict["last_run"] = (end - start).total_seconds()
            shared_dict["state"] = "waiting for next loop"
            shared_dict["next_loop"] = (
                end + timedelta(seconds=schedule.idle_seconds())
            ).timestamp()

        if exit_event.wait(timeout=max(0, schedule.idle_seconds())):
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
