#!/usr/bin/env python3
"""Settings > Video: the voice, speaking speed, burned-in captions and the optional FLUX illustrations."""

import json
import os
import sys

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db
import voiceover

SETTINGS_KEY = "video_settings"
DEFAULTS = {
    "voice": voiceover.DEFAULT_VOICE,
    "speed": voiceover.DEFAULT_SPEED,
    "captions": True,
    "flux": False,
}


def _clean_voice(value):
    try:
        return voiceover.clean_voice(value)
    except voiceover.VoiceError as exc:
        raise ValueError(exc.message) from exc


def _clean_speed(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Speed must be a number from {voiceover.MIN_SPEED} to {voiceover.MAX_SPEED}.")
    if not voiceover.MIN_SPEED <= float(value) <= voiceover.MAX_SPEED:
        raise ValueError(f"Speed must be a number from {voiceover.MIN_SPEED} to {voiceover.MAX_SPEED}.")
    return round(float(value), 2)


def _clean_bool(value):
    if not isinstance(value, bool):
        raise ValueError("This setting must be on or off.")
    return value


_CLEANERS = {"voice": _clean_voice, "speed": _clean_speed, "captions": _clean_bool, "flux": _clean_bool}


def _stored():
    try:
        raw = db.get_meta(SETTINGS_KEY)
        value = json.loads(raw) if raw else {}
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def get_settings():
    """The video settings. A damaged stored value falls back to the default for that field."""
    stored = _stored()
    out = {}
    for key, clean in _CLEANERS.items():
        try:
            out[key] = clean(stored[key]) if key in stored else DEFAULTS[key]
        except ValueError:
            out[key] = DEFAULTS[key]
    return out


def update_settings(changes):
    """Change settings. Every value is checked first, so a refused change changes nothing."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("Nothing to change.")
    unknown = [k for k in changes if k not in _CLEANERS]
    if unknown:
        raise ValueError("That setting cannot be changed.")
    clean = {key: _CLEANERS[key](value) for key, value in changes.items()}
    merged = get_settings()
    merged.update(clean)
    db.set_meta(SETTINGS_KEY, json.dumps(merged, sort_keys=True))
    return get_settings()
