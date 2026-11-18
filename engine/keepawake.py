#!/usr/bin/env python3
"""Cross-platform sleep inhibition helper while background jobs run."""

import contextlib
import os
import platform
import shutil
import subprocess
import sys


@contextlib.contextmanager
def inhibit():
    """Keep the system awake while inside the context, logging and ignoring any failure."""
    proc = None
    win_active = False
    system = platform.system().lower()

    try:
        if system == "linux":
            bin_path = shutil.which("systemd-inhibit")
            if bin_path:
                proc = subprocess.Popen(
                    [
                        bin_path,
                        "--what=idle:sleep",
                        "--who=Magnm",
                        "--why=Magnm job running",
                        "--mode=block",
                        "sleep",
                        "infinity",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        elif system == "darwin":
            bin_path = shutil.which("caffeinate")
            if bin_path:
                proc = subprocess.Popen(
                    [bin_path, "-i", "-w", str(os.getpid())],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        elif system == "windows" or os.name == "nt":
            import ctypes

            es_continuous = 0x80000000
            es_system_required = 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)
            win_active = True
    except Exception as exc:
        print(f"[keepawake] Failed to acquire sleep inhibitor: {exc}", file=sys.stderr)
        proc = None
        win_active = False

    try:
        yield
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception as exc:
                    print(f"[keepawake] Failed to stop sleep inhibitor: {exc}", file=sys.stderr)
        if win_active:
            try:
                import ctypes

                es_continuous = 0x80000000
                ctypes.windll.kernel32.SetThreadExecutionState(es_continuous)
            except Exception as exc:
                print(f"[keepawake] Failed to clear execution state: {exc}", file=sys.stderr)
