#!/usr/bin/env python3
"""Local text-to-speech for video voice-over, with Kokoro (kokoro-onnx). Nothing here uses the network except the
one-time model download the person starts from Settings > Video.

kokoro-onnx is imported lazily: the engine and its tests work without it, and a missing voice or library is a plain
error (VoiceError) that the UI can show with the Download button.
"""

import array
import hashlib
import os
import sys
import threading
import urllib.request
import wave

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import paths

DEFAULT_VOICE = "am_michael"
DEFAULT_SPEED = 1.0
MIN_SPEED = 0.7
MAX_SPEED = 1.3
SAMPLE_RATE = 24000
SAMPLE_TEXT = "This is how your video will sound. Short, clear and calm."

_RELEASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MODEL_FILES = {
    "kokoro-v1.0.int8.onnx": {
        "sha256": "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb",
        "url": _RELEASE_URL + "kokoro-v1.0.int8.onnx",
        "size_bytes": 92361271,
    },
    "voices-v1.0.bin": {
        "sha256": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
        "url": _RELEASE_URL + "voices-v1.0.bin",
        "size_bytes": 28214398,
    },
}

# English voices of Kokoro v1.0. The first letter of the id is the accent (a = US, b = UK), the second the gender.
VOICES = [
    {"id": "am_michael", "label": "Michael (US, male)", "lang": "en-us"},
    {"id": "am_adam", "label": "Adam (US, male)", "lang": "en-us"},
    {"id": "am_eric", "label": "Eric (US, male)", "lang": "en-us"},
    {"id": "am_liam", "label": "Liam (US, male)", "lang": "en-us"},
    {"id": "af_heart", "label": "Heart (US, female)", "lang": "en-us"},
    {"id": "af_bella", "label": "Bella (US, female)", "lang": "en-us"},
    {"id": "af_nicole", "label": "Nicole (US, female)", "lang": "en-us"},
    {"id": "af_sarah", "label": "Sarah (US, female)", "lang": "en-us"},
    {"id": "bf_emma", "label": "Emma (UK, female)", "lang": "en-gb"},
    {"id": "bm_george", "label": "George (UK, male)", "lang": "en-gb"},
    {"id": "bm_daniel", "label": "Daniel (UK, male)", "lang": "en-gb"},
]
_VOICE_LANG = {v["id"]: v["lang"] for v in VOICES}

_engine_lock = threading.Lock()
_engine = None
_engine_key = None


class VoiceError(Exception):
    """A voice problem in plain words. `code` tells the UI what to offer (no_voice shows the Download button)."""

    def __init__(self, message, code="voice_failed"):
        super().__init__(message)
        self.message = str(message)
        self.code = code


def models_dir():
    """The folder holding the Kokoro model files: MAGNM_KOKORO_DIR if set, else <data>/models/kokoro."""
    custom = os.environ.get("MAGNM_KOKORO_DIR", "").strip()
    if custom:
        return os.path.abspath(os.path.expanduser(custom))
    return paths.data("models", "kokoro")


def cache_dir():
    """Where voice samples and per-scene clips are cached."""
    return paths.data("cache", "voice")


def _model_path(name):
    return os.path.join(models_dir(), name)


def missing_files():
    """Names of model files that are not in the models folder yet."""
    return [name for name in MODEL_FILES if not os.path.isfile(_model_path(name))]


def library_available():
    """True if kokoro-onnx can be imported in this build."""
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        return False
    return True


def status():
    """What Settings shows: is the voice installed, where, which voices exist."""
    missing = missing_files()
    total = sum(info["size_bytes"] for info in MODEL_FILES.values())
    return {
        "installed": not missing,
        "missing": missing,
        "dir": models_dir(),
        "size_mb": round(total / (1024 * 1024)),
        "library": library_available(),
        "voices": [dict(v) for v in VOICES],
        "default_voice": DEFAULT_VOICE,
        "default_speed": DEFAULT_SPEED,
        "min_speed": MIN_SPEED,
        "max_speed": MAX_SPEED,
    }


def clean_speed(value):
    """A speaking speed inside the safe range; anything unreadable is the default."""
    try:
        speed = float(value)
    except (TypeError, ValueError):
        return DEFAULT_SPEED
    if speed != speed:  # NaN
        return DEFAULT_SPEED
    return round(max(MIN_SPEED, min(MAX_SPEED, speed)), 2)


def clean_voice(value):
    """A known voice id, or VoiceError."""
    voice = str(value or DEFAULT_VOICE).strip()
    if voice not in _VOICE_LANG:
        raise VoiceError(f"The voice '{voice[:40]}' is not one of the available voices. Pick another in Settings > Video.", "bad_voice")
    return voice


def require_voice():
    """Raise the plain 'no voice installed' error when the model files are missing."""
    if missing_files():
        raise VoiceError(
            "The voice is not installed yet. Open Settings > Video and press Download voice (about "
            f"{status()['size_mb']} MB), or render the video without a voice.",
            "no_voice",
        )


def reset_engine():
    """Forget the loaded model (tests, and after a download)."""
    global _engine, _engine_key
    with _engine_lock:
        _engine = None
        _engine_key = None


