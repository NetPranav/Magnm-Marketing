#!/usr/bin/env python3
"""Social research tool for agents: creates an awaiting_start run with approval hash; never starts it."""

import os
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_TOOLS_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

from social import SocialError, service as social_service
from tools import tool


@tool(
    name="social_research",
    description=(
        "Request a read-only logged-in social research run for an opted-in platform "
        "(instagram, x, or linkedin) using a fixed recipe (profile_posts, search, post_replies, trending). "
        "Creates the run in 'awaiting_start' status and returns immediately; "
        "a human must press Start in the app. Continue with other research sources while pending."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "platform": {
                "type": "string",
                "enum": ["instagram", "x", "linkedin"],
                "description": "Social platform to research.",
            },
            "recipe": {
                "type": "string",
                "enum": ["profile_posts", "search", "post_replies", "trending"],
                "description": "Fixed read-only research recipe.",
            },
            "argument": {
                "type": "string",
                "description": "Handle (for profile_posts), query (search), exact post URL (post_replies), or topic (trending).",
            },
        },
        "required": ["platform", "recipe", "argument"],
    },
    side_effects=True,
)
def social_research(ctx, platform, recipe, argument):
    if ctx and callable(getattr(ctx, "is_cancelled", None)) and ctx.is_cancelled():
        return {"error": "session cancelled"}

    try:
        pending = social_service.create_run_request(
            platform=platform,
            recipe=recipe,
            argument=argument,
            requested_by=ctx.agent_id if ctx else None,
            session_id=ctx.session_id if ctx else None,
        )
    except SocialError as exc:
        return {"error": exc.message, "code": exc.code}

    return {
        "run_id": pending["id"],
        "platform": pending["platform"],
        "recipe": pending["recipe"],
        "argument": pending["argument"],
        "status": pending["status"],
        "pending": True,
        "message": (
            f"Social research run for {pending['platform']} is pending user Start "
            "(status 'awaiting_start'). Continue with other sources."
        ),
    }
