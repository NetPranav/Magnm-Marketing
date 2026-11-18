#!/usr/bin/env python3
"""Pausable multi-step agent session runtime backed by SQLite, gateway, and tools."""

import base64
from datetime import datetime, timezone
import json
import os
import sys
import threading
import time
import uuid

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import ai_gateway
import db
import events
import paths
import product_rules
import tools as tools_pkg
from tools import ToolContext

MAX_STEPS = 10
WALL_CLOCK_LIMIT_S = 15 * 60


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _build_user_content(user_message, attachments=None):
    """Build string or multimodal content parts from user_message and optional attachments."""
    text = str(user_message or "")
    if not attachments:
        return text

    parts = [{"type": "text", "text": text}]
    has_media = False
    for item in attachments:
        if isinstance(item, dict):
            if item.get("type") == "image_url" and isinstance(item.get("image_url"), dict):
                parts.append(item)
                has_media = True
            elif item.get("url"):
                parts.append({"type": "image_url", "image_url": {"url": str(item["url"])}})
                has_media = True
            elif item.get("path") and os.path.isfile(item["path"]):
                encoded = _encode_image_part(item["path"])
                if encoded:
                    parts.append(encoded)
                    has_media = True
        elif isinstance(item, str):
            if item.startswith("data:image/") or item.startswith("http://") or item.startswith("https://"):
                parts.append({"type": "image_url", "image_url": {"url": item}})
                has_media = True
            elif os.path.isfile(item):
                encoded = _encode_image_part(item)
                if encoded:
                    parts.append(encoded)
                    has_media = True

    return parts if has_media else text


