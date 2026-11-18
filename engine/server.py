#!/usr/bin/env python3
"""Magnm PR & Social Media Marketing Agency Command Center (v5)
Multi-model NVIDIA NIM inference studio, PR & crisis console, business research radar,
codebase intelligence hub, editorial & Palette v3 visual card studio, CEO approval gate,
centralized Assets Library, Global Team Room, and 13-agent capability-guarded chat.

Strict zero-emoji policy; uniform embedded SVG vector icon system.
Runs on http://127.0.0.1:5050
"""

import base64
import hmac
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import agent_chat
import ai_gateway
import assets_manager
import cmo_runtime
import paths
import product_rules
import repo_sync
import scheduler
import team_room

ROOT = paths.DATA_ROOT
PORT = 5050
HOST = "127.0.0.1"
ENGINE_TOKEN = os.environ.get("MAGNM_ENGINE_TOKEN", "")

CURRENT_JOB = {"running": False, "job_name": None, "job_id": None, "output": "", "error": None}


def run_job_async(job_name, actor="UI"):
    global CURRENT_JOB
    CURRENT_JOB = {
        "running": True,
        "job_name": job_name,
        "job_id": f"{job_name}_{int(threading.get_native_id() if hasattr(threading, 'get_native_id') else 1)}",
        "output": f"Starting {job_name}...\n",
        "error": None
    }

    def worker():
        global CURRENT_JOB
        try:
            res = cmo_runtime.execute_job(job_name, actor=actor)
            if res.get("success"):
                CURRENT_JOB["output"] = res.get("output", "Completed successfully.")
            else:
                CURRENT_JOB["error"] = res.get("error", "Failed.")
        except Exception as e:
            CURRENT_JOB["error"] = str(e)
        finally:
            CURRENT_JOB["running"] = False

    t = threading.Thread(target=worker, daemon=True)
    t.start()


def _read_text_safe(rel_path, limit=12000):
    fp = paths.data(rel_path)
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                return f.read(limit)
        except Exception:
            pass
    return ""


def _parse_channels_from_config():
    md = _read_text_safe("config.md")
    channels = []
    in_accounts = False
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("## Accounts"):
            in_accounts = True
            continue
        if in_accounts and s.startswith("## "):
            break
        if in_accounts and s.startswith("|") and not s.startswith("|---") and not s.lower().startswith("| platform"):
            parts = [p.strip() for p in s.strip("|").split("|")]
            if len(parts) >= 3:
                channels.append({
                    "platform": parts[0],
                    "handle_url": parts[1],
                    "status": parts[2]
                })
    return channels


def _parse_editorial_plan():
    md = _read_text_safe("plan/plan.md")
    rows = []
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("|") and not s.startswith("|---") and not s.lower().startswith("| slot"):
            parts = [p.strip() for p in s.strip("|").split("|")]
            if len(parts) >= 4:
                rows.append({
                    "slot": parts[0],
                    "platforms": parts[1],
                    "topic": parts[2],
                    "status": parts[3]
                })
    return rows


def _get_latest_research_findings():
    res_dir = paths.data("outputs", "research")
    if not os.path.exists(res_dir):
        return {"file": None, "items": []}
    candidates = sorted([f for f in os.listdir(res_dir) if f.endswith("-findings.json")], reverse=True)
    if not candidates:
        return {"file": None, "items": []}
    latest = os.path.join(res_dir, candidates[0])
    try:
        with open(latest, "r", encoding="utf-8") as f:
            items = json.load(f)
        return {"file": os.path.relpath(latest, paths.DATA_ROOT), "items": items[:20]}
    except Exception:
        return {"file": os.path.relpath(latest, paths.DATA_ROOT), "items": []}


class CommandCenterHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _is_authorized(self):
        """Return True if no ENGINE_TOKEN is set or request carries a valid token header/cookie."""
        if not ENGINE_TOKEN:
            return True
        header_token = self.headers.get("X-Magnm-Token") or self.headers.get("x-magnm-token") or ""
        if header_token and hmac.compare_digest(header_token, ENGINE_TOKEN):
            return True
        cookie_hdr = self.headers.get("Cookie") or self.headers.get("cookie") or ""
        for part in cookie_hdr.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip() == "magnm_token" and hmac.compare_digest(v.strip(), ENGINE_TOKEN):
                    return True
        return False

    def send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file_with_range(self, file_path, mime):
        size = os.path.getsize(file_path)
        range_hdr = self.headers.get("Range")
        if range_hdr and range_hdr.startswith("bytes="):
            try:
                spec = range_hdr.replace("bytes=", "").split(",")[0]
                start_s, end_s = spec.split("-", 1)
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else size - 1
                end = min(end, size - 1)
                if start <= end < size:
                    length = end - start + 1
                    with open(file_path, "rb") as f:
                        f.seek(start)
                        chunk = f.read(length)
                    self.send_response(206)
                    self.send_header("Content-Type", mime)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Content-Length", str(length))
                    self.end_headers()
                    self.wfile.write(chunk)
                    return
            except Exception:
                pass

        with open(file_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)

        if url.path == "/api/health":
            self.send_json({
                "ok": True,
                "service": "magnm-engine",
                "version": "0.1.0",
                "data_root": paths.DATA_ROOT,
            })
            return

        if ENGINE_TOKEN and url.path == "/":
            qs = urllib.parse.parse_qs(url.query)
            tok_list = qs.get("token")
            if tok_list and hmac.compare_digest(tok_list[0], ENGINE_TOKEN):
                self.send_response(302)
                self.send_header(
                    "Set-Cookie",
                    f"magnm_token={ENGINE_TOKEN}; HttpOnly; SameSite=Strict; Path=/",
                )
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

        if not self._is_authorized():
            self.send_json({"error": "unauthorized"}, status=401)
            return

        if url.path == "/" or url.path == "/index.html":
            self.serve_ui()
        elif url.path == "/api/status":
            state = cmo_runtime.get_system_status()
            state["current_job"] = CURRENT_JOB
            cfg = ai_gateway.get_config()
            matrix = ai_gateway.get_role_model_matrix()
            roles_ready = sum(1 for m in matrix if m["has_key"])

            state["env_status"] = {
                "has_env_file": os.path.exists(paths.data(".env")),
                "default_provider": cfg["DEFAULT_PROVIDER"],
                "nvidia_nim_base_url": cfg["NVIDIA_NIM_BASE_URL"],
                "nvidia_nim_global": bool(cfg["NVIDIA_NIM_API_KEY"]),
                "nvidia_nim_global_masked": ai_gateway._mask_secret(cfg["NVIDIA_NIM_API_KEY"]),
                "nvidia_nim_fallback_model": cfg["NIM_FALLBACK_MODEL"],
                "nvidia_nim_roles_configured": roles_ready,
                "openai": bool(cfg["OPENAI_API_KEY"]),
                "anthropic": bool(cfg["ANTHROPIC_API_KEY"]),
                "gemini": bool(cfg["GEMINI_API_KEY"]),
                "groq": bool(cfg["GROQ_API_KEY"]),
                "ollama_url": cfg["OLLAMA_BASE_URL"],
                "chatgpt_cli": ai_gateway._is_port_open("127.0.0.1", 9222, timeout=0.1)
            }
            state["models_matrix"] = matrix
            state["nim_catalog_suggestions"] = ai_gateway.NIM_CATALOG_SUGGESTIONS
            state["research_findings"] = _get_latest_research_findings()
            state["editorial_plan"] = _parse_editorial_plan()
            state["channels"] = _parse_channels_from_config()
            state["crisis_playbook"] = _read_text_safe("playbooks/crisis.md")
            state["product_facts"] = _read_text_safe("research/product-facts.md")
            self.send_json(state)

        elif url.path == "/api/job_status":
            self.send_json(CURRENT_JOB)

        elif url.path == "/api/roster":
            self.send_json(agent_chat.get_roster_status())

        elif url.path == "/api/assets":
            self.send_json(assets_manager.get_assets())

        elif url.path == "/api/messages":
            self.send_json(team_room.load_messages())

        elif url.path == "/api/product_rules":
            try:
                self.send_json(product_rules.load_rules())
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)

        elif url.path.startswith("/api/chat_history/"):
            agent_id = url.path.split("/")[-1]
            self.send_json(agent_chat.get_agent_chat_history(agent_id))

        elif url.path.startswith("/api/asset_file/"):
            asset_id = url.path.split("/")[-1]
            all_assets = assets_manager.get_assets()
            target = next((a for a in all_assets if a["id"] == asset_id), None)
            if target:
                full_path = paths.data(target["relative_path"])
                if os.path.exists(full_path) and os.path.isfile(full_path):
                    mime, _ = mimetypes.guess_type(full_path)
                    mime = mime or target.get("mime_type", "application/octet-stream")
                    self._serve_file_with_range(full_path, mime)
                    return
            self.send_error(404, "Asset file not found")

        elif url.path.startswith("/api/asset/"):
            parts = url.path.split("/")[3:]
            if len(parts) >= 2:
                post_id, filename = parts[0], parts[1]
                for sub in ("posts", "video", "blogs"):
                    cand = paths.data("outputs", sub, post_id, filename)
                    if os.path.exists(cand) and os.path.isfile(cand):
                        mime, _ = mimetypes.guess_type(cand)
                        mime = mime or "application/octet-stream"
                        self._serve_file_with_range(cand, mime)
                        return
            self.send_error(404, "Asset not found")

        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if not self._is_authorized():
            self.send_json({"error": "unauthorized"}, status=401)
            return

        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
        try:
            params = json.loads(body)
        except Exception:
            params = {}

        if url.path == "/api/shutdown":
            self.send_json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        if url.path == "/api/run":
            job_name = params.get("job")
            if not job_name or job_name not in cmo_runtime.ALLOWED_JOBS:
                self.send_json({"error": "Invalid job"}, status=400)
                return
            if CURRENT_JOB["running"]:
                self.send_json({"error": "Job already running"}, status=409)
                return
            run_job_async(job_name)
            self.send_json({"success": True, "message": f"Started {job_name}"})

        elif url.path == "/api/messages":
            text = params.get("text", "").strip()
            sender = params.get("sender", "CEO")
            attached_assets = params.get("attached_assets", [])
            if not text:
                self.send_json({"error": "Text required"}, status=400)
                return
            replies = team_room.post_message(sender, text, attached_assets=attached_assets)
            self.send_json({"success": True, "replies": replies})

        elif url.path == "/api/assets/upload":
            filename = params.get("filename", "upload.dat")
            content_b64 = params.get("content_base64", "")
            category = params.get("category", "brand")
            description = params.get("description", "")
            tags = params.get("tags", [])

            if not content_b64:
                self.send_json({"error": "No file content"}, status=400)
                return
            try:
                raw_bytes = base64.b64decode(content_b64)
                entry = assets_manager.register_asset(filename, raw_bytes, category=category, description=description, tags=tags)
                self.send_json({"success": True, "asset": entry})
            except Exception as e:
                self.send_json({"error": f"Upload failed: {e}"}, status=500)

        elif url.path == "/api/assets/delete":
            asset_id = params.get("id")
            if not asset_id:
                self.send_json({"error": "id required"}, status=400)
                return
            res = assets_manager.delete_asset(asset_id)
            self.send_json({"success": res})

        elif url.path == "/api/repos/add":
            remote_url = params.get("remote_url", "").strip()
            name = params.get("name", "").strip()
            product = params.get("product", "").strip() or name
            status = params.get("status", "in_development")
            marketing_angle = params.get("marketing_angle", "")

            if not remote_url or not name:
                self.send_json({"error": "Remote URL and Name are required"}, status=400)
                return

            repo_id = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
            repo_conf = {
                "id": repo_id,
                "name": name,
                "product": product,
                "status": status,
                "remote_url": remote_url,
                "local_path": params.get("local_path", "").strip() or None,
                "marketing_angle": marketing_angle
            }
            res = repo_sync.add_repository(repo_conf)
            self.send_json({"success": True, "repos": res})

        elif url.path == "/api/agent_chat":
            agent_id = params.get("agent_id")
            message = params.get("message")
            attached_assets = params.get("attached_assets", [])
            if not agent_id or not message:
                self.send_json({"error": "agent_id and message required"}, status=400)
                return
            res = agent_chat.chat_with_agent(agent_id, message, attached_assets=attached_assets)
            self.send_json(res)

        elif url.path == "/api/approve":
            post_id = params.get("post_id")
            if not post_id:
                self.send_json({"error": "post_id required"}, status=400)
                return
            try:
                manifest = cmo_runtime.set_post_state(post_id, "approved", actor="CEO")
                self.send_json({"success": True, "manifest": manifest})
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)

        elif url.path == "/api/request_revision":
            post_id = params.get("post_id")
            notes = params.get("notes", "Revision requested by CEO.")
            if not post_id:
                self.send_json({"error": "post_id required"}, status=400)
                return
            try:
                manifest = cmo_runtime.set_post_state(post_id, "revision_required", actor="CEO")
                cmo_runtime.log_activity("CEO", "Writer", f"revision_requested:{post_id}", notes)
                self.send_json({"success": True, "manifest": manifest})
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)

        elif url.path == "/api/publish":
            post_id = params.get("post_id")
            if not post_id:
                self.send_json({"error": "post_id required"}, status=400)
                return
            try:
                manifest = cmo_runtime.set_post_state(post_id, "published", actor="CEO")
                self.send_json({"success": True, "manifest": manifest})
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)

        elif url.path == "/api/save_env":
            key_name = params.get("key_name", "").strip().upper()
            key_val = params.get("key_val", "").strip()
            if not key_name:
                self.send_json({"error": "Invalid key name"}, status=400)
                return
            try:
                saved = ai_gateway.save_env_values({key_name: key_val})
                self.send_json({"success": True, "saved_keys": saved})
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)

        elif url.path == "/api/save_env_config":
            updates = params.get("updates", {})
            if not isinstance(updates, dict) or not updates:
                self.send_json({"error": "updates dictionary required"}, status=400)
                return
            try:
                saved = ai_gateway.save_env_values(updates)
                cmo_runtime.log_activity("CEO", "AI_GATEWAY", "env_config_updated", f"{len(saved)} keys saved to .env")
                self.send_json({"success": True, "saved_keys": saved})
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)

        elif url.path == "/api/test_ai":
            role_id = params.get("role_id", "cmo")
            res = ai_gateway.test_role_endpoint(role_id)
            self.send_json(res)

        elif url.path == "/api/cards/render":
            raw_post_id = params.get("post_id", "custom-card").strip()
            post_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw_post_id).strip("-") or "custom-card"
            field = params.get("field", "sky")
            kicker = params.get("kicker", "CERO")
            big_lines = params.get("big", ["ASKS", "FIRST."])
            if isinstance(big_lines, str):
                big_lines = [ln.strip() for ln in big_lines.splitlines() if ln.strip()]
            serif = params.get("serif", "")
            foot = params.get("foot", "magnm-org.vercel.app")
            sizes = params.get("sizes", ["4x5", "1x1", "16x9"])
            asset_id = params.get("asset_id", "")

            out_dir = paths.data("outputs", "posts", post_id)
            os.makedirs(out_dir, exist_ok=True)

            spec = {
                "id": post_id,
                "field": field,
                "kicker": kicker,
                "big": big_lines[:3],
                "serif": serif,
                "foot": foot,
                "sizes": sizes
            }

            if asset_id:
                asset_match = next((a for a in assets_manager.get_assets() if a["id"] == asset_id), None)
                if asset_match and asset_match.get("file_group") == "image":
                    img_abs = paths.data(asset_match["relative_path"])
                    if os.path.exists(img_abs):
                        spec["image"] = img_abs

            spec_path = os.path.join(out_dir, "card_spec.json")
            with open(spec_path, "w", encoding="utf-8") as f:
                json.dump(spec, f, indent=2)

            try:
                proc = subprocess.run(
                    [sys.executable, paths.resource("templates", "card.py"), spec_path, out_dir],
                    capture_output=True, text=True, timeout=30
                )
                if proc.returncode != 0:
                    self.send_json({"error": proc.stderr or proc.stdout or "Card render failed"}, status=500)
                    return

                rendered_paths = [os.path.basename(line.strip()) for line in proc.stdout.splitlines() if line.strip()]
                kit_path = os.path.join(out_dir, "POSTING-KIT.md")
                if not os.path.exists(kit_path):
                    with open(kit_path, "w", encoding="utf-8") as kf:
                        kf.write(
                            f"# Posting kit: {post_id}\n"
                            f"Owner: CEO approves & posts manually.\n"
                            f"Palette Field: {field}\n"
                            f"Headline: {' / '.join(big_lines[:3])}\n"
                            f"Subline: {serif}\n"
                            f"Rendered formats: {', '.join(rendered_paths)}\n"
                        )

                manifest = cmo_runtime.build_delivery_package(post_id)
                cmo_runtime.log_activity("Designer", "CEO", f"render_cards:{post_id}", f"outputs/posts/{post_id}/")
                self.send_json({
                    "success": True,
                    "post_id": post_id,
                    "rendered_files": rendered_paths,
                    "manifest": manifest
                })
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)

        elif url.path == "/api/pr/draft_reply":
            scenario = params.get("scenario", "misunderstanding")
            severity = params.get("severity", "L1")
            source_context = params.get("source_context", "").strip()
            if not source_context:
                self.send_json({"error": "source_context is required"}, status=400)
                return

            crisis_playbook = _read_text_safe("playbooks/crisis.md")
            facts = _read_text_safe("research/product-facts.md")
            repo_sync_md = _read_text_safe("research/repo-sync.md", limit=1500)

            system_prompt = f"""You are the Community & PR Crisis Lead (@community) working with the Gatekeeper (@reviewer) at MAGNM.
Draft a concise, high-trust external PR or client response strictly adhering to MAGNM's crisis playbook and verified ground truth.

Crisis & Reply Playbook:
{crisis_playbook}

Verified Product Facts (ONLY claim what is written here):
{facts}

Live Codebase Context:
{repo_sync_md}

Hard Rules:
1. ZERO emojis. Direct, calm, transparent tone.
2. Never argue, never use hype words, never invent features/benchmarks, never admit unverified legal liability.
3. Follow the product rules:
{product_rules.prompt_block()}
4. Output structure:
   - SEVERITY & ESCALATION CHECK: State L1/L2/L3 action and whether CEO approval is mandatory before posting.
   - DRAFT RESPONSE: Ready-to-review reply text.
   - FACTUAL VERIFICATION NOTES: Exact claims referenced from product-facts.md or repo-sync.md, plus anything the CEO must confirm first.
"""
            user_prompt = f"Scenario Type: {scenario}\nSeverity Classification: {severity}\nIncoming Comment / Client / Press Inquiry:\n{source_context}"

            try:
                res = ai_gateway.generate_completion_with_meta(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    model_tier="reasoning",
                    agent_role="community"
                )
                cmo_runtime.log_activity("Community", "CEO", f"pr_draft:{scenario}:{severity}", f"model:{res.get('model_used')}")
                self.send_json({
                    "success": True,
                    "draft": res["text"],
                    "model_used": res.get("model_used"),
                    "provider": res.get("provider"),
                    "fallback_used": res.get("fallback_used", False),
                    "fallback_reason": res.get("fallback_reason"),
                    "severity": severity,
                    "scenario": scenario
                })
            except ai_gateway.AIAuthError as e:
                self.send_json({"success": False, "error_code": "ai_auth_error", "error": str(e)})
            except ai_gateway.AINotConnected as e:
                self.send_json({"success": False, "error_code": "ai_not_connected", "error": str(e)})

        elif url.path == "/api/check_text":
            text = params.get("text", "")
            if not isinstance(text, str):
                self.send_json({"error": "text must be a string"}, status=400)
                return
            try:
                warnings = product_rules.check_text(text)
                self.send_json({"warnings": warnings})
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)

        elif url.path == "/api/schedule/update":
            sched_id = params.get("id")
            interval = params.get("interval_hours")
            enabled = params.get("enabled")
            sched_file = paths.data("state", "schedules.json")
            if os.path.exists(sched_file):
                with open(sched_file, "r", encoding="utf-8") as f:
                    s_data = json.load(f)
                for s in s_data.get("schedules", []):
                    if s["id"] == sched_id:
                        if interval is not None:
                            s["interval_hours"] = int(interval)
                        if enabled is not None:
                            s["enabled"] = bool(enabled)
                with open(sched_file, "w", encoding="utf-8") as f:
                    json.dump(s_data, f, indent=2)
                scheduler.generate_systemd_units()
                self.send_json({"success": True})
            else:
                self.send_json({"error": "Schedules file missing"}, status=500)

        elif url.path == "/api/schedule/toggle":
            sched_id = params.get("id")
            sched_file = paths.data("state", "schedules.json")
            if os.path.exists(sched_file):
                with open(sched_file, "r", encoding="utf-8") as f:
                    s_data = json.load(f)
                for s in s_data.get("schedules", []):
                    if s["id"] == sched_id:
                        s["enabled"] = not s.get("enabled", False)
                with open(sched_file, "w", encoding="utf-8") as f:
                    json.dump(s_data, f, indent=2)
                self.send_json({"success": True})
            else:
                self.send_json({"error": "Schedules file missing"}, status=500)

        else:
            self.send_error(404, "Unknown endpoint")

    def serve_ui(self):
        html = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MAGNM // PR &amp; Social Agency Command Center</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-canvas: #090A0D;
      --bg-rail: #0E1015;
      --bg-panel: #13161C;
      --bg-elevated: #1A1E26;
      --border: #252A36;
      --border-strong: #32394A;
      --ink: #F2F2EE;
      --muted: #8C93A4;
      --lime: #C8F31D;
      --lime-dim: rgba(200, 243, 29, 0.12);
      --mint: #6EE7B7;
      --sky: #7DD3FC;
      --amber: #FCD34D;
      --danger: #F87171;
      --lilac: #C4B5FD;
      --font-sans: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
      --font-mono: 'JetBrains Mono', monospace;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg-canvas);
      color: var(--ink);
      font-family: var(--font-sans);
      min-height: 100vh;
      display: flex;
      overflow-x: hidden;
    }

    /* Uniform Geometric SVG Icons */
    .icon {
      width: 15px;
      height: 15px;
      stroke-width: 1.85;
      stroke: currentColor;
      fill: none;
      stroke-linecap: round;
      stroke-linejoin: round;
      vertical-align: middle;
      display: inline-block;
      flex-shrink: 0;
    }
    .icon-lg { width: 18px; height: 18px; }
    .icon-xl { width: 34px; height: 34px; }

    /* Left Navigation Rail */
    aside.sidebar {
      width: 264px;
      flex-shrink: 0;
      background: var(--bg-rail);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow-y: auto;
    }
    .brand-box {
      padding: 20px 18px 16px;
      border-bottom: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .brand-top {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .brand-mark {
      background: var(--lime);
      color: #090A0D;
      font-family: var(--font-mono);
      font-weight: 800;
      font-size: 12px;
      letter-spacing: 1px;
      padding: 4px 8px;
      border-radius: 4px;
    }
    .brand-name {
      font-size: 14px;
      font-weight: 700;
      letter-spacing: -0.2px;
    }
    .brand-sub {
      font-family: var(--font-mono);
      font-size: 10px;
      color: var(--muted);
      letter-spacing: 0.5px;
      text-transform: uppercase;
    }
    .nav-sections {
      padding: 14px 10px;
      display: flex;
      flex-direction: column;
      gap: 16px;
      flex: 1;
    }
    .nav-group-label {
      font-family: var(--font-mono);
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.9px;
      color: #636B7D;
      padding: 0 10px 6px;
      text-transform: uppercase;
    }
    .nav-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 9px 12px;
      border-radius: 6px;
      cursor: pointer;
      color: var(--muted);
      font-size: 13px;
      font-weight: 500;
      transition: all 0.14s ease;
      margin-bottom: 2px;
      border: 1px solid transparent;
    }
    .nav-item:hover {
      background: var(--bg-panel);
      color: var(--ink);
    }
    .nav-item.active {
      background: var(--lime-dim);
      color: var(--lime);
      border-color: rgba(200, 243, 29, 0.28);
      font-weight: 600;
    }
    .nav-item-left {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .nav-pill {
      font-family: var(--font-mono);
      font-size: 10px;
      padding: 1px 6px;
      border-radius: 99px;
      background: var(--bg-elevated);
      color: var(--ink);
      border: 1px solid var(--border-strong);
    }
    .rail-footer {
      padding: 14px 16px;
      border-top: 1px solid var(--border);
      background: #0B0C10;
      font-family: var(--font-mono);
      font-size: 11px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .status-line {
      display: flex;
      align-items: center;
      justify-content: space-between;
      color: var(--muted);
    }

    /* Main Workspace Shell */
    .workspace-shell {
      flex: 1;
      display: flex;
      flex-direction: column;
      min-width: 0;
    }
    header.topbar {
      background: rgba(14, 16, 21, 0.9);
      backdrop-filter: blur(8px);
      border-bottom: 1px solid var(--border);
      padding: 14px 28px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      position: sticky;
      top: 0;
      z-index: 90;
    }
    .topbar-left {
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .page-title {
      font-size: 15px;
      font-weight: 700;
      letter-spacing: -0.2px;
    }
    .page-crumb {
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--muted);
    }
    .topbar-right {
      display: flex;
      align-items: center;
      gap: 16px;
      font-family: var(--font-mono);
      font-size: 11px;
    }
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--lime);
      display: inline-block;
      box-shadow: 0 0 8px var(--lime);
      margin-right: 4px;
    }
    .status-dot.busy {
      background: var(--amber);
      box-shadow: 0 0 8px var(--amber);
      animation: pulse 1s infinite alternate;
    }
    @keyframes pulse { from { opacity: 0.4; } to { opacity: 1; } }

    main.stage {
      padding: 24px 28px 48px;
      max-width: 1480px;
      width: 100%;
      margin: 0 auto;
    }
    .tab-view { display: none; flex-direction: column; gap: 22px; }
    .tab-view.active { display: flex; }

    /* Grids & Cards */
    .kpi-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 16px; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
    .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; }
    .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
    .grid-asymmetric { display: grid; grid-template-columns: 1.35fr 1fr; gap: 20px; }
    @media (max-width: 1180px) {
      .kpi-grid { grid-template-columns: repeat(2, 1fr); }
      .grid-3, .grid-4, .grid-asymmetric { grid-template-columns: 1fr 1fr; }
    }
    @media (max-width: 860px) {
      body { flex-direction: column; }
      aside.sidebar { width: 100%; height: auto; position: static; }
      .grid-2, .grid-3, .grid-4, .grid-asymmetric, .kpi-grid { grid-template-columns: 1fr; }
    }

    .kpi-card {
      background: var(--bg-panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px 18px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .kpi-label {
      font-family: var(--font-mono);
      font-size: 10px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.7px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .kpi-val {
      font-size: 24px;
      font-weight: 800;
      letter-spacing: -0.5px;
    }
    .kpi-foot {
      font-size: 11px;
      color: var(--muted);
    }

    .panel {
      background: var(--bg-panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 20px 22px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .panel-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--border);
      padding-bottom: 12px;
      gap: 12px;
      flex-wrap: wrap;
    }
    .panel-title {
      font-family: var(--font-mono);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.9px;
      color: var(--lime);
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .panel-meta {
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--muted);
    }

    /* Tables */
    .data-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }
    .data-table th {
      text-align: left;
      font-family: var(--font-mono);
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.7px;
      color: var(--muted);
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      background: #101319;
    }
    .data-table td {
      padding: 11px 12px;
      border-bottom: 1px solid #1C2029;
      vertical-align: top;
    }
    .data-table tr:hover td {
      background: rgba(255, 255, 255, 0.02);
    }

    /* Badges & Tags */
    .badge {
      font-family: var(--font-mono);
      font-size: 10px;
      padding: 3px 8px;
      border-radius: 4px;
      text-transform: uppercase;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      white-space: nowrap;
    }
    .badge-draft { background: #1E222B; color: #A6ADBB; border: 1px solid #2E3442; }
    .badge-approved, .badge-ok { background: rgba(110, 231, 183, 0.12); color: var(--mint); border: 1px solid rgba(110, 231, 183, 0.35); }
    .badge-revision, .badge-warn { background: rgba(252, 211, 77, 0.12); color: var(--amber); border: 1px solid rgba(252, 211, 77, 0.35); }
    .badge-published, .badge-info { background: rgba(125, 211, 252, 0.12); color: var(--sky); border: 1px solid rgba(125, 211, 252, 0.35); }
    .badge-danger { background: rgba(248, 113, 113, 0.12); color: var(--danger); border: 1px solid rgba(248, 113, 113, 0.35); }
    .code-tag {
      font-family: var(--font-mono);
      font-weight: 700;
      font-size: 10px;
      background: var(--bg-elevated);
      color: var(--lime);
      padding: 2px 7px;
      border-radius: 3px;
      border: 1px solid var(--border-strong);
      display: inline-block;
    }

    /* Buttons & Form Inputs */
    .btn {
      background: var(--bg-elevated);
      color: var(--ink);
      border: 1px solid var(--border-strong);
      padding: 8px 14px;
      border-radius: 6px;
      font-family: var(--font-mono);
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 7px;
      transition: all 0.14s ease;
    }
    .btn:hover { background: #242A36; border-color: #465066; }
    .btn-sm { padding: 4px 10px; font-size: 10px; }
    .btn-lime { background: var(--lime); color: #090A0D; border-color: var(--lime); font-weight: 700; }
    .btn-lime:hover { background: #D7FF3B; border-color: #D7FF3B; }
    .btn-mint { background: rgba(110, 231, 183, 0.16); color: var(--mint); border-color: rgba(110, 231, 183, 0.45); }
    .btn-mint:hover { background: rgba(110, 231, 183, 0.25); }
    .btn-warning { background: rgba(252, 211, 77, 0.14); color: var(--amber); border-color: rgba(252, 211, 77, 0.4); }
    .btn-danger { background: rgba(248, 113, 113, 0.14); color: var(--danger); border-color: rgba(248, 113, 113, 0.4); }

    input, select, textarea {
      background: #0B0D11;
      border: 1px solid var(--border-strong);
      color: var(--ink);
      padding: 9px 12px;
      border-radius: 6px;
      font-family: var(--font-mono);
      font-size: 12px;
      width: 100%;
    }
    input:focus, select:focus, textarea:focus {
      outline: none;
      border-color: var(--lime);
    }
    label.field-label {
      font-family: var(--font-mono);
      font-size: 10px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.6px;
      display: block;
      margin-bottom: 5px;
    }

    /* Terminal & Previews */
    .terminal-window {
      background: #07080B;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 14px;
      font-family: var(--font-mono);
      font-size: 11px;
      line-height: 1.5;
      min-height: 140px;
      max-height: 220px;
      overflow-y: auto;
      color: #B4F8C8;
      white-space: pre-wrap;
    }

    /* Repo & Agent Cards */
    .repo-card, .agent-card, .asset-item-card, .review-card {
      background: #101319;
      border: 1px solid var(--border);
      border-radius: 7px;
      padding: 16px 18px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .agent-card { transition: border-color 0.15s, transform 0.15s; justify-content: space-between; }
    .agent-card:hover { border-color: var(--lime); transform: translateY(-2px); }
    .commit-box {
      background: #090A0E;
      border-left: 2px solid var(--lime);
      padding: 8px 12px;
      font-family: var(--font-mono);
      font-size: 11px;
      color: #C7CCD6;
    }

    /* Review Gallery */
    .review-gallery { display: flex; gap: 14px; overflow-x: auto; padding-bottom: 6px; align-items: flex-start; }
    .review-img { max-height: 210px; border-radius: 6px; border: 1px solid var(--border-strong); background: #090A0D; }
    .review-video { max-height: 220px; max-width: 380px; border-radius: 6px; border: 1px solid var(--border-strong); background: #000; }
    .review-kit-box {
      background: #080A0D;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 14px;
      font-family: var(--font-mono);
      font-size: 11px;
      line-height: 1.5;
      max-height: 220px;
      overflow-y: auto;
      white-space: pre-wrap;
      color: #D2D7E0;
    }

    /* Global Room & Chat Drawer */
    .chat-modal {
      position: fixed;
      right: 0;
      top: 0;
      width: 520px;
      max-width: 100vw;
      height: 100vh;
      background: var(--bg-rail);
      border-left: 1px solid var(--border-strong);
      z-index: 200;
      display: none;
      flex-direction: column;
      box-shadow: -14px 0 40px rgba(0, 0, 0, 0.75);
    }
    .chat-modal.open { display: flex; }
    .chat-header {
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: var(--bg-panel);
    }
    .chat-messages {
      flex: 1;
      padding: 18px 20px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .chat-bubble {
      padding: 12px 15px;
      border-radius: 7px;
      font-size: 13px;
      line-height: 1.5;
      max-width: 90%;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .chat-bubble.user {
      align-self: flex-end;
      background: #202531;
      color: var(--ink);
      border: 1px solid var(--border-strong);
    }
    .chat-bubble.agent {
      align-self: flex-start;
      background: #141E19;
      color: #E5F9EC;
      border: 1px solid #264032;
    }
    .chat-bubble.cmo {
      align-self: flex-start;
      background: #14212E;
      color: #E2F1FF;
      border: 1px solid #284664;
    }
    .chat-bubble.redirect {
      align-self: flex-start;
      background: #281B18;
      color: #FFE4DE;
      border: 1px solid #59322A;
    }
    .chat-input-bar {
      padding: 14px 16px;
      border-top: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      gap: 8px;
      background: var(--bg-panel);
    }

    /* Asset Preview */
    .asset-preview-box {
      height: 136px;
      background: #08090C;
      border-radius: 4px;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      border: 1px solid var(--border);
    }
    .asset-preview-img { max-height: 100%; max-width: 100%; object-fit: contain; }

    /* Modal Backdrop */
    .modal-backdrop {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.78);
      z-index: 300;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .modal-card {
      background: var(--bg-panel);
      border: 1px solid var(--border-strong);
      border-radius: 8px;
      padding: 22px;
      width: 500px;
      max-width: 100%;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
  </style>
</head>
<body>
  <!-- Embedded Uniform SVG Vector Icon Set -->
  <svg style="display:none;">
    <defs>
      <g id="icon-grid"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></g>
      <g id="icon-radar"><circle cx="12" cy="12" r="10"></circle><circle cx="12" cy="12" r="6"></circle><circle cx="12" cy="12" r="2"></circle><line x1="12" y1="2" x2="12" y2="12"></line></g>
      <g id="icon-calendar"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></g>
      <g id="icon-shield"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></g>
      <g id="icon-check-circle"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></g>
      <g id="icon-globe"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></g>
      <g id="icon-users"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path></g>
      <g id="icon-cpu"><rect x="4" y="4" width="16" height="16" rx="2" ry="2"></rect><rect x="9" y="9" width="6" height="6"></rect><line x1="9" y1="1" x2="9" y2="4"></line><line x1="15" y1="1" x2="15" y2="4"></line><line x1="9" y1="20" x2="9" y2="23"></line><line x1="15" y1="20" x2="15" y2="23"></line><line x1="20" y1="9" x2="23" y2="9"></line><line x1="20" y1="14" x2="23" y2="14"></line><line x1="1" y1="9" x2="4" y2="9"></line><line x1="1" y1="14" x2="4" y2="14"></line></g>
      <g id="icon-folder"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></g>
      <g id="icon-clock"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></g>
      <g id="icon-zap"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></g>
      <g id="icon-search"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></g>
      <g id="icon-refresh"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></g>
      <g id="icon-check"><polyline points="20 6 9 17 4 12"></polyline></g>
      <g id="icon-edit"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></g>
      <g id="icon-send"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></g>
      <g id="icon-plus"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></g>
      <g id="icon-trash"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></g>
      <g id="icon-file"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path><polyline points="13 2 13 9 20 9"></polyline></g>
      <g id="icon-film"><rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"></rect><line x1="7" y1="2" x2="7" y2="22"></line><line x1="17" y1="2" x2="17" y2="22"></line><line x1="2" y1="12" x2="22" y2="12"></line></g>
      <g id="icon-paperclip"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path></g>
      <g id="icon-x"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></g>
      <g id="icon-copy"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></g>
      <g id="icon-git"><line x1="6" y1="3" x2="6" y2="15"></line><circle cx="18" cy="6" r="3"></circle><circle cx="6" cy="18" r="3"></circle><path d="M18 9a9 9 0 0 1-9 9"></path></g>
      <g id="icon-alert"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></g>
      <g id="icon-external"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></g>
      <g id="icon-activity"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></g>
    </defs>
  </svg>

  <!-- Left Agency Rail -->
  <aside class="sidebar">
    <div>
      <div class="brand-box">
        <div class="brand-top">
          <span class="brand-mark">MAGNM</span>
          <span class="brand-name">PR &amp; Growth OS</span>
        </div>
        <div class="brand-sub">Studio + Freelance Agency</div>
      </div>

      <nav class="nav-sections">
        <div>
          <div class="nav-group-label">Agency Operations</div>
          <div class="nav-item active" data-tab="tab-ops" data-title="Agency Overview &amp; Presence" onclick="switchTab('tab-ops', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-grid"/></svg> Overview &amp; Presence</span>
          </div>
          <div class="nav-item" data-tab="tab-research" data-title="Business Research &amp; PR Radar" onclick="switchTab('tab-research', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-radar"/></svg> Research &amp; PR Radar</span>
            <span class="nav-pill" id="radarCountBadge">0</span>
          </div>
          <div class="nav-item" data-tab="tab-campaigns" data-title="Editorial Calendar &amp; Card Studio" onclick="switchTab('tab-campaigns', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-calendar"/></svg> Editorial &amp; Studio</span>
          </div>
        </div>

        <div>
          <div class="nav-group-label">PR &amp; Governance</div>
          <div class="nav-item" data-tab="tab-pr" data-title="PR &amp; Crisis Communications" onclick="switchTab('tab-pr', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-shield"/></svg> PR &amp; Crisis Console</span>
          </div>
          <div class="nav-item" data-tab="tab-review" data-title="CEO Review &amp; Approval Gate" onclick="switchTab('tab-review', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-check-circle"/></svg> CEO Review Gate</span>
            <span class="nav-pill" id="pendingReviewBadge">0</span>
          </div>
        </div>

        <div>
          <div class="nav-group-label">AI Agency Team</div>
          <div class="nav-item" data-tab="tab-room" data-title="Global Team Room &amp; Swarm" onclick="switchTab('tab-room', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-globe"/></svg> Global Team Room</span>
          </div>
          <div class="nav-item" data-tab="tab-team" data-title="Team Roster &amp; Specialist Chat" onclick="switchTab('tab-team', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-users"/></svg> Team Roster &amp; Chat</span>
            <span class="nav-pill">13</span>
          </div>
        </div>

        <div>
          <div class="nav-group-label">Infrastructure</div>
          <div class="nav-item" data-tab="tab-models" data-title="AI Models &amp; .env Studio" onclick="switchTab('tab-models', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-cpu"/></svg> AI Models &amp; .env</span>
            <span class="nav-pill" id="nimReadyPill">0/13</span>
          </div>
          <div class="nav-item" data-tab="tab-assets" data-title="Centralized Assets Library" onclick="switchTab('tab-assets', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-folder"/></svg> Assets Library</span>
            <span class="nav-pill" id="assetsCountBadge">0</span>
          </div>
          <div class="nav-item" data-tab="tab-schedule" data-title="Schedules &amp; Timers" onclick="switchTab('tab-schedule', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-clock"/></svg> Schedules</span>
          </div>
        </div>
      </nav>
    </div>

    <div class="rail-footer">
      <div class="status-line"><span>Gateway</span><b id="railGatewayState" style="color:var(--amber);">Standby</b></div>
      <div class="status-line"><span>ChatGPT CLI</span><b id="railChatGptState">Offline</b></div>
      <div class="status-line"><span>Crisis Level</span><b style="color:var(--mint);">L0 NOMINAL</b></div>
    </div>
  </aside>

  <!-- Main Command Workspace -->
  <div class="workspace-shell">
    <header class="topbar">
      <div class="topbar-left">
        <div>
          <div class="page-title" id="headerPageTitle">Agency Overview &amp; Presence</div>
          <div class="page-crumb">Magnm Studio</div>
        </div>
      </div>
      <div class="topbar-right">
        <div><span class="status-dot" id="systemDot"></span> <span id="systemText">SYSTEM READY</span></div>
        <button class="btn btn-sm" onclick="switchTab('tab-models', document.querySelector('[data-tab=tab-models]'))">
          <svg class="icon"><use href="#icon-cpu"/></svg> <span id="topGatewayBadge">Configure .env</span>
        </button>
        <div id="liveClock" style="color:var(--muted);"></div>
      </div>
    </header>

    <main class="stage">
      <!-- 01. OVERVIEW & PRESENCE -->
      <div id="tab-ops" class="tab-view active">
        <div class="kpi-grid">
          <div class="kpi-card">
            <div class="kpi-label">Active Campaigns <svg class="icon"><use href="#icon-activity"/></svg></div>
            <div class="kpi-val" id="kpiCampaigns">0</div>
            <div class="kpi-foot">Trackable post &amp; blog bundles</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">CEO Queue <svg class="icon"><use href="#icon-check-circle"/></svg></div>
            <div class="kpi-val" style="color:var(--amber);" id="kpiPending">0</div>
            <div class="kpi-foot">Awaiting CEO sign-off</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">PR Crisis Posture <svg class="icon"><use href="#icon-shield"/></svg></div>
            <div class="kpi-val" style="color:var(--mint);">L0 NOMINAL</div>
            <div class="kpi-foot">Playbook L1-L3 armed</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Monitored Repos <svg class="icon"><use href="#icon-git"/></svg></div>
            <div class="kpi-val" id="kpiRepos">0</div>
            <div class="kpi-foot">Codebase reality Ground Truth</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">NIM Role Models <svg class="icon"><use href="#icon-cpu"/></svg></div>
            <div class="kpi-val" id="kpiNimModels">0 / 13</div>
            <div class="kpi-foot" id="kpiNimSub">Configure key in AI Studio</div>
          </div>
        </div>

        <div class="grid-asymmetric">
          <div class="panel">
            <div class="panel-header">
              <span class="panel-title"><svg class="icon"><use href="#icon-globe"/></svg> Managed Brand Handles &amp; Online Presence</span>
              <span class="panel-meta">config.md</span>
            </div>
            <table class="data-table">
              <thead>
                <tr><th>Platform</th><th>Handle / URL</th><th>Baseline Status</th></tr>
              </thead>
              <tbody id="channelsTableBody"></tbody>
            </table>
          </div>

          <div class="panel">
            <div class="panel-header">
              <span class="panel-title"><svg class="icon"><use href="#icon-zap"/></svg> Operations Dispatch</span>
              <span class="panel-meta">Deterministic Runtime</span>
            </div>
            <div style="display:flex; flex-wrap:wrap; gap:10px;">
              <button class="btn btn-lime" onclick="runJob('daily_cycle')">
                <svg class="icon"><use href="#icon-zap"/></svg> Run Daily Cycle
              </button>
              <button class="btn btn-mint" onclick="runJob('market_scan')">
                <svg class="icon"><use href="#icon-search"/></svg> Market &amp; HN Scan
              </button>
              <button class="btn btn-mint" onclick="runJob('repo_sync')">
                <svg class="icon"><use href="#icon-refresh"/></svg> Sync Repositories
              </button>
            </div>
            <div>
              <label class="field-label">Execution Output Stream</label>
              <div class="terminal-window" id="terminalLog">System ready. Launch any operation above.</div>
            </div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-activity"/></svg> Agency Audit &amp; Orchestration Log</span>
            <span class="panel-meta">state/activity.log</span>
          </div>
          <div class="terminal-window" id="activityLogBox" style="color:var(--sky); max-height:170px;">Loading audit stream...</div>
        </div>
      </div>

      <!-- 02. BUSINESS RESEARCH & PR RADAR -->
      <div id="tab-research" class="tab-view">
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-radar"/></svg> Market &amp; Opportunity Radar</span>
            <div style="display:flex; gap:10px; align-items:center;">
              <span class="panel-meta" id="researchSourcePath">No scan loaded</span>
              <button class="btn btn-mint btn-sm" onclick="runJob('market_scan')">
                <svg class="icon"><use href="#icon-refresh"/></svg> Scan Live Ecosystem
              </button>
            </div>
          </div>
          <p style="font-size:12px; color:var(--muted);">
            Scored using deterministic formula: Relevance (1-5) x Audience Interest (1-5) x Credibility (1-5) x Timeliness (1-5) = Max 625. Click Pitch to @cmo to initiate campaign orchestration.
          </p>
          <div style="overflow-x:auto;">
            <table class="data-table">
              <thead>
                <tr><th>Score</th><th>Opportunity / Discussion</th><th>Source &amp; Signal</th><th>Action</th></tr>
              </thead>
              <tbody id="researchTableBody"></tbody>
            </table>
          </div>
        </div>

        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-git"/></svg> Monitored Codebases &amp; GitHub Intelligence</span>
            <div style="display:flex; gap:10px;">
              <button class="btn btn-sm" onclick="runJob('repo_sync')"><svg class="icon"><use href="#icon-refresh"/></svg> Sync Now</button>
              <button class="btn btn-lime btn-sm" onclick="openAddRepoModal()"><svg class="icon"><use href="#icon-plus"/></svg> Add GitHub Repo</button>
            </div>
          </div>
          <div class="grid-2" id="repoContainer"></div>
        </div>
      </div>

      <!-- 03. EDITORIAL CALENDAR & CARD STUDIO -->
      <div id="tab-campaigns" class="tab-view">
        <div class="grid-asymmetric">
          <div class="panel">
            <div class="panel-header">
              <span class="panel-title"><svg class="icon"><use href="#icon-calendar"/></svg> Editorial Publishing Schedule</span>
              <span class="panel-meta">plan/plan.md</span>
            </div>
            <table class="data-table">
              <thead>
                <tr><th>Target Slot</th><th>Platforms</th><th>Topic / Package</th><th>Status</th></tr>
              </thead>
              <tbody id="editorialTableBody"></tbody>
            </table>
          </div>

          <div class="panel">
            <div class="panel-header">
              <span class="panel-title"><svg class="icon"><use href="#icon-edit"/></svg> Palette v3 Visual Card Studio</span>
              <span class="panel-meta">templates/card.py (Zero LLM Cost)</span>
            </div>
            <div class="grid-2" style="gap:10px;">
              <div>
                <label class="field-label">Campaign / Post Slug ID</label>
                <input type="text" id="cardPostId" value="20261115-cero-release">
              </div>
              <div>
                <label class="field-label">Palette v3 Field Color</label>
                <select id="cardField">
                  <option value="sky">Sky (#BCE3FF)</option>
                  <option value="stone">Stone (#EFEFEC)</option>
                  <option value="ink">Ink (#0A0A0A Dark)</option>
                  <option value="lime">Lime (#C8F31D)</option>
                  <option value="mint">Mint (#C7F5D9)</option>
                  <option value="butter">Butter (#FFE89E)</option>
                  <option value="coral">Coral (#FF9F80)</option>
                  <option value="lilac">Lilac (#DCCBFF)</option>
                </select>
              </div>
            </div>
            <div class="grid-2" style="gap:10px;">
              <div>
                <label class="field-label">Top Kicker Label</label>
                <input type="text" id="cardKicker" value="CERO TERMINAL v2.2.3">
              </div>
              <div>
                <label class="field-label">Optional Screenshot Asset</label>
                <select id="cardAssetSelect"><option value="">None (Typography Only)</option></select>
              </div>
            </div>
            <div>
              <label class="field-label">Display Headline (Up to 3 lines)</label>
              <textarea id="cardBigLines" rows="2">ASKS
FIRST.</textarea>
            </div>
            <div class="grid-2" style="gap:10px;">
              <div>
                <label class="field-label">Italic Serif Connector</label>
                <input type="text" id="cardSerif" value="before anything changes your files.">
              </div>
              <div>
                <label class="field-label">Footer URL</label>
                <input type="text" id="cardFoot" value="magnm-org.vercel.app">
              </div>
            </div>
            <button class="btn btn-lime" onclick="renderStudioCard()">
              <svg class="icon"><use href="#icon-zap"/></svg> Render Multi-Ratio Cards &amp; Stage for Review
            </button>
            <div id="cardRenderPreview" style="display:none; flex-direction:column; gap:10px;">
              <div class="panel-meta" id="cardRenderStatus">Rendered!</div>
              <div class="review-gallery" id="cardRenderGallery"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- 04. PR & CRISIS COMMUNICATIONS -->
      <div id="tab-pr" class="tab-view">
        <div class="grid-asymmetric">
          <div class="panel">
            <div class="panel-header">
              <span class="panel-title"><svg class="icon"><use href="#icon-shield"/></svg> PR, Backlash &amp; Freelance Client Response Drafter</span>
              <span class="panel-meta">Agent: @community + @reviewer guardrail</span>
            </div>
            <div class="grid-2" style="gap:12px;">
              <div>
                <label class="field-label">Communication Scenario</label>
                <select id="prScenario">
                  <option value="freelance_inquiry">Freelance Client / Project Inquiry</option>
                  <option value="misunderstanding">Technical Misunderstanding (Clarify w/ Evidence)</option>
                  <option value="valid_bug">Valid Bug Report (Plainly Own &amp; Fix Timeline)</option>
                  <option value="fair_criticism">Fair Criticism (Acknowledge &amp; Action)</option>
                  <option value="unfair">Unfair / Bad-Faith Comment (One Calm Correction)</option>
                </select>
              </div>
              <div>
                <label class="field-label">Incident Severity Tier</label>
                <select id="prSeverity">
                  <option value="L1">L1 Minor (1-2 skeptical comments / standard question)</option>
                  <option value="L2">L2 Notable (Critical thread gaining traction - CEO approves)</option>
                  <option value="L3">L3 Serious (Security/Privacy/Legal - Pause promos, CEO decides)</option>
                </select>
              </div>
            </div>
            <div>
              <label class="field-label">Incoming Comment, Thread, Press Question or Client Brief</label>
              <textarea id="prContextInput" rows="4" placeholder="Paste the Hacker News comment, Reddit feedback, press query, or client requirement here..."></textarea>
            </div>
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span class="panel-meta">Hard gate: Never posts autonomously. All PR replies require CEO send.</span>
              <button class="btn btn-lime" id="btnDraftPr" onclick="draftPrReply()">
                <svg class="icon"><use href="#icon-send"/></svg> Generate Grounded Draft
              </button>
            </div>
            <div id="prDraftOutputBox" style="display:none; flex-direction:column; gap:8px;">
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <span class="badge badge-info" id="prDraftModelTag">Model</span>
                <button class="btn btn-sm" onclick="copyPrDraft()"><svg class="icon"><use href="#icon-copy"/></svg> Copy Draft</button>
              </div>
              <div class="review-kit-box" id="prDraftText"></div>
            </div>
          </div>

          <div style="display:flex; flex-direction:column; gap:20px;">
            <div class="panel">
              <div class="panel-header">
                <span class="panel-title"><svg class="icon"><use href="#icon-alert"/></svg> Crisis Protocol (L1 - L3)</span>
                <span class="panel-meta">playbooks/crisis.md</span>
              </div>
              <div class="review-kit-box" id="crisisPlaybookBox" style="max-height:240px;"></div>
            </div>
            <div class="panel">
              <div class="panel-header">
                <span class="panel-title"><svg class="icon"><use href="#icon-check-circle"/></svg> Verified Facts Guardrail</span>
                <span class="panel-meta">research/product-facts.md</span>
              </div>
              <div class="review-kit-box" id="productFactsBox" style="max-height:240px;"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- 05. CEO REVIEW & APPROVAL GATE -->
      <div id="tab-review" class="tab-view">
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-check-circle"/></svg> CEO Content Review &amp; Publishing Approval Gate</span>
            <span class="panel-meta">Explicit CEO sign-off required before publication</span>
          </div>
          <div id="ceoReviewList" style="display:flex; flex-direction:column; gap:20px;"></div>
        </div>
      </div>

      <!-- 06. GLOBAL TEAM ROOM -->
      <div id="tab-room" class="tab-view">
        <div class="panel" style="height: 700px; display:flex; flex-direction:column;">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-globe"/></svg> Global Team Room &amp; CMO Swarm Board</span>
            <span class="panel-meta">Tag any of 13 roles with @handle · Specialist tasks auto-escalate to @cmo</span>
          </div>
          <div id="globalMessagesBox" style="flex:1; overflow-y:auto; padding:10px; display:flex; flex-direction:column; gap:14px;"></div>
          <div style="border-top:1px solid var(--border); padding-top:12px; display:flex; flex-direction:column; gap:10px;">
            <div style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;">
              <span class="panel-meta" style="margin-right:4px;">Quick @mention:</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@cmo ')">@cmo</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@strategist ')">@strategist</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@advisor ')">@advisor</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@researcher ')">@researcher</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@blog-writer ')">@blog-writer</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@writer ')">@writer</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@designer ')">@designer</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@video-producer ')">@video-producer</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@reviewer ')">@reviewer</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@analyst ')">@analyst</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@listener ')">@listener</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@community ')">@community</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@growth ')">@growth</span>
            </div>
            <div style="display:flex; gap:10px; align-items:center;">
              <select id="roomAssetSelect" style="width:220px; padding:8px;">
                <option value="">Attach Asset (None)</option>
              </select>
              <input type="text" id="globalMsgInput" placeholder="Message the agency room or assign a task (e.g. '@cmo plan Cero v2.2.3 PR rollout')..." onkeydown="if(event.key==='Enter') sendGlobalMessage()">
              <button class="btn btn-lime" onclick="sendGlobalMessage()">
                <svg class="icon"><use href="#icon-send"/></svg> Send
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- 07. TEAM ROSTER & SPECIALIST CHAT -->
      <div id="tab-team" class="tab-view">
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-users"/></svg> 13-Specialist Agency Roster</span>
            <span class="panel-meta">Strict Capability Contracts · Non-CMO Assignments Auto-Escalate to @cmo</span>
          </div>
          <div class="grid-3" id="rosterContainer"></div>
        </div>
      </div>

      <!-- 08. AI MODELS & .ENV STUDIO -->
      <div id="tab-models" class="tab-view">
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-cpu"/></svg> NVIDIA NIM &amp; Multi-Provider Gateway Configuration (.env)</span>
            <span class="panel-meta" id="envStudioMeta">Reading .env...</span>
          </div>
          <div class="grid-3" style="gap:14px;">
            <div>
              <label class="field-label">Default Inference Provider</label>
              <select id="envDefaultProvider">
                <option value="nvidia_nim">nvidia_nim (Primary Recommended)</option>
                <option value="auto">auto</option>
                <option value="openai">openai</option>
                <option value="anthropic">anthropic</option>
                <option value="gemini">gemini</option>
                <option value="ollama">ollama</option>
              </select>
            </div>
            <div>
              <label class="field-label">NVIDIA NIM Base URL</label>
              <input type="text" id="envNimBaseUrl" value="https://integrate.api.nvidia.com/v1">
            </div>
            <div>
              <label class="field-label">Availability Fallback Model</label>
              <input type="text" id="envNimFallbackModel" value="meta/llama-3.3-70b-instruct">
            </div>
          </div>

          <div class="grid-2" style="gap:14px;">
            <div>
              <label class="field-label">Primary NVIDIA NIM API Key (NVIDIA_NIM_API_KEY) <span id="nimGlobalMaskBadge" class="code-tag" style="margin-left:6px;">Unset</span></label>
              <input type="password" id="envNimGlobalKey" placeholder="nvapi-... (leave blank to keep existing)">
            </div>
            <div class="grid-2" style="gap:10px;">
              <div>
                <label class="field-label">Optional OpenAI Key</label>
                <input type="password" id="envOpenAiKey" placeholder="sk-... (optional)">
              </div>
              <div>
                <label class="field-label">Optional Gemini Key</label>
                <input type="password" id="envGeminiKey" placeholder="AIza... (optional)">
              </div>
            </div>
          </div>

          <div style="display:flex; justify-content:space-between; align-items:center;">
            <span class="panel-meta">API keys are stored securely in local .env (chmod 600, git-ignored) and never exposed in client logs.</span>
            <button class="btn btn-lime" onclick="saveFullEnvConfig()">
              <svg class="icon"><use href="#icon-check"/></svg> Save Gateway &amp; Per-Role Models to .env
            </button>
          </div>
        </div>

        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-cpu"/></svg> Per-Role NVIDIA NIM Model Matrix &amp; Live Endpoint Tester</span>
            <span class="panel-meta">Assign dedicated models &amp; optional per-role keys for all 13 specialists</span>
          </div>
          <div style="overflow-x:auto;">
            <table class="data-table">
              <thead>
                <tr>
                  <th>Role</th>
                  <th>Assigned NVIDIA NIM Model ID</th>
                  <th>Optional Role Key Override</th>
                  <th>Key Status</th>
                  <th>Endpoint Diagnostic</th>
                </tr>
              </thead>
              <tbody id="modelsMatrixBody"></tbody>
            </table>
          </div>
          <datalist id="nimCatalogList"></datalist>
        </div>
      </div>

      <!-- 09. ASSETS LIBRARY -->
      <div id="tab-assets" class="tab-view">
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-folder"/></svg> Centralized Agency Assets Library</span>
            <button class="btn btn-lime btn-sm" onclick="openUploadModal()">
              <svg class="icon"><use href="#icon-plus"/></svg> Upload Asset
            </button>
          </div>
          <p style="font-size:12px; color:var(--muted);">
            Upload real Cero screenshots, screen recordings, brand logos, or press brief documents. Attach them directly in Global Team Room, 1-on-1 Specialist Chats, or Card Studio.
          </p>
          <div class="grid-4" id="assetsGrid"></div>
        </div>
      </div>

      <!-- 10. SCHEDULES -->
      <div id="tab-schedule" class="tab-view">
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-clock"/></svg> Automated Operations &amp; Systemd Timers</span>
            <span class="panel-meta">state/schedules.json</span>
          </div>
          <div id="scheduleConfigContainer" style="display:flex; flex-direction:column; gap:14px;"></div>
        </div>
      </div>
    </main>
  </div>

  <!-- Slide-Over 1-on-1 Specialist Chat Drawer -->
  <div class="chat-modal" id="chatModal">
    <div class="chat-header">
      <div style="display:flex; align-items:center; gap:10px;">
        <span class="code-tag" id="chatAgentCode">AGT</span>
        <div>
          <b id="chatAgentName" style="font-size:14px;">Agent</b>
          <div id="chatAgentRole" style="font-family:var(--font-mono); font-size:10px; color:var(--lime);">Role</div>
          <div id="chatAgentModel" style="font-family:var(--font-mono); font-size:10px; color:var(--muted);">Model</div>
        </div>
      </div>
      <button class="btn btn-sm" onclick="closeChat()"><svg class="icon"><use href="#icon-x"/></svg></button>
    </div>
    <div class="chat-messages" id="chatMessages"></div>
    <div class="chat-input-bar">
      <div style="display:flex; align-items:center; gap:8px;">
        <svg class="icon"><use href="#icon-paperclip"/></svg>
        <span class="panel-meta">Attach Asset:</span>
        <select id="chatAssetSelect" style="padding:5px 8px; font-size:11px;">
          <option value="">None</option>
        </select>
      </div>
      <div style="display:flex; gap:8px;">
        <input type="text" id="chatInput" placeholder="Assign a task or ask a role-specific question..." onkeydown="if(event.key==='Enter') sendAgentMessage()">
        <button class="btn btn-lime" onclick="sendAgentMessage()">
          <svg class="icon"><use href="#icon-send"/></svg> Send
        </button>
      </div>
    </div>
  </div>

  <!-- Upload Asset Modal -->
  <div id="uploadAssetModal" class="modal-backdrop">
    <div class="modal-card">
      <h3 style="font-size:15px;">Upload Asset to Agency Library</h3>
      <div>
        <label class="field-label">Select File (Image, Video, Document)</label>
        <input type="file" id="assetFileInput">
      </div>
      <div>
        <label class="field-label">Category</label>
        <select id="assetCategory">
          <option value="cero">Cero Product Media</option>
          <option value="brand">Brand Logo &amp; Identity</option>
          <option value="screenshot">Product UI Screenshot</option>
          <option value="recording">Terminal / Screen Recording</option>
          <option value="document">PR &amp; Technical Document</option>
        </select>
      </div>
      <div>
        <label class="field-label">Description &amp; Usage Notes</label>
        <textarea id="assetDesc" rows="2" placeholder="e.g. Cero v2.2.3 asks-first confirmation screenshot"></textarea>
      </div>
      <div style="display:flex; justify-content:flex-end; gap:10px;">
        <button class="btn" onclick="closeUploadModal()">Cancel</button>
        <button class="btn btn-lime" onclick="submitUploadAsset()"><svg class="icon"><use href="#icon-plus"/></svg> Upload Asset</button>
      </div>
    </div>
  </div>

  <!-- Add Repo Modal -->
  <div id="addRepoModal" class="modal-backdrop">
    <div class="modal-card">
      <h3 style="font-size:15px;">Monitor GitHub or Local Repository</h3>
      <div>
        <label class="field-label">GitHub Remote URL</label>
        <input type="text" id="newRepoUrl" placeholder="https://github.com/owner/repo">
      </div>
      <div>
        <label class="field-label">Project Name</label>
        <input type="text" id="newRepoName" placeholder="e.g. Cero Terminal">
      </div>
      <div>
        <label class="field-label">Optional Local Path</label>
        <input type="text" id="newRepoLocalPath" placeholder="/home/user/projects/repo (optional)">
      </div>
      <div>
        <label class="field-label">Lifecycle Status</label>
        <select id="newRepoStatus">
          <option value="in_development">In Development (Build-in-public / Internal R&amp;D)</option>
          <option value="released">Released (Promote verified features)</option>
        </select>
      </div>
      <div>
        <label class="field-label">Marketing Angle &amp; Rules</label>
        <textarea id="newRepoAngle" rows="2" placeholder="e.g. Safe AI terminal that asks before executing"></textarea>
      </div>
      <div style="display:flex; justify-content:flex-end; gap:10px;">
        <button class="btn" onclick="closeAddRepoModal()">Cancel</button>
        <button class="btn btn-lime" onclick="submitAddRepo()"><svg class="icon"><use href="#icon-check"/></svg> Save &amp; Sync</button>
      </div>
    </div>
  </div>

  <script>
    let activeAgentId = null;
    let cachedAssets = [];
    let cachedPackages = {};
    let cachedRoster = [];
    let latestState = null;

    function escapeHtml(str) {
      if (str === null || str === undefined) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function updateClock() {
      const now = new Date();
      document.getElementById('liveClock').innerText = now.toLocaleTimeString() + ' Local';
    }
    setInterval(updateClock, 1000);
    updateClock();

    function switchTab(tabId, navEl) {
      document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
      document.querySelectorAll('.tab-view').forEach(v => v.classList.remove('active'));
      if (navEl) navEl.classList.add('active');
      else {
        const match = document.querySelector(`.nav-item[data-tab="${tabId}"]`);
        if (match) match.classList.add('active');
      }
      const view = document.getElementById(tabId);
      if (view) view.classList.add('active');

      const title = navEl ? navEl.getAttribute('data-title') : (document.querySelector(`.nav-item[data-tab="${tabId}"]`)?.getAttribute('data-title') || 'MAGNM Command Center');
      document.getElementById('headerPageTitle').innerText = title;

      if (tabId === 'tab-assets' || tabId === 'tab-campaigns' || tabId === 'tab-room') loadAssets();
      if (tabId === 'tab-room') loadGlobalMessages();
      if (tabId === 'tab-team') loadRoster();
      if (tabId === 'tab-models' && latestState) renderModelsStudio(latestState, true);
    }

    function copyPostingKit(pkgId) {
      const p = cachedPackages[pkgId];
      if (p && p.posting_kit_content) {
        navigator.clipboard.writeText(p.posting_kit_content);
        alert('Posting Kit copied to clipboard.');
      }
    }

    async function fetchStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        latestState = data;
        renderDashboard(data);
      } catch(e) {}
    }

    function renderDashboard(data) {
      const dot = document.getElementById('systemDot');
      const text = document.getElementById('systemText');
      const term = document.getElementById('terminalLog');

      if (data.current_job && data.current_job.running) {
        dot.className = 'status-dot busy';
        text.innerText = 'EXECUTING ' + data.current_job.job_name;
        term.innerText = data.current_job.output || 'Executing job...';
      } else {
        dot.className = 'status-dot';
        text.innerText = 'SYSTEM READY';
        if (data.current_job && data.current_job.output) term.innerText = data.current_job.output;
      }

      // Env & Gateway status telemetry
      const env = data.env_status || {};
      const readyCount = env.nvidia_nim_roles_configured || 0;
      const totalRoles = (data.models_matrix || []).length || 13;
      document.getElementById('nimReadyPill').innerText = `${readyCount}/${totalRoles}`;
      document.getElementById('kpiNimModels').innerText = `${readyCount} / ${totalRoles}`;

      const railGw = document.getElementById('railGatewayState');
      const topGwBtn = document.getElementById('topGatewayBadge');
      if (readyCount > 0 || env.openai || env.anthropic || env.gemini) {
        railGw.innerText = env.nvidia_nim_global ? 'NVIDIA NIM Active' : 'Keys Active';
        railGw.style.color = 'var(--mint)';
        topGwBtn.innerText = `NIM Studio (${readyCount}/${totalRoles} Ready)`;
        document.getElementById('kpiNimSub').innerText = `Provider: ${env.default_provider || 'nvidia_nim'}`;
      } else {
        railGw.innerText = 'No Key Set';
        railGw.style.color = 'var(--amber)';
        topGwBtn.innerText = 'Set NIM Key (.env)';
        document.getElementById('kpiNimSub').innerText = 'Paste NVIDIA_NIM_API_KEY in AI Studio';
      }
      document.getElementById('railChatGptState').innerText = env.chatgpt_cli ? 'Connected (CDP)' : 'Disconnected';
      document.getElementById('railChatGptState').style.color = env.chatgpt_cli ? 'var(--sky)' : 'var(--muted)';

      // Channels
      const chBody = document.getElementById('channelsTableBody');
      chBody.innerHTML = '';
      (data.channels || []).forEach(c => {
        chBody.innerHTML += `
          <tr>
            <td><b>${escapeHtml(c.platform)}</b></td>
            <td><a href="${escapeHtml(c.handle_url)}" target="_blank" style="color:var(--sky); text-decoration:none;">${escapeHtml(c.handle_url)}</a></td>
            <td><span class="badge badge-draft">${escapeHtml(c.status)}</span></td>
          </tr>
        `;
      });

      // Audit Log
      document.getElementById('activityLogBox').innerText = (data.activity_log || []).join('\n') || 'No activity logged yet.';

      // Research Radar
      const findings = (data.research_findings && data.research_findings.items) || [];
      document.getElementById('radarCountBadge').innerText = findings.length;
      document.getElementById('researchSourcePath').innerText = (data.research_findings && data.research_findings.file) || 'Run Market Scan';
      const rBody = document.getElementById('researchTableBody');
      rBody.innerHTML = '';
      findings.slice(0, 12).forEach(it => {
        const sc = (it.scoring && it.scoring.score) || 0;
        const badgeCls = sc >= 200 ? 'badge-approved' : (sc >= 100 ? 'badge-info' : 'badge-draft');
        const safeTitle = escapeHtml(it.title);
        rBody.innerHTML += `
          <tr>
            <td><span class="badge ${badgeCls}">${sc} / 625</span></td>
            <td>
              <div style="font-weight:600; margin-bottom:4px;">${safeTitle}</div>
              <a href="${escapeHtml(it.url)}" target="_blank" style="color:var(--sky); font-size:11px; text-decoration:none;">Open Source Link</a>
            </td>
            <td>
              <div>${escapeHtml(it.source)}</div>
              <div class="panel-meta">${escapeHtml(it.engagement)}</div>
            </td>
            <td>
              <button class="btn btn-lime btn-sm" onclick="pitchOpportunityToCmo('${encodeURIComponent(it.title)}', '${encodeURIComponent(it.url)}', ${sc})">
                <svg class="icon"><use href="#icon-send"/></svg> Pitch to @cmo
              </button>
            </td>
          </tr>
        `;
      });

      // Repos
      const repoDiv = document.getElementById('repoContainer');
      repoDiv.innerHTML = '';
      const reposList = (data.repos && data.repos.repositories) || [];
      document.getElementById('kpiRepos').innerText = reposList.length;
      reposList.forEach(r => {
        const badgeClass = r.status === 'released' ? 'badge-approved' : 'badge-warn';
        const commitsHtml = (r.recent_commits || []).slice(0, 4).map(c =>
          `<div style="margin-bottom:4px;"><b>${escapeHtml(c.hash)}</b> (${escapeHtml(c.date)}) - ${escapeHtml(c.message)}</div>`
        ).join('');
        repoDiv.innerHTML += `
          <div class="repo-card">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <b style="font-size:15px;">${escapeHtml(r.name)}</b>
              <span class="badge ${badgeClass}">${escapeHtml(r.status)} (v${escapeHtml(r.version)})</span>
            </div>
            <div class="panel-meta">Branch: <b>${escapeHtml(r.branch || 'main')}</b> | <a href="${escapeHtml(r.remote_url)}" target="_blank" style="color:var(--sky); text-decoration:none;">GitHub Remote</a></div>
            <div style="font-size:12px; color:#B6BCC8;"><b>Directive:</b> ${escapeHtml(r.marketing_angle || 'General')}</div>
            <div class="commit-box">${commitsHtml || 'No recent commits synced'}</div>
          </div>
        `;
      });

      // Editorial Plan
      const edBody = document.getElementById('editorialTableBody');
      edBody.innerHTML = '';
      (data.editorial_plan || []).forEach(row => {
        const stCls = row.status.includes('CANCEL') ? 'badge-danger' : (row.status.includes('AWAIT') ? 'badge-warn' : 'badge-draft');
        edBody.innerHTML += `
          <tr>
            <td><b>${escapeHtml(row.slot)}</b></td>
            <td>${escapeHtml(row.platforms)}</td>
            <td>${escapeHtml(row.topic)}</td>
            <td><span class="badge ${stCls}">${escapeHtml(row.status)}</span></td>
          </tr>
        `;
      });

      // PR Reference docs
      document.getElementById('crisisPlaybookBox').innerText = data.crisis_playbook || 'Playbook missing';
      document.getElementById('productFactsBox').innerText = data.product_facts || 'Product facts missing';

      // CEO Reviews
      const reviewDiv = document.getElementById('ceoReviewList');
      reviewDiv.innerHTML = '';
      let pendingCount = 0;
      const pkgs = data.packages || [];
      document.getElementById('kpiCampaigns').innerText = pkgs.length;

      pkgs.forEach(p => {
        cachedPackages[p.id] = p;
        const m = p.manifest || {};
        if (m.status !== 'published') pendingCount++;
        let badgeStyle = 'badge-draft';
        if (m.status === 'approved') badgeStyle = 'badge-approved';
        if (m.status === 'revision_required') badgeStyle = 'badge-revision';
        if (m.status === 'published') badgeStyle = 'badge-published';

        let mediaHtml = '';
        if (p.images && p.images.length > 0) {
          mediaHtml += p.images.map(img =>
            `<img src="/api/asset/${encodeURIComponent(p.id)}/${encodeURIComponent(img)}" class="review-img" title="${escapeHtml(img)}">`
          ).join('');
        }
        if (p.videos && p.videos.length > 0) {
          mediaHtml += p.videos.slice(0, 2).map(vid =>
            `<video src="/api/asset/${encodeURIComponent(p.id)}/${encodeURIComponent(vid)}" class="review-video" controls preload="metadata" title="${escapeHtml(vid)}"></video>`
          ).join('');
        }

        reviewDiv.innerHTML += `
          <div class="review-card">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
              <div>
                <b style="font-size:15px;">Campaign Package: ${escapeHtml(p.id)}</b>
                <span class="badge ${badgeStyle}" style="margin-left:10px;">${escapeHtml(m.status)}</span>
              </div>
              <div style="display:flex; gap:8px;">
                ${m.status !== 'approved' && m.status !== 'published' ? `
                  <button class="btn btn-lime" onclick="approvePackage('${escapeHtml(p.id)}')">
                    <svg class="icon"><use href="#icon-check"/></svg> Approve Package
                  </button>
                  <button class="btn btn-warning" onclick="requestRevision('${escapeHtml(p.id)}')">
                    <svg class="icon"><use href="#icon-edit"/></svg> Request Revision
                  </button>
                ` : m.status === 'approved' ? `
                  <button class="btn btn-mint" onclick="publishPackage('${escapeHtml(p.id)}')">
                    <svg class="icon"><use href="#icon-send"/></svg> Mark Published
                  </button>
                ` : `<span class="badge badge-published">Published by CEO</span>`}
              </div>
            </div>
            ${mediaHtml ? `<div><div class="panel-meta" style="margin-bottom:8px;">CREATIVE ASSETS &amp; RENDERED REELS</div><div class="review-gallery">${mediaHtml}</div></div>` : ''}
            ${p.posting_kit_content ? `
              <div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                  <span class="panel-meta">POSTING KIT &amp; APPROVED COPY</span>
                  <button class="btn btn-sm" onclick="copyPostingKit('${escapeHtml(p.id)}')">
                    <svg class="icon"><use href="#icon-copy"/></svg> Copy Kit
                  </button>
                </div>
                <div class="review-kit-box">${escapeHtml(p.posting_kit_content)}</div>
              </div>
            ` : ''}
          </div>
        `;
      });

      document.getElementById('pendingReviewBadge').innerText = pendingCount;
      document.getElementById('kpiPending').innerText = pendingCount;

      // Schedules
      const schedDiv = document.getElementById('scheduleConfigContainer');
      schedDiv.innerHTML = '';
      (data.schedules || []).forEach(s => {
        schedDiv.innerHTML += `
          <div style="display:flex; justify-content:space-between; align-items:center; background:#101319; padding:16px 20px; border-radius:6px; border:1px solid var(--border);">
            <div>
              <b style="font-size:14px;">${escapeHtml(s.name)}</b>
              <div class="panel-meta" style="margin-top:3px;">${escapeHtml(s.description)}</div>
              <div class="panel-meta" style="margin-top:5px;">
                Cadence: ${s.interval_hours ? 'Every ' + s.interval_hours + 'h' : escapeHtml(s.cron)} | Last run: ${escapeHtml(s.last_run || 'Never')}
              </div>
            </div>
            <button class="btn" onclick="toggleSchedule('${escapeHtml(s.id)}')">
              ${s.enabled ? '<span class="badge badge-ok">Active</span>' : '<span class="badge badge-draft">Paused</span>'}
            </button>
          </div>
        `;
      });

      // Populate AI Models studio if not currently typing in it
      if (document.getElementById('tab-models').classList.contains('active')) {
        // Only populate on explicit switch or initial load so user edits aren't overwritten during 3s poll
      } else {
        renderModelsStudio(data, false);
      }
    }

    function renderModelsStudio(data, forceInputs) {
      const env = data.env_status || {};
      document.getElementById('envStudioMeta').innerText = env.has_env_file ? '.env Loaded (chmod 600)' : '.env Missing';
      const maskBadge = document.getElementById('nimGlobalMaskBadge');
      maskBadge.innerText = env.nvidia_nim_global ? env.nvidia_nim_global_masked : 'Not Set';
      maskBadge.style.color = env.nvidia_nim_global ? 'var(--mint)' : 'var(--amber)';

      if (forceInputs || !document.getElementById('envNimBaseUrl').dataset.dirty) {
        document.getElementById('envDefaultProvider').value = env.default_provider || 'nvidia_nim';
        if (env.nvidia_nim_base_url) document.getElementById('envNimBaseUrl').value = env.nvidia_nim_base_url;
        if (env.nvidia_nim_fallback_model) document.getElementById('envNimFallbackModel').value = env.nvidia_nim_fallback_model;
      }

      const dList = document.getElementById('nimCatalogList');
      dList.innerHTML = (data.nim_catalog_suggestions || []).map(m => `<option value="${escapeHtml(m)}">`).join('');

      const tbody = document.getElementById('modelsMatrixBody');
      if (!forceInputs && tbody.children.length > 0) return;

      tbody.innerHTML = '';
      (data.models_matrix || []).forEach(r => {
        const keyBadge = r.has_key
          ? `<span class="badge badge-ok">${escapeHtml(r.key_source)} (${escapeHtml(r.masked_key)})</span>`
          : `<span class="badge badge-warn">Missing Key</span>`;

        tbody.innerHTML += `
          <tr>
            <td>
              <div style="display:flex; align-items:center; gap:8px;">
                <span class="code-tag">${escapeHtml(r.code)}</span>
                <div>
                  <b>${escapeHtml(r.name)}</b>
                  <div class="panel-meta">${escapeHtml(r.specialization)}</div>
                </div>
              </div>
            </td>
            <td>
              <input type="text" list="nimCatalogList" class="role-model-input" data-env="${escapeHtml(r.model_env)}" value="${escapeHtml(r.model)}">
            </td>
            <td>
              <input type="password" class="role-key-input" data-env="${escapeHtml(r.key_env)}" placeholder="Inherit global key">
            </td>
            <td>${keyBadge}</td>
            <td>
              <button class="btn btn-sm" onclick="testRoleModel('${escapeHtml(r.role_id)}', this)">
                <svg class="icon"><use href="#icon-activity"/></svg> Test Live
              </button>
              <div class="panel-meta" id="testRes-${escapeHtml(r.role_id)}" style="margin-top:4px; max-width:220px;"></div>
            </td>
          </tr>
        `;
      });
    }

    async function saveFullEnvConfig() {
      const updates = {
        AI_DEFAULT_PROVIDER: document.getElementById('envDefaultProvider').value,
        NVIDIA_NIM_BASE_URL: document.getElementById('envNimBaseUrl').value.trim(),
        NIM_FALLBACK_MODEL: document.getElementById('envNimFallbackModel').value.trim()
      };
      const gk = document.getElementById('envNimGlobalKey').value.trim();
      if (gk) updates['NVIDIA_NIM_API_KEY'] = gk;
      const ok = document.getElementById('envOpenAiKey').value.trim();
      if (ok) updates['OPENAI_API_KEY'] = ok;
      const gmk = document.getElementById('envGeminiKey').value.trim();
      if (gmk) updates['GEMINI_API_KEY'] = gmk;

      document.querySelectorAll('.role-model-input').forEach(inp => {
        const k = inp.getAttribute('data-env');
        if (k && inp.value.trim()) updates[k] = inp.value.trim();
      });
      document.querySelectorAll('.role-key-input').forEach(inp => {
        const k = inp.getAttribute('data-env');
        if (k && inp.value.trim()) updates[k] = inp.value.trim();
      });

      const res = await fetch('/api/save_env_config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ updates })
      });
      const out = await res.json();
      if (out.success) {
        alert(`Saved ${out.saved_keys.length} settings cleanly to .env.`);
        document.getElementById('envNimGlobalKey').value = '';
        await fetchStatus();
        renderModelsStudio(latestState, true);
      } else {
        alert('Error saving .env: ' + (out.error || 'Unknown'));
      }
    }

    async function testRoleModel(roleId, btn) {
      const outEl = document.getElementById('testRes-' + roleId);
      outEl.innerText = 'Testing endpoint...';
      outEl.style.color = 'var(--amber)';
      try {
        const res = await fetch('/api/test_ai', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ role_id: roleId })
        });
        const data = await res.json();
        if (data.success) {
          const fbNote = data.fallback_used ? ` [Fallback -> ${data.model_used}]` : '';
          outEl.innerText = `[OK] ${data.latency_ms}ms | ${data.model_used}${fbNote}`;
          outEl.style.color = 'var(--mint)';
        } else {
          outEl.innerText = `[FAIL] ${data.error}`;
          outEl.style.color = 'var(--danger)';
        }
      } catch(e) {
        outEl.innerText = `[ERROR] ${e}`;
        outEl.style.color = 'var(--danger)';
      }
    }

    async function pitchOpportunityToCmo(encTitle, encUrl, score) {
      const title = decodeURIComponent(encTitle);
      const url = decodeURIComponent(encUrl);
      const msg = `@cmo Opportunity Radar hit (${score}/625): "${title}" (${url}). Evaluate Cero v2.2.3 positioning and delegate response or post tasks if worth pursuing.`;
      switchTab('tab-room');
      const box = document.getElementById('globalMessagesBox');
      box.innerHTML += `<div class="panel-meta" style="color:var(--lime);">Escalating opportunity to @cmo...</div>`;
      await fetch('/api/messages', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ text: msg, sender: 'CEO' })
      });
      loadGlobalMessages();
    }

    async function renderStudioCard() {
      const payload = {
        post_id: document.getElementById('cardPostId').value.trim(),
        field: document.getElementById('cardField').value,
        kicker: document.getElementById('cardKicker').value.trim(),
        big: document.getElementById('cardBigLines').value,
        serif: document.getElementById('cardSerif').value.trim(),
        foot: document.getElementById('cardFoot').value.trim(),
        asset_id: document.getElementById('cardAssetSelect').value,
        sizes: ['4x5', '1x1', '16x9', '9x16']
      };
      const res = await fetch('/api/cards/render', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data.error) { alert('Render error: ' + data.error); return; }

      const wrap = document.getElementById('cardRenderPreview');
      wrap.style.display = 'flex';
      document.getElementById('cardRenderStatus').innerText = `Rendered ${(data.rendered_files || []).length} formats into outputs/posts/${data.post_id}/ and staged for CEO Review.`;
      const gal = document.getElementById('cardRenderGallery');
      gal.innerHTML = (data.rendered_files || []).map(f =>
        `<img src="/api/asset/${encodeURIComponent(data.post_id)}/${encodeURIComponent(f)}" class="review-img" title="${escapeHtml(f)}">`
      ).join('');
      fetchStatus();
    }

    async function draftPrReply() {
      const scenario = document.getElementById('prScenario').value;
      const severity = document.getElementById('prSeverity').value;
      const ctx = document.getElementById('prContextInput').value.trim();
      if (!ctx) { alert('Enter an incoming comment, press query, or client brief first.'); return; }

      const btn = document.getElementById('btnDraftPr');
      btn.disabled = true;
      btn.innerText = 'Drafting Response...';
      try {
        const res = await fetch('/api/pr/draft_reply', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ scenario, severity, source_context: ctx })
        });
        const data = await res.json();
        document.getElementById('prDraftOutputBox').style.display = 'flex';
        document.getElementById('prDraftModelTag').innerText = `${data.provider || 'gateway'} | ${data.model_used || 'unknown'}`;
        document.getElementById('prDraftText').innerText = data.draft || data.error || 'No response';
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<svg class="icon"><use href="#icon-send"/></svg> Generate Grounded Draft';
      }
    }

    function copyPrDraft() {
      navigator.clipboard.writeText(document.getElementById('prDraftText').innerText);
      alert('PR draft copied to clipboard.');
    }

    // Assets Library
    async function loadAssets() {
      try {
        const res = await fetch('/api/assets');
        cachedAssets = await res.json();
        document.getElementById('assetsCountBadge').innerText = cachedAssets.length;
        const grid = document.getElementById('assetsGrid');
        grid.innerHTML = '';

        const chatSel = document.getElementById('chatAssetSelect');
        const roomSel = document.getElementById('roomAssetSelect');
        const cardSel = document.getElementById('cardAssetSelect');
        if (chatSel) chatSel.innerHTML = '<option value="">None</option>';
        if (roomSel) roomSel.innerHTML = '<option value="">Attach Asset (None)</option>';
        if (cardSel) cardSel.innerHTML = '<option value="">None (Typography Only)</option>';

        cachedAssets.forEach(a => {
          const opt = `<option value="${escapeHtml(a.id)}">${escapeHtml(a.name)} (${escapeHtml(a.file_group)})</option>`;
          if (chatSel) chatSel.innerHTML += opt;
          if (roomSel) roomSel.innerHTML += opt;
          if (cardSel && a.file_group === 'image') cardSel.innerHTML += opt;

          let previewContent = '';
          if (a.file_group === 'image') {
            previewContent = `<img src="/api/asset_file/${encodeURIComponent(a.id)}" class="asset-preview-img" alt="${escapeHtml(a.name)}">`;
          } else if (a.file_group === 'video') {
            previewContent = `<svg class="icon icon-xl" style="color:var(--muted);"><use href="#icon-film"/></svg>`;
          } else {
            previewContent = `<svg class="icon icon-xl" style="color:var(--muted);"><use href="#icon-file"/></svg>`;
          }

          grid.innerHTML += `
            <div class="asset-item-card">
              <div class="asset-preview-box">${previewContent}</div>
              <div>
                <b style="font-size:13px;" title="${escapeHtml(a.name)}">${escapeHtml(a.name.length > 24 ? a.name.slice(0, 22) + '...' : a.name)}</b>
                <div class="panel-meta">${escapeHtml(a.file_group.toUpperCase())} · ${escapeHtml(a.size)}</div>
                <div style="font-size:11px; color:var(--muted); margin-top:4px;">${escapeHtml(a.description || '')}</div>
              </div>
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <span class="badge badge-draft">${escapeHtml(a.category)}</span>
                <button class="btn btn-danger btn-sm" onclick="deleteAsset('${escapeHtml(a.id)}')">
                  <svg class="icon"><use href="#icon-trash"/></svg> Delete
                </button>
              </div>
            </div>
          `;
        });
      } catch(e) {}
    }

    function openUploadModal() { document.getElementById('uploadAssetModal').style.display = 'flex'; }
    function closeUploadModal() { document.getElementById('uploadAssetModal').style.display = 'none'; }

    async function submitUploadAsset() {
      const fileInput = document.getElementById('assetFileInput');
      const category = document.getElementById('assetCategory').value;
      const description = document.getElementById('assetDesc').value;
      if (!fileInput.files || fileInput.files.length === 0) { alert('Select a file first.'); return; }
      const file = fileInput.files[0];
      const reader = new FileReader();
      reader.onload = async function() {
        const b64 = reader.result.split(',')[1];
        await fetch('/api/assets/upload', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ filename: file.name, content_base64: b64, category, description })
        });
        closeUploadModal();
        loadAssets();
      };
      reader.readAsDataURL(file);
    }

    async function deleteAsset(id) {
      if (!confirm('Delete this asset from repository?')) return;
      await fetch('/api/assets/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id })
      });
      loadAssets();
    }

    // Global Team Room
    async function loadGlobalMessages() {
      try {
        const res = await fetch('/api/messages');
        const msgs = await res.json();
        const box = document.getElementById('globalMessagesBox');
        box.innerHTML = '';

        msgs.forEach(m => {
          let bubbleCls = 'chat-bubble';
          if (m.sender === 'CEO') bubbleCls += ' user';
          else if (m.sender === 'cmo') bubbleCls += ' cmo';
          else if (m.capability_redirect) bubbleCls += ' redirect';
          else bubbleCls += ' agent';

          const modelTag = m.model_used ? `<span class="badge badge-draft">${escapeHtml(m.model_used)}</span>` : '';
          box.innerHTML += `
            <div style="display:flex; flex-direction:column; ${m.sender==='CEO'?'align-items:flex-end':'align-items:flex-start'}">
              <div style="font-family:var(--font-mono); font-size:10px; color:var(--muted); margin-bottom:4px; display:flex; align-items:center; gap:6px;">
                <span class="code-tag">${escapeHtml(m.sender_code || m.sender.toUpperCase())}</span>
                <b>${escapeHtml(m.sender_name)}</b>
                ${modelTag}
                <span>${new Date(m.timestamp).toLocaleTimeString()}</span>
              </div>
              <div class="${bubbleCls}">${escapeHtml(m.text)}</div>
            </div>
          `;
        });
        box.scrollTop = box.scrollHeight;
      } catch(e) {}
    }

    function insertTag(tag) {
      const inp = document.getElementById('globalMsgInput');
      inp.value += tag;
      inp.focus();
    }

    async function sendGlobalMessage() {
      const input = document.getElementById('globalMsgInput');
      const text = input.value.trim();
      const sel = document.getElementById('roomAssetSelect');
      const attached = sel && sel.value ? [sel.value] : [];
      if (!text) return;
      input.value = '';

      const box = document.getElementById('globalMessagesBox');
      box.innerHTML += `<div class="panel-meta" style="color:var(--lime);">Routing message and orchestration replies...</div>`;
      box.scrollTop = box.scrollHeight;

      await fetch('/api/messages', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ text, sender: 'CEO', attached_assets: attached })
      });
      loadGlobalMessages();
    }

    // Team Roster & Specialist Chat
    async function loadRoster() {
      try {
        const res = await fetch('/api/roster');
        cachedRoster = await res.json();
        const container = document.getElementById('rosterContainer');
        container.innerHTML = '';

        cachedRoster.forEach(m => {
          const mInfo = m.model_info || {};
          const canHtml = (m.can || []).slice(0, 2).map(c => `<div class="panel-meta">+ ${escapeHtml(c)}</div>`).join('');
          const cannotHtml = (m.cannot || []).slice(0, 1).map(c => `<div class="panel-meta" style="color:var(--danger);">- Cannot: ${escapeHtml(c)}</div>`).join('');

          container.innerHTML += `
            <div class="agent-card">
              <div style="display:flex; flex-direction:column; gap:8px;">
                <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:8px;">
                  <div style="display:flex; align-items:center; gap:10px;">
                    <span class="code-tag" style="font-size:12px; padding:4px 8px;">${escapeHtml(m.code)}</span>
                    <div>
                      <b style="font-size:14px;">${escapeHtml(m.name)}</b>
                      <div style="font-family:var(--font-mono); font-size:10px; color:var(--lime);">${escapeHtml(m.role)}</div>
                    </div>
                  </div>
                </div>
                <div style="font-size:12px; color:#B5BCC9; line-height:1.45;">${escapeHtml(m.description)}</div>
                <div style="display:flex; gap:6px; flex-wrap:wrap;">
                  <span class="badge badge-info">${escapeHtml(mInfo.model || 'NIM Default')}</span>
                  <span class="badge ${mInfo.has_key ? 'badge-ok' : 'badge-draft'}">${mInfo.has_key ? 'Key Ready' : 'Fallback'}</span>
                </div>
                <div style="border-top:1px solid var(--border); padding-top:8px;">
                  ${canHtml}
                  ${cannotHtml}
                  <div class="panel-meta" style="margin-top:4px;">Latest file: ${escapeHtml(m.latest_output)}</div>
                </div>
              </div>
              <button class="btn btn-lime" style="width:100%; justify-content:center;" onclick="openAgentChat('${escapeHtml(m.id)}')">
                <svg class="icon"><use href="#icon-send"/></svg> Chat with @${escapeHtml(m.id)}
              </button>
            </div>
          `;
        });
      } catch(e) {}
    }

    async function openAgentChat(agentId) {
      activeAgentId = agentId;
      const m = cachedRoster.find(r => r.id === agentId) || { id: agentId, name: agentId, role: '', code: agentId.toUpperCase() };
      const mInfo = m.model_info || {};
      document.getElementById('chatAgentName').innerText = m.name;
      document.getElementById('chatAgentRole').innerText = m.role;
      document.getElementById('chatAgentCode').innerText = m.code || 'AGT';
      document.getElementById('chatAgentModel').innerText = `Model: ${mInfo.model || 'NIM Default'} (${mInfo.has_key ? 'Live NIM' : 'Fallback Mode'})`;
      document.getElementById('chatModal').classList.add('open');

      const res = await fetch('/api/chat_history/' + encodeURIComponent(agentId));
      const history = await res.json();
      renderChatMessages(history);
      loadAssets();
    }

    function closeChat() {
      document.getElementById('chatModal').classList.remove('open');
      activeAgentId = null;
    }

    function renderChatMessages(history) {
      const container = document.getElementById('chatMessages');
      container.innerHTML = '';
      if (!history || history.length === 0) {
        container.innerHTML = '<div class="panel-meta" style="text-align:center; margin-top:40px;">No conversation history yet. Direct this specialist or assign a task.</div>';
        return;
      }
      history.forEach(m => {
        const cls = m.sender === 'user' ? 'user' : (m.capability_redirect ? 'redirect' : 'agent');
        let extra = '';
        if (m.capability_redirect && m.redirect_to) {
          extra = `<div style="margin-top:8px;"><button class="btn btn-sm" onclick="openAgentChat('${escapeHtml(m.redirect_to)}')">Switch chat to @${escapeHtml(m.redirect_to)}</button></div>`;
        } else if (m.sender !== 'user' && m.model_used) {
          extra = `<div class="panel-meta" style="margin-top:6px; font-size:10px;">Engine: ${escapeHtml(m.model_used)} (${escapeHtml(m.provider || '')})</div>`;
        }
        container.innerHTML += `<div class="chat-bubble ${cls}">${escapeHtml(m.text)}${extra}</div>`;
      });
      container.scrollTop = container.scrollHeight;
    }

    async function sendAgentMessage() {
      const input = document.getElementById('chatInput');
      const text = input.value.trim();
      const assetSelect = document.getElementById('chatAssetSelect');
      const attachedAssets = assetSelect.value ? [assetSelect.value] : [];
      if (!text || !activeAgentId) return;
      input.value = '';

      const container = document.getElementById('chatMessages');
      container.innerHTML += `<div class="chat-bubble user">${escapeHtml(text)}${attachedAssets.length ? ' [Asset Attached]' : ''}</div>`;
      container.innerHTML += `<div class="chat-bubble agent" id="thinkingBubble">Processing via AI Gateway...</div>`;
      container.scrollTop = container.scrollHeight;

      try {
        const res = await fetch('/api/agent_chat', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ agent_id: activeAgentId, message: text, attached_assets: attachedAssets })
        });
        const data = await res.json();
        const tb = document.getElementById('thinkingBubble');
        if (tb) tb.remove();

        if (data.history) renderChatMessages(data.history);
        if (!data.success && data.error) {
          container.innerHTML += `<div class="chat-bubble redirect">${escapeHtml(data.error)}</div>`;
          container.scrollTop = container.scrollHeight;
        }
        if (data.escalated_to_cmo) {
          container.innerHTML += `<div class="panel-meta" style="color:var(--sky); text-align:center;">[Task escalated to @cmo in Global Team Room]</div>`;
          container.scrollTop = container.scrollHeight;
        }
      } catch(e) {
        const tb = document.getElementById('thinkingBubble');
        if (tb) tb.innerText = 'Error: ' + e;
      }
    }

    function openAddRepoModal() { document.getElementById('addRepoModal').style.display = 'flex'; }
    function closeAddRepoModal() { document.getElementById('addRepoModal').style.display = 'none'; }

    async function submitAddRepo() {
      const remote_url = document.getElementById('newRepoUrl').value.trim();
      const name = document.getElementById('newRepoName').value.trim();
      const local_path = document.getElementById('newRepoLocalPath').value.trim();
      const status = document.getElementById('newRepoStatus').value;
      const marketing_angle = document.getElementById('newRepoAngle').value.trim();
      if (!remote_url || !name) { alert('URL and Name are required.'); return; }
      await fetch('/api/repos/add', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ remote_url, name, local_path, status, marketing_angle })
      });
      closeAddRepoModal();
      fetchStatus();
    }

    async function runJob(job) {
      await fetch('/api/run', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ job }) });
      fetchStatus();
    }
    async function approvePackage(post_id) {
      if (!confirm(`Confirm CEO approval for "${post_id}"?`)) return;
      await fetch('/api/approve', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ post_id }) });
      fetchStatus();
    }
    async function requestRevision(post_id) {
      const notes = prompt('Enter revision instructions for @writer / @reviewer:', 'Verify claims and tighten tone.');
      if (notes === null) return;
      await fetch('/api/request_revision', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ post_id, notes }) });
      fetchStatus();
    }
    async function publishPackage(post_id) {
      if (!confirm(`Confirm "${post_id}" is published to social handles?`)) return;
      await fetch('/api/publish', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ post_id }) });
      fetchStatus();
    }
    async function toggleSchedule(id) {
      await fetch('/api/schedule/toggle', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ id }) });
      fetchStatus();
    }

    setInterval(fetchStatus, 3000);
    fetchStatus();
    loadRoster();
    loadAssets();
    loadGlobalMessages();
  </script>
</body>
</html>
"""
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    print("============================================================")
    print("MAGNM PR & SOCIAL AGENCY COMMAND CENTER (v5 - NVIDIA NIM)")
    print("============================================================")
    print(f"Starting multi-threaded server on http://{HOST}:{PORT}")
    server = ThreadingHTTPServer((HOST, PORT), CommandCenterHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
