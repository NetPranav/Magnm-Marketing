#!/usr/bin/env python3
"""Repository management, local/GitHub sync, shipped feed, and fact-drafting hooks."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import agents
import agent_runtime
import ai_gateway
import db
import events
import paths
import product_rules

_MEDIA_EXTENSIONS = {".mp4", ".gif", ".png", ".jpg", ".webm"}
_MEDIA_MAX_FILES = 30
_MEDIA_MAX_BYTES = 50 * 1024 * 1024
_README_NAMES = ("README.md", "readme.md", "README.MD", "README", "README.txt")
_README_MAX_CHARS = 6000
_FACT_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="magnm-fact-draft")


class RateLimitError(RuntimeError):
    """Raised when GitHub API returns 403/429 with X-RateLimit-Remaining=0."""


def _now_utc():
    return datetime.now(timezone.utc)


def _now_iso():
    return _now_utc().isoformat()


def _slugify(text):
    slug = re.sub(r"[^a-z0-9-]+", "-", str(text or "").strip().lower()).strip("-")
    return slug or "repo"


def _repos_config_read_path():
    data_path = paths.data("config", "repos.json")
    if os.path.exists(data_path):
        return data_path
    res_path = paths.resource("config", "repos.json")
    return res_path if os.path.exists(res_path) else data_path


def _repos_config_write_path():
    return paths.data("config", "repos.json")


def _rules_path():
    cand = paths.data("config", "product_rules.json")
    if os.path.exists(cand):
        return cand
    fb = paths.resource("config", "product_rules.json")
    return fb if os.path.exists(fb) else cand


def _confidential_product_ids():
    try:
        rp = _rules_path()
        if os.path.exists(rp):
            return product_rules.confidential_product_ids(rp)
    except Exception:
        pass
    return set()


def parse_github_slug(value):
    """Extract 'owner/repo' from a GitHub URL or slug, or return None."""
    raw = str(value or "").strip()
    if not raw:
        return None

    # Handle SSH git@github.com:owner/repo.git
    ssh_match = re.match(r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$", raw, re.IGNORECASE)
    if ssh_match:
        owner, repo = ssh_match.group(1).strip(), ssh_match.group(2).strip()
        if owner and repo and ".." not in owner and ".." not in repo:
            return f"{owner}/{repo}"
        return None

    if "://" in raw:
        parsed = urllib.parse.urlparse(raw)
        host = (parsed.netloc or "").lower()
        if host not in ("github.com", "www.github.com"):
            return None
        path = parsed.path.strip("/")
    else:
        path = raw.strip("/")

    if path.endswith(".git"):
        path = path[:-4]
    parts = [p for p in path.split("/") if p]
    if len(parts) != 2:
        return None
    owner, repo = parts[0], parts[1]
    if any(not p or p in (".", "..") or any(c.isspace() for c in p) for p in (owner, repo)):
        return None
    return f"{owner}/{repo}"


def migrate_repo_entry(entry, confidential_ids=None):
    """Normalize a legacy or current repos.json entry into the canonical schema."""
    if not isinstance(entry, dict):
        return {}
    conf_ids = confidential_ids if confidential_ids is not None else _confidential_product_ids()

    name = str(entry.get("name") or entry.get("product") or entry.get("id") or "").strip()
    repo_id = str(entry.get("id") or _slugify(name)).strip()
    product_id = str(
        entry.get("product_id") or _slugify(entry.get("product") or name or repo_id)
    ).strip()

    local_path = (entry.get("local_path") or "").strip() or None
    github = parse_github_slug(entry.get("github")) or parse_github_slug(entry.get("remote_url"))

    raw_kind = str(entry.get("kind") or "").strip().lower()
    if raw_kind in ("local", "github"):
        kind = raw_kind
    elif local_path:
        kind = "local"
    elif github:
        kind = "github"
    else:
        kind = "local"

    branch = (entry.get("branch") or entry.get("primary_branch") or "").strip() or None
    if "confidential" in entry:
        confidential = bool(entry["confidential"])
    else:
        confidential = product_id in conf_ids

    out = {
        "id": repo_id,
        "name": name or repo_id,
        "product_id": product_id,
        "kind": kind,
        "confidential": confidential,
        "status": str(entry.get("status") or "in_development").strip(),
    }
    if local_path:
        out["local_path"] = local_path
    if github:
        out["github"] = github
    if branch:
        out["branch"] = branch
    if entry.get("version"):
        out["version"] = str(entry["version"])
    if entry.get("marketing_angle"):
        out["marketing_angle"] = str(entry["marketing_angle"])
    return out


def _read_raw_repos_config(path=None):
    cfg_path = path or _repos_config_read_path()
    if not os.path.exists(cfg_path):
        return []
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return list(data.get("repositories") or [])
    if isinstance(data, list):
        return list(data)
    return []


def _write_repos_config(repos_list, path=None):
    cfg_path = path or _repos_config_write_path()
    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    tmp_path = cfg_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({"repositories": repos_list}, f, indent=2)
    os.replace(tmp_path, cfg_path)


def migrate_repos_config(path=None):
    """Migrate repos.json entries in-place to the canonical schema and return them."""
    read_path = path or _repos_config_read_path()
    write_path = path or _repos_config_write_path()
    raw_list = _read_raw_repos_config(read_path)
    conf_ids = _confidential_product_ids()
    migrated = [migrate_repo_entry(r, confidential_ids=conf_ids) for r in raw_list]
    if migrated != raw_list or (os.path.exists(read_path) and not os.path.exists(write_path)):
        _write_repos_config(migrated, write_path)
    return migrated


def list_repos():
    """Return all configured repositories (migrated to canonical schema)."""
    raw_list = _read_raw_repos_config()
    conf_ids = _confidential_product_ids()
    return [migrate_repo_entry(r, confidential_ids=conf_ids) for r in raw_list]


def get_repo(repo_id):
    """Return a single repository config dict by id or product_id, or None."""
    target = str(repo_id or "").strip().lower()
    if not target:
        return None
    for r in list_repos():
        if (r.get("id") or "").lower() == target or (r.get("product_id") or "").lower() == target:
            return r
    return None


def _get_github_token():
    """Read GITHUB_TOKEN from environment or DATA_ROOT/.env (never prints or logs it)."""
    env_tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if env_tok:
        return env_tok
    env_path = paths.data(".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s and not s.startswith("#") and "=" in s:
                        k, v = s.split("=", 1)
                        if k.strip() == "GITHUB_TOKEN":
                            val = v.strip()
                            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                                val = val[1:-1]
                            if val:
                                return val
        except OSError:
            pass
    try:
        loaded = ai_gateway.load_env()
        tok = (loaded.get("GITHUB_TOKEN") or "").strip()
        if tok:
            return tok
    except Exception:
        pass
    return ""


def _header_get_ci(headers, key):
    if not headers:
        return None
    target = key.lower()
    for k, v in headers.items():
        if str(k).lower() == target:
            return v
    return None


def _format_rate_limit_message(headers):
    reset_raw = _header_get_ci(headers, "X-RateLimit-Reset")
    if reset_raw:
        try:
            ts = int(str(reset_raw).strip())
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return f"rate limited until {dt.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        except (TypeError, ValueError, OSError):
            pass
    return "rate limited until reset"


def _github_request(path_or_url, *, accept="application/vnd.github+json", raw=False):
    """Perform a GET request against the GitHub REST API using urllib."""
    url = path_or_url if path_or_url.startswith("http") else f"https://api.github.com{path_or_url}"
    headers = {
        "Accept": accept,
        "User-Agent": "Magnm-Studio/1.0",
    }
    token = _get_github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if raw:
                return resp.status, body
            return resp.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as e:
        remaining = _header_get_ci(e.headers, "X-RateLimit-Remaining")
        if e.code in (403, 429) and str(remaining or "").strip() == "0":
            raise RateLimitError(_format_rate_limit_message(e.headers)) from e
        raise


def add_repo(spec):
    """Validate and add a local or GitHub repository to config/repos.json."""
    if not isinstance(spec, dict):
        raise ValueError("Repository spec must be an object")

    raw_kind = str(spec.get("kind") or "").strip().lower()
    local_raw = (spec.get("local_path") or "").strip()
    github_raw = spec.get("github") or spec.get("remote_url") or ""
    github_slug = parse_github_slug(github_raw)

    if raw_kind in ("local", "github"):
        kind = raw_kind
    elif local_raw:
        kind = "local"
    elif github_slug:
        kind = "github"
    else:
        raise ValueError("kind must be 'local' or 'github'")

    name = str(spec.get("name") or "").strip()
    if not name:
        if kind == "github" and github_slug:
            name = github_slug.split("/")[-1]
        elif kind == "local" and local_raw:
            name = os.path.basename(os.path.abspath(os.path.expanduser(local_raw)))
    if not name:
        raise ValueError("Repository name is required")

    repo_id = _slugify(name)
    existing = list_repos()
    for r in existing:
        if (r.get("id") or "").lower() == repo_id:
            raise ValueError(f"Duplicate repository id '{repo_id}'")

    branch = (spec.get("branch") or "").strip() or None
    local_path = None
    if kind == "local":
        if not local_raw:
            raise ValueError("local_path is required for local repositories")
        local_path = os.path.abspath(os.path.expanduser(local_raw))
        if not os.path.isdir(local_path):
            raise ValueError(f"Local path does not exist: {local_path}")
        proc = subprocess.run(
            ["git", "-C", local_path, "rev-parse", "--git-dir"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0:
            raise ValueError(f"Not a valid git repository: {local_path}")
        real_local = os.path.realpath(local_path)
        for r in existing:
            if r.get("local_path") and os.path.realpath(r["local_path"]) == real_local:
                raise ValueError(f"Duplicate repository local_path '{local_path}'")
    else:
        if not github_slug:
            raise ValueError("Valid GitHub repository ('owner/repo') is required")
        for r in existing:
            if (r.get("github") or "").lower() == github_slug.lower() and r.get("kind") == "github":
                raise ValueError(f"Duplicate GitHub repository '{github_slug}'")
        try:
            status_code, info = _github_request(f"/repos/{github_slug}")
        except RateLimitError as e:
            raise ValueError(f"GitHub API {e}") from e
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise ValueError(
                    f"GitHub repository '{github_slug}' not found (404). If private, configure GITHUB_TOKEN."
                ) from e
            if e.code == 401:
                raise ValueError(
                    f"GitHub authentication failed (401) for '{github_slug}'. Check GITHUB_TOKEN."
                ) from e
            if e.code == 403:
                raise ValueError(f"GitHub access forbidden (403) for '{github_slug}'.") from e
            raise ValueError(f"GitHub API error ({e.code}) for '{github_slug}'") from e
        except urllib.error.URLError as e:
            raise ValueError(f"Could not reach GitHub API: {e.reason}") from e

        if status_code != 200:
            raise ValueError(f"Unexpected GitHub status {status_code} for '{github_slug}'")
        if not branch and isinstance(info, dict):
            branch = info.get("default_branch") or None

    product_id = str(spec.get("product_id") or repo_id).strip() or repo_id
    conf_ids = _confidential_product_ids()
    if "confidential" in spec:
        confidential = bool(spec["confidential"])
    else:
        confidential = product_id in conf_ids

    entry = {
        "id": repo_id,
        "name": name,
        "product_id": product_id,
        "kind": kind,
        "confidential": confidential,
        "status": str(spec.get("status") or "in_development").strip(),
    }
    if local_path:
        entry["local_path"] = local_path
    if github_slug:
        entry["github"] = github_slug
    if branch:
        entry["branch"] = branch
    if spec.get("version"):
        entry["version"] = str(spec["version"])
    if spec.get("marketing_angle"):
        entry["marketing_angle"] = str(spec["marketing_angle"])

    existing.append(entry)
    _write_repos_config(existing)
    return entry


def remove_repo(repo_id):
    """Remove a repository from config/repos.json by id. Return True if removed."""
    target = str(repo_id or "").strip().lower()
    existing = list_repos()
    kept = [r for r in existing if (r.get("id") or "").lower() != target]
    if len(kept) == len(existing):
        return False
    _write_repos_config(kept)
    return True


def _run_git(args, cwd):
    proc = subprocess.run(
        ["git", "-C", cwd] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _detect_local_version(repo_path, tags, fallback="unknown"):
    pkg = os.path.join(repo_path, "package.json")
    if os.path.isfile(pkg):
        try:
            with open(pkg, "r", encoding="utf-8") as f:
                ver = json.load(f).get("version")
                if ver:
                    return str(ver)
        except Exception:
            pass

    cargo = os.path.join(repo_path, "Cargo.toml")
    if os.path.isfile(cargo):
        try:
            with open(cargo, "r", encoding="utf-8") as f:
                txt = f.read()
            m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', txt)
            if m:
                return m.group(1)
        except Exception:
            pass

    pyproj = os.path.join(repo_path, "pyproject.toml")
    if os.path.isfile(pyproj):
        try:
            with open(pyproj, "r", encoding="utf-8") as f:
                txt = f.read()
            m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', txt)
            if m:
                return m.group(1)
        except Exception:
            pass

    for rel in ("tauri.conf.json", os.path.join("src-tauri", "tauri.conf.json")):
        tp = os.path.join(repo_path, rel)
        if os.path.isfile(tp):
            try:
                with open(tp, "r", encoding="utf-8") as f:
                    tdata = json.load(f)
                ver = tdata.get("version") or (tdata.get("package") or {}).get("version")
                if ver:
                    return str(ver)
            except Exception:
                pass

    if tags:
        first = tags[0]
        return first.get("name") if isinstance(first, dict) else str(first)
    return fallback or "unknown"


def _read_local_readme(repo_path):
    for name in _README_NAMES:
        cand = os.path.join(repo_path, name)
        if os.path.isfile(cand):
            try:
                with open(cand, "r", encoding="utf-8", errors="replace") as f:
                    return f.read(_README_MAX_CHARS)
            except OSError:
                pass
    return ""


def scan_local_media(repo_path):
    """Find up to 30 demo media files <= 50 MB inside docs/ and assets/."""
    real_root = os.path.realpath(repo_path)
    found = []
    for sub in ("docs", "assets"):
        base = os.path.join(repo_path, sub)
        if not os.path.isdir(base):
            continue
        for root_dir, dirnames, files in os.walk(base, followlinks=False):
            dirnames.sort()
            for fname in sorted(files):
                ext = os.path.splitext(fname)[1].lower()
                if ext not in _MEDIA_EXTENSIONS:
                    continue
                full = os.path.join(root_dir, fname)
                if os.path.islink(full):
                    real_file = os.path.realpath(full)
                    try:
                        if os.path.commonpath([real_root, real_file]) != real_root:
                            continue
                    except ValueError:
                        continue
                if not os.path.isfile(full):
                    continue
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                if size > _MEDIA_MAX_BYTES:
                    continue
                rel = os.path.relpath(full, repo_path).replace("\\", "/")
                found.append({"path": rel, "size": size, "ext": ext.lstrip(".")})
                if len(found) >= _MEDIA_MAX_FILES:
                    return found
    return found


def _sync_local_repo(repo):
    repo_path = repo.get("local_path")
    if not repo_path or not os.path.isdir(repo_path):
        return {
            **repo,
            "synced": False,
            "synced_at": _now_iso(),
            "error": f"Local path not found: {repo_path}",
            "recent_commits": [],
            "tags": [],
            "releases": [],
            "prs": [],
            "media": [],
            "readme": "",
        }

    rc, branch_out, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
    branch = branch_out if rc == 0 and branch_out else (repo.get("branch") or "main")

    rc, log_out, _ = _run_git(
        ["log", "--since=14.days", "--pretty=format:%H\x1f%h\x1f%aI\x1f%an\x1f%P\x1f%s"],
        repo_path,
    )
    commits = []
    if rc == 0 and log_out:
        for line in log_out.splitlines():
            parts = line.split("\x1f")
            if len(parts) >= 6:
                full_sha, short_hash, date_s, author, parents, subject = (
                    parts[0],
                    parts[1],
                    parts[2],
                    parts[3],
                    parts[4],
                    "\x1f".join(parts[5:]),
                )
                is_merge = len(parents.split()) > 1 or subject.startswith("Merge ")
                commits.append(
                    {
                        "sha": full_sha,
                        "hash": short_hash,
                        "date": date_s,
                        "author": author,
                        "message": subject,
                        "is_merge": is_merge,
                    }
                )

    rc, tag_out, _ = _run_git(
        [
            "for-each-ref",
            "--sort=-creatordate",
            "--format=%(refname:short)\x1f%(creatordate:iso-strict)",
            "refs/tags",
        ],
        repo_path,
    )
    tags = []
    if rc == 0 and tag_out:
        for line in tag_out.splitlines()[:20]:
            if "\x1f" in line:
                tname, tdate = line.split("\x1f", 1)
                if tname.strip():
                    tags.append({"name": tname.strip(), "date": tdate.strip()})
    else:
        rc2, tag_out2, _ = _run_git(["tag", "--sort=-creatordate"], repo_path)
        if rc2 == 0 and tag_out2:
            for tname in tag_out2.splitlines()[:20]:
                if tname.strip():
                    tags.append({"name": tname.strip(), "date": ""})

    version = _detect_local_version(repo_path, tags, fallback=repo.get("version") or "unknown")
    readme = _read_local_readme(repo_path)
    media = scan_local_media(repo_path)

    gh = repo.get("github")
    remote_url = f"https://github.com/{gh}" if gh else ""
    return {
        **repo,
        "remote_url": remote_url,
        "branch": branch,
        "version": version,
        "recent_commits": commits,
        "latest_commit": commits[0] if commits else None,
        "tags": tags,
        "releases": [],
        "prs": [],
        "readme": readme,
        "media": media,
        "synced": True,
        "synced_at": _now_iso(),
    }


def _sync_github_repo(repo):
    slug = repo.get("github") or parse_github_slug(repo.get("remote_url"))
    if not slug:
        return {
            **repo,
            "synced": False,
            "synced_at": _now_iso(),
            "error": "Missing owner/repo for GitHub repository",
        }

    since_dt = _now_utc() - timedelta(days=14)
    since_iso = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    since_date = since_dt.strftime("%Y-%m-%d")

    _, repo_info = _github_request(f"/repos/{slug}")
    stars = int(repo_info.get("stargazers_count") or 0) if isinstance(repo_info, dict) else 0
    branch = repo.get("branch") or (repo_info.get("default_branch") if isinstance(repo_info, dict) else None) or "main"
    html_url = (repo_info.get("html_url") if isinstance(repo_info, dict) else None) or f"https://github.com/{slug}"

    _, releases_raw = _github_request(f"/repos/{slug}/releases?per_page=10")
    releases = []
    if isinstance(releases_raw, list):
        for rel in releases_raw:
            if not isinstance(rel, dict) or rel.get("draft"):
                continue
            releases.append(
                {
                    "tag_name": rel.get("tag_name") or "",
                    "name": rel.get("name") or rel.get("tag_name") or "",
                    "html_url": rel.get("html_url") or "",
                    "published_at": rel.get("published_at") or rel.get("created_at") or "",
                    "body": (rel.get("body") or "")[:2000],
                    "prerelease": bool(rel.get("prerelease")),
                }
            )

    q = urllib.parse.quote(f"repo:{slug} is:pr is:merged merged:>={since_date}", safe="")
    _, prs_raw = _github_request(f"/search/issues?q={q}&per_page=30")
    prs = []
    items = prs_raw.get("items") if isinstance(prs_raw, dict) else []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        pr_meta = item.get("pull_request") or {}
        prs.append(
            {
                "number": item.get("number"),
                "title": item.get("title") or "",
                "html_url": item.get("html_url") or "",
                "merged_at": pr_meta.get("merged_at") or item.get("closed_at") or "",
            }
        )

    commits_q = urllib.parse.quote(since_iso, safe="")
    _, commits_raw = _github_request(f"/repos/{slug}/commits?since={commits_q}&per_page=50")
    commits = []
    if isinstance(commits_raw, list):
        for c in commits_raw:
            if not isinstance(c, dict):
                continue
            sha = c.get("sha") or ""
            c_obj = c.get("commit") or {}
            author_obj = c_obj.get("author") or {}
            msg_full = c_obj.get("message") or ""
            subject = msg_full.splitlines()[0] if msg_full else ""
            parents = c.get("parents") or []
            is_merge = len(parents) > 1 or subject.startswith("Merge ")
            commits.append(
                {
                    "sha": sha,
                    "hash": sha[:7],
                    "date": author_obj.get("date") or "",
                    "author": author_obj.get("name") or ((c.get("author") or {}).get("login") or ""),
                    "message": subject,
                    "html_url": c.get("html_url") or "",
                    "is_merge": is_merge,
                }
            )

    readme = ""
    try:
        _, readme_body = _github_request(
            f"/repos/{slug}/readme",
            accept="application/vnd.github.raw+json",
            raw=True,
        )
        readme = (readme_body or "")[:_README_MAX_CHARS]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise

    version = (
        releases[0]["tag_name"]
        if releases and releases[0].get("tag_name")
        else (repo.get("version") or "unknown")
    )

    return {
        **repo,
        "github": slug,
        "remote_url": html_url,
        "branch": branch,
        "version": version,
        "stars": stars,
        "recent_commits": commits,
        "latest_commit": commits[0] if commits else None,
        "tags": [{"name": r["tag_name"], "date": r["published_at"]} for r in releases if r.get("tag_name")],
        "releases": releases,
        "prs": prs,
        "readme": readme,
        "media": [],
        "synced": True,
        "synced_at": _now_iso(),
    }


def _extract_release_markers(snap_data):
    """Return ordered list of (tag_or_version, date) markers from releases and tags."""
    markers = []
    for rel in snap_data.get("releases") or []:
        tag = rel.get("tag_name") or rel.get("name")
        if tag:
            markers.append((str(tag), rel.get("published_at") or ""))
    for tag in snap_data.get("tags") or []:
        tname = tag.get("name") if isinstance(tag, dict) else str(tag)
        tdate = tag.get("date") if isinstance(tag, dict) else ""
        if tname and not any(m[0] == tname for m in markers):
            markers.append((str(tname), tdate))
    return markers


def _detect_new_release(prev_snap, new_data):
    """Return the new release tag/version if a new release appeared since prev_snap, else None."""
    if prev_snap is None:
        return None
    prev_data = prev_snap.get("data") or {}
    prev_tags = {m[0] for m in _extract_release_markers(prev_data)}
    new_markers = _extract_release_markers(new_data)
    for tag, _ in new_markers:
        if tag not in prev_tags:
            return tag
    return None


def _ollama_is_available():
    try:
        cfg = ai_gateway.get_config()
        base_url = cfg.get("OLLAMA_BASE_URL") or ""
        return bool(base_url and ai_gateway._ollama_reachable(base_url))
    except Exception:
        return False


def enqueue_fact_draft_session(repo, version=None):
    """Create and run a researcher session in the background to draft facts for a release."""
    agent_cfg = agents.get_agent("researcher")
    if agent_cfg is None:
        return None
    repo_name = repo.get("name") or repo.get("id")
    ver_str = f" {version}" if version else ""
    user_message = (
        f"Draft product facts for {repo_name}{ver_str} (repo_id={repo['id']}, "
        f"product_id={repo.get('product_id') or repo['id']}). "
        f"Call draft_repo_facts and propose concise, verifiable facts via propose_fact."
    )
    session = agent_runtime.create_session(
        "researcher",
        user_message,
        system_prompt=agents.build_system_prompt("researcher", confidential=bool(repo.get("confidential"))),
        allowed_tools=list(agent_cfg.get("tools") or []),
        confidential=bool(repo.get("confidential")),
        title=f"Draft product facts for {repo_name}{ver_str}".strip(),
    )
    _FACT_EXECUTOR.submit(agent_runtime.run_session, session["id"])
    return session


def _write_repo_sync_outputs(repos_data=None):
    """Write research/repo-sync.json and research/repo-sync.md for compatibility."""
    if repos_data is None:
        repos_data = []
        for r in list_repos():
            snap = db.get_latest_repo_snapshot(r["id"])
            if snap and isinstance(snap.get("data"), dict):
                repos_data.append(snap["data"])
            else:
                repos_data.append({**r, "synced": False, "recent_commits": [], "tags": []})

    synced_at = _now_iso()
    payload = {"synced_at": synced_at, "repositories": repos_data}

    out_json = paths.data("research", "repo-sync.json")
    out_md = paths.data("research", "repo-sync.md")
    os.makedirs(os.path.dirname(out_json), exist_ok=True)

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    with open(out_md, "w", encoding="utf-8") as f:
        f.write("# Codebase & GitHub Intelligence Sync\n")
        f.write(f"Synced at: `{synced_at}`\n\n")
        f.write(
            "This document provides verified engineering reality from local & remote repositories "
            "to prevent hallucinated product claims.\n\n"
        )
        for r in repos_data:
            prod = r.get("product_id") or r.get("product") or r.get("id")
            f.write(f"## {r.get('name', r.get('id'))} ({prod})\n")
            f.write(
                f"- **Lifecycle Status:** `{str(r.get('status', 'in_development')).upper()}` "
                f"(Version: `{r.get('version', 'unknown')}`)\n"
            )
            remote = r.get("remote_url") or (f"https://github.com/{r['github']}" if r.get("github") else "")
            f.write(f"- **Branch:** `{r.get('branch', 'unknown')}` | **Remote:** [{remote}]({remote})\n")
            if r.get("marketing_angle"):
                f.write(f"- **Marketing Directive:** {r.get('marketing_angle')}\n")
            f.write("\n### Recent Commits (What Was Built):\n")
            for c in r.get("recent_commits") or []:
                f.write(f"- `{c.get('hash', '')}` ({c.get('date', '')}): {c.get('message', '')}\n")
            f.write("\n")

    return payload


def sync_repo(repo_id, *, write_files=True):
    """Sync a single repository by id, store snapshot in DB, and emit events."""
    repo = get_repo(repo_id)
    if repo is None:
        raise ValueError(f"Repository '{repo_id}' not found")

    prev_snap = db.get_latest_repo_snapshot(repo["id"])

    if repo.get("kind") == "github":
        try:
            data = _sync_github_repo(repo)
        except RateLimitError as rl_err:
            msg = str(rl_err)
            if prev_snap and isinstance(prev_snap.get("data"), dict):
                cached = dict(prev_snap["data"])
                cached["rate_limited"] = True
                cached["rate_limit_message"] = msg
                cached["cached"] = True
                events.publish(
                    "repo_synced",
                    {"repo_id": repo["id"], "rate_limited": True, "message": msg},
                )
                return cached
            return {
                **repo,
                "synced": False,
                "rate_limited": True,
                "rate_limit_message": msg,
                "error": msg,
            }
        except urllib.error.HTTPError as e:
            return {
                **repo,
                "synced": False,
                "synced_at": _now_iso(),
                "error": f"GitHub API HTTP {e.code}",
            }
        except Exception as e:
            return {
                **repo,
                "synced": False,
                "synced_at": _now_iso(),
                "error": str(e),
            }
    else:
        data = _sync_local_repo(repo)

    fetched_at = data.get("synced_at") or _now_iso()
    if data.get("synced"):
        db.save_repo_snapshot(repo["id"], fetched_at, data, keep=10)

    if write_files:
        _write_repo_sync_outputs()

    events.publish(
        "repo_synced",
        {
            "repo_id": repo["id"],
            "synced": bool(data.get("synced")),
            "version": data.get("version"),
            "fetched_at": fetched_at,
        },
    )

    if data.get("synced"):
        new_rel = _detect_new_release(prev_snap, data)
        if new_rel:
            if (not repo.get("confidential")) or _ollama_is_available():
                enqueue_fact_draft_session(repo, new_rel)

    return data


def sync_all():
    """Sync all configured repositories and write compatibility outputs."""
    repos = list_repos()
    results = []
    for r in repos:
        results.append(sync_repo(r["id"], write_files=False))
    return _write_repo_sync_outputs(results)


def _parse_iso_dt(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    try:
        dt = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _is_merge_commit(commit):
    if commit.get("is_merge"):
        return True
    msg = str(commit.get("message") or "").strip()
    return msg.startswith((
        "Merge pull request ",
        "Merge branch ",
        "Merge remote-tracking branch ",
        "Merge tag ",
    ))


def _commit_covered_by_pr(commit, prs):
    msg = str(commit.get("message") or "").strip()
    for pr in prs or []:
        num = pr.get("number")
        if num is not None:
            if f"(#{num})" in msg or f"pull request #{num}" in msg.lower():
                return True
        pr_title = str(pr.get("title") or "").strip()
        if pr_title and msg == pr_title:
            return True
    return False


def shipped(repo_id=None, days=7, now=None):
    """Return recent releases, PRs, commits, and tags across repos, newest first."""
    days_int = max(1, int(days or 7))
    ref_now = now or _now_utc()
    cutoff = ref_now - timedelta(days=days_int)

    if repo_id:
        r = get_repo(repo_id)
        target_repos = [r] if r else [{"id": repo_id}]
    else:
        target_repos = list_repos()

    items = []
    for repo in target_repos:
        rid = repo["id"]
        snap = db.get_latest_repo_snapshot(rid)
        if not snap or not isinstance(snap.get("data"), dict):
            continue
        data = snap["data"]
        gh = data.get("github") or repo.get("github")

        prs = data.get("prs") or []
        releases = data.get("releases") or []
        release_tags = {str(rel.get("tag_name") or "") for rel in releases if rel.get("tag_name")}

        for rel in releases:
            dt = _parse_iso_dt(rel.get("published_at") or rel.get("created_at"))
            if dt is None or dt < cutoff:
                continue
            tag = rel.get("tag_name") or ""
            title = rel.get("name") or tag
            url = rel.get("html_url") or (f"https://github.com/{gh}/releases/tag/{tag}" if gh and tag else None)
            items.append({
                "repo_id": rid,
                "kind": "release",
                "title": title,
                "url": url,
                "date": dt.isoformat(),
            })

        for tag in data.get("tags") or []:
            tname = tag.get("name") if isinstance(tag, dict) else str(tag)
            tdate = tag.get("date") if isinstance(tag, dict) else ""
            if not tname or tname in release_tags:
                continue
            dt = _parse_iso_dt(tdate)
            if dt is None or dt < cutoff:
                continue
            url = f"https://github.com/{gh}/releases/tag/{tname}" if gh else None
            items.append({
                "repo_id": rid,
                "kind": "tag",
                "title": tname,
                "url": url,
                "date": dt.isoformat(),
            })

        for pr in prs:
            dt = _parse_iso_dt(pr.get("merged_at") or pr.get("closed_at"))
            if dt is None or dt < cutoff:
                continue
            num = pr.get("number")
            title = pr.get("title") or (f"PR #{num}" if num else "Merged PR")
            url = pr.get("html_url") or (f"https://github.com/{gh}/pull/{num}" if gh and num else None)
            items.append({
                "repo_id": rid,
                "kind": "pr",
                "title": title,
                "url": url,
                "date": dt.isoformat(),
            })

        for commit in data.get("recent_commits") or []:
            if _is_merge_commit(commit):
                continue
            if _commit_covered_by_pr(commit, prs):
                continue
            dt = _parse_iso_dt(commit.get("date"))
            if dt is None or dt < cutoff:
                continue
            sha = commit.get("sha") or commit.get("hash") or ""
            url = commit.get("html_url") or (f"https://github.com/{gh}/commit/{sha}" if gh and sha else None)
            items.append({
                "repo_id": rid,
                "kind": "commit",
                "title": commit.get("message") or sha,
                "url": url,
                "date": dt.isoformat(),
            })

    items.sort(key=lambda x: x.get("date") or "", reverse=True)
    return items