def _encode_image_part(file_path):
    """Read an image file into an OpenAI image_url data-URI content part, or None."""
    low = file_path.lower()
    if low.endswith(".png"):
        mime = "image/png"
    elif low.endswith((".jpg", ".jpeg")):
        mime = "image/jpeg"
    elif low.endswith(".webp"):
        mime = "image/webp"
    elif low.endswith(".gif"):
        mime = "image/gif"
    else:
        return None
    try:
        with open(file_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
    except OSError:
        return None


class AgentRuntime:
    """Manages agent session persistence, tool-call loops, pausing, and cancellation."""

    def __init__(self, max_steps=MAX_STEPS, wall_clock_limit_s=WALL_CLOCK_LIMIT_S):
        self.max_steps = int(max_steps)
        self.wall_clock_limit_s = float(wall_clock_limit_s)
        self._lock = threading.Lock()
        self._cancel_events = {}

    def _get_cancel_event(self, session_id):
        with self._lock:
            ev = self._cancel_events.get(session_id)
            if ev is None:
                ev = threading.Event()
                self._cancel_events[session_id] = ev
            return ev

    def _clear_cancel_event(self, session_id):
        with self._lock:
            self._cancel_events.pop(session_id, None)

    def create_session(
        self,
        agent_id,
        user_message,
        *,
        system_prompt,
        allowed_tools,
        attachments=None,
        parent_session_id=None,
        depth=0,
        confidential=False,
        title=None,
    ):
        """Persist a new session in 'running' state and return its dict without starting the loop."""
        session_id = f"sess_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": str(system_prompt)})
        messages.append({"role": "user", "content": _build_user_content(user_message, attachments)})

        is_conf = bool(confidential)
        if not is_conf and user_message:
            try:
                rules_path = paths.data("config", "product_rules.json")
                if not os.path.exists(rules_path):
                    rules_path = paths.resource("config", "product_rules.json")
                if os.path.exists(rules_path) and product_rules.mentions_confidential(str(user_message), path=rules_path):
                    is_conf = True
            except Exception:
                pass

        now = _now_iso()
        session = db.create_agent_session(
            {
                "id": session_id,
                "agent_id": str(agent_id),
                "status": "running",
                "messages": messages,
                "pending_question": None,
                "parent_session_id": parent_session_id,
                "depth": int(depth or 0),
                "title": title,
                "result_text": None,
                "error": None,
                "allowed_tools": list(allowed_tools or []),
                "confidential": is_conf,
                "steps": 0,
                "created_at": now,
                "updated_at": now,
            }
        )
        self._get_cancel_event(session_id)
        return session

    def start_session(
        self,
        agent_id,
        user_message,
        *,
        system_prompt,
        allowed_tools,
        attachments=None,
        parent_session_id=None,
        depth=0,
        confidential=False,
        title=None,
    ):
        """Create a new session and run the tool-calling loop synchronously in the calling thread."""
        session = self.create_session(
            agent_id,
            user_message,
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            attachments=attachments,
            parent_session_id=parent_session_id,
            depth=depth,
            confidential=confidential,
            title=title,
        )
        return self.run_session(session["id"])

    def prepare_resume(self, session_id, answer):
        """Append the user's answer for a waiting_user session and transition it back to running."""
        session = db.get_agent_session(session_id)
        if session is None:
            raise ValueError(f"Session '{session_id}' not found")
        if session["status"] != "waiting_user":
            raise ValueError(
                f"Session '{session_id}' is not waiting for user input (status={session['status']})"
            )

        pending = session.get("pending_question") or {}
        tool_call_id = pending.get("tool_call_id")
        if not tool_call_id:
            raise ValueError(f"Session '{session_id}' has no pending ask_user tool_call_id")

        messages = list(session.get("messages") or [])
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps({"answer": answer}, ensure_ascii=False),
            }
        )
        self._get_cancel_event(session_id).clear()
        return db.update_agent_session(
            session_id,
            status="running",
            messages=messages,
            pending_question=None,
            updated_at=_now_iso(),
        )

    def resume_session(self, session_id, answer):
        """Resume a waiting_user session synchronously with the user's answer."""
        self.prepare_resume(session_id, answer)
        return self.run_session(session_id)

    def cancel_session(self, session_id):
        """Request cancellation for a running session or immediately cancel a waiting_user session."""
        session = db.get_agent_session(session_id)
        if session is None:
            return None

        self._get_cancel_event(session_id).set()

        if session["status"] == "waiting_user":
            updated = db.update_agent_session(
                session_id,
                status="cancelled",
                pending_question=None,
                updated_at=_now_iso(),
            )
            events.publish(
                "agent_cancelled",
                {
                    "session_id": session_id,
                    "agent_id": updated["agent_id"],
                    "status": "cancelled",
                },
            )
            self._notify_session_finished(updated)
            return updated

        return session

    def _notify_session_finished(self, session):
        """Notify workflow engine when a session reaches a terminal state."""
        if not session:
            return
        try:
            import workflows
            workflows.on_session_finished(session)
        except Exception:
            pass

    def get_session(self, session_id):
        """Return a session dict by ID, or None if missing."""
        return db.get_agent_session(session_id)

    def list_sessions(self, limit=20, status=None):
        """Return recent agent sessions ordered newest-first."""
        return db.list_agent_sessions(limit=limit, status=status)

    def recover_stale_sessions(self):
        """Mark any sessions left in 'running' state after a restart as failed."""
        recovered = db.fail_stale_running_sessions(_now_iso(), error="engine restarted")
        for sess in recovered:
            events.publish(
                "agent_failed",
                {
                    "session_id": sess["id"],
                    "agent_id": sess["agent_id"],
                    "status": "failed",
                    "error": sess.get("error") or "engine restarted",
                },
            )
        return recovered

    def _mark_failed(self, session_id, agent_id, error_msg, error_code=None):
        updated = db.update_agent_session(
            session_id,
            status="failed",
            error=str(error_msg),
            pending_question=None,
            updated_at=_now_iso(),
        )
        if updated is not None and error_code:
            updated["error_code"] = error_code
        events.publish(
            "agent_failed",
            {
                "session_id": session_id,
                "agent_id": agent_id,
                "status": "failed",
                "error": str(error_msg),
            },
        )
        self._clear_cancel_event(session_id)
        self._notify_session_finished(updated)
        return updated

    def _mark_cancelled(self, session_id, agent_id):
        updated = db.update_agent_session(
            session_id,
            status="cancelled",
            pending_question=None,
            updated_at=_now_iso(),
        )
        events.publish(
            "agent_cancelled",
            {
                "session_id": session_id,
                "agent_id": agent_id,
                "status": "cancelled",
            },
        )
        self._clear_cancel_event(session_id)
        self._notify_session_finished(updated)
        return updated

    def run_session(self, session_id):
        """Execute the synchronous model/tool loop for an existing 'running' session."""
        session = db.get_agent_session(session_id)
        if session is None:
            raise ValueError(f"Session '{session_id}' not found")

        agent_id = session["agent_id"]
        allowed_tools = list(session.get("allowed_tools") or [])
        confidential = bool(session.get("confidential"))
        depth = int(session.get("depth") or 0)
        messages = list(session.get("messages") or [])
        steps = int(session.get("steps") or 0)

        cancel_ev = self._get_cancel_event(session_id)
        t_start = time.monotonic()

        while True:
            if cancel_ev.is_set():
                return self._mark_cancelled(session_id, agent_id)

            if (time.monotonic() - t_start) > self.wall_clock_limit_s:
                return self._mark_failed(session_id, agent_id, "session timed out (15 min limit)")

            if steps >= self.max_steps:
                return self._mark_failed(session_id, agent_id, "too many steps")

            fresh_sess = db.get_agent_session(session_id)
            if fresh_sess is not None:
                confidential = bool(fresh_sess.get("confidential"))

            tool_schemas = tools_pkg.schemas_for(allowed_tools)
            try:
                resp = ai_gateway.chat_with_tools(
                    messages=messages,
                    tools=tool_schemas or None,
                    agent_role=agent_id,
                    confidential=confidential,
                )
            except ai_gateway.AIAuthError as exc:
                return self._mark_failed(session_id, agent_id, str(exc), error_code="ai_auth_error")
            except ai_gateway.AINotConnected as exc:
                return self._mark_failed(session_id, agent_id, str(exc), error_code="ai_not_connected")
            except Exception as exc:
                return self._mark_failed(session_id, agent_id, str(exc))

            steps += 1
            text = resp.get("text") or ""
            tool_calls = resp.get("tool_calls") or []
            model_used = resp.get("model_used")
            provider = resp.get("provider")
            fallback_used = bool(resp.get("fallback_used"))

            if cancel_ev.is_set():
                return self._mark_cancelled(session_id, agent_id)

            if tool_calls:
                assistant_msg = {
                    "role": "assistant",
                    "content": text,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc.get("arguments") or {}, ensure_ascii=False),
                            },
                        }
                        for tc in tool_calls
                    ],
                }
                messages.append(assistant_msg)

                def _mark_confidential():
                    db.update_agent_session(session_id, confidential=True, updated_at=_now_iso())
                    ctx.confidential = True

                ctx = ToolContext(
                    session_id=session_id,
                    agent_id=agent_id,
                    user_id="local",
                    depth=depth,
                    confidential=confidential,
                    publish=events.publish,
                    mark_confidential=_mark_confidential,
                )

                executed_names = []
                for idx, tc in enumerate(tool_calls):
                    if cancel_ev.is_set():
                        # Keep valid OpenAI tool-call pairing before cancelling
                        assistant_msg["tool_calls"] = assistant_msg["tool_calls"][:idx]
                        if not assistant_msg["tool_calls"]:
                            messages.pop()
                        db.update_agent_session(
                            session_id,
                            messages=messages,
                            steps=steps,
                            updated_at=_now_iso(),
                        )
                        return self._mark_cancelled(session_id, agent_id)

                    result = tools_pkg.run_tool(
                        tc["name"],
                        ctx,
                        tc.get("arguments") or {},
                        allowed_tools,
                    )
                    executed_names.append(tc["name"])

                    if tc["name"] == "ask_user" and isinstance(result, dict) and "error" not in result:
                        assistant_msg["tool_calls"] = assistant_msg["tool_calls"][: idx + 1]
                        pending = {
                            "question": result["question"],
                            "options": result.get("options") or [],
                            "tool_call_id": tc["id"],
                        }
                        paused = db.update_agent_session(
                            session_id,
                            status="waiting_user",
                            messages=messages,
                            pending_question=pending,
                            steps=steps,
                            model_used=model_used,
                            provider=provider,
                            fallback_used=fallback_used,
                            updated_at=_now_iso(),
                        )
                        events.publish(
                            "agent_step",
                            {
                                "session_id": session_id,
                                "agent_id": agent_id,
                                "step": steps,
                                "tool_names": executed_names,
                                "text_preview": text[:200],
                            },
                        )
                        events.publish(
                            "agent_waiting",
                            {
                                "session_id": session_id,
                                "agent_id": agent_id,
                                "question": pending["question"],
                                "options": pending["options"],
                                "tool_call_id": pending["tool_call_id"],
                            },
                        )
                        return paused

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )

                db.update_agent_session(
                    session_id,
                    messages=messages,
                    steps=steps,
                    model_used=model_used,
                    provider=provider,
                    fallback_used=fallback_used,
                    updated_at=_now_iso(),
                )
                events.publish(
                    "agent_step",
                    {
                        "session_id": session_id,
                        "agent_id": agent_id,
                        "step": steps,
                        "tool_names": executed_names,
                        "text_preview": text[:200],
                    },
                )
                continue

            # Text response with no tool calls -> done
            messages.append({"role": "assistant", "content": text})
            done_sess = db.update_agent_session(
                session_id,
                status="done",
                messages=messages,
                result_text=text,
                pending_question=None,
                error=None,
                steps=steps,
                model_used=model_used,
                provider=provider,
                fallback_used=fallback_used,
                updated_at=_now_iso(),
            )
            events.publish(
                "agent_step",
                {
                    "session_id": session_id,
                    "agent_id": agent_id,
                    "step": steps,
                    "tool_names": [],
                    "text_preview": text[:200],
                },
            )
            events.publish(
                "agent_done",
                {
                    "session_id": session_id,
                    "agent_id": agent_id,
                    "status": "done",
                    "result_text": text[:200],
                },
            )
            self._clear_cancel_event(session_id)
            self._notify_session_finished(done_sess)
            return done_sess


_default_runtime = AgentRuntime()


def create_session(*args, **kwargs):
    return _default_runtime.create_session(*args, **kwargs)


def start_session(*args, **kwargs):
    return _default_runtime.start_session(*args, **kwargs)


def run_session(session_id):
    return _default_runtime.run_session(session_id)


def prepare_resume(session_id, answer):
    return _default_runtime.prepare_resume(session_id, answer)


def resume_session(session_id, answer):
    return _default_runtime.resume_session(session_id, answer)


def cancel_session(session_id):
    return _default_runtime.cancel_session(session_id)


def get_session(session_id):
    return _default_runtime.get_session(session_id)


def list_sessions(limit=20, status=None):
    return _default_runtime.list_sessions(limit=limit, status=status)


def recover_stale_sessions():
    return _default_runtime.recover_stale_sessions()
