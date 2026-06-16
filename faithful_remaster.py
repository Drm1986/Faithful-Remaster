
import json, queue, threading, time, uuid, hashlib, shutil, subprocess
import urllib.request, urllib.parse, os, fnmatch
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog, messagebox
from pathlib import Path

try:
    from PIL import Image
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

APP_DIR = Path(__file__).resolve().parent
TEMP_DIR = APP_DIR / "_temp_outputs"
CACHE_DIR = APP_DIR / "_hash_cache"
TEMP_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

CONFIG_PATH = APP_DIR / "config.json"
CACHE_INDEX_PATH = CACHE_DIR / "cache_index.json"
EXCEPTIONS_PATH = APP_DIR / "exceptions.txt"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga"}

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
    "enable_separate_alpha_workflow": False,
    "alpha_workflow_api_json": "",
    "alpha_load_image_node_id": 1,
    "alpha_save_image_node_id": 4,
    "alpha_workflow_invert_output": False,
    "auto_check_missing_load": True,
    "timeout_seconds": 900,
    "scan_interval_seconds": 2,
    "processed_log": "processed.txt"
}

TERMINAL_STATES = {
    "done", "done_cached", "skip_exists", "cache_hit_restored",
    "ignored_existing", "exception_skipped", "no_output", "stopped"
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
        self.processed_log = APP_DIR / cfg.get("processed_log", "processed.txt")
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
        out_path = output_path_for_input(path, dump_folder, load_folder)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if is_exception_texture(path):
            self.stats["exceptions_skipped"] = self.stats.get("exceptions_skipped", 0) + 1
            return "exception_skipped"

        if self.cfg.get("ignore_existing_silently", True) and out_path.exists() and not self.cfg.get("overwrite", False):
            return "ignored_existing"

        digest = sha1_file(path) if self.cfg.get("enable_hash_cache", True) else None
        if digest and self.restore_from_cache_if_possible(digest, out_path):
            return "cache_hit_restored"
        if out_path.exists() and not self.cfg.get("overwrite", False):
            return "skip_exists"

        self.wait_for_comfy_online()
        self.wait_for_vram_budget()
        if self.stop_event.is_set():
            return "stopped"

        alpha = self.cfg.get("preserve_alpha", True) and has_alpha(path)
        if alpha:
            self.log(f"Alpha detected: {path.name}")

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

        out_path.write_bytes(temp_png.read_bytes())
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
        self.title("Faithful Remaster v10.2 - Auto Detect Nodes")
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
            "status":"STOPPED", "comfy_online": False, "comfy_running": None, "comfy_pending": None, "comfy_error": "", "exceptions_skipped": 0
        }
        self.build()
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
        tk.Label(header, text="Faithful Remaster  v10.1", font="SegoeUI 18 bold", fg="#60a5fa", bg="#0b111a").pack(side="left")
        tk.Label(header, text="  Dolphin → ComfyUI Live Texture Control Center", font="SegoeUI 10", fg="#9ca3af", bg="#0b111a").pack(side="left", padx=10)
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

    def check_comfy_now(self):
        cfg = self.collect()
        st = check_comfy_status(cfg["comfy_url"])
        self.stats["comfy_online"] = st["online"]
        self.stats["comfy_running"] = st.get("queue_running")
        self.stats["comfy_pending"] = st.get("queue_pending")
        self.stats["comfy_error"] = st.get("error", "")
        if st["online"]:
            self.log(f"ComfyUI ONLINE. Running: {st.get('queue_running')}, Pending: {st.get('queue_pending')}")
        else:
            self.log(f"ComfyUI OFFLINE: {st.get('error','unknown error')}")

    def auto_check_comfy(self):
        try:
            cfg = self.collect()
            if cfg.get("enable_comfy_status", True):
                st = check_comfy_status(cfg["comfy_url"])
                self.stats["comfy_online"] = st["online"]
                self.stats["comfy_running"] = st.get("queue_running")
                self.stats["comfy_pending"] = st.get("queue_pending")
                self.stats["comfy_error"] = st.get("error", "")
        except Exception:
            pass
        self.after(5000, self.auto_check_comfy)

    def stop(self):
        self.stop_event.set()

    def clear_processed(self):
        p = APP_DIR / "processed.txt"
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
        while True:
            try:
                msg = self.log_q.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("end", msg + "\n")
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
            f"Exceptions skipped: {self.stats.get('exceptions_skipped',0)}"
        )
        self.after(1000, self.update_dashboard)

if __name__ == "__main__":
    App().mainloop()
