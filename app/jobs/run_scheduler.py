"""Dedicated scheduler process:  python -m app.jobs.run_scheduler"""
import logging
import signal
import threading

from app.jobs.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    start_scheduler()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait()
    stop_scheduler()
