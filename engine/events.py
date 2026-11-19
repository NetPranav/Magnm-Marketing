#!/usr/bin/env python3
"""In-process pub/sub event bus for Server-Sent Events (SSE) streaming."""

import queue
import threading

MAX_QUEUE_SIZE = 500

_lock = threading.Lock()
_subscribers = set()


def subscribe():
    """Register a new bounded subscriber queue (max 500 items) and return it."""
    q = queue.Queue(maxsize=MAX_QUEUE_SIZE)
    with _lock:
        _subscribers.add(q)
    return q


def unsubscribe(q):
    """Remove a subscriber queue from active listeners."""
    if q is None:
        return
    with _lock:
        _subscribers.discard(q)


def publish(event_type, data):
    """Broadcast an event to all active subscriber queues, dropping oldest on overflow."""
    payload = {"type": str(event_type), "data": data}
    with _lock:
        targets = list(_subscribers)

    for q in targets:
        try:
            q.put_nowait(payload)
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass
