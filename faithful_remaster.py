
import json
import sqlite3
import urllib.parse
from html.parser import HTMLParser
import queue
import threading
import time
import uuid
import hashlib
import shutil
import subprocess
import urllib.request, urllib.parse, os, fnmatch
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog, messagebox
from pathlib import Path
import re

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except Exception:
    ImageTk = None
    PIL_AVAILABLE = False

APP_DIR = Path(__file__).resolve().parent
APPDATA_ROOT = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
DATA_DIR = APPDATA_ROOT / "Faithful Remaster"
TEMP_DIR = DATA_DIR / "_temp_outputs"
CACHE_DIR = DATA_DIR / "_hash_cache"
LOGS_DIR = DATA_DIR / "logs"
CONFIG_PATH = DATA_DIR / "config.json"
CACHE_INDEX_PATH = CACHE_DIR / "cache_index.json"
EXCEPTIONS_PATH = DATA_DIR / "exceptions.txt"
MIGRATION_MARKER = DATA_DIR / ".persistent_data_v1"
APP_LOG_PATH = LOGS_DIR / "faithful_remaster.log"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga"}


def _copy_if_missing(source, destination):
    source = Path(source)
    destination = Path(destination)
    if not source.exists() or destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)
    return True


def _legacy_data_candidates():
    """Find likely prior extracted Faithful Remaster folders near this build."""
    candidates = [APP_DIR]
    parents = [APP_DIR.parent]
    if APP_DIR.parent.parent != APP_DIR.parent:
        parents.append(APP_DIR.parent.parent)

    for parent in parents:
        try:
            for child in parent.iterdir():
                if not child.is_dir() or child == APP_DIR:
                    continue
                name = child.name.lower()
                if "faithful" in name and "remaster" in name:
                    candidates.append(child)
        except Exception:
            pass

    unique = []
    seen = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve()).casefold()
        except Exception:
            key = str(candidate).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)

    def score(path):
        try:
            return path.stat().st_mtime
        except Exception:
            return 0

    return sorted(unique, key=score, reverse=True)


def migrate_legacy_user_data():
    """
    Copy prior per-version data into the shared AppData folder.
    Existing persistent files always win and are never overwritten.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    if MIGRATION_MARKER.exists():
        return []

    copied = []
    file_names = [
        "config.json",
        "profiles.json",
        "game_titles.sqlite",
        "game_titles.json",
        "exceptions.txt",
        "processed.txt",
    ]
    dir_names = ["profiles", "_hash_cache"]

    for candidate in _legacy_data_candidates():
        for name in file_names:
            src = candidate / name
            dst = DATA_DIR / name
            if _copy_if_missing(src, dst):
                copied.append(str(src))
        for name in dir_names:
            src = candidate / name
            dst = DATA_DIR / name
            if _copy_if_missing(src, dst):
                copied.append(str(src))

    MIGRATION_MARKER.write_text(
        json.dumps(
            {
                "migrated": bool(copied),
                "sources": copied,
                "data_dir": str(DATA_DIR),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return copied


MIGRATED_ITEMS = migrate_legacy_user_data()

DEFAULT_CONFIG = {
    "comfy_url": "http://127.0.0.1:8188",
    "comfy_start_file": "",
    "enable_alpha_workflow": True,
    "alpha_workflow_api_json": "",
    "alpha_load_image_node_id": 1,
    "alpha_save_image_node_id": 5,
    "invert_alpha_output": False,
    "workflow_api_json": "",
    "dump_folder": "",
    "load_folder": "",
    "load_image_node_id": 1,
    "save_image_node_id": 4,
    "overwrite": False,
    "preserve_alpha": True,
    "alpha_resize_method": "nearest",
    "alpha_source": "original",
    "alpha_feather_radius": 0.0,
    "process_tmp_image_files": True,
    "enable_hash_cache": True,
    "enable_vram_protection": True,
    "max_vram_gb": 10.0,
    "vram_resume_margin_gb": 0.5,
    "ignore_existing_silently": True,
    "prioritize_new_dumps": True,
    "enable_comfy_status": True,
    "pause_when_comfy_offline": True,
    "fix_alpha_edge_bleed": False,
    "alpha_bleed_iterations": 1,
    "alpha_edge_threshold": 32,
    "enable_separate_alpha_workflow": True,
    "alpha_workflow_api_json": "",
    "alpha_load_image_node_id": 1,
    "alpha_save_image_node_id": 4,
    "alpha_workflow_invert_output": False,
    "auto_check_missing_load": True,
    "timeout_seconds": 900,
    "scan_interval_seconds": 2,
    "processed_log": "processed.txt",
    "skip_cutscene_buffers": True,
    "delete_skipped_cutscene_buffers": False,
    "live_texture_preview": True,
    "cutscene_min_width": 256,
    "cutscene_min_height": 160,
    "cutscene_grayscale_ratio": 0.985,
    "auto_sync_azahar_pack_json": True
}


def detect_cutscene_buffer(path, cfg):
    """
    Conservative detector for pre-rendered cutscene/framebuffer dumps.

    It requires all of the following:
    - sufficiently large, screen-like dimensions
    - landscape aspect ratio commonly used by framebuffer dumps
    - almost all sampled pixels are grayscale / near-grayscale

    Returns: (is_buffer: bool, reason: str)
    """
    if not PIL_AVAILABLE:
        return False, "Pillow unavailable"

    try:
        with Image.open(path) as im:
            width, height = im.size
            min_w = int(cfg.get("cutscene_min_width", 256))
            min_h = int(cfg.get("cutscene_min_height", 160))

            if width < min_w or height < min_h:
                return False, "too small"

            aspect = width / max(height, 1)
            if not (1.20 <= aspect <= 1.50):
                return False, f"aspect {aspect:.2f} outside screen-buffer range"

            rgb = im.convert("RGB")
            total_pixels = width * height
            target_samples = 6000
            step = max(1, int((total_pixels / target_samples) ** 0.5))

            gray_like = 0
            sampled = 0
            max_channel_delta = 4

            px = rgb.load()
            for y in range(0, height, step):
                for x in range(0, width, step):
                    r, g, b = px[x, y]
                    sampled += 1
                    if max(r, g, b) - min(r, g, b) <= max_channel_delta:
                        gray_like += 1

            if sampled == 0:
                return False, "no samples"

            ratio = gray_like / sampled
            threshold = float(cfg.get("cutscene_grayscale_ratio", 0.985))
            if ratio < threshold:
                return False, f"grayscale ratio {ratio:.3f}"

            return True, (
                f"{width}x{height}, aspect={aspect:.2f}, "
                f"near-grayscale={ratio:.1%}"
            )
    except Exception as e:
        return False, f"analysis failed: {e}"


TERMINAL_STATES = {
    "done", "done_cached", "skip_exists", "cache_hit_restored",
    "ignored_existing", "exception_skipped", "cutscene_buffer_skipped", "no_output", "stopped"
}

def load_config():
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            out = dict(DEFAULT_CONFIG); out.update(cfg); return out
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

def load_cache_index():
    if CACHE_INDEX_PATH.exists():
        try:
            return json.loads(CACHE_INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_cache_index(index):
    CACHE_INDEX_PATH.write_text(json.dumps(index, indent=2), encoding="utf-8")

def sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def get_vram_info():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, text=True, timeout=3
        )
        used, total = [int(x.strip()) for x in out.strip().splitlines()[0].split(",")]
        return used, total
    except Exception:
        return None, None

def load_json(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)

def post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))

def get_json(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def check_comfy_status(comfy_url):
    """
    Returns dict:
    online, queue_running, queue_pending, error
    """
    base = comfy_url.rstrip("/")
    result = {"online": False, "queue_running": None, "queue_pending": None, "error": ""}
    try:
        # /system_stats is a light health check.
        get_json(base + "/system_stats", timeout=3)
        result["online"] = True
    except Exception as e:
        result["error"] = str(e)
        return result

    try:
        q = get_json(base + "/queue", timeout=3)
        running = q.get("queue_running", [])
        pending = q.get("queue_pending", [])
        result["queue_running"] = len(running)
        result["queue_pending"] = len(pending)
    except Exception as e:
        result["error"] = "online, queue check failed: " + str(e)

    return result

def stable_file(path, checks=3, delay=0.5):
    last = -1
    for _ in range(checks):
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size == 0:
            return False
        if size == last:
            return True
        last = size
        time.sleep(delay)
    return False

def is_image_like(path, process_tmp=True):
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTS:
        return True
    if suffix == ".tmp" and process_tmp and PIL_AVAILABLE:
        try:
            with Image.open(path) as im:
                im.verify()
            return True
        except Exception:
            return False
    return False

def output_path_for_input(path, dump_folder, load_folder):
    rel = path.relative_to(dump_folder)
    out = load_folder / rel
    if out.suffix.lower() == ".tmp":
        out = out.with_suffix(".png")
    return out

def upload_to_comfy(comfy_url, image_path, subfolder="dolphin_auto"):
    upload_path = image_path
    temp_upload = None
    if image_path.suffix.lower() == ".tmp":
        if not PIL_AVAILABLE:
            raise RuntimeError("Pillow needed to upload .tmp image files")
        temp_upload = TEMP_DIR / f"upload_{uuid.uuid4().hex}.png"
        Image.open(image_path).convert("RGBA").save(temp_upload, format="PNG")
        upload_path = temp_upload

    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex
    body = bytearray()

    def add_field(name, value):
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(f"{value}\r\n".encode())

    def add_file(name, filename, content):
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
        body.extend(b"Content-Type: image/png\r\n\r\n")
        body.extend(content)
        body.extend(b"\r\n")

    add_file("image", upload_path.name, upload_path.read_bytes())
    add_field("type", "input")
    add_field("subfolder", subfolder)
    add_field("overwrite", "true")
    body.extend(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        comfy_url.rstrip("/") + "/upload/image",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            result = json.loads(r.read().decode("utf-8"))
    finally:
        if temp_upload and temp_upload.exists():
            temp_upload.unlink(missing_ok=True)

    if result.get("subfolder"):
        return result["subfolder"].replace("\\", "/") + "/" + result["name"]
    return result["name"]

def set_node_input(workflow, node_id, input_name, value):
    workflow[str(node_id)]["inputs"][input_name] = value

def find_output(comfy_url, prompt_id, timeout=900):
    start = time.time()
    while time.time() - start < timeout:
        hist = get_json(comfy_url.rstrip("/") + f"/history/{prompt_id}", timeout=10)
        if prompt_id in hist:
            for node_output in hist[prompt_id].get("outputs", {}).values():
                for img in node_output.get("images", []):
                    return img
            return None
        time.sleep(1)
    raise TimeoutError("Timed out waiting for ComfyUI output")

def download_output(comfy_url, image_info, dest_png):
    params = urllib.parse.urlencode({
        "filename": image_info["filename"],
        "subfolder": image_info.get("subfolder", ""),
        "type": image_info.get("type", "output"),
    })
    with urllib.request.urlopen(comfy_url.rstrip("/") + "/view?" + params, timeout=120) as r:
        dest_png.write_bytes(r.read())

def run_comfy_image_workflow_to_file(comfy_url, workflow_template, image_path, load_node_id, save_node_id, filename_prefix, timeout=900):
    """
    Upload image_path, run a Comfy API workflow, download first saved image to a temp PNG.
    Used for both RGB workflow and optional separate alpha workflow.
    """
    uploaded = upload_to_comfy(comfy_url, image_path)
    workflow = json.loads(json.dumps(workflow_template))
    set_node_input(workflow, int(load_node_id), "image", uploaded)
    if save_node_id:
        set_node_input(workflow, int(save_node_id), "filename_prefix", filename_prefix)

    response = post_json(comfy_url.rstrip("/") + "/prompt", {
        "prompt": workflow,
        "client_id": "faithful-remaster-v10-alpha-workflow"
    })
    img_info = find_output(comfy_url, response["prompt_id"], int(timeout))
    if not img_info:
        return None
    out_file = TEMP_DIR / f"comfy_{uuid.uuid4().hex}.png"
    download_output(comfy_url, img_info, out_file)
    return out_file

def apply_alpha_image_to_rgba(rgb_png_path, alpha_image_path, invert=False):
    """
    Take a grayscale alpha workflow output where white=visible and black=transparent,
    then apply it as alpha to rgb_png_path.
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is not installed")
    from PIL import ImageOps
    out = Image.open(rgb_png_path).convert("RGBA")
    mask = Image.open(alpha_image_path).convert("L")
    if mask.size != out.size:
        mask = mask.resize(out.size, Image.Resampling.LANCZOS)
    if invert:
        mask = ImageOps.invert(mask)
    out.putalpha(mask)
    out.save(rgb_png_path, format="PNG")

def has_alpha(path):
    if not PIL_AVAILABLE:
        return False
    try:
        im = Image.open(path)
        if im.mode not in ("RGBA", "LA") and "transparency" not in im.info:
            return False
        a = im.convert("RGBA").getchannel("A")
        mn, mx = a.getextrema()
        return mn < 255
    except Exception:
        return False

def _alpha_resample(method):
    method = str(method).lower()
    if method == "nearest":
        return Image.Resampling.NEAREST
    if method == "lanczos":
        return Image.Resampling.LANCZOS
    if method == "bicubic":
        return Image.Resampling.BICUBIC
    if method == "bilinear":
        return Image.Resampling.BILINEAR
    if method in ("area", "box"):
        return Image.Resampling.BOX
    return Image.Resampling.BOX

def _has_useful_alpha_image(im):
    try:
        a = im.convert("RGBA").getchannel("A")
        mn, mx = a.getextrema()
        return mn < 255
    except Exception:
        return False

def reattach_alpha(original_path, png_path, method="nearest", alpha_source="original", feather_radius=0.0):
    """
    Stable legacy alpha:
    - Always reattach original alpha.
    - No soft alpha.
    - No contour reconstruction.
    - No shrink.
    - No RGB smoothing.
    This is the safest behavior that matched the earlier good Faithful Remaster builds.
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is not installed")
    orig = Image.open(original_path).convert("RGBA")
    out = Image.open(png_path).convert("RGBA")
    alpha = orig.getchannel("A")

    method = str(method).lower()
    if method == "lanczos":
        resample = Image.Resampling.LANCZOS
    elif method == "bicubic":
        resample = Image.Resampling.BICUBIC
    elif method == "bilinear":
        resample = Image.Resampling.BILINEAR
    elif method in ("area", "box"):
        resample = Image.Resampling.BOX
    else:
        resample = Image.Resampling.NEAREST

    if alpha.size != out.size:
        alpha = alpha.resize(out.size, resample)

    out.putalpha(alpha)
    out.save(png_path, format="PNG")


def alpha_edge_bleed_png(png_path, iterations=3, threshold=48):
    """
    Fix halos/squares around alpha textures.

    Problem:
    Emulators/GPU filtering can sample RGB from transparent or semi-transparent pixels.
    If Comfy generated gray/blue/dark RGB around the cutout, it appears as a square/halo.

    Fix:
    - Keep alpha unchanged.
    - Treat pixels with alpha <= threshold as edge/transparent pixels.
    - Bleed RGB from nearby solid pixels (alpha > threshold) into those pixels.
    - Repeat a few times so transparent background RGB becomes the same color as the edge.
    """
    if not PIL_AVAILABLE:
        return
    try:
        im = Image.open(png_path).convert("RGBA")
        w, h = im.size
        threshold = int(threshold)
        iterations = max(1, int(iterations))

        for _ in range(iterations):
            src = im.copy()
            sp = src.load()
            dp = im.load()
            changed = False

            for y in range(h):
                for x in range(w):
                    r, g, b, a = sp[x, y]

                    # Fix both fully transparent and weak semi-transparent edge pixels.
                    if a > threshold:
                        continue

                    rs = gs = bs = count = 0

                    # Search a 3x3 neighborhood first.
                    for yy in (y - 1, y, y + 1):
                        if yy < 0 or yy >= h:
                            continue
                        for xx in (x - 1, x, x + 1):
                            if xx < 0 or xx >= w or (xx == x and yy == y):
                                continue
                            nr, ng, nb, na = sp[xx, yy]
                            if na > threshold:
                                rs += nr
                                gs += ng
                                bs += nb
                                count += 1

                    if count:
                        dp[x, y] = (rs // count, gs // count, bs // count, a)
                        changed = True

            if not changed:
                break

        im.save(png_path, format="PNG")
    except Exception:
        pass


def load_exception_patterns():
    if not EXCEPTIONS_PATH.exists():
        return []
    try:
        return [
            line.strip()
            for line in EXCEPTIONS_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    except Exception:
        return []

def save_exception_patterns(patterns):
    unique = []
    seen = set()
    for p in patterns:
        p = str(p).strip()
        if p and p not in seen:
            seen.add(p)
            unique.append(p)
    EXCEPTIONS_PATH.write_text("\n".join(unique) + ("\n" if unique else ""), encoding="utf-8")

def add_exception_pattern(pattern):
    patterns = load_exception_patterns()
    if pattern not in patterns:
        patterns.append(pattern)
        save_exception_patterns(patterns)

def is_exception_texture(path):
    name = Path(path).name
    stem = Path(path).stem
    rel = str(path).replace("\\", "/")
    for pat in load_exception_patterns():
        p = pat.replace("\\", "/")
        if fnmatch.fnmatch(name, p) or fnmatch.fnmatch(stem, p) or fnmatch.fnmatch(rel, p):
            return True
    return False



def detect_comfy_nodes_from_api(path):
    """
    Returns:
      {
        "load_nodes": [(id, title), ...],
        "save_nodes": [(id, title), ...],
        "best_load": "1" or "",
        "best_save": "4" or "",
        "warnings": [...]
      }
    """
    result = {"load_nodes": [], "save_nodes": [], "best_load": "", "best_save": "", "warnings": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            wf = json.load(f)
    except Exception as e:
        result["warnings"].append(f"Could not read workflow JSON: {e}")
        return result

    if not isinstance(wf, dict):
        result["warnings"].append("Workflow JSON is not an API workflow object.")
        return result

    for node_id, node in wf.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type", ""))
        title = ""
        meta = node.get("_meta")
        if isinstance(meta, dict):
            title = str(meta.get("title", ""))
        label = title or class_type or f"Node {node_id}"

        if class_type == "LoadImage":
            result["load_nodes"].append((str(node_id), label))
        elif class_type == "SaveImage":
            result["save_nodes"].append((str(node_id), label))

    if len(result["load_nodes"]) == 1:
        result["best_load"] = result["load_nodes"][0][0]
    elif len(result["load_nodes"]) > 1:
        result["warnings"].append("More than one LoadImage node found. Please choose the correct one.")

    if len(result["save_nodes"]) == 1:
        result["best_save"] = result["save_nodes"][0][0]
    elif len(result["save_nodes"]) > 1:
        # Prefer a SaveImage fed by an ImageSharpen node, then VAEDecode, then first SaveImage.
        best = ""
        try:
            for sid, _ in result["save_nodes"]:
                inputs = wf.get(str(sid), {}).get("inputs", {})
                src = inputs.get("images")
                if isinstance(src, list) and src:
                    src_id = str(src[0])
                    src_cls = str(wf.get(src_id, {}).get("class_type", ""))
                    if src_cls == "ImageSharpen":
                        best = sid
                        break
            if not best:
                for sid, _ in result["save_nodes"]:
                    inputs = wf.get(str(sid), {}).get("inputs", {})
                    src = inputs.get("images")
                    if isinstance(src, list) and src:
                        src_id = str(src[0])
                        src_cls = str(wf.get(src_id, {}).get("class_type", ""))
                        if src_cls == "VAEDecode":
                            best = sid
                            break
            if best:
                result["best_save"] = best
                result["warnings"].append("More than one SaveImage node found; auto-selected the most likely final output.")
            else:
                result["warnings"].append("More than one SaveImage node found. Please choose the correct one.")
        except Exception:
            result["warnings"].append("More than one SaveImage node found. Please choose the correct one.")

    if not result["load_nodes"]:
        result["warnings"].append("No LoadImage node found. Make sure this is an API workflow JSON.")
    if not result["save_nodes"]:
        result["warnings"].append("No SaveImage node found. Make sure this is an API workflow JSON.")

    return result


class Worker:
    def __init__(self, cfg, log_q, stop_event, force_scan_event, force_missing_event, stats):
        self.cfg = cfg
        self.log_q = log_q
        self.stop_event = stop_event
        self.force_scan_event = force_scan_event
        self.force_missing_event = force_missing_event
        self.stats = stats
        self.workflow_template = load_json(cfg["workflow_api_json"])
        self.processed_log = Path(cfg.get("processed_log") or (DATA_DIR / "processed.txt"))
        self.processed = set()
        if self.processed_log.exists():
            self.processed = {x.strip() for x in self.processed_log.read_text(encoding="utf-8").splitlines() if x.strip()}
        self.cache_index = load_cache_index()
        self.vram_paused = False
        self.comfy_paused = False
        self.known_files = set()
        self.high_q = []
        self.low_q = []

    def log(self, msg):
        self.log_q.put(msg)

    def mark_processed(self, path):
        s = str(path)
        if s not in self.processed:
            self.processed.add(s)
            with self.processed_log.open("a", encoding="utf-8") as f:
                f.write(s + "\n")

    def unmark_processed(self, path):
        s = str(path)
        if s in self.processed:
            self.processed.remove(s)
        try:
            self.processed_log.write_text("\n".join(sorted(self.processed)) + ("\n" if self.processed else ""), encoding="utf-8")
        except Exception:
            pass

    def cache_file_for_hash(self, digest):
        return CACHE_DIR / f"{digest}.png"

    def wait_for_comfy_online(self):
        if not self.cfg.get("enable_comfy_status", True) or not self.cfg.get("pause_when_comfy_offline", True):
            return
        while not self.stop_event.is_set():
            st = check_comfy_status(self.cfg["comfy_url"])
            self.stats["comfy_online"] = st["online"]
            self.stats["comfy_running"] = st.get("queue_running")
            self.stats["comfy_pending"] = st.get("queue_pending")
            self.stats["comfy_error"] = st.get("error", "")

            if st["online"]:
                if self.comfy_paused:
                    self.log("ComfyUI ONLINE. Queue resumed.")
                self.comfy_paused = False
                return

            if not self.comfy_paused:
                self.log("ComfyUI OFFLINE. Queue paused.")
                self.stats["status"] = "PAUSED (COMFY OFFLINE)"
            self.comfy_paused = True
            time.sleep(3)

    def wait_for_vram_budget(self):
        if not self.cfg.get("enable_vram_protection", True):
            return
        limit_mb = float(self.cfg.get("max_vram_gb", 10.0)) * 1024
        margin_mb = float(self.cfg.get("vram_resume_margin_gb", 0.5)) * 1024
        resume_under = max(0, limit_mb - margin_mb)

        while not self.stop_event.is_set():
            used, total = get_vram_info()
            if used is None:
                return
            self.stats["vram_used_mb"] = used
            self.stats["vram_total_mb"] = total
            self.stats["peak_vram_mb"] = max(self.stats.get("peak_vram_mb", 0), used)

            if not self.vram_paused and used > limit_mb:
                self.vram_paused = True
                self.stats["status"] = "PAUSED (VRAM LIMIT)"
                self.log(f"PAUSED: VRAM {used/1024:.1f}/{total/1024:.1f} GB > limit {limit_mb/1024:.1f} GB")

            if self.vram_paused:
                if used <= resume_under:
                    self.vram_paused = False
                    self.stats["status"] = "RUNNING"
                    self.log(f"RESUMED: VRAM {used/1024:.1f}/{total/1024:.1f} GB")
                    return
                time.sleep(2)
                continue
            return

    def restore_from_cache_if_possible(self, digest, out_path):
        cache_file = self.cache_file_for_hash(digest)
        if cache_file.exists():
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if not out_path.exists() or self.cfg.get("overwrite", False):
                shutil.copy2(cache_file, out_path)
            self.stats["cache_hits"] += 1
            return True
        return False

    def process_one(self, path):
        dump_folder = Path(self.cfg["dump_folder"])
        load_folder = Path(self.cfg["load_folder"])
        if self.cfg.get("emulator") == "Azahar / Citra" and self.cfg.get("auto_sync_azahar_pack_json", True):
            sync_azahar_pack_json(dump_folder, load_folder, self.log)
        out_path = output_path_for_input(path, dump_folder, load_folder)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        self.stats["current_input_path"] = str(path)
        self.stats["current_output_path"] = ""
        self.stats["current_texture_stage"] = "Preparing"

        if is_exception_texture(path):
            self.stats["exceptions_skipped"] = self.stats.get("exceptions_skipped", 0) + 1
            return "exception_skipped"

        if self.cfg.get("skip_cutscene_buffers", True):
            is_buffer, reason = detect_cutscene_buffer(path, self.cfg)
            if is_buffer:
                self.stats["cutscene_buffers_skipped"] = self.stats.get("cutscene_buffers_skipped", 0) + 1
                self.log(f"Cutscene buffer skipped: {path.name} ({reason})")
                if self.cfg.get("delete_skipped_cutscene_buffers", False):
                    try:
                        path.unlink()
                        self.stats["cutscene_buffers_deleted"] = self.stats.get("cutscene_buffers_deleted", 0) + 1
                        self.log(f"Deleted skipped cutscene buffer: {path.name}")
                    except Exception as e:
                        self.log(f"Could not delete skipped cutscene buffer {path.name}: {e}")
                return "cutscene_buffer_skipped"

        if self.cfg.get("ignore_existing_silently", True) and out_path.exists() and not self.cfg.get("overwrite", False):
            return "ignored_existing"

        digest = sha1_file(path) if self.cfg.get("enable_hash_cache", True) else None
        if digest and self.restore_from_cache_if_possible(digest, out_path):
            self.stats["current_output_path"] = str(out_path)
            self.stats["current_texture_stage"] = "Restored from cache"
            return "cache_hit_restored"
        if out_path.exists() and not self.cfg.get("overwrite", False):
            return "skip_exists"

        self.stats["current_texture_stage"] = "Waiting for ComfyUI / VRAM"
        self.wait_for_comfy_online()
        self.wait_for_vram_budget()
        if self.stop_event.is_set():
            return "stopped"

        alpha = self.cfg.get("preserve_alpha", True) and has_alpha(path)
        if alpha:
            self.log(f"Alpha detected: {path.name}")

        self.stats["current_texture_stage"] = "Uploading and processing RGB"
        uploaded = upload_to_comfy(self.cfg["comfy_url"], path)
        workflow = json.loads(json.dumps(self.workflow_template))
        set_node_input(workflow, self.cfg["load_image_node_id"], "image", uploaded)
        if self.cfg.get("save_image_node_id"):
            set_node_input(workflow, self.cfg["save_image_node_id"], "filename_prefix", "dolphin_auto/" + path.stem)

        self.stats["comfy_jobs"] += 1
        response = post_json(self.cfg["comfy_url"].rstrip("/") + "/prompt", {
            "prompt": workflow,
            "client_id": "faithful-remaster-v6-comfy-status"
        })
        img_info = find_output(self.cfg["comfy_url"], response["prompt_id"], int(self.cfg.get("timeout_seconds", 900)))
        if not img_info:
            return "no_output"

        temp_png = TEMP_DIR / f"out_{uuid.uuid4().hex}.png"
        download_output(self.cfg["comfy_url"], img_info, temp_png)
        alpha_temp_png = None
        if alpha:
            if self.cfg.get("enable_separate_alpha_workflow", False) and self.cfg.get("alpha_workflow_api_json"):
                try:
                    alpha_workflow_path = Path(self.cfg.get("alpha_workflow_api_json", ""))
                    if alpha_workflow_path.exists():
                        self.stats["current_texture_stage"] = "Processing separate alpha"
                        self.log(f"Alpha workflow: running separate alpha path for {path.name}")
                        alpha_template = load_json(alpha_workflow_path)
                        self.stats["comfy_jobs"] += 1
                        alpha_temp_png = run_comfy_image_workflow_to_file(
                            self.cfg["comfy_url"],
                            alpha_template,
                            path,
                            int(self.cfg.get("alpha_load_image_node_id", 1)),
                            int(self.cfg.get("alpha_save_image_node_id", 12)),
                            "alpha_auto/" + path.stem,
                            int(self.cfg.get("timeout_seconds", 900))
                        )
                        if alpha_temp_png and alpha_temp_png.exists():
                            apply_alpha_image_to_rgba(
                                temp_png,
                                alpha_temp_png,
                                bool(self.cfg.get("alpha_workflow_invert_output", False))
                            )
                            self.log("Alpha workflow: applied alpha output")
                        else:
                            self.log("Alpha workflow: no output, falling back to legacy alpha")
                            reattach_alpha(path, temp_png, self.cfg.get("alpha_resize_method", "nearest"), "original", 0.0)
                    else:
                        self.log("Alpha workflow file not found, falling back to legacy alpha")
                        reattach_alpha(path, temp_png, self.cfg.get("alpha_resize_method", "nearest"), "original", 0.0)
                except Exception as e:
                    self.log(f"Alpha workflow ERROR: {e} -> falling back to legacy alpha")
                    reattach_alpha(path, temp_png, self.cfg.get("alpha_resize_method", "nearest"), "original", 0.0)
            else:
                reattach_alpha(path, temp_png, self.cfg.get("alpha_resize_method", "nearest"), "original", 0.0)

            if self.cfg.get("fix_alpha_edge_bleed", True):
                alpha_edge_bleed_png(temp_png, int(self.cfg.get("alpha_bleed_iterations", 1)), int(self.cfg.get("alpha_edge_threshold", 32)))

        self.stats["current_texture_stage"] = "Saving enhanced texture"
        out_path.write_bytes(temp_png.read_bytes())
        self.stats["current_output_path"] = str(out_path)
        self.stats["current_texture_stage"] = "Done"
        if alpha_temp_png:
            alpha_temp_png.unlink(missing_ok=True)

        if digest:
            cache_file = self.cache_file_for_hash(digest)
            shutil.copy2(out_path, cache_file)
            self.cache_index[digest] = {"source_name": path.name, "cached_file": cache_file.name}
            save_cache_index(self.cache_index)

        temp_png.unlink(missing_ok=True)
        self.stats["processed"] += 1
        return "done_cached" if digest else "done"

    def check_missing_loads(self):
        """
        Compare Dump folder against Load folder.
        If a dump exists but its expected Load output is missing, requeue it as HIGH priority.
        This fixes deleted custom textures not being regenerated/restored.
        """
        dump_folder = Path(self.cfg["dump_folder"])
        load_folder = Path(self.cfg["load_folder"])
        missing = []
        files = sorted([
            p for p in dump_folder.rglob("*")
            if p.is_file() and is_image_like(p, self.cfg.get("process_tmp_image_files", True))
        ], key=lambda p: p.stat().st_mtime, reverse=True)

        queued = {str(p) for p in self.high_q} | {str(p) for p in self.low_q}

        for p in files:
            out_path = output_path_for_input(p, dump_folder, load_folder)
            if not out_path.exists():
                self.unmark_processed(p)
                if str(p) not in queued:
                    missing.append(p)

        if missing:
            self.high_q = missing + self.high_q
            self.log(f"Missing Load Check: {len(missing)} missing file(s) added to HIGH priority")
        else:
            self.log("Missing Load Check: no missing files")

        self.update_queue_stats()

    def scan_folder(self, initial=False):
        dump_folder = Path(self.cfg["dump_folder"])
        files = sorted([
            p for p in dump_folder.rglob("*")
            if p.is_file() and is_image_like(p, self.cfg.get("process_tmp_image_files", True))
        ], key=lambda p: p.stat().st_mtime, reverse=True)

        if initial:
            self.known_files = {str(p) for p in files}
            self.low_q.extend([p for p in files if str(p) not in self.processed])
            self.log(f"Initial index: {len(files)} files. Low priority queue: {len(self.low_q)}")
        else:
            new_files = []
            old_candidates = []
            for p in files:
                s = str(p)
                if s not in self.known_files:
                    self.known_files.add(s)
                    if s not in self.processed:
                        new_files.append(p)
                elif s not in self.processed:
                    old_candidates.append(p)

            if new_files:
                self.high_q.extend(new_files)
                self.log(f"New dump detected: {len(new_files)} file(s) added to HIGH priority")
            existing_low = {str(p) for p in self.low_q}
            for p in old_candidates:
                if str(p) not in existing_low:
                    self.low_q.append(p)
        self.update_queue_stats()

    def pop_next_task(self):
        while self.high_q:
            p = self.high_q.pop(0)
            if str(p) not in self.processed and p.exists():
                return p, "HIGH"
        while self.low_q:
            p = self.low_q.pop(0)
            if str(p) not in self.processed and p.exists():
                return p, "LOW"
        return None, None

    def update_queue_stats(self):
        self.stats["high_queue_len"] = len([p for p in self.high_q if str(p) not in self.processed])
        self.stats["low_queue_len"] = len([p for p in self.low_q if str(p) not in self.processed])
        self.stats["queue_len"] = self.stats["high_queue_len"] + self.stats["low_queue_len"]


    def auto_detect_workflow_nodes(self, workflow_key, load_key, save_key, label="workflow"):
        path = ""
        try:
            path = self.vars[workflow_key].get().strip()
        except Exception:
            path = str(self.cfg.get(workflow_key, "")).strip()
        if not path:
            self.log(f"Auto-detect {label}: no workflow file selected.")
            return

        info = detect_comfy_nodes_from_api(path)
        if info.get("best_load"):
            try:
                self.vars[load_key].set(str(info["best_load"]))
            except Exception:
                self.cfg[load_key] = str(info["best_load"])
        if info.get("best_save"):
            try:
                self.vars[save_key].set(str(info["best_save"]))
            except Exception:
                self.cfg[save_key] = str(info["best_save"])

        load_list = ", ".join([f'{nid} ({title})' for nid, title in info.get("load_nodes", [])]) or "none"
        save_list = ", ".join([f'{nid} ({title})' for nid, title in info.get("save_nodes", [])]) or "none"
        self.log(f"Auto-detect {label}: LoadImage nodes: {load_list}")
        self.log(f"Auto-detect {label}: SaveImage nodes: {save_list}")
        if info.get("best_load") or info.get("best_save"):
            self.log(f"Auto-detect {label}: selected Load={info.get('best_load') or '?'} Save={info.get('best_save') or '?'}")
        for w in info.get("warnings", []):
            self.log(f"Auto-detect {label}: {w}")

    def auto_detect_rgb_nodes(self):
        self.auto_detect_workflow_nodes("workflow_api_json", "load_image_node_id", "save_image_node_id", "RGB workflow")

    def auto_detect_alpha_nodes(self):
        self.auto_detect_workflow_nodes("alpha_workflow_api_json", "alpha_load_image_node_id", "alpha_save_image_node_id", "Alpha workflow")

    def auto_detect_all_nodes(self):
        self.auto_detect_rgb_nodes()
        try:
            if self.vars.get("alpha_workflow_api_json") and self.vars["alpha_workflow_api_json"].get().strip():
                self.auto_detect_alpha_nodes()
        except Exception:
            pass


    def run(self):
        self.log(f"Watching: {Path(self.cfg['dump_folder'])}")
        self.scan_folder(initial=True)
        if self.cfg.get("auto_check_missing_load", True):
            self.check_missing_loads()
        last_scan = 0
        last_missing_check = 0
        while not self.stop_event.is_set():
            now = time.time()
            if self.force_scan_event.is_set() or now - last_scan >= float(self.cfg.get("scan_interval_seconds", 2)):
                self.force_scan_event.clear()
                self.scan_folder(initial=False)
                last_scan = now

            if self.force_missing_event.is_set():
                self.force_missing_event.clear()
                self.check_missing_loads()
                last_missing_check = now

            if self.cfg.get("auto_check_missing_load", True) and now - last_missing_check >= 30:
                self.check_missing_loads()
                last_missing_check = now

            task, priority = self.pop_next_task()
            if not task:
                self.stats["status"] = "IDLE"
                time.sleep(0.2)
                continue

            if not stable_file(task):
                if priority == "HIGH":
                    self.high_q.insert(0, task)
                else:
                    self.low_q.insert(0, task)
                time.sleep(0.5)
                continue

            try:
                if priority == "HIGH":
                    self.log(f"HIGH priority: {task.name}")
                status = self.process_one(task)
                if status != "ignored_existing":
                    self.log(f"{status}: {task.name}")
                if status in TERMINAL_STATES:
                    self.mark_processed(task)
                self.update_queue_stats()
            except Exception as e:
                self.log(f"ERROR: {task.name} => {e}")
                time.sleep(1)

            if not self.vram_paused and not self.comfy_paused:
                self.stats["status"] = "RUNNING"
        self.stats["status"] = "STOPPED"
        self.log("Stopped.")

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Faithful Remaster v10.3 - Cutscene Buffer Filter")
        self.geometry("1180x860")
        self.apply_dark_theme()
        self.cfg = load_config()
        self.log_q = queue.Queue()
        self.stop_event = threading.Event()
        self.force_scan_event = threading.Event()
        self.force_missing_event = threading.Event()
        self.worker_thread = None
        self.vars = {}
        self.stats = {
            "processed":0, "cache_hits":0, "comfy_jobs":0, "queue_len":0,
            "high_queue_len":0, "low_queue_len":0, "peak_vram_mb":0,
            "status":"STOPPED", "comfy_online": False, "comfy_running": None, "comfy_pending": None, "comfy_error": "", "exceptions_skipped": 0, "cutscene_buffers_skipped": 0, "cutscene_buffers_deleted": 0
        }
        self.build()
        if MIGRATED_ITEMS:
            self.log(f"Migrated {len(MIGRATED_ITEMS)} legacy data item(s) to {DATA_DIR}")
        else:
            self.log(f"Using persistent data folder: {DATA_DIR}")
        self.after(200, self.poll_log)
        self.after(1000, self.update_dashboard)
        self.after(3000, self.auto_check_comfy)

    def apply_dark_theme(self):
        self.configure(bg="#0b111a")
        try:
            self.option_add("*Font", "SegoeUI 9")
            self.option_add("*Background", "#0b111a")
            self.option_add("*Foreground", "#e5e7eb")
            self.option_add("*Entry.Background", "#111827")
            self.option_add("*Entry.Foreground", "#e5e7eb")
            self.option_add("*Button.Background", "#1f2937")
            self.option_add("*Button.Foreground", "#e5e7eb")
            self.option_add("*Checkbutton.Background", "#0b111a")
            self.option_add("*Checkbutton.Foreground", "#e5e7eb")
            self.option_add("*LabelFrame.Background", "#0b111a")
            self.option_add("*LabelFrame.Foreground", "#93c5fd")
        except Exception:
            pass

    def entry_row(self, parent, label, key, kind):
        f = tk.Frame(parent); f.pack(fill="x", padx=10, pady=5)
        tk.Label(f, text=label, width=30, anchor="w").pack(side="left")
        v = tk.StringVar(value=str(self.cfg.get(key, "")))
        self.vars[key] = v
        tk.Entry(f, textvariable=v).pack(side="left", fill="x", expand=True, padx=5)
        tk.Button(f, text="Browse", command=lambda: self.browse(key, kind)).pack(side="left")

    def build(self):
        header = tk.Frame(self, bg="#0b111a"); header.pack(fill="x", padx=10, pady=(8,0))
        tk.Label(header, text="Faithful Remaster  v10.3", font="SegoeUI 18 bold", fg="#60a5fa", bg="#0b111a").pack(side="left")
        tk.Label(header, text="  Multi-Emulator → ComfyUI Live Texture Control Center", font="SegoeUI 10", fg="#9ca3af", bg="#0b111a").pack(side="left", padx=10)
        top = tk.Frame(self, bg="#0b111a"); top.pack(fill="x", pady=8)
        self.entry_row(top, "Dump folder location", "dump_folder", "folder")
        self.entry_row(top, "Load folder location", "load_folder", "folder")
        self.entry_row(top, "Comfy workflow API file", "workflow_api_json", "file")

        f = tk.Frame(top); f.pack(fill="x", padx=10, pady=5)
        tk.Label(f, text="ComfyUI URL", width=30, anchor="w").pack(side="left")
        self.vars["comfy_url"] = tk.StringVar(value=str(self.cfg.get("comfy_url", "http://127.0.0.1:8188")))
        tk.Entry(f, textvariable=self.vars["comfy_url"]).pack(side="left", fill="x", expand=True, padx=5)

        cf = tk.Frame(top); cf.pack(fill="x", padx=10, pady=5)
        tk.Label(cf, text="ComfyUI start file", width=30, anchor="w").pack(side="left")
        self.vars["comfy_start_file"] = tk.StringVar(value=str(self.cfg.get("comfy_start_file", "")))
        tk.Entry(cf, textvariable=self.vars["comfy_start_file"]).pack(side="left", fill="x", expand=True, padx=5)
        tk.Button(cf, text="Browse", command=lambda: self.browse("comfy_start_file", "file")).pack(side="left")

        ids = tk.Frame(top); ids.pack(fill="x", padx=10, pady=5)
        tk.Label(ids, text="Load Image node ID", width=30, anchor="w").pack(side="left")
        self.vars["load_image_node_id"] = tk.StringVar(value=str(self.cfg.get("load_image_node_id", 1)))
        tk.Entry(ids, textvariable=self.vars["load_image_node_id"], width=8).pack(side="left", padx=5)
        tk.Label(ids, text="Save Image node ID").pack(side="left", padx=(20,5))
        self.vars["save_image_node_id"] = tk.StringVar(value=str(self.cfg.get("save_image_node_id", 12)))
        tk.Entry(ids, textvariable=self.vars["save_image_node_id"], width=8).pack(side="left", padx=5)

        opts = tk.Frame(top); opts.pack(fill="x", padx=10, pady=5)
        self.overwrite_var = tk.BooleanVar(value=bool(self.cfg.get("overwrite", False)))
        self.alpha_var = tk.BooleanVar(value=bool(self.cfg.get("preserve_alpha", True)))
        self.tmp_var = tk.BooleanVar(value=bool(self.cfg.get("process_tmp_image_files", True)))
        self.hash_var = tk.BooleanVar(value=bool(self.cfg.get("enable_hash_cache", True)))
        self.ignore_existing_var = tk.BooleanVar(value=bool(self.cfg.get("ignore_existing_silently", True)))
        self.priority_var = tk.BooleanVar(value=bool(self.cfg.get("prioritize_new_dumps", True)))
        tk.Checkbutton(opts, text="Overwrite existing", variable=self.overwrite_var).pack(side="left")
        tk.Checkbutton(opts, text="Preserve alpha", variable=self.alpha_var).pack(side="left", padx=(8,0))
        tk.Checkbutton(opts, text="Process .TMP", variable=self.tmp_var).pack(side="left", padx=(8,0))
        tk.Checkbutton(opts, text="Hash cache", variable=self.hash_var).pack(side="left", padx=(8,0))
        tk.Checkbutton(opts, text="Ignore existing", variable=self.ignore_existing_var).pack(side="left", padx=(8,0))
        tk.Checkbutton(opts, text="New dumps first", variable=self.priority_var).pack(side="left", padx=(8,0))

        filter_opts = tk.Frame(top); filter_opts.pack(fill="x", padx=10, pady=5)
        self.cutscene_filter_var = tk.BooleanVar(value=bool(self.cfg.get("skip_cutscene_buffers", True)))
        self.delete_cutscene_var = tk.BooleanVar(value=bool(self.cfg.get("delete_skipped_cutscene_buffers", False)))
        tk.Checkbutton(
            filter_opts,
            text="Skip pre-rendered cutscene buffers",
            variable=self.cutscene_filter_var
        ).pack(side="left")
        tk.Checkbutton(
            filter_opts,
            text="Delete skipped cutscene dumps",
            variable=self.delete_cutscene_var
        ).pack(side="left", padx=(20,0))
        tk.Label(
            filter_opts,
            text="Delete is OFF by default for safety",
            fg="#9ca3af"
        ).pack(side="left", padx=(12,0))

        status_opts = tk.Frame(top); status_opts.pack(fill="x", padx=10, pady=5)
        self.comfy_status_var = tk.BooleanVar(value=bool(self.cfg.get("enable_comfy_status", True)))
        self.pause_comfy_var = tk.BooleanVar(value=bool(self.cfg.get("pause_when_comfy_offline", True)))
        tk.Checkbutton(status_opts, text="Monitor Comfy status", variable=self.comfy_status_var).pack(side="left")
        tk.Checkbutton(status_opts, text="Pause when Comfy offline", variable=self.pause_comfy_var).pack(side="left", padx=(20,0))
        self.auto_missing_var = tk.BooleanVar(value=bool(self.cfg.get("auto_check_missing_load", True)))
        tk.Checkbutton(status_opts, text="Auto check missing Load", variable=self.auto_missing_var).pack(side="left", padx=(20,0))


        alpha_wf = tk.Frame(top); alpha_wf.pack(fill="x", padx=10, pady=5)
        self.alpha_workflow_var = tk.BooleanVar(value=bool(self.cfg.get("enable_separate_alpha_workflow", False)))
        tk.Checkbutton(alpha_wf, text="Separate Alpha Workflow", variable=self.alpha_workflow_var).pack(side="left")
        tk.Label(alpha_wf, text="Alpha workflow API", width=18, anchor="w").pack(side="left", padx=(20,5))
        self.vars["alpha_workflow_api_json"] = tk.StringVar(value=str(self.cfg.get("alpha_workflow_api_json", "")))
        tk.Entry(alpha_wf, textvariable=self.vars["alpha_workflow_api_json"]).pack(side="left", fill="x", expand=True, padx=5)
        tk.Button(alpha_wf, text="Browse", command=lambda: self.browse("alpha_workflow_api_json", "file")).pack(side="left")

        alpha_wf_ids = tk.Frame(top); alpha_wf_ids.pack(fill="x", padx=10, pady=5)
        tk.Label(alpha_wf_ids, text="Alpha Load node ID", width=30, anchor="w").pack(side="left")
        self.vars["alpha_load_image_node_id"] = tk.StringVar(value=str(self.cfg.get("alpha_load_image_node_id", 1)))
        tk.Entry(alpha_wf_ids, textvariable=self.vars["alpha_load_image_node_id"], width=8).pack(side="left", padx=5)
        tk.Label(alpha_wf_ids, text="Alpha Save node ID").pack(side="left", padx=(20,5))
        self.vars["alpha_save_image_node_id"] = tk.StringVar(value=str(self.cfg.get("alpha_save_image_node_id", 12)))
        tk.Entry(alpha_wf_ids, textvariable=self.vars["alpha_save_image_node_id"], width=8).pack(side="left", padx=5)
        self.alpha_wf_invert_var = tk.BooleanVar(value=bool(self.cfg.get("alpha_workflow_invert_output", False)))
        tk.Checkbutton(alpha_wf_ids, text="Invert alpha output", variable=self.alpha_wf_invert_var).pack(side="left", padx=(20,0))
        tk.Label(alpha_wf_ids, text="alpha image should be white=visible, black=transparent").pack(side="left", padx=10)

        vram = tk.Frame(top); vram.pack(fill="x", padx=10, pady=5)
        self.vram_var = tk.BooleanVar(value=bool(self.cfg.get("enable_vram_protection", True)))
        tk.Checkbutton(vram, text="Enable VRAM Protection", variable=self.vram_var).pack(side="left")
        tk.Label(vram, text="Max VRAM GB").pack(side="left", padx=(20,5))
        self.vars["max_vram_gb"] = tk.StringVar(value=str(self.cfg.get("max_vram_gb", 10.0)))
        tk.Entry(vram, textvariable=self.vars["max_vram_gb"], width=6).pack(side="left")
        tk.Label(vram, text="Resume margin GB").pack(side="left", padx=(20,5))
        self.vars["vram_resume_margin_gb"] = tk.StringVar(value=str(self.cfg.get("vram_resume_margin_gb", 0.5)))
        tk.Entry(vram, textvariable=self.vars["vram_resume_margin_gb"], width=6).pack(side="left")

        # Clean Stable: alpha is handled by the separate Alpha Workflow, so old alpha tuning controls are hidden.
        a = tk.Frame(top); a.pack(fill="x", padx=10, pady=5)
        tk.Label(a, text="Alpha mode", width=30, anchor="w").pack(side="left")
        tk.Label(a, text="Separate Alpha Workflow / UltraSharp mask path", anchor="w").pack(side="left", padx=5)

        buttons = tk.Frame(top); buttons.pack(fill="x", padx=10, pady=8)
        tk.Button(buttons, text="Save Settings", command=self.save_settings, width=16).pack(side="left", padx=3)
        tk.Button(buttons, text="Start Watching", command=self.start, width=16).pack(side="left", padx=3)
        tk.Button(buttons, text="Force Dump Check", command=self.force_dump_check, width=16).pack(side="left", padx=3)
        tk.Button(buttons, text="Check Missing Load", command=self.check_missing_load_now, width=17).pack(side="left", padx=3)
        tk.Button(buttons, text="Start ComfyUI", command=self.start_comfy_ui, width=14).pack(side="left", padx=3)
        tk.Button(buttons, text="Check Comfy Now", command=self.check_comfy_now, width=16).pack(side="left", padx=3)
        tk.Button(buttons, text="Stop", command=self.stop, width=9).pack(side="left", padx=3)
        tk.Button(buttons, text="Clear processed", command=self.clear_processed, width=14).pack(side="left", padx=3)
        tk.Button(buttons, text="Clear cache", command=self.clear_cache, width=11).pack(side="left", padx=3)
        tk.Button(buttons, text="Texture Manager", command=self.open_texture_manager, width=16).pack(side="left", padx=3)

        dash = tk.LabelFrame(self, text="Dashboard")
        dash.pack(fill="x", padx=10, pady=(0,8))
        self.dashboard_var = tk.StringVar(value="")
        tk.Label(dash, textvariable=self.dashboard_var, justify="left", anchor="w").pack(fill="x", padx=10, pady=8)

        tk.Label(self, text="Log").pack(anchor="w", padx=10)
        self.log_text = tk.Text(self, height=22, bg="#020617", fg="#d1d5db", insertbackground="#ffffff")
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(0,10))
        if not PIL_AVAILABLE:
            self.log_q.put("WARNING: Pillow not installed. Install: pip install pillow")

    def get_dump_load_paths_for_item(self, dump_path):
        cfg = self.collect()
        dump_folder = Path(cfg["dump_folder"])
        load_folder = Path(cfg["load_folder"])
        return output_path_for_input(Path(dump_path), dump_folder, load_folder)

    def _make_thumb(self, path, size=(180, 180)):
        if not PIL_AVAILABLE or not Path(path).exists():
            return None
        try:
            from PIL import ImageTk
            im = Image.open(path).convert("RGBA")
            im.thumbnail(size)
            return ImageTk.PhotoImage(im)
        except Exception:
            return None

    def open_texture_manager(self):
        try:
            cfg = self.collect()
            dump_folder = Path(cfg["dump_folder"])
            load_folder = Path(cfg["load_folder"])
            if not dump_folder.exists():
                messagebox.showerror("Texture Manager", "Dump folder does not exist.")
                return
        except Exception as e:
            messagebox.showerror("Texture Manager", str(e))
            return

        win = tk.Toplevel(self)
        win.title("Texture Manager")
        win.geometry("980x650")
        try:
            win.configure(bg="#0b111a")
        except Exception:
            pass

        left = tk.Frame(win, bg="#0b111a")
        left.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        right = tk.Frame(win, bg="#0b111a")
        right.pack(side="right", fill="both", padx=8, pady=8)

        search_var = tk.StringVar()
        tk.Label(left, text="Dump textures", bg="#0b111a", fg="#93c5fd", font="SegoeUI 12 bold").pack(anchor="w")
        tk.Entry(left, textvariable=search_var, bg="#111827", fg="#e5e7eb").pack(fill="x", pady=(4,6))

        list_frame = tk.Frame(left)
        list_frame.pack(fill="both", expand=True)
        lb = tk.Listbox(list_frame, bg="#020617", fg="#e5e7eb", selectbackground="#1d4ed8")
        lb.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(list_frame, command=lb.yview)
        sb.pack(side="right", fill="y")
        lb.config(yscrollcommand=sb.set)

        info_var = tk.StringVar(value="Select a texture")
        tk.Label(right, textvariable=info_var, bg="#0b111a", fg="#e5e7eb", wraplength=360, justify="left").pack(anchor="w", pady=(0,8))

        dump_label = tk.Label(right, text="Dump preview", bg="#0b111a", fg="#93c5fd")
        dump_label.pack()
        dump_img_label = tk.Label(right, bg="#111827", width=200, height=200)
        dump_img_label.pack(pady=(2,8))

        load_label = tk.Label(right, text="Load preview", bg="#0b111a", fg="#93c5fd")
        load_label.pack()
        load_img_label = tk.Label(right, bg="#111827", width=200, height=200)
        load_img_label.pack(pady=(2,8))

        files = []

        def refresh():
            nonlocal files
            q = search_var.get().lower().strip()
            all_files = [
                p for p in dump_folder.rglob("*")
                if p.is_file() and is_image_like(p, cfg.get("process_tmp_image_files", True))
            ]
            all_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            if q:
                all_files = [p for p in all_files if q in p.name.lower() or q in str(p).lower()]
            files = all_files
            lb.delete(0, "end")
            for p in files[:5000]:
                out = output_path_for_input(p, dump_folder, load_folder)
                tag = "LOAD" if out.exists() else "MISSING"
                exc = "EXC" if is_exception_texture(p) else ""
                lb.insert("end", f"[{tag}] {exc} {p.name}")

        def selected_path():
            sel = lb.curselection()
            if not sel:
                return None
            idx = sel[0]
            if idx >= len(files):
                return None
            return files[idx]

        def update_preview(event=None):
            p = selected_path()
            if not p:
                return
            out = output_path_for_input(p, dump_folder, load_folder)
            info_var.set(f"Dump: {p}\nLoad: {out}\nLoad exists: {out.exists()}\nException: {is_exception_texture(p)}")
            dimg = self._make_thumb(p)
            limg = self._make_thumb(out)
            dump_img_label.configure(image=dimg)
            dump_img_label.image = dimg
            load_img_label.configure(image=limg)
            load_img_label.image = limg

        def add_to_exceptions():
            p = selected_path()
            if not p:
                return
            add_exception_pattern(p.name)
            self.log(f"Added to exceptions: {p.name}")
            refresh()
            update_preview()

        def delete_load():
            p = selected_path()
            if not p:
                return
            out = output_path_for_input(p, dump_folder, load_folder)
            if out.exists():
                out.unlink()
                self.log(f"Deleted load texture: {out.name}")
            refresh()
            update_preview()

        def recreate():
            messagebox.showinfo(
                "Recreate texture",
                "Stable Legacy disables direct Recreate to avoid worker stalls.\n\n"
                "Safe method:\n"
                "1. Click Delete Load texture\n"
                "2. Click Check Missing Load in the main window\n"
                "3. Or restart watching with Hash cache OFF and Overwrite ON for testing."
            )

        def open_dump_folder():
            p = selected_path()
            if p:
                os.startfile(str(p.parent))

        def open_load_folder():
            p = selected_path()
            if p:
                out = output_path_for_input(p, dump_folder, load_folder)
                out.parent.mkdir(parents=True, exist_ok=True)
                os.startfile(str(out.parent))

        btns = tk.Frame(right, bg="#0b111a")
        btns.pack(fill="x", pady=8)
        tk.Button(btns, text="Refresh", command=refresh).pack(fill="x", pady=2)
        tk.Button(btns, text="Add to exceptions", command=add_to_exceptions).pack(fill="x", pady=2)
        tk.Button(btns, text="Recreate texture", command=recreate).pack(fill="x", pady=2)
        tk.Button(btns, text="Delete Load texture", command=delete_load).pack(fill="x", pady=2)
        tk.Button(btns, text="Open Dump folder", command=open_dump_folder).pack(fill="x", pady=2)
        tk.Button(btns, text="Open Load folder", command=open_load_folder).pack(fill="x", pady=2)

        lb.bind("<<ListboxSelect>>", update_preview)
        search_var.trace_add("write", lambda *args: refresh())
        refresh()

    def browse(self, key, kind):
        p = filedialog.askdirectory() if kind == "folder" else filedialog.askopenfilename(filetypes=[("JSON files","*.json"),("All files","*.*")])
        if p:
            self.vars[key].set(p)

    def collect(self):
        cfg = dict(self.cfg)
        for k,v in self.vars.items():
            cfg[k] = v.get()
        cfg["load_image_node_id"] = int(cfg["load_image_node_id"])
        cfg["save_image_node_id"] = int(cfg["save_image_node_id"])
        cfg["overwrite"] = bool(self.overwrite_var.get())
        cfg["preserve_alpha"] = bool(self.alpha_var.get())
        cfg["process_tmp_image_files"] = bool(self.tmp_var.get())
        cfg["enable_hash_cache"] = bool(self.hash_var.get())
        cfg["ignore_existing_silently"] = bool(self.ignore_existing_var.get())
        cfg["prioritize_new_dumps"] = bool(self.priority_var.get())
        cfg["skip_cutscene_buffers"] = bool(self.cutscene_filter_var.get())
        cfg["delete_skipped_cutscene_buffers"] = bool(self.delete_cutscene_var.get())
        cfg["enable_comfy_status"] = bool(self.comfy_status_var.get())
        cfg["pause_when_comfy_offline"] = bool(self.pause_comfy_var.get())
        cfg["auto_check_missing_load"] = bool(self.auto_missing_var.get())
        cfg["fix_alpha_edge_bleed"] = False
        cfg["alpha_bleed_iterations"] = 1
        cfg["alpha_edge_threshold"] = 32
        cfg["enable_separate_alpha_workflow"] = bool(self.alpha_workflow_var.get())
        cfg["alpha_load_image_node_id"] = int(cfg.get("alpha_load_image_node_id", 1))
        cfg["alpha_save_image_node_id"] = int(cfg.get("alpha_save_image_node_id", 12))
        cfg["alpha_workflow_invert_output"] = bool(self.alpha_wf_invert_var.get())
        cfg["enable_alpha_workflow"] = bool(getattr(self, "alpha_workflow_var", tk.BooleanVar(value=False)).get())
        cfg["alpha_load_image_node_id"] = int(cfg.get("alpha_load_image_node_id", 1))
        cfg["alpha_save_image_node_id"] = int(cfg.get("alpha_save_image_node_id", 5))
        cfg["invert_alpha_output"] = bool(getattr(self, "invert_alpha_var", tk.BooleanVar(value=False)).get())
        cfg["enable_vram_protection"] = bool(self.vram_var.get())
        cfg["max_vram_gb"] = float(cfg["max_vram_gb"])
        cfg["vram_resume_margin_gb"] = float(cfg["vram_resume_margin_gb"])
        cfg["alpha_resize_method"] = "nearest"
        cfg["alpha_source"] = "original"
        cfg["alpha_feather_radius"] = float(cfg.get("alpha_feather_radius", 0.0))
        return cfg

    def save_settings(self):

        # Auto-detect node IDs before saving when each workflow has a single LoadImage/SaveImage.
        try:
            rgb_path = self.vars.get("workflow_api_json").get().strip()
            if rgb_path:
                info = detect_comfy_nodes_from_api(rgb_path)
                if info.get("best_load"):
                    self.vars["load_image_node_id"].set(str(info["best_load"]))
                if info.get("best_save"):
                    self.vars["save_image_node_id"].set(str(info["best_save"]))
        except Exception:
            pass
        try:
            alpha_path = self.vars.get("alpha_workflow_api_json").get().strip()
            if alpha_path:
                info = detect_comfy_nodes_from_api(alpha_path)
                if info.get("best_load"):
                    self.vars["alpha_load_image_node_id"].set(str(info["best_load"]))
                if info.get("best_save"):
                    self.vars["alpha_save_image_node_id"].set(str(info["best_save"]))
        except Exception:
            pass
        try:
            self.cfg = self.collect()
            save_config(self.cfg)
            self.log("Settings saved. Auto Detect Nodes mode active.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def validate(self,cfg):
        for k in ["dump_folder","load_folder","workflow_api_json"]:
            if not cfg.get(k):
                raise ValueError(f"Missing: {k}")
        if not Path(cfg["dump_folder"]).exists():
            raise ValueError("Dump folder does not exist")
        if not Path(cfg["workflow_api_json"]).exists():
            raise ValueError("Workflow API JSON file does not exist")
        if cfg.get("enable_separate_alpha_workflow") and cfg.get("alpha_workflow_api_json") and not Path(cfg["alpha_workflow_api_json"]).exists():
            raise ValueError("Alpha workflow API JSON file does not exist")
        if (cfg.get("preserve_alpha") or cfg.get("process_tmp_image_files")) and not PIL_AVAILABLE:
            raise ValueError("This option needs Pillow. Install: pip install pillow")

    def start(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self.log("Already running.")
            return
        try:
            self.cfg = self.collect()
            self.validate(self.cfg)
            save_config(self.cfg)
            self.stop_event.clear()
            self.force_scan_event.clear()
            self.force_missing_event.clear()
            self.stats.update({"processed":0, "cache_hits":0, "comfy_jobs":0, "queue_len":0, "high_queue_len":0, "low_queue_len":0, "peak_vram_mb":0, "exceptions_skipped":0, "status":"RUNNING"})
            worker = Worker(self.cfg, self.log_q, self.stop_event, self.force_scan_event, self.force_missing_event, self.stats)
            self.worker_thread = threading.Thread(target=worker.run, daemon=True)
            self.worker_thread.start()
            self.log("Started.")
        except Exception as e:
            messagebox.showerror("Start failed", str(e))

    def force_dump_check(self):
        self.force_scan_event.set()
        self.log("Force dump check requested.")

    def check_missing_load_now(self):
        self.force_missing_event.set()
        self.force_scan_event.set()
        self.log("Manual missing Load check requested.")

    def start_comfy_ui(self):
        try:
            cfg = self.collect()
            path = str(cfg.get("comfy_start_file", "")).strip()
            if not path:
                messagebox.showerror("Start ComfyUI", "Please select ComfyUI start file first.")
                return
            p = Path(path)
            if not p.exists():
                messagebox.showerror("Start ComfyUI", "ComfyUI start file does not exist.")
                return

            suffix = p.suffix.lower()
            cwd = str(p.parent)

            if suffix in (".bat", ".cmd"):
                subprocess.Popen(["cmd", "/c", str(p)], cwd=cwd, creationflags=subprocess.CREATE_NEW_CONSOLE)
            elif suffix == ".py":
                subprocess.Popen(["python", str(p)], cwd=cwd, creationflags=subprocess.CREATE_NEW_CONSOLE)
            elif suffix == ".exe":
                subprocess.Popen([str(p)], cwd=cwd)
            else:
                os.startfile(str(p))

            self.log(f"Starting ComfyUI: {p.name}")
        except Exception as e:
            messagebox.showerror("Start ComfyUI failed", str(e))

    def _set_comfy_status(self, status, detail="", state="neutral"):
        if hasattr(self, "comfy_status_var"):
            self.comfy_status_var.set(status)
        if hasattr(self, "comfy_detail_var"):
            self.comfy_detail_var.set(detail)
        if hasattr(self, "comfy_status_label"):
            colors = {
                "online": "#22c55e",
                "offline": "#ef4444",
                "checking": "#f59e0b",
                "neutral": "#9ca3af",
            }
            self.comfy_status_label.configure(fg=colors.get(state, "#9ca3af"))

    def _apply_comfy_result(self, st, comfy_url, elapsed_ms=None, log_result=True):
        self.stats["comfy_online"] = bool(st.get("online"))
        self.stats["comfy_running"] = st.get("queue_running")
        self.stats["comfy_pending"] = st.get("queue_pending")
        self.stats["comfy_error"] = st.get("error", "")

        if st.get("online"):
            detail = comfy_url
            if elapsed_ms is not None:
                detail += f" — {elapsed_ms} ms"
            running = st.get("queue_running")
            pending = st.get("queue_pending")
            if running is not None or pending is not None:
                detail += f" — Running: {running or 0}, Pending: {pending or 0}"
            self._set_comfy_status("● Online", detail, "online")
            if log_result:
                self.log(f"ComfyUI ONLINE. {detail}")
        else:
            reason = st.get("error", "unknown error")
            detail = f"{comfy_url} — {reason}" if comfy_url else reason
            self._set_comfy_status("● Offline", detail, "offline")
            if log_result:
                self.log(f"ComfyUI OFFLINE: {reason}")

    def check_comfy_now(self):
        if getattr(self, "_comfy_check_running", False):
            return
        self._comfy_check_running = True
        self._set_comfy_status("Checking…", "", "checking")

        try:
            cfg = self.collect()
            comfy_url = str(cfg.get("comfy_url", "") or "").strip().rstrip("/")
        except Exception:
            comfy_url = str(self.cfg.get("comfy_url", "") or "").strip().rstrip("/")

        def worker():
            started = time.perf_counter()
            try:
                st = check_comfy_status(comfy_url)
            except Exception as exc:
                st = {"online": False, "error": str(exc)}
            elapsed_ms = int((time.perf_counter() - started) * 1000)

            def finish():
                self._comfy_check_running = False
                self._apply_comfy_result(st, comfy_url, elapsed_ms, log_result=True)

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def auto_check_comfy(self):
        try:
            cfg = self.collect()
            if cfg.get("enable_comfy_status", True):
                comfy_url = str(cfg.get("comfy_url", "") or "").strip().rstrip("/")
                st = check_comfy_status(comfy_url)
                self._apply_comfy_result(st, comfy_url, None, log_result=False)
        except Exception:
            pass
        self.after(5000, self.auto_check_comfy)

    def stop(self):
        self.stop_event.set()

    def clear_processed(self):
        p = DATA_DIR / "processed.txt"
        if p.exists():
            p.unlink()
        self.log("Processed log cleared.")

    def clear_cache(self):
        if messagebox.askyesno("Clear hash cache", "Delete all cached outputs?"):
            for p in CACHE_DIR.glob("*.png"):
                p.unlink(missing_ok=True)
            if CACHE_INDEX_PATH.exists():
                CACHE_INDEX_PATH.unlink(missing_ok=True)
            self.log("Hash cache cleared.")

    def log(self,msg):
        self.log_q.put(msg)

    def poll_log(self):
        messages = []
        while True:
            try:
                msg = self.log_q.get_nowait()
            except queue.Empty:
                break
            messages.append(str(msg))

        if messages:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            try:
                with APP_LOG_PATH.open("a", encoding="utf-8") as log_file:
                    for msg in messages:
                        log_file.write(msg + "\n")
            except Exception:
                pass

            if hasattr(self, "log_text"):
                for msg in messages:
                    self.log_text.insert("end", msg + "\n")
                if not hasattr(self, "log_autoscroll_var") or self.log_autoscroll_var.get():
                    self.log_text.see("end")

        self.after(200, self.poll_log)

    def update_dashboard(self):
        used, total = get_vram_info()
        if used is not None:
            self.stats["vram_used_mb"] = used
            self.stats["vram_total_mb"] = total
            self.stats["peak_vram_mb"] = max(self.stats.get("peak_vram_mb", 0), used)
            vram_line = f"VRAM: {used/1024:.1f} / {total/1024:.1f} GB    Peak: {self.stats.get('peak_vram_mb',0)/1024:.1f} GB"
        else:
            vram_line = "VRAM: nvidia-smi unavailable"

        if self.stats.get("comfy_online"):
            comfy_line = f"Comfy: ONLINE    Running: {self.stats.get('comfy_running')}    Pending: {self.stats.get('comfy_pending')}"
        else:
            err = self.stats.get("comfy_error", "")
            comfy_line = "Comfy: OFFLINE" + (f"    {err[:90]}" if err else "")

        self.dashboard_var.set(
            f"Status: {self.stats.get('status','STOPPED')}\n"
            f"{comfy_line}\n"
            f"{vram_line}\n"
            f"HIGH priority: {self.stats.get('high_queue_len',0)}    "
            f"LOW priority: {self.stats.get('low_queue_len',0)}    "
            f"Total queue: {self.stats.get('queue_len',0)}\n"
            f"Processed this session: {self.stats.get('processed',0)}    "
            f"Cache hits/restores: {self.stats.get('cache_hits',0)}    "
            f"Comfy jobs: {self.stats.get('comfy_jobs',0)}    "
            f"Exceptions skipped: {self.stats.get('exceptions_skipped',0)}    "
            f"Cutscene buffers skipped: {self.stats.get('cutscene_buffers_skipped',0)}    "
            f"Deleted: {self.stats.get('cutscene_buffers_deleted',0)}"
        )
        try:
            self.update_live_preview()
        except Exception:
            pass
        self.after(1000, self.update_dashboard)


# ============================================================
# Faithful Remaster v11.5.2 Beta
# Profiles + Multi-emulator tabbed interface
# ============================================================


# ---------- Universal game title database helpers ----------
GAME_DB_PATH = DATA_DIR / "game_titles.sqlite"
GAME_TITLES_PATH = DATA_DIR / "game_titles.json"

DEFAULT_GAME_TITLES = {
    "GPTE41": {
        "title": "Prince of Persia: The Sands of Time",
        "region": "USA",
        "emulator": "Dolphin"
    },
    "SLUS20312": {
        "title": "Final Fantasy X",
        "region": "USA",
        "emulator": "PCSX2"
    }
}

NINTENDO_REGION_CODES = {
    "E": "USA", "P": "Europe", "J": "Japan", "K": "Korea",
    "W": "Taiwan", "C": "China", "D": "Germany", "F": "France",
    "I": "Italy", "S": "Spain"
}

LIBRETRO_DAT_SOURCES = {
    "DuckStation": [
        "metadat/redump/Sony - PlayStation.dat",
    ],
    "PCSX2": [
        "metadat/redump/Sony - PlayStation 2.dat",
    ],
    "PPSSPP": [
        "metadat/redump/Sony - PlayStation Portable.dat",
    ],
    "Azahar / Citra": [
        "metadat/no-intro/Nintendo - Nintendo 3DS.dat",
    ],
    "Dolphin": [
        "dat/Nintendo - GameCube.dat",
        "dat/Nintendo - Wii.dat",
    ],
}

class _TitleHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title_text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title_text.append(data)

def normalize_game_id(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper().strip())

def init_game_database():
    con = sqlite3.connect(GAME_DB_PATH)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS games (
                normalized_id TEXT NOT NULL,
                original_id TEXT NOT NULL,
                title TEXT NOT NULL,
                region TEXT DEFAULT '',
                platform TEXT DEFAULT '',
                source TEXT DEFAULT '',
                PRIMARY KEY (normalized_id, platform)
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_games_id ON games(normalized_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_games_platform ON games(platform)")
        con.commit()
    finally:
        con.close()

def db_upsert_game(game_id, title, region="", platform="", source=""):
    normalized = normalize_game_id(game_id)
    title = str(title or "").strip()
    if not normalized or not title:
        return False
    init_game_database()
    con = sqlite3.connect(GAME_DB_PATH)
    try:
        con.execute("""
            INSERT INTO games(normalized_id, original_id, title, region, platform, source)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(normalized_id, platform) DO UPDATE SET
                original_id=excluded.original_id,
                title=excluded.title,
                region=excluded.region,
                source=excluded.source
        """, (normalized, str(game_id).strip(), title, region, platform, source))
        con.commit()
        return True
    finally:
        con.close()

def db_lookup_game(game_id, platform=""):
    normalized = normalize_game_id(game_id)
    if not normalized:
        return None
    init_game_database()
    con = sqlite3.connect(GAME_DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        if platform:
            row = con.execute(
                "SELECT * FROM games WHERE normalized_id=? AND platform=? LIMIT 1",
                (normalized, platform)
            ).fetchone()
            if row:
                return dict(row)
        row = con.execute(
            "SELECT * FROM games WHERE normalized_id=? LIMIT 1",
            (normalized,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()

def load_game_titles():
    data = dict(DEFAULT_GAME_TITLES)
    if GAME_TITLES_PATH.exists():
        try:
            custom = json.loads(GAME_TITLES_PATH.read_text(encoding="utf-8"))
            if isinstance(custom, dict):
                data.update(custom)
        except Exception:
            pass
    else:
        GAME_TITLES_PATH.write_text(
            json.dumps(DEFAULT_GAME_TITLES, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
    return data

def save_game_titles(data):
    GAME_TITLES_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

def infer_nintendo_region(game_id):
    gid = str(game_id).strip().upper()
    return NINTENDO_REGION_CODES.get(gid[3], "") if len(gid) >= 4 else ""

def clean_gametdb_title(raw_title, game_id):
    title = (raw_title or "").strip()
    title = re.sub(r"\s*[-|]\s*GameTDB.*$", "", title, flags=re.I)
    title = re.sub(rf"^\s*{re.escape(game_id)}\s*[-:|]\s*", "", title, flags=re.I)
    title = re.sub(rf"\s*\(\s*{re.escape(game_id)}\s*\)\s*$", "", title, flags=re.I)
    return title.strip()

def infer_region_from_title(title):
    for pattern, region in [
        (r"\((USA|US)\)", "USA"),
        (r"\((Europe|EU)\)", "Europe"),
        (r"\((Japan|JP)\)", "Japan"),
        (r"\((Korea|KR)\)", "Korea"),
        (r"\((Australia)\)", "Australia"),
        (r"\((World)\)", "World"),
    ]:
        if re.search(pattern, str(title or ""), re.I):
            return region
    return ""

def clean_title_region_suffix(title):
    return re.sub(
        r"\s*\((USA|US|Europe|EU|Japan|JP|Korea|KR|Australia|World)\)\s*$",
        "",
        str(title or "").strip(),
        flags=re.I
    ).strip()

def parse_libretro_dat(text, platform, source_name):
    """
    Parse clrmamepro-style DAT files, including minified files with many
    game entries packed onto long lines.
    """
    results = []
    lower = text.lower()
    pos = 0

    while True:
        match = re.search(r"\bgame\s*\(", lower[pos:])
        if not match:
            break

        start = pos + match.start()
        paren = text.find("(", start)
        if paren < 0:
            break

        depth = 0
        in_quote = False
        escape = False
        end = None

        for i in range(paren, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\" and in_quote:
                escape = True
                continue
            if ch == '"':
                in_quote = not in_quote
                continue
            if in_quote:
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        if end is None:
            break

        block = text[start:end]
        pos = end

        name_match = re.search(r'\bname\s+"([^"]+)"', block, re.I)
        if not name_match:
            continue

        full_title = name_match.group(1).strip()

        serials = re.findall(
            r'\b(?:serial|gameid|game_id|product_code)\s+"([^"]+)"',
            block,
            re.I
        )

        if not serials:
            serials = re.findall(
                r'(?i)\b((?:SLUS|SCUS|SLES|SCES|SLPS|SLPM|ULUS|UCUS|ULES|UCES|'
                r'NPJH|NPUH|NPUG|NPEH|NPEG)[-_. ]?\d{4,6})\b',
                block
            )

        if not serials and platform == "Dolphin":
            serials = re.findall(r'\b([A-Z0-9]{6})\b', block)

        if not serials:
            continue

        region_match = re.search(r'\bregion\s+"([^"]+)"', block, re.I)
        region = region_match.group(1).strip() if region_match else infer_region_from_title(full_title)
        title = clean_title_region_suffix(full_title)

        seen = set()
        for serial in serials:
            serial = serial.strip()
            normalized = normalize_game_id(serial)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            results.append({
                "id": serial,
                "title": title,
                "region": region,
                "platform": platform,
                "source": source_name,
            })

    return results

def download_libretro_dat(repo_path, timeout=60):
    encoded_path = "/".join(urllib.parse.quote(part) for part in repo_path.split("/"))
    urls = [
        f"https://raw.githubusercontent.com/libretro/libretro-database/master/{encoded_path}",
        f"https://raw.githubusercontent.com/libretro/libretro-database/main/{encoded_path}",
    ]
    last_error = None

    for url in urls:
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "Faithful-Remaster/11.3.6"}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content = response.read()
            if len(content) > 100:
                return content.decode("utf-8", errors="replace"), url
            last_error = RuntimeError("Downloaded file was unexpectedly small")
        except Exception as e:
            last_error = e

    raise RuntimeError(f"Could not download {repo_path}: {last_error}")


PROFILES_PATH = DATA_DIR / "profiles.json"
PROFILES_DIR = DATA_DIR / "profiles"
PROFILES_DIR.mkdir(exist_ok=True)

EMULATORS = ["Dolphin", "DuckStation", "PCSX2", "PPSSPP", "Azahar / Citra", "Generic"]


def _first_existing_or_default(candidates):
    candidates = [Path(p) for p in candidates if p]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else None


def default_emulator_roots(emulator):
    """Return known per-user texture roots without hard-coding a username."""
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    documents = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents"

    if emulator == "Dolphin":
        base = appdata / "Dolphin Emulator"
        return base / "Dump" / "Textures", base / "Load" / "Textures"

    if emulator == "Azahar / Citra":
        base = appdata / "Azahar"
        return base / "dump" / "textures", base / "load" / "textures"

    if emulator == "PPSSPP":
        candidates = [
            documents / "PPSSPP" / "PSP" / "TEXTURES",
            appdata / "PPSSPP" / "PSP" / "TEXTURES",
            APP_DIR / "memstick" / "PSP" / "TEXTURES",
            APP_DIR / "PSP" / "TEXTURES",
        ]
        root = _first_existing_or_default(candidates)
        return root, root

    return None, None

def derive_load_folder_for_emulator(emulator, dump_folder):
    """Derive a same-game load folder from a selected dump folder when possible."""
    dump = Path(dump_folder)
    if emulator == "PPSSPP":
        if dump.name.lower() == "new":
            return dump.parent
        root_dump, _ = default_emulator_roots(emulator)
        if root_dump and dump.parent == root_dump:
            return dump
        if dump.parent.name.upper() == "TEXTURES":
            return dump

    if emulator == "Azahar / Citra":
        parts = list(dump.parts)
        lowered = [p.lower() for p in parts]
        try:
            idx = lowered.index("dump")
            parts[idx] = "load"
            return Path(*parts)
        except ValueError:
            root_dump, root_load = default_emulator_roots(emulator)
            if root_dump and dump.parent == root_dump:
                return root_load / dump.name
    if emulator == "Dolphin":
        parts = list(dump.parts)
        lowered = [p.lower() for p in parts]
        try:
            idx = lowered.index("dump")
            parts[idx] = "Load"
            return Path(*parts)
        except ValueError:
            root_dump, root_load = default_emulator_roots(emulator)
            if root_dump and dump.parent == root_dump:
                return root_load / dump.name
    return None


def sync_azahar_pack_json(dump_folder, load_folder, logger=None):
    """Copy Azahar pack.json only when it changed, preserving one backup."""
    src = Path(dump_folder) / "pack.json"
    dst_dir = Path(load_folder)
    dst = dst_dir / "pack.json"
    if not src.is_file():
        return False
    try:
        src_bytes = src.read_bytes()
        if dst.is_file() and dst.read_bytes() == src_bytes:
            return False
        dst_dir.mkdir(parents=True, exist_ok=True)
        if dst.is_file():
            shutil.copy2(dst, dst.with_name("pack.json.bak"))
        shutil.copy2(src, dst)
        if logger:
            logger("Azahar pack.json synchronized to Load folder.")
        return True
    except Exception as exc:
        if logger:
            logger(f"Azahar pack.json sync failed: {exc}")
        return False

PROFILE_SETTING_KEYS = [
    "dump_folder", "load_folder", "workflow_api_json", "alpha_workflow_api_json",
    "comfy_url", "comfy_start_file", "load_image_node_id", "save_image_node_id",
    "alpha_load_image_node_id", "alpha_save_image_node_id", "overwrite",
    "preserve_alpha", "process_tmp_image_files", "enable_hash_cache",
    "ignore_existing_silently", "prioritize_new_dumps", "enable_comfy_status",
    "pause_when_comfy_offline", "auto_check_missing_load",
    "enable_separate_alpha_workflow", "alpha_workflow_invert_output",
    "enable_vram_protection", "max_vram_gb", "vram_resume_margin_gb",
    "skip_cutscene_buffers", "delete_skipped_cutscene_buffers",
    "auto_sync_azahar_pack_json", "live_texture_preview"
]

def safe_profile_name(name):
    text = "".join(c if c.isalnum() or c in "._- " else "_" for c in str(name))
    return text.strip().replace(" ", "_") or "profile"

def load_profiles_data():
    default = {
        "active_profile": "",
        "profiles": {},
        "scan_roots": {},
        "auto_discover_games": True,
        "auto_add_discovered_games": False
    }
    if PROFILES_PATH.exists():
        try:
            data = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
            default.update(data)
        except Exception:
            pass
    return default

def save_profiles_data(data):
    PROFILES_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

class V11App(App):
    def __init__(self):
        tk.Tk.__init__(self)
        self.title("Faithful Remaster v11.5.2 Beta - ComfyUI Status Fix")
        self.geometry("1220x880")
        self.minsize(1040, 720)
        self.apply_dark_theme()

        self.profile_data = load_profiles_data()
        self.current_profile_name = self.profile_data.get("active_profile", "")
        base_cfg = load_config()

        if self.current_profile_name in self.profile_data.get("profiles", {}):
            base_cfg.update(self.profile_data["profiles"][self.current_profile_name].get("settings", {}))

        self.cfg = base_cfg
        self.log_q = queue.Queue()
        self.stop_event = threading.Event()
        self.force_scan_event = threading.Event()
        self.force_missing_event = threading.Event()
        self.worker_thread = None
        self.vars = {}
        self.stats = {
            "processed": 0, "cache_hits": 0, "comfy_jobs": 0, "queue_len": 0,
            "high_queue_len": 0, "low_queue_len": 0, "peak_vram_mb": 0,
            "status": "STOPPED", "comfy_online": False, "comfy_running": None,
            "comfy_pending": None, "comfy_error": "", "exceptions_skipped": 0,
            "cutscene_buffers_skipped": 0, "cutscene_buffers_deleted": 0,
            "current_input_path": "", "current_output_path": "",
            "current_texture_stage": "Waiting"
        }
        self._preview_last_input = ""
        self._preview_last_output = ""
        self._preview_original_photo = None
        self._preview_enhanced_photo = None
        self.comfy_status_var = tk.StringVar(value="Not checked")
        self.comfy_detail_var = tk.StringVar(value="")
        self._comfy_check_running = False
        self._loading_profile = False
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        APP_LOG_PATH.touch(exist_ok=True)
        self.seed_local_game_database()
        self.build()
        self.after(200, self.poll_log)
        self.after(1000, self.update_dashboard)
        self.after(3000, self.auto_check_comfy)

    def auto_check_comfy(self):
        if getattr(self, "_comfy_check_running", False):
            self.after(5000, self.auto_check_comfy)
            return

        try:
            cfg = self.collect()
            enabled = bool(cfg.get("enable_comfy_status", True))
            comfy_url = str(cfg.get("comfy_url", "") or "").strip().rstrip("/")
        except Exception:
            enabled = bool(self.cfg.get("enable_comfy_status", True))
            comfy_url = str(self.cfg.get("comfy_url", "") or "").strip().rstrip("/")

        if not enabled:
            self.after(5000, self.auto_check_comfy)
            return

        self._comfy_check_running = True

        def worker():
            started = time.perf_counter()
            try:
                st = check_comfy_status(comfy_url)
            except Exception as exc:
                st = {"online": False, "error": str(exc)}
            elapsed_ms = int((time.perf_counter() - started) * 1000)

            def finish():
                self._comfy_check_running = False
                self._apply_comfy_result(
                    st,
                    comfy_url,
                    elapsed_ms,
                    log_result=False
                )

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()
        self.after(5000, self.auto_check_comfy)

    # ---------- Generic UI helpers ----------
    def labeled_entry(self, parent, label, key, browse_kind=None):
        row = tk.Frame(parent)
        row.pack(fill="x", padx=12, pady=6)
        tk.Label(row, text=label, width=28, anchor="w").pack(side="left")
        var = tk.StringVar(value=str(self.cfg.get(key, "")))
        self.vars[key] = var
        tk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True, padx=6)
        if browse_kind:
            tk.Button(row, text="Browse", command=lambda: self.browse(key, browse_kind)).pack(side="left")
        return row

    def section(self, parent, title):
        box = tk.LabelFrame(parent, text=title)
        box.pack(fill="x", padx=12, pady=8)
        return box

    def build(self):
        header = tk.Frame(self, bg="#0b111a")
        header.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(
            header, text="Faithful Remaster v11.5.2 Beta",
            font="SegoeUI 18 bold", fg="#60a5fa", bg="#0b111a"
        ).pack(side="left")
        tk.Label(
            header, text="Profiles • Multi-Emulator • ComfyUI",
            font="SegoeUI 10", fg="#9ca3af", bg="#0b111a"
        ).pack(side="left", padx=12)

        self.profile_header_var = tk.StringVar(value="No profile selected")
        tk.Label(
            header, textvariable=self.profile_header_var,
            fg="#e5e7eb", bg="#111827", padx=10, pady=5
        ).pack(side="right")

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=8)

        self.tab_profiles = tk.Frame(self.tabs)
        self.tab_workflows = tk.Frame(self.tabs)
        self.tab_processing = tk.Frame(self.tabs)
        self.tab_monitor = tk.Frame(self.tabs)
        self.tab_manager = tk.Frame(self.tabs)
        self.tab_logs = tk.Frame(self.tabs)

        self.tabs.add(self.tab_profiles, text="Profiles")
        self.tabs.add(self.tab_workflows, text="Workflows")
        self.tabs.add(self.tab_processing, text="Processing")
        self.tabs.add(self.tab_monitor, text="Monitor")
        self.tabs.add(self.tab_manager, text="Texture Manager")
        self.tabs.add(self.tab_logs, text="Advanced")

        self.build_profiles_tab()
        self.build_workflows_tab()
        self.build_processing_tab()
        self.build_monitor_tab()
        self.build_manager_tab()
        self.build_logs_tab()

        self.refresh_profile_combo()
        if self.current_profile_name in self.profile_data.get("profiles", {}):
            active_emu = self.profile_data["profiles"][self.current_profile_name].get("emulator", "Generic")
            self.profile_emulator_filter_var.set(active_emu)
            self.refresh_profile_combo()
            self.load_profile(self.current_profile_name, save_current=False)
        else:
            names = self.refresh_profile_combo()
            if names:
                self.load_profile(names[0], save_current=False)
            elif self.profile_data.get("profiles"):
                first = sorted(self.profile_data["profiles"])[0]
                first_emu = self.profile_data["profiles"][first].get("emulator", "Generic")
                self.profile_emulator_filter_var.set(first_emu)
                self.refresh_profile_combo()
                self.load_profile(first, save_current=False)
            else:
                self.create_profile(initial=True)




    def lookup_universal_database(self, game_id, emulator):
        row = db_lookup_game(game_id, emulator)
        if not row:
            row = db_lookup_game(game_id, "")
        if not row:
            return None
        return {
            "game_id": row["original_id"],
            "title": row["title"],
            "region": row.get("region", ""),
            "source": row.get("source", "local universal database")
        }

    def update_universal_game_database(self):
        if getattr(self, "_db_update_running", False):
            self.log("Game database update is already running.")
            return

        self._db_update_running = True
        self.log("Updating universal game database…")
        if hasattr(self, "game_db_status_var"):
            self.game_db_status_var.set("Updating…")

        def worker():
            imported = 0
            downloaded = 0
            errors = []
            try:
                init_game_database()
                for platform, filenames in LIBRETRO_DAT_SOURCES.items():
                    for repo_path in filenames:
                        try:
                            self.log(f"Downloading game database: {repo_path}")
                            text, url = download_libretro_dat(repo_path)
                            downloaded += 1
                            records = parse_libretro_dat(text, platform, repo_path)
                            count = 0
                            for record in records:
                                if db_upsert_game(
                                    record["id"],
                                    record["title"],
                                    record["region"],
                                    record["platform"],
                                    record["source"]
                                ):
                                    count += 1
                            imported += count
                            self.log(f"Imported {count} ID entries from {repo_path}")
                        except Exception as e:
                            errors.append(f"{repo_path}: {e}")
                            self.log(f"Game database source skipped: {repo_path}: {e}")

                # Import manual JSON overrides into SQLite too.
                titles = load_game_titles()
                for gid, value in titles.items():
                    if isinstance(value, str):
                        value = {"title": value}
                    if isinstance(value, dict) and value.get("title"):
                        db_upsert_game(
                            gid,
                            value["title"],
                            value.get("region", ""),
                            value.get("emulator", ""),
                            "game_titles.json"
                        )

                message = f"Database update finished: {imported} entries imported from {downloaded} source files."
                self.log(message)
                self.after(0, lambda: self.game_db_status_var.set(f"{imported:,} entries imported"))
                if errors:
                    self.log(f"{len(errors)} optional source(s) were unavailable.")
            finally:
                self._db_update_running = False

        threading.Thread(target=worker, daemon=True).start()

    def seed_local_game_database(self):
        try:
            titles = load_game_titles()
            for gid, value in titles.items():
                if isinstance(value, str):
                    value = {"title": value}
                if isinstance(value, dict) and value.get("title"):
                    db_upsert_game(
                        gid,
                        value["title"],
                        value.get("region", ""),
                        value.get("emulator", ""),
                        "game_titles.json"
                    )
        except Exception as e:
            self.log(f"Could not seed local game database: {e}")

    def test_game_database_lookup(self):
        tests = [
            ("GPTE41", "Dolphin"),
            ("SLUS-20312", "PCSX2"),
            ("UCUS98737", "PPSSPP"),
        ]
        lines = []
        for gid, platform in tests:
            result = self.lookup_universal_database(gid, platform)
            lines.append(f"{platform}: {gid} -> {result['title'] if result else 'not found'}")
        messagebox.showinfo("Database test", "\n".join(lines))

    def get_game_database_count(self):
        init_game_database()
        con = sqlite3.connect(GAME_DB_PATH)
        try:
            return con.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        finally:
            con.close()


    def lookup_game_title(self, game_id=None, emulator=None, allow_online=True):
        gid = (game_id or self.vars.get("game_id", tk.StringVar()).get()).strip().upper()
        emu = emulator or (self.emulator_var.get() if hasattr(self, "emulator_var") else "Generic")
        if not gid:
            return None

        try:
            universal = self.lookup_universal_database(gid, emu)
            if universal:
                return universal
        except Exception as e:
            self.log(f"Universal database lookup failed for {gid}: {e}")

        titles = load_game_titles()
        cached = titles.get(gid)
        if isinstance(cached, str):
            cached = {"title": cached}
        if isinstance(cached, dict) and cached.get("title"):
            region = cached.get("region", "")
            if not region and emu == "Dolphin":
                region = infer_nintendo_region(gid)
            return {
                "game_id": gid,
                "title": cached["title"],
                "region": region,
                "source": "local database"
            }

        # Online GameTDB lookup is currently used for Dolphin/GameCube/Wii IDs.
        if allow_online and emu == "Dolphin" and re.fullmatch(r"[A-Z0-9]{6}", gid):
            try:
                url = f"https://www.gametdb.com/Wii/{gid}"
                response = requests.get(url, timeout=10, headers={"User-Agent": "Faithful-Remaster/11.2"})
                response.raise_for_status()
                parser = _TitleHTMLParser()
                parser.feed(response.text)
                page_title = "".join(parser.title_text)
                title = clean_gametdb_title(page_title, gid)

                # Reject obvious error/generic titles.
                bad = {"", "gametdb", "game database"}
                if title.lower() not in bad and gid.lower() not in title.lower():
                    region = infer_nintendo_region(gid)
                    titles[gid] = {
                        "title": title,
                        "region": region,
                        "emulator": emu
                    }
                    save_game_titles(titles)
                    db_upsert_game(gid, title, region, emu, "GameTDB")
                    return {
                        "game_id": gid,
                        "title": title,
                        "region": region,
                        "source": "GameTDB"
                    }
            except Exception as e:
                self.log(f"Game title online lookup failed for {gid}: {e}")

        return None

    def format_game_title(self, title, region=""):
        title = (title or "").strip()
        region = (region or "").strip()
        if not title:
            return ""
        if region and region.lower() not in title.lower():
            return f"{title} ({region})"
        return title

    def lookup_and_apply_game_title(self, manual=False):
        game_id = self.vars.get("game_id", tk.StringVar()).get().strip().upper()
        if not game_id:
            if manual:
                messagebox.showinfo("Game title lookup", "No Game ID is available.")
            return False

        result = self.lookup_game_title(game_id=game_id, allow_online=True)
        if not result:
            if manual:
                messagebox.showinfo(
                    "Game title lookup",
                    f"No title was found for {game_id}.\n\n"
                    "You can type the title manually or add it to game_titles.json."
                )
            return False

        display_name = self.format_game_title(result["title"], result.get("region", ""))
        self.vars["game_name"].set(display_name)
        self.vars["game_id"].set(result["game_id"])
        self.log(
            f"Game title resolved: {result['game_id']} → {display_name} "
            f"({result.get('source', 'database')})"
        )
        if hasattr(self, "saved_state_var"):
            self.saved_state_var.set("● Unsaved changes")
        return True


    def detect_game_name_from_dump_folder(self, folder_path):
        """
        Infer a readable game/profile name from the selected dump folder.

        Examples:
        Dolphin:
            .../Dump/Textures/GZLE01          -> GZLE01

        PPSSPP:
            .../PSP/TEXTURES/ULUS10511/new   -> ULUS10511

        Generic:
            .../Some Game/Dump               -> Some Game
        """
        if not folder_path:
            return ""

        p = Path(folder_path)
        emu = self.emulator_var.get() if hasattr(self, "emulator_var") else "Generic"

        # Ignore common technical leaf folders.
        ignored_leaf_names = {
            "new", "dump", "dumps", "texture", "textures",
            "replacement", "replacements", "load", "output", "input"
        }

        candidate = p.name
        if candidate.lower() in ignored_leaf_names and p.parent:
            candidate = p.parent.name

        # PPSSPP normally uses <GAME_ID>/new.
        if emu == "PPSSPP" and p.name.lower() == "new":
            candidate = p.parent.name

        # Dolphin / PCSX2 / DuckStation usually use the game folder itself.
        # Keep the folder name exactly because it may be a game ID.
        candidate = candidate.strip()

        # Convert separators to spaces only when it improves readability.
        readable = candidate.replace("_", " ").strip()

        return readable or candidate

    def apply_detected_game_name(self, folder_path):
        detected = self.detect_game_name_from_dump_folder(folder_path)
        if not detected:
            return

        game_var = self.vars.get("game_name")
        game_id_var = self.vars.get("game_id")

        if game_var is not None:
            current = game_var.get().strip()
            if not current or current in {"New Game", "Generic — New Game"}:
                game_var.set(detected)
                self.log(f"Game name auto-detected: {detected}")

        # If the folder name looks like an emulator game ID, fill Game ID too.
        compact = detected.replace(" ", "")
        looks_like_id = (
            5 <= len(compact) <= 16
            and any(ch.isdigit() for ch in compact)
            and compact.replace("-", "").replace("_", "").isalnum()
        )
        if game_id_var is not None and looks_like_id and not game_id_var.get().strip():
            game_id_var.set(compact.upper())

        # Resolve the readable title from the ID. If lookup fails, retain the folder/ID.
        resolved = False
        if looks_like_id:
            resolved = self.lookup_and_apply_game_title(manual=False)

        if not resolved and game_var is not None:
            current = game_var.get().strip()
            if not current or current in {"New Game", "Generic — New Game"}:
                game_var.set(detected)

        if hasattr(self, "saved_state_var"):
            self.saved_state_var.set("● Unsaved changes")


    # ---------- Profiles ----------
    def build_profiles_tab(self):
        chooser = self.section(self.tab_profiles, "Current profile")

        filter_row = tk.Frame(chooser); filter_row.pack(fill="x", padx=10, pady=(8, 3))
        tk.Label(filter_row, text="Select emulator", width=18, anchor="w").pack(side="left")
        active_record = self.profile_data.get("profiles", {}).get(self.current_profile_name, {})
        initial_emulator = active_record.get("emulator", "Dolphin")
        self.profile_emulator_filter_var = tk.StringVar(value=initial_emulator)
        self.profile_emulator_filter_combo = ttk.Combobox(
            filter_row,
            textvariable=self.profile_emulator_filter_var,
            values=EMULATORS,
            state="readonly"
        )
        self.profile_emulator_filter_combo.pack(side="left", fill="x", expand=True, padx=6)
        self.profile_emulator_filter_combo.bind("<<ComboboxSelected>>", self.on_profile_filter_changed)

        row = tk.Frame(chooser); row.pack(fill="x", padx=10, pady=(3, 8))
        tk.Label(row, text="Profile", width=18, anchor="w").pack(side="left")
        self.profile_var = tk.StringVar()
        self.profile_combo = ttk.Combobox(row, textvariable=self.profile_var, state="readonly")
        self.profile_combo.pack(side="left", fill="x", expand=True, padx=6)
        self.profile_combo.bind("<<ComboboxSelected>>", self.on_profile_selected)

        for text, command in [
            ("New", self.create_profile),
            ("Duplicate", self.duplicate_profile),
            ("Rename", self.rename_profile),
            ("Delete", self.delete_profile),
        ]:
            tk.Button(row, text=text, command=command).pack(side="left", padx=3)

        details = self.section(self.tab_profiles, "Game profile")
        row = tk.Frame(details); row.pack(fill="x", padx=10, pady=6)
        tk.Label(row, text="Emulator", width=28, anchor="w").pack(side="left")
        self.emulator_var = tk.StringVar(value="Generic")
        self.emulator_combo = ttk.Combobox(row, textvariable=self.emulator_var, values=EMULATORS, state="readonly")
        self.emulator_combo.pack(side="left", fill="x", expand=True, padx=6)
        self.emulator_combo.bind("<<ComboboxSelected>>", self.on_profile_emulator_changed)

        self.labeled_entry(details, "Game name", "game_name")
        self.labeled_entry(details, "Game ID (optional)", "game_id")
        self.labeled_entry(details, "Input / Dump folder", "dump_folder", "folder")
        self.labeled_entry(details, "Output / Load folder", "load_folder", "folder")

        actions = tk.Frame(details); actions.pack(fill="x", padx=10, pady=8)
        tk.Button(actions, text="Save Profile", command=self.save_current_profile).pack(side="left")
        tk.Button(
            actions,
            text="Detect Game Name",
            command=lambda: self.apply_detected_game_name(self.vars.get("dump_folder").get().strip())
        ).pack(side="left", padx=5)
        tk.Button(
            actions,
            text="Lookup Full Title",
            command=lambda: self.lookup_and_apply_game_title(manual=True)
        ).pack(side="left", padx=5)
        tk.Button(actions, text="Open Input Folder", command=lambda: self.open_cfg_folder("dump_folder")).pack(side="left", padx=5)
        tk.Button(actions, text="Open Output Folder", command=lambda: self.open_cfg_folder("load_folder")).pack(side="left", padx=5)
        tk.Button(actions, text="Auto-fill Folders", command=self.auto_fill_profile_folders).pack(side="left", padx=5)
        self.saved_state_var = tk.StringVar(value="● Saved")
        tk.Label(actions, textvariable=self.saved_state_var, fg="#86efac").pack(side="right")
        self.auto_sync_azahar_pack_var = tk.BooleanVar(value=bool(self.cfg.get("auto_sync_azahar_pack_json", True)))
        tk.Checkbutton(
            details,
            text="Auto-sync Azahar pack.json from Dump to Load",
            variable=self.auto_sync_azahar_pack_var
        ).pack(anchor="w", padx=38, pady=(0, 8))

        dbbox = self.section(self.tab_profiles, "Universal game title database")
        dbrow = tk.Frame(dbbox); dbrow.pack(fill="x", padx=10, pady=8)
        tk.Button(
            dbrow,
            text="Update Game Database",
            command=self.update_universal_game_database
        ).pack(side="left")
        tk.Button(
            dbrow,
            text="Lookup Current Game",
            command=lambda: self.lookup_and_apply_game_title(manual=True)
        ).pack(side="left", padx=6)
        tk.Button(
            dbrow,
            text="Test Database",
            command=self.test_game_database_lookup
        ).pack(side="left", padx=6)
        count = self.get_game_database_count()
        self.game_db_status_var = tk.StringVar(
            value=f"{count:,} local ID entries" if count else "Database not downloaded yet"
        )
        tk.Label(
            dbrow,
            textvariable=self.game_db_status_var,
            fg="#9ca3af"
        ).pack(side="left", padx=14)
        tk.Label(
            dbbox,
            text="Downloads public metadata only. No ROMs, textures or game files are downloaded.",
            fg="#9ca3af"
        ).pack(anchor="w", padx=10, pady=(0,8))

        scan = self.section(self.tab_profiles, "Discover games from emulator folders")
        scan_top = tk.Frame(scan); scan_top.pack(fill="x", padx=10, pady=5)
        tk.Label(scan_top, text="Emulator", width=18, anchor="w").pack(side="left")
        self.scan_emulator_var = tk.StringVar(value="Dolphin")
        ttk.Combobox(scan_top, textvariable=self.scan_emulator_var, values=EMULATORS, state="readonly", width=22).pack(side="left", padx=6)

        self.scan_input_var = tk.StringVar()
        self.scan_output_var = tk.StringVar()
        for label, var, which in [
            ("Input root", self.scan_input_var, "input"),
            ("Output root", self.scan_output_var, "output")
        ]:
            r = tk.Frame(scan); r.pack(fill="x", padx=10, pady=4)
            tk.Label(r, text=label, width=18, anchor="w").pack(side="left")
            tk.Entry(r, textvariable=var).pack(side="left", fill="x", expand=True, padx=6)
            tk.Button(r, text="Browse", command=lambda v=var: self.browse_scan_root(v)).pack(side="left")

        scan_opts = tk.Frame(scan); scan_opts.pack(fill="x", padx=10, pady=6)
        self.auto_discover_var = tk.BooleanVar(value=bool(self.profile_data.get("auto_discover_games", True)))
        self.auto_add_var = tk.BooleanVar(value=bool(self.profile_data.get("auto_add_discovered_games", False)))
        tk.Checkbutton(scan_opts, text="Auto-discover games", variable=self.auto_discover_var).pack(side="left")
        tk.Checkbutton(scan_opts, text="Automatically add discovered games", variable=self.auto_add_var).pack(side="left", padx=18)
        tk.Button(scan_opts, text="Scan for Games", command=self.scan_for_games).pack(side="right")

        self.scan_emulator_var.trace_add("write", lambda *_: self.load_scan_roots())
        self.load_scan_roots()

    def profile_record_from_ui(self):
        settings = self.collect()
        return {
            "emulator": self.emulator_var.get() or "Generic",
            "game_name": self.vars.get("game_name", tk.StringVar()).get().strip(),
            "game_id": self.vars.get("game_id", tk.StringVar()).get().strip(),
            "settings": {k: settings.get(k) for k in PROFILE_SETTING_KEYS if k in settings}
        }

    def save_current_profile(self, quiet=False):
        if not self.current_profile_name:
            return
        try:
            record = self.profile_record_from_ui()
            profile_dir = PROFILES_DIR / safe_profile_name(self.current_profile_name)
            profile_dir.mkdir(parents=True, exist_ok=True)
            record["profile_dir"] = str(profile_dir)
            self.profile_data.setdefault("profiles", {})[self.current_profile_name] = record
            self.profile_data["active_profile"] = self.current_profile_name
            self.profile_data["auto_discover_games"] = bool(self.auto_discover_var.get())
            self.profile_data["auto_add_discovered_games"] = bool(self.auto_add_var.get())
            self.save_scan_roots()
            save_profiles_data(self.profile_data)
            self.refresh_profile_combo()
            save_config(self.collect())
            self.saved_state_var.set("● Saved")
            if not quiet:
                self.log(f"Profile saved: {self.current_profile_name}")
        except Exception as e:
            if not quiet:
                messagebox.showerror("Profile save failed", str(e))

    def profile_display_label(self, profile_name):
        """Human-friendly profile label while keeping the internal key unchanged."""
        record = self.profile_data.get("profiles", {}).get(profile_name, {})
        game_name = str(record.get("game_name", "") or "").strip()
        game_id = str(record.get("game_id", "") or "").strip()

        if game_name and game_id and game_name.casefold() != game_id.casefold():
            base = f"{game_name} — {game_id}"
        elif game_name:
            base = game_name
        elif game_id:
            base = game_id
        else:
            base = profile_name

        # Show the internal profile name only when it adds useful distinction.
        if profile_name.casefold() not in {base.casefold(), game_name.casefold(), game_id.casefold()}:
            return f"{base}  [{profile_name}]"
        return base

    def rebuild_profile_display_map(self, profile_names):
        """Create a collision-safe mapping from dropdown labels to profile keys."""
        self.profile_display_to_name = {}
        self.profile_name_to_display = {}
        used = set()

        for profile_name in profile_names:
            label = self.profile_display_label(profile_name)
            unique_label = label
            suffix = 2
            while unique_label in used:
                unique_label = f"{label} ({suffix})"
                suffix += 1
            used.add(unique_label)
            self.profile_display_to_name[unique_label] = profile_name
            self.profile_name_to_display[profile_name] = unique_label

        return [self.profile_name_to_display[name] for name in profile_names]

    def filtered_profile_names(self, emulator=None):
        selected = emulator or (self.profile_emulator_filter_var.get() if hasattr(self, "profile_emulator_filter_var") else "")
        profiles = self.profile_data.get("profiles", {})
        return sorted(
            name for name, record in profiles.items()
            if record.get("emulator", "Generic") == selected
        )

    def refresh_profile_combo(self):
        names = self.filtered_profile_names()
        labels = self.rebuild_profile_display_map(names)
        if hasattr(self, "profile_combo"):
            self.profile_combo["values"] = labels
        if self.current_profile_name in names:
            self.profile_var.set(self.profile_name_to_display.get(self.current_profile_name, self.current_profile_name))
        elif hasattr(self, "profile_var"):
            self.profile_var.set("")
        return names

    def on_profile_filter_changed(self, _event=None):
        selected_emulator = self.profile_emulator_filter_var.get()
        if hasattr(self, "scan_emulator_var"):
            self.scan_emulator_var.set(selected_emulator)
        names = self.refresh_profile_combo()
        if self.current_profile_name in names:
            return
        if self.current_profile_name:
            self.save_current_profile(quiet=True)
        if names:
            self.load_profile(names[0], save_current=False)
        else:
            self.current_profile_name = ""
            self.profile_header_var.set(f"{selected_emulator} — No profile selected")
            self.log(f"No {selected_emulator} profiles yet. Click New or Scan for Games.")

    def on_profile_emulator_changed(self, _event=None):
        if self._loading_profile:
            return
        selected = self.emulator_var.get() or "Generic"
        self.profile_emulator_filter_var.set(selected)
        if hasattr(self, "scan_emulator_var"):
            self.scan_emulator_var.set(selected)
        self.auto_fill_profile_folders(only_when_empty=True)
        self.refresh_profile_combo()
        if hasattr(self, "saved_state_var"):
            self.saved_state_var.set("● Unsaved changes")

    def auto_fill_profile_folders(self, only_when_empty=False):
        emulator = self.emulator_var.get() or self.profile_emulator_filter_var.get()
        dump_var = self.vars.get("dump_folder")
        load_var = self.vars.get("load_folder")
        if not dump_var or not load_var:
            return
        dump_text = dump_var.get().strip()
        load_text = load_var.get().strip()

        if dump_text:
            derived = derive_load_folder_for_emulator(emulator, dump_text)
            if derived and (not only_when_empty or not load_text):
                load_var.set(str(derived))
                load_text = str(derived)
        elif not only_when_empty:
            dump_root, load_root = default_emulator_roots(emulator)
            if dump_root:
                dump_var.set(str(dump_root))
            if load_root:
                load_var.set(str(load_root))

        if hasattr(self, "saved_state_var"):
            self.saved_state_var.set("● Unsaved changes")

    def load_profile(self, name, save_current=True):
        if not name or name not in self.profile_data.get("profiles", {}):
            return
        if save_current and self.current_profile_name and self.current_profile_name != name:
            self.save_current_profile(quiet=True)
        if self.worker_thread and self.worker_thread.is_alive():
            self.stop()
            self.log("Watcher stopped before switching profile.")

        self._loading_profile = True
        record = self.profile_data["profiles"][name]
        settings = dict(DEFAULT_CONFIG)
        settings.update(record.get("settings", {}))
        self.cfg = settings
        self.current_profile_name = name
        self.profile_data["active_profile"] = name

        loaded_emulator = record.get("emulator", "Generic")
        self.emulator_var.set(loaded_emulator)
        self.profile_emulator_filter_var.set(loaded_emulator)
        if hasattr(self, "scan_emulator_var"):
            self.scan_emulator_var.set(loaded_emulator)
        self.refresh_profile_combo()
        values = {
            "game_name": record.get("game_name", ""),
            "game_id": record.get("game_id", "")
        }
        values.update(settings)
        for key, var in self.vars.items():
            if key in values:
                var.set(str(values[key]))

        bool_map = {
            "overwrite_var": "overwrite",
            "alpha_var": "preserve_alpha",
            "tmp_var": "process_tmp_image_files",
            "hash_var": "enable_hash_cache",
            "ignore_existing_var": "ignore_existing_silently",
            "priority_var": "prioritize_new_dumps",
            "cutscene_filter_var": "skip_cutscene_buffers",
            "delete_cutscene_var": "delete_skipped_cutscene_buffers",
            "comfy_monitor_var": "enable_comfy_status",
            "pause_comfy_var": "pause_when_comfy_offline",
            "auto_missing_var": "auto_check_missing_load",
            "alpha_workflow_var": "enable_separate_alpha_workflow",
            "alpha_wf_invert_var": "alpha_workflow_invert_output",
            "vram_var": "enable_vram_protection",
            "auto_sync_azahar_pack_var": "auto_sync_azahar_pack_json"
        }
        for attr, key in bool_map.items():
            if hasattr(self, attr):
                getattr(self, attr).set(bool(settings.get(key, DEFAULT_CONFIG.get(key, False))))

        self.profile_var.set(self.profile_name_to_display.get(name, self.profile_display_label(name)))
        self.profile_header_var.set(f'{record.get("emulator","Generic")} — {record.get("game_name") or name}')
        self.saved_state_var.set("● Saved")
        save_profiles_data(self.profile_data)
        self._loading_profile = False
        self.log(f"Profile loaded: {name}")

    def on_profile_selected(self, _event=None):
        selected_label = self.profile_var.get()
        name = getattr(self, "profile_display_to_name", {}).get(selected_label, selected_label)
        if name and name != self.current_profile_name:
            self.load_profile(name)

    def create_profile(self, initial=False):
        name = None
        if initial:
            name = "Generic — New Game"
        else:
            name = self.simple_prompt("New Profile", "Profile name (example: PCSX2 — Final Fantasy X):")
        if not name:
            return
        base = name
        i = 2
        while name in self.profile_data.setdefault("profiles", {}):
            name = f"{base} ({i})"; i += 1
        self.profile_data["profiles"][name] = {
            "emulator": self.profile_emulator_filter_var.get() or "Generic",
            "game_name": "",
            "game_id": "",
            "settings": dict(DEFAULT_CONFIG)
        }
        save_profiles_data(self.profile_data)
        self.refresh_profile_combo()
        self.load_profile(name, save_current=not initial)

    def duplicate_profile(self):
        if not self.current_profile_name:
            return
        new_name = self.simple_prompt("Duplicate Profile", "New profile name:", self.current_profile_name + " Copy")
        if not new_name:
            return
        self.save_current_profile(quiet=True)
        data = json.loads(json.dumps(self.profile_data["profiles"][self.current_profile_name]))
        self.profile_data["profiles"][new_name] = data
        save_profiles_data(self.profile_data)
        self.refresh_profile_combo()
        self.load_profile(new_name, save_current=False)

    def rename_profile(self):
        if not self.current_profile_name:
            return
        new_name = self.simple_prompt("Rename Profile", "New profile name:", self.current_profile_name)
        if not new_name or new_name == self.current_profile_name:
            return
        if new_name in self.profile_data["profiles"]:
            messagebox.showerror("Rename", "A profile with that name already exists.")
            return
        self.save_current_profile(quiet=True)
        self.profile_data["profiles"][new_name] = self.profile_data["profiles"].pop(self.current_profile_name)
        self.current_profile_name = new_name
        self.profile_data["active_profile"] = new_name
        save_profiles_data(self.profile_data)
        self.refresh_profile_combo()
        self.load_profile(new_name, save_current=False)

    def delete_profile(self):
        if not self.current_profile_name:
            return
        if not messagebox.askyesno("Delete Profile", f"Delete profile '{self.current_profile_name}'?\n\nTexture files will not be deleted."):
            return
        self.profile_data["profiles"].pop(self.current_profile_name, None)
        self.current_profile_name = ""
        names = self.filtered_profile_names()
        self.profile_data["active_profile"] = names[0] if names else ""
        save_profiles_data(self.profile_data)
        self.refresh_profile_combo()
        if names:
            self.load_profile(names[0], save_current=False)
        else:
            self.create_profile(initial=True)

    def simple_prompt(self, title, prompt, initial=""):
        win = tk.Toplevel(self)
        win.title(title); win.geometry("480x150"); win.transient(self); win.grab_set()
        tk.Label(win, text=prompt, anchor="w").pack(fill="x", padx=12, pady=(15, 5))
        var = tk.StringVar(value=initial)
        entry = tk.Entry(win, textvariable=var); entry.pack(fill="x", padx=12); entry.focus_set()
        result = {"value": None}
        def ok():
            result["value"] = var.get().strip()
            win.destroy()
        tk.Button(win, text="OK", command=ok).pack(side="right", padx=12, pady=12)
        tk.Button(win, text="Cancel", command=win.destroy).pack(side="right", pady=12)
        win.bind("<Return>", lambda _e: ok())
        self.wait_window(win)
        return result["value"]

    def browse_scan_root(self, var):
        path = filedialog.askdirectory()
        if path:
            var.set(path)

    def load_scan_roots(self):
        if not hasattr(self, "scan_emulator_var"):
            return
        emu = self.scan_emulator_var.get()
        roots = self.profile_data.get("scan_roots", {}).get(emu, {})
        default_input, default_output = default_emulator_roots(emu)
        self.scan_input_var.set(roots.get("input") or (str(default_input) if default_input else ""))
        self.scan_output_var.set(roots.get("output") or (str(default_output) if default_output else ""))

    def save_scan_roots(self):
        if not hasattr(self, "scan_emulator_var"):
            return
        emu = self.scan_emulator_var.get()
        self.profile_data.setdefault("scan_roots", {})[emu] = {
            "input": self.scan_input_var.get().strip(),
            "output": self.scan_output_var.get().strip()
        }

    def scan_for_games(self):
        emu = self.scan_emulator_var.get()
        input_root = Path(self.scan_input_var.get().strip())
        output_root_text = self.scan_output_var.get().strip()
        output_root = Path(output_root_text) if output_root_text else None
        if not input_root.exists():
            messagebox.showerror("Scan", "Input root folder does not exist.")
            return

        self.save_scan_roots()
        found = []
        excluded = {"new", "cache", "textures", "dump", "load", "replacements", "replacement"}
        for child in sorted(input_root.iterdir()):
            if not child.is_dir() or child.name.lower() in excluded:
                continue
            if emu == "PPSSPP":
                dump = child / "new" if (child / "new").exists() else child
                load = child
            else:
                dump = child
                load = (output_root / child.name) if output_root else child
            profile_name = f"{emu} — {child.name}"
            if profile_name not in self.profile_data.get("profiles", {}):
                found.append((profile_name, child.name, dump, load))

        if not found:
            messagebox.showinfo("Scan", "No new game folders were found.")
            return

        if self.auto_add_var.get():
            selected = found
        else:
            selected = self.review_discovered_games(found)

        for profile_name, game_id, dump, load in selected:
            cfg = dict(DEFAULT_CONFIG)
            cfg.update({
                "dump_folder": str(dump),
                "load_folder": str(load),
                "workflow_api_json": self.cfg.get("workflow_api_json", ""),
                "alpha_workflow_api_json": self.cfg.get("alpha_workflow_api_json", ""),
                "comfy_url": self.cfg.get("comfy_url", "http://127.0.0.1:8188"),
                "comfy_start_file": self.cfg.get("comfy_start_file", "")
            })
            self.profile_data["profiles"][profile_name] = {
                "emulator": emu,
                "game_name": game_id,
                "game_id": game_id,
                "settings": cfg
            }

        save_profiles_data(self.profile_data)
        self.profile_emulator_filter_var.set(emu)
        self.refresh_profile_combo()
        if selected:
            self.load_profile(selected[0][0], save_current=True)
            self.log(f"Added {len(selected)} discovered profile(s).")

    def review_discovered_games(self, found):
        win = tk.Toplevel(self)
        win.title("Discovered Games")
        win.geometry("760x480")
        win.transient(self); win.grab_set()
        tk.Label(win, text="Select game folders to add as profiles:", font="SegoeUI 11 bold").pack(anchor="w", padx=12, pady=10)
        lb = tk.Listbox(win, selectmode="extended")
        lb.pack(fill="both", expand=True, padx=12, pady=6)
        for item in found:
            lb.insert("end", f"{item[0]}   |   Input: {item[2]}   |   Output: {item[3]}")
        lb.select_set(0, "end")
        result = {"items": []}
        def add_selected():
            result["items"] = [found[i] for i in lb.curselection()]
            win.destroy()
        tk.Button(win, text="Add Selected Profiles", command=add_selected).pack(side="right", padx=12, pady=10)
        tk.Button(win, text="Cancel", command=win.destroy).pack(side="right", pady=10)
        self.wait_window(win)
        return result["items"]

    # ---------- Workflows ----------
    def build_workflows_tab(self):
        rgb = self.section(self.tab_workflows, "RGB workflow")
        self.labeled_entry(rgb, "RGB workflow API JSON", "workflow_api_json", "json")
        ids = tk.Frame(rgb); ids.pack(fill="x", padx=12, pady=6)
        tk.Label(ids, text="Load Image node ID", width=28, anchor="w").pack(side="left")
        self.vars["load_image_node_id"] = tk.StringVar(value=str(self.cfg.get("load_image_node_id", 1)))
        tk.Entry(ids, textvariable=self.vars["load_image_node_id"], width=10).pack(side="left", padx=6)
        tk.Label(ids, text="Save Image node ID").pack(side="left", padx=(25, 5))
        self.vars["save_image_node_id"] = tk.StringVar(value=str(self.cfg.get("save_image_node_id", 4)))
        tk.Entry(ids, textvariable=self.vars["save_image_node_id"], width=10).pack(side="left", padx=6)
        tk.Button(ids, text="Auto Detect RGB Nodes", command=self.auto_detect_rgb_nodes).pack(side="right")

        alpha = self.section(self.tab_workflows, "Separate alpha workflow")
        self.alpha_workflow_var = tk.BooleanVar(value=bool(self.cfg.get("enable_separate_alpha_workflow", True)))
        tk.Checkbutton(alpha, text="Enable separate alpha workflow", variable=self.alpha_workflow_var).pack(anchor="w", padx=12, pady=5)
        self.labeled_entry(alpha, "Alpha workflow API JSON", "alpha_workflow_api_json", "json")
        aids = tk.Frame(alpha); aids.pack(fill="x", padx=12, pady=6)
        tk.Label(aids, text="Alpha Load node ID", width=28, anchor="w").pack(side="left")
        self.vars["alpha_load_image_node_id"] = tk.StringVar(value=str(self.cfg.get("alpha_load_image_node_id", 1)))
        tk.Entry(aids, textvariable=self.vars["alpha_load_image_node_id"], width=10).pack(side="left", padx=6)
        tk.Label(aids, text="Alpha Save node ID").pack(side="left", padx=(25, 5))
        self.vars["alpha_save_image_node_id"] = tk.StringVar(value=str(self.cfg.get("alpha_save_image_node_id", 5)))
        tk.Entry(aids, textvariable=self.vars["alpha_save_image_node_id"], width=10).pack(side="left", padx=6)
        self.alpha_wf_invert_var = tk.BooleanVar(value=bool(self.cfg.get("alpha_workflow_invert_output", False)))
        tk.Checkbutton(aids, text="Invert output", variable=self.alpha_wf_invert_var).pack(side="left", padx=18)
        tk.Button(aids, text="Auto Detect Alpha Nodes", command=self.auto_detect_alpha_nodes).pack(side="right")

        comfy = self.section(self.tab_workflows, "ComfyUI")
        self.labeled_entry(comfy, "ComfyUI URL", "comfy_url")
        self.labeled_entry(comfy, "ComfyUI start file", "comfy_start_file", "start_file")
        buttons = tk.Frame(comfy); buttons.pack(fill="x", padx=12, pady=8)
        tk.Button(buttons, text="Start ComfyUI", command=self.start_comfy_ui).pack(side="left")
        tk.Button(buttons, text="Check Comfy Now", command=self.check_comfy_now).pack(side="left", padx=6)
        self.comfy_status_label = tk.Label(
            buttons,
            textvariable=self.comfy_status_var,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
            fg="#9ca3af"
        )
        self.comfy_status_label.pack(side="left", padx=(8, 6))
        tk.Label(
            buttons,
            textvariable=self.comfy_detail_var,
            fg="#9ca3af",
            anchor="w"
        ).pack(side="left", fill="x", expand=True)
        tk.Button(buttons, text="Auto Detect All Nodes", command=self.auto_detect_all_nodes).pack(side="right")

    def auto_detect_workflow_nodes(self, workflow_key, load_key, save_key, label):
        path = self.vars.get(workflow_key).get().strip()
        if not path:
            self.log(f"Auto-detect {label}: no workflow selected.")
            return
        info = detect_comfy_nodes_from_api(path)
        if info.get("best_load"):
            self.vars[load_key].set(str(info["best_load"]))
        if info.get("best_save"):
            self.vars[save_key].set(str(info["best_save"]))
        self.log(f"{label} nodes: Load={info.get('best_load') or '?'} Save={info.get('best_save') or '?'}")
        for warning in info.get("warnings", []):
            self.log(f"{label}: {warning}")

    def auto_detect_rgb_nodes(self):
        self.auto_detect_workflow_nodes("workflow_api_json", "load_image_node_id", "save_image_node_id", "RGB workflow")

    def auto_detect_alpha_nodes(self):
        self.auto_detect_workflow_nodes("alpha_workflow_api_json", "alpha_load_image_node_id", "alpha_save_image_node_id", "Alpha workflow")

    def auto_detect_all_nodes(self):
        self.auto_detect_rgb_nodes()
        if self.vars.get("alpha_workflow_api_json").get().strip():
            self.auto_detect_alpha_nodes()

    # ---------- Processing ----------
    def build_processing_tab(self):
        general = self.section(self.tab_processing, "General processing")
        self.overwrite_var = tk.BooleanVar(value=bool(self.cfg.get("overwrite", False)))
        self.alpha_var = tk.BooleanVar(value=bool(self.cfg.get("preserve_alpha", True)))
        self.tmp_var = tk.BooleanVar(value=bool(self.cfg.get("process_tmp_image_files", True)))
        self.hash_var = tk.BooleanVar(value=bool(self.cfg.get("enable_hash_cache", True)))
        self.ignore_existing_var = tk.BooleanVar(value=bool(self.cfg.get("ignore_existing_silently", True)))
        self.priority_var = tk.BooleanVar(value=bool(self.cfg.get("prioritize_new_dumps", True)))
        for text, var in [
            ("Overwrite existing outputs", self.overwrite_var),
            ("Preserve alpha", self.alpha_var),
            ("Process .TMP image files", self.tmp_var),
            ("Enable hash cache", self.hash_var),
            ("Ignore existing outputs silently", self.ignore_existing_var),
            ("Prioritize new dumps", self.priority_var),
        ]:
            tk.Checkbutton(general, text=text, variable=var).pack(anchor="w", padx=12, pady=3)

        cutscene = self.section(self.tab_processing, "Pre-rendered cutscene buffer filter")
        self.cutscene_filter_var = tk.BooleanVar(value=bool(self.cfg.get("skip_cutscene_buffers", True)))
        self.delete_cutscene_var = tk.BooleanVar(value=bool(self.cfg.get("delete_skipped_cutscene_buffers", False)))
        tk.Checkbutton(cutscene, text="Skip pre-rendered cutscene buffers", variable=self.cutscene_filter_var).pack(anchor="w", padx=12, pady=4)
        tk.Checkbutton(cutscene, text="Delete skipped cutscene dumps", variable=self.delete_cutscene_var).pack(anchor="w", padx=12, pady=4)
        tk.Label(cutscene, text="Deletion remains OFF by default because emulators may dump the files again.", fg="#9ca3af").pack(anchor="w", padx=30, pady=(0,6))

        protection = self.section(self.tab_processing, "Status and VRAM protection")
        self.comfy_monitor_var = tk.BooleanVar(value=bool(self.cfg.get("enable_comfy_status", True)))
        self.pause_comfy_var = tk.BooleanVar(value=bool(self.cfg.get("pause_when_comfy_offline", True)))
        self.auto_missing_var = tk.BooleanVar(value=bool(self.cfg.get("auto_check_missing_load", True)))
        self.vram_var = tk.BooleanVar(value=bool(self.cfg.get("enable_vram_protection", True)))
        for text, var in [
            ("Monitor ComfyUI status", self.comfy_monitor_var),
            ("Pause while ComfyUI is offline", self.pause_comfy_var),
            ("Automatically check missing outputs", self.auto_missing_var),
            ("Enable VRAM protection", self.vram_var)
        ]:
            tk.Checkbutton(protection, text=text, variable=var).pack(anchor="w", padx=12, pady=3)

        r = tk.Frame(protection); r.pack(fill="x", padx=12, pady=6)
        tk.Label(r, text="Maximum VRAM usage (GB)", width=28, anchor="w").pack(side="left")
        self.vars["max_vram_gb"] = tk.StringVar(value=str(self.cfg.get("max_vram_gb", 10.0)))
        tk.Entry(r, textvariable=self.vars["max_vram_gb"], width=12).pack(side="left")
        tk.Label(r, text="Resume margin (GB)").pack(side="left", padx=(25, 6))
        self.vars["vram_resume_margin_gb"] = tk.StringVar(value=str(self.cfg.get("vram_resume_margin_gb", 0.5)))
        tk.Entry(r, textvariable=self.vars["vram_resume_margin_gb"], width=12).pack(side="left")

    # ---------- Monitor ----------
    def build_monitor_tab(self):
        controls = self.section(self.tab_monitor, "Watcher controls")
        tk.Button(controls, text="▶ Start Watching", command=self.start, width=18).pack(side="left", padx=10, pady=10)
        tk.Button(controls, text="■ Stop", command=self.stop, width=12).pack(side="left", padx=4, pady=10)
        tk.Button(controls, text="Force Dump Check", command=self.force_dump_check).pack(side="left", padx=4)
        tk.Button(controls, text="Check Missing Load", command=self.check_missing_load_now).pack(side="left", padx=4)
        tk.Button(controls, text="Save Profile", command=self.save_current_profile).pack(side="right", padx=10)

        dash = self.section(self.tab_monitor, "Live dashboard")
        self.dashboard_var = tk.StringVar(value="Status: STOPPED")
        tk.Label(
            dash, textvariable=self.dashboard_var, justify="left", anchor="nw",
            font="Consolas 10", bg="#111827", fg="#e5e7eb", padx=12, pady=12
        ).pack(fill="x", expand=False, padx=8, pady=8)

        preview = self.section(self.tab_monitor, "Current texture preview")
        preview_controls = tk.Frame(preview)
        preview_controls.pack(fill="x", padx=10, pady=(6, 2))
        self.live_preview_var = tk.BooleanVar(value=bool(self.cfg.get("live_texture_preview", True)))
        tk.Checkbutton(
            preview_controls,
            text="Live texture preview",
            variable=self.live_preview_var,
            command=self.on_live_preview_toggled
        ).pack(side="left")
        self.preview_status_var = tk.StringVar(value="Waiting for a texture…")
        tk.Label(preview_controls, textvariable=self.preview_status_var, fg="#9ca3af").pack(side="left", padx=16)

        preview_body = tk.Frame(preview, bg="#0b111a", height=340)
        preview_body.pack(fill="x", expand=False, padx=10, pady=8)
        preview_body.pack_propagate(False)

        left = tk.Frame(preview_body, bg="#0b111a")
        left.pack(side="left", fill="both", expand=True, padx=(0, 5))
        tk.Label(left, text="Original", font="SegoeUI 10 bold").pack(pady=(0, 4))
        self.preview_original_canvas = tk.Canvas(
            left, width=430, height=300, bg="#05070b",
            highlightthickness=1, highlightbackground="#374151"
        )
        self.preview_original_canvas.pack(fill="both", expand=True)
        self.preview_original_canvas.create_text(
            215, 150, text="No preview", fill="#6b7280",
            tags=("placeholder",), font=("Segoe UI", 10)
        )

        right = tk.Frame(preview_body, bg="#0b111a")
        right.pack(side="left", fill="both", expand=True, padx=(5, 0))
        tk.Label(right, text="Enhanced", font="SegoeUI 10 bold").pack(pady=(0, 4))
        self.preview_enhanced_canvas = tk.Canvas(
            right, width=430, height=300, bg="#05070b",
            highlightthickness=1, highlightbackground="#374151"
        )
        self.preview_enhanced_canvas.pack(fill="both", expand=True)
        self.preview_enhanced_canvas.create_text(
            215, 150, text="Waiting for output", fill="#6b7280",
            tags=("placeholder",), font=("Segoe UI", 10)
        )

        logbox = self.section(self.tab_monitor, "Live logs")
        log_controls = tk.Frame(logbox)
        log_controls.pack(fill="x", padx=8, pady=(6, 2))

        self.log_autoscroll_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            log_controls,
            text="Auto-scroll",
            variable=self.log_autoscroll_var
        ).pack(side="left")
        tk.Button(
            log_controls,
            text="Copy Logs",
            command=self.copy_live_logs
        ).pack(side="right", padx=4)
        tk.Button(
            log_controls,
            text="Clear View",
            command=self.clear_live_log_view
        ).pack(side="right", padx=4)
        tk.Button(
            log_controls,
            text="Open Log File",
            command=lambda: os.startfile(str(APP_LOG_PATH))
        ).pack(side="right", padx=4)

        log_frame = tk.Frame(logbox, height=190)
        log_frame.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        log_frame.pack_propagate(False)

        self.log_text = tk.Text(
            log_frame,
            height=10,
            bg="#05070b",
            fg="#d1d5db",
            insertbackground="white",
            wrap="none"
        )
        log_scroll_y = tk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scroll_x = tk.Scrollbar(log_frame, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(
            yscrollcommand=log_scroll_y.set,
            xscrollcommand=log_scroll_x.set
        )
        log_scroll_y.pack(side="right", fill="y")
        log_scroll_x.pack(side="bottom", fill="x")
        self.log_text.pack(side="left", fill="both", expand=True)

    def clear_live_log_view(self):
        if hasattr(self, "log_text"):
            self.log_text.delete("1.0", "end")

    def copy_live_logs(self):
        if not hasattr(self, "log_text"):
            return
        text = self.log_text.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        self.log("Logs copied to clipboard.")

    def on_live_preview_toggled(self):
        self.cfg["live_texture_preview"] = bool(self.live_preview_var.get())
        if not self.live_preview_var.get():
            self.clear_live_preview("Preview disabled")
        else:
            self._preview_last_input = ""
            self._preview_last_output = ""
            self.update_live_preview(force=True)

    def _set_preview_canvas(self, canvas, photo=None, placeholder=""):
        if canvas is None:
            return
        canvas.delete("all")
        width = max(canvas.winfo_width(), 430)
        height = max(canvas.winfo_height(), 300)
        if photo is not None:
            canvas.create_image(width // 2, height // 2, image=photo, anchor="center")
        elif placeholder:
            canvas.create_text(
                width // 2, height // 2, text=placeholder,
                fill="#6b7280", font=("Segoe UI", 10)
            )

    def clear_live_preview(self, status="Waiting for a texture…"):
        self._preview_original_photo = None
        self._preview_enhanced_photo = None
        if hasattr(self, "preview_original_canvas"):
            self._set_preview_canvas(self.preview_original_canvas, placeholder="No preview")
        if hasattr(self, "preview_enhanced_canvas"):
            self._set_preview_canvas(self.preview_enhanced_canvas, placeholder="Waiting for output")
        if hasattr(self, "preview_status_var"):
            self.preview_status_var.set(status)

    def _make_preview_photo(self, path, max_size=(430, 300)):
        if not PIL_AVAILABLE or ImageTk is None:
            return None
        image_path = Path(path)
        if not image_path.is_file():
            return None
        with Image.open(image_path) as image:
            image.seek(0)
            image = image.convert("RGBA")
            image.thumbnail(max_size, Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", max_size, (5, 7, 11, 255))
            canvas.alpha_composite(
                image,
                ((max_size[0] - image.width) // 2, (max_size[1] - image.height) // 2)
            )
            return ImageTk.PhotoImage(canvas)

    def update_live_preview(self, force=False):
        if not hasattr(self, "live_preview_var") or not self.live_preview_var.get():
            return
        input_path = str(self.stats.get("current_input_path", "") or "")
        output_path = str(self.stats.get("current_output_path", "") or "")
        stage = str(self.stats.get("current_texture_stage", "Waiting") or "Waiting")

        if not force and input_path == self._preview_last_input and output_path == self._preview_last_output:
            self.preview_status_var.set(stage)
            return

        self._preview_last_input = input_path
        self._preview_last_output = output_path

        try:
            if input_path:
                photo = self._make_preview_photo(input_path)
                if photo:
                    self._preview_original_photo = photo
                    self._set_preview_canvas(self.preview_original_canvas, photo=photo)
                else:
                    self._set_preview_canvas(self.preview_original_canvas, placeholder="Preview unavailable")
            else:
                self._set_preview_canvas(self.preview_original_canvas, placeholder="No preview")

            if output_path and Path(output_path).is_file():
                photo = self._make_preview_photo(output_path)
                if photo:
                    self._preview_enhanced_photo = photo
                    self._set_preview_canvas(self.preview_enhanced_canvas, photo=photo)
                else:
                    self._set_preview_canvas(self.preview_enhanced_canvas, placeholder="Preview unavailable")
            else:
                self._preview_enhanced_photo = None
                self._set_preview_canvas(self.preview_enhanced_canvas, placeholder="Processing…")

            filename = Path(input_path).name if input_path else ""
            self.preview_status_var.set(stage + (f" — {filename}" if filename else ""))
        except Exception as exc:
            self.preview_status_var.set(f"Preview error: {exc}")

    # ---------- Texture manager ----------
    def build_manager_tab(self):
        box = self.section(self.tab_manager, "Texture Manager")
        tk.Label(
            box,
            text="Open the texture browser for the currently selected game profile.",
            fg="#9ca3af"
        ).pack(anchor="w", padx=12, pady=8)
        tk.Button(box, text="Open Texture Manager", command=self.open_texture_manager, width=24).pack(anchor="w", padx=12, pady=8)
        tk.Button(box, text="Open Input Folder", command=lambda: self.open_cfg_folder("dump_folder")).pack(anchor="w", padx=12, pady=3)
        tk.Button(box, text="Open Output Folder", command=lambda: self.open_cfg_folder("load_folder")).pack(anchor="w", padx=12, pady=3)

    # ---------- Advanced ----------
    def build_logs_tab(self):
        tools = self.section(self.tab_logs, "Advanced tools")
        tk.Button(tools, text="Clear Processed Log", command=self.clear_processed).pack(side="left", padx=8, pady=8)
        tk.Button(tools, text="Clear Current Profile Cache", command=self.clear_cache).pack(side="left", padx=4)
        tk.Button(tools, text="Open Profiles Folder", command=lambda: os.startfile(str(PROFILES_DIR))).pack(side="left", padx=4)
        tk.Button(tools, text="Open Shared Data Folder", command=lambda: os.startfile(str(DATA_DIR))).pack(side="left", padx=4)

        info = self.section(self.tab_logs, "Persistent application data")
        tk.Label(
            info,
            text=(
                "Settings, profiles, title database, cache and logs are shared by all versions.\n"
                f"Location: {DATA_DIR}"
            ),
            justify="left",
            anchor="w",
            fg="#9ca3af"
        ).pack(fill="x", padx=12, pady=10)


    # ---------- Browse / collect / start ----------
    def browse(self, key, kind):
        if kind == "folder":
            path = filedialog.askdirectory()
        elif kind == "start_file":
            path = filedialog.askopenfilename(filetypes=[
                ("Batch files", "*.bat"),
                ("Command files", "*.cmd"),
                ("Executable files", "*.exe"),
                ("All files", "*.*")
            ])
        else:
            path = filedialog.askopenfilename(filetypes=[
                ("JSON files", "*.json"),
                ("All files", "*.*")
            ])
        if path and key in self.vars:
            self.vars[key].set(path)

            if key == "dump_folder":
                self.apply_detected_game_name(path)
                derived = derive_load_folder_for_emulator(self.emulator_var.get(), path)
                if derived and "load_folder" in self.vars:
                    self.vars["load_folder"].set(str(derived))

            if hasattr(self, "saved_state_var"):
                self.saved_state_var.set("● Unsaved changes")

    def collect(self):
        cfg = dict(self.cfg)
        for key, var in self.vars.items():
            cfg[key] = var.get()
        cfg["load_image_node_id"] = int(cfg.get("load_image_node_id", 1))
        cfg["save_image_node_id"] = int(cfg.get("save_image_node_id", 4))
        cfg["alpha_load_image_node_id"] = int(cfg.get("alpha_load_image_node_id", 1))
        cfg["alpha_save_image_node_id"] = int(cfg.get("alpha_save_image_node_id", 5))
        cfg["overwrite"] = bool(self.overwrite_var.get())
        cfg["preserve_alpha"] = bool(self.alpha_var.get())
        cfg["process_tmp_image_files"] = bool(self.tmp_var.get())
        cfg["enable_hash_cache"] = bool(self.hash_var.get())
        cfg["ignore_existing_silently"] = bool(self.ignore_existing_var.get())
        cfg["prioritize_new_dumps"] = bool(self.priority_var.get())
        cfg["skip_cutscene_buffers"] = bool(self.cutscene_filter_var.get())
        cfg["delete_skipped_cutscene_buffers"] = bool(self.delete_cutscene_var.get())
        cfg["enable_comfy_status"] = bool(self.comfy_monitor_var.get())
        cfg["pause_when_comfy_offline"] = bool(self.pause_comfy_var.get())
        cfg["auto_check_missing_load"] = bool(self.auto_missing_var.get())
        cfg["enable_separate_alpha_workflow"] = bool(self.alpha_workflow_var.get())
        cfg["alpha_workflow_invert_output"] = bool(self.alpha_wf_invert_var.get())
        cfg["enable_vram_protection"] = bool(self.vram_var.get())
        cfg["auto_sync_azahar_pack_json"] = bool(self.auto_sync_azahar_pack_var.get())
        cfg["live_texture_preview"] = bool(self.live_preview_var.get()) if hasattr(self, "live_preview_var") else True
        cfg["emulator"] = self.emulator_var.get() or "Generic"
        cfg["max_vram_gb"] = float(cfg.get("max_vram_gb", 10.0))
        cfg["vram_resume_margin_gb"] = float(cfg.get("vram_resume_margin_gb", 0.5))
        cfg["alpha_resize_method"] = "nearest"
        cfg["alpha_source"] = "original"
        cfg["alpha_feather_radius"] = 0.0
        cfg["fix_alpha_edge_bleed"] = False
        return cfg

    def configure_profile_runtime_paths(self, cfg):
        global CACHE_DIR, CACHE_INDEX_PATH, EXCEPTIONS_PATH
        profile_dir = PROFILES_DIR / safe_profile_name(self.current_profile_name)
        profile_dir.mkdir(parents=True, exist_ok=True)
        CACHE_DIR = profile_dir / "_hash_cache"
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_INDEX_PATH = CACHE_DIR / "cache_index.json"
        EXCEPTIONS_PATH = profile_dir / "exceptions.txt"
        if not EXCEPTIONS_PATH.exists():
            EXCEPTIONS_PATH.write_text("", encoding="utf-8")
        cfg["processed_log"] = str(profile_dir / "processed.txt")
        return cfg

    def start(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self.log("Already running.")
            return
        try:
            self.auto_detect_all_nodes()
            self.cfg = self.collect()
            self.validate(self.cfg)
            self.save_current_profile(quiet=True)
            self.cfg = self.configure_profile_runtime_paths(self.cfg)
            if self.emulator_var.get() == "Azahar / Citra" and self.cfg.get("auto_sync_azahar_pack_json", True):
                sync_azahar_pack_json(self.cfg["dump_folder"], self.cfg["load_folder"], self.log)
            save_config(self.cfg)

            self.stop_event.clear()
            self.force_scan_event.clear()
            self.force_missing_event.clear()
            self.stats.update({
                "processed": 0, "cache_hits": 0, "comfy_jobs": 0,
                "queue_len": 0, "high_queue_len": 0, "low_queue_len": 0,
                "peak_vram_mb": 0, "exceptions_skipped": 0,
                "cutscene_buffers_skipped": 0, "cutscene_buffers_deleted": 0,
                "current_input_path": "", "current_output_path": "",
                "current_texture_stage": "Waiting",
                "status": "RUNNING"
            })
            self._preview_last_input = ""
            self._preview_last_output = ""
            worker = Worker(
                self.cfg, self.log_q, self.stop_event,
                self.force_scan_event, self.force_missing_event, self.stats
            )
            self.worker_thread = threading.Thread(target=worker.run, daemon=True)
            self.worker_thread.start()
            self.profile_header_var.set(
                f"{self.emulator_var.get()} — {self.vars.get('game_name').get() or self.current_profile_name}"
            )
            self.log(f"Started watching profile: {self.current_profile_name}")
        except Exception as e:
            messagebox.showerror("Start failed", str(e))

    def save_settings(self):
        self.save_current_profile()

    def open_cfg_folder(self, key):
        path = self.vars.get(key).get().strip() if key in self.vars else ""
        if not path:
            return
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(str(p))

    def clear_processed(self):
        profile_dir = PROFILES_DIR / safe_profile_name(self.current_profile_name)
        path = profile_dir / "processed.txt"
        if path.exists():
            path.unlink()
        self.log("Current profile processed log cleared.")

    def clear_cache(self):
        profile_dir = PROFILES_DIR / safe_profile_name(self.current_profile_name)
        cache_dir = profile_dir / "_hash_cache"
        if messagebox.askyesno("Clear profile cache", f"Delete cached outputs for '{self.current_profile_name}'?"):
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
            self.log("Current profile hash cache cleared.")


if __name__ == "__main__":
    V11App().mainloop()
