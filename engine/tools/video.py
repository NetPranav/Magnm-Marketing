#!/usr/bin/env python3
"""Video producer tool: render_video turns a saved video script into finished MP4s, locally."""

import os
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_TOOLS_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

from tools import tool


@tool(
    name="render_video",
    description=(
        "Render a saved video script (content_id) into finished MP4s with a local voice-over, burned-in captions and "
        "a poster, in 9:16 and 16:9. Runs on this computer with no AI call; takes about a minute. The files are "
        "attached to the script. Only one video renders at a time."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "content_id": {"type": "string", "description": "The saved video script's content id."},
            "aspects": {
                "type": "array",
                "items": {"type": "string", "enum": ["9:16", "16:9"]},
                "description": "Sizes to render (default both).",
            },
            "voice": {"type": "boolean", "description": "Speak the voice-over (default true). False renders without a voice."},
        },
        "required": ["content_id"],
    },
    side_effects=True,
)
def render_video(ctx, content_id, aspects=None, voice=None):
    import video_render

    try:
        res = video_render.render_video(
            content_id,
            aspects=aspects,
            voice=True if voice is None else bool(voice),
            cancel=getattr(ctx, "is_cancelled", None),
            wait=False,
        )
    except video_render.RenderError as exc:
        return {"error": exc.message, "code": exc.code}
    return {
        "content_id": res["content_id"],
        "aspects": res["aspects"],
        "length_s": res["total_s"],
        "voice": res["voice"]["used"],
        "render_seconds": res["render_seconds"],
        "files": [f["path"] for f in res["files"]],
    }
