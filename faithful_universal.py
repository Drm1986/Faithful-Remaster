"""Faithful Remaster universal backend, recovery, N64 strip-safe and artwork layer.

This module intentionally sits outside the main GUI file.  The application core
only emits semantic tasks; backend adapters and workflow files remain replaceable.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk
    PIL_OK = True
except Exception:
    Image = ImageDraw = ImageFont = ImageOps = ImageTk = None
    PIL_OK = False


_UNIVERSAL_LAYER_VERSION = "11.10.22-github-release-docs-v1"

# Prevent the startup thread and the live watchdog from spawning duplicate
# backend processes while ComfyUI is still booting.
_BACKEND_LAUNCH_LOCK = threading.RLock()
_BACKEND_LAST_LAUNCH = {}
_BACKEND_DUPLICATE_GUARD_SECONDS = 20.0


def install(fr):
    """Install the universal feature layer into the already-loaded application.

    Do not override the core application version here. Earlier builds hardcoded
    this layer to v11.10.17, which made the packaged build name and the visible
    UI version disagree.
    """
    app_version = str(getattr(fr, "APP_VERSION", "11.10.22")).strip() or "11.10.22"
    fr.APP_VERSION = app_version
    fr.APP_TITLE = f"Faithful Remaster v{app_version}"

    fr.BACKEND_PROFILES_PATH = fr.DATA_DIR / "backend_profiles.json"
    fr.ARTWORK_DIR = fr.DATA_DIR / "artwork"
    fr.ARTWORK_FAILURES_PATH = fr.ARTWORK_DIR / "failures.json"
    fr.BUNDLED_BACKENDS_PATH = fr.APP_DIR / "backends" / "backend_profiles.json"
    fr.BUNDLED_ARTWORK_SOURCES_PATH = fr.APP_DIR / "artwork" / "artwork_sources.json"
    fr.ARTWORK_DIR.mkdir(parents=True, exist_ok=True)

    fr.DEFAULT_CONFIG.update({
        "active_backend_id": "default_comfyui",
        "skip_dynamic_efb_postprocess": True,
        "alpha_backend_id": "",
        "n64_strip_safe_enabled": True,
        "n64_strip_max_height": 16,
        "n64_strip_min_aspect_ratio": 20.0,
        "n64_strip_padding_pixels": 16,
        "n64_strip_workflow_profile": "n64_strip_safe",
        "game_artwork_enabled": True,
        "game_artwork_style": "Named_Boxarts",
        "auto_restart_backend_when_offline": True,
        "backend_restart_offline_grace_seconds": 15,
        "backend_restart_retry_seconds": 60,
        "backend_interrupted_job_retries": 1,
    })
    for key in (
        "active_backend_id", "alpha_backend_id", "skip_dynamic_efb_postprocess", "n64_strip_safe_enabled",
        "n64_strip_max_height", "n64_strip_min_aspect_ratio",
        "n64_strip_padding_pixels", "n64_strip_workflow_profile",
        "game_artwork_enabled", "game_artwork_style",
        "auto_restart_backend_when_offline",
        "backend_restart_offline_grace_seconds",
        "backend_restart_retry_seconds",
        "backend_interrupted_job_retries",
    ):
        if key not in fr.PROFILE_SETTING_KEYS:
            fr.PROFILE_SETTING_KEYS.append(key)

    # Export helpers into the app module for diagnostics and future plugins.
    fr.load_backend_profiles = lambda: load_backend_profiles(fr)
    fr.save_backend_profiles = lambda profiles, active_id=None: save_backend_profiles(fr, profiles, active_id)
    fr.find_backend_profile = lambda value=None, cfg=None: find_backend_profile(fr, value, cfg)
    fr.check_processing_backend = lambda backend: check_processing_backend(fr, backend)
    fr.run_backend_workflow_to_file = lambda backend, workflow_profile, image_path, filename_prefix, timeout=900, task_type="standard": run_backend_workflow_to_file(fr, backend, workflow_profile, image_path, filename_prefix, timeout, task_type)
    fr.is_n64_strip_texture = lambda path, cfg: is_n64_strip_texture(fr, path, cfg)
    fr.generate_mode_comparison_output = lambda source_path, cfg, preset, force=False, log=None: generate_mode_comparison_output(
        fr, source_path, cfg, preset, force=force, log=log
    )

    _patch_workflow_profiles(fr)
    _patch_worker(fr)
    _patch_backend_lifecycle(fr)
    _patch_config_and_profile_loading(fr)
    _patch_settings_ui(fr)
    _patch_artwork_ui(fr)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _builtin_backends(fr):
    data = _read_json(fr.BUNDLED_BACKENDS_PATH, {})
    rows = data.get("backends", []) if isinstance(data, dict) else []
    if not rows:
        rows = [{
            "id": "default_comfyui", "name": "Local ComfyUI", "type": "comfyui",
            "api_url": "http://127.0.0.1:8188", "start_file": "",
            "command_template": "", "enabled": True,
        }]
    return rows, str(data.get("active_backend_id") or "default_comfyui")


def _normalize_backend(item):
    return {
        "id": str(item.get("id") or "").strip(),
        "name": str(item.get("name") or "Unnamed backend").strip(),
        "type": str(item.get("type") or "comfyui").strip().lower(),
        "api_url": str(item.get("api_url") or "").strip(),
        "start_file": str(item.get("start_file") or "").strip(),
        "command_template": str(item.get("command_template") or "").strip(),
        "enabled": bool(item.get("enabled", True)),
    }


def load_backend_profiles(fr):
    bundled, bundled_active = _builtin_backends(fr)
    stored = _read_json(fr.BACKEND_PROFILES_PATH, {})
    rows = stored.get("backends", []) if isinstance(stored, dict) else []
    active = str(stored.get("active_backend_id") or bundled_active)

    by_id = {}
    for row in rows:
        n = _normalize_backend(row)
        if n["id"]:
            by_id[n["id"]] = n
    changed = False
    for row in bundled:
        n = _normalize_backend(row)
        if n["id"] not in by_id:
            by_id[n["id"]] = n
            changed = True
    result = list(by_id.values())
    if not result:
        result = [_normalize_backend(bundled[0])]
        changed = True
    if active not in by_id:
        active = result[0]["id"]
        changed = True
    if changed or not fr.BACKEND_PROFILES_PATH.exists():
        save_backend_profiles(fr, result, active)
    return {"active_backend_id": active, "backends": result}


def save_backend_profiles(fr, profiles, active_id=None):
    clean = []
    seen = set()
    for row in profiles:
        n = _normalize_backend(row)
        if not n["id"] or n["id"] in seen:
            continue
        seen.add(n["id"])
        clean.append(n)
    if not clean:
        clean, default_active = _builtin_backends(fr)
        clean = [_normalize_backend(x) for x in clean]
        active_id = active_id or default_active
    active_id = str(active_id or clean[0]["id"])
    if active_id not in {x["id"] for x in clean}:
        active_id = clean[0]["id"]
    _atomic_json(fr.BACKEND_PROFILES_PATH, {
        "version": 1, "active_backend_id": active_id, "backends": clean,
    })


def find_backend_profile(fr, value=None, cfg=None):
    data = load_backend_profiles(fr)
    wanted = str(value or (cfg or {}).get("active_backend_id") or data["active_backend_id"]).strip()
    for row in data["backends"]:
        if wanted.casefold() in {row["id"].casefold(), row["name"].casefold()}:
            found = dict(row)
            # Seamless migration: old per-profile ComfyUI fields continue to drive
            # the default local backend until the user edits Backend Manager.
            if found["id"] == "default_comfyui" and cfg:
                found["api_url"] = str(cfg.get("comfy_url") or found["api_url"])
                found["start_file"] = str(cfg.get("comfy_start_file") or found["start_file"])
            return found
    return dict(data["backends"][0])


def backend_fingerprint(backend):
    raw = json.dumps(_normalize_backend(backend), sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def check_processing_backend(fr, backend):
    kind = str(backend.get("type") or "comfyui").lower()
    if kind == "comfyui":
        return fr.check_comfy_status(str(backend.get("api_url") or ""))
    if kind == "external_command":
        command = str(backend.get("command_template") or "").strip()
        return {
            "online": bool(command), "queue_running": 0, "queue_pending": 0,
            "error": "" if command else "External command template is empty",
        }
    return {"online": False, "queue_running": None, "queue_pending": None, "error": f"Unsupported backend type: {kind}"}


def _launch_backend(fr, backend, show_errors=False):
    """Launch a configured local backend without creating duplicate instances.

    Both Start Watching and the live recovery watchdog can notice an offline
    backend at almost the same moment.  The short process-wide guard treats a
    second launch request as already handled while the first process boots.
    """
    kind = str(backend.get("type") or "comfyui").lower()
    if kind != "comfyui":
        return False
    start = str(backend.get("start_file") or "").strip()
    if not start:
        return False
    p = Path(start)
    if not p.exists():
        return False

    key = backend_fingerprint(backend)
    now = time.monotonic()
    with _BACKEND_LAUNCH_LOCK:
        last = float(_BACKEND_LAST_LAUNCH.get(key, 0.0) or 0.0)
        if last and now - last < _BACKEND_DUPLICATE_GUARD_SECONDS:
            return True
        try:
            kwargs = fr._hidden_subprocess_kwargs()
            if p.suffix.lower() in {".bat", ".cmd"}:
                subprocess.Popen(["cmd", "/c", str(p)], cwd=str(p.parent), **kwargs)
            else:
                subprocess.Popen([str(p)], cwd=str(p.parent), **kwargs)
            _BACKEND_LAST_LAUNCH[key] = now
            return True
        except Exception:
            return False


def run_backend_workflow_to_file(fr, backend, workflow_profile, image_path, filename_prefix, timeout=900, task_type="standard"):
    kind = str(backend.get("type") or "comfyui").lower()
    api_text = str(workflow_profile.get("api_path") or "").strip()
    ui_text = str(workflow_profile.get("ui_path") or "").strip()
    api_path = Path(api_text) if api_text else None
    if kind == "comfyui":
        validation = fr.validate_comfy_api_workflow(
            api_path or "",
            workflow_profile.get("load_node") or "1",
            workflow_profile.get("save_node") or "4",
            require_reachable=True,
        )
        return fr.run_comfy_image_workflow_to_file(
            str(backend.get("api_url") or ""), validation["workflow"], Path(image_path),
            validation["load_node"], validation["save_node"],
            filename_prefix, int(timeout),
        )

    if kind == "external_command":
        command = str(backend.get("command_template") or "").strip()
        if not command:
            raise RuntimeError("External backend command template is empty")
        output = fr.TEMP_DIR / f"external_{uuid.uuid4().hex}.png"
        values = {
            "input": str(Path(image_path).resolve()),
            "output": str(output.resolve()),
            "workflow_api": str(api_path.resolve()) if api_path is not None else "",
            "workflow_ui": str(Path(ui_text).resolve()) if ui_text else "",
            "preset": str(workflow_profile.get("name") or ""),
            "task_type": str(task_type),
            "scale": str(workflow_profile.get("output_scale") or 4),
        }
        try:
            rendered = command.format_map(values)
        except KeyError as exc:
            raise RuntimeError(f"Unknown command-template placeholder: {exc}") from exc
        completed = subprocess.run(
            rendered, shell=True, timeout=int(timeout), cwd=str(fr.APP_DIR),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, **fr._hidden_subprocess_kwargs(),
        )
        if completed.returncode != 0:
            tail = (completed.stdout or "")[-1600:]
            raise RuntimeError(f"External backend failed ({completed.returncode}): {tail}")
        if not output.exists():
            raise RuntimeError(f"External backend completed but did not create {output}")
        fr.validate_image_file(output, label="external backend output")
        return output

    raise RuntimeError(f"Unsupported backend type: {kind}")


# ---------------------------------------------------------------------------
# External workflow profiles
# ---------------------------------------------------------------------------

def _patch_workflow_profiles(fr):
    def builtins():
        wf = fr.APP_DIR / "workflows"
        return [
            {
                "id": "midway", "name": "Clean Heart",
                "ui_path": str(wf / "Faithful_RGB_Workflow_UI_Clean_Heart.json"),
                "api_path": str(wf / "Faithful_RGB_Workflow_API_Clean_Heart.json"),
                "load_node": "1", "save_node": "4", "builtin": True,
                "enabled": True, "show_as_mode": True, "task_type": "standard",
                "output_scale": 4, "backend_id": "",
            },
            {
                "id": "strong_believer", "name": "Strong Believer",
                "ui_path": str(wf / "Faithful_RGB_Workflow_UI_Strong_Believer.json"),
                "api_path": str(wf / "Faithful_RGB_Workflow_API_Strong_Believer.json"),
                "load_node": "1", "save_node": "4", "builtin": True,
                "enabled": True, "show_as_mode": True, "task_type": "standard",
                "output_scale": 4, "backend_id": "",
            },
            {
                "id": "n64_strip_safe", "name": "N64 Strip Safe",
                "ui_path": str(wf / "Faithful_N64_Strip_Safe_UI.json"),
                "api_path": str(wf / "Faithful_N64_Strip_Safe_API.json"),
                "load_node": "1", "save_node": "4", "builtin": True,
                "enabled": True, "show_as_mode": False, "task_type": "n64_strip_safe",
                "output_scale": 4, "backend_id": "",
            },
        ]

    def _positive_int(value, default):
        try:
            return max(1, int(float(value)))
        except Exception:
            return int(default)

    def normalize(row):
        row = row if isinstance(row, dict) else {}
        return {
            "id": str(row.get("id") or "").strip(),
            "name": str(row.get("name") or "").strip(),
            "ui_path": str(row.get("ui_path") or "").strip(),
            "api_path": str(row.get("api_path") or "").strip(),
            "load_node": str(row.get("load_node") or "1").strip(),
            "save_node": str(row.get("save_node") or "4").strip(),
            "builtin": bool(row.get("builtin", False)),
            "enabled": bool(row.get("enabled", True)),
            "show_as_mode": bool(row.get("show_as_mode", row.get("task_type", "standard") == "standard")),
            "task_type": str(row.get("task_type") or "standard").strip(),
            "output_scale": _positive_int(row.get("output_scale"), 4),
            "backend_id": str(row.get("backend_id") or "").strip(),
        }

    def save_profiles(rows):
        clean = []
        seen = set()
        for row in rows:
            n = normalize(row)
            if not n["id"] or not n["name"] or n["id"] in seen:
                continue
            seen.add(n["id"])
            clean.append(n)
        _atomic_json(fr.WORKFLOW_PROFILES_PATH, {"version": 3, "profiles": clean})

    def load_profiles():
        defaults = builtins()
        data = _read_json(fr.WORKFLOW_PROFILES_PATH, {})
        rows = data.get("profiles", []) if isinstance(data, dict) else []
        if not isinstance(rows, list):
            rows = []
        by_id = {}
        changed = False
        for row in rows:
            if not isinstance(row, dict):
                changed = True
                continue
            n = normalize(row)
            if n["id"] == "soft_heart":
                changed = True
                continue
            if not n["id"] or not n["name"]:
                changed = True
                continue
            if n["id"] in by_id:
                changed = True
            by_id[n["id"]] = n

        for default in defaults:
            normalized_default = normalize(default)
            did = normalized_default["id"]
            if did not in by_id:
                by_id[did] = normalized_default
                changed = True
                continue
            saved = by_id[did]
            for key in ("name", "builtin", "task_type", "show_as_mode"):
                if saved.get(key) != normalized_default[key]:
                    saved[key] = normalized_default[key]
                    changed = True
            # Preserve genuinely custom replacements, but always rebase paths
            # that still point at a bundled workflow inside an older extracted
            # Faithful Remaster folder. Merely existing on disk no longer makes
            # an old version-owned path authoritative.
            rebased = False
            for key in ("ui_path", "api_path"):
                current_value = str(saved.get(key) or "")
                expected_value = str(normalized_default[key])
                parts = [part for part in re.split(r"[\\/]+", current_value.strip()) if part]
                filename = parts[-1].casefold() if parts else ""
                parent_name = parts[-2].casefold() if len(parts) >= 2 else ""
                ancestors = " ".join(parts[:-2]).casefold() if len(parts) >= 2 else ""
                expected_name = Path(expected_value).name.casefold()
                legacy_names = {expected_name}
                if did == "midway":
                    if key == "api_path":
                        legacy_names.update({
                            "faithful_rgb_workflow_api.json",
                            "faithful_rgb_workflow_api_midway.json",
                            "faithful_rgb_workflow_api_soft_heart.json",
                            "faithful_rgb_workflow_api_clean_heart.json",
                        })
                    else:
                        legacy_names.update({
                            "faithful_rgb_workflow_ui.json",
                            "faithful_rgb_workflow_ui_midway.json",
                            "faithful_rgb_workflow_ui_soft_heart.json",
                            "faithful_rgb_workflow_ui_clean_heart.json",
                        })
                old_bundled_path = (
                    filename in legacy_names
                    and parent_name == "workflows"
                    and ("faithful-remaster" in ancestors or "faithful remaster" in ancestors)
                )
                if not Path(current_value).is_file() or old_bundled_path:
                    if current_value != expected_value:
                        saved[key] = expected_value
                        changed = True
                    rebased = True
            if rebased:
                for key in ("load_node", "save_node", "output_scale", "backend_id", "enabled"):
                    if saved.get(key) != normalized_default[key]:
                        saved[key] = normalized_default[key]
                        changed = True
            else:
                for key in ("load_node", "save_node", "output_scale", "backend_id", "enabled"):
                    if key not in saved or saved.get(key) in (None, ""):
                        saved[key] = normalized_default[key]
                        changed = True

        result = list(by_id.values())
        if changed or not fr.WORKFLOW_PROFILES_PATH.exists():
            save_profiles(result)
        return result

    def names(enabled_only=True):
        return tuple(
            p["name"] for p in load_profiles()
            if p.get("show_as_mode", True) and (p.get("enabled", True) or not enabled_only)
        )

    def find_exact(value):
        raw = str(value or "").strip()
        canonical = fr._canonical_workflow_profile_id(raw)
        profiles = load_profiles()
        for profile in profiles:
            if canonical == str(profile.get("id", "")).casefold():
                return profile
        for profile in profiles:
            if raw.casefold() == str(profile.get("name", "")).casefold():
                return profile
        return None

    def find(value):
        exact = find_exact(value)
        if exact is not None:
            return exact
        profiles = load_profiles()
        for profile in profiles:
            if profile.get("id") == "midway":
                return profile
        return profiles[0] if profiles else None

    def fingerprint(profile):
        semantic = {
            "pipeline": getattr(fr, "PROCESSING_PIPELINE_VERSION", _UNIVERSAL_LAYER_VERSION),
            "id": str(profile.get("id") or ""),
            "api_path": str(profile.get("api_path") or ""),
            "api_sha256": fr.workflow_file_fingerprint(profile.get("api_path") or ""),
            "load_node": str(profile.get("load_node") or ""),
            "save_node": str(profile.get("save_node") or ""),
            "task_type": str(profile.get("task_type") or "standard"),
            "output_scale": str(profile.get("output_scale") or 4),
            "backend_id": str(profile.get("backend_id") or ""),
        }
        return hashlib.sha256(json.dumps(semantic, sort_keys=True).encode("utf-8")).hexdigest()

    fr._builtin_workflow_profiles = builtins
    fr.load_workflow_profiles = load_profiles
    fr.save_workflow_profiles = save_profiles
    fr.workflow_profile_names = names
    fr.find_workflow_profile_exact = find_exact
    fr.find_workflow_profile = find
    fr.workflow_profile_fingerprint = fingerprint


# ---------------------------------------------------------------------------
# N64 strip-safe routing and processing
# ---------------------------------------------------------------------------

def is_n64_profile(cfg):
    return str(cfg.get("emulator") or "").startswith("Nintendo 64")


def is_n64_strip_texture(fr, path, cfg):
    if not cfg.get("n64_strip_safe_enabled", True) or not is_n64_profile(cfg) or not PIL_OK:
        return False, ""
    try:
        with Image.open(path) as image:
            width, height = image.size
        max_h = max(1, int(float(cfg.get("n64_strip_max_height", 16))))
        min_ratio = max(2.0, float(cfg.get("n64_strip_min_aspect_ratio", 20.0)))
        ratio = max(width / max(1, height), height / max(1, width))
        if min(width, height) <= max_h and ratio >= min_ratio:
            return True, f"{width}×{height}, aspect {ratio:.1f}:1"
    except Exception:
        pass
    return False, ""


def _reflect_index(index, length):
    if length <= 1:
        return 0
    period = length * 2
    index %= period
    return index if index < length else period - index - 1


def make_reflect_padded_input(fr, path, padding):
    """Pad a thin N64 strip across its thin axis using reflection."""
    if not PIL_OK:
        return Path(path), None
    with Image.open(path) as source:
        source = source.convert("RGBA")
        width, height = source.size
        pad = max(1, int(padding))
        if width >= height:
            axis = "vertical"
            canvas = Image.new("RGBA", (width, height + pad * 2))
            for y in range(-pad, height + pad):
                sy = _reflect_index(y, height)
                canvas.paste(source.crop((0, sy, width, sy + 1)), (0, y + pad))
        else:
            axis = "horizontal"
            canvas = Image.new("RGBA", (width + pad * 2, height))
            for x in range(-pad, width + pad):
                sx = _reflect_index(x, width)
                canvas.paste(source.crop((sx, 0, sx + 1, height)), (x + pad, 0))
    temp = fr.TEMP_DIR / f"n64_strip_padded_{uuid.uuid4().hex}.png"
    temp.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(temp, format="PNG")
    return temp, {"width": width, "height": height, "padding": pad, "axis": axis}


def crop_strip_output(fr, output_path, info, expected_scale=4):
    if not info or not PIL_OK:
        return
    with Image.open(output_path) as image:
        image = image.convert("RGBA")
        axis = str(info.get("axis") or "vertical")
        if axis == "horizontal":
            observed_scale = image.height / max(1, info["height"])
            left = int(round(info["padding"] * observed_scale))
            desired_w = int(round(info["width"] * observed_scale))
            desired_h = int(round(info["height"] * observed_scale))
            top = max(0, (image.height - desired_h) // 2)
            right = min(image.width, left + desired_w)
            cropped = image.crop((left, top, right, min(image.height, top + desired_h)))
        else:
            observed_scale = image.width / max(1, info["width"])
            top = int(round(info["padding"] * observed_scale))
            desired_w = int(round(info["width"] * observed_scale))
            desired_h = int(round(info["height"] * observed_scale))
            left = max(0, (image.width - desired_w) // 2)
            bottom = min(image.height, top + desired_h)
            cropped = image.crop((left, top, min(image.width, left + desired_w), bottom))
        exact_w = info["width"] * int(expected_scale or 4)
        exact_h = info["height"] * int(expected_scale or 4)
        if cropped.size != (exact_w, exact_h):
            cropped = cropped.resize((exact_w, exact_h), Image.Resampling.LANCZOS)
        cropped.save(output_path, format="PNG")


def _patch_worker(fr):
    def _record_status(self, backend, status):
        online = bool(status.get("online"))
        self.stats["comfy_online"] = online
        self.stats["comfy_running"] = status.get("queue_running")
        self.stats["comfy_pending"] = status.get("queue_pending")
        self.stats["comfy_error"] = status.get("error", "")
        self.stats["backend_name"] = backend.get("name", "Backend")
        event = getattr(self, "_backend_online_event", None)
        if event is not None:
            event.set() if online else event.clear()
        return online

    def _watch_backend(self, backend):
        grace = max(3.0, float(self.cfg.get("backend_restart_offline_grace_seconds", 15) or 15))
        retry = max(15.0, float(self.cfg.get("backend_restart_retry_seconds", 60) or 60))
        seen_online = False
        offline_since = None
        next_attempt = 0.0
        attempts = 0
        unavailable_logged = False

        watchdog_stop = getattr(self, "_backend_watchdog_stop", None)
        while not self.stop_event.is_set() and not (watchdog_stop and watchdog_stop.is_set()):
            try:
                status = check_processing_backend(fr, backend)
            except Exception as exc:
                status = {"online": False, "error": str(exc), "queue_running": None, "queue_pending": None}
            online = _record_status(self, backend, status)
            now = time.monotonic()

            if online:
                seen_online = True
                offline_since = None
                next_attempt = 0.0
                unavailable_logged = False
                if self.comfy_paused:
                    suffix = f" after {attempts} restart attempt(s)" if attempts else ""
                    self.log(f"Backend ONLINE: {backend.get('name')}. Queue resumed{suffix}.")
                self.comfy_paused = False
                attempts = 0
                self.stats["backend_restart_attempts"] = 0
                if str(self.stats.get("status", "")).startswith("PAUSED (BACKEND"):
                    self.stats["status"] = "RUNNING"
            else:
                if offline_since is None:
                    offline_since = now
                if not self.comfy_paused:
                    self.log(f"Backend OFFLINE: {backend.get('name')}. Queue paused; recovery watchdog is active.")
                self.comfy_paused = True
                self.stats["status"] = "PAUSED (BACKEND OFFLINE)"

                initial_start_allowed = (not seen_online and bool(self.cfg.get("auto_start_comfy_when_watching", True)))
                restart_allowed = (seen_online and bool(self.cfg.get("auto_restart_backend_when_offline", True)))
                recovery_allowed = initial_start_allowed or restart_allowed
                offline_long_enough = now - offline_since >= grace

                if recovery_allowed and offline_long_enough and now >= next_attempt:
                    start_file = str(backend.get("start_file") or "").strip()
                    launchable = str(backend.get("type") or "").lower() == "comfyui" and start_file and Path(start_file).is_file()
                    if launchable:
                        attempts += 1
                        self.stats["backend_restart_attempts"] = attempts
                        self.stats["backend_last_restart_at"] = time.time()
                        action = "Auto-start" if not seen_online else "Auto-restart"
                        launched = _launch_backend(fr, backend, False)
                        if launched:
                            self.log(
                                f"{action} attempt {attempts}: launched {backend.get('name')}. "
                                f"Waiting for the API; next retry in {int(retry)}s if it stays offline."
                            )
                        else:
                            self.log(f"{action} attempt {attempts} failed for {backend.get('name')}.")
                        unavailable_logged = False
                    elif not unavailable_logged:
                        self.log(
                            "Backend auto-recovery is enabled, but the active backend has no valid Start file. "
                            "Configure it in Settings → Workflows & Backends."
                        )
                        unavailable_logged = True
                    next_attempt = now + retry

            if watchdog_stop is not None:
                watchdog_stop.wait(3.0)
            else:
                self.stop_event.wait(3.0)

    def wait_for_backend(self, backend=None):
        backend = backend or find_backend_profile(fr, cfg=self.cfg)
        if not self.cfg.get("enable_comfy_status", True) or not self.cfg.get("pause_when_comfy_offline", True):
            return

        requested_key = backend_fingerprint(backend)
        watched_key = getattr(self, "_backend_watchdog_fingerprint", None)
        event = getattr(self, "_backend_online_event", None)
        use_watchdog_event = event is not None and requested_key == watched_key
        logged_wait = False

        while not self.stop_event.is_set():
            if use_watchdog_event:
                if event.wait(timeout=1.0):
                    return
                continue

            # A workflow may explicitly use a backend different from the game's
            # active backend. Never reuse the active backend's ONLINE event for
            # that route; check the actual requested backend instead.
            status = check_processing_backend(fr, backend)
            if bool(status.get("online")):
                self.comfy_paused = False
                self.stats["backend_name"] = backend.get("name", "Backend")
                self.stats["comfy_error"] = status.get("error", "")
                return
            self.comfy_paused = True
            self.stats["backend_name"] = backend.get("name", "Backend")
            self.stats["comfy_error"] = status.get("error", "")
            self.stats["status"] = "PAUSED (WORKFLOW BACKEND OFFLINE)"
            if not logged_wait:
                self.log(
                    f"Workflow backend OFFLINE: {backend.get('name', 'Backend')}. "
                    "Waiting for the backend selected by this workflow."
                )
                logged_wait = True
            if self.cfg.get("auto_restart_backend_when_offline", True):
                _launch_backend(fr, backend, False)
            self.stop_event.wait(2.0)

    fr.Worker.wait_for_comfy_online = wait_for_backend

    original_run = fr.Worker.run
    def run_with_backend_watchdog(self):
        backend = find_backend_profile(fr, self.cfg.get("active_backend_id"), self.cfg)
        needs_watchdog = bool(
            self.cfg.get("enable_comfy_status", True)
            or self.cfg.get("auto_start_comfy_when_watching", True)
            or self.cfg.get("auto_restart_backend_when_offline", True)
        )
        self._backend_online_event = threading.Event()
        self._backend_watchdog_fingerprint = backend_fingerprint(backend)
        self._backend_watchdog_stop = threading.Event()
        self._backend_watchdog_thread = None
        if needs_watchdog:
            self._backend_watchdog_thread = threading.Thread(
                target=_watch_backend, args=(self, backend),
                name="FaithfulBackendWatchdog", daemon=True
            )
            self._backend_watchdog_thread.start()
        try:
            return original_run(self)
        finally:
            self._backend_watchdog_stop.set()

    fr.Worker.run = run_with_backend_watchdog

    def process_one(self, path):
        path = Path(path)
        dump_folder = Path(self.cfg["dump_folder"])
        load_folder = Path(self.cfg["load_folder"])
        if self.cfg.get("emulator") == "Azahar / Citra" and self.cfg.get("auto_sync_azahar_pack_json", True):
            fr.sync_azahar_pack_json(dump_folder, load_folder, self.log)

        alpha = self.cfg.get("preserve_alpha", True) and fr.has_alpha(path)
        out_path = fr.output_path_for_input(path, dump_folder, load_folder)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        self.stats["current_input_path"] = str(path)
        self.stats["current_output_path"] = ""
        self.stats["current_texture_stage"] = "Preparing"
        self.stats["current_texture_started_at"] = time.time()
        self.stats["current_task_type"] = "standard"

        if alpha:
            self.log(f"Alpha detected: {path.name}")
        if fr.is_exception_texture(path):
            self.stats["exceptions_skipped"] = self.stats.get("exceptions_skipped", 0) + 1
            return "exception_skipped"

        if self.cfg.get("skip_dynamic_efb_postprocess", True):
            is_dynamic, reason = fr.detect_dynamic_efb_postprocess_dump(path, self.cfg)
            if is_dynamic:
                self.stats["cutscene_buffers_skipped"] = self.stats.get("cutscene_buffers_skipped", 0) + 1
                self.log(f"Dynamic EFB/post-processing dump skipped and KEPT: {path.name} ({reason})")
                return "dynamic_efb_skipped"

        if self.cfg.get("skip_cutscene_buffers", True):
            is_buffer, reason = fr.detect_cutscene_buffer(path, self.cfg, include_dynamic=False)
            if is_buffer:
                self.stats["cutscene_buffers_skipped"] = self.stats.get("cutscene_buffers_skipped", 0) + 1
                self.log(f"Cutscene/blank buffer skipped: {path.name} ({reason})")
                if self.cfg.get("delete_skipped_cutscene_buffers", False):
                    try:
                        session = self.profile_dir / "_cleanup_quarantine" / time.strftime("live-%Y%m%d-%H%M%S")
                        destination = fr.quarantine_dump(path, dump_folder, session, category="cutscene_or_blank", reason=reason)
                        self.stats["cutscene_buffers_deleted"] = self.stats.get("cutscene_buffers_deleted", 0) + 1
                        self.log(f"Moved skipped dump to quarantine: {destination}")
                    except Exception as exc:
                        self.log(f"Could not quarantine skipped buffer {path.name}: {exc}")
                return "cutscene_buffer_skipped"

        temporary_paths = []
        processing_input = path
        try:
            strip, strip_reason = is_n64_strip_texture(fr, path, self.cfg)
            strip_info = None
            if strip:
                workflow_profile = fr.find_workflow_profile_exact(
                    self.cfg.get("n64_strip_workflow_profile", "n64_strip_safe")
                )
                if not workflow_profile or not workflow_profile.get("enabled", True):
                    raise RuntimeError("N64 Strip Safe workflow is unavailable or disabled")
                preset = workflow_profile["name"]
                task_type = "n64_strip_safe"
                processing_input, strip_info = make_reflect_padded_input(
                    fr, path, int(float(self.cfg.get("n64_strip_padding_pixels", 16)))
                )
                if processing_input != path:
                    temporary_paths.append(Path(processing_input))
                self.log(f"N64 STRIP SAFE: {path.name} ({strip_reason})")
            else:
                preset = fr.effective_texture_preset(path, self.cfg, self.texture_preset_overrides)
                workflow_profile = fr.find_workflow_profile_exact(preset)
                task_type = str((workflow_profile or {}).get("task_type") or "standard")

            if not workflow_profile or not workflow_profile.get("enabled", True):
                raise RuntimeError(f"Workflow profile is missing or disabled: {preset}")
            backend_id = workflow_profile.get("backend_id") or self.cfg.get("active_backend_id")
            backend = find_backend_profile(fr, backend_id, self.cfg)
            if not backend.get("enabled", True):
                raise RuntimeError(f"Processing backend is disabled: {backend.get('name', backend_id)}")
            backend_kind = str(backend.get("type") or "comfyui").lower()
            if backend_kind == "comfyui":
                validation = fr.validate_comfy_api_workflow(
                    workflow_profile.get("api_path") or "",
                    workflow_profile.get("load_node") or "1",
                    workflow_profile.get("save_node") or "4",
                    require_reachable=True,
                )
            else:
                api_text = str(workflow_profile.get("api_path") or "").strip()
                validation = {
                    "path": Path(api_text) if api_text else None,
                    "load_node": str(workflow_profile.get("load_node") or ""),
                    "save_node": str(workflow_profile.get("save_node") or ""),
                }
            output_scale = max(1, int(float(workflow_profile.get("output_scale") or 4)))
            source_size = fr.validate_image_file(path, label="source texture")
            expected_final_size = (
                (source_size[0] * output_scale, source_size[1] * output_scale)
                if source_size else None
            )

            self.stats["current_faithfulness_preset"] = preset
            self.stats["current_task_type"] = task_type
            self.log(f"Processing: {path.name} [{preset}]")

            if self.cfg.get("ignore_existing_silently", True) and out_path.exists() and not self.cfg.get("overwrite", False):
                return "ignored_existing"

            self.stats["backend_name"] = backend.get("name", "Backend")

            alpha_route = {
                "enabled": bool(alpha and self.cfg.get("enable_separate_alpha_workflow", False)),
                "preserve_alpha": bool(alpha),
                "invert": bool(self.cfg.get("alpha_workflow_invert_output", False)),
                "resize": str(self.cfg.get("alpha_resize_method", "nearest")),
                "edge_bleed": bool(self.cfg.get("fix_alpha_edge_bleed", False)),
                "edge_iterations": int(self.cfg.get("alpha_bleed_iterations", 1) or 1),
                "edge_threshold": int(self.cfg.get("alpha_edge_threshold", 32) or 32),
            }
            if alpha_route["enabled"]:
                alpha_path = Path(str(self.cfg.get("alpha_workflow_api_json") or ""))
                alpha_route.update({
                    "api_sha256": fr.workflow_file_fingerprint(alpha_path),
                    "load_node": str(self.cfg.get("alpha_load_image_node_id", "1")),
                    "save_node": str(self.cfg.get("alpha_save_image_node_id", "5")),
                })
                alpha_backend_for_key = find_backend_profile(
                    fr, self.cfg.get("alpha_backend_id") or backend.get("id"), self.cfg
                )
                alpha_route["backend"] = backend_fingerprint(alpha_backend_for_key)

            digest = fr.sha1_file(path) if self.cfg.get("enable_hash_cache", True) else None
            if digest:
                route_fingerprint = {
                    "pipeline": getattr(fr, "PROCESSING_PIPELINE_VERSION", _UNIVERSAL_LAYER_VERSION),
                    "workflow": fr.workflow_profile_fingerprint(workflow_profile),
                    "backend": backend_fingerprint(backend),
                    "task_type": task_type,
                    "strip_padding": str(self.cfg.get("n64_strip_padding_pixels", 16)) if strip else "",
                    "alpha": alpha_route,
                }
                digest = hashlib.sha256(
                    (digest + "|" + json.dumps(route_fingerprint, sort_keys=True)).encode("utf-8")
                ).hexdigest()

            if digest and self.restore_from_cache_if_possible(digest, out_path):
                try:
                    fr.validate_image_file(out_path, expected_final_size, label="cached output")
                    self.stats["current_output_path"] = str(out_path)
                    self.stats["current_texture_stage"] = "Restored from cache"
                    entry = self.cache_index.setdefault(digest, {})
                    source_paths = set(entry.get("source_paths", [])) if isinstance(entry.get("source_paths", []), list) else set()
                    source_paths.add(str(path.resolve()))
                    entry.update({
                        "source_name": path.name,
                        "source_path": str(path.resolve()),
                        "source_paths": sorted(source_paths),
                        "cached_file": self.cache_file_for_hash(digest).name,
                    })
                    fr.save_cache_index(self.cache_index)
                    return "cache_hit_restored"
                except Exception as exc:
                    self.log(f"Discarding invalid cache entry for {path.name}: {exc}")
                    self.stats["cache_hits"] = max(0, self.stats.get("cache_hits", 0) - 1)
                    out_path.unlink(missing_ok=True)
                    cache_file = self.cache_file_for_hash(digest)
                    cache_file.unlink(missing_ok=True)
                    self.cache_index.pop(digest, None)
                    fr.save_cache_index(self.cache_index)

            if out_path.exists() and not self.cfg.get("overwrite", False):
                return "skip_exists"

            logged_routes = getattr(self, "_logged_workflow_routes", set())
            route_path = validation.get("path")
            route_label = route_path.name if isinstance(route_path, Path) else "external command"
            route_key = (workflow_profile.get("id"), str(route_path or ""), validation.get("load_node"), validation.get("save_node"), backend.get("id"))
            if route_key not in logged_routes:
                if backend_kind == "comfyui":
                    route_detail = f"LoadImage {validation['load_node']} -> SaveImage {validation['save_node']}"
                else:
                    route_detail = "external command adapter"
                self.log(
                    f"Workflow route: {preset} -> {route_label} | {route_detail} | "
                    f"{output_scale}x | {backend.get('name', 'Backend')}"
                )
                logged_routes.add(route_key)
                self._logged_workflow_routes = logged_routes

            self.stats["current_texture_stage"] = f"Waiting for {backend.get('name', 'backend')} / VRAM"
            self.wait_for_comfy_online(backend)
            self.wait_for_vram_budget()
            if self.stop_event.is_set():
                return "stopped"

            self.stats["current_texture_stage"] = f"Processing RGB via {backend.get('name', 'backend')}"
            self.stats["comfy_jobs"] += 1
            if task_type == "n64_strip_safe":
                self.log("N64 Strip Safe: conservative non-diffusion workflow submitted")
            elif preset in fr.BUILTIN_WORKFLOW_PROFILE_NAMES:
                self.log(f"{preset}: ControlNet + KSampler RGB job submitted")
            else:
                self.log(f"{preset}: RGB workflow job submitted")

            temp_png = run_backend_workflow_to_file(
                fr, backend, workflow_profile, processing_input,
                f"faithful_auto/{path.stem}", int(self.cfg.get("timeout_seconds", 900)), task_type,
            )
            temporary_paths.append(Path(temp_png))
            if strip:
                crop_strip_output(fr, temp_png, strip_info, output_scale)
            fr.validate_image_file(temp_png, expected_final_size, label=f"{preset} workflow output")

            if alpha:
                if strip:
                    fr.reattach_alpha(path, temp_png, "nearest", "original", 0.0)
                elif self.cfg.get("enable_separate_alpha_workflow", False) and self.cfg.get("alpha_workflow_api_json"):
                    try:
                        alpha_path = Path(str(self.cfg.get("alpha_workflow_api_json", "")))
                        alpha_backend = find_backend_profile(
                            fr, self.cfg.get("alpha_backend_id") or backend.get("id"), self.cfg
                        )
                        if not alpha_backend.get("enabled", True):
                            raise RuntimeError(f"Alpha backend is disabled: {alpha_backend.get('name')}")
                        alpha_kind = str(alpha_backend.get("type") or "comfyui").lower()
                        if alpha_kind == "comfyui":
                            alpha_validation = fr.validate_alpha_comfy_api_workflow(
                                alpha_path,
                                self.cfg.get("alpha_load_image_node_id", "1"),
                                self.cfg.get("alpha_save_image_node_id", "5"),
                                require_reachable=True,
                            )
                            alpha_load = alpha_validation["load_node"]
                            alpha_save = alpha_validation["save_node"]
                        else:
                            alpha_load = str(self.cfg.get("alpha_load_image_node_id", ""))
                            alpha_save = str(self.cfg.get("alpha_save_image_node_id", ""))
                        alpha_profile = {
                            "id": "alpha", "name": "Alpha", "api_path": str(alpha_path),
                            "ui_path": "", "load_node": alpha_load,
                            "save_node": alpha_save, "output_scale": output_scale,
                        }
                        self.stats["current_texture_stage"] = "Processing separate alpha"
                        self.stats["comfy_jobs"] += 1
                        alpha_temp_png = run_backend_workflow_to_file(
                            fr, alpha_backend, alpha_profile, path, f"alpha_auto/{path.stem}",
                            int(self.cfg.get("timeout_seconds", 900)), "alpha",
                        )
                        temporary_paths.append(Path(alpha_temp_png))
                        fr.validate_image_file(alpha_temp_png, expected_final_size, label="alpha workflow output")
                        fr.apply_alpha_image_to_rgba(
                            temp_png, alpha_temp_png,
                            bool(self.cfg.get("alpha_workflow_invert_output", False)),
                            reference_alpha_path=path,
                        )
                        self.log("Alpha workflow: validated and applied alpha output")
                    except Exception as exc:
                        self.log(f"Alpha workflow ERROR: {exc} -> original alpha restored")
                        fr.reattach_alpha(path, temp_png, "nearest", "original", 0.0)
                else:
                    fr.reattach_alpha(path, temp_png, "nearest", "original", 0.0)

                if self.cfg.get("fix_alpha_edge_bleed", False):
                    fr.alpha_edge_bleed_png(
                        temp_png,
                        int(self.cfg.get("alpha_bleed_iterations", 1)),
                        int(self.cfg.get("alpha_edge_threshold", 32)),
                    )

            fr.validate_image_file(temp_png, expected_final_size, label="final processed texture")
            self.stats["current_texture_stage"] = "Saving enhanced texture"
            fr.atomic_save_processed_image(temp_png, out_path)
            fr.validate_image_file(out_path, expected_final_size, label="saved texture")
            self.stats["current_output_path"] = str(out_path)
            self.stats["current_texture_stage"] = "Done"
            self.log(f"Saved output: {out_path.name}")

            if digest:
                cache_file = self.cache_file_for_hash(digest)
                fr.atomic_copy_file(temp_png, cache_file)
                old_entry = self.cache_index.get(digest, {}) if isinstance(self.cache_index.get(digest), dict) else {}
                source_paths = set(old_entry.get("source_paths", [])) if isinstance(old_entry.get("source_paths", []), list) else set()
                source_paths.add(str(path.resolve()))
                self.cache_index[digest] = {
                    "source_name": path.name,
                    "source_path": str(path.resolve()),
                    "source_paths": sorted(source_paths),
                    "cached_file": cache_file.name,
                    "pipeline": getattr(fr, "PROCESSING_PIPELINE_VERSION", _UNIVERSAL_LAYER_VERSION),
                    "workflow": workflow_profile.get("id"),
                }
                fr.save_cache_index(self.cache_index)

            self.stats["processed"] += 1
            return "done_cached" if digest else "done"
        finally:
            for temporary in reversed(temporary_paths):
                try:
                    Path(temporary).unlink(missing_ok=True)
                except Exception:
                    pass

    fr.Worker.process_one = process_one


# ---------------------------------------------------------------------------
# On-demand Clean Heart / Strong Believer comparison
# ---------------------------------------------------------------------------

def _comparison_alpha_route(fr, cfg, alpha, rgb_backend):
    route = {
        "enabled": bool(alpha and cfg.get("enable_separate_alpha_workflow", False)),
        "preserve_alpha": bool(alpha),
        "invert": bool(cfg.get("alpha_workflow_invert_output", False)),
        "resize": str(cfg.get("alpha_resize_method", "nearest")),
        "edge_bleed": bool(cfg.get("fix_alpha_edge_bleed", False)),
        "edge_iterations": int(cfg.get("alpha_bleed_iterations", 1) or 1),
        "edge_threshold": int(cfg.get("alpha_edge_threshold", 32) or 32),
    }
    if route["enabled"]:
        alpha_path = Path(str(cfg.get("alpha_workflow_api_json") or ""))
        alpha_backend = find_backend_profile(
            fr, cfg.get("alpha_backend_id") or rgb_backend.get("id"), cfg
        )
        route.update({
            "api_sha256": fr.workflow_file_fingerprint(alpha_path),
            "load_node": str(cfg.get("alpha_load_image_node_id", "1")),
            "save_node": str(cfg.get("alpha_save_image_node_id", "5")),
            "backend": backend_fingerprint(alpha_backend),
        })
    return route


def _comparison_alpha_file(fr, source_path, cfg, output_scale, rgb_backend, source_digest, log):
    """Return a validated alpha-workflow output, reusing a route-specific cache."""
    alpha_path = Path(str(cfg.get("alpha_workflow_api_json") or ""))
    alpha_backend = find_backend_profile(
        fr, cfg.get("alpha_backend_id") or rgb_backend.get("id"), cfg
    )
    if not alpha_backend.get("enabled", True):
        raise RuntimeError(f"Alpha backend is disabled: {alpha_backend.get('name', 'Alpha backend')}")
    alpha_kind = str(alpha_backend.get("type") or "comfyui").lower()
    if alpha_kind == "comfyui":
        validation = fr.validate_alpha_comfy_api_workflow(
            alpha_path,
            cfg.get("alpha_load_image_node_id", "1"),
            cfg.get("alpha_save_image_node_id", "5"),
            require_reachable=True,
        )
        alpha_load = validation["load_node"]
        alpha_save = validation["save_node"]
    else:
        alpha_load = str(cfg.get("alpha_load_image_node_id", ""))
        alpha_save = str(cfg.get("alpha_save_image_node_id", ""))
    alpha_profile = {
        "id": "alpha", "name": "Alpha", "api_path": str(alpha_path),
        "ui_path": "", "load_node": alpha_load, "save_node": alpha_save,
        "output_scale": output_scale,
    }
    alpha_semantic = {
        "purpose": "mode-comparison-alpha-v1",
        "pipeline": getattr(fr, "PROCESSING_PIPELINE_VERSION", _UNIVERSAL_LAYER_VERSION),
        "source": source_digest,
        "workflow": fr.workflow_file_fingerprint(alpha_path),
        "load_node": str(alpha_load), "save_node": str(alpha_save),
        "backend": backend_fingerprint(alpha_backend),
        "scale": int(output_scale),
        "invert": bool(cfg.get("alpha_workflow_invert_output", False)),
    }
    alpha_digest = hashlib.sha256(
        json.dumps(alpha_semantic, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cache_file = fr.CACHE_DIR / f"compare_alpha_{alpha_digest}.png"
    expected = None
    size = fr.validate_image_file(source_path, label="comparison source")
    if size:
        expected = (size[0] * int(output_scale), size[1] * int(output_scale))
    if cache_file.is_file():
        try:
            fr.validate_image_file(cache_file, expected, label="cached comparison alpha")
            return cache_file, True
        except Exception:
            cache_file.unlink(missing_ok=True)
    if log:
        log("Comparison: generating shared Alpha preview")
    temp = run_backend_workflow_to_file(
        fr, alpha_backend, alpha_profile, source_path,
        f"faithful_compare/alpha_{Path(source_path).stem}",
        int(cfg.get("timeout_seconds", 900)), "alpha",
    )
    try:
        fr.validate_image_file(temp, expected, label="comparison alpha workflow output")
        fr.atomic_copy_file(temp, cache_file)
        return cache_file, False
    finally:
        Path(temp).unlink(missing_ok=True)


def generate_mode_comparison_output(fr, source_path, cfg, preset, force=False, log=None):
    """Generate one non-destructive comparison preview using the production route.

    The result is written only to the global hash cache. It never touches the
    game's Load folder, processed log, queue, per-texture override or current
    output. The route fingerprint matches production processing, so an existing
    valid production cache can be reused and a generated comparison can later be
    reused by normal processing.
    """
    source_path = Path(source_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"Texture no longer exists: {source_path}")
    if preset not in ("Clean Heart", "Strong Believer"):
        raise ValueError(f"Unsupported comparison mode: {preset}")

    workflow_profile = fr.find_workflow_profile_exact(preset)
    if not workflow_profile or not workflow_profile.get("enabled", True):
        raise RuntimeError(f"Workflow profile is missing or disabled: {preset}")
    task_type = str(workflow_profile.get("task_type") or "standard")
    backend = find_backend_profile(
        fr, workflow_profile.get("backend_id") or cfg.get("active_backend_id"), cfg
    )
    if not backend.get("enabled", True):
        raise RuntimeError(f"Processing backend is disabled: {backend.get('name', 'Backend')}")

    backend_kind = str(backend.get("type") or "comfyui").lower()
    if backend_kind == "comfyui":
        fr.validate_comfy_api_workflow(
            workflow_profile.get("api_path") or "",
            workflow_profile.get("load_node") or "1",
            workflow_profile.get("save_node") or "4",
            require_reachable=True,
        )
        status = check_processing_backend(fr, backend)
        if not status.get("online") and cfg.get("auto_start_comfy_when_watching", True):
            if log:
                log(f"Comparison: waiting for {backend.get('name', 'ComfyUI')} to come online")
            deadline = time.monotonic() + 60.0
            while time.monotonic() < deadline and not status.get("online"):
                time.sleep(2.0)
                status = check_processing_backend(fr, backend)
        if not status.get("online"):
            raise RuntimeError(
                f"{backend.get('name', 'ComfyUI')} is offline. Start the backend, then press Refresh comparison."
            )

    output_scale = max(1, int(float(workflow_profile.get("output_scale") or 4)))
    source_size = fr.validate_image_file(source_path, label="comparison source texture")
    expected_size = (
        (source_size[0] * output_scale, source_size[1] * output_scale)
        if source_size else None
    )
    alpha = bool(cfg.get("preserve_alpha", True) and fr.has_alpha(source_path))
    source_digest = fr.sha1_file(source_path)
    alpha_route = _comparison_alpha_route(fr, cfg, alpha, backend)
    route_fingerprint = {
        "pipeline": getattr(fr, "PROCESSING_PIPELINE_VERSION", _UNIVERSAL_LAYER_VERSION),
        "workflow": fr.workflow_profile_fingerprint(workflow_profile),
        "backend": backend_fingerprint(backend),
        "task_type": task_type,
        "strip_padding": "",  # CH/SB comparison intentionally bypasses hidden N64 Strip Safe.
        "alpha": alpha_route,
    }
    digest = hashlib.sha256(
        (source_digest + "|" + json.dumps(route_fingerprint, sort_keys=True)).encode("utf-8")
    ).hexdigest()
    cache_file = fr.CACHE_DIR / f"{digest}.png"
    fr.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if cache_file.is_file() and not force:
        try:
            fr.validate_image_file(cache_file, expected_size, label=f"cached {preset} comparison")
            return {
                "path": cache_file, "cache_hit": True, "preset": preset,
                "output_scale": output_scale, "alpha_fallback": False,
            }
        except Exception:
            cache_file.unlink(missing_ok=True)

    temporary = []
    alpha_fallback = False
    try:
        if log:
            log(f"Comparison: generating {preset}")
        temp_png = run_backend_workflow_to_file(
            fr, backend, workflow_profile, source_path,
            f"faithful_compare/{preset.replace(' ', '_').lower()}_{source_path.stem}",
            int(cfg.get("timeout_seconds", 900)), task_type,
        )
        temp_png = Path(temp_png)
        temporary.append(temp_png)
        fr.validate_image_file(temp_png, expected_size, label=f"{preset} comparison workflow output")

        if alpha:
            if cfg.get("enable_separate_alpha_workflow", False) and cfg.get("alpha_workflow_api_json"):
                try:
                    alpha_file, _alpha_cache_hit = _comparison_alpha_file(
                        fr, source_path, cfg, output_scale, backend, source_digest, log
                    )
                    fr.apply_alpha_image_to_rgba(
                        temp_png, alpha_file,
                        bool(cfg.get("alpha_workflow_invert_output", False)),
                        reference_alpha_path=source_path,
                    )
                except Exception as exc:
                    alpha_fallback = True
                    if log:
                        log(f"Comparison Alpha fallback for {preset}: {exc}")
                    fr.reattach_alpha(source_path, temp_png, "nearest", "original", 0.0)
            else:
                fr.reattach_alpha(source_path, temp_png, "nearest", "original", 0.0)
            if cfg.get("fix_alpha_edge_bleed", False):
                fr.alpha_edge_bleed_png(
                    temp_png,
                    int(cfg.get("alpha_bleed_iterations", 1)),
                    int(cfg.get("alpha_edge_threshold", 32)),
                )

        fr.validate_image_file(temp_png, expected_size, label=f"final {preset} comparison")
        fr.atomic_copy_file(temp_png, cache_file)
        fr.validate_image_file(cache_file, expected_size, label=f"saved {preset} comparison")
        return {
            "path": cache_file, "cache_hit": False, "preset": preset,
            "output_scale": output_scale, "alpha_fallback": alpha_fallback,
        }
    finally:
        for path in reversed(temporary):
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Backend startup/status lifecycle
# ---------------------------------------------------------------------------

def _patch_backend_lifecycle(fr):
    def launch(self, cfg, show_errors=True):
        backend = find_backend_profile(fr, cfg.get("active_backend_id"), cfg)
        ok = _launch_backend(fr, backend, show_errors)
        if not ok and show_errors:
            try:
                fr.messagebox.showinfo("Processing Backend", "This backend has no launch file. Start it manually or configure one in Settings → Workflows & Backends.")
            except Exception:
                pass
        return ok

    def ensure(self, cfg):
        if not cfg.get("auto_start_comfy_when_watching", True):
            return
        backend = find_backend_profile(fr, cfg.get("active_backend_id"), cfg)
        status = check_processing_backend(fr, backend)
        if not status.get("online"):
            _launch_backend(fr, backend, False)

    fr.App._launch_comfy_ui = launch
    fr.App.ensure_comfy_started_for_watching = ensure


def _required_workflow_profiles(fr, cfg, profile_dir):
    required = {}
    default = fr.find_workflow_profile_exact(cfg.get("faithfulness_preset", "Clean Heart"))
    if default is None:
        raise fr.WorkflowValidationError(
            f"Selected game mode does not exist: {cfg.get('faithfulness_preset')!r}"
        )
    required[default["id"]] = default

    try:
        overrides = fr.load_texture_preset_overrides(profile_dir)
    except Exception:
        overrides = {}
    for profile_id in set(overrides.values()):
        profile = fr.find_workflow_profile_exact(profile_id)
        if profile is not None:
            required[profile["id"]] = profile

    if is_n64_profile(cfg) and cfg.get("n64_strip_safe_enabled", True):
        strip_profile = fr.find_workflow_profile_exact(
            cfg.get("n64_strip_workflow_profile", "n64_strip_safe")
        )
        if strip_profile is None:
            raise fr.WorkflowValidationError("Configured N64 Strip Safe workflow profile does not exist.")
        required[strip_profile["id"]] = strip_profile
    return list(required.values())


def validate_runtime_routes(fr, cfg, profile_dir):
    """Validate the routes the worker will actually execute for this game."""
    dump_text = str(cfg.get("dump_folder") or "").strip()
    load_text = str(cfg.get("load_folder") or "").strip()
    if not dump_text:
        raise ValueError("Missing Dump folder.")
    if not load_text:
        raise ValueError("Missing Load folder.")
    dump_folder = Path(dump_text)
    load_folder = Path(load_text)
    if not dump_folder.is_dir():
        raise ValueError(f"Dump folder does not exist: {dump_folder}")
    try:
        load_folder.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise ValueError(f"Load folder cannot be created or opened: {load_folder} ({exc})") from exc
    if fr.is_path_within(load_folder, dump_folder):
        raise ValueError(
            "Load folder must not be the Dump folder or a subfolder of it; otherwise generated outputs can be dumped and processed recursively."
        )
    if not fr.PIL_AVAILABLE and (cfg.get("preserve_alpha") or cfg.get("process_tmp_image_files")):
        raise ValueError("Pillow is required for texture validation and alpha preservation.")

    backend_data = load_backend_profiles(fr)
    backend_by_id = {row.get("id"): row for row in backend_data.get("backends", [])}
    active_backend_id = str(cfg.get("active_backend_id") or backend_data.get("active_backend_id") or "")
    if active_backend_id not in backend_by_id:
        raise ValueError(f"Active processing backend does not exist: {active_backend_id!r}")

    validated = []
    for profile in _required_workflow_profiles(fr, cfg, profile_dir):
        if not profile.get("enabled", True):
            raise fr.WorkflowValidationError(f"Required workflow profile is disabled: {profile.get('name')}")
        backend_id = str(profile.get("backend_id") or active_backend_id)
        backend = backend_by_id.get(backend_id)
        if backend is None:
            raise ValueError(
                f"Workflow {profile.get('name')!r} refers to missing backend {backend_id!r}."
            )
        if not backend.get("enabled", True):
            raise ValueError(f"Workflow {profile.get('name')!r} uses disabled backend {backend.get('name')!r}.")
        kind = str(backend.get("type") or "comfyui").lower()
        if kind == "comfyui":
            check = fr.validate_comfy_api_workflow(
                profile.get("api_path") or "",
                profile.get("load_node") or "1",
                profile.get("save_node") or "4",
                require_reachable=True,
            )
        elif kind == "external_command":
            if not str(backend.get("command_template") or "").strip():
                raise ValueError(f"External backend {backend.get('name')!r} has no command template.")
            check = {"path": Path(str(profile.get("api_path") or "")), "load_node": profile.get("load_node"), "save_node": profile.get("save_node")}
        else:
            raise ValueError(f"Unsupported backend type for {profile.get('name')}: {kind}")
        try:
            scale = int(float(profile.get("output_scale") or 4))
        except Exception as exc:
            raise ValueError(f"Invalid output scale for {profile.get('name')}: {profile.get('output_scale')}") from exc
        if scale < 1:
            raise ValueError(f"Output scale must be at least 1 for {profile.get('name')}.")
        validated.append((profile, backend, check))

    if cfg.get("preserve_alpha", True) and cfg.get("enable_separate_alpha_workflow", False):
        alpha_path = str(cfg.get("alpha_workflow_api_json") or "").strip()
        if not alpha_path:
            raise fr.WorkflowValidationError("Separate alpha workflow is enabled but no Alpha API file is selected.")
        alpha_backend_id = str(cfg.get("alpha_backend_id") or active_backend_id)
        alpha_backend = backend_by_id.get(alpha_backend_id)
        if alpha_backend is None:
            raise ValueError(f"Alpha workflow refers to missing backend {alpha_backend_id!r}.")
        if not alpha_backend.get("enabled", True):
            raise ValueError(f"Alpha workflow uses disabled backend {alpha_backend.get('name')!r}.")
        if str(alpha_backend.get("type") or "comfyui").lower() == "comfyui":
            fr.validate_alpha_comfy_api_workflow(
                alpha_path,
                cfg.get("alpha_load_image_node_id", "1"),
                cfg.get("alpha_save_image_node_id", "5"),
                require_reachable=True,
            )
        elif not str(alpha_backend.get("command_template") or "").strip():
            raise ValueError(f"External alpha backend {alpha_backend.get('name')!r} has no command template.")
    return validated


# ---------------------------------------------------------------------------
# Configuration and UI
# ---------------------------------------------------------------------------

def _patch_config_and_profile_loading(fr):
    original_collect = fr.V11App.collect
    original_load_profile = fr.V11App.load_profile

    def collect(self):
        cfg = original_collect(self)
        if hasattr(self, "active_backend_id_var"):
            label = self.active_backend_id_var.get()
            cfg["active_backend_id"] = getattr(self, "backend_name_to_id", {}).get(label, label or "default_comfyui")
        if hasattr(self, "n64_strip_enabled_var"):
            cfg["n64_strip_safe_enabled"] = bool(self.n64_strip_enabled_var.get())
            cfg["n64_strip_max_height"] = int(float(self.n64_strip_max_height_var.get() or 16))
            cfg["n64_strip_min_aspect_ratio"] = float(self.n64_strip_ratio_var.get() or 20)
            cfg["n64_strip_padding_pixels"] = int(float(self.n64_strip_padding_var.get() or 16))
            cfg["n64_strip_workflow_profile"] = self.n64_strip_workflow_var.get() or "n64_strip_safe"
        if hasattr(self, "game_artwork_enabled_var"):
            cfg["game_artwork_enabled"] = bool(self.game_artwork_enabled_var.get())
        if hasattr(self, "auto_restart_backend_var"):
            cfg["auto_restart_backend_when_offline"] = bool(self.auto_restart_backend_var.get())
            cfg["backend_restart_offline_grace_seconds"] = max(3, int(float(self.backend_restart_grace_var.get() or 15)))
            cfg["backend_restart_retry_seconds"] = max(15, int(float(self.backend_restart_retry_var.get() or 60)))
            cfg["backend_interrupted_job_retries"] = max(0, int(float(self.backend_job_retry_var.get() or 1)))
        # Keep legacy fields synchronized with the selected ComfyUI backend so
        # old profiles and the footer/status widgets remain functional.
        backend = find_backend_profile(fr, cfg.get("active_backend_id"), cfg)
        if backend.get("type") == "comfyui":
            cfg["comfy_url"] = backend.get("api_url") or cfg.get("comfy_url")
            cfg["comfy_start_file"] = backend.get("start_file") or cfg.get("comfy_start_file")
        return cfg

    def load_profile(self, name, save_current=True):
        result = original_load_profile(self, name, save_current)
        settings = self.cfg
        if hasattr(self, "active_backend_id_var"):
            bid = settings.get("active_backend_id", "default_comfyui")
            self.active_backend_id_var.set(getattr(self, "backend_id_to_name", {}).get(bid, bid))
        if hasattr(self, "n64_strip_enabled_var"):
            self.n64_strip_enabled_var.set(bool(settings.get("n64_strip_safe_enabled", True)))
            self.n64_strip_max_height_var.set(str(settings.get("n64_strip_max_height", 16)))
            self.n64_strip_ratio_var.set(str(settings.get("n64_strip_min_aspect_ratio", 20.0)))
            self.n64_strip_padding_var.set(str(settings.get("n64_strip_padding_pixels", 16)))
            self.n64_strip_workflow_var.set(str(settings.get("n64_strip_workflow_profile", "n64_strip_safe")))
        if hasattr(self, "game_artwork_enabled_var"):
            self.game_artwork_enabled_var.set(bool(settings.get("game_artwork_enabled", True)))
        if hasattr(self, "auto_restart_backend_var"):
            self.auto_restart_backend_var.set(bool(settings.get("auto_restart_backend_when_offline", True)))
            self.backend_restart_grace_var.set(str(settings.get("backend_restart_offline_grace_seconds", 15)))
            self.backend_restart_retry_var.set(str(settings.get("backend_restart_retry_seconds", 60)))
            self.backend_job_retry_var.set(str(settings.get("backend_interrupted_job_retries", 1)))
        if hasattr(self, "profile_artwork_path_var"):
            record = self.profile_data.get("profiles", {}).get(name, {})
            self.profile_artwork_path_var.set(str(record.get("artwork_path") or ""))
        _refresh_artwork_views(self, force=False)
        return result

    def validate(self, cfg):
        profile_dir = Path(cfg.get("profile_dir") or (fr.PROFILES_DIR / fr.safe_profile_name(self.current_profile_name)))
        validated = validate_runtime_routes(fr, cfg, profile_dir)
        self._last_validated_routes = validated
        return True

    fr.V11App.collect = collect
    fr.V11App.load_profile = load_profile
    fr.V11App.validate = validate

    original_record = fr.V11App.profile_record_from_ui
    def profile_record(self):
        record = original_record(self)
        if hasattr(self, "profile_artwork_path_var"):
            record["artwork_path"] = self.profile_artwork_path_var.get().strip()
        existing = self.profile_data.get("profiles", {}).get(self.current_profile_name, {})
        for key in ("title_source", "artwork_source", "azahar_icon_path", "publisher"):
            if key in existing and key not in record:
                record[key] = existing[key]
        return record
    fr.V11App.profile_record_from_ui = profile_record


def _patch_settings_ui(fr):
    original_workflows = fr.V11App.build_workflows_tab
    original_processing = fr.V11App.build_processing_tab

    def build_workflows(self):
        _build_backend_manager(self, fr)
        original_workflows(self)
        # Make the legacy card self-explanatory without removing backwards compatibility.
        try:
            for child in self.tab_workflows.winfo_children():
                for desc in child.winfo_children():
                    pass
        except Exception:
            pass

    def build_processing(self):
        original_processing(self)

        recovery = self.section(self.tab_processing, "Automatic backend recovery")
        self.auto_restart_backend_var = fr.tk.BooleanVar(
            value=bool(self.cfg.get("auto_restart_backend_when_offline", True))
        )
        fr.tk.Checkbutton(
            recovery,
            text="Auto-restart the processing backend if it goes offline while watching",
            variable=self.auto_restart_backend_var
        ).pack(anchor="w", padx=12, pady=(6, 4))
        row = fr.tk.Frame(recovery); row.pack(fill="x", padx=12, pady=4)
        fr.tk.Label(row, text="Offline grace before restart", width=28, anchor="w").pack(side="left")
        self.backend_restart_grace_var = fr.tk.StringVar(
            value=str(self.cfg.get("backend_restart_offline_grace_seconds", 15))
        )
        fr.tk.Entry(row, textvariable=self.backend_restart_grace_var, width=8).pack(side="left")
        fr.tk.Label(row, text="seconds", padx=6).pack(side="left")
        fr.tk.Label(row, text="Retry every").pack(side="left", padx=(24, 6))
        self.backend_restart_retry_var = fr.tk.StringVar(
            value=str(self.cfg.get("backend_restart_retry_seconds", 60))
        )
        fr.tk.Entry(row, textvariable=self.backend_restart_retry_var, width=8).pack(side="left")
        fr.tk.Label(row, text="seconds").pack(side="left", padx=6)
        row2 = fr.tk.Frame(recovery); row2.pack(fill="x", padx=12, pady=(2, 4))
        fr.tk.Label(row2, text="Retry a texture interrupted by restart", width=36, anchor="w").pack(side="left")
        self.backend_job_retry_var = fr.tk.StringVar(
            value=str(self.cfg.get("backend_interrupted_job_retries", 1))
        )
        fr.tk.Entry(row2, textvariable=self.backend_job_retry_var, width=8).pack(side="left")
        fr.tk.Label(row2, text="time(s)").pack(side="left", padx=6)
        fr.tk.Label(
            recovery,
            text=("The watchdog runs for the whole watching session—even during a long texture job. "
                  "It pauses the queue, launches the active backend's Start file, waits for the API, "
                  "and keeps retrying without opening duplicate instances."),
            fg="#71b9d6", anchor="w", justify="left", wraplength=980
        ).pack(fill="x", padx=32, pady=(0, 8))

        card = self.section(self.tab_processing, "N64 strip-safe routing")
        self.n64_strip_enabled_var = fr.tk.BooleanVar(value=bool(self.cfg.get("n64_strip_safe_enabled", True)))
        fr.tk.Checkbutton(
            card, text="Automatically route extreme N64 strips to a conservative workflow",
            variable=self.n64_strip_enabled_var
        ).pack(anchor="w", padx=12, pady=(5, 3))
        row = fr.tk.Frame(card); row.pack(fill="x", padx=12, pady=4)
        fr.tk.Label(row, text="Maximum thin side", width=24, anchor="w").pack(side="left")
        self.n64_strip_max_height_var = fr.tk.StringVar(value=str(self.cfg.get("n64_strip_max_height", 16)))
        fr.tk.Entry(row, textvariable=self.n64_strip_max_height_var, width=8).pack(side="left")
        fr.tk.Label(row, text="Minimum aspect ratio", padx=18).pack(side="left")
        self.n64_strip_ratio_var = fr.tk.StringVar(value=str(self.cfg.get("n64_strip_min_aspect_ratio", 20.0)))
        fr.tk.Entry(row, textvariable=self.n64_strip_ratio_var, width=8).pack(side="left")
        fr.tk.Label(row, text="Mirror padding", padx=18).pack(side="left")
        self.n64_strip_padding_var = fr.tk.StringVar(value=str(self.cfg.get("n64_strip_padding_pixels", 16)))
        fr.tk.Entry(row, textvariable=self.n64_strip_padding_var, width=8).pack(side="left")
        row2 = fr.tk.Frame(card); row2.pack(fill="x", padx=12, pady=(3, 7))
        fr.tk.Label(row2, text="Workflow profile", width=24, anchor="w").pack(side="left")
        hidden_profiles = [p for p in fr.load_workflow_profiles() if p.get("task_type") == "n64_strip_safe"]
        values = [p["id"] for p in hidden_profiles] or ["n64_strip_safe"]
        self.n64_strip_workflow_var = fr.tk.StringVar(value=str(self.cfg.get("n64_strip_workflow_profile", "n64_strip_safe")))
        fr.ttk.Combobox(row2, textvariable=self.n64_strip_workflow_var, values=values, state="readonly", width=28).pack(side="left")
        fr.tk.Label(
            card,
            text="The workflow UI/API files remain external and replaceable. The app only detects, pads, routes and crops the strip.",
            fg="#71b9d6", anchor="w", justify="left"
        ).pack(fill="x", padx=12, pady=(0, 8))

        art = self.section(self.tab_processing, "Game artwork")
        self.game_artwork_enabled_var = fr.tk.BooleanVar(value=bool(self.cfg.get("game_artwork_enabled", True)))
        fr.tk.Checkbutton(art, text="Download and cache game artwork for profile identity cards", variable=self.game_artwork_enabled_var).pack(anchor="w", padx=12, pady=5)
        fr.tk.Label(art, text="Artwork is optional, cached locally and never blocks the UI. Manual images always override the provider.", fg="#91a4b7").pack(anchor="w", padx=32, pady=(0, 8))

    fr.V11App.build_workflows_tab = build_workflows
    fr.V11App.build_processing_tab = build_processing


def _build_backend_manager(self, fr):
    card = self.section(self.tab_workflows, "Processing Backend Manager")
    fr.tk.Label(
        card,
        text="Choose the engine that executes external UI/API workflows. ComfyUI is bundled as an adapter; custom command backends can be added without changing the app core.",
        fg="#9fb1bf", anchor="w", justify="left", wraplength=980
    ).pack(fill="x", padx=12, pady=(7, 5))

    data = load_backend_profiles(fr)
    self.backend_profiles_cache = data["backends"]
    self.backend_id_to_name = {b["id"]: b["name"] for b in data["backends"]}
    self.backend_name_to_id = {b["name"]: b["id"] for b in data["backends"]}

    active_row = fr.tk.Frame(card); active_row.pack(fill="x", padx=12, pady=5)
    fr.tk.Label(active_row, text="Active backend", width=20, anchor="w").pack(side="left")
    active_id = self.cfg.get("active_backend_id", data["active_backend_id"])
    self.active_backend_id_var = fr.tk.StringVar(value=self.backend_id_to_name.get(active_id, active_id))
    self.active_backend_combo = fr.ttk.Combobox(
        active_row, textvariable=self.active_backend_id_var,
        values=tuple(self.backend_name_to_id), state="readonly", width=34
    )
    self.active_backend_combo.pack(side="left", fill="x", expand=True)

    body = fr.tk.Frame(card); body.pack(fill="both", padx=12, pady=(4, 8))
    body.grid_columnconfigure(1, weight=1)
    listbox = fr.tk.Listbox(body, height=6, exportselection=False)
    listbox.grid(row=0, column=0, rowspan=7, sticky="nsew", padx=(0, 12))
    body.grid_rowconfigure(6, weight=1)
    for backend in data["backends"]:
        listbox.insert("end", f"{backend['name']}  —  {backend['type']}")

    vars_ = {
        "name": fr.tk.StringVar(), "type": fr.tk.StringVar(), "api_url": fr.tk.StringVar(),
        "start_file": fr.tk.StringVar(), "command_template": fr.tk.StringVar(),
    }
    labels = [
        ("Backend name", "name"), ("Backend type", "type"), ("API URL", "api_url"),
        ("Start file", "start_file"), ("Command template", "command_template"),
    ]
    for row, (label, key) in enumerate(labels):
        fr.tk.Label(body, text=label, width=18, anchor="w").grid(row=row, column=1, sticky="w", pady=3)
        if key == "type":
            widget = fr.ttk.Combobox(body, textvariable=vars_[key], values=("comfyui", "external_command"), state="readonly")
        else:
            widget = fr.tk.Entry(body, textvariable=vars_[key])
        widget.grid(row=row, column=2, sticky="ew", pady=3)
        if key == "start_file":
            fr.tk.Button(body, text="Browse", command=lambda: _browse_backend_start(self, vars_["start_file"])).grid(row=row, column=3, padx=(6, 0))

    enabled_var = fr.tk.BooleanVar(value=True)
    fr.tk.Checkbutton(body, text="Enabled", variable=enabled_var).grid(row=5, column=2, sticky="w", pady=3)
    current_id = {"value": None}

    def populate(index=0):
        if not self.backend_profiles_cache:
            return
        index = max(0, min(index, len(self.backend_profiles_cache) - 1))
        row = self.backend_profiles_cache[index]
        current_id["value"] = row["id"]
        for key in vars_:
            vars_[key].set(str(row.get(key, "")))
        enabled_var.set(bool(row.get("enabled", True)))
        listbox.selection_clear(0, "end")
        listbox.selection_set(index)

    def selected(_event=None):
        sel = listbox.curselection()
        if sel:
            populate(sel[0])

    def refresh(select_id=None):
        current = load_backend_profiles(fr)
        self.backend_profiles_cache = current["backends"]
        self.backend_id_to_name = {b["id"]: b["name"] for b in current["backends"]}
        self.backend_name_to_id = {b["name"]: b["id"] for b in current["backends"]}
        self.active_backend_combo.configure(values=tuple(self.backend_name_to_id))
        listbox.delete(0, "end")
        selected_index = 0
        for i, row in enumerate(self.backend_profiles_cache):
            listbox.insert("end", f"{row['name']}  —  {row['type']}")
            if select_id and row["id"] == select_id:
                selected_index = i
        populate(selected_index)

    def save_current():
        bid = current_id["value"] or _slug(vars_["name"].get())
        if not vars_["name"].get().strip():
            return fr.messagebox.showerror("Backend", "Enter a backend name.")
        row = {
            "id": bid, "name": vars_["name"].get().strip(), "type": vars_["type"].get().strip(),
            "api_url": vars_["api_url"].get().strip(), "start_file": vars_["start_file"].get().strip(),
            "command_template": vars_["command_template"].get().strip(), "enabled": enabled_var.get(),
        }
        rows = load_backend_profiles(fr)["backends"]
        replaced = False
        for i, existing in enumerate(rows):
            if existing["id"] == bid:
                rows[i] = row; replaced = True; break
        if not replaced:
            rows.append(row)
        active_label = self.active_backend_id_var.get()
        active = self.backend_name_to_id.get(active_label, active_label or bid)
        save_backend_profiles(fr, rows, active)
        refresh(bid)
        self.log(f"Backend saved: {row['name']}")

    def add_new():
        current_id["value"] = None
        for key, var in vars_.items():
            var.set("comfyui" if key == "type" else "")
        enabled_var.set(True)
        listbox.selection_clear(0, "end")

    def duplicate():
        sel = listbox.curselection()
        if not sel:
            return
        row = dict(self.backend_profiles_cache[sel[0]])
        row["id"] = _slug(row["name"] + " copy")
        row["name"] += " Copy"
        rows = self.backend_profiles_cache + [row]
        save_backend_profiles(fr, rows, load_backend_profiles(fr)["active_backend_id"])
        refresh(row["id"])

    def delete():
        bid = current_id["value"]
        if not bid or bid == "default_comfyui":
            return fr.messagebox.showinfo("Backend", "The default backend is retained for compatibility.")
        rows = [x for x in self.backend_profiles_cache if x["id"] != bid]
        save_backend_profiles(fr, rows, "default_comfyui")
        refresh()

    def test():
        row = {
            "type": vars_["type"].get(), "api_url": vars_["api_url"].get(),
            "command_template": vars_["command_template"].get(), "name": vars_["name"].get(),
        }
        def worker():
            status = check_processing_backend(fr, row)
            self.after(0, lambda: fr.messagebox.showinfo(
                "Backend Test", f"{row['name'] or 'Backend'}\n\n" +
                ("Ready / Online" if status.get("online") else f"Unavailable: {status.get('error')}")
            ))
        threading.Thread(target=worker, daemon=True).start()

    listbox.bind("<<ListboxSelect>>", selected)
    controls = fr.tk.Frame(body); controls.grid(row=6, column=1, columnspan=3, sticky="ew", pady=(7, 0))
    for text, command in (("New", add_new), ("Duplicate", duplicate), ("Save", save_current), ("Test", test), ("Delete", delete)):
        fr.tk.Button(controls, text=text, command=command).pack(side="left", padx=3)
    fr.tk.Label(
        card,
        text='External command placeholders: {input}, {output}, {workflow_api}, {workflow_ui}, {preset}, {task_type}, {scale}',
        fg="#71b9d6", anchor="w"
    ).pack(fill="x", padx=12, pady=(0, 9))
    populate(0)


def _browse_backend_start(self, var):
    path = self.filedialog.askopenfilename() if hasattr(self, "filedialog") else ""
    if not path:
        try:
            from tkinter import filedialog
            path = filedialog.askopenfilename(filetypes=[("Launch files", "*.bat *.cmd *.exe *.py"), ("All files", "*.*")])
        except Exception:
            path = ""
    if path:
        var.set(path)


def _slug(text):
    value = re.sub(r"[^a-z0-9]+", "_", str(text).casefold()).strip("_")
    return value or f"backend_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Artwork
# ---------------------------------------------------------------------------

def _artwork_sources(fr):
    return _read_json(fr.BUNDLED_ARTWORK_SOURCES_PATH, {})


def _safe_art_key(record, profile_name=""):
    raw = "|".join([
        str(record.get("emulator") or ""), str(record.get("game_id") or ""),
        str(record.get("game_name") or profile_name),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _thumbnail_filename(value):
    # Libretro thumbnails replace filesystem-forbidden punctuation with underscores.
    value = str(value or "").strip()
    value = re.sub(r'[\\/:*?"<>|]', "_", value)
    return value.rstrip(". ")


def _title_variants(record, profile_name=""):
    title = str(record.get("game_name") or profile_name).strip()
    variants = []
    for candidate in (title, re.sub(r"\s*\[[^\]]+\]\s*$", "", title)):
        candidate = _thumbnail_filename(candidate)
        if candidate and candidate not in variants:
            variants.append(candidate)
    return variants


def _failure_cache(fr):
    return _read_json(fr.ARTWORK_FAILURES_PATH, {})


def _save_failure_cache(fr, data):
    _atomic_json(fr.ARTWORK_FAILURES_PATH, data)


def profile_artwork_path(fr, record, profile_name="", force=False):
    manual = Path(str(record.get("artwork_path") or ""))
    if manual.is_file():
        return manual
    # Azahar's native SMDH icon is more accurate than an online box-art guess.
    native_azahar_icon = Path(str(record.get("azahar_icon_path") or ""))
    if str(record.get("emulator") or "") == "Azahar / Citra" and native_azahar_icon.is_file():
        return native_azahar_icon
    key = _safe_art_key(record, profile_name)
    cached = fr.ARTWORK_DIR / f"{key}.png"
    if cached.is_file():
        return cached
    if not force:
        failures = _failure_cache(fr)
        last = float(failures.get(key, 0) or 0)
        if time.time() - last < 24 * 3600:
            return None
    return None


def download_profile_artwork(fr, record, profile_name="", force=False):
    existing = profile_artwork_path(fr, record, profile_name, force=force)
    if existing:
        return existing, "cache/manual"
    sources = _artwork_sources(fr)
    repos = sources.get("repositories", {}).get(str(record.get("emulator") or ""), [])
    bases = list(sources.get("base_urls") or [])
    if not bases and sources.get("base_url"):
        bases = [str(sources.get("base_url"))]
    style = str(sources.get("style") or "Named_Boxarts")
    key = _safe_art_key(record, profile_name)
    target = fr.ARTWORK_DIR / f"{key}.png"
    headers = {"User-Agent": "Faithful-Remaster/11.9"}
    for repo in repos:
        repo_slug = str(repo).replace(" - ", "_-_").replace(" ", "_")
        for title in _title_variants(record, profile_name):
            for base in bases:
                url = str(base).format(
                    repository=urllib.parse.quote(str(repo), safe=""),
                    repository_slug=urllib.parse.quote(repo_slug, safe=""),
                    style=urllib.parse.quote(style, safe="/"),
                    title=urllib.parse.quote(title, safe=""),
                )
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=8) as response:
                        payload = response.read(8 * 1024 * 1024)
                    if len(payload) < 128:
                        continue
                    temp = target.with_suffix(".tmp")
                    temp.write_bytes(payload)
                    if PIL_OK:
                        with Image.open(temp) as im:
                            im.verify()
                    temp.replace(target)
                    failures = _failure_cache(fr); failures.pop(key, None); _save_failure_cache(fr, failures)
                    return target, f"Libretro: {repo}/{style}"
                except Exception:
                    continue
    failures = _failure_cache(fr); failures[key] = time.time(); _save_failure_cache(fr, failures)
    return None, "not found"


def _platform_abbrev(emulator):
    mapping = {
        "Dolphin": "GC/Wii", "PCSX2": "PS2", "PPSSPP": "PSP", "DuckStation": "PS1",
        "Azahar / Citra": "3DS", "Nintendo 64 / RMG": "N64", "Nintendo 64 / Project64": "N64",
        "Flycast — Dreamcast / Naomi / Atomiswave": "DC",
    }
    return mapping.get(str(emulator), "GAME")


def _fallback_art(fr, record, profile_name, size):
    if not PIL_OK:
        return None
    key = hashlib.sha1(f"fallback|{record.get('emulator')}|{size}".encode()).hexdigest()
    target = fr.ARTWORK_DIR / f"fallback_{key}.png"
    if target.exists():
        return target
    image = Image.new("RGBA", (size, size), (9, 18, 27, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, size - 3, size - 3), radius=max(4, size // 8), fill=(14, 43, 52, 255), outline=(52, 214, 198, 255), width=max(1, size // 24))
    text = _platform_abbrev(record.get("emulator"))
    try:
        font = ImageFont.truetype("arialbd.ttf", max(9, size // 5))
    except Exception:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((size - (box[2] - box[0])) / 2, (size - (box[3] - box[1])) / 2), text, fill=(235, 248, 246, 255), font=font)
    image.save(target)
    return target


def _render_art_label(label, path, size, fr):
    if not PIL_OK or label is None:
        return
    try:
        with Image.open(path) as source:
            source = source.convert("RGBA")
            source.thumbnail((size, size), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (size, size), (6, 12, 18, 255))
            x = (size - source.width) // 2; y = (size - source.height) // 2
            canvas.paste(source, (x, y), source)
        photo = ImageTk.PhotoImage(canvas)
        label.configure(image=photo, text="")
        label._art_photo = photo
    except Exception:
        pass


def _current_record(self):
    return self.profile_data.get("profiles", {}).get(self.current_profile_name, {}) if self.current_profile_name else {}


def _refresh_artwork_views(self, force=False):
    if not getattr(self, "current_profile_name", ""):
        return
    record = _current_record(self)
    enabled = bool(self.cfg.get("game_artwork_enabled", True))
    if hasattr(self, "game_artwork_enabled_var"):
        enabled = bool(self.game_artwork_enabled_var.get())
    labels = [
        (getattr(self, "header_game_art_label", None), 54),
        (getattr(self, "profile_summary_art_label", None), 72),
        (getattr(self, "batch_game_art_label", None), 52),
        (getattr(self, "manager_game_art_label", None), 38),
    ]
    existing = profile_artwork_path(self._fr_module, record, self.current_profile_name, force=force)
    local_art = Path(str(record.get("artwork_path") or "")).is_file() or Path(str(record.get("azahar_icon_path") or "")).is_file()
    if existing and local_art:
        for label, size in labels:
            if label:
                _render_art_label(label, existing, size, self._fr_module)
        if hasattr(self, "profile_artwork_status_var") and record.get("azahar_icon_path") and not record.get("artwork_path"):
            self.profile_artwork_status_var.set("Azahar SMDH icon")
        return
    fallback = _fallback_art(self._fr_module, record, self.current_profile_name, 96)
    for label, size in labels:
        if label and fallback:
            _render_art_label(label, fallback, size, self._fr_module)
    if not enabled:
        return
    if existing:
        for label, size in labels:
            if label:
                _render_art_label(label, existing, size, self._fr_module)
        return
    key = _safe_art_key(record, self.current_profile_name)
    if key in getattr(self, "_artwork_downloads", set()):
        return
    self._artwork_downloads = getattr(self, "_artwork_downloads", set())
    self._artwork_downloads.add(key)

    def worker():
        path, source = download_profile_artwork(self._fr_module, record, self.current_profile_name, force=force)
        def finish():
            self._artwork_downloads.discard(key)
            if path and self.current_profile_name:
                current = self.profile_data.get("profiles", {}).get(self.current_profile_name, {})
                if _safe_art_key(current, self.current_profile_name) == key:
                    current["artwork_source"] = source
                    for label, size in labels:
                        if label:
                            _render_art_label(label, path, size, self._fr_module)
                    if hasattr(self, "profile_artwork_status_var"):
                        self.profile_artwork_status_var.set(source)
            elif hasattr(self, "profile_artwork_status_var"):
                self.profile_artwork_status_var.set("No online artwork match — using platform fallback")
        self.after(0, finish)
    threading.Thread(target=worker, daemon=True).start()


def _patch_artwork_ui(fr):
    original_build = fr.V11App.build
    original_summary = fr.V11App._refresh_profile_summary
    original_header = fr.V11App._update_profile_header_card
    original_batch_refresh = fr.V11App.refresh_batch_queue_list
    original_available_refresh = fr.V11App.refresh_batch_profiles

    def build(self):
        self._fr_module = fr
        self._artwork_downloads = set()
        original_build(self)
        # Active profile artwork.
        try:
            identity = self.header_profile_card.winfo_children()[0]
            self.header_game_art_label = fr.tk.Label(
                self.header_profile_card, bg="#09131c", width=54, height=54,
                highlightthickness=1, highlightbackground="#284252"
            )
            self.header_game_art_label.pack(side="left", before=identity, padx=(8, 0), pady=7)
            self._bind_recursive(self.header_game_art_label, "<Button-1>", lambda _e: self.tabs.select(self.page_profiles))
        except Exception:
            self.header_game_art_label = None

        # Profile summary artwork, placed without rebuilding the whole card.
        try:
            summary = self.profile_summary_ready_label.master
            self.profile_summary_art_label = fr.tk.Label(
                summary, bg="#071018", width=72, height=72,
                highlightthickness=1, highlightbackground="#29404f"
            )
            self.profile_summary_art_label.place(x=12, y=12, width=72, height=72)
            children = summary.winfo_children()
            for child in children:
                if child is self.profile_summary_art_label:
                    continue
                info = child.pack_info() if child.winfo_manager() == "pack" else {}
                if info:
                    child.pack_configure(padx=(94, 12))
            summary.configure(height=max(126, summary.winfo_reqheight()))
        except Exception:
            self.profile_summary_art_label = None

        # Manual artwork controls in Profiles.
        try:
            setup = self.faithfulness_preset_var.master if False else None
        except Exception:
            setup = None
        # Locate the Game setup card body from the Game name entry parent.
        try:
            form = self.vars["game_name"]._tk.globalgetvar  # sentinel only
            game_entry = None
            for widget in self.tab_profiles.winfo_children():
                pass
            # The entry bound to game_name is discoverable by walking descendants.
            def descendants(root):
                for child in root.winfo_children():
                    yield child
                    yield from descendants(child)
            for widget in descendants(self.tab_profiles):
                try:
                    if widget.winfo_class() == "Entry" and str(widget.cget("textvariable")) == str(self.vars["game_name"]):
                        game_entry = widget; break
                except Exception:
                    pass
            setup_body = game_entry.master.master if game_entry else None
            if setup_body:
                controls = fr.tk.Frame(setup_body, bg="#0d151f")
                controls.pack(fill="x", padx=10, pady=(3, 8))
                self.profile_artwork_path_var = fr.tk.StringVar(value=str(_current_record(self).get("artwork_path") or ""))
                self.profile_artwork_status_var = fr.tk.StringVar(value="Artwork: automatic")
                fr.tk.Label(controls, text="GAME ARTWORK", bg="#0d151f", fg="#8194a7", font=("Segoe UI", 8, "bold")).pack(side="left")
                fr.ttk.Button(controls, text="Choose Image", style="Compact.TButton", command=lambda: _choose_manual_artwork(self, fr)).pack(side="left", padx=(10, 4))
                fr.ttk.Button(controls, text="Refresh Online", style="Compact.TButton", command=lambda: _force_artwork_refresh(self, fr)).pack(side="left", padx=4)
                fr.ttk.Button(controls, text="Clear", style="Compact.TButton", command=lambda: _clear_manual_artwork(self, fr)).pack(side="left", padx=4)
                fr.tk.Label(controls, textvariable=self.profile_artwork_status_var, bg="#0d151f", fg="#71b9d6", font=("Segoe UI", 8)).pack(side="right")
        except Exception:
            self.profile_artwork_path_var = fr.tk.StringVar(value="")

        # Batch selected/current profile art.
        try:
            hero = self.batch_status_badge.master
            self.batch_game_art_label = fr.tk.Label(hero, bg="#09131c", highlightthickness=1, highlightbackground="#284252")
            self.batch_game_art_label.pack(side="right", before=self.batch_status_badge, padx=(8, 0), pady=3)
            self.batch_available_list.bind("<<ListboxSelect>>", lambda _e: _batch_selection_art(self, True), add="+")
            self.batch_queue_list.bind("<<ListboxSelect>>", lambda _e: _batch_selection_art(self, False), add="+")
        except Exception:
            self.batch_game_art_label = None

        # Texture Manager uses the same native icon while keeping the toolbar compact.
        try:
            manager_label = None
            def manager_descendants(root):
                for child in root.winfo_children():
                    yield child
                    yield from manager_descendants(child)
            for widget in manager_descendants(self.page_manager):
                try:
                    if widget.winfo_class() == "Label" and str(widget.cget("textvariable")) == str(self.manager_profile_label_var):
                        manager_label = widget; break
                except Exception:
                    pass
            if manager_label:
                self.manager_game_art_label = fr.tk.Label(
                    manager_label.master, bg="#09131c", highlightthickness=1, highlightbackground="#284252"
                )
                self.manager_game_art_label.pack(side="left", before=manager_label, padx=(0, 7))
            else:
                self.manager_game_art_label = None
        except Exception:
            self.manager_game_art_label = None
        _refresh_artwork_views(self, force=False)

    def summary(self):
        result = original_summary(self)
        _refresh_artwork_views(self, force=False)
        return result

    def header(self):
        result = original_header(self)
        _refresh_artwork_views(self, force=False)
        return result

    def batch_q(self):
        result = original_batch_refresh(self)
        _refresh_artwork_views(self, force=False)
        return result

    def batch_av(self):
        result = original_available_refresh(self)
        _refresh_artwork_views(self, force=False)
        return result

    fr.V11App.build = build
    fr.V11App._refresh_profile_summary = summary
    fr.V11App._update_profile_header_card = header
    fr.V11App.refresh_batch_queue_list = batch_q
    fr.V11App.refresh_batch_profiles = batch_av
    fr.V11App._refresh_artwork_views = lambda self, force=False: _refresh_artwork_views(self, force)


def _choose_manual_artwork(self, fr):
    from tkinter import filedialog
    path = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")])
    if path:
        self.profile_artwork_path_var.set(path)
        if self.current_profile_name in self.profile_data.get("profiles", {}):
            self.profile_data["profiles"][self.current_profile_name]["artwork_path"] = path
            fr.save_profiles_data(self.profile_data)
        self.profile_artwork_status_var.set("Manual artwork")
        _refresh_artwork_views(self, force=False)


def _clear_manual_artwork(self, fr):
    self.profile_artwork_path_var.set("")
    record = self.profile_data.get("profiles", {}).get(self.current_profile_name, {})
    record["artwork_path"] = ""
    fr.save_profiles_data(self.profile_data)
    self.profile_artwork_status_var.set("Artwork: automatic")
    _refresh_artwork_views(self, force=False)


def _force_artwork_refresh(self, fr):
    record = _current_record(self)
    cache = fr.ARTWORK_DIR / f"{_safe_art_key(record, self.current_profile_name)}.png"
    cache.unlink(missing_ok=True)
    failures = _failure_cache(fr); failures.pop(_safe_art_key(record, self.current_profile_name), None); _save_failure_cache(fr, failures)
    self.profile_artwork_status_var.set("Searching…")
    _refresh_artwork_views(self, force=True)


def _batch_selection_art(self, available):
    try:
        if available:
            selection = self.batch_available_list.curselection()
            name = self.batch_available_names[selection[0]] if selection else self.current_profile_name
        else:
            selection = self.batch_queue_list.curselection()
            name = self.batch_queue[selection[0]] if selection else self.current_profile_name
        record = self.profile_data.get("profiles", {}).get(name, {})
        path = profile_artwork_path(self._fr_module, record, name)
        if not path:
            path = _fallback_art(self._fr_module, record, name, 96)
        if path and self.batch_game_art_label:
            _render_art_label(self.batch_game_art_label, path, 52, self._fr_module)
    except Exception:
        pass