def _get_engine():
    """The loaded Kokoro model, loaded once and shared."""
    global _engine, _engine_key
    require_voice()
    key = models_dir()
    with _engine_lock:
        if _engine is not None and _engine_key == key:
            return _engine
        try:
            from kokoro_onnx import Kokoro
        except ImportError as exc:
            raise VoiceError(
                "The voice library is not part of this build, so voice-over is not available. "
                "Render the video without a voice, or update Magnm Studio.",
                "no_library",
            ) from exc
        try:
            _engine = Kokoro(_model_path("kokoro-v1.0.int8.onnx"), _model_path("voices-v1.0.bin"))
        except Exception as exc:
            raise VoiceError(
                "The voice files could not be loaded. Press Download voice in Settings > Video to fetch them again.",
                "bad_model",
            ) from exc
        _engine_key = key
        return _engine


def write_wav(path, samples, sample_rate=SAMPLE_RATE):
    """Write mono 16-bit PCM from float samples in [-1, 1], using only the standard library."""
    if hasattr(samples, "astype") and hasattr(samples, "clip"):
        raw = (samples.clip(-1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    else:
        pcm = array.array("h", (int(max(-1.0, min(1.0, float(x))) * 32767) for x in samples))
        if sys.byteorder == "big":
            pcm.byteswap()
        raw = pcm.tobytes()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(int(sample_rate))
        wav.writeframes(raw)


def wav_duration(path):
    """Length of a WAV file in seconds."""
    with wave.open(path, "rb") as wav:
        return wav.getnframes() / float(wav.getframerate())


def synthesize(text, wav_path, voice=None, speed=None, engine=None):
    """Speak `text` into `wav_path`. Returns {"duration_s", "sample_rate", "path"}."""
    text = " ".join(str(text or "").split())
    if not text:
        raise VoiceError("There is no text to speak.", "empty_text")
    voice = clean_voice(voice)
    speed = clean_speed(speed)
    model = engine if engine is not None else _get_engine()
    try:
        samples, rate = model.create(text, voice=voice, speed=speed, lang=_VOICE_LANG[voice])
    except VoiceError:
        raise
    except Exception as exc:
        raise VoiceError(f"The voice could not speak this text: {str(exc)[:160]}", "tts_failed") from exc
    write_wav(wav_path, samples, rate)
    return {"duration_s": wav_duration(wav_path), "sample_rate": int(rate), "path": wav_path}


def sample(voice=None, speed=None):
    """A short WAV sample of a voice for the Play sample button. Returns its path (cached per voice and speed)."""
    voice = clean_voice(voice)
    speed = clean_speed(speed)
    path = os.path.join(cache_dir(), f"sample-{voice}-{speed}.wav")
    if not os.path.isfile(path):
        synthesize(SAMPLE_TEXT, path, voice=voice, speed=speed)
    return path


def write_timeline_track(out_path, clips, total_s, sample_rate=SAMPLE_RATE):
    """One WAV as long as the video: each clip at its start second (clips = [(start_s, wav_path)]), silence between."""
    frames = int(round(float(total_s) * sample_rate))
    track = bytearray(frames * 2)
    for start_s, clip_path in clips:
        with wave.open(clip_path, "rb") as wav:
            if wav.getframerate() != sample_rate or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                raise VoiceError("A voice clip has an unexpected audio format.", "bad_clip")
            raw = wav.readframes(wav.getnframes())
        offset = int(round(float(start_s) * sample_rate)) * 2
        end = min(len(track), offset + len(raw))
        if offset < end:
            track[offset:end] = raw[: end - offset]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with wave.open(out_path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(track))
    return out_path


def _default_opener(url):
    req = urllib.request.Request(url, headers={"User-Agent": "magnm-studio-voice-download"})
    return urllib.request.urlopen(req, timeout=30)


def download(progress=None, cancel=None, opener=None, only=None):
    """Download the missing model files into models_dir(), checking each against its SHA-256.

    A file that does not match is deleted and the error says so. progress(percent, message) is called as bytes
    arrive; cancel() returning True stops the download and removes the partial file.
    """
    names = [n for n in (only or MODEL_FILES) if n in MODEL_FILES]
    todo = [n for n in names if not os.path.isfile(_model_path(n))]
    open_url = opener or _default_opener
    total = sum(MODEL_FILES[n]["size_bytes"] for n in todo) or 1
    done_before = 0
    folder = models_dir()
    if todo:
        os.makedirs(folder, exist_ok=True)
    if progress:
        progress(0, "Starting the voice download")
    for name in todo:
        info = MODEL_FILES[name]
        final = _model_path(name)
        part = final + ".part"
        digest = hashlib.sha256()
        got = 0
        try:
            with open_url(info["url"]) as resp, open(part, "wb") as out:
                while True:
                    if cancel is not None and cancel():
                        raise VoiceError("The voice download was cancelled.", "cancelled")
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    got += len(chunk)
                    if progress:
                        pct = int(min(99, (done_before + got) * 100 / total))
                        progress(pct, f"Downloading the voice ({pct}%)")
        except VoiceError:
            _remove(part)
            raise
        except OSError as exc:
            _remove(part)
            raise VoiceError(
                "The voice could not be downloaded. Check your internet connection and try again "
                f"({str(exc)[:120]}).",
                "download_failed",
            ) from exc
        if digest.hexdigest() != info["sha256"]:
            _remove(part)
            raise VoiceError(
                f"The downloaded file {name} did not match its checksum, so it was deleted. Try the download again.",
                "bad_checksum",
            )
        os.replace(part, final)
        done_before += got
    reset_engine()
    if progress:
        progress(100, "The voice is ready")
    return status()


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
