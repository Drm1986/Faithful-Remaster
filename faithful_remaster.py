
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
import urllib.request, urllib.parse, urllib.error, os, fnmatch, mimetypes
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog, messagebox
from pathlib import Path
import re
import struct

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
WORKFLOW_PROFILES_PATH = DATA_DIR / "workflow_profiles.json"
MIGRATION_MARKER = DATA_DIR / ".persistent_data_v1"
APP_LOG_PATH = LOGS_DIR / "faithful_remaster.log"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga"}

def _load_app_version():
    """Return the release version from the bundled VERSION file.

    The VERSION file is the single source of truth used by the GUI, launcher
    package name and validation notes. The fallback keeps local source checkouts
    runnable if the file is accidentally missing.
    """
    try:
        version = (APP_DIR / "VERSION").read_text(encoding="utf-8").strip()
        if version:
            return version
    except Exception:
        pass
    return "11.10.22"

APP_VERSION = _load_app_version()
APP_TITLE = f"Faithful Remaster v{APP_VERSION}"
PROCESSING_PIPELINE_VERSION = "11.10.5-alpha-route-guard-v1"
APP_USER_MODEL_ID = "FaithfulRemaster.TextureClarity"
# One reusable remote Comfy input name per application/thread prevents thousands
# of abandoned UUID uploads while remaining collision-safe across app instances.
_COMFY_UPLOAD_SESSION_ID = f"{os.getpid()}_{uuid.uuid4().hex[:12]}"

def configure_windows_app_identity():
    """Give the GUI a stable Windows taskbar identity instead of Python's."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def apply_native_windows_icon(window, icon_path):
    """Apply real Win32 small/large icons to the Tk frame and window class.

    Tk's iconphoto/iconbitmap can silently fall back to the generic Tcl/Tk or
    Python icon on some Windows/Tk builds. WM_SETICON plus the class icons is
    the reliable path used by the title bar, Alt+Tab and the running taskbar
    button.
    """
    if os.name != "nt":
        return False
    icon_path = Path(icon_path)
    if not icon_path.exists():
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        GCLP_HICON = -14
        GCLP_HICONSM = -34

        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                      wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                      wintypes.UINT]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                        ctypes.c_size_t, ctypes.c_ssize_t]
        user32.SendMessageW.restype = ctypes.c_ssize_t
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND

        window.update_idletasks()
        try:
            hwnd = int(str(window.wm_frame()), 0)
        except Exception:
            hwnd = int(window.winfo_id())
            parent = user32.GetParent(hwnd)
            if parent:
                hwnd = int(parent.value) if hasattr(parent, "value") else int(parent)

        # Load explicit sizes so Windows selects the optimized ICO entries.
        hicon_big = user32.LoadImageW(None, str(icon_path), IMAGE_ICON, 64, 64,
                                      LR_LOADFROMFILE)
        hicon_small = user32.LoadImageW(None, str(icon_path), IMAGE_ICON, 16, 16,
                                        LR_LOADFROMFILE)
        if not hicon_big or not hicon_small:
            return False

        hicon_big_value = int(hicon_big.value) if hasattr(hicon_big, "value") else int(hicon_big)
        hicon_small_value = int(hicon_small.value) if hasattr(hicon_small, "value") else int(hicon_small)
        user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big_value)
        user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small_value)

        # Also set the class icons. This is what some Windows builds use for
        # the title bar/taskbar after a window is re-mapped or restored.
        set_class_long = getattr(user32, "SetClassLongPtrW", None)
        if set_class_long is None:
            set_class_long = user32.SetClassLongW
        set_class_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        set_class_long.restype = ctypes.c_void_p
        set_class_long(hwnd, GCLP_HICON, ctypes.c_void_p(hicon_big_value))
        set_class_long(hwnd, GCLP_HICONSM, ctypes.c_void_p(hicon_small_value))

        # Keep the handles alive for the lifetime of the Tk window.
        window._native_icon_handles = (hicon_big, hicon_small)
        window._native_icon_hwnd = hwnd
        return True
    except Exception:
        return False


configure_windows_app_identity()


class HoverTooltip:
    """Small delayed tooltip for Tk widgets."""
    def __init__(self, widget, text, delay=450):
        self.widget = widget
        self.text = str(text or "").strip()
        self.delay = delay
        self.after_id = None
        self.window = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self.after_id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self.after_id is not None:
            try:
                self.widget.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None

    def _show(self):
        self._cancel()
        if self.window is not None or not self.text:
            return
        try:
            x = self.widget.winfo_pointerx() + 14
            y = self.widget.winfo_pointery() + 18
            self.window = tk.Toplevel(self.widget)
            self.window.wm_overrideredirect(True)
            self.window.wm_geometry(f"+{x}+{y}")
            label = tk.Label(
                self.window, text=self.text, justify="left", wraplength=380,
                bg="#111827", fg="#f3f4f6", relief="solid", bd=1,
                padx=8, pady=6, font=("Segoe UI", 9)
            )
            label.pack()
        except Exception:
            self.window = None

    def _hide(self, _event=None):
        self._cancel()
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None



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
    "invert_alpha_output": False,
    "workflow_api_json": str(APP_DIR / "workflows" / "Faithful_RGB_Workflow_API_Clean_Heart.json"),
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
    "enable_vram_protection": False,
    "max_vram_gb": 10.0,
    "vram_resume_margin_gb": 0.5,
    "ignore_existing_silently": True,
    "prioritize_new_dumps": True,
    "enable_comfy_status": True,
    "pause_when_comfy_offline": True,
    "auto_start_comfy_when_watching": True,
    "fix_alpha_edge_bleed": False,
    "alpha_bleed_iterations": 1,
    "alpha_edge_threshold": 32,
    "enable_separate_alpha_workflow": True,
    "alpha_workflow_api_json": str(APP_DIR / "workflows" / "Faithful_Alpha_Workflow_API.json"),
    "alpha_load_image_node_id": 1,
    "alpha_save_image_node_id": 5,
    "alpha_workflow_invert_output": False,
    "auto_check_missing_load": True,
    "timeout_seconds": 900,
    "scan_interval_seconds": 2,
    "batch_queue_mode": False,
    "processed_log": "processed.txt",
    "skip_cutscene_buffers": True,
    "skip_dynamic_efb_postprocess": True,
    "delete_skipped_cutscene_buffers": False,
    "auto_scan_delete_cutscene_buffers_on_start": False,
    "auto_quarantine_efb_cutscenes": False,
    "auto_quarantine_live_threshold": 12,
    "auto_quarantine_live_idle_seconds": 5.0,
    "live_texture_preview": True,
    "cutscene_min_width": 256,
    "cutscene_min_height": 160,
    "cutscene_grayscale_ratio": 0.985,
    "auto_sync_azahar_pack_json": True,
    "faithfulness_preset": "Clean Heart",
    "comparison_black_background": False,
    "manager_sort_by": "modified_newest",
    "manager_group_by": "none"
}


MANAGER_SORT_OPTIONS = (
    ("modified_newest", "Newest first"),
    ("modified_oldest", "Oldest first"),
    ("name_az", "Name A → Z"),
    ("name_za", "Name Z → A"),
    ("resolution_largest", "Resolution largest"),
    ("resolution_smallest", "Resolution smallest"),
    ("file_largest", "File size largest"),
    ("file_smallest", "File size smallest"),
    ("unprocessed_first", "Unprocessed first"),
    ("processed_first", "Processed first"),
    ("alpha_first", "Alpha first"),
    ("opaque_first", "Opaque first"),
    ("masks_first", "Masks / Gray first"),
    ("color_first", "Color / RGB first"),
    ("mode_override_first", "Mode override first"),
    ("exceptions_first", "Exceptions first"),
)
MANAGER_GROUP_OPTIONS = (
    ("none", "No grouping"),
    ("status", "Processing status"),
    ("type", "Texture type"),
    ("alpha", "Alpha / opacity"),
    ("resolution", "Resolution"),
    ("mode", "Mode override"),
    ("size", "File size class"),
    ("quarantine", "Quarantine reason"),
)
_MANAGER_SORT_LABELS = {code: label for code, label in MANAGER_SORT_OPTIONS}
_MANAGER_SORT_CODES = {label: code for code, label in MANAGER_SORT_OPTIONS}
_MANAGER_GROUP_LABELS = {code: label for code, label in MANAGER_GROUP_OPTIONS}
_MANAGER_GROUP_CODES = {label: code for code, label in MANAGER_GROUP_OPTIONS}

def manager_sort_label(code):
    return _MANAGER_SORT_LABELS.get(str(code or ""), _MANAGER_SORT_LABELS["modified_newest"])

def manager_sort_code(label_or_code):
    text = str(label_or_code or "")
    if text in _MANAGER_SORT_LABELS:
        return text
    return _MANAGER_SORT_CODES.get(text, "modified_newest")

def manager_group_label(code):
    return _MANAGER_GROUP_LABELS.get(str(code or ""), _MANAGER_GROUP_LABELS["none"])

def manager_group_code(label_or_code):
    text = str(label_or_code or "")
    if text in _MANAGER_GROUP_LABELS:
        return text
    return _MANAGER_GROUP_CODES.get(text, "none")

def _manager_size_bucket(size):
    try:
        size = int(size or 0)
    except Exception:
        size = 0
    if size < 64 * 1024:
        return "< 64 KB"
    if size < 256 * 1024:
        return "64–256 KB"
    if size < 1024 * 1024:
        return "256 KB–1 MB"
    if size < 4 * 1024 * 1024:
        return "1–4 MB"
    return "> 4 MB"


AUTO_STARTUP_CLEANUP_EMULATORS = set()
AUTO_BUFFER_QUARANTINE_EMULATORS = {"Dolphin", "PPSSPP"}


def new_profile_settings_for_emulator(emulator):
    """Return safe defaults for a newly-created profile.

    Strict EFB/cutscene quarantine is enabled by default only for Dolphin and
    PPSSPP profiles. Other emulators keep the feature available but opt-in.
    Safe blank cleanup and per-file live deletion remain disabled everywhere.
    """
    settings = dict(DEFAULT_CONFIG)
    settings["auto_scan_delete_cutscene_buffers_on_start"] = False
    settings["delete_skipped_cutscene_buffers"] = False
    settings["auto_quarantine_efb_cutscenes"] = str(emulator or "").strip() in AUTO_BUFFER_QUARANTINE_EMULATORS
    settings["auto_quarantine_live_threshold"] = 12
    settings["auto_quarantine_live_idle_seconds"] = 5.0
    return settings



_DOLPHIN_DUMP_DIMENSIONS_RE = re.compile(r"^tex\d*_(\d+)x(\d+)_", re.IGNORECASE)
_NATIVE_WII_EFB_PYRAMID = {
    (640, 528), (320, 264), (160, 132), (80, 66),
    (640, 264), (320, 132), (160, 66),
}
_NATIVE_PSP_FRAMEBUFFER_PYRAMID = {
    (960, 544), (480, 272), (240, 136), (120, 68),
}


def _is_power_of_two(value):
    value = int(value)
    return value > 0 and (value & (value - 1)) == 0


def _dolphin_dump_dimensions(path):
    match = _DOLPHIN_DUMP_DIMENSIONS_RE.match(Path(path).name)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _quarantine_texture_protection_reason(image, width, height, native_efb_geometry=False):
    """Return a reason when an image looks like static artwork, UI or a mask.

    Quarantine is destructive to the active Dump tree, so this protection layer
    intentionally prefers a false negative over moving a real texture.  It is
    shared by the live Dynamic-EFB skip detector and the manual quarantine scan.
    """
    # Arbitrary small non-native render-target shapes are overwhelmingly UI icons,
    # portraits and effect sprites.  Exact Wii EFB pyramid sizes remain eligible.
    if not native_efb_geometry and (int(width) < 256 or int(height) < 160):
        return f"protected small UI/sprite geometry {width}x{height}"

    # Binary / tiny-palette grayscale images are static masks and overlays far
    # more often than framebuffers.  Alpha may contain many antialiasing levels,
    # so inspect RGB independently from alpha.
    try:
        rgb = image.convert("RGB")
        palette = rgb.getcolors(maxcolors=8)
        if palette is not None and 1 < len(palette) <= 4:
            colors = [color for _count, color in palette]
            if all(max(color) - min(color) <= 3 for color in colors):
                values = sorted({int(round(sum(color) / 3.0)) for color in colors})
                if len(values) <= 4 and (max(values) - min(values) >= 32):
                    return (
                        f"protected grayscale mask/overlay "
                        f"({len(values)} RGB levels: {values})"
                    )
    except Exception:
        pass

    return ""


def _dynamic_efb_visual_signature(image, allow_smooth_scene=False):
    """Return a conservative post-processing signature from a small sample.

    Smooth full-frame color is accepted only for native Wii EFB geometry or
    non-power-of-two screen-shaped targets. Power-of-two square textures are
    rejected before this function to protect masks and ordinary artwork.
    """
    rgba = image.convert("RGBA")
    sample = rgba.copy()
    sample.thumbnail((96, 96), Image.Resampling.BILINEAR)
    width, height = sample.size
    if width <= 1 or height <= 1:
        return False, "sample too small"

    pixels = list(sample.getdata())
    visible = [(r, g, b, a) for r, g, b, a in pixels if a > 8]
    if not visible:
        return True, "empty transparent render target"

    luma = []
    quantized = set()
    neutral = 0
    dark = 0
    bright = 0
    partial_alpha = 0
    for r, g, b, a in visible:
        y = (77 * r + 150 * g + 29 * b) >> 8
        luma.append(y)
        quantized.add((r >> 4, g >> 4, b >> 4))
        if max(r, g, b) - min(r, g, b) <= 7:
            neutral += 1
        if y <= 24:
            dark += 1
        if y >= 210:
            bright += 1
        if 8 < a < 247:
            partial_alpha += 1

    count = max(1, len(visible))
    mean = sum(luma) / count
    variance = sum((value - mean) ** 2 for value in luma) / count
    stddev = variance ** 0.5
    luma_range = max(luma) - min(luma)
    neutral_ratio = neutral / count
    dark_ratio = dark / count
    bright_ratio = bright / count
    partial_alpha_ratio = partial_alpha / count

    px = sample.load()
    neighbor_total = 0.0
    neighbor_count = 0
    for y in range(height):
        for x in range(width):
            r, g, b, a = px[x, y]
            if a <= 8:
                continue
            if x + 1 < width:
                r2, g2, b2, a2 = px[x + 1, y]
                if a2 > 8:
                    neighbor_total += (abs(r - r2) + abs(g - g2) + abs(b - b2)) / 3.0
                    neighbor_count += 1
            if y + 1 < height:
                r2, g2, b2, a2 = px[x, y + 1]
                if a2 > 8:
                    neighbor_total += (abs(r - r2) + abs(g - g2) + abs(b - b2)) / 3.0
                    neighbor_count += 1
    neighbor_delta = neighbor_total / max(1, neighbor_count)

    # Dynamic bloom, blur, luminance and compositing targets are typically
    # smooth, broad-range images, often dark with sparse bright pixels or with
    # meaningful intermediate alpha. These rules deliberately avoid ordinary
    # sharp color textures unless their dimensions are a native EFB signature.
    translucent_effect = partial_alpha_ratio >= 0.025 and len(quantized) >= 12
    smooth_effect = (
        len(quantized) >= 20 and stddev >= 7.0 and luma_range >= 34
        and neighbor_delta <= 13.5
    )
    bloom_effect = (
        dark_ratio >= 0.55 and bright_ratio >= 0.008
        and len(quantized) >= 16 and neighbor_delta <= 18.0
    )
    luminance_effect = neutral_ratio >= 0.955 and stddev >= 5.0
    detected = translucent_effect or bloom_effect or luminance_effect or (allow_smooth_scene and smooth_effect)
    reason = (
        f"colors={len(quantized)}, std={stddev:.1f}, range={luma_range}, "
        f"edge={neighbor_delta:.1f}, alpha={partial_alpha_ratio:.1%}, "
        f"neutral={neutral_ratio:.1%}"
    )
    return detected, reason


def detect_dynamic_efb_postprocess_dump(path, cfg):
    """Detect high-confidence Dolphin/PPSSPP framebuffer post-processing dumps.

    Native Wii and PSP framebuffer pyramids are strong geometry signals, but
    content must still look like bloom/blur/luminance/compositing data. Dolphin
    filenames must declare dimensions that match the image. PPSSPP hashes do not
    reliably carry dimensions, so its native framebuffer sizes are accepted only
    after the same precision protection and visual-signature checks.
    """
    if not PIL_AVAILABLE or not cfg.get("skip_dynamic_efb_postprocess", True):
        return False, "dynamic EFB filter disabled"

    named_size = _dolphin_dump_dimensions(path)
    emulator = str(cfg.get("emulator", "") or "").strip().lower()
    if emulator not in {"dolphin", "ppsspp"} and named_size is None:
        return False, "not a supported framebuffer dump"
    if emulator == "dolphin" and named_size is None:
        return False, "Dolphin dump dimensions missing from filename"

    try:
        with Image.open(path) as image:
            width, height = image.size
            if named_size is not None and named_size != (width, height):
                return False, "filename/image dimensions differ"
            if width < 64 or height < 48 or width > 2048 or height > 2048:
                return False, "outside render-target size range"

            native_wii_geometry = (width, height) in _NATIVE_WII_EFB_PYRAMID
            native_psp_geometry = (width, height) in _NATIVE_PSP_FRAMEBUFFER_PYRAMID
            native_efb_geometry = (
                native_wii_geometry if emulator == "dolphin"
                else native_psp_geometry if emulator == "ppsspp"
                else native_wii_geometry
            )

            protected_reason = _quarantine_texture_protection_reason(
                image, width, height, native_efb_geometry=native_efb_geometry
            )
            if protected_reason:
                return False, protected_reason

            aspect = width / max(1, height)
            screen_shaped = 1.14 <= aspect <= 1.60 or 1.70 <= aspect <= 1.90
            non_power_screen = screen_shaped and (
                not _is_power_of_two(width) or not _is_power_of_two(height)
            )
            if not (native_efb_geometry or non_power_screen):
                return False, f"geometry {width}x{height} is texture-like"

            # Native dimensions are strong evidence, but never sufficient by
            # themselves. This prevents static 640x528 artwork, menus, maps and
            # pre-rendered images from being classified solely by dimensions.
            effect_like, visual_reason = _dynamic_efb_visual_signature(
                image, allow_smooth_scene=(native_efb_geometry or non_power_screen)
            )
            if not effect_like:
                return False, f"no post-process signature ({visual_reason})"

            if native_wii_geometry and emulator == "dolphin":
                geometry = "native Wii EFB-shaped target"
            elif native_psp_geometry and emulator == "ppsspp":
                geometry = "native PSP framebuffer-shaped target"
            else:
                geometry = "screen-shaped target"
            return True, f"dynamic {geometry} {width}x{height}; {visual_reason}"
    except Exception as exc:
        return False, f"dynamic EFB analysis failed: {exc}"

def detect_cutscene_buffer(path, cfg, include_dynamic=True, include_blank=True):
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

    if include_dynamic:
        dynamic_efb, dynamic_reason = detect_dynamic_efb_postprocess_dump(path, cfg)
        if dynamic_efb:
            return True, dynamic_reason
    if not cfg.get("skip_cutscene_buffers", True):
        return False, "cutscene buffer filter disabled"

    try:
        with Image.open(path) as im:
            width, height = im.size
            min_w = int(cfg.get("cutscene_min_width", 256))
            min_h = int(cfg.get("cutscene_min_height", 160))

            # Empty transparent dumps are useful for the separate safe blank
            # cleanup, but the strict EFB + Cutscene quarantine button excludes them.
            if include_blank and (im.mode in ("RGBA", "LA") or "transparency" in im.info):
                alpha = im.convert("RGBA").getchannel("A")
                alpha_min, alpha_max = alpha.getextrema()
                if alpha_max == 0:
                    return True, f"{width}x{height}, fully transparent dump"

            if width < min_w or height < min_h:
                return False, "too small"

            # Large, essentially solid-black dumps remain available to the
            # separate safe blank cleanup, not the strict buffer quarantine action.
            if include_blank:
                black_rgb = im.convert("RGB")
                black_total = width * height
                black_target_samples = 6000
                black_step = max(1, int((black_total / black_target_samples) ** 0.5))
                black_like = 0
                black_sampled = 0
                black_px = black_rgb.load()
                for y in range(0, height, black_step):
                    for x in range(0, width, black_step):
                        r, g, b = black_px[x, y]
                        black_sampled += 1
                        if max(r, g, b) <= 3:
                            black_like += 1
                if black_sampled and black_like / black_sampled >= 0.999:
                    return True, f"{width}x{height}, near-solid black dump"

            native_efb_geometry = (width, height) in _NATIVE_WII_EFB_PYRAMID
            protected_reason = _quarantine_texture_protection_reason(
                im, width, height, native_efb_geometry=native_efb_geometry
            )
            if protected_reason:
                return False, protected_reason

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


def detect_strict_quarantine_cutscene_buffer(path, cfg):
    """Extra-conservative cutscene detector for the one-click quarantine action.

    The normal skip detector is intentionally broad enough to avoid processing
    likely framebuffer dumps. Moving a file out of the active Dump tree needs a
    higher bar: the image must pass the cutscene test, use screen-like dimensions
    where neither axis is a power of two, and (for Dolphin) carry a parseable dump
    size in the filename that matches the real image. This protects grayscale
    effect maps such as 512x384 or 1024x768 from being moved.
    """
    detected, reason = detect_cutscene_buffer(
        path, cfg, include_dynamic=False, include_blank=False
    )
    if not detected:
        return False, reason
    try:
        with Image.open(path) as image:
            width, height = image.size
            native_efb_geometry = (width, height) in _NATIVE_WII_EFB_PYRAMID
            protected_reason = _quarantine_texture_protection_reason(
                image, width, height, native_efb_geometry=native_efb_geometry
            )
        if protected_reason:
            return False, protected_reason
        if _is_power_of_two(width) or _is_power_of_two(height):
            return False, f"texture-like axis in {width}x{height}"
        named_size = _dolphin_dump_dimensions(path)
        emulator = str(cfg.get("emulator", "") or "").strip().lower()
        if emulator == "dolphin":
            if named_size is None:
                return False, "Dolphin dump dimensions missing from filename"
            if named_size != (width, height):
                return False, "filename/image dimensions differ"
        return True, f"strict cutscene buffer; {reason}"
    except Exception as exc:
        return False, f"strict cutscene analysis failed: {exc}"


def classify_strict_buffer_quarantine_candidate(path, cfg):
    """Return (category, reason) for high-confidence EFB/cutscene quarantine.

    This is the single decision path used by manual quarantine, startup scans and
    live bulk quarantine. It deliberately excludes blank cleanup, ordinary effects,
    UI sprites and mask classifications. An empty category means keep the file active.
    """
    scan_cfg = dict(cfg or {})
    scan_cfg["skip_dynamic_efb_postprocess"] = True
    scan_cfg["skip_cutscene_buffers"] = True

    dynamic, dynamic_reason = detect_dynamic_efb_postprocess_dump(path, scan_cfg)
    if dynamic:
        return "dynamic_efb", dynamic_reason
    if str(dynamic_reason).startswith("protected "):
        return "", dynamic_reason

    cutscene, cutscene_reason = detect_strict_quarantine_cutscene_buffer(path, scan_cfg)
    if cutscene:
        return "cutscene", cutscene_reason
    return "", cutscene_reason


def detect_safe_blank_dump(path, cfg):
    """Detect only provably empty dumps suitable for cleanup/quarantine.

    This intentionally excludes grayscale cutscene inference and all dynamic EFB
    detection. It accepts only fully transparent images or sufficiently large
    images where at least 99.9% of sampled pixels are essentially black.
    """
    if not PIL_AVAILABLE:
        return False, "Pillow unavailable"
    try:
        with Image.open(path) as im:
            width, height = im.size
            if im.mode in ("RGBA", "LA") or "transparency" in im.info:
                alpha = im.convert("RGBA").getchannel("A")
                _alpha_min, alpha_max = alpha.getextrema()
                if alpha_max == 0:
                    return True, f"{width}x{height}, fully transparent dump"

            min_w = int(cfg.get("cutscene_min_width", 256))
            min_h = int(cfg.get("cutscene_min_height", 160))
            if width < min_w or height < min_h:
                return False, "too small for safe blank cleanup"

            rgb = im.convert("RGB")
            total = width * height
            step = max(1, int((total / 6000) ** 0.5))
            black_like = sampled = 0
            px = rgb.load()
            for y in range(0, height, step):
                for x in range(0, width, step):
                    sampled += 1
                    if max(px[x, y]) <= 3:
                        black_like += 1
            ratio = black_like / max(1, sampled)
            if ratio >= 0.999:
                return True, f"{width}x{height}, near-solid black dump ({ratio:.2%})"
            return False, f"not safely blank ({ratio:.2%} black)"
    except Exception as exc:
        return False, f"safe blank analysis failed: {exc}"


QUARANTINE_MANIFEST_NAME = "_manifest.json"
QUARANTINE_ROOT_NAMES = ("_buffer_quarantine", "_cleanup_quarantine")
_QUARANTINE_MANIFEST_LOCK = threading.RLock()


def _load_quarantine_manifest(session_dir):
    manifest_path = Path(session_dir) / QUARANTINE_MANIFEST_NAME
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            return data
    except Exception:
        pass
    return {"version": 1, "entries": []}


def _save_quarantine_manifest(session_dir, data):
    session_dir = Path(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = session_dir / QUARANTINE_MANIFEST_NAME
    temp = manifest_path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(manifest_path)


def _record_quarantine_entry(session_dir, destination, original_path, dump_folder, category, reason):
    session_dir = Path(session_dir)
    destination = Path(destination)
    original_path = Path(original_path)
    dump_folder = Path(dump_folder)
    try:
        destination_relative = destination.relative_to(session_dir)
    except Exception:
        destination_relative = Path(destination.name)
    try:
        original_relative = original_path.relative_to(dump_folder)
    except Exception:
        original_relative = Path(original_path.name)
    entry = {
        "destination_relative": destination_relative.as_posix(),
        "original_relative": original_relative.as_posix(),
        "original_dump_folder": str(dump_folder),
        "original_path": str(original_path),
        "category": str(category or "quarantined"),
        "reason": str(reason or ""),
        "quarantined_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with _QUARANTINE_MANIFEST_LOCK:
        data = _load_quarantine_manifest(session_dir)
        data.setdefault("entries", []).append(entry)
        _save_quarantine_manifest(session_dir, data)


def quarantine_dump(path, dump_folder, quarantine_session, category="quarantined", reason=""):
    """Move a dump out of the active dump tree while preserving its relative path.

    Cleanup is reversible: no file is permanently deleted by the application.
    A per-session manifest stores the exact original path, category and detector
    reason so Texture Manager can preview and restore the file later.
    """
    path = Path(path)
    dump_folder = Path(dump_folder)
    quarantine_session = Path(quarantine_session)
    original_path = path
    try:
        relative = path.relative_to(dump_folder)
    except Exception:
        relative = Path(path.name)
    destination = quarantine_session / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        stem, suffix = destination.stem, destination.suffix
        index = 2
        while destination.exists():
            destination = destination.with_name(f"{stem}_{index}{suffix}")
            index += 1
    shutil.move(str(path), str(destination))
    try:
        _record_quarantine_entry(
            quarantine_session, destination, original_path, dump_folder, category, reason
        )
    except Exception:
        # The moved texture remains safe even if metadata could not be written.
        pass
    return destination


def quarantine_dumps_bulk(candidates, dump_folder, quarantine_session, manifest_flush_every=250):
    """Move many dumps with batched manifest writes.

    candidates is an iterable of (path, category, reason). Relative paths and exact
    restore metadata are preserved. Manifest state is flushed in chunks so thousands
    of framebuffer dumps do not repeatedly parse and rewrite the same JSON file.
    Returns (moved, failures), where moved contains (source, destination, category, reason).
    """
    dump_folder = Path(dump_folder)
    quarantine_session = Path(quarantine_session)
    quarantine_session.mkdir(parents=True, exist_ok=True)
    flush_every = max(1, int(manifest_flush_every or 250))
    moved = []
    failures = []

    with _QUARANTINE_MANIFEST_LOCK:
        data = _load_quarantine_manifest(quarantine_session)
        entries = data.setdefault("entries", [])
        pending_since_flush = 0

        for item in candidates:
            try:
                path, category, reason = item
                path = Path(path)
                if not path.is_file():
                    continue
                original_path = path
                try:
                    relative = path.resolve().relative_to(dump_folder.resolve())
                except Exception:
                    relative = Path(path.name)
                destination = quarantine_session / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    stem, suffix = destination.stem, destination.suffix
                    index = 2
                    while destination.exists():
                        destination = destination.with_name(f"{stem}_{index}{suffix}")
                        index += 1

                shutil.move(str(path), str(destination))
                try:
                    destination_relative = destination.relative_to(quarantine_session)
                except Exception:
                    destination_relative = Path(destination.name)
                try:
                    original_relative = original_path.relative_to(dump_folder)
                except Exception:
                    original_relative = Path(original_path.name)
                entries.append({
                    "destination_relative": destination_relative.as_posix(),
                    "original_relative": original_relative.as_posix(),
                    "original_dump_folder": str(dump_folder),
                    "original_path": str(original_path),
                    "category": str(category or "quarantined"),
                    "reason": str(reason or ""),
                    "quarantined_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                moved.append((original_path, destination, str(category or "quarantined"), str(reason or "")))
                pending_since_flush += 1
                if pending_since_flush >= flush_every:
                    _save_quarantine_manifest(quarantine_session, data)
                    pending_since_flush = 0
            except Exception as exc:
                failures.append((Path(item[0]) if item else Path("unknown"), str(exc)))

        if pending_since_flush or not (quarantine_session / QUARANTINE_MANIFEST_NAME).exists():
            _save_quarantine_manifest(quarantine_session, data)

    return moved, failures


def quarantine_metadata_for_path(path, profile_dir, current_dump_folder=None):
    """Return restore metadata for a quarantined image, including legacy sessions."""
    path = Path(path)
    profile_dir = Path(profile_dir)
    for root_name in QUARANTINE_ROOT_NAMES:
        root = profile_dir / root_name
        try:
            relative_to_root = path.relative_to(root)
        except Exception:
            continue
        if len(relative_to_root.parts) < 2:
            return None
        session_dir = root / relative_to_root.parts[0]
        destination_relative = Path(*relative_to_root.parts[1:])
        entry = None
        with _QUARANTINE_MANIFEST_LOCK:
            data = _load_quarantine_manifest(session_dir)
        wanted = destination_relative.as_posix().casefold()
        for candidate in data.get("entries", []):
            if str(candidate.get("destination_relative", "")).replace("\\", "/").casefold() == wanted:
                entry = dict(candidate)
                break
        if entry is None:
            # v11.9.5 quarantine sessions did not have a manifest. Their folder
            # layout already preserved the path relative to the dump root.
            entry = {
                "destination_relative": destination_relative.as_posix(),
                "original_relative": destination_relative.as_posix(),
                "original_dump_folder": str(current_dump_folder or ""),
                "original_path": "",
                "category": "blank" if root_name == "_cleanup_quarantine" else "buffer",
                "reason": "Legacy quarantine entry",
                "quarantined_at": "",
            }
        entry["path"] = path
        entry["session_dir"] = session_dir
        entry["quarantine_root"] = root
        entry["root_name"] = root_name
        return entry
    return None


def iter_quarantined_images(profile_dir, current_dump_folder=None, process_tmp=True):
    """List quarantined images without mixing them into the active dump list."""
    profile_dir = Path(profile_dir)
    rows = []
    for root_name in QUARANTINE_ROOT_NAMES:
        root = profile_dir / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.name == QUARANTINE_MANIFEST_NAME:
                continue
            if not is_image_like(path, process_tmp):
                continue
            metadata = quarantine_metadata_for_path(path, profile_dir, current_dump_folder)
            if metadata is None:
                continue
            try:
                stamp = path.stat().st_mtime
            except Exception:
                stamp = 0
            rows.append((stamp, path, metadata))
    rows.sort(key=lambda item: item[0], reverse=True)
    return rows


def _remove_quarantine_manifest_entry(metadata):
    session_dir = Path(metadata.get("session_dir", ""))
    destination_relative = str(metadata.get("destination_relative", "")).replace("\\", "/").casefold()
    if not session_dir:
        return
    with _QUARANTINE_MANIFEST_LOCK:
        data = _load_quarantine_manifest(session_dir)
        before = len(data.get("entries", []))
        data["entries"] = [
            entry for entry in data.get("entries", [])
            if str(entry.get("destination_relative", "")).replace("\\", "/").casefold() != destination_relative
        ]
        if len(data["entries"]) != before:
            _save_quarantine_manifest(session_dir, data)


def _remove_empty_quarantine_dirs(start_dir, stop_dir):
    current = Path(start_dir)
    stop_dir = Path(stop_dir)
    while current != stop_dir and is_path_within(current, stop_dir):
        try:
            if any(current.iterdir()):
                break
            current.rmdir()
        except Exception:
            break
        current = current.parent
    # Remove an empty manifest and then the empty session itself.
    try:
        manifest = stop_dir / QUARANTINE_MANIFEST_NAME
        data = _load_quarantine_manifest(stop_dir)
        if manifest.exists() and not data.get("entries"):
            manifest.unlink()
        if stop_dir.exists() and not any(stop_dir.iterdir()):
            stop_dir.rmdir()
    except Exception:
        pass


def restore_quarantined_dump(path, profile_dir, current_dump_folder, overwrite=False):
    """Restore one quarantined image to its exact active dump-relative path."""
    path = Path(path)
    current_dump_folder = Path(current_dump_folder)
    metadata = quarantine_metadata_for_path(path, profile_dir, current_dump_folder)
    if metadata is None:
        raise ValueError("The selected file is not inside this profile's quarantine.")
    original_relative = Path(str(metadata.get("original_relative") or path.name))
    if original_relative.is_absolute() or ".." in original_relative.parts:
        raise ValueError("Unsafe original path in quarantine metadata.")
    target = current_dump_folder / original_relative
    if not is_path_within(target, current_dump_folder):
        raise ValueError("Restore target is outside the configured Dump folder.")
    if target.exists() and not overwrite:
        return "conflict", target
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    shutil.move(str(path), str(target))
    try:
        _remove_quarantine_manifest_entry(metadata)
        _remove_empty_quarantine_dirs(path.parent, Path(metadata.get("session_dir")))
    except Exception:
        pass
    return "restored", target


TERMINAL_STATES = {
    "done", "done_cached", "skip_exists", "cache_hit_restored",
    "ignored_existing", "exception_skipped", "cutscene_buffer_skipped", "dynamic_efb_skipped"
}

def load_config():
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            out = dict(DEFAULT_CONFIG); out.update(cfg); return out
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def _atomic_write_text(path, text, encoding="utf-8"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    temp.write_text(text, encoding=encoding)
    os.replace(temp, path)


def save_config(cfg):
    _atomic_write_text(CONFIG_PATH, json.dumps(cfg, indent=2), encoding="utf-8")

def load_cache_index():
    if CACHE_INDEX_PATH.exists():
        try:
            return json.loads(CACHE_INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_cache_index(index):
    _atomic_write_text(CACHE_INDEX_PATH, json.dumps(index, indent=2), encoding="utf-8")

def sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def _hidden_subprocess_kwargs():
    """Return Windows flags that keep short-lived helper commands invisible.

    Faithful Remaster is launched as a GUI application without a parent console.
    Without these flags Windows creates a transient console window every time a
    command-line helper such as nvidia-smi is polled.
    """
    if os.name != "nt":
        return {}
    kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo
    except Exception:
        pass
    return kwargs

def get_vram_info():
    try:
        kwargs = _hidden_subprocess_kwargs()
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=3,
            **kwargs,
        )
        used, total = [int(x.strip()) for x in out.strip().splitlines()[0].split(",")]
        return used, total
    except Exception:
        return None, None

def load_json(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


class WorkflowValidationError(ValueError):
    pass


class ComfyAPIError(RuntimeError):
    pass


def _workflow_connection_source(value, node_ids):
    if isinstance(value, list) and len(value) == 2:
        source = str(value[0])
        if source in node_ids:
            return source
    return None


def validate_comfy_api_workflow(path, load_node_id=None, save_node_id=None, require_reachable=True):
    """Validate the exact LoadImage→SaveImage route used by the worker.

    Comfy API node IDs are strings and may contain non-numeric characters, so
    validation never coerces them to integers. The selected LoadImage must be an
    upstream dependency of the selected SaveImage; this catches a common failure
    where valid node IDs belong to different branches of the same workflow.
    """
    workflow_path = Path(path)
    if not workflow_path.is_file():
        raise WorkflowValidationError(f"Workflow API file does not exist: {workflow_path}")
    try:
        workflow = load_json(workflow_path)
    except Exception as exc:
        raise WorkflowValidationError(f"Could not read workflow API JSON: {exc}") from exc
    if not isinstance(workflow, dict) or not workflow:
        raise WorkflowValidationError("Workflow API JSON must be a non-empty object.")

    nodes = {str(node_id): node for node_id, node in workflow.items() if isinstance(node, dict)}
    if not nodes:
        raise WorkflowValidationError("Workflow API JSON does not contain any nodes.")

    load_nodes = [node_id for node_id, node in nodes.items() if str(node.get("class_type")) == "LoadImage"]
    save_nodes = [node_id for node_id, node in nodes.items() if str(node.get("class_type")) == "SaveImage"]
    load_id = str(load_node_id).strip() if load_node_id not in (None, "") else (load_nodes[0] if len(load_nodes) == 1 else "")
    save_id = str(save_node_id).strip() if save_node_id not in (None, "") else (save_nodes[0] if len(save_nodes) == 1 else "")

    if not load_id:
        raise WorkflowValidationError(
            "The workflow has no unambiguous LoadImage node. Select the exact LoadImage node ID."
        )
    if not save_id:
        raise WorkflowValidationError(
            "The workflow has no unambiguous SaveImage node. Select the exact SaveImage node ID."
        )
    if load_id not in nodes:
        raise WorkflowValidationError(f"LoadImage node {load_id!r} does not exist in the API workflow.")
    if save_id not in nodes:
        raise WorkflowValidationError(f"SaveImage node {save_id!r} does not exist in the API workflow.")
    if str(nodes[load_id].get("class_type")) != "LoadImage":
        raise WorkflowValidationError(
            f"Selected load node {load_id!r} is {nodes[load_id].get('class_type')!r}, not LoadImage."
        )
    if str(nodes[save_id].get("class_type")) != "SaveImage":
        raise WorkflowValidationError(
            f"Selected save node {save_id!r} is {nodes[save_id].get('class_type')!r}, not SaveImage."
        )
    load_inputs = nodes[load_id].get("inputs")
    save_inputs = nodes[save_id].get("inputs")
    if not isinstance(load_inputs, dict) or "image" not in load_inputs:
        raise WorkflowValidationError(f"LoadImage node {load_id!r} has no editable 'image' input.")
    if not isinstance(save_inputs, dict) or "images" not in save_inputs:
        raise WorkflowValidationError(f"SaveImage node {save_id!r} has no connected 'images' input.")

    broken_links = []
    for node_id, node in nodes.items():
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        for input_name, value in inputs.items():
            # Comfy API graph links are serialized as ["node-id", output-index].
            # Do not mistake ordinary numeric two-item widget arrays (for example
            # [width, height]) for broken links.
            if (isinstance(value, list) and len(value) == 2
                    and isinstance(value[0], str)
                    and isinstance(value[1], int) and not isinstance(value[1], bool)):
                source = str(value[0])
                if source not in nodes:
                    broken_links.append(f"{node_id}.{input_name} -> missing node {source}")
    if broken_links:
        raise WorkflowValidationError("Workflow contains broken links: " + "; ".join(broken_links[:8]))

    reachable = set()
    stack = [save_id]
    while stack:
        current = stack.pop()
        if current in reachable:
            continue
        reachable.add(current)
        inputs = nodes.get(current, {}).get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        for value in inputs.values():
            source = _workflow_connection_source(value, set(nodes))
            if source and source not in reachable:
                stack.append(source)
    if require_reachable and load_id not in reachable:
        raise WorkflowValidationError(
            f"LoadImage node {load_id!r} is not connected upstream to SaveImage node {save_id!r}."
        )

    return {
        "path": workflow_path,
        "workflow": workflow,
        "nodes": nodes,
        "load_node": load_id,
        "save_node": save_id,
        "load_nodes": load_nodes,
        "save_nodes": save_nodes,
        "reachable_nodes": reachable,
        "class_types": sorted({str(node.get("class_type") or "") for node in nodes.values()}),
    }


_ALPHA_FORBIDDEN_BUNDLED_API_FILENAMES = {
    "faithful_n64_strip_safe_api.json",
    "faithful_rgb_workflow_api.json",
    "faithful_rgb_workflow_api_clean_heart.json",
    "faithful_rgb_workflow_api_strong_believer.json",
    "faithful_rgb_workflow_api_midway.json",
    "faithful_rgb_workflow_api_soft_heart.json",
}


def validate_alpha_comfy_api_workflow(path, load_node_id=None, save_node_id=None, require_reachable=True):
    """Validate that a selected Alpha API is actually an alpha/mask workflow.

    A normal RGB or N64 strip workflow can still contain a valid connected
    LoadImage -> SaveImage route, so generic graph validation alone is not
    enough. The final SaveImage branch must either consume the LoadImage MASK
    output (port 1) or contain a reachable mask/alpha processing node.
    """
    result = validate_comfy_api_workflow(
        path, load_node_id, save_node_id, require_reachable=require_reachable
    )
    workflow_path = Path(result["path"])
    if workflow_path.name.casefold() in _ALPHA_FORBIDDEN_BUNDLED_API_FILENAMES:
        raise WorkflowValidationError(
            f"{workflow_path.name} is an RGB/N64 workflow, not an Alpha workflow. "
            "Select Faithful_Alpha_Workflow_API.json."
        )

    nodes = result["nodes"]
    reachable = set(result["reachable_nodes"])
    load_id = str(result["load_node"])
    mask_nodes = []
    mask_output_consumers = []
    for node_id in reachable:
        node = nodes.get(node_id, {})
        class_type = str(node.get("class_type") or "")
        class_key = class_type.casefold()
        if "mask" in class_key or "alpha" in class_key:
            mask_nodes.append(node_id)
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        for input_name, value in inputs.items():
            if (isinstance(value, list) and len(value) == 2
                    and str(value[0]) == load_id and value[1] == 1):
                mask_output_consumers.append(f"{node_id}.{input_name}")

    if not mask_nodes and not mask_output_consumers:
        raise WorkflowValidationError(
            "The selected Alpha API does not use the source alpha/mask and has no "
            "mask-processing node on the final SaveImage branch. It looks like an "
            "RGB or strip-upscale workflow. Select Faithful_Alpha_Workflow_API.json."
        )
    result["alpha_mask_nodes"] = sorted(mask_nodes)
    result["alpha_mask_output_consumers"] = sorted(mask_output_consumers)
    return result


def validate_comfy_ui_workflow(path):
    """Validate an optional ComfyUI editor-format workflow file."""
    text = str(path or "").strip()
    if not text:
        return None
    workflow_path = Path(text)
    if not workflow_path.is_file():
        raise WorkflowValidationError(f"Workflow UI file does not exist: {workflow_path}")
    try:
        data = load_json(workflow_path)
    except Exception as exc:
        raise WorkflowValidationError(f"Could not read workflow UI JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkflowValidationError("Workflow UI JSON must contain an object.")
    nodes = data.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise WorkflowValidationError(
            "Workflow UI JSON has no editor node list. This may be an API workflow saved in the UI field."
        )
    return {"path": workflow_path, "node_count": len(nodes), "link_count": len(data.get("links", [])) if isinstance(data.get("links"), list) else 0}

def workflow_file_fingerprint(path):
    path = Path(path)
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return hashlib.sha256(str(path).encode("utf-8", errors="replace")).hexdigest()


def _minor_workflow_size_mismatch(size, expected_size):
    """Return True for tiny ComfyUI multiple-of-8 rounding drift.

    Diffusion/latent workflows often round odd source dimensions internally.
    Example: a 147x52 texture at 4x may return 584x208 instead of the exact
    emulator-required 588x208.  That should be normalized, while a clearly
    wrong SaveImage branch should still fail loudly.
    """
    try:
        w, h = int(size[0]), int(size[1])
        ew, eh = int(expected_size[0]), int(expected_size[1])
    except Exception:
        return False
    if ew <= 0 or eh <= 0 or w <= 0 or h <= 0:
        return False
    dw, dh = abs(w - ew), abs(h - eh)
    if dw == 0 and dh == 0:
        return True
    tolerance_w = min(32, max(8, int(round(ew * 0.02))))
    tolerance_h = min(32, max(8, int(round(eh * 0.02))))
    return dw <= tolerance_w and dh <= tolerance_h


def normalize_image_size_if_close(path, expected_size, label="processed output"):
    """Resize a near-miss output to the exact emulator texture size.

    This is intentionally conservative.  It only fixes tiny dimension drift
    caused by latent/workflow rounding, and refuses large mismatches that likely
    mean the workflow selected the wrong branch or scale.
    """
    if expected_size is None or not PIL_AVAILABLE:
        return None
    path = Path(path)
    with Image.open(path) as image:
        image.load()
        size = tuple(image.size)
        expected = (int(expected_size[0]), int(expected_size[1]))
        if size == expected:
            return size
        if not _minor_workflow_size_mismatch(size, expected):
            return size
        fmt = image.format or ("PNG" if path.suffix.lower() == ".png" else None)
        mode = image.mode
        if mode in ("1", "P"):
            work = image.convert("RGBA")
        else:
            work = image.copy()
        resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
        fixed = work.resize(expected, resample)
        save_kwargs = {}
        suffix = path.suffix.lower()
        if suffix in (".jpg", ".jpeg"):
            fmt = "JPEG"
            if fixed.mode in ("RGBA", "LA"):
                fixed = fixed.convert("RGB")
            save_kwargs.update({"quality": 95, "subsampling": 0})
        elif suffix == ".tga":
            fmt = "TGA"
        elif suffix == ".webp":
            fmt = "WEBP"
            save_kwargs.update({"lossless": True})
        elif suffix == ".bmp":
            fmt = "BMP"
        else:
            fmt = "PNG"
        temp = path.with_name(path.name + f".{uuid.uuid4().hex}.resize.tmp")
        try:
            fixed.save(temp, format=fmt, **save_kwargs)
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
        return expected


def validate_image_file(path, expected_size=None, label="processed output"):
    path = Path(path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"{label.capitalize()} was not created or is empty: {path}")
    if not PIL_AVAILABLE:
        return None
    try:
        with Image.open(path) as image:
            image.load()
            size = tuple(image.size)
    except Exception as exc:
        raise RuntimeError(f"{label.capitalize()} is not a valid image: {exc}") from exc
    if expected_size is not None and tuple(expected_size) != size:
        normalized_size = normalize_image_size_if_close(path, expected_size, label=label)
        if normalized_size == tuple(expected_size):
            return normalized_size
        raise RuntimeError(
            f"{label.capitalize()} has the wrong size: got {size[0]}x{size[1]}, "
            f"expected {expected_size[0]}x{expected_size[1]}. Check the workflow output scale and SaveImage node."
        )
    return size


def atomic_copy_file(source, destination):
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + f".{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temp)
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def atomic_save_processed_image(source, destination):
    """Atomically save an image using the destination's real file format.

    ComfyUI always returns PNG. Copying those bytes directly to a .jpg/.tga/.webp
    destination creates a misleading extension and can break emulator loaders.
    """
    source = Path(source)
    destination = Path(destination)
    suffix = destination.suffix.lower()
    if suffix == ".png":
        atomic_copy_file(source, destination)
        return
    if not PIL_AVAILABLE:
        raise RuntimeError(f"Pillow is required to save processed {suffix or 'image'} files")
    format_map = {
        ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP",
        ".bmp": "BMP", ".tga": "TGA",
    }
    image_format = format_map.get(suffix)
    if not image_format:
        raise RuntimeError(f"Unsupported output image extension: {suffix}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + f".{uuid.uuid4().hex}.tmp")
    try:
        with Image.open(source) as image:
            output = image.convert("RGB") if image_format == "JPEG" else image.convert("RGBA")
            save_kwargs = {"quality": 95, "subsampling": 0} if image_format == "JPEG" else {}
            output.save(temp, format=image_format, **save_kwargs)
        validate_image_file(temp, label="temporary converted output")
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def _decode_http_error(exc):
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    detail = body.strip()
    if detail:
        try:
            parsed = json.loads(detail)
            detail = json.dumps(parsed, ensure_ascii=False)
        except Exception:
            detail = detail[:2000]
    return detail or str(exc)


def post_json(url, payload, timeout=60):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise ComfyAPIError(f"ComfyUI HTTP {exc.code}: {_decode_http_error(exc)}") from exc
    except urllib.error.URLError as exc:
        raise ComfyAPIError(f"Could not reach ComfyUI: {exc.reason}") from exc
    try:
        return json.loads(raw)
    except Exception as exc:
        raise ComfyAPIError(f"ComfyUI returned invalid JSON: {raw[:500]}") from exc


def get_json(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise ComfyAPIError(f"ComfyUI HTTP {exc.code}: {_decode_http_error(exc)}") from exc
    except urllib.error.URLError as exc:
        raise ComfyAPIError(f"Could not reach ComfyUI: {exc.reason}") from exc
    try:
        return json.loads(raw)
    except Exception as exc:
        raise ComfyAPIError(f"ComfyUI returned invalid JSON: {raw[:500]}") from exc

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

def is_inside_alpha_output_folder(path, root=None):
    """Ignore generated Alpha/Simple Alpha trees during watching/scanning."""
    try:
        rel = Path(path).relative_to(root) if root is not None else Path(path)
    except Exception:
        rel = Path(path)
    parts = [part.casefold() for part in rel.parts]
    return "alpha" in parts or "simple alpha" in parts or "soft alpha" in parts or "hard alpha" in parts

def is_path_within(path, root):
    """Return True when path is inside root, without requiring either to exist."""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError, RuntimeError):
        return False

def routed_output_path_for_input(path, dump_folder, load_folder, cfg, alpha_attached=None, pure_alpha_kind=None):
    # Legacy alpha flow: every standalone texture uses the normal Load path.
    return output_path_for_input(path, dump_folder, load_folder)

def upload_to_comfy(comfy_url, image_path, subfolder="dolphin_auto"):
    """Upload a collision-safe PNG copy to ComfyUI.

    Converting through Pillow avoids sending TGA/JPEG/WebP bytes with an
    image/png content type, and a UUID filename prevents two Faithful Remaster
    instances from overwriting each other's input image on the Comfy server.
    """
    image_path = Path(image_path)
    temp_upload = None
    upload_name = f"faithful_{_COMFY_UPLOAD_SESSION_ID}_{threading.get_ident()}.png"
    try:
        if PIL_AVAILABLE:
            temp_upload = TEMP_DIR / f"upload_{uuid.uuid4().hex}.png"
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
            with Image.open(image_path) as source:
                source.convert("RGBA").save(temp_upload, format="PNG")
            upload_path = temp_upload
        else:
            if image_path.suffix.lower() not in {".png"}:
                raise RuntimeError("Pillow is required to upload non-PNG textures")
            upload_path = image_path

        boundary = "----FaithfulRemaster" + uuid.uuid4().hex
        body = bytearray()

        def add_field(name, value):
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(f"{value}\r\n".encode())

        def add_file(name, filename, content):
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
            content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
            body.extend(content)
            body.extend(b"\r\n")

        add_file("image", upload_name, upload_path.read_bytes())
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
            with urllib.request.urlopen(req, timeout=120) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ComfyAPIError(f"ComfyUI upload HTTP {exc.code}: {_decode_http_error(exc)}") from exc
        except urllib.error.URLError as exc:
            raise ComfyAPIError(f"Could not upload texture to ComfyUI: {exc.reason}") from exc

        if not isinstance(result, dict) or not result.get("name"):
            raise ComfyAPIError(f"ComfyUI upload returned no filename: {result!r}")
        if result.get("subfolder"):
            return result["subfolder"].replace("\\", "/") + "/" + result["name"]
        return result["name"]
    finally:
        if temp_upload is not None:
            temp_upload.unlink(missing_ok=True)

def set_node_input(workflow, node_id, input_name, value):
    node_key = str(node_id)
    node = workflow.get(node_key)
    if not isinstance(node, dict):
        raise WorkflowValidationError(f"Workflow node {node_key!r} does not exist.")
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        raise WorkflowValidationError(f"Workflow node {node_key!r} has no inputs object.")
    if input_name not in inputs:
        raise WorkflowValidationError(
            f"Workflow node {node_key!r} has no input named {input_name!r}."
        )
    inputs[input_name] = value


def _history_error_text(entry):
    status = entry.get("status", {}) if isinstance(entry, dict) else {}
    messages = status.get("messages", []) if isinstance(status, dict) else []
    details = []
    for item in messages:
        if not isinstance(item, (list, tuple)) or not item:
            continue
        event = str(item[0])
        payload = item[1] if len(item) > 1 else ""
        if event in {"execution_error", "execution_interrupted"}:
            if isinstance(payload, dict):
                node = payload.get("node_id") or payload.get("node_type") or ""
                message = payload.get("exception_message") or payload.get("message") or payload.get("exception_type") or ""
                traceback_tail = payload.get("traceback") or []
                if isinstance(traceback_tail, list) and traceback_tail:
                    traceback_tail = str(traceback_tail[-1]).strip()
                else:
                    traceback_tail = ""
                text = " - ".join(x for x in (event, str(node), str(message), traceback_tail) if x)
            else:
                text = f"{event}: {payload}"
            details.append(text)
    return "; ".join(details[:4])


def find_output(comfy_url, prompt_id, timeout=900, save_node_id=None):
    """Wait for a prompt and return the image from the configured SaveImage node.

    Older builds returned the first image emitted by any node. Workflows with
    previews or multiple SaveImage branches could therefore apply the wrong
    image to a texture. This implementation never falls back to another branch
    when an exact SaveImage node was configured.
    """
    start = time.monotonic()
    save_key = str(save_node_id).strip() if save_node_id not in (None, "") else ""
    while time.monotonic() - start < timeout:
        history = get_json(comfy_url.rstrip("/") + f"/history/{prompt_id}", timeout=10)
        if prompt_id in history:
            entry = history[prompt_id]
            error_text = _history_error_text(entry)
            status = entry.get("status", {}) if isinstance(entry, dict) else {}
            status_str = str(status.get("status_str") or "").casefold() if isinstance(status, dict) else ""
            completed = bool(status.get("completed", False)) if isinstance(status, dict) else False
            if error_text or status_str in {"error", "failed"}:
                raise ComfyAPIError(f"ComfyUI workflow failed: {error_text or status_str}")

            outputs = entry.get("outputs", {}) if isinstance(entry, dict) else {}
            if save_key:
                node_output = outputs.get(save_key, {})
                images = node_output.get("images", []) if isinstance(node_output, dict) else []
                if images:
                    if len(images) != 1:
                        raise ComfyAPIError(
                            f"Configured SaveImage node {save_key!r} produced {len(images)} images. "
                            "Faithful Remaster requires exactly one output image per input texture."
                        )
                    return images[0]
                if completed or status_str in {"success", "completed"}:
                    available = ", ".join(sorted(map(str, outputs.keys()))) or "none"
                    raise ComfyAPIError(
                        f"Configured SaveImage node {save_key!r} produced no image. "
                        f"History contains output nodes: {available}."
                    )
            else:
                for node_output in outputs.values():
                    images = node_output.get("images", []) if isinstance(node_output, dict) else []
                    if images:
                        return images[0]
                if completed or status_str in {"success", "completed"}:
                    return None
        time.sleep(0.75)
    raise TimeoutError(f"Timed out after {int(timeout)} seconds waiting for ComfyUI prompt {prompt_id}")


def download_output(comfy_url, image_info, dest_png):
    if not isinstance(image_info, dict) or not image_info.get("filename"):
        raise ComfyAPIError(f"Invalid ComfyUI image metadata: {image_info!r}")
    params = urllib.parse.urlencode({
        "filename": image_info["filename"],
        "subfolder": image_info.get("subfolder", ""),
        "type": image_info.get("type", "output"),
    })
    try:
        with urllib.request.urlopen(comfy_url.rstrip("/") + "/view?" + params, timeout=120) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise ComfyAPIError(f"ComfyUI output download HTTP {exc.code}: {_decode_http_error(exc)}") from exc
    except urllib.error.URLError as exc:
        raise ComfyAPIError(f"Could not download ComfyUI output: {exc.reason}") from exc
    dest_png = Path(dest_png)
    dest_png.parent.mkdir(parents=True, exist_ok=True)
    dest_png.write_bytes(data)
    validate_image_file(dest_png, label="ComfyUI output")


def run_comfy_image_workflow_to_file(comfy_url, workflow_template, image_path, load_node_id, save_node_id, filename_prefix, timeout=900):
    """Run one exact API workflow route and return its validated temporary PNG."""
    load_id = str(load_node_id)
    save_id = str(save_node_id) if save_node_id not in (None, "") else ""
    # Revalidate the in-memory template as well as files validated during setup.
    workflow = json.loads(json.dumps(workflow_template))
    nodes = {str(key): value for key, value in workflow.items() if isinstance(value, dict)}
    if load_id not in nodes or str(nodes[load_id].get("class_type")) != "LoadImage":
        raise WorkflowValidationError(f"Configured LoadImage node {load_id!r} is invalid.")
    if save_id and (save_id not in nodes or str(nodes[save_id].get("class_type")) != "SaveImage"):
        raise WorkflowValidationError(f"Configured SaveImage node {save_id!r} is invalid.")

    uploaded = upload_to_comfy(comfy_url, Path(image_path))
    set_node_input(workflow, load_id, "image", uploaded)
    if save_id:
        set_node_input(workflow, save_id, "filename_prefix", filename_prefix)

    client_id = "faithful-remaster-" + uuid.uuid4().hex
    response = post_json(comfy_url.rstrip("/") + "/prompt", {
        "prompt": workflow,
        "client_id": client_id,
    }, timeout=min(120, max(10, int(timeout))))
    if not isinstance(response, dict) or not response.get("prompt_id"):
        raise ComfyAPIError(f"ComfyUI did not return a prompt_id: {response!r}")
    node_errors = response.get("node_errors")
    if node_errors:
        raise ComfyAPIError(f"ComfyUI rejected workflow nodes: {json.dumps(node_errors, ensure_ascii=False)[:2000]}")
    image_info = find_output(
        comfy_url, str(response["prompt_id"]), int(timeout), save_node_id=save_id or None
    )
    if not image_info:
        raise ComfyAPIError(f"Workflow completed but SaveImage node {save_id!r} returned no output.")
    out_file = TEMP_DIR / f"comfy_{uuid.uuid4().hex}.png"
    try:
        download_output(comfy_url, image_info, out_file)
        return out_file
    except Exception:
        out_file.unlink(missing_ok=True)
        raise

def apply_alpha_image_to_rgba(rgb_png_path, alpha_image_path, invert=False, reference_alpha_path=None):
    """Apply a validated grayscale alpha-workflow result to an RGB output."""
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is not installed")
    from PIL import ImageOps
    with Image.open(rgb_png_path) as rgb_source:
        out = rgb_source.convert("RGBA")
    with Image.open(alpha_image_path) as alpha_source:
        mask = alpha_source.convert("L")
    if mask.size != out.size:
        mask = mask.resize(out.size, Image.Resampling.LANCZOS)
    if invert:
        mask = ImageOps.invert(mask)

    generated_extrema = mask.getextrema()
    if reference_alpha_path is not None:
        with Image.open(reference_alpha_path) as reference_source:
            reference = reference_source.convert("RGBA").getchannel("A")
        if reference.size != mask.size:
            reference = reference.resize(mask.size, Image.Resampling.NEAREST)
        reference_extrema = reference.getextrema()
        if reference_extrema[0] != reference_extrema[1] and generated_extrema[0] == generated_extrema[1]:
            raise RuntimeError(
                "Alpha workflow produced a flat mask for a texture whose original alpha contains detail."
            )

        # A non-flat image can still be the wrong SaveImage branch (for example
        # RGB luminance instead of the upscaled alpha mask). Compare a lightweight
        # sample against the original alpha and reject only clear mismatches.
        if reference_extrema[0] != reference_extrema[1] and generated_extrema[0] != generated_extrema[1]:
            sample_size = (min(128, mask.width), min(128, mask.height))
            generated_sample = mask.resize(sample_size, Image.Resampling.BOX)
            reference_sample = reference.resize(sample_size, Image.Resampling.BOX)
            gv = list(generated_sample.tobytes())
            rv = list(reference_sample.tobytes())
            count = max(1, len(gv))
            gmean = sum(gv) / count
            rmean = sum(rv) / count
            mae = sum(abs(g - r) for g, r in zip(gv, rv)) / (255.0 * count)
            gc = [g - gmean for g in gv]
            rc = [r - rmean for r in rv]
            covariance = sum(g * r for g, r in zip(gc, rc))
            genergy = sum(g * g for g in gc)
            renergy = sum(r * r for r in rc)
            correlation = covariance / max(1e-9, (genergy * renergy) ** 0.5)
            coverage_gap = abs(gmean - rmean) / 255.0
            if ((correlation < 0.20 and mae > 0.18) or
                    (coverage_gap > 0.35 and mae > 0.28)):
                raise RuntimeError(
                    "Alpha workflow output does not resemble the source alpha mask "
                    f"(correlation={correlation:.2f}, error={mae:.2f}). "
                    "Check the configured Alpha SaveImage node or invert setting."
                )
    out.putalpha(mask)
    out.save(rgb_png_path, format="PNG")
    validate_image_file(rgb_png_path, label="RGBA output")

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

def classify_texture_visual_type(path, max_sample_side=64):
    """Classify a texture for Texture Manager review filters.

    Returns a dictionary with:
      - ``mask_grayscale``: True when the visible RGB content is overwhelmingly
        neutral/grayscale. These are *candidates* for masks, fog, light maps and
        other effect textures; the classifier deliberately does not claim that
        every grayscale texture is an effect.
      - ``has_transparency``: True when any alpha value is below 255.

    The scan is intentionally lightweight: the image is sampled down to at most
    64×64 and transparent pixels are ignored when judging color. This avoids a
    colored cutout on a transparent black background being mislabeled as a mask.
    """
    result = {"mask_grayscale": False, "has_transparency": False, "dimensions": None, "pixels": 0}
    if not PIL_AVAILABLE:
        return result
    try:
        with Image.open(path) as source:
            result["dimensions"] = tuple(source.size)
            result["pixels"] = int(source.size[0]) * int(source.size[1])
            rgba = source.convert("RGBA")
            alpha = rgba.getchannel("A")
            alpha_min, _alpha_max = alpha.getextrema()
            result["has_transparency"] = alpha_min < 255

            sample = rgba.copy()
            sample.thumbnail((max(8, int(max_sample_side)), max(8, int(max_sample_side))), Image.Resampling.BOX)
            pixels = list(sample.getdata())
            visible = [(r, g, b) for r, g, b, a in pixels if a > 16]
            if not visible:
                visible = [(r, g, b) for r, g, b, _a in pixels]
            if not visible:
                return result

            # PNG masks are often exact grayscale, while emulator dumps may have
            # tiny channel differences. Requiring 94% of sampled visible pixels
            # to have a channel spread <= 8 keeps the filter useful without
            # broadly swallowing muted colored textures.
            neutral = 0
            total_spread = 0
            for r, g, b in visible:
                spread = max(r, g, b) - min(r, g, b)
                total_spread += spread
                if spread <= 8:
                    neutral += 1
            neutral_ratio = neutral / max(1, len(visible))
            average_spread = total_spread / max(1, len(visible))
            result["mask_grayscale"] = neutral_ratio >= 0.94 and average_spread <= 6.0
            return result
    except Exception:
        return result

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

def matching_exception_patterns(path):
    """Return every saved exception pattern that matches *path*."""
    name = Path(path).name
    stem = Path(path).stem
    rel = str(path).replace("\\", "/")
    matches = []
    for pat in load_exception_patterns():
        normalized = pat.replace("\\", "/")
        if (
            fnmatch.fnmatch(name, normalized)
            or fnmatch.fnmatch(stem, normalized)
            or fnmatch.fnmatch(rel, normalized)
        ):
            matches.append(pat)
    return matches


def remove_exception_patterns_for_texture(path):
    """Remove all saved patterns that currently mark *path* as an exception."""
    matches = matching_exception_patterns(path)
    if not matches:
        return []
    match_set = set(matches)
    remaining = [p for p in load_exception_patterns() if p not in match_set]
    save_exception_patterns(remaining)
    return matches


def is_exception_texture(path):
    return bool(matching_exception_patterns(path))



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
        result["warnings"].append(
            "More than one SaveImage node found. Auto-detect will not guess; enter the exact final SaveImage node ID."
        )

    if not result["load_nodes"]:
        result["warnings"].append("No LoadImage node found. Make sure this is an API workflow JSON.")
    if not result["save_nodes"]:
        result["warnings"].append("No SaveImage node found. Make sure this is an API workflow JSON.")

    return result


BUILTIN_WORKFLOW_PROFILE_NAMES = ("Clean Heart", "Strong Believer")
TEXTURE_PRESET_INHERIT = "Use game default"

# Legacy preset names/IDs are migrated without breaking existing profiles or
# per-texture overrides. The historical "midway" ID is intentionally retained
# internally so old override files continue to resolve automatically.
LEGACY_PRESET_ALIASES = {
    "midway": "midway",
    "mid way": "midway",
    "clean heart": "midway",
    "clean_heart": "midway",
    "soft heart": "midway",
    "soft_heart": "midway",
    "strong believer": "strong_believer",
    "strong_believer": "strong_believer",
}


def _canonical_workflow_profile_id(value):
    text = str(value or "").strip().casefold()
    return LEGACY_PRESET_ALIASES.get(text, text)


def _builtin_workflow_profiles():
    wf = APP_DIR / "workflows"
    return [
        {
            "id": "midway",  # retained for seamless migration from older builds
            "name": "Clean Heart",
            "ui_path": str(wf / "Faithful_RGB_Workflow_UI_Clean_Heart.json"),
            "api_path": str(wf / "Faithful_RGB_Workflow_API_Clean_Heart.json"),
            "load_node": "1", "save_node": "4", "builtin": True, "enabled": True,
        },
        {
            "id": "strong_believer",
            "name": "Strong Believer",
            "ui_path": str(wf / "Faithful_RGB_Workflow_UI_Strong_Believer.json"),
            "api_path": str(wf / "Faithful_RGB_Workflow_API_Strong_Believer.json"),
            "load_node": "1", "save_node": "4", "builtin": True, "enabled": True,
        },
    ]

def load_workflow_profiles():
    defaults = _builtin_workflow_profiles()
    data = None
    try:
        if WORKFLOW_PROFILES_PATH.exists():
            data = json.loads(WORKFLOW_PROFILES_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = None
    profiles = data.get("profiles", []) if isinstance(data, dict) else []
    if not isinstance(profiles, list):
        profiles = []

    # Remove the retired built-in Soft Heart entry. Existing game defaults and
    # per-texture overrides are migrated to Clean Heart by alias resolution.
    filtered = []
    changed = False
    for item in profiles:
        if not isinstance(item, dict):
            changed = True
            continue
        pid = str(item.get("id") or "").strip().casefold()
        name = str(item.get("name") or "").strip().casefold()
        if pid == "soft_heart" or (item.get("builtin") and name == "soft heart"):
            changed = True
            continue
        filtered.append(item)
    profiles = filtered

    by_id = {str(x.get("id")): x for x in profiles if isinstance(x, dict)}
    for default in defaults:
        if default["id"] not in by_id:
            profiles.append(dict(default))
            changed = True
            continue
        item = by_id[default["id"]]
        # Built-ins always follow the current build's names and bundled files.
        for key in ("name", "ui_path", "api_path", "load_node", "save_node", "builtin", "enabled"):
            if item.get(key) != default.get(key):
                item[key] = default.get(key)
                changed = True

    clean = []
    seen = set()
    for item in profiles:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not pid or not name or pid in seen:
            changed = True
            continue
        seen.add(pid)
        clean.append({
            "id": pid, "name": name,
            "ui_path": str(item.get("ui_path") or ""),
            "api_path": str(item.get("api_path") or ""),
            "load_node": str(item.get("load_node") or "1"),
            "save_node": str(item.get("save_node") or "4"),
            "builtin": bool(item.get("builtin", False)),
            "enabled": bool(item.get("enabled", True)),
        })
    if changed or not WORKFLOW_PROFILES_PATH.exists():
        save_workflow_profiles(clean)
    return clean

def save_workflow_profiles(profiles):
    WORKFLOW_PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp=WORKFLOW_PROFILES_PATH.with_suffix('.json.tmp')
    temp.write_text(json.dumps({"profiles": profiles}, indent=2), encoding='utf-8')
    temp.replace(WORKFLOW_PROFILES_PATH)


def workflow_profile_names(enabled_only=True):
    return tuple(p["name"] for p in load_workflow_profiles() if (p.get("enabled", True) or not enabled_only))


def find_workflow_profile_exact(value):
    raw = str(value or "").strip()
    canonical_id = _canonical_workflow_profile_id(raw)
    for profile in load_workflow_profiles():
        if canonical_id == str(profile.get("id", "")).casefold():
            return profile
    for profile in load_workflow_profiles():
        if raw.casefold() == str(profile.get("name", "")).casefold():
            return profile
    return None


def find_workflow_profile(value):
    exact = find_workflow_profile_exact(value)
    if exact is not None:
        return exact
    raw = str(value or "").strip()
    profiles = load_workflow_profiles()
    for profile in profiles:
        if profile.get("name") == "Clean Heart":
            return profile
    return profiles[0] if profiles else None


def normalize_faithfulness_preset(value, default="Clean Heart"):
    found = find_workflow_profile(value)
    if found and found.get("enabled", True):
        return found["name"]
    fallback = find_workflow_profile(default)
    return fallback["name"] if fallback else "Clean Heart"

def texture_preset_overrides_path(profile_dir):
    return Path(profile_dir) / "texture_preset_overrides.json"


def load_texture_preset_overrides(profile_dir):
    path=texture_preset_overrides_path(profile_dir)
    if not path.exists(): return {}
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
        if isinstance(data,dict):
            clean={}
            for key,value in data.items():
                profile=find_workflow_profile_exact(value)
                if profile and profile.get("enabled", True):
                    clean[str(key)]=profile['id']
            return clean
    except Exception: pass
    return {}


def save_texture_preset_overrides(profile_dir, data):
    path=texture_preset_overrides_path(profile_dir)
    path.parent.mkdir(parents=True,exist_ok=True)
    clean={}
    for key,value in sorted(data.items()):
        profile=find_workflow_profile_exact(value)
        if profile and profile.get("enabled", True):
            clean[str(key)]=profile['id']
    temp=path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(clean,indent=2),encoding='utf-8')
    temp.replace(path)


def texture_override_key(path, dump_folder):
    try: return Path(path).resolve().relative_to(Path(dump_folder).resolve()).as_posix()
    except Exception: return Path(path).name


def effective_texture_preset(path,cfg,overrides=None):
    default=normalize_faithfulness_preset(cfg.get('faithfulness_preset','Clean Heart'))
    if overrides is None:
        pd=cfg.get('profile_dir'); overrides=load_texture_preset_overrides(pd) if pd else {}
    key=texture_override_key(path,cfg.get('dump_folder',''))
    chosen=overrides.get(key)
    profile=find_workflow_profile_exact(chosen) if chosen else find_workflow_profile_exact(default)
    if not profile or not profile.get("enabled", True):
        profile=find_workflow_profile_exact(default) or find_workflow_profile("Clean Heart")
    return profile['name'] if profile else default


def workflow_profile_fingerprint(profile):
    api = Path(str(profile.get("api_path", "")))
    semantic = {
        "pipeline": PROCESSING_PIPELINE_VERSION,
        "id": str(profile.get("id") or ""),
        "api_path": str(api),
        "api_sha256": workflow_file_fingerprint(api),
        "load_node": str(profile.get("load_node") or ""),
        "save_node": str(profile.get("save_node") or ""),
        "task_type": str(profile.get("task_type") or "standard"),
        "output_scale": str(profile.get("output_scale") or 4),
        "backend_id": str(profile.get("backend_id") or ""),
    }
    return hashlib.sha256(json.dumps(semantic, sort_keys=True).encode("utf-8")).hexdigest()




class Worker:
    def __init__(self, cfg, log_q, stop_event, force_scan_event, force_missing_event, stats):
        self.cfg = cfg
        self.log_q = log_q
        self.stop_event = stop_event
        self.force_scan_event = force_scan_event
        self.force_missing_event = force_missing_event
        self.stats = stats
        self.workflow_template = None  # workflow is selected per texture/profile
        self._workflow_template_cache = {}
        self.profile_dir = Path(cfg.get("profile_dir") or DATA_DIR)
        self.texture_preset_overrides = load_texture_preset_overrides(self.profile_dir)
        self.processed_log = Path(cfg.get("processed_log") or (DATA_DIR / "processed.txt"))
        self.processed = set()
        if self.processed_log.exists():
            self.processed = {x.strip() for x in self.processed_log.read_text(encoding="utf-8").splitlines() if x.strip()}
        self.cache_index = load_cache_index()
        self.vram_paused = False
        self.comfy_paused = False
        self.known_files = set()
        # Queue lanes, from highest to lowest priority:
        # Texture Manager requests -> newly dumped / missing outputs -> initial backlog.
        # A lock is required because Texture Manager actions run on the Tk UI thread
        # while the worker consumes tasks on its background thread.
        self.queue_lock = threading.RLock()
        self.manager_q = []
        self.high_q = []
        self.low_q = []
        # A texture interrupted by a backend crash may be retried once after
        # the watchdog restarts the API. Per-file counts prevent crash loops.
        self.backend_outage_retries = {}
        # Textures that failed for deterministic reasons in this worker session.
        # They are not written to processed.txt, so a later program run can retry
        # them after workflow/model/backend fixes, but they must not be requeued
        # forever by the live scanner or Batch Queue will never advance.
        self.failed_this_session = set()
        # Auto EFB/cutscene quarantine state. Candidates are classified before
        # they enter the processing queues, then moved in bulk to avoid per-file
        # manifest rewrites and UI/log overhead when games dump thousands of buffers.
        self._auto_quarantine_classified = set()
        self._live_buffer_candidates = {}
        self._live_buffer_last_candidate_at = 0.0

    def log(self, msg):
        self.log_q.put(msg)

    def mark_processed(self, path):
        s = str(path)
        self.failed_this_session.discard(s)
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
        if not self.cfg.get("enable_vram_protection", False):
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
                atomic_save_processed_image(cache_file, out_path)
            self.stats["cache_hits"] += 1
            return True
        return False

    def process_one(self, path):
        dump_folder = Path(self.cfg["dump_folder"])
        load_folder = Path(self.cfg["load_folder"])
        if self.cfg.get("emulator") == "Azahar / Citra" and self.cfg.get("auto_sync_azahar_pack_json", True):
            sync_azahar_pack_json(dump_folder, load_folder, self.log)

        alpha = self.cfg.get("preserve_alpha", True) and has_alpha(path)
        out_path = output_path_for_input(path, dump_folder, load_folder)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        self.stats["current_input_path"] = str(path)
        self.stats["current_output_path"] = ""
        self.stats["current_texture_stage"] = "Preparing"
        self.stats["current_texture_started_at"] = time.time()

        if alpha:
            self.log(f"Alpha detected: {path.name}")

        if is_exception_texture(path):
            self.stats["exceptions_skipped"] = self.stats.get("exceptions_skipped", 0) + 1
            return "exception_skipped"

        if self.cfg.get("skip_dynamic_efb_postprocess", True):
            is_dynamic, reason = detect_dynamic_efb_postprocess_dump(path, self.cfg)
            if is_dynamic:
                self.stats["cutscene_buffers_skipped"] = self.stats.get("cutscene_buffers_skipped", 0) + 1
                self.log(f"Dynamic EFB/post-processing dump skipped and KEPT: {path.name} ({reason})")
                return "dynamic_efb_skipped"

        if self.cfg.get("skip_cutscene_buffers", True):
            is_buffer, reason = detect_cutscene_buffer(path, self.cfg, include_dynamic=False)
            if is_buffer:
                self.stats["cutscene_buffers_skipped"] = self.stats.get("cutscene_buffers_skipped", 0) + 1
                self.log(f"Cutscene/blank buffer skipped: {path.name} ({reason})")
                if self.cfg.get("delete_skipped_cutscene_buffers", False):
                    try:
                        session = self.profile_dir / "_cleanup_quarantine" / time.strftime("live-%Y%m%d-%H%M%S")
                        destination = quarantine_dump(path, dump_folder, session, category="cutscene_or_blank", reason=reason)
                        self.stats["cutscene_buffers_deleted"] = self.stats.get("cutscene_buffers_deleted", 0) + 1
                        self.log(f"Moved skipped dump to quarantine: {destination}")
                    except Exception as e:
                        self.log(f"Could not quarantine skipped cutscene buffer {path.name}: {e}")
                return "cutscene_buffer_skipped"

        preset = effective_texture_preset(path, self.cfg, self.texture_preset_overrides)
        self.stats["current_faithfulness_preset"] = preset
        self.log(f"Processing: {path.name} [{preset}]")

        if self.cfg.get("ignore_existing_silently", True) and out_path.exists() and not self.cfg.get("overwrite", False):
            return "ignored_existing"

        digest = sha1_file(path) if self.cfg.get("enable_hash_cache", True) else None
        if digest:
            _wp = find_workflow_profile(preset)
            _fp = workflow_profile_fingerprint(_wp) if _wp else preset
            digest = hashlib.sha1(f"{digest}|{preset}|{_fp}".encode("utf-8")).hexdigest()
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

        self.stats["current_texture_stage"] = "Uploading and processing RGB"
        uploaded = upload_to_comfy(self.cfg["comfy_url"], path)
        workflow_profile = find_workflow_profile(preset)
        if not workflow_profile:
            raise RuntimeError(f"Workflow profile not found: {preset}")
        api_path = Path(str(workflow_profile.get("api_path", "")))
        if not api_path.exists():
            raise FileNotFoundError(f"Workflow API file does not exist for {preset}: {api_path}")
        cache_key = workflow_profile_fingerprint(workflow_profile)
        template = self._workflow_template_cache.get(cache_key)
        if template is None:
            template = load_json(api_path)
            self._workflow_template_cache = {cache_key: template}
        workflow = json.loads(json.dumps(template))
        load_node = str(workflow_profile.get("load_node") or "1")
        save_node = str(workflow_profile.get("save_node") or "4")
        set_node_input(workflow, load_node, "image", uploaded)
        if save_node:
            set_node_input(workflow, save_node, "filename_prefix", "dolphin_auto/" + path.stem)

        self.stats["comfy_jobs"] += 1
        if preset in BUILTIN_WORKFLOW_PROFILE_NAMES:
            self.log(f"{preset}: ControlNet + KSampler RGB job submitted")
        else:
            self.log(f"{preset}: RGB workflow job submitted")
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
                        validate_alpha_comfy_api_workflow(
                            alpha_workflow_path,
                            self.cfg.get("alpha_load_image_node_id", "1"),
                            self.cfg.get("alpha_save_image_node_id", "5"),
                            require_reachable=True,
                        )
                        self.stats["current_texture_stage"] = "Processing separate alpha"
                        self.log(f"Alpha workflow: running separate alpha path for {path.name}")
                        alpha_template = load_json(alpha_workflow_path)
                        self.stats["comfy_jobs"] += 1
                        alpha_temp_png = run_comfy_image_workflow_to_file(
                            self.cfg["comfy_url"],
                            alpha_template,
                            path,
                            int(self.cfg.get("alpha_load_image_node_id", 1)),
                            int(self.cfg.get("alpha_save_image_node_id", 5)),
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
        self.log(f"Saved output: {out_path.name}")
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

    def enqueue_manager_tasks(self, paths):
        """Queue Texture Manager recreation requests above every automatic lane.

        The active ComfyUI job is allowed to finish safely. The first manager task is
        selected immediately after that job, before HIGH and LOW priority textures.
        """
        added = 0
        normalized = [Path(p) for p in paths]
        with self.queue_lock:
            requested = {str(p) for p in normalized}
            # Manager requests are explicit user retries; allow them even if the
            # texture failed earlier in the same watch/batch session.
            self.failed_this_session.difference_update(requested)
            # Move requested textures out of lower lanes so each file exists in one lane.
            self.high_q = [p for p in self.high_q if str(p) not in requested]
            self.low_q = [p for p in self.low_q if str(p) not in requested]
            existing = {str(p) for p in self.manager_q}
            for p in normalized:
                key = str(p)
                if key in existing or not p.exists():
                    continue
                self.manager_q.append(p)
                existing.add(key)
                added += 1
        self.update_queue_stats()
        if added:
            self.log(f"Texture Manager: {added} request(s) queued at MANAGER priority")
        return added

    def check_missing_loads(self):
        """
        Compare Dump folder against Load folder.
        Missing outputs use HIGH priority when 'Prioritize new dumps' is enabled;
        otherwise they join the ordinary LOW queue. Texture Manager requests remain first.
        """
        dump_folder = Path(self.cfg["dump_folder"])
        load_folder = Path(self.cfg["load_folder"])
        missing = []
        files = sorted([
            p for p in dump_folder.rglob("*")
            if p.is_file() and not is_inside_alpha_output_folder(p, dump_folder) and is_image_like(p, self.cfg.get("process_tmp_image_files", True))
        ], key=lambda p: p.stat().st_mtime, reverse=True)
        files = self._filter_and_stage_live_buffer_candidates(files)

        with self.queue_lock:
            queued = ({str(p) for p in self.manager_q} |
                      {str(p) for p in self.high_q} |
                      {str(p) for p in self.low_q})
            failed = set(self.failed_this_session)

        for p in files:
            if str(p) in failed:
                continue
            # Terminally handled files that intentionally have no Load output must
            # not be requeued forever. Re-check only processed/no-output files so
            # deleted normal outputs can still be rebuilt.
            if str(p) in self.processed:
                if self.cfg.get("batch_queue_mode", False):
                    continue
                dynamic, _ = detect_dynamic_efb_postprocess_dump(p, self.cfg)
                static_buffer, _ = detect_cutscene_buffer(p, self.cfg, include_dynamic=False)
                if dynamic or static_buffer or is_exception_texture(p):
                    continue
            out_path = routed_output_path_for_input(p, dump_folder, load_folder, self.cfg)
            if not out_path.exists():
                self.unmark_processed(p)
                if str(p) not in queued:
                    missing.append(p)

        if missing:
            with self.queue_lock:
                if self.cfg.get("prioritize_new_dumps", True):
                    self.high_q = missing + self.high_q
                    lane = "HIGH priority"
                else:
                    self.low_q.extend(missing)
                    lane = "the normal queue"
            self.log(f"Missing Load Check: {len(missing)} missing file(s) added to {lane}")
        else:
            self.log("Missing Load Check: no missing files")

        self.update_queue_stats()

    def _remove_paths_from_queues(self, paths):
        keys = {str(Path(path)) for path in paths}
        if not keys:
            return
        with self.queue_lock:
            self.manager_q = [p for p in self.manager_q if str(p) not in keys]
            self.high_q = [p for p in self.high_q if str(p) not in keys]
            self.low_q = [p for p in self.low_q if str(p) not in keys]
        self.update_queue_stats()

    def _bulk_quarantine_buffer_candidates(self, candidates, session_prefix="auto"):
        """Move strict EFB/cutscene candidates in one reversible bulk operation."""
        candidates = [item for item in candidates if Path(item[0]).is_file()]
        if not candidates:
            return [], []
        dump_folder = Path(self.cfg["dump_folder"])
        session = (
            self.profile_dir / "_buffer_quarantine" /
            f"{session_prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        )
        moved, failures = quarantine_dumps_bulk(
            candidates, dump_folder, session, manifest_flush_every=250
        )
        moved_sources = [source for source, _destination, _category, _reason in moved]
        moved_keys = {str(path) for path in moved_sources}
        if moved_keys:
            self._remove_paths_from_queues(moved_sources)
            processed_changed = False
            for key in moved_keys:
                if key in self.processed:
                    self.processed.discard(key)
                    processed_changed = True
                self.known_files.discard(key)
                self._auto_quarantine_classified.discard(key)
            if processed_changed:
                try:
                    self.processed_log.write_text(
                        "\n".join(sorted(self.processed)) + ("\n" if self.processed else ""),
                        encoding="utf-8"
                    )
                except Exception as exc:
                    self.log(f"Auto-quarantine could not update processed log: {exc}")

        efb_count = sum(1 for _s, _d, category, _r in moved if category == "dynamic_efb")
        cutscene_count = sum(1 for _s, _d, category, _r in moved if category == "cutscene")
        self.stats["auto_buffer_quarantine_moved"] = self.stats.get("auto_buffer_quarantine_moved", 0) + len(moved)
        self.stats["auto_buffer_quarantine_failed"] = self.stats.get("auto_buffer_quarantine_failed", 0) + len(failures)
        self.stats["cutscene_buffers_deleted"] = self.stats.get("cutscene_buffers_deleted", 0) + len(moved)
        self.log(
            f"Auto EFB/Cutscene bulk quarantine: moved={len(moved)} "
            f"(EFB={efb_count}, cutscenes={cutscene_count}), failed={len(failures)}"
        )
        if moved:
            self.log(f"Quarantine session: {session}")
        for path, error in failures[:10]:
            self.log(f"Auto-quarantine failed: {path.name}: {error}")
        return moved, failures

    def auto_quarantine_efb_cutscenes_on_start(self):
        """Scan once before indexing and quarantine strict buffers without prompts."""
        if not self.cfg.get("auto_quarantine_efb_cutscenes", False):
            return
        if not PIL_AVAILABLE:
            self.log("Auto EFB/Cutscene quarantine skipped: Pillow is unavailable.")
            return

        dump_folder = Path(self.cfg["dump_folder"])
        load_text = str(self.cfg.get("load_folder") or "").strip()
        load_folder = Path(load_text) if load_text else None
        exclude_load_tree = bool(load_folder and is_path_within(load_folder, dump_folder))
        process_tmp = bool(self.cfg.get("process_tmp_image_files", True))
        candidates = []
        protected = scanned = recent_skipped = failed_analysis = 0
        started = time.time()
        self.stats["current_texture_stage"] = "Scanning EFB/Cutscene buffers"
        self.log(f"Auto EFB/Cutscene startup scan: {dump_folder}")

        files = [
            path for path in dump_folder.rglob("*")
            if path.is_file()
            and not is_inside_alpha_output_folder(path, dump_folder)
            and not (exclude_load_tree and is_path_within(path, load_folder))
            and is_image_like(path, process_tmp)
        ]
        for path in files:
            if self.stop_event.is_set():
                break
            scanned += 1
            key = str(path)
            try:
                if time.time() - path.stat().st_mtime < 1.5:
                    recent_skipped += 1
                    continue
                category, reason = classify_strict_buffer_quarantine_candidate(path, self.cfg)
                self._auto_quarantine_classified.add(key)
                if category:
                    candidates.append((path, category, reason))
                elif str(reason).startswith("protected "):
                    protected += 1
            except Exception:
                failed_analysis += 1
            if scanned % 500 == 0:
                self.log(
                    f"Auto buffer scan progress: scanned={scanned}, candidates={len(candidates)}, "
                    f"protected={protected}"
                )

        moved, failures = self._bulk_quarantine_buffer_candidates(candidates, "auto-start")
        elapsed = time.time() - started
        self.stats["auto_buffer_quarantine_scanned"] = scanned
        self.stats["auto_buffer_quarantine_detected"] = len(candidates)
        self.stats["auto_buffer_quarantine_recent_skipped"] = recent_skipped
        self.log(
            "Auto EFB/Cutscene startup scan complete: "
            f"scanned={scanned}, detected={len(candidates)}, moved={len(moved)}, "
            f"protected={protected}, recent-skipped={recent_skipped}, "
            f"analysis-failed={failed_analysis}, move-failed={len(failures)}, elapsed={elapsed:.1f}s"
        )

    def _flush_live_buffer_candidates(self, force=False):
        if not self.cfg.get("auto_quarantine_efb_cutscenes", False):
            self._live_buffer_candidates.clear()
            return
        if not self._live_buffer_candidates:
            return
        threshold = max(2, int(float(self.cfg.get("auto_quarantine_live_threshold", 12) or 12)))
        idle_seconds = max(1.0, float(self.cfg.get("auto_quarantine_live_idle_seconds", 5.0) or 5.0))
        oldest = min(float(item.get("first_seen", time.time())) for item in self._live_buffer_candidates.values())
        if not force and len(self._live_buffer_candidates) < threshold and time.time() - oldest < idle_seconds:
            return

        batch = []
        for key, item in list(self._live_buffer_candidates.items()):
            path = Path(key)
            if path.is_file():
                batch.append((path, item["category"], item["reason"]))
            else:
                self._live_buffer_candidates.pop(key, None)
        if not batch:
            return

        moved, failures = self._bulk_quarantine_buffer_candidates(batch, "auto-live")
        moved_keys = {str(source) for source, _d, _c, _r in moved}
        failure_keys = {str(path) for path, _error in failures}
        for key in moved_keys:
            self._live_buffer_candidates.pop(key, None)
        for key in list(self._live_buffer_candidates):
            if key not in failure_keys and not Path(key).exists():
                self._live_buffer_candidates.pop(key, None)
        for key in failure_keys:
            item = self._live_buffer_candidates.get(key)
            if not item:
                continue
            item["retries"] = int(item.get("retries", 0)) + 1
            item["first_seen"] = time.time()
            if item["retries"] >= 3:
                self.log(f"Auto-quarantine giving up after 3 move attempts: {Path(key).name}")
                self._live_buffer_candidates.pop(key, None)
                self._auto_quarantine_classified.add(key)

    def _filter_and_stage_live_buffer_candidates(self, files):
        """Classify unqueued files; hold strict candidates until a bulk flush."""
        if not self.cfg.get("auto_quarantine_efb_cutscenes", False) or not PIL_AVAILABLE:
            return [path for path in files if path.is_file()]

        allowed = []
        now = time.time()
        for path in files:
            if not path.is_file():
                continue
            key = str(path)
            if key in self._live_buffer_candidates:
                continue
            if key in self._auto_quarantine_classified:
                allowed.append(path)
                continue
            try:
                if now - path.stat().st_mtime < 1.0:
                    # Keep newly-written images out of processing until they are stable
                    # enough for a reliable buffer decision on a later scan.
                    continue
                category, reason = classify_strict_buffer_quarantine_candidate(path, self.cfg)
                self._auto_quarantine_classified.add(key)
                if category:
                    self._live_buffer_candidates[key] = {
                        "category": category,
                        "reason": reason,
                        "first_seen": now,
                        "retries": 0,
                    }
                    self._live_buffer_last_candidate_at = now
                    continue
            except Exception as exc:
                self.log(f"Auto buffer classification failed; keeping active: {path.name}: {exc}")
                self._auto_quarantine_classified.add(key)
            allowed.append(path)

        self._flush_live_buffer_candidates(force=False)
        return [path for path in allowed if path.is_file() and str(path) not in self._live_buffer_candidates]

    def auto_cleanup_cutscene_dumps_on_start(self):
        """Safely quarantine only fully transparent or near-solid-black dumps before indexing.

        Dynamic EFB/post-processing candidates are deliberately excluded and remain
        untouched. Files modified in the last two seconds are also left alone.
        """
        if not self.cfg.get("auto_scan_delete_cutscene_buffers_on_start", False):
            return
        if not PIL_AVAILABLE:
            self.log("Startup cleanup skipped: Pillow is unavailable.")
            return

        dump_folder = Path(self.cfg["dump_folder"])
        load_folder_text = str(self.cfg.get("load_folder") or "").strip()
        load_folder = Path(load_folder_text) if load_folder_text else None
        exclude_load_tree = bool(load_folder and is_path_within(load_folder, dump_folder))
        process_tmp = bool(self.cfg.get("process_tmp_image_files", True))
        scanned = detected = quarantined = failed = recent_skipped = 0
        processed_changed = False
        started = time.time()
        quarantine_session = self.profile_dir / "_cleanup_quarantine" / time.strftime("startup-%Y%m%d-%H%M%S")
        self.stats["current_texture_stage"] = "Safe startup blank-dump quarantine"
        self.log(f"Safe startup cleanup scanning: {dump_folder}")
        self.log("Dynamic EFB/post-processing dumps are excluded and will never be moved by startup cleanup.")

        files = [
            p for p in dump_folder.rglob("*")
            if p.is_file()
            and not is_inside_alpha_output_folder(p, dump_folder)
            and not (exclude_load_tree and is_path_within(p, load_folder))
            and is_image_like(p, process_tmp)
        ]

        for path in files:
            if self.stop_event.is_set():
                self.log("Startup cleanup interrupted by Stop.")
                break
            scanned += 1
            try:
                if time.time() - path.stat().st_mtime < 2.0:
                    recent_skipped += 1
                    continue
            except OSError:
                failed += 1
                continue

            is_dump, reason = detect_safe_blank_dump(path, self.cfg)
            if not is_dump:
                continue
            detected += 1
            try:
                destination = quarantine_dump(path, dump_folder, quarantine_session, category="blank", reason=reason)
                quarantined += 1
                if str(path) in self.processed:
                    self.processed.discard(str(path))
                    processed_changed = True
                self.known_files.discard(str(path))
                self.stats["cutscene_buffers_skipped"] = self.stats.get("cutscene_buffers_skipped", 0) + 1
                self.stats["cutscene_buffers_deleted"] = self.stats.get("cutscene_buffers_deleted", 0) + 1
                if quarantined <= 20:
                    self.log(f"Startup cleanup quarantined: {path.name} ({reason}) -> {destination}")
            except Exception as exc:
                failed += 1
                if failed <= 20:
                    self.log(f"Startup cleanup could not quarantine {path.name}: {exc}")

            if scanned % 250 == 0:
                self.log(
                    f"Startup cleanup progress: scanned={scanned}, detected={detected}, quarantined={quarantined}"
                )

        if processed_changed:
            try:
                self.processed_log.write_text(
                    "\n".join(sorted(self.processed)) + ("\n" if self.processed else ""),
                    encoding="utf-8"
                )
            except Exception as exc:
                failed += 1
                self.log(f"Startup cleanup could not update processed log: {exc}")

        elapsed = time.time() - started
        self.stats["startup_cleanup_scanned"] = scanned
        self.stats["startup_cleanup_detected"] = detected
        self.stats["startup_cleanup_deleted"] = quarantined
        self.stats["startup_cleanup_failed"] = failed
        self.stats["startup_cleanup_recent_skipped"] = recent_skipped
        self.log(
            "Safe startup cleanup complete: "
            f"scanned={scanned}, detected={detected}, quarantined={quarantined}, "
            f"recent-skipped={recent_skipped}, failed={failed}, elapsed={elapsed:.1f}s"
        )
        if quarantined:
            self.log(f"Quarantine folder: {quarantine_session}")

    def scan_folder(self, initial=False):
        dump_folder = Path(self.cfg["dump_folder"])
        files = sorted([
            p for p in dump_folder.rglob("*")
            if p.is_file()
            and not is_inside_alpha_output_folder(p, dump_folder)
            and is_image_like(p, self.cfg.get("process_tmp_image_files", True))
        ], key=lambda p: p.stat().st_mtime, reverse=True)

        # Strict buffer candidates are classified before they can enter any
        # processing lane. They are held briefly and moved as one bulk session.
        files = self._filter_and_stage_live_buffer_candidates(files)

        if initial:
            self.known_files = {str(p) for p in files}
            with self.queue_lock:
                queued = ({str(p) for p in self.manager_q} |
                          {str(p) for p in self.high_q} |
                          {str(p) for p in self.low_q})
                failed = set(self.failed_this_session)
                self.low_q.extend([
                    p for p in files
                    if str(p) not in self.processed and str(p) not in queued and str(p) not in failed
                ])
                low_count = len(self.low_q)
            self.log(f"Initial index: {len(files)} active files. Low priority queue: {low_count}")
        else:
            new_files = []
            old_candidates = []
            for p in files:
                key = str(p)
                if key not in self.known_files:
                    self.known_files.add(key)
                    if key not in self.processed and key not in self.failed_this_session:
                        new_files.append(p)
                elif key not in self.processed and key not in self.failed_this_session:
                    old_candidates.append(p)

            with self.queue_lock:
                queued = ({str(p) for p in self.manager_q} |
                          {str(p) for p in self.high_q} |
                          {str(p) for p in self.low_q})
                new_files = [p for p in new_files if str(p) not in queued and p.exists()]
                if new_files:
                    if self.cfg.get("prioritize_new_dumps", True):
                        self.high_q.extend(new_files)
                        lane = "HIGH priority"
                    else:
                        self.low_q.extend(new_files)
                        lane = "the normal queue"
                    queued.update(str(p) for p in new_files)
                else:
                    lane = ""
                for p in old_candidates:
                    if str(p) not in queued and p.exists():
                        self.low_q.append(p)
                        queued.add(str(p))
            if new_files:
                self.log(f"New active texture detected: {len(new_files)} file(s) added to {lane}")
        self.update_queue_stats()

    def pop_next_task(self):
        def pop_valid(items):
            while items:
                p = items.pop(0)
                key = str(p)
                if key not in self.processed and key not in self.failed_this_session and p.exists():
                    return p
            return None

        with self.queue_lock:
            p = pop_valid(self.manager_q)
            if p is not None:
                self.update_queue_stats()
                return p, "MANAGER"

            if self.cfg.get("prioritize_new_dumps", True):
                p = pop_valid(self.high_q)
                if p is not None:
                    self.update_queue_stats()
                    return p, "HIGH"
                p = pop_valid(self.low_q)
                if p is not None:
                    self.update_queue_stats()
                    return p, "LOW"
            else:
                # With prioritization disabled, automatic work is a single normal lane.
                # HIGH should normally be empty, but LOW is intentionally consumed first
                # for compatibility with queues created before the setting changed.
                p = pop_valid(self.low_q)
                if p is not None:
                    self.update_queue_stats()
                    return p, "LOW"
                p = pop_valid(self.high_q)
                if p is not None:
                    self.update_queue_stats()
                    return p, "HIGH"
        self.update_queue_stats()
        return None, None

    def update_queue_stats(self):
        with self.queue_lock:
            failed = set(self.failed_this_session)
            manager_len = len([p for p in self.manager_q if str(p) not in self.processed and str(p) not in failed and p.exists()])
            high_len = len([p for p in self.high_q if str(p) not in self.processed and str(p) not in failed and p.exists()])
            low_len = len([p for p in self.low_q if str(p) not in self.processed and str(p) not in failed and p.exists()])
        self.stats["manager_queue_len"] = manager_len
        self.stats["high_queue_len"] = high_len
        self.stats["low_queue_len"] = low_len
        self.stats["queue_len"] = manager_len + high_len + low_len


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
        path = self.rgb_workflow_api_var.get().strip() if hasattr(self, "rgb_workflow_api_var") else ""
        if not path:
            self.log("Auto-detect RGB workflow: no workflow selected.")
            return
        info = detect_comfy_nodes_from_api(path)
        if info.get("best_load"):
            self.rgb_load_node_var.set(str(info["best_load"]))
        if info.get("best_save"):
            self.rgb_save_node_var.set(str(info["best_save"]))
        self.log(f"RGB workflow nodes: Load={info.get('best_load') or '?'} Save={info.get('best_save') or '?'}")
        for warning in info.get("warnings", []):
            self.log(f"RGB workflow: {warning}")

    def apply_rgb_api_to_all_profiles(self):
        path_text = self.rgb_workflow_api_var.get().strip()
        if not path_text:
            messagebox.showerror("Apply RGB API", "Select an RGB workflow API JSON file first.")
            return
        workflow_path = Path(path_text)
        if not workflow_path.exists() or not workflow_path.is_file():
            messagebox.showerror("Apply RGB API", "The selected RGB workflow API file does not exist.")
            return
        try:
            workflow_data = load_json(workflow_path)
            if not isinstance(workflow_data, dict):
                raise ValueError("Workflow JSON must contain an object.")
            load_id = int(self.vars.get("load_image_node_id").get().strip())
            save_id = int(self.vars.get("save_image_node_id").get().strip())
        except Exception as exc:
            messagebox.showerror("Apply RGB API", f"Invalid RGB workflow or node IDs:\n{exc}")
            return

        profiles = self.profile_data.setdefault("profiles", {})
        if not profiles:
            messagebox.showinfo("Apply RGB API", "There are no game profiles to update.")
            return

        if not messagebox.askyesno(
            "Apply RGB API to All Profiles",
            f"Apply this RGB API to all {len(profiles)} profile(s)?\n\n{workflow_path}\n\n"
            f"Load node: {load_id}    Save node: {save_id}\n\nThe change will be saved immediately."
        ):
            return

        normalized_path = str(workflow_path.resolve())
        for record in profiles.values():
            settings = record.setdefault("settings", {})
            settings["workflow_api_json"] = normalized_path
            settings["load_image_node_id"] = load_id
            settings["save_image_node_id"] = save_id

        self.profile_data["active_profile"] = self.current_profile_name
        save_profiles_data(self.profile_data)

        self.cfg["workflow_api_json"] = normalized_path
        self.cfg["load_image_node_id"] = load_id
        self.cfg["save_image_node_id"] = save_id
        save_config(self.cfg)

        self.vars["workflow_api_json"].set(normalized_path)
        self.saved_state_var.set("● Saved")
        self.log(f"RGB API applied and saved for all {len(profiles)} profile(s): {normalized_path}")
        messagebox.showinfo(
            "Apply RGB API",
            f"Updated and saved {len(profiles)} profile(s).\n\n"
            f"Load node: {load_id}    Save node: {save_id}"
        )


    def apply_alpha_api_to_all_profiles(self):
        path_text = self.vars.get("alpha_workflow_api_json").get().strip()
        if not path_text:
            messagebox.showerror("Apply Alpha API", "Select an Alpha workflow API JSON file first.")
            return
        workflow_path = Path(path_text)
        if not workflow_path.exists() or not workflow_path.is_file():
            messagebox.showerror("Apply Alpha API", "The selected Alpha workflow API file does not exist.")
            return
        try:
            workflow_data = load_json(workflow_path)
            if not isinstance(workflow_data, dict):
                raise ValueError("Workflow JSON must contain an object.")
            load_id = int(self.vars.get("alpha_load_image_node_id").get().strip())
            save_id = int(self.vars.get("alpha_save_image_node_id").get().strip())
            validate_alpha_comfy_api_workflow(
                workflow_path, str(load_id), str(save_id), require_reachable=True
            )
        except Exception as exc:
            messagebox.showerror("Apply Alpha API", f"Invalid Alpha workflow or node IDs:\n{exc}")
            return

        profiles = self.profile_data.setdefault("profiles", {})
        if not profiles:
            messagebox.showinfo("Apply Alpha API", "There are no game profiles to update.")
            return

        if not messagebox.askyesno(
            "Apply Alpha API to All Profiles",
            f"Apply this Alpha API to all {len(profiles)} profile(s)?\n\n{workflow_path}\n\n"
            f"Load node: {load_id}    Save node: {save_id}\n\nThe change will be saved immediately."
        ):
            return

        normalized_path = str(workflow_path.resolve())
        invert_output = bool(self.alpha_wf_invert_var.get()) if hasattr(self, "alpha_wf_invert_var") else False
        enabled = bool(self.alpha_workflow_var.get()) if hasattr(self, "alpha_workflow_var") else True
        for record in profiles.values():
            settings = record.setdefault("settings", {})
            settings["alpha_workflow_api_json"] = normalized_path
            settings["alpha_load_image_node_id"] = load_id
            settings["alpha_save_image_node_id"] = save_id
            settings["alpha_workflow_invert_output"] = invert_output
            settings["enable_separate_alpha_workflow"] = enabled

        self.profile_data["active_profile"] = self.current_profile_name
        save_profiles_data(self.profile_data)

        self.cfg["alpha_workflow_api_json"] = normalized_path
        self.cfg["alpha_load_image_node_id"] = load_id
        self.cfg["alpha_save_image_node_id"] = save_id
        self.cfg["alpha_workflow_invert_output"] = invert_output
        self.cfg["enable_separate_alpha_workflow"] = enabled
        save_config(self.cfg)

        self.vars["alpha_workflow_api_json"].set(normalized_path)
        self.saved_state_var.set("● Saved")
        self.log(f"Alpha API applied and saved for all {len(profiles)} profile(s): {normalized_path}")
        messagebox.showinfo(
            "Apply Alpha API",
            f"Updated and saved {len(profiles)} profile(s).\n\n"
            f"Load node: {load_id}    Save node: {save_id}"
        )

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
        self.auto_quarantine_efb_cutscenes_on_start()
        self.auto_cleanup_cutscene_dumps_on_start()
        if self.stop_event.is_set():
            self.stats["status"] = "STOPPED"
            self.log("Stopped before initial index.")
            return
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

            # Continuous missing-output recovery is useful for normal watching,
            # but unsafe for one-shot Batch Queue profiles because intentionally
            # skipped textures have no Load output.
            if (not self.cfg.get("batch_queue_mode", False) and
                    self.cfg.get("auto_check_missing_load", True) and
                    now - last_missing_check >= 30):
                self.check_missing_loads()
                last_missing_check = now

            task, priority = self.pop_next_task()
            if not task:
                self.stats["current_priority_lane"] = "IDLE"
                self.stats["status"] = "IDLE"
                if self.cfg.get("batch_queue_mode", False):
                    self.log("Batch profile complete.")
                    break
                time.sleep(0.2)
                continue

            if not stable_file(task):
                with self.queue_lock:
                    if priority == "MANAGER":
                        self.manager_q.insert(0, task)
                    elif priority == "HIGH":
                        self.high_q.insert(0, task)
                    else:
                        self.low_q.insert(0, task)
                self.update_queue_stats()
                time.sleep(0.5)
                continue

            try:
                self.stats["current_priority_lane"] = priority or "LOW"
                if priority == "MANAGER":
                    self.log(f"MANAGER priority: {task.name}")
                elif priority == "HIGH":
                    self.log(f"HIGH priority: {task.name}")
                status = self.process_one(task)
                self.backend_outage_retries.pop(str(task), None)
                if status != "ignored_existing":
                    self.log(f"{status}: {task.name}")
                if status == "cutscene_buffer_skipped" and not task.exists():
                    # A deleted dump may be recreated under the same filename later.
                    # Do not permanently suppress that future file.
                    self.processed.discard(str(task))
                    self.known_files.discard(str(task))
                elif status in TERMINAL_STATES:
                    self.mark_processed(task)
                self.update_queue_stats()
            except Exception as e:
                retry_limit = max(0, int(self.cfg.get("backend_interrupted_job_retries", 1) or 0))
                job_started = float(self.stats.get("current_texture_started_at", 0.0) or 0.0)
                restart_at = float(self.stats.get("backend_last_restart_at", 0.0) or 0.0)
                backend_restarted_during_job = bool(job_started and restart_at >= job_started)
                backend_offline_now = bool(self.comfy_paused or self.stats.get("comfy_online") is False)
                retry_key = str(task)
                retries = int(self.backend_outage_retries.get(retry_key, 0) or 0)
                error_text = str(e).lower()
                deterministic_workflow_error = any(token in error_text for token in (
                    "comfyui workflow failed",
                    "execution error",
                    "kernel size",
                    "calculated padded input size",
                    "wrong size",
                    "saveimage",
                    "output has the wrong size",
                ))

                if (not deterministic_workflow_error and
                        self.cfg.get("auto_restart_backend_when_offline", True) and
                        retry_limit > retries and
                        (backend_restarted_during_job or backend_offline_now)):
                    self.backend_outage_retries[retry_key] = retries + 1
                    with self.queue_lock:
                        if priority == "MANAGER":
                            self.manager_q.insert(0, task)
                        elif priority == "HIGH":
                            self.high_q.insert(0, task)
                        else:
                            self.low_q.insert(0, task)
                    self.update_queue_stats()
                    self.log(
                        f"Backend interruption: requeued {task.name} in {priority or 'LOW'} "
                        f"(retry {retries + 1}/{retry_limit})"
                    )
                    time.sleep(1)
                    continue

                self.log(f"ERROR: {task.name} => {e}")
                self.stats["failed"] = self.stats.get("failed", 0) + 1
                self.failed_this_session.add(str(task))
                # Failed textures are terminal for this worker session so the live
                # scanner cannot immediately re-add them and block Batch Queue
                # completion. They are not written to processed.txt, so a later run
                # or explicit Texture Manager recreation can retry them.
                self.update_queue_stats()
                time.sleep(1)

            if not self.vram_paused and not self.comfy_paused:
                self.stats["status"] = "RUNNING"
        # Flush any final sub-threshold live batch when the watcher or a Batch
        # profile ends, while still preserving reversible quarantine metadata.
        self._flush_live_buffer_candidates(force=True)
        self.stats["current_priority_lane"] = "IDLE"
        self.stats["status"] = "STOPPED"
        self.log("Stopped.")


class TextureComparisonViewer(tk.Toplevel):
    """Non-destructive Original / Clean Heart / Strong Believer viewer."""

    IMAGE_NAMES = ("Original", "Clean Heart", "Strong Believer")

    def __init__(self, app, source_path, cfg):
        super().__init__(app)
        self.app = app
        self.source_path = Path(source_path)
        self.cfg = dict(cfg)
        self.title(f"Compare modes — {self.source_path.name}")
        self.configure(bg="#0b1119")
        self.geometry("1480x880")
        self.minsize(920, 620)
        try:
            self.iconbitmap(str(APP_DIR / "assets" / "faithful_remaster.ico"))
            apply_native_windows_icon(self, APP_DIR / "assets" / "faithful_remaster.ico")
        except Exception:
            pass

        self.images = {}
        self.photos = {}
        self.result_meta = {}
        self.result_queue = queue.Queue()
        self.generation_running = False
        self.closed = False
        self.poll_after = None
        self.render_after = None
        self.zoom = 1.0
        self.center = [0.5, 0.5]
        self.split_ratio = 0.5
        self.drag_kind = None
        self.drag_origin = None
        self.view_var = tk.StringVar(value="Triple")
        self.a_var = tk.StringVar(value="Original")
        self.b_var = tk.StringVar(value="Strong Believer")
        self.opacity_var = tk.DoubleVar(value=50.0)
        self.black_background_var = tk.BooleanVar(
            value=bool(getattr(self.app, "cfg", {}).get("comparison_black_background", False))
        )
        self.zoom_var = tk.StringVar(value="1.00×")
        self.status_var = tk.StringVar(value="Loading original texture…")
        self.detail_var = tk.StringVar(value="Comparison previews never overwrite the game's Load folder.")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._load_original()
        self.after(80, self.generate_missing)

    def _build_ui(self):
        header = tk.Frame(self, bg="#0b1119")
        header.pack(fill="x", padx=16, pady=(14, 6))
        tk.Label(header, text="Mode comparison", bg="#0b1119", fg="#f3f7fa",
                 font=("Segoe UI", 18, "bold"), anchor="w").pack(side="left")
        tk.Label(header, text=self.source_path.name, bg="#0b1119", fg="#7ff0df",
                 font=("Consolas", 10, "bold"), anchor="e").pack(side="right", padx=(12, 0))

        toolbar = tk.Frame(self, bg="#0d151f", highlightthickness=1, highlightbackground="#223141")
        toolbar.pack(fill="x", padx=16, pady=(0, 8))
        top = tk.Frame(toolbar, bg="#0d151f")
        top.pack(fill="x", padx=10, pady=8)
        ttk.Button(top, text="Refresh comparison", style="Accent.TButton",
                   command=lambda: self.generate_missing(force=True)).pack(side="left", padx=(0, 8))
        tk.Label(top, text="View", bg="#0d151f", fg="#8194a7",
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(4, 4))
        view_combo = ttk.Combobox(top, textvariable=self.view_var,
                                  values=("Triple", "Split A/B", "Overlay A/B"),
                                  state="readonly", width=13)
        view_combo.pack(side="left")
        view_combo.bind("<<ComboboxSelected>>", lambda _e: self._switch_view())

        tk.Label(top, text="A", bg="#0d151f", fg="#8194a7",
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(14, 4))
        a_combo = ttk.Combobox(top, textvariable=self.a_var, values=self.IMAGE_NAMES,
                               state="readonly", width=15)
        a_combo.pack(side="left")
        a_combo.bind("<<ComboboxSelected>>", lambda _e: self._schedule_render())
        tk.Label(top, text="B", bg="#0d151f", fg="#8194a7",
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(8, 4))
        b_combo = ttk.Combobox(top, textvariable=self.b_var, values=self.IMAGE_NAMES,
                               state="readonly", width=15)
        b_combo.pack(side="left")
        b_combo.bind("<<ComboboxSelected>>", lambda _e: self._schedule_render())

        self.opacity_label = tk.Label(top, text="Overlay 50%", bg="#0d151f", fg="#8194a7",
                                      font=("Segoe UI", 8, "bold"))
        self.opacity_label.pack(side="left", padx=(14, 4))
        self.opacity_scale = ttk.Scale(top, from_=0, to=100, variable=self.opacity_var,
                                       command=self._opacity_changed, length=130)
        self.opacity_scale.pack(side="left")

        self.black_background_check = ttk.Checkbutton(
            top, text="Black background", variable=self.black_background_var,
            command=self._background_changed
        )
        self.black_background_check.pack(side="left", padx=(14, 4))

        ttk.Button(top, text="−", style="Compact.TButton", width=3,
                   command=lambda: self._zoom_by(1/1.25)).pack(side="right", padx=2)
        ttk.Button(top, text="Fit", style="Compact.TButton",
                   command=self._fit).pack(side="right", padx=2)
        ttk.Button(top, text="+", style="Compact.TButton", width=3,
                   command=lambda: self._zoom_by(1.25)).pack(side="right", padx=2)
        tk.Label(top, textvariable=self.zoom_var, bg="#0d151f", fg="#e3ebf1",
                 font=("Consolas", 9, "bold"), width=7).pack(side="right", padx=(8, 2))

        status = tk.Frame(toolbar, bg="#101923")
        status.pack(fill="x")
        tk.Label(status, textvariable=self.status_var, bg="#101923", fg="#7ff0df",
                 font=("Segoe UI", 9, "bold"), anchor="w", padx=10, pady=6).pack(side="left", fill="x", expand=True)
        tk.Label(status, textvariable=self.detail_var, bg="#101923", fg="#8ea2b3",
                 font=("Segoe UI", 8), anchor="e", padx=10, pady=6).pack(side="right")

        self.viewer_host = tk.Frame(self, bg="#070b10")
        self.viewer_host.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        self.triple_frame = tk.Frame(self.viewer_host, bg="#070b10")
        for col in range(3):
            self.triple_frame.grid_columnconfigure(col, weight=1, uniform="compare")
        self.triple_frame.grid_rowconfigure(1, weight=1)
        self.triple_canvases = {}
        for col, name in enumerate(self.IMAGE_NAMES):
            tk.Label(self.triple_frame, text=name.upper(), bg="#0d151f", fg="#f0f5f8",
                     font=("Segoe UI", 9, "bold"), pady=6).grid(
                         row=0, column=col, sticky="ew", padx=(0 if col == 0 else 4, 0 if col == 2 else 4)
                     )
            canvas = tk.Canvas(self.triple_frame, bg="#05080c", highlightthickness=1,
                               highlightbackground="#273747", cursor="fleur")
            canvas.grid(row=1, column=col, sticky="nsew",
                        padx=(0 if col == 0 else 4, 0 if col == 2 else 4))
            self._bind_canvas(canvas, split=False)
            self.triple_canvases[name] = canvas

        self.single_frame = tk.Frame(self.viewer_host, bg="#070b10")
        self.single_frame.grid_columnconfigure(0, weight=1)
        self.single_frame.grid_rowconfigure(0, weight=1)
        self.single_canvas = tk.Canvas(self.single_frame, bg="#05080c", highlightthickness=1,
                                       highlightbackground="#273747", cursor="fleur")
        self.single_canvas.grid(row=0, column=0, sticky="nsew")
        self._bind_canvas(self.single_canvas, split=True)
        self._switch_view()

    def _bind_canvas(self, canvas, split=False):
        canvas.bind("<Configure>", lambda _e: self._schedule_render())
        canvas.bind("<MouseWheel>", self._mouse_wheel)
        canvas.bind("<Button-4>", lambda _e: self._zoom_by(1.25))
        canvas.bind("<Button-5>", lambda _e: self._zoom_by(1/1.25))
        canvas.bind("<ButtonPress-1>", lambda e, c=canvas, s=split: self._drag_start(e, c, s))
        canvas.bind("<B1-Motion>", lambda e, c=canvas: self._drag_move(e, c))
        canvas.bind("<ButtonRelease-1>", lambda _e: self._drag_end())
        canvas.bind("<Double-Button-1>", lambda _e: self._fit())

    def _load_original(self):
        if not PIL_AVAILABLE:
            self.status_var.set("Pillow is required for comparison previews.")
            return
        try:
            with Image.open(self.source_path) as image:
                self.images["Original"] = image.convert("RGBA").copy()
            self.status_var.set("Original loaded — generating missing mode previews…")
            self._schedule_render()
        except Exception as exc:
            self.status_var.set(f"Could not load original: {exc}")

    def _switch_view(self):
        self.triple_frame.pack_forget()
        self.single_frame.pack_forget()
        if self.view_var.get() == "Triple":
            self.triple_frame.pack(fill="both", expand=True)
        else:
            self.single_frame.pack(fill="both", expand=True)
        overlay = self.view_var.get() == "Overlay A/B"
        state = "normal" if overlay else "disabled"
        try:
            self.opacity_scale.state(["!disabled"] if overlay else ["disabled"])
        except Exception:
            pass
        self.opacity_label.configure(fg="#8194a7" if overlay else "#526272")
        self._schedule_render()

    def _opacity_changed(self, _value=None):
        self.opacity_label.configure(text=f"Overlay {int(round(self.opacity_var.get()))}%")
        self._schedule_render()

    def _background_changed(self):
        enabled = bool(self.black_background_var.get())
        try:
            self.app.cfg["comparison_black_background"] = enabled
            save_config(self.app.cfg)
        except Exception as exc:
            try:
                self.app.log(f"Could not save comparison background preference: {exc}")
            except Exception:
                pass
        self._schedule_render()

    def _mouse_wheel(self, event):
        self._zoom_by(1.25 if event.delta > 0 else 1/1.25)
        return "break"

    def _zoom_by(self, factor):
        self.zoom = min(24.0, max(0.25, self.zoom * float(factor)))
        self.zoom_var.set(f"{self.zoom:.2f}×")
        self._schedule_render()

    def _fit(self):
        self.zoom = 1.0
        self.center[:] = [0.5, 0.5]
        self.zoom_var.set("1.00×")
        self._schedule_render()

    def _drag_start(self, event, canvas, split_canvas):
        self.drag_origin = (event.x, event.y)
        if split_canvas and self.view_var.get() == "Split A/B":
            divider = int(max(1, canvas.winfo_width()) * self.split_ratio)
            if abs(event.x - divider) <= 14:
                self.drag_kind = "divider"
                canvas.configure(cursor="sb_h_double_arrow")
                return
        self.drag_kind = "pan"
        canvas.configure(cursor="fleur")

    def _drag_move(self, event, canvas):
        if not self.drag_origin or not self.drag_kind:
            return
        if self.drag_kind == "divider":
            width = max(1, canvas.winfo_width())
            self.split_ratio = min(0.95, max(0.05, event.x / width))
            self.drag_origin = (event.x, event.y)
            self._schedule_render()
            return
        old_x, old_y = self.drag_origin
        dx, dy = event.x - old_x, event.y - old_y
        width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        # A normalized center keeps pan synchronized across different resolutions.
        sensitivity = max(1.0, self.zoom)
        self.center[0] = min(1.0, max(0.0, self.center[0] - dx / (width * sensitivity)))
        self.center[1] = min(1.0, max(0.0, self.center[1] - dy / (height * sensitivity)))
        self.drag_origin = (event.x, event.y)
        self._schedule_render()

    def _drag_end(self):
        self.drag_kind = None
        self.drag_origin = None
        try:
            self.single_canvas.configure(cursor="fleur")
        except Exception:
            pass

    def _checkerboard(self, width, height):
        from PIL import ImageDraw
        image = Image.new("RGBA", (width, height), (20, 25, 31, 255))
        draw = ImageDraw.Draw(image)
        tile = 16
        c1, c2 = (27, 34, 42, 255), (42, 50, 59, 255)
        for y in range(0, height, tile):
            for x in range(0, width, tile):
                draw.rectangle((x, y, min(width, x+tile), min(height, y+tile)),
                               fill=c1 if ((x//tile + y//tile) % 2 == 0) else c2)
        return image

    def _preview_background(self, width, height):
        if bool(self.black_background_var.get()):
            return Image.new("RGBA", (width, height), (0, 0, 0, 255))
        return self._checkerboard(width, height)

    def _render_panel(self, image, width, height):
        width, height = max(48, int(width)), max(48, int(height))
        base = self._preview_background(width, height)
        if image is None:
            return base
        source = image.convert("RGBA")
        iw, ih = source.size
        pad = 12
        avail_w, avail_h = max(1, width - 2*pad), max(1, height - 2*pad)
        fit_scale = min(avail_w / max(1, iw), avail_h / max(1, ih))
        scale = max(1e-6, fit_scale * self.zoom)
        scaled_w, scaled_h = iw * scale, ih * scale
        if scaled_w <= avail_w and scaled_h <= avail_h:
            resized = source.resize((max(1, round(scaled_w)), max(1, round(scaled_h))), Image.Resampling.LANCZOS)
            x, y = (width - resized.width)//2, (height - resized.height)//2
            base.alpha_composite(resized, (x, y))
            return base

        visible_w = min(iw, avail_w / scale)
        visible_h = min(ih, avail_h / scale)
        cx, cy = self.center[0] * iw, self.center[1] * ih
        left = min(max(0.0, cx - visible_w/2), max(0.0, iw - visible_w))
        top = min(max(0.0, cy - visible_h/2), max(0.0, ih - visible_h))
        right, bottom = left + visible_w, top + visible_h
        cropped = source.crop((left, top, right, bottom))
        render_w = max(1, min(avail_w, round(cropped.width * scale)))
        render_h = max(1, min(avail_h, round(cropped.height * scale)))
        resized = cropped.resize((render_w, render_h), Image.Resampling.LANCZOS)
        x, y = (width - render_w)//2, (height - render_h)//2
        base.alpha_composite(resized, (x, y))
        return base

    def _draw_photo(self, canvas, image, key):
        canvas.delete("all")
        photo = ImageTk.PhotoImage(image)
        self.photos[key] = photo
        canvas.create_image(canvas.winfo_width()//2, canvas.winfo_height()//2,
                            image=photo, anchor="center")

    def _render(self):
        self.render_after = None
        if self.closed or not PIL_AVAILABLE:
            return
        try:
            if self.view_var.get() == "Triple":
                for name, canvas in self.triple_canvases.items():
                    width, height = max(48, canvas.winfo_width()), max(48, canvas.winfo_height())
                    panel = self._render_panel(self.images.get(name), width, height)
                    self._draw_photo(canvas, panel, f"triple:{name}")
                    if name not in self.images:
                        canvas.create_text(width//2, height//2, text="Generating…" if self.generation_running else "Not generated",
                                           fill="#91a4b7", font=("Segoe UI", 10, "bold"))
            else:
                canvas = self.single_canvas
                width, height = max(48, canvas.winfo_width()), max(48, canvas.winfo_height())
                a_name, b_name = self.a_var.get(), self.b_var.get()
                a_panel = self._render_panel(self.images.get(a_name), width, height)
                b_panel = self._render_panel(self.images.get(b_name), width, height)
                if self.view_var.get() == "Split A/B":
                    divider = int(width * self.split_ratio)
                    composite = a_panel.copy()
                    if divider < width:
                        composite.paste(b_panel.crop((divider, 0, width, height)), (divider, 0))
                    self._draw_photo(canvas, composite, "single")
                    canvas.create_line(divider, 0, divider, height, fill="#7ff0df", width=3)
                    canvas.create_text(12, 12, text=a_name, anchor="nw", fill="#ffffff",
                                       font=("Segoe UI", 9, "bold"))
                    canvas.create_text(width-12, 12, text=b_name, anchor="ne", fill="#ffffff",
                                       font=("Segoe UI", 9, "bold"))
                else:
                    amount = min(1.0, max(0.0, self.opacity_var.get()/100.0))
                    composite = Image.blend(a_panel.convert("RGBA"), b_panel.convert("RGBA"), amount)
                    self._draw_photo(canvas, composite, "single")
                    canvas.create_text(12, 12, text=f"{a_name}  ← {100-int(amount*100)}%",
                                       anchor="nw", fill="#ffffff", font=("Segoe UI", 9, "bold"))
                    canvas.create_text(width-12, 12, text=f"{int(amount*100)}% →  {b_name}",
                                       anchor="ne", fill="#ffffff", font=("Segoe UI", 9, "bold"))
        except Exception as exc:
            self.status_var.set(f"Preview rendering error: {exc}")

    def _schedule_render(self):
        if self.closed:
            return
        if self.render_after is not None:
            try:
                self.after_cancel(self.render_after)
            except Exception:
                pass
        self.render_after = self.after(40, self._render)

    def generate_missing(self, force=False):
        if self.generation_running:
            return
        if not hasattr(self.app, "generate_mode_comparison_output") and "generate_mode_comparison_output" not in globals():
            self.status_var.set("Comparison processing layer is unavailable.")
            return
        self.generation_running = True
        self.status_var.set("Preparing comparison jobs…")
        self.detail_var.set("Clean Heart and Strong Believer are generated without touching Load outputs.")
        if force:
            self.images.pop("Clean Heart", None)
            self.images.pop("Strong Believer", None)
            self.result_meta.clear()
        self._schedule_render()

        # Match the normal auto-start behavior. The generation thread will report
        # a clear error if a workflow-specific backend remains offline.
        try:
            if self.cfg.get("auto_start_comfy_when_watching", True):
                self.app.ensure_comfy_started_for_watching(self.cfg)
        except Exception:
            pass

        def worker():
            errors = []
            for index, preset in enumerate(("Clean Heart", "Strong Believer"), start=1):
                if self.closed:
                    break
                self.result_queue.put(("status", f"Generating {preset} ({index}/2)…"))
                try:
                    result = generate_mode_comparison_output(
                        self.source_path, self.cfg, preset, force=force,
                        log=lambda text: self.result_queue.put(("log", text)),
                    )
                    with Image.open(result["path"]) as image:
                        rgba = image.convert("RGBA").copy()
                    self.result_queue.put(("image", preset, rgba, result))
                except Exception as exc:
                    errors.append(f"{preset}: {exc}")
                    self.result_queue.put(("error", preset, str(exc)))
            self.result_queue.put(("done", errors))

        threading.Thread(target=worker, daemon=True, name="ModeComparisonGenerator").start()
        self._poll_results()

    def _poll_results(self):
        self.poll_after = None
        if self.closed:
            return
        handled = 0
        while handled < 20:
            try:
                item = self.result_queue.get_nowait()
            except queue.Empty:
                break
            handled += 1
            kind = item[0]
            if kind == "status":
                self.status_var.set(item[1])
            elif kind == "log":
                self.app.log(item[1])
            elif kind == "image":
                _kind, preset, rgba, meta = item
                self.images[preset] = rgba
                self.result_meta[preset] = meta
                source = "cache" if meta.get("cache_hit") else "new"
                alpha_note = " • alpha fallback" if meta.get("alpha_fallback") else ""
                self.detail_var.set(f"{preset}: {rgba.width}×{rgba.height} • {source}{alpha_note}")
                self._schedule_render()
            elif kind == "error":
                _kind, preset, text = item
                self.app.log(f"Comparison {preset} ERROR: {text}")
            elif kind == "done":
                errors = item[1]
                self.generation_running = False
                if errors:
                    self.status_var.set("Comparison completed with errors")
                    self.detail_var.set(" | ".join(errors)[:350])
                else:
                    cached = sum(1 for m in self.result_meta.values() if m.get("cache_hit"))
                    self.status_var.set("Comparison ready")
                    self.detail_var.set(
                        f"Original + Clean Heart + Strong Believer • {cached}/2 mode previews reused from cache"
                    )
                self._schedule_render()
        if self.generation_running or not self.result_queue.empty():
            self.poll_after = self.after(100, self._poll_results)

    def _close(self):
        self.closed = True
        for after_id in (self.poll_after, self.render_after):
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except Exception:
                    pass
        try:
            if getattr(self.app, "manager_compare_window", None) is self:
                self.app.manager_compare_window = None
        except Exception:
            pass
        self.destroy()

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x860")
        self.apply_dark_theme()
        self.cfg = load_config()
        self.profile_data = load_profiles_data()
        self.current_profile_name = self.profile_data.get("active_profile", "")
        self.log_q = queue.Queue()
        self.stop_event = threading.Event()
        self.force_scan_event = threading.Event()
        self.force_missing_event = threading.Event()
        self.worker_thread = None
        self.worker = None
        self.batch_state = load_batch_queue_state()
        known_profiles = self.profile_data.get("profiles", {})
        self.batch_queue = [name for name in self.batch_state.get("queue", []) if name in known_profiles]
        self.batch_active = False
        self.batch_current_index = -1
        self.batch_shutdown_requested = False
        self.vars = {}
        self.stats = {
            "processed":0, "cache_hits":0, "comfy_jobs":0, "queue_len":0,
            "high_queue_len":0, "low_queue_len":0, "peak_vram_mb":0,
            "status":"STOPPED", "comfy_online": False, "comfy_running": None, "comfy_pending": None, "comfy_error": "", "exceptions_skipped": 0, "cutscene_buffers_skipped": 0, "cutscene_buffers_deleted": 0, "startup_cleanup_deleted": 0
        }
        self.build()
        if MIGRATED_ITEMS:
            self.log(f"Migrated {len(MIGRATED_ITEMS)} legacy data item(s) to {DATA_DIR}")
        else:
            self.log(f"Using persistent data folder: {DATA_DIR}")
        self.after(200, self.poll_log)
        self.after(1000, self.update_dashboard)
        self.after(1400, lambda: self.refresh_azahar_metadata(manual=False))
        self.after(3000, self.auto_check_comfy)

    def apply_dark_theme(self):
        """Apply the v11.8 polished dark dashboard theme."""
        self.configure(bg="#070b11")
        try:
            self.option_add("*Font", "{Segoe UI} 9")
            self.option_add("*Background", "#0b1119")
            self.option_add("*Foreground", "#e6edf3")
            self.option_add("*Entry.Background", "#0f1722")
            self.option_add("*Entry.Foreground", "#e6edf3")
            self.option_add("*Entry.InsertBackground", "#ffffff")
            self.option_add("*Button.Background", "#182331")
            self.option_add("*Button.Foreground", "#e6edf3")
            self.option_add("*Button.ActiveBackground", "#223246")
            self.option_add("*Button.ActiveForeground", "#ffffff")
            self.option_add("*Checkbutton.Background", "#0b1119")
            self.option_add("*Checkbutton.Foreground", "#e6edf3")
            self.option_add("*Checkbutton.ActiveBackground", "#0b1119")
            self.option_add("*Checkbutton.ActiveForeground", "#ffffff")
            self.option_add("*Radiobutton.Background", "#0b1119")
            self.option_add("*Radiobutton.Foreground", "#e6edf3")
            self.option_add("*LabelFrame.Background", "#0b1119")
            self.option_add("*LabelFrame.Foreground", "#8adbd1")
            self.option_add("*Listbox.Background", "#091019")
            self.option_add("*Listbox.Foreground", "#d7e0e8")
            self.option_add("*Listbox.SelectBackground", "#0f766e")
            self.option_add("*Listbox.SelectForeground", "#ffffff")
            self.option_add("*Text.Background", "#070c12")
            self.option_add("*Text.Foreground", "#d7e0e8")

            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except Exception:
                pass
            style.configure(".", background="#0b1119", foreground="#e6edf3")
            style.configure("TFrame", background="#0b1119")
            style.configure("TLabel", background="#0b1119", foreground="#e6edf3")
            style.configure("TNotebook", background="#070b11", borderwidth=0, tabmargins=(6, 6, 6, 0))
            style.configure(
                "TNotebook.Tab", background="#101923", foreground="#8fa2b5",
                padding=(18, 9), borderwidth=0, font=("Segoe UI", 10, "bold")
            )
            style.map(
                "TNotebook.Tab",
                background=[("selected", "#162534"), ("active", "#13202c")],
                foreground=[("selected", "#eafdf9"), ("active", "#c9f7ef")]
            )
            style.configure("TCombobox", fieldbackground="#0f1722", background="#182331", foreground="#e6edf3", arrowcolor="#8adbd1")
            style.map("TCombobox", fieldbackground=[("readonly", "#0f1722")], foreground=[("readonly", "#e6edf3")])
            style.configure("Horizontal.TProgressbar", troughcolor="#111a25", background="#20b8aa", bordercolor="#111a25", lightcolor="#20b8aa", darkcolor="#20b8aa", thickness=16)
            style.configure("Accent.TButton", background="#087f77", foreground="#ffffff", padding=(14, 8), font=("Segoe UI", 9, "bold"), borderwidth=0)
            style.map("Accent.TButton", background=[("active", "#0a9b90"), ("disabled", "#24443f")])
            style.configure("Secondary.TButton", background="#1a2634", foreground="#e6edf3", padding=(12, 8), borderwidth=0)
            style.map("Secondary.TButton", background=[("active", "#25364a")])
            style.configure("Danger.TButton", background="#6f2528", foreground="#ffe8e8", padding=(12, 8), borderwidth=0)
            style.map("Danger.TButton", background=[("active", "#943238")])
            style.configure("Compact.TButton", background="#15212e", foreground="#dce7ef", padding=(8, 5), borderwidth=0)
            style.map("Compact.TButton", background=[("active", "#203247")])
            style.configure("Settings.TNotebook.Tab", padding=(14, 7), font=("Segoe UI", 9, "bold"))
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
        self.dynamic_efb_filter_var = tk.BooleanVar(value=bool(self.cfg.get("skip_dynamic_efb_postprocess", True)))
        self.delete_cutscene_var = tk.BooleanVar(value=bool(self.cfg.get("delete_skipped_cutscene_buffers", False)))
        self.auto_cleanup_cutscene_var = tk.BooleanVar(value=bool(self.cfg.get("auto_scan_delete_cutscene_buffers_on_start", False)))
        tk.Checkbutton(
            filter_opts,
            text="Skip cutscene buffers and empty/black dumps",
            variable=self.cutscene_filter_var
        ).pack(side="left")
        tk.Checkbutton(
            filter_opts,
            text="Skip dynamic EFB / post-processing dumps",
            variable=self.dynamic_efb_filter_var
        ).pack(side="left", padx=(20,0))
        tk.Checkbutton(
            filter_opts,
            text="Quarantine skipped cutscene / blank dumps",
            variable=self.delete_cutscene_var
        ).pack(side="left", padx=(20,0))
        tk.Checkbutton(
            filter_opts,
            text="Safe startup blank-dump quarantine",
            variable=self.auto_cleanup_cutscene_var
        ).pack(side="left", padx=(20,0))

        status_opts = tk.Frame(top); status_opts.pack(fill="x", padx=10, pady=5)
        self.comfy_status_var = tk.BooleanVar(value=bool(self.cfg.get("enable_comfy_status", True)))
        self.pause_comfy_var = tk.BooleanVar(value=bool(self.cfg.get("pause_when_comfy_offline", True)))
        self.auto_start_comfy_var = tk.BooleanVar(value=bool(self.cfg.get("auto_start_comfy_when_watching", True)))
        tk.Checkbutton(status_opts, text="Monitor Comfy status", variable=self.comfy_status_var).pack(side="left")
        tk.Checkbutton(status_opts, text="Pause when Comfy offline", variable=self.pause_comfy_var).pack(side="left", padx=(20,0))
        tk.Checkbutton(status_opts, text="Auto-start Comfy if offline", variable=self.auto_start_comfy_var).pack(side="left", padx=(20,0))
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
        self.vars["alpha_save_image_node_id"] = tk.StringVar(value=str(self.cfg.get("alpha_save_image_node_id", 5)))
        tk.Entry(alpha_wf_ids, textvariable=self.vars["alpha_save_image_node_id"], width=8).pack(side="left", padx=5)
        self.alpha_wf_invert_var = tk.BooleanVar(value=bool(self.cfg.get("alpha_workflow_invert_output", False)))
        tk.Checkbutton(alpha_wf_ids, text="Invert alpha output", variable=self.alpha_wf_invert_var).pack(side="left", padx=(20,0))
        tk.Label(alpha_wf_ids, text="alpha image should be white=visible, black=transparent").pack(side="left", padx=10)

        vram = tk.Frame(top); vram.pack(fill="x", padx=10, pady=5)
        self.vram_var = tk.BooleanVar(value=bool(self.cfg.get("enable_vram_protection", False)))
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
            cfg = self.configure_profile_runtime_paths(self.collect())
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

        right_host = tk.Frame(win, bg="#0b111a")
        right_host.pack(side="right", fill="y", padx=(0, 8), pady=8)
        right_canvas = tk.Canvas(right_host, bg="#0b111a", highlightthickness=0, width=390)
        right_scrollbar = tk.Scrollbar(right_host, orient="vertical", command=right_canvas.yview, width=16)
        right_canvas.configure(yscrollcommand=right_scrollbar.set)
        right_scrollbar.pack(side="right", fill="y")
        right_canvas.pack(side="left", fill="both", expand=True)
        right = tk.Frame(right_canvas, bg="#0b111a")
        right_window = right_canvas.create_window((0, 0), window=right, anchor="nw")

        def _sync_right_scroll_region(event=None):
            right_canvas.configure(scrollregion=right_canvas.bbox("all"))
            try:
                right_canvas.itemconfigure(right_window, width=right_canvas.winfo_width())
            except Exception:
                pass

        def _right_mousewheel(event):
            # Keep listbox scrolling independent when the pointer is over it.
            widget = win.winfo_containing(event.x_root, event.y_root)
            if widget is lb or str(widget).startswith(str(lb)):
                return
            delta = -1 if event.delta > 0 else 1
            right_canvas.yview_scroll(delta * 3, "units")
            return "break"

        right.bind("<Configure>", _sync_right_scroll_region)
        right_canvas.bind("<Configure>", _sync_right_scroll_region)
        right_canvas.bind("<Enter>", lambda e: win.bind_all("<MouseWheel>", _right_mousewheel))
        right_canvas.bind("<Leave>", lambda e: win.unbind_all("<MouseWheel>"))

        search_var = tk.StringVar()
        exceptions_only_var = tk.BooleanVar(value=False)
        tk.Label(left, text="Dump textures", bg="#0b111a", fg="#93c5fd", font="SegoeUI 12 bold").pack(anchor="w")
        filter_row = tk.Frame(left, bg="#0b111a")
        filter_row.pack(fill="x", pady=(4,6))
        tk.Entry(filter_row, textvariable=search_var, bg="#111827", fg="#e5e7eb").pack(side="left", fill="x", expand=True)
        exceptions_button = tk.Button(filter_row, text="Show Exceptions Only")
        exceptions_button.pack(side="left", padx=(6,0))

        list_frame = tk.Frame(left)
        list_frame.pack(fill="both", expand=True)
        lb = tk.Listbox(list_frame, bg="#020617", fg="#e5e7eb", selectbackground="#1d4ed8", selectmode="extended", exportselection=False)
        lb.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(list_frame, command=lb.yview)
        sb.pack(side="right", fill="y")
        lb.config(yscrollcommand=sb.set)

        info_var = tk.StringVar(value="Select a texture")
        tk.Label(right, textvariable=info_var, bg="#0b111a", fg="#e5e7eb", wraplength=360, justify="left").pack(anchor="w", pady=(0,8))

        profile_dir = PROFILES_DIR / safe_profile_name(self.current_profile_name)
        profile_dir.mkdir(parents=True, exist_ok=True)
        texture_preset_overrides = load_texture_preset_overrides(profile_dir)
        game_default_preset = normalize_faithfulness_preset(cfg.get("faithfulness_preset", "Clean Heart"))
        preset_box = tk.LabelFrame(right, text="Faithfulness preset", bg="#0b111a", fg="#93c5fd")
        preset_box.pack(fill="x", pady=(0, 8))
        texture_preset_var = tk.StringVar(value=TEXTURE_PRESET_INHERIT)
        texture_preset_combo = ttk.Combobox(
            preset_box, textvariable=texture_preset_var,
            values=(TEXTURE_PRESET_INHERIT,) + workflow_profile_names(), state="readonly"
        )
        texture_preset_combo.pack(fill="x", padx=6, pady=6)
        preset_status_var = tk.StringVar(value=f"Game default: {game_default_preset}")
        tk.Label(preset_box, textvariable=preset_status_var, bg="#0b111a", fg="#cbd5e1", anchor="w").pack(fill="x", padx=6, pady=(0,6))

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
                if p.is_file() and not is_inside_alpha_output_folder(p, dump_folder) and is_image_like(p, cfg.get("process_tmp_image_files", True))
            ]
            all_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            if q:
                all_files = [p for p in all_files if q in p.name.lower() or q in str(p).lower()]
            if exceptions_only_var.get():
                all_files = [p for p in all_files if is_exception_texture(p)]
            files = all_files
            lb.delete(0, "end")
            for p in files[:5000]:
                out = routed_output_path_for_input(p, dump_folder, load_folder, cfg)
                tag = "LOAD" if out.exists() else "MISSING"
                exc = "EXC" if is_exception_texture(p) else ""
                lb.insert("end", f"[{tag}] {exc} {p.name}")

        def toggle_exceptions_only():
            enabled = not exceptions_only_var.get()
            exceptions_only_var.set(enabled)
            exceptions_button.configure(
                text="Show All Textures" if enabled else "Show Exceptions Only",
                relief="sunken" if enabled else "raised"
            )
            refresh()

        exceptions_button.configure(command=toggle_exceptions_only)

        def selected_paths():
            out = []
            for idx in lb.curselection():
                if 0 <= idx < len(files):
                    out.append(files[idx])
            return out

        def selected_path():
            paths = selected_paths()
            return paths[0] if paths else None

        def update_preview(event=None):
            p = selected_path()
            if not p:
                return
            out = output_path_for_input(p, dump_folder, load_folder)
            key = texture_override_key(p, dump_folder)
            override = texture_preset_overrides.get(key)
            override_profile = find_workflow_profile(override) if override else None
            effective = override_profile['name'] if override_profile else game_default_preset
            texture_preset_var.set(override_profile['name'] if override_profile else TEXTURE_PRESET_INHERIT)
            preset_status_var.set(f"Effective profile: {effective}" + (" (texture override)" if override_profile else " (game default)"))
            info_var.set(f"Dump: {p}\nLoad: {out}\nLoad exists: {out.exists()}\nFaithfulness: {effective}\nException: {is_exception_texture(p)}")
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

        def remove_from_exceptions():
            p = selected_path()
            if not p:
                return
            removed = remove_exception_patterns_for_texture(p)
            if not removed:
                messagebox.showinfo(
                    "Remove from exceptions",
                    "The selected texture is not currently matched by any exception pattern.",
                    parent=win,
                )
                return
            self.log(
                f"Removed from exceptions: {p.name} "
                f"({len(removed)} pattern{'s' if len(removed) != 1 else ''})"
            )
            refresh()
            update_preview()

        def delete_load():
            p = selected_path()
            if not p:
                return
            out = routed_output_path_for_input(p, dump_folder, load_folder, cfg)
            if out.exists():
                out.unlink()
                self.log(f"Deleted load texture: {out.name}")
            refresh()
            update_preview()

        def apply_selected_texture_preset(clear=False):
            paths = selected_paths()
            if not paths:
                messagebox.showinfo("Faithfulness preset", "Select one or more textures first.", parent=win)
                return False
            chosen = texture_preset_var.get()
            if clear or chosen == TEXTURE_PRESET_INHERIT:
                for p in paths:
                    texture_preset_overrides.pop(texture_override_key(p, dump_folder), None)
                action = "Cleared preset override for"
            else:
                chosen = normalize_faithfulness_preset(chosen)
                chosen_profile = find_workflow_profile_exact(chosen)
                if not chosen_profile:
                    messagebox.showerror("Workflow profile", f"Profile not found: {chosen}", parent=win)
                    return False
                for p in paths:
                    texture_preset_overrides[texture_override_key(p, dump_folder)] = chosen_profile['id']
                action = f"Assigned {chosen_profile['name']} to"
            save_texture_preset_overrides(profile_dir, texture_preset_overrides)
            if getattr(self, "worker", None) is not None:
                try:
                    self.worker.texture_preset_overrides = dict(texture_preset_overrides)
                except Exception:
                    pass
            self.log(f"{action} {len(paths)} texture(s).")
            update_preview()
            return True

        def recreate_with_selected_preset():
            if not apply_selected_texture_preset(clear=False):
                return
            recreate()

        def recreate():
            paths = selected_paths()
            if not paths:
                messagebox.showinfo("Recreate selected textures", "Select one or more textures first.", parent=win)
                return
            if not messagebox.askyesno(
                "Recreate selected textures",
                f"Recreate {len(paths)} selected texture(s)?\n\n"
                "This will delete their current remastered outputs, clear their processed records and hash-cache entries, then queue them again.\n\n"
                "Original dump files will not be deleted.",
                parent=win,
            ):
                return

            processed_log = Path(cfg.get("processed_log") or (DATA_DIR / "processed.txt"))
            processed_lines = []
            if processed_log.exists():
                try:
                    processed_lines = [x.strip() for x in processed_log.read_text(encoding="utf-8").splitlines() if x.strip()]
                except Exception:
                    processed_lines = []
            selected_strings = {str(p) for p in paths}
            remaining_lines = [x for x in processed_lines if x not in selected_strings]

            cache_index = load_cache_index()
            deleted_outputs = 0
            cleared_cache = 0
            failures = []

            for p in paths:
                try:
                    out = routed_output_path_for_input(p, dump_folder, load_folder, cfg)
                    if out.exists():
                        out.unlink()
                        deleted_outputs += 1

                    digest = sha1_file(p) if p.exists() else None
                    preset_digests = []
                    if digest:
                        preset_digests.append(digest)
                        for preset in workflow_profile_names(False):
                            _profile = find_workflow_profile(preset)
                            _fingerprint = workflow_profile_fingerprint(_profile) if _profile else preset
                            preset_digests.append(
                                hashlib.sha1(f"{digest}|{preset}|{_fingerprint}".encode("utf-8")).hexdigest()
                            )
                        for cache_digest in preset_digests:
                            cache_file = CACHE_DIR / f"{cache_digest}.png"
                            if cache_file.exists():
                                cache_file.unlink()
                                cleared_cache += 1
                            cache_index.pop(cache_digest, None)

                    active_worker = getattr(self, "worker", None)
                    if active_worker is not None:
                        try:
                            active_worker.unmark_processed(p)
                            if digest:
                                for cache_digest in preset_digests:
                                    active_worker.cache_index.pop(cache_digest, None)
                        except Exception:
                            pass
                except Exception as e:
                    failures.append(f"{p.name}: {e}")

            try:
                processed_log.parent.mkdir(parents=True, exist_ok=True)
                processed_log.write_text(
                    "\n".join(remaining_lines) + ("\n" if remaining_lines else ""),
                    encoding="utf-8",
                )
            except Exception as e:
                failures.append(f"processed.txt: {e}")

            try:
                save_cache_index(cache_index)
            except Exception as e:
                failures.append(f"cache_index.json: {e}")

            self.force_missing_event.set()
            self.force_scan_event.set()
            self.log(
                f"Recreate requested for {len(paths)} texture(s): "
                f"{deleted_outputs} output(s) deleted, {cleared_cache} cache file(s) cleared."
            )
            refresh()
            update_preview()

            summary = (
                f"Prepared {len(paths)} texture(s) for recreation.\n\n"
                f"Deleted outputs: {deleted_outputs}\n"
                f"Cleared hash-cache files: {cleared_cache}\n"
                f"Failures: {len(failures)}"
            )
            if failures:
                summary += "\n\n" + "\n".join(failures[:10])
            if not (self.worker_thread and self.worker_thread.is_alive()):
                summary += "\n\nStart watching or run Check Missing Load to process them."
            messagebox.showinfo("Recreate selected textures", summary, parent=win)

        def open_dump_folder():
            p = selected_path()
            if p:
                os.startfile(str(p.parent))

        def open_load_folder():
            p = selected_path()
            if p:
                out = routed_output_path_for_input(p, dump_folder, load_folder, cfg)
                out.parent.mkdir(parents=True, exist_ok=True)
                os.startfile(str(out.parent))

        btns = tk.Frame(right, bg="#0b111a")
        btns.pack(fill="x", pady=8)
        tk.Button(btns, text="Refresh", command=refresh).pack(fill="x", pady=2)
        tk.Button(
            btns,
            text="Delete All Cutscene / Black Dumps",
            command=lambda: self.mass_delete_cutscene_black_dumps(refresh_callback=refresh)
        ).pack(fill="x", pady=2)
        tk.Button(btns, text="Add to exceptions", command=add_to_exceptions).pack(fill="x", pady=2)
        tk.Button(btns, text="Remove from exceptions", command=remove_from_exceptions).pack(fill="x", pady=2)
        tk.Button(btns, text="Apply Selected Preset", command=apply_selected_texture_preset).pack(fill="x", pady=2)
        tk.Button(btns, text="Clear Preset Override", command=lambda: apply_selected_texture_preset(clear=True)).pack(fill="x", pady=2)
        tk.Button(btns, text="Recreate with Selected Preset", command=recreate_with_selected_preset).pack(fill="x", pady=2)
        tk.Button(btns, text="Recreate Selected Texture(s)", command=recreate).pack(fill="x", pady=2)
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
        cfg["skip_dynamic_efb_postprocess"] = bool(self.dynamic_efb_filter_var.get())
        cfg["delete_skipped_cutscene_buffers"] = bool(self.delete_cutscene_var.get())
        cfg["auto_scan_delete_cutscene_buffers_on_start"] = bool(self.auto_cleanup_cutscene_var.get())
        cfg["auto_quarantine_efb_cutscenes"] = bool(
            self.auto_quarantine_buffers_var.get() if hasattr(self, "auto_quarantine_buffers_var") else False
        )
        cfg["auto_quarantine_live_threshold"] = max(2, int(float(cfg.get("auto_quarantine_live_threshold", 12) or 12)))
        cfg["auto_quarantine_live_idle_seconds"] = max(1.0, float(cfg.get("auto_quarantine_live_idle_seconds", 5.0) or 5.0))
        cfg["enable_comfy_status"] = bool(self.comfy_status_var.get())
        cfg["pause_when_comfy_offline"] = bool(self.pause_comfy_var.get())
        cfg["auto_start_comfy_when_watching"] = bool(self.auto_start_comfy_var.get())
        cfg["auto_check_missing_load"] = bool(self.auto_missing_var.get())
        cfg["fix_alpha_edge_bleed"] = False
        cfg["alpha_bleed_iterations"] = 1
        cfg["alpha_edge_threshold"] = 32
        cfg["enable_separate_alpha_workflow"] = bool(self.alpha_workflow_var.get())
        cfg["alpha_load_image_node_id"] = int(cfg.get("alpha_load_image_node_id", 1))
        cfg["alpha_save_image_node_id"] = int(cfg.get("alpha_save_image_node_id", 5))
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
        if hasattr(self, "manager_sort_var"):
            cfg["manager_sort_by"] = manager_sort_code(self.manager_sort_var.get())
        else:
            cfg["manager_sort_by"] = manager_sort_code(cfg.get("manager_sort_by", "modified_newest"))
        if hasattr(self, "manager_group_var"):
            cfg["manager_group_by"] = manager_group_code(self.manager_group_var.get())
        else:
            cfg["manager_group_by"] = manager_group_code(cfg.get("manager_group_by", "none"))
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
        if cfg.get("enable_separate_alpha_workflow") and cfg.get("alpha_workflow_api_json"):
            if not Path(cfg["alpha_workflow_api_json"]).exists():
                raise ValueError("Alpha workflow API JSON file does not exist")
            validate_alpha_comfy_api_workflow(
                cfg["alpha_workflow_api_json"],
                cfg.get("alpha_load_image_node_id", "1"),
                cfg.get("alpha_save_image_node_id", "5"),
                require_reachable=True,
            )
        if (cfg.get("preserve_alpha") or cfg.get("process_tmp_image_files")) and not PIL_AVAILABLE:
            raise ValueError("This option needs Pillow. Install: pip install pillow")

    def start(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self.log("Already running.")
            return False
        try:
            self.cfg = self.collect()
            self.validate(self.cfg)
            save_config(self.cfg)
            self.stop_event.clear()
            self.force_scan_event.clear()
            self.force_missing_event.clear()
            self.stats.update({"processed":0, "cache_hits":0, "comfy_jobs":0, "queue_len":0, "manager_queue_len":0, "high_queue_len":0, "low_queue_len":0, "peak_vram_mb":0, "exceptions_skipped":0, "status":"RUNNING"})
            worker = Worker(self.cfg, self.log_q, self.stop_event, self.force_scan_event, self.force_missing_event, self.stats)
            self.worker = worker
            self.worker_thread = threading.Thread(target=worker.run, daemon=True)
            self.worker_thread.start()
            self.log("Started.")
            return True
        except Exception as e:
            messagebox.showerror("Start failed", str(e))
            return False

    def force_dump_check(self):
        self.force_scan_event.set()
        self.log("Force dump check requested.")

    def check_missing_load_now(self):
        self.force_missing_event.set()
        self.force_scan_event.set()
        self.log("Manual missing Load check requested.")

    def _launch_comfy_ui(self, cfg, show_errors=True):
        try:
            path = str(cfg.get("comfy_start_file", "")).strip()
            if not path:
                msg = "Please select ComfyUI start file first."
                if show_errors:
                    messagebox.showerror("Start ComfyUI", msg)
                else:
                    self.log(f"Auto-start skipped: {msg}")
                return False
            p = Path(path)
            if not p.exists():
                msg = "ComfyUI start file does not exist."
                if show_errors:
                    messagebox.showerror("Start ComfyUI", msg)
                else:
                    self.log(f"Auto-start skipped: {msg}")
                return False

            suffix = p.suffix.lower()
            cwd = str(p.parent)
            creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)

            if suffix in (".bat", ".cmd"):
                subprocess.Popen(["cmd", "/c", str(p)], cwd=cwd, creationflags=creationflags)
            elif suffix == ".py":
                subprocess.Popen(["python", str(p)], cwd=cwd, creationflags=creationflags)
            elif suffix == ".exe":
                subprocess.Popen([str(p)], cwd=cwd)
            else:
                os.startfile(str(p))

            self.log(f"Starting ComfyUI: {p.name}")
            return True
        except Exception as e:
            if show_errors:
                messagebox.showerror("Start ComfyUI failed", str(e))
            else:
                self.log(f"Auto-start ComfyUI failed: {e}")
            return False

    def start_comfy_ui(self):
        self._launch_comfy_ui(self.collect(), show_errors=True)

    def ensure_comfy_started_for_watching(self, cfg):
        if not cfg.get("auto_start_comfy_when_watching", True):
            return
        try:
            status = check_comfy_status(cfg.get("comfy_url", "http://127.0.0.1:8188"))
            if status.get("online"):
                self.log("ComfyUI is already online.")
                return
            self.log("ComfyUI is offline. Auto-starting it before processing.")
            self._launch_comfy_ui(cfg, show_errors=False)
        except Exception as exc:
            self.log(f"Could not check/auto-start ComfyUI: {exc}")

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
        for _ in range(200):
            try:
                msg = str(self.log_q.get_nowait())
            except queue.Empty:
                break
            stamp = time.strftime("%H:%M:%S")
            low = msg.casefold()
            if "error" in low or "failed" in low or "offline" in low:
                tag = "error"
            elif any(key in low for key in ("cutscene", "deleted", "warning", "skipped", "dump detected")):
                tag = "warning"
            elif any(key in low for key in ("done", "saved output", "complete", "online", "restored", "cache hit")):
                tag = "success"
            elif any(key in low for key in ("processing", "ksampler", "controlnet", "realesrgan", "workflow", "alpha")):
                tag = "process"
            elif any(key in low for key in ("started", "watching", "queue", "profile loaded", "mode selected")):
                tag = "info"
            else:
                tag = "normal"
            messages.append((stamp, msg, tag))

        if messages:
            # Worker and batch threads communicate file-system changes through
            # logs. Use those events to invalidate the Texture Manager index
            # immediately, while throttling the actual scan so thousands of
            # dumps do not trigger thousands of rescans.
            try:
                dirty_words = (
                    "dump detected", "saved output", "cache hit", "processing",
                    "quarantined", "restored", "deleted", "recreated",
                    "request(s) queued", "batch profile complete", "profile loaded",
                    "skipped", "missing", "failed"
                )
                if any(any(word in str(msg).casefold() for word in dirty_words) for _stamp, msg, _tag in messages):
                    self._manager_mark_dirty()
            except Exception:
                pass
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            try:
                with APP_LOG_PATH.open("a", encoding="utf-8") as log_file:
                    for stamp, msg, _tag in messages:
                        log_file.write(f"[{stamp}] {msg}\n")
            except Exception:
                pass

            if hasattr(self, "log_text"):
                for stamp, msg, tag in messages:
                    try:
                        self.log_text.insert("end", f"[{stamp}] ", "time")
                        self.log_text.insert("end", msg + "\n", tag)
                    except Exception:
                        self.log_text.insert("end", f"[{stamp}] {msg}\n")
                if not hasattr(self, "log_autoscroll_var") or self.log_autoscroll_var.get():
                    self.log_text.see("end")

        self.after(200, self.poll_log)

    def _dashboard_metrics_tick(self):
        self._request_dashboard_metrics_refresh()
        try:
            self._dashboard_metrics_after = self.after(5000, self._dashboard_metrics_tick)
        except Exception:
            self._dashboard_metrics_after = None

    def _request_dashboard_metrics_refresh(self, force=False):
        if getattr(self, "_dashboard_metrics_running", False):
            self._dashboard_metrics_refresh_pending = self._dashboard_metrics_refresh_pending or force
            return

        now = time.time()
        if not force and now - float(self._dashboard_metrics_cache.get("updated_at", 0.0) or 0.0) < 4.0:
            return

        profile_names = []
        if self.current_profile_name:
            profile_names.append(self.current_profile_name)
        profile_names.extend(name for name in self.batch_queue if name not in profile_names)
        snapshots = {}
        profiles = self.profile_data.get("profiles", {})
        for name in profile_names:
            record = profiles.get(name, {})
            settings = dict(DEFAULT_CONFIG)
            settings.update(dict(record.get("settings", {})))
            snapshots[name] = settings

        self._dashboard_metrics_running = True
        self._dashboard_metrics_refresh_pending = False

        def worker():
            progress = {}
            for name, settings in snapshots.items():
                try:
                    progress[name] = self._texture_progress_for_settings(settings)
                except Exception:
                    progress[name] = (0, 0)
            used, total = get_vram_info()
            result = {
                "progress": progress,
                "vram_used_mb": used,
                "vram_total_mb": total,
                "updated_at": time.time(),
            }

            def finish():
                self._dashboard_metrics_cache = result
                if used is not None:
                    self.stats["vram_used_mb"] = used
                    self.stats["vram_total_mb"] = total
                    self.stats["peak_vram_mb"] = max(self.stats.get("peak_vram_mb", 0), used)
                self._dashboard_metrics_running = False
                if self._dashboard_metrics_refresh_pending:
                    self._dashboard_metrics_refresh_pending = False
                    self.after(100, lambda: self._request_dashboard_metrics_refresh(force=True))

            try:
                self.after(0, finish)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True, name="FR-DashboardMetrics").start()

    def update_dashboard(self):
        used = self._dashboard_metrics_cache.get("vram_used_mb")
        total_vram = self._dashboard_metrics_cache.get("vram_total_mb")
        if used is not None and total_vram:
            vram_text = f"VRAM: {used/1024:.1f} / {total_vram/1024:.1f} GB"
        else:
            vram_text = "VRAM: checking…"

        done, total = self._current_profile_progress_cached() if hasattr(self, "_current_profile_progress_cached") else (0, 0)
        pct = (100.0 * done / total) if total else 0.0
        worker_active = bool(self.worker_thread and self.worker_thread.is_alive())
        current_path = str(self.stats.get("current_input_path", "") or "")
        current_name = Path(current_path).name if current_path else "—"
        stage = str(self.stats.get("current_texture_stage", "Waiting") or "Waiting")
        running = 1 if worker_active and current_path and stage not in {"Done", "Waiting"} else 0
        queued = int(self.stats.get("queue_len", 0) or 0)

        if hasattr(self, "dashboard_progress_var"):
            self.dashboard_progress_var.set(pct)
            self.dashboard_progress_pct_text.set(f"{pct:.0f}%")
            status_word = "Processing" if worker_active else ("Complete" if total and done >= total else "Ready")
            self.dashboard_progress_text.set(f"{status_word} — {done} / {total} textures")
            self.dashboard_current_job_var.set(f"Current job: {current_name}  •  {stage}")
            self.dashboard_queued_var.set(str(queued))
            self.dashboard_manager_priority_var.set(str(int(self.stats.get("manager_queue_len", 0) or 0)))
            self.dashboard_high_priority_var.set(str(int(self.stats.get("high_queue_len", 0) or 0)))
            self.dashboard_low_priority_var.set(str(int(self.stats.get("low_queue_len", 0) or 0)))
            self._refresh_priority_lane_visuals()
            self.dashboard_running_var.set(str(running))
            if hasattr(self, "manager_priority_badge_var"):
                manager_count = int(self.stats.get("manager_queue_len", 0) or 0)
                self.manager_priority_badge_var.set(f"Manager priority: {manager_count}")
                self.manager_priority_badge.configure(
                    bg="#352342" if manager_count else "#152431",
                    fg="#eda9ff" if manager_count else "#91a8ba"
                )
            self.dashboard_done_var.set(str(done))
            self.dashboard_failed_var.set(str(int(self.stats.get("failed", 0) or 0)))

            now = time.time()
            if self._session_started_at:
                elapsed = max(0.0, now - self._session_started_at)
                self._session_last_elapsed = elapsed
                if not worker_active:
                    self._session_started_at = None
            else:
                elapsed = self._session_last_elapsed
            self.dashboard_elapsed_var.set(f"Elapsed {self._format_duration(elapsed)}")
            completed_this_run = max(0, done - int(getattr(self, "_session_initial_done", 0)))
            if worker_active and completed_this_run > 0 and elapsed > 1 and total > done:
                rate = completed_this_run / elapsed
                eta = (total - done) / rate if rate > 0 else 0
                self.dashboard_eta_var.set(f"ETA {self._format_duration(eta)}")
            elif total and done >= total:
                self.dashboard_eta_var.set("ETA 00:00:00")
            else:
                self.dashboard_eta_var.set("ETA —")

            preset = normalize_faithfulness_preset(self.stats.get("current_faithfulness_preset") or self.faithfulness_preset_var.get())
            self.dashboard_mode_var.set(preset)
            self.dashboard_mode_footer_var.set(f"Mode: {preset}")
            self.dashboard_cleanup_footer_var.set(
                "Auto cleanup: Enabled" if self.auto_cleanup_cutscene_var.get() else "Auto cleanup: Off"
            )
            self.dashboard_vram_footer_var.set(vram_text)
            if self.stats.get("comfy_online"):
                running_jobs = self.stats.get("comfy_running")
                pending_jobs = self.stats.get("comfy_pending")
                self.dashboard_comfy_footer_var.set(f"Backend: Online  •  {running_jobs or 0} running / {pending_jobs or 0} pending")
            else:
                self.dashboard_comfy_footer_var.set("Backend: Offline")

            texture_started = float(self.stats.get("current_texture_started_at", 0.0) or 0.0)
            texture_elapsed = max(0.0, now - texture_started) if texture_started else 0.0
            self.preview_texture_elapsed_var.set(f"Texture  {self._format_duration(texture_elapsed)}")
            self.preview_mode_var.set(f"Mode  {preset}")
            self._refresh_mode_cards()
            self._update_dashboard_batch_values()
            self._update_profile_header_card()

        try:
            if not hasattr(self, "tabs") or self.tabs.select() == str(self.page_dashboard):
                self.update_live_preview()
        except Exception:
            pass
        self.after(1000, self.update_dashboard)



# ============================================================
# Faithful Remaster current UI
# Profiles + Multi-emulator tabbed interface
# ============================================================


# ---------- Universal game title database helpers ----------
GAME_DB_PATH = DATA_DIR / "game_titles.sqlite"
GAME_TITLES_PATH = DATA_DIR / "game_titles.json"
AZAHAR_METADATA_PATH = DATA_DIR / "azahar_game_metadata.json"
AZAHAR_ICON_DIR = DATA_DIR / "azahar_icons"
AZAHAR_TITLE_ID_RE = re.compile(r"^[0-9A-Fa-f]{16}$")
AZAHAR_GAME_EXTENSIONS = {".3ds", ".cci", ".cxi", ".app", ".cia", ".smdh", ".zcci", ".zcxi"}


def is_azahar_title_id(value):
    return bool(AZAHAR_TITLE_ID_RE.fullmatch(str(value or "").strip()))


def normalize_azahar_title_id(value):
    raw = str(value or "").strip().upper()
    return raw if is_azahar_title_id(raw) else ""


def load_azahar_metadata_cache():
    try:
        data = json.loads(AZAHAR_METADATA_PATH.read_text(encoding="utf-8"))
        entries = data.get("titles", data) if isinstance(data, dict) else {}
        if not isinstance(entries, dict):
            return {}
        clean = {}
        for key, value in entries.items():
            tid = normalize_azahar_title_id(key)
            if tid and isinstance(value, dict):
                clean[tid] = dict(value)
        return clean
    except Exception:
        return {}


def save_azahar_metadata_cache(entries):
    AZAHAR_METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "titles": {k: v for k, v in sorted(entries.items()) if is_azahar_title_id(k) and isinstance(v, dict)},
    }
    temp = AZAHAR_METADATA_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(AZAHAR_METADATA_PATH)


def _align_up(value, alignment):
    return (int(value) + alignment - 1) & ~(alignment - 1)


def _read_file_range(handle, offset, size):
    handle.seek(max(0, int(offset)))
    data = handle.read(max(0, int(size)))
    return data


def _decode_smdh_text(block):
    try:
        return block.decode("utf-16le", errors="ignore").split("\x00", 1)[0].strip()
    except Exception:
        return ""


def _morton8(x, y):
    value = 0
    for bit in range(3):
        value |= ((x >> bit) & 1) << (bit * 2)
        value |= ((y >> bit) & 1) << (bit * 2 + 1)
    return value


def decode_smdh_icon(icon_data, width=48, height=48):
    """Decode the tiled RGB565 large icon embedded in a Nintendo 3DS SMDH."""
    if not PIL_AVAILABLE or len(icon_data) < width * height * 2:
        return None
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = image.load()
    tiles_x = width // 8
    for y in range(height):
        for x in range(width):
            tile_x, tile_y = x // 8, y // 8
            within = _morton8(x & 7, y & 7)
            index = ((tile_y * tiles_x + tile_x) * 64 + within) * 2
            value = int.from_bytes(icon_data[index:index + 2], "little")
            r = ((value >> 11) & 0x1F) * 255 // 31
            g = ((value >> 5) & 0x3F) * 255 // 63
            b = (value & 0x1F) * 255 // 31
            # SMDH texture rows use the 3DS GPU's bottom-up orientation.
            pixels[x, height - 1 - y] = (r, g, b, 255)
    return image


def parse_smdh_bytes(data):
    if len(data) < 0x36C0 or data[:4] != b"SMDH":
        return None
    titles = []
    # Language order: Japanese, English, French, German, Italian, Spanish,
    # Simplified Chinese, Korean, Dutch, Portuguese, Russian, Traditional Chinese.
    for language_index in range(16):
        base = 0x8 + language_index * 0x200
        short_title = _decode_smdh_text(data[base:base + 0x80])
        long_title = _decode_smdh_text(data[base + 0x80:base + 0x180])
        publisher = _decode_smdh_text(data[base + 0x180:base + 0x200])
        titles.append((short_title, long_title, publisher))
    chosen = titles[1] if titles[1][0] or titles[1][1] else next((row for row in titles if row[0] or row[1]), ("", "", ""))
    title = chosen[0] or chosen[1]
    if not title:
        return None
    icon = decode_smdh_icon(data[0x24C0:0x36C0], 48, 48)
    return {"title": title.strip(), "long_title": chosen[1].strip(), "publisher": chosen[2].strip(), "icon": icon}


def _read_ncch_metadata(handle, base_offset=0):
    header = _read_file_range(handle, base_offset + 0x100, 0x100)
    if len(header) < 0xB0 or header[:4] != b"NCCH":
        return None
    program_id = struct.unpack_from("<Q", header, 0x18)[0]
    title_id = f"{program_id:016X}"
    exefs_units = struct.unpack_from("<I", header, 0xA0)[0]
    exefs_size_units = struct.unpack_from("<I", header, 0xA4)[0]
    if not exefs_units or not exefs_size_units:
        return {"title_id": title_id, "smdh": None}
    exefs_offset = base_offset + exefs_units * 0x200
    exefs_header = _read_file_range(handle, exefs_offset, 0x200)
    if len(exefs_header) < 0xA0:
        return {"title_id": title_id, "smdh": None}
    for index in range(10):
        entry = exefs_header[index * 0x10:(index + 1) * 0x10]
        name = entry[:8].split(b"\x00", 1)[0].decode("ascii", errors="ignore")
        file_offset = struct.unpack_from("<I", entry, 8)[0]
        file_size = struct.unpack_from("<I", entry, 12)[0]
        if name == "icon" and 0x100 <= file_size <= 0x100000:
            smdh = _read_file_range(handle, exefs_offset + 0x200 + file_offset, file_size)
            if smdh[:4] == b"SMDH":
                return {"title_id": title_id, "smdh": smdh}
    return {"title_id": title_id, "smdh": None}


def _read_ncsd_metadata(handle):
    header = _read_file_range(handle, 0x100, 0x100)
    if len(header) < 0x60 or header[:4] != b"NCSD":
        return None
    for index in range(8):
        part_offset_units, part_size_units = struct.unpack_from("<II", header, 0x20 + index * 8)
        if not part_offset_units or not part_size_units:
            continue
        metadata = _read_ncch_metadata(handle, part_offset_units * 0x200)
        if metadata and metadata.get("title_id"):
            return metadata
    return None


def _tmd_signed_body_offset(signature_type):
    return {
        0x00010000: 0x240, 0x00010001: 0x140, 0x00010002: 0x80,
        0x00010003: 0x240, 0x00010004: 0x140, 0x00010005: 0x80,
    }.get(signature_type, 0)


def _read_cia_metadata(handle):
    header = _read_file_range(handle, 0, 0x20)
    if len(header) < 0x20:
        return None
    header_size = struct.unpack_from("<I", header, 0)[0]
    cert_size, ticket_size, tmd_size, meta_size = struct.unpack_from("<IIII", header, 8)
    content_size = struct.unpack_from("<Q", header, 0x18)[0]
    if not (0x20 <= header_size <= 0x100000) or tmd_size <= 0:
        return None
    cert_offset = _align_up(header_size, 0x40)
    ticket_offset = _align_up(cert_offset + cert_size, 0x40)
    tmd_offset = _align_up(ticket_offset + ticket_size, 0x40)
    content_offset = _align_up(tmd_offset + tmd_size, 0x40)
    meta_offset = _align_up(content_offset + content_size, 0x40)
    tmd = _read_file_range(handle, tmd_offset, min(tmd_size, 0x4000))
    title_id = ""
    if len(tmd) >= 4:
        signature_type = int.from_bytes(tmd[:4], "big")
        body = _tmd_signed_body_offset(signature_type)
        if body and len(tmd) >= body + 0x54:
            title_id = tmd[body + 0x4C:body + 0x54].hex().upper()
    smdh = None
    if meta_size >= 0x3AC0:
        candidate = _read_file_range(handle, meta_offset + 0x400, 0x36C0)
        if candidate[:4] == b"SMDH":
            smdh = candidate
    return {"title_id": normalize_azahar_title_id(title_id), "smdh": smdh}


def extract_azahar_file_metadata(path):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {".zcci", ".zcxi"}:
        return None  # Compressed Azahar containers require the emulator's decompressor.
    try:
        with path.open("rb") as handle:
            if suffix == ".smdh":
                smdh = _read_file_range(handle, 0, 0x36C0)
                inferred = next((normalize_azahar_title_id(part) for part in reversed(path.parts) if is_azahar_title_id(part)), "")
                return {"title_id": inferred, "smdh": smdh}
            magic = _read_file_range(handle, 0x100, 4)
            if magic == b"NCCH":
                return _read_ncch_metadata(handle, 0)
            if magic == b"NCSD":
                return _read_ncsd_metadata(handle)
            if suffix == ".cia":
                return _read_cia_metadata(handle)
    except (OSError, ValueError, struct.error):
        return None
    return None


def infer_azahar_user_root_from_dump(dump_folder):
    try:
        path = Path(dump_folder)
        parts = list(path.parts)
        lowered = [part.casefold() for part in parts]
        for index in range(len(parts) - 1):
            if lowered[index] == "dump" and lowered[index + 1] == "textures":
                return Path(*parts[:index])
    except Exception:
        pass
    return None


def candidate_azahar_user_roots(dump_folders=None):
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    candidates = []
    for dump in dump_folders or []:
        inferred = infer_azahar_user_root_from_dump(dump)
        if inferred:
            candidates.append(inferred)
    candidates.extend([
        appdata / "Azahar", appdata / "azahar-emu", appdata / "Citra", appdata / "citra-emu",
        Path.home() / ".local" / "share" / "azahar-emu",
        Path.home() / ".var" / "app" / "org.azahar_emu.Azahar" / "data" / "azahar-emu",
    ])
    result, seen = [], set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve(strict=False)).casefold()
        except Exception:
            key = str(candidate).casefold()
        if key in seen or not candidate.exists():
            continue
        seen.add(key); result.append(candidate)
    return result


def _decode_qsettings_path(value):
    value = str(value or "").strip().strip('"')
    if value.startswith("@ByteArray(") and value.endswith(")"):
        value = value[11:-1]
    return value.replace("\\\\", "\\")


def azahar_library_roots(user_root):
    user_root = Path(user_root)
    roots = []
    config_candidates = [user_root / "config" / "qt-config.ini", user_root / "qt-config.ini"]
    for config in config_candidates:
        if not config.is_file():
            continue
        try:
            content = config.read_text(encoding="utf-8-sig", errors="replace")
        except Exception:
            continue
        for match in re.finditer(r"(?mi)^\s*Paths\\gamedirs\\\d+\\path=(.*)$", content):
            raw = _decode_qsettings_path(match.group(1))
            if raw and raw not in {"INSTALLED", "SYSTEM"}:
                roots.append(Path(raw))
    zero = "0" * 32
    roots.extend([
        user_root / "sdmc" / "Nintendo 3DS" / zero / zero / "title" / "00040000",
        user_root / "sdmc" / "Nintendo 3DS" / zero / zero / "title" / "00040002",
        user_root / "nand" / zero / "title" / "00040010",
    ])
    result, seen = [], set()
    for root in roots:
        try:
            key = str(root.resolve(strict=False)).casefold()
        except Exception:
            key = str(root).casefold()
        if key in seen or not root.exists():
            continue
        seen.add(key); result.append(root)
    return result


def _iter_azahar_metadata_files(roots, max_files=12000):
    yielded = 0
    skip_dirs = {"dump", "load", "textures", "shader", "shaders", "log", "logs", "cache", "states"}
    for root in roots:
        root = Path(root)
        if root.is_file() and root.suffix.lower() in AZAHAR_GAME_EXTENSIONS:
            yield root; yielded += 1
            if yielded >= max_files: return
            continue
        if not root.is_dir():
            continue
        for current, dirs, files in os.walk(root):
            dirs[:] = [name for name in dirs if name.casefold() not in skip_dirs]
            for name in files:
                path = Path(current) / name
                if path.suffix.lower() not in AZAHAR_GAME_EXTENSIONS:
                    continue
                yield path; yielded += 1
                if yielded >= max_files: return


def scan_azahar_game_metadata(title_ids=None, dump_folders=None, progress_callback=None):
    targets = {normalize_azahar_title_id(value) for value in (title_ids or [])}
    targets.discard("")
    entries = load_azahar_metadata_cache()
    roots = candidate_azahar_user_roots(dump_folders)
    library_roots = []
    for user_root in roots:
        library_roots.extend(azahar_library_roots(user_root))
    seen_roots = set(); library_roots = [r for r in library_roots if not (str(r).casefold() in seen_roots or seen_roots.add(str(r).casefold()))]
    stats = {"roots": len(library_roots), "files": 0, "resolved": 0, "cache_hits": 0, "errors": 0}

    unresolved = set(targets)
    for title_id in list(unresolved):
        row = entries.get(title_id, {})
        icon_ok = not row.get("icon_path") or Path(str(row.get("icon_path"))).is_file()
        source = Path(str(row.get("source_path") or ""))
        try:
            source_ok = not source or (source.is_file() and source.stat().st_mtime_ns == int(row.get("source_mtime_ns", -1)) and source.stat().st_size == int(row.get("source_size", -1)))
        except Exception:
            source_ok = False
        if row.get("title") and icon_ok and source_ok:
            unresolved.discard(title_id); stats["cache_hits"] += 1

    AZAHAR_ICON_DIR.mkdir(parents=True, exist_ok=True)
    for path in _iter_azahar_metadata_files(library_roots):
        if targets and not unresolved:
            break
        stats["files"] += 1
        if progress_callback and stats["files"] % 25 == 0:
            progress_callback(f"Reading Azahar metadata… {stats['files']} files")
        try:
            st = path.stat()
            metadata = extract_azahar_file_metadata(path)
            if not metadata:
                continue
            title_id = normalize_azahar_title_id(metadata.get("title_id"))
            if not title_id:
                continue
            if targets and title_id not in targets:
                continue
            parsed = parse_smdh_bytes(metadata.get("smdh") or b"") if metadata.get("smdh") else None
            old = entries.get(title_id, {})
            title = parsed.get("title", "") if parsed else str(old.get("title") or "")
            icon_path = str(old.get("icon_path") or "")
            if parsed and parsed.get("icon") is not None:
                target = AZAHAR_ICON_DIR / f"{title_id}.png"
                parsed["icon"].save(target, format="PNG")
                icon_path = str(target)
            if not title:
                continue
            entries[title_id] = {
                "title": title,
                "long_title": parsed.get("long_title", "") if parsed else str(old.get("long_title") or ""),
                "publisher": parsed.get("publisher", "") if parsed else str(old.get("publisher") or ""),
                "icon_path": icon_path,
                "source": "Azahar SMDH metadata",
                "source_path": str(path),
                "source_mtime_ns": st.st_mtime_ns,
                "source_size": st.st_size,
            }
            db_upsert_game(title_id, title, "", "Azahar / Citra", "Azahar SMDH metadata")
            unresolved.discard(title_id); stats["resolved"] += 1
        except Exception:
            stats["errors"] += 1
    save_azahar_metadata_cache(entries)
    return entries, stats


def lookup_azahar_metadata(title_id):
    title_id = normalize_azahar_title_id(title_id)
    if not title_id:
        return None
    row = load_azahar_metadata_cache().get(title_id)
    if not row or not row.get("title"):
        return None
    return {
        "game_id": title_id,
        "title": row.get("title", ""),
        "region": row.get("region", ""),
        "source": row.get("source", "Azahar metadata cache"),
        "icon_path": row.get("icon_path", ""),
        "publisher": row.get("publisher", ""),
    }

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

N64_FOLDER_TITLE_ALIASES = {
    "MARIOKART64": "Mario Kart 64", "SUPERMARIO64": "Super Mario 64",
    "SUPER MARIO 64": "Super Mario 64", "ZELDA OCARINA": "The Legend of Zelda: Ocarina of Time",
    "ZELDA MAJORA": "The Legend of Zelda: Majora's Mask", "MAJORAS MASK": "The Legend of Zelda: Majora's Mask",
    "GOLDENEYE": "GoldenEye 007", "PERFECT DARK": "Perfect Dark", "BANJO KAZOOIE": "Banjo-Kazooie",
    "BANJO TOOIE": "Banjo-Tooie", "STARFOX64": "Star Fox 64", "F ZERO X": "F-Zero X",
    "WAVE RACE 64": "Wave Race 64", "PAPER MARIO": "Paper Mario", "SUPER SMASH BROS": "Super Smash Bros.",
    "DONKEY KONG 64": "Donkey Kong 64", "DK64": "Donkey Kong 64", "KIRBY64": "Kirby 64: The Crystal Shards",
    "DIDDY KONG RACING": "Diddy Kong Racing", "YOSHIS STORY": "Yoshi's Story", "POKEMON STADIUM": "Pokémon Stadium",
}

def humanize_n64_folder_name(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = re.sub(r"[_\-.]+", " ", raw).strip()
    key = re.sub(r"\s+", " ", normalized).upper()
    if key in N64_FOLDER_TITLE_ALIASES:
        return N64_FOLDER_TITLE_ALIASES[key]
    compact = key.replace(" ", "")
    if compact in N64_FOLDER_TITLE_ALIASES:
        return N64_FOLDER_TITLE_ALIASES[compact]
    return " ".join(token if token.isdigit() else token.capitalize() for token in normalized.split())

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
    "Flycast — Dreamcast / Naomi / Atomiswave": [
        "metadat/redump/Sega - Dreamcast.dat",
    ],
    "Nintendo 64 / RMG": [
        "dat/Nintendo - Nintendo 64.dat",
    ],
    "Nintendo 64 / Project64": [
        "dat/Nintendo - Nintendo 64.dat",
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


def db_upsert_games(records):
    """Insert many title records in a single transaction."""
    rows = []
    for record in records or []:
        game_id = str(record.get("id", "") or "").strip()
        title = str(record.get("title", "") or "").strip()
        normalized = normalize_game_id(game_id)
        if not normalized or not title:
            continue
        rows.append((
            normalized,
            game_id,
            title,
            str(record.get("region", "") or "").strip(),
            str(record.get("platform", "") or "").strip(),
            str(record.get("source", "") or "").strip(),
        ))
    if not rows:
        return 0
    init_game_database()
    con = sqlite3.connect(GAME_DB_PATH)
    try:
        con.executemany("""
            INSERT INTO games(normalized_id, original_id, title, region, platform, source)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(normalized_id, platform) DO UPDATE SET
                original_id=excluded.original_id,
                title=excluded.title,
                region=excluded.region,
                source=excluded.source
        """, rows)
        con.commit()
        return len(rows)
    finally:
        con.close()


def db_platform_count(platform):
    init_game_database()
    con = sqlite3.connect(GAME_DB_PATH)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM games WHERE platform=?",
            (str(platform or ""),)
        ).fetchone()[0]
    finally:
        con.close()


def import_platform_game_database(platform, log_callback=None):
    """Download and import only the metadata sources needed by one emulator."""
    imported = 0
    downloaded = 0
    errors = []
    for repo_path in LIBRETRO_DAT_SOURCES.get(platform, []):
        try:
            if log_callback:
                log_callback(f"Downloading title metadata: {repo_path}")
            text, _url = download_libretro_dat(repo_path)
            downloaded += 1
            records = parse_libretro_dat(text, platform, repo_path)
            count = db_upsert_games(records)
            imported += count
            if log_callback:
                log_callback(f"Imported {count} {platform} title ID entries")
        except Exception as exc:
            errors.append(f"{repo_path}: {exc}")
            if log_callback:
                log_callback(f"Title metadata source unavailable: {repo_path}: {exc}")
    return imported, downloaded, errors


def game_id_lookup_candidates(game_id, emulator=""):
    """Return safe database aliases while preserving the emulator's real folder ID.

    Flycast reads the Dreamcast product number from IP.BIN (for example
    ``MK-51058``), while Redump's USA entry for the same disc may store only
    ``51058``.  Exact matches always win; the stripped/added ``MK`` form is a
    fallback only for numeric Sega product numbers.
    """
    raw = str(game_id or "").strip().upper()
    normalized = normalize_game_id(raw)
    if not normalized:
        return []

    candidates = [normalized]
    is_flycast = (str(emulator or "") == FLYCAST_EMULATOR or
                  str(emulator or "").startswith("Flycast"))
    if is_flycast:
        if normalized.startswith("MK") and normalized[2:].isdigit():
            candidates.append(normalized[2:])
        elif normalized.isdigit() and 4 <= len(normalized) <= 8:
            candidates.append("MK" + normalized)

    # Stable de-duplication.
    return list(dict.fromkeys(candidates))


def lookup_local_game_title(game_id, emulator=""):
    """Resolve a title without touching Tk widgets or performing online lookups."""
    gid = str(game_id or "").strip().upper()
    if not gid:
        return None

    if str(emulator or "") == "Azahar / Citra":
        azahar = lookup_azahar_metadata(gid)
        if azahar:
            return azahar

    row = None
    matched_candidate = ""
    for candidate in game_id_lookup_candidates(gid, emulator):
        row = db_lookup_game(candidate, emulator) or db_lookup_game(candidate, "")
        if row:
            matched_candidate = candidate
            break

    if row:
        # Flycast replacement folders are named with its exact product ID.
        # Never rewrite MK-51058 to Redump's alias 51058 after title lookup.
        resolved_id = gid if str(emulator or "").startswith("Flycast") else row["original_id"]
        source = row.get("source", "local universal database")
        if matched_candidate and matched_candidate != normalize_game_id(gid):
            source = f"{source} (Flycast ID alias)"
        return {
            "game_id": resolved_id,
            "title": row["title"],
            "region": row.get("region", ""),
            "source": source,
        }

    titles = load_game_titles()
    cached = titles.get(gid)
    if isinstance(cached, str):
        cached = {"title": cached}
    if isinstance(cached, dict) and cached.get("title"):
        return {
            "game_id": gid,
            "title": cached["title"],
            "region": cached.get("region", ""),
            "source": "game_titles.json",
        }
    return None


def format_resolved_game_title(title, region=""):
    title = str(title or "").strip()
    region = str(region or "").strip()
    if not title:
        return ""
    # Redump names often end in: (USA) (En,Ja,Fr,...). Keep the title concise,
    # then add one clear region tag back to the display name.
    if region:
        title = re.sub(
            rf"\s*\({re.escape(region)}\)(?:\s*\([^)]*\))*\s*$",
            "",
            title,
            flags=re.I,
        ).strip()
    if region and region.casefold() not in title.casefold():
        return f"{title} ({region})"
    return title

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
BATCH_QUEUE_PATH = DATA_DIR / "batch_queue.json"
PROFILES_DIR.mkdir(exist_ok=True)

FLYCAST_EMULATOR = "Flycast — Dreamcast / Naomi / Atomiswave"

EMULATORS = [
    "Dolphin",
    FLYCAST_EMULATOR,
    "DuckStation",
    "PCSX2",
    "PPSSPP",
    "Azahar / Citra",
    "Nintendo 64 / RMG",
    "Nintendo 64 / Project64",
    "Generic",
]


def _first_existing_or_default(candidates):
    candidates = [Path(p) for p in candidates if p]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else None



def detect_rmg_portable_root():
    """Find a likely RMG portable folder in common user locations."""
    search_roots = [
        Path(os.environ.get("USERPROFILE", Path.home())) / "Downloads",
        Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop",
        Path(os.environ.get("USERPROFILE", Path.home())) / "Documents",
    ]
    for root in search_roots:
        if not root.exists():
            continue
        try:
            candidates = sorted(root.glob("RMG-Portable-*"), key=lambda x: x.stat().st_mtime, reverse=True)
        except Exception:
            candidates = []
        for candidate in candidates:
            if (candidate / "Cache" / "texture_dump").exists() or (candidate / "Data" / "hires_texture").exists():
                return candidate
    return None

def detect_flycast_data_root():
    """Find Flycast's data folder without recursively scanning whole drives.

    Standalone/portable Flycast normally keeps texdump and replacement folders
    below a data directory.  We prefer a folder that already contains texdump
    or textures, then fall back to a nearby portable Flycast data directory.
    """
    user = Path(os.environ.get("USERPROFILE", Path.home()))
    appdata = Path(os.environ.get("APPDATA", user / "AppData" / "Roaming"))
    localappdata = Path(os.environ.get("LOCALAPPDATA", user / "AppData" / "Local"))

    candidates = [
        APP_DIR / "data",
        APP_DIR.parent / "data",
        APP_DIR.parent / "Flycast" / "data",
        appdata / "flycast",
        appdata / "Flycast",
        appdata / "flycast" / "data",
        localappdata / "flycast",
        localappdata / "Flycast",
        localappdata / "flycast" / "data",
        user / "Documents" / "Flycast" / "data",
    ]

    # Portable archives are commonly extracted to Downloads/Desktop/Documents.
    for root in (user / "Downloads", user / "Desktop", user / "Documents"):
        if not root.exists():
            continue
        try:
            for folder in root.glob("[Ff]lycast*"):
                if folder.is_dir():
                    candidates.extend((folder / "data", folder))
        except Exception:
            pass

    seen = set()
    valid = []
    fallback = []
    for candidate in candidates:
        try:
            candidate = candidate.expanduser()
            key = str(candidate.resolve())
        except Exception:
            key = str(candidate)
        if key in seen:
            continue
        seen.add(key)

        if (candidate / "texdump").exists() or (candidate / "textures").exists():
            valid.append(candidate)
            continue
        if candidate.name.lower() == "data" and candidate.exists():
            fallback.append(candidate)
        elif (candidate / "flycast.exe").is_file():
            fallback.append(candidate / "data")

    return valid[0] if valid else (fallback[0] if fallback else None)


def detect_project64_root():
    search_roots = [Path.home() / "Downloads", Path.home() / "Desktop", Path.home() / "Documents"]
    candidates = []
    for root in search_roots:
        if not root.exists():
            continue
        for pattern in ("Project64*", "PJ64*"):
            try:
                candidates.extend(root.glob(pattern))
            except Exception:
                pass
    try:
        candidates = sorted(set(candidates), key=lambda x: x.stat().st_mtime, reverse=True)
    except Exception:
        pass
    for candidate in candidates:
        plugin = candidate / "Plugin"
        if (plugin / "texture_dump").exists() or (plugin / "hires_texture").exists():
            return candidate
    return None

def _portable_texture_root(folder_patterns, relative_path):
    """Find a nearby portable emulator texture root without scanning whole drives."""
    user = Path(os.environ.get("USERPROFILE", Path.home()))
    search_roots = [user / "Downloads", user / "Desktop", user / "Documents"]
    candidates = []
    for root in search_roots:
        if not root.exists():
            continue
        for pattern in folder_patterns:
            try:
                for folder in root.glob(pattern):
                    candidate = folder.joinpath(*relative_path)
                    if candidate.exists():
                        candidates.append(candidate)
            except Exception:
                pass
    try:
        candidates.sort(key=lambda item: item.stat().st_mtime_ns, reverse=True)
    except Exception:
        pass
    return candidates[0] if candidates else None


def default_emulator_roots(emulator):
    """Return the standard dump-root and load-root for an emulator.

    The returned values are roots which contain per-game folders.  Per-game
    technical leaves (``dumps``, ``replacements``, ``new`` and ``GLideNHQ``)
    are added by :func:`profile_paths_from_dump_selection`.
    """
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    localappdata = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    documents = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents"

    if emulator == "Dolphin":
        base = appdata / "Dolphin Emulator"
        return base / "Dump" / "Textures", base / "Load" / "Textures"

    if emulator == FLYCAST_EMULATOR:
        data_root = detect_flycast_data_root()
        if data_root:
            return data_root / "texdump", data_root / "textures"
        return None, None

    if emulator == "Azahar / Citra":
        base = appdata / "Azahar"
        return base / "dump" / "textures", base / "load" / "textures"

    if emulator == "PCSX2":
        portable = _portable_texture_root(("PCSX2*", "pcsx2*"), ("textures",))
        candidates = [
            documents / "PCSX2" / "textures",
            localappdata / "PCSX2" / "textures",
            appdata / "PCSX2" / "textures",
            portable,
        ]
        root = _first_existing_or_default(candidates)
        return root, root

    if emulator == "DuckStation":
        portable = _portable_texture_root(("DuckStation*", "duckstation*"), ("textures",))
        candidates = [
            documents / "DuckStation" / "textures",
            localappdata / "DuckStation" / "textures",
            appdata / "DuckStation" / "textures",
            portable,
        ]
        root = _first_existing_or_default(candidates)
        return root, root

    if emulator == "PPSSPP":
        candidates = [
            documents / "PPSSPP" / "PSP" / "TEXTURES",
            appdata / "PPSSPP" / "PSP" / "TEXTURES",
            APP_DIR / "memstick" / "PSP" / "TEXTURES",
            APP_DIR / "PSP" / "TEXTURES",
        ]
        root = _first_existing_or_default(candidates)
        return root, root

    if emulator == "Nintendo 64 / RMG":
        rmg_root = detect_rmg_portable_root()
        if rmg_root:
            return rmg_root / "Cache" / "texture_dump", rmg_root / "Data" / "hires_texture"
        return None, None

    if emulator == "Nintendo 64 / Project64":
        pj64_root = detect_project64_root()
        if pj64_root:
            return pj64_root / "Plugin" / "texture_dump", pj64_root / "Plugin" / "hires_texture"
        return None, None

    return None, None


def _path_is_within(path, root):
    if not root:
        return False
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except Exception:
        return False


def _contains_path_sequence(parts, sequence):
    sequence = tuple(str(part).casefold() for part in sequence)
    lowered = tuple(str(part).casefold() for part in parts)
    width = len(sequence)
    return any(lowered[index:index + width] == sequence for index in range(max(0, len(lowered) - width + 1)))


def _ps_serial_like(value):
    compact = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    return bool(re.fullmatch(r"[A-Z]{4}\d{5}", compact))


def _dolphin_id_like(value):
    text = str(value or "").strip().upper()
    return bool(re.fullmatch(r"[A-Z0-9]{4,6}", text) and any(ch.isdigit() for ch in text))


def detect_emulator_from_dump_path(folder_path, fallback=""):
    """Infer the emulator from a selected dump/root path.

    Returns a dictionary with ``emulator``, ``confidence`` and ``reason``.
    Ambiguous PlayStation ``textures/<serial>/dumps`` layouts respect the
    caller's current emulator unless DuckStation's per-game ``config.yaml`` or
    an emulator name in the path resolves the ambiguity.
    """
    if not folder_path:
        return {"emulator": str(fallback or "Generic"), "confidence": 0, "reason": "empty path"}
    path = Path(folder_path).expanduser()
    parts = [str(part).casefold() for part in path.parts]
    joined = "/".join(parts)
    fallback = str(fallback or "").strip()

    scores = {emu: 0 for emu in EMULATORS if emu != "Generic"}
    reasons = {emu: [] for emu in scores}

    def add(emu, points, reason):
        scores[emu] += int(points)
        reasons[emu].append(reason)

    named_tokens = {
        "Dolphin": ("dolphin emulator", "dolphin-x64", "dolphin"),
        "PCSX2": ("pcsx2",),
        "DuckStation": ("duckstation",),
        "PPSSPP": ("ppsspp",),
        "Azahar / Citra": ("azahar", "citra"),
        FLYCAST_EMULATOR: ("flycast",),
        "Nintendo 64 / RMG": ("rmg", "rosalie's mupen gui"),
        "Nintendo 64 / Project64": ("project64", "pj64"),
    }
    for emu, tokens in named_tokens.items():
        if any(token in joined for token in tokens):
            add(emu, 85, "emulator name appears in path")

    if "texdump" in parts:
        add(FLYCAST_EMULATOR, 100, "Flycast texdump folder")
    if _contains_path_sequence(path.parts, ("PSP", "TEXTURES")):
        add("PPSSPP", 100, "PPSSPP PSP/TEXTURES layout")
    if "glidenhq" in parts or "texture_dump" in parts:
        if "cache" in parts:
            add("Nintendo 64 / RMG", 100, "RMG Cache/texture_dump layout")
        if "plugin" in parts:
            add("Nintendo 64 / Project64", 100, "Project64 Plugin/texture_dump layout")
    if _contains_path_sequence(path.parts, ("dump", "textures")):
        # Azahar and Dolphin share these leaf names. Use ancestry and IDs.
        if "azahar" in joined or "citra" in joined:
            add("Azahar / Citra", 100, "Azahar dump/textures layout")
        elif "dolphin" in joined:
            add("Dolphin", 100, "Dolphin Dump/Textures layout")
        else:
            add("Dolphin", 35, "generic Dump/Textures layout")
            add("Azahar / Citra", 35, "generic dump/textures layout")

    # Compare against known/default roots where available.
    for emu in scores:
        try:
            dump_root, _load_root = default_emulator_roots(emu)
        except Exception:
            dump_root = None
        if dump_root and _path_is_within(path, dump_root):
            add(emu, 95, "path is inside detected emulator dump root")

    # Inspect the selected folder and immediate game parents without opening images.
    candidates = []
    if path.is_dir():
        candidates.append(path)
        if path.name.casefold() in {"dumps", "new", "glidenhq"}:
            candidates.append(path.parent)
        try:
            candidates.extend(child for child in path.iterdir() if child.is_dir())
        except Exception:
            pass

    ids = [candidate.name for candidate in candidates]
    if any(is_azahar_title_id(value) for value in ids):
        add("Azahar / Citra", 70, "16-digit 3DS title ID folder")
    if any(_dolphin_id_like(value) for value in ids):
        add("Dolphin", 35, "Dolphin-style game ID folder")
    if any((candidate / "new").is_dir() for candidate in candidates):
        add("PPSSPP", 70, "per-game new dump folder")
    if any((candidate / "GLideNHQ").is_dir() for candidate in candidates):
        if "plugin" in parts:
            add("Nintendo 64 / Project64", 70, "GLideNHQ game dump")
        else:
            add("Nintendo 64 / RMG", 55, "GLideNHQ game dump")
    ps_game_dirs = [candidate for candidate in candidates if (candidate / "dumps").is_dir()]
    if path.name.casefold() == "dumps":
        ps_game_dirs.append(path.parent)
    if ps_game_dirs:
        if any((candidate / "config.yaml").is_file() for candidate in ps_game_dirs):
            add("DuckStation", 95, "DuckStation per-game config.yaml")
        else:
            add("PCSX2", 45, "PlayStation textures/<serial>/dumps layout")
            add("DuckStation", 35, "PlayStation textures/<serial>/dumps layout")

    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_emu, best_score = ordered[0] if ordered else ("Generic", 0)
    second_score = ordered[1][1] if len(ordered) > 1 else 0

    # A user's selected profile emulator is the safest tie-breaker for the two
    # identical PlayStation directory layouts.
    if fallback in scores and scores[fallback] > 0 and best_score - scores[fallback] <= 15:
        best_emu, best_score = fallback, scores[fallback]
        reasons[best_emu].append("current profile used as ambiguity tie-breaker")
    elif best_score == second_score and fallback in scores and scores[fallback] == best_score:
        best_emu = fallback

    confidence = min(100, best_score)
    if best_score < 35:
        return {"emulator": fallback if fallback in EMULATORS else "Generic", "confidence": best_score, "reason": "layout not recognized"}
    reason = "; ".join(reasons.get(best_emu, [])[-2:]) or "folder layout"
    return {"emulator": best_emu, "confidence": confidence, "reason": reason}


def game_id_from_dump_path(emulator, dump_folder):
    path = Path(dump_folder)
    leaf = path.name.casefold()
    if emulator in ("PCSX2", "DuckStation") and leaf == "dumps":
        return path.parent.name
    if emulator == "PPSSPP" and leaf == "new":
        return path.parent.name
    if emulator in ("Nintendo 64 / RMG", "Nintendo 64 / Project64") and leaf == "glidenhq":
        return path.parent.name
    if emulator == FLYCAST_EMULATOR and leaf == "texdump":
        return ""
    if leaf in {"textures", "texture_dump", "dump", "dumps", "new", "replacements", "replacement", "load"}:
        return ""
    return path.name


def profile_paths_from_dump_selection(emulator, dump_selection, game_id=""):
    """Normalize a root/game dump selection into exact per-profile paths."""
    emulator = str(emulator or "Generic")
    selected = Path(dump_selection).expanduser() if dump_selection else None
    game_id = str(game_id or "").strip()
    if selected is None:
        return None, None, game_id

    leaf = selected.name.casefold()
    inferred = game_id_from_dump_path(emulator, selected)
    if inferred:
        game_id = inferred

    if emulator in ("PCSX2", "DuckStation"):
        if leaf == "dumps":
            dump = selected
            load = selected.parent / "replacements"
            return dump, load, selected.parent.name
        if (selected / "dumps").is_dir() or (selected / "replacements").is_dir():
            return selected / "dumps", selected / "replacements", selected.name
        if game_id:
            return selected / game_id / "dumps", selected / game_id / "replacements", game_id
        return selected, None, game_id

    if emulator == "PPSSPP":
        if leaf == "new":
            return selected, selected.parent, selected.parent.name
        if selected.parent.name.casefold() == "textures" and leaf != "textures":
            return selected / "new", selected, selected.name
        if game_id:
            game = selected / game_id
            return game / "new", game, game_id
        return selected, None, game_id

    if emulator in ("Nintendo 64 / RMG", "Nintendo 64 / Project64"):
        if leaf == "glidenhq":
            return selected, derive_load_folder_for_emulator(emulator, selected), selected.parent.name
        if selected.parent.name.casefold() == "texture_dump" or (selected / "GLideNHQ").is_dir():
            dump = selected / "GLideNHQ" if (selected / "GLideNHQ").is_dir() else selected
            return dump, derive_load_folder_for_emulator(emulator, dump), selected.name
        if game_id:
            dump = selected / game_id / "GLideNHQ"
            return dump, derive_load_folder_for_emulator(emulator, dump), game_id
        return selected, derive_load_folder_for_emulator(emulator, selected), game_id

    if emulator in ("Dolphin", "Azahar / Citra", FLYCAST_EMULATOR):
        root_markers = (
            leaf == "texdump" or
            (leaf == "textures" and selected.parent.name.casefold() == "dump") or
            (leaf == "textures" and emulator == FLYCAST_EMULATOR)
        )
        if root_markers and game_id:
            dump = selected / game_id
            return dump, derive_load_folder_for_emulator(emulator, dump), game_id
        if root_markers:
            return selected, derive_load_folder_for_emulator(emulator, selected), game_id
        return selected, derive_load_folder_for_emulator(emulator, selected), game_id or selected.name

    return selected, derive_load_folder_for_emulator(emulator, selected), game_id or selected.name


def derive_load_folder_for_emulator(emulator, dump_folder):
    """Derive the exact same-game replacement folder from a dump path."""
    dump = Path(dump_folder)
    leaf = dump.name.casefold()

    if emulator in ("PCSX2", "DuckStation"):
        if leaf == "dumps":
            return dump.parent / "replacements"
        if (dump / "dumps").is_dir() or (dump / "replacements").is_dir():
            return dump / "replacements"
        return None

    if emulator == FLYCAST_EMULATOR:
        parts = list(dump.parts)
        lowered = [part.casefold() for part in parts]
        if "texdump" in lowered:
            idx = lowered.index("texdump")
            parts[idx] = "textures"
            return Path(*parts)
        root_dump, root_load = default_emulator_roots(emulator)
        if root_dump and root_load and _path_is_within(dump, root_dump):
            return root_load / dump.relative_to(root_dump)
        return None

    if emulator == "PPSSPP":
        if leaf == "new":
            return dump.parent
        if dump.parent.name.casefold() == "textures":
            return dump
        root_dump, _root_load = default_emulator_roots(emulator)
        if root_dump and dump == root_dump:
            return root_dump
        return None

    if emulator in ("Nintendo 64 / RMG", "Nintendo 64 / Project64"):
        game_dir = dump.parent.name if leaf == "glidenhq" else dump.name
        lowered = [part.casefold() for part in dump.parts]
        if "texture_dump" in lowered:
            idx = lowered.index("texture_dump")
            base = Path(*dump.parts[:idx])
            if emulator == "Nintendo 64 / RMG":
                if base.name.casefold() == "cache":
                    base = base.parent
                return base / "Data" / "hires_texture" / game_dir
            return base / "hires_texture" / game_dir
        root_dump, root_load = default_emulator_roots(emulator)
        if root_dump and root_load and _path_is_within(dump, root_dump):
            rel = dump.relative_to(root_dump)
            rel_parts = list(rel.parts)
            if rel_parts and rel_parts[-1].casefold() == "glidenhq":
                rel_parts.pop()
            return root_load.joinpath(*rel_parts) if rel_parts else root_load
        return None

    if emulator == "Azahar / Citra":
        parts = list(dump.parts)
        lowered = [part.casefold() for part in parts]
        for idx in range(len(lowered) - 1):
            if lowered[idx:idx + 2] == ["dump", "textures"]:
                parts[idx] = "load"
                return Path(*parts)
        root_dump, root_load = default_emulator_roots(emulator)
        if root_dump and root_load and _path_is_within(dump, root_dump):
            return root_load / dump.relative_to(root_dump)
        return None

    if emulator == "Dolphin":
        parts = list(dump.parts)
        lowered = [part.casefold() for part in parts]
        for idx in range(len(lowered) - 1):
            if lowered[idx:idx + 2] == ["dump", "textures"]:
                parts[idx] = "Load"
                return Path(*parts)
        root_dump, root_load = default_emulator_roots(emulator)
        if root_dump and root_load and _path_is_within(dump, root_dump):
            return root_load / dump.relative_to(root_dump)
        return None
    return None


def discover_game_folders(emulator, selected_dump_path):
    """Return ``(game_id, dump_folder, load_folder)`` from one selected path.

    The path can be a global texture root, a per-game folder, or the final
    technical dump leaf.  Only folders with actual dump content/layout are
    returned for structured emulators.
    """
    emulator = str(emulator or "Generic")
    selected = Path(selected_dump_path).expanduser()
    excluded = {"new", "cache", "textures", "dump", "dumps", "load", "replacements", "replacement"}
    results = []

    def add(game_id, dump):
        game_id = str(game_id or "").strip()
        dump = Path(dump)
        if not game_id or game_id.casefold() in excluded:
            return
        load = derive_load_folder_for_emulator(emulator, dump)
        key = (game_id.casefold(), str(dump).casefold())
        if key not in {(gid.casefold(), str(dp).casefold()) for gid, dp, _lp in results}:
            results.append((game_id, dump, load))

    leaf = selected.name.casefold()
    if emulator in ("PCSX2", "DuckStation"):
        if leaf == "dumps":
            add(selected.parent.name, selected)
        elif (selected / "dumps").is_dir() and selected.name.casefold() != "textures":
            add(selected.name, selected / "dumps")
        else:
            try:
                for game in sorted((item for item in selected.iterdir() if item.is_dir()), key=lambda item: item.name.casefold()):
                    if (game / "dumps").is_dir():
                        add(game.name, game / "dumps")
            except Exception:
                pass
        return results

    if emulator == "PPSSPP":
        if leaf == "new":
            add(selected.parent.name, selected)
        elif selected.parent.name.casefold() == "textures" and leaf != "textures":
            add(selected.name, selected / "new" if (selected / "new").is_dir() else selected)
        else:
            try:
                for game in sorted((item for item in selected.iterdir() if item.is_dir()), key=lambda item: item.name.casefold()):
                    dump = game / "new"
                    if dump.is_dir():
                        add(game.name, dump)
            except Exception:
                pass
        return results

    if emulator in ("Nintendo 64 / RMG", "Nintendo 64 / Project64"):
        if leaf == "glidenhq":
            add(selected.parent.name, selected)
        elif selected.parent.name.casefold() == "texture_dump":
            add(selected.name, selected / "GLideNHQ" if (selected / "GLideNHQ").is_dir() else selected)
        else:
            try:
                for game in sorted((item for item in selected.iterdir() if item.is_dir()), key=lambda item: item.name.casefold()):
                    dump = game / "GLideNHQ" if (game / "GLideNHQ").is_dir() else game
                    add(game.name, dump)
            except Exception:
                pass
        return results

    is_root = (
        leaf == "texdump" or leaf == "texture_dump" or
        (leaf == "textures" and selected.parent.name.casefold() == "dump")
    )
    if emulator in ("Dolphin", "Azahar / Citra", FLYCAST_EMULATOR):
        if not is_root and selected.parent.exists():
            add(selected.name, selected)
        else:
            try:
                for game in sorted((item for item in selected.iterdir() if item.is_dir()), key=lambda item: item.name.casefold()):
                    if game.name.casefold() not in excluded:
                        add(game.name, game)
            except Exception:
                pass
        return results

    try:
        for game in sorted((item for item in selected.iterdir() if item.is_dir()), key=lambda item: item.name.casefold()):
            if game.name.casefold() not in excluded:
                add(game.name, game)
    except Exception:
        pass
    return results

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
    "pause_when_comfy_offline", "auto_start_comfy_when_watching", "auto_check_missing_load",
    "enable_separate_alpha_workflow", "alpha_workflow_invert_output",
    "enable_vram_protection", "max_vram_gb", "vram_resume_margin_gb",
    "skip_cutscene_buffers", "skip_dynamic_efb_postprocess", "delete_skipped_cutscene_buffers",
    "auto_scan_delete_cutscene_buffers_on_start",
    "auto_quarantine_efb_cutscenes", "auto_quarantine_live_threshold",
    "auto_quarantine_live_idle_seconds",
    "auto_sync_azahar_pack_json", "live_texture_preview", "faithfulness_preset",
    "manager_sort_by", "manager_group_by"
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
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(PROFILES_PATH, json.dumps(data, indent=2), encoding="utf-8")

def load_batch_queue_state():
    default = {
        "queue": [],
        "shutdown_when_finished": False,
        "last_status": "idle",
        "last_current_index": -1
    }
    if BATCH_QUEUE_PATH.exists():
        try:
            data = json.loads(BATCH_QUEUE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                default.update(data)
        except Exception:
            pass
    if not isinstance(default.get("queue"), list):
        default["queue"] = []
    return default

def save_batch_queue_state(queue, shutdown_when_finished=False, last_status="idle", last_current_index=-1):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "queue": list(queue),
        "shutdown_when_finished": bool(shutdown_when_finished),
        "last_status": str(last_status),
        "last_current_index": int(last_current_index)
    }
    temp = BATCH_QUEUE_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp.replace(BATCH_QUEUE_PATH)

BUILD_DEFAULTS_VERSION = "11.10.6-dump-path-discovery-v1"

_MANAGED_RGB_WORKFLOW_FILENAMES = {
    "faithful_rgb_workflow_api.json",
    "faithful_rgb_workflow_api_clean_heart.json",
    "faithful_rgb_workflow_api_strong_believer.json",
    "faithful_rgb_workflow_api_midway.json",
    "faithful_rgb_workflow_api_soft_heart.json",
}
_MANAGED_ALPHA_WORKFLOW_FILENAMES = {
    "faithful_alpha_workflow_api.json",
}
_MANAGED_NON_ALPHA_WORKFLOW_FILENAMES = set(_MANAGED_RGB_WORKFLOW_FILENAMES) | {
    "faithful_n64_strip_safe_api.json",
}


def _portable_path_parts(value):
    """Split Windows or POSIX paths without depending on the host OS."""
    return [part for part in re.split(r"[\\/]+", str(value or "").strip()) if part]


def _looks_like_managed_bundled_workflow(value, allowed_filenames):
    """Return True only for a workflow that belongs to a Faithful build.

    Custom workflow paths are deliberately excluded, even when they currently
    exist. Old bundled paths are recognized so they can be rebased to the
    workflows shipped beside the executable that is actually running.
    """
    parts = _portable_path_parts(value)
    if not parts or parts[-1].casefold() not in allowed_filenames:
        return False
    if len(parts) < 2 or parts[-2].casefold() != "workflows":
        return False
    ancestors = " ".join(parts[:-2]).casefold()
    return "faithful-remaster" in ancestors or "faithful remaster" in ancestors


def apply_current_build_defaults(profile_data):
    """Synchronize managed settings while preserving genuine user choices.

    Managed bundled routes are rebased on every startup, not merely when their
    old files disappear. This prevents profiles from continuing to point at an
    older extracted release that still happens to exist on disk.
    """
    bundled_rgb = APP_DIR / "workflows" / "Faithful_RGB_Workflow_API_Clean_Heart.json"
    bundled_alpha = APP_DIR / "workflows" / "Faithful_Alpha_Workflow_API.json"
    changed = False

    # This refreshes the built-in Clean Heart / Strong Believer definitions to
    # the current APP_DIR before game profile routes are synchronized.
    workflow_profiles = load_workflow_profiles()

    def existing_file(value):
        try:
            path = Path(str(value or "")).expanduser()
            return path if path.is_file() else None
        except Exception:
            return None

    def selected_rgb_profile(settings):
        selected = find_workflow_profile(settings.get("faithfulness_preset", "Clean Heart"))
        if selected and existing_file(selected.get("api_path")) is not None:
            return selected
        selected = find_workflow_profile("Clean Heart")
        if selected and existing_file(selected.get("api_path")) is not None:
            return selected
        return {
            "name": "Clean Heart", "api_path": str(bundled_rgb),
            "load_node": "1", "save_node": "4", "builtin": True,
        }

    def repair_managed_routes(settings):
        local_changed = False
        chosen = selected_rgb_profile(settings)
        current_rgb = settings.get("workflow_api_json")
        rgb_is_managed = _looks_like_managed_bundled_workflow(
            current_rgb, _MANAGED_RGB_WORKFLOW_FILENAMES
        )
        if existing_file(current_rgb) is None or rgb_is_managed:
            target_rgb = str(Path(str(chosen.get("api_path") or bundled_rgb)).resolve())
            target_load = str(chosen.get("load_node") or "1")
            target_save = str(chosen.get("save_node") or "4")
            if str(current_rgb or "") != target_rgb:
                settings["workflow_api_json"] = target_rgb
                local_changed = True
            if str(settings.get("load_image_node_id") or "") != target_load:
                settings["load_image_node_id"] = target_load
                local_changed = True
            if str(settings.get("save_image_node_id") or "") != target_save:
                settings["save_image_node_id"] = target_save
                local_changed = True

        current_alpha = settings.get("alpha_workflow_api_json")
        alpha_is_managed = _looks_like_managed_bundled_workflow(
            current_alpha, _MANAGED_ALPHA_WORKFLOW_FILENAMES
        )
        alpha_is_known_wrong_route = _looks_like_managed_bundled_workflow(
            current_alpha, _MANAGED_NON_ALPHA_WORKFLOW_FILENAMES
        )
        if existing_file(current_alpha) is None or alpha_is_managed or alpha_is_known_wrong_route:
            target_alpha = str(bundled_alpha.resolve())
            if str(current_alpha or "") != target_alpha:
                settings["alpha_workflow_api_json"] = target_alpha
                local_changed = True
            if str(settings.get("alpha_load_image_node_id") or "") != "1":
                settings["alpha_load_image_node_id"] = "1"
                local_changed = True
            if str(settings.get("alpha_save_image_node_id") or "") != "5":
                settings["alpha_save_image_node_id"] = "5"
                local_changed = True
        return local_changed

    # Managed routes are synchronized on every startup. New settings are added
    # to every existing profile, while explicit user toggles remain untouched.
    for record in profile_data.setdefault("profiles", {}).values():
        settings = record.setdefault("settings", {})
        changed = repair_managed_routes(settings) or changed
        settings.setdefault("enable_separate_alpha_workflow", True)
        if settings.get("enable_vram_protection") is not False:
            settings["enable_vram_protection"] = False
            changed = True
        if settings.get("skip_cutscene_buffers") is not True:
            settings["skip_cutscene_buffers"] = True
            changed = True
        if settings.get("skip_dynamic_efb_postprocess") is not True:
            settings["skip_dynamic_efb_postprocess"] = True
            changed = True
        if settings.get("delete_skipped_cutscene_buffers") is not False:
            settings["delete_skipped_cutscene_buffers"] = False
            changed = True
        if settings.get("auto_scan_delete_cutscene_buffers_on_start") is not False:
            settings["auto_scan_delete_cutscene_buffers_on_start"] = False
            changed = True
        emulator = str(record.get("emulator") or "").strip()
        if "auto_quarantine_efb_cutscenes" not in settings:
            settings["auto_quarantine_efb_cutscenes"] = emulator in AUTO_BUFFER_QUARANTINE_EMULATORS
            changed = True
        if "auto_quarantine_live_threshold" not in settings:
            settings["auto_quarantine_live_threshold"] = 12
            changed = True
        if "auto_quarantine_live_idle_seconds" not in settings:
            settings["auto_quarantine_live_idle_seconds"] = 5.0
            changed = True
        normalized = normalize_faithfulness_preset(settings.get("faithfulness_preset", "Clean Heart"))
        if settings.get("faithfulness_preset") != normalized:
            settings["faithfulness_preset"] = normalized
            changed = True

    if profile_data.get("build_defaults_version") != BUILD_DEFAULTS_VERSION:
        profile_data["build_defaults_version"] = BUILD_DEFAULTS_VERSION
        changed = True

    if changed:
        save_profiles_data(profile_data)

    cfg = load_config()
    cfg_changed = repair_managed_routes(cfg)
    cfg.setdefault("enable_separate_alpha_workflow", True)
    if cfg.get("enable_vram_protection") is not False:
        cfg["enable_vram_protection"] = False
        cfg_changed = True
    if cfg.get("skip_cutscene_buffers") is not True:
        cfg["skip_cutscene_buffers"] = True
        cfg_changed = True
    if cfg.get("skip_dynamic_efb_postprocess") is not True:
        cfg["skip_dynamic_efb_postprocess"] = True
        cfg_changed = True
    if cfg.get("delete_skipped_cutscene_buffers") is not False:
        cfg["delete_skipped_cutscene_buffers"] = False
        cfg_changed = True
    if cfg.get("auto_scan_delete_cutscene_buffers_on_start") is not False:
        cfg["auto_scan_delete_cutscene_buffers_on_start"] = False
        cfg_changed = True
    active_record = profile_data.get("profiles", {}).get(profile_data.get("active_profile", ""), {})
    active_settings = active_record.get("settings", {}) if isinstance(active_record, dict) else {}
    if "auto_quarantine_efb_cutscenes" not in cfg:
        active_emulator = str(active_record.get("emulator") or "").strip() if isinstance(active_record, dict) else ""
        cfg["auto_quarantine_efb_cutscenes"] = active_emulator in AUTO_BUFFER_QUARANTINE_EMULATORS
        cfg_changed = True
    for key, default_value in (
        ("auto_quarantine_live_threshold", 12),
        ("auto_quarantine_live_idle_seconds", 5.0),
    ):
        if key not in cfg:
            cfg[key] = active_settings.get(key, default_value)
            cfg_changed = True
    normalized_cfg = normalize_faithfulness_preset(cfg.get("faithfulness_preset", "Clean Heart"))
    if cfg.get("faithfulness_preset") != normalized_cfg:
        cfg["faithfulness_preset"] = normalized_cfg
        cfg_changed = True
    if cfg_changed:
        save_config(cfg)

    # Normalize saved per-texture overrides, but never rewrite a valid custom
    # workflow profile or Alpha route during a version migration.
    try:
        for profile_dir in PROFILES_DIR.iterdir():
            if not profile_dir.is_dir():
                continue
            overrides_path = texture_preset_overrides_path(profile_dir)
            if not overrides_path.exists():
                continue
            overrides = load_texture_preset_overrides(profile_dir)
            save_texture_preset_overrides(profile_dir, overrides)
    except Exception:
        pass

    return changed or cfg_changed

class V11App(App):
    def __init__(self):
        tk.Tk.__init__(self)
        self.title(APP_TITLE)
        self._app_icon_photo = None
        self._header_logo_photo = None
        self._native_icon_handles = None
        self._native_icon_hwnd = None
        self._app_icon_ico = APP_DIR / "assets" / "faithful_remaster.ico"
        try:
            icon_png = APP_DIR / "assets" / "faithful_remaster_icon_64.png"
            if icon_png.exists():
                # PIL's Tk bridge is more consistent than Tcl's PNG loader on
                # older Windows/Tk combinations, while keeping a reference
                # prevents the image from being garbage-collected.
                if PIL_AVAILABLE:
                    with Image.open(icon_png) as icon_image:
                        self._app_icon_photo = ImageTk.PhotoImage(icon_image.convert("RGBA"))
                else:
                    self._app_icon_photo = tk.PhotoImage(file=str(icon_png))
                self.iconphoto(True, self._app_icon_photo)
            if os.name == "nt" and self._app_icon_ico.exists():
                try:
                    self.wm_iconbitmap(str(self._app_icon_ico))
                except Exception:
                    pass
        except Exception:
            self._app_icon_photo = None
        self.geometry("1540x920")
        self.minsize(1180, 760)
        self.apply_dark_theme()
        if os.name == "nt":
            # Apply after the native wrapper is created, then once more after
            # the window is mapped. This fixes both title-bar and taskbar icons.
            self.after_idle(lambda: apply_native_windows_icon(self, self._app_icon_ico))
            self.after(250, lambda: apply_native_windows_icon(self, self._app_icon_ico))

        self.profile_data = load_profiles_data()
        apply_current_build_defaults(self.profile_data)
        self.current_profile_name = self.profile_data.get("active_profile", "")
        base_cfg = load_config()

        if self.current_profile_name in self.profile_data.get("profiles", {}):
            base_cfg.update(self.profile_data["profiles"][self.current_profile_name].get("settings", {}))

        # Bundled workflow defaults for this build. Existing profiles were migrated above.
        bundled_rgb = APP_DIR / "workflows" / "Faithful_RGB_Workflow_API_Clean_Heart.json"
        bundled_alpha = APP_DIR / "workflows" / "Faithful_Alpha_Workflow_API.json"
        if not Path(str(base_cfg.get("workflow_api_json", ""))).exists():
            base_cfg["workflow_api_json"] = str(bundled_rgb)
            base_cfg["load_image_node_id"] = 1
            base_cfg["save_image_node_id"] = 4
        if not Path(str(base_cfg.get("alpha_workflow_api_json", ""))).exists():
            base_cfg["alpha_workflow_api_json"] = str(bundled_alpha)
            base_cfg["alpha_load_image_node_id"] = 1
            base_cfg["alpha_save_image_node_id"] = 5
        base_cfg["enable_vram_protection"] = False
        base_cfg["delete_skipped_cutscene_buffers"] = False

        self.cfg = base_cfg
        self.log_q = queue.Queue()
        self.stop_event = threading.Event()
        self.force_scan_event = threading.Event()
        self.force_missing_event = threading.Event()
        self.worker_thread = None
        self.worker = None
        self.batch_state = load_batch_queue_state()
        known_profiles = self.profile_data.get("profiles", {})
        self.batch_queue = [name for name in self.batch_state.get("queue", []) if name in known_profiles]
        self.batch_active = False
        self.batch_current_index = -1
        self.batch_shutdown_requested = False
        self.batch_available_names = []
        self._batch_launching = False
        self._batch_skip_requested = False
        self._batch_previous_requested = False
        self._batch_last_started_profile = ""
        self.vars = {}
        self.stats = {
            "processed": 0, "cache_hits": 0, "comfy_jobs": 0, "queue_len": 0,
            "manager_queue_len": 0, "high_queue_len": 0, "low_queue_len": 0, "peak_vram_mb": 0,
            "status": "STOPPED", "comfy_online": False, "comfy_running": None,
            "comfy_pending": None, "comfy_error": "", "exceptions_skipped": 0,
            "cutscene_buffers_skipped": 0, "cutscene_buffers_deleted": 0,
            "startup_cleanup_scanned": 0, "startup_cleanup_detected": 0,
            "startup_cleanup_deleted": 0, "startup_cleanup_failed": 0,
            "startup_cleanup_recent_skipped": 0,
            "current_input_path": "", "current_output_path": "",
            "current_texture_stage": "Waiting", "current_texture_started_at": 0.0,
            "current_faithfulness_preset": "Clean Heart", "current_priority_lane": "IDLE", "failed": 0
        }
        self._preview_last_input = ""
        self._preview_last_output = ""
        self._preview_original_photo = None
        self._preview_enhanced_photo = None
        self._session_started_at = None
        self._session_initial_done = 0
        self._session_last_elapsed = 0.0
        self._progress_cache = {"at": 0.0, "profile": "", "done": 0, "total": 0}
        self._batch_progress_cache_at = 0.0
        self._preview_resize_after = None
        self._dashboard_metrics_running = False
        self._dashboard_metrics_refresh_pending = False
        self._dashboard_metrics_after = None
        self._dashboard_metrics_cache = {
            "progress": {}, "vram_used_mb": None, "vram_total_mb": None,
            "updated_at": 0.0
        }
        self.comfy_status_var = tk.StringVar(value="Not checked")
        self.comfy_detail_var = tk.StringVar(value="")
        self._comfy_check_running = False
        self._loading_profile = False
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        APP_LOG_PATH.touch(exist_ok=True)
        self.seed_local_game_database()
        self.build()
        self.after(500, self.install_hover_help)
        self.after(200, self.poll_log)
        self.after(250, self._dashboard_metrics_tick)
        self.after(1000, self.update_dashboard)
        self.after(3000, self.auto_check_comfy)

    def install_hover_help(self):
        """Attach concise explanations to important controls throughout the UI."""
        explanations = {
            "Start Watching": "Continuously watches the selected dump folder and processes new textures as they appear.",
            "Stop": "Stops watching and prevents new textures from being queued. The current ComfyUI job may finish first.",
            "Force Dump Check": "Immediately rescans the dump folder for new or changed textures.",
            "Check Missing Load": "Queues dump textures that do not currently have a matching output texture.",
            "Overwrite Existing": "Reprocesses textures even when an output file already exists. Useful for testing a new workflow.",
            "Preserve Alpha": "Keeps transparency by processing RGB and alpha separately, then recombining them.",
            "Process .tmp image files": "Treats valid image files with a .tmp extension as textures.",
            "Enable hash cache": "Reuses previously processed identical textures to avoid unnecessary ComfyUI work.",
            "Ignore existing outputs silently": "Skips textures that already have output files without filling the log with messages.",
            "Prioritize newly dumped textures": "Processes newly dumped and missing-output textures before the initial backlog. Texture Manager recreation requests always run first.",
            "Skip cutscene buffers and empty/black dumps": "Skips likely cutscene frame buffers, fully transparent textures, and nearly black empty dumps.",
            "Quarantine skipped cutscene / blank dumps": "Moves automatically skipped cutscene or blank dumps into the current profile quarantine instead of deleting them. Dynamic EFB stays in place during live watching; use the manual EFB + Cutscene quarantine button when desired.",
            "Safe startup blank-dump quarantine": "Before the first queue is built, moves only fully transparent or near-solid-black dumps into a reversible profile quarantine. Dynamic EFB and grayscale textures are excluded.",
            "Enable ComfyUI status monitoring": "Periodically checks whether ComfyUI is online and reports its queue status.",
            "Pause processing when ComfyUI is offline": "Keeps textures queued instead of failing them while ComfyUI is unavailable.",
            "If ComfyUI is offline, start it automatically": "When Start Watching is pressed, checks ComfyUI and launches the configured start file only if the server is offline.",
            "Automatically check missing Load files": "Periodically looks for dump textures whose output files are missing.",
            "Enable separate alpha workflow": "Uses the Alpha API workflow for transparency while the RGB workflow handles color.",
            "Invert output": "Inverts the alpha workflow output before recombining it with RGB. Enable only when the resulting transparency is reversed.",
            "Enable VRAM protection": "Pauses new jobs when GPU memory usage exceeds the configured limit. Usually unnecessary unless ComfyUI runs out of VRAM.",
            "Auto-sync Azahar pack.json from Dump to Load": "Copies Azahar's pack.json to the output folder when it changes.",
            "Live texture preview": "Shows the current original and remastered texture while processing.",
            "Auto-fill Folders": "Builds exact per-game dump and replacement paths from the emulator and Game ID.",
            "Lookup Full Title": "Looks up the readable game title using the current Game ID.",
            "Scan for Games": "Scans one selected dump location, detects its emulator and derives exact replacement folders.",
            "Auto-discover games": "Allows the program to detect game folders from known emulator locations.",
            "Automatically add discovered games": "Creates profiles immediately for discovered games without asking you to review each one.",
            "Save Profile": "Saves the current emulator, game paths, workflows, and settings to this profile.",
            "New": "Creates a new game profile.",
            "Duplicate": "Copies the current profile, including its folders and processing settings.",
            "Rename": "Changes the current profile name without changing its folders or settings.",
            "Delete": "Deletes the selected profile only. Texture files are not removed.",
            "Start Batch Queue": "Processes the queued profiles one after another for unattended or overnight work.",
            "Stop Batch Queue": "Stops the batch after the active processing step is safely halted.",
            "Skip to next game": "Safely stops only the current profile and continues with the next queued game.",
            "Previous game": "Safely stops the current profile and returns to the previous queued game without deleting outputs or cache.",
            "Shutdown PC when queue finishes": "Shuts down Windows after every profile in the batch queue completes.",
            "Move Up": "Moves the selected profile earlier in the batch order.",
            "Move Down": "Moves the selected profile later in the batch order.",
            "Start ComfyUI": "Starts ComfyUI using the configured start file.",
            "Check Comfy Now": "Immediately checks the configured ComfyUI address and queue status.",
            "Auto Detect RGB Nodes": "Finds the LoadImage and SaveImage node IDs in the selected RGB API workflow.",
            "Auto Detect Alpha Nodes": "Finds the LoadImage and SaveImage node IDs in the selected Alpha API workflow.",
            "Texture Manager": "Opens the integrated texture workspace for previews, per-texture modes, recreation and exceptions.",
            "Safe Cleanup Now": "Scans the current game's dump folder and moves only fully transparent or near-solid-black dumps into a reversible profile quarantine. Dynamic EFB and grayscale textures are excluded.",
            "Add to exceptions": "Prevents the selected texture filename from being processed in future scans.",
            "Remove from exceptions": "Removes every saved exception pattern that currently matches the selected texture.",
            "Delete Load texture": "Deletes only the remastered output so it can be recreated from the original dump.",
            "Recreate Selected Texture(s)": "Deletes selected outputs, clears their processed/cache records, and queues them for recreation without touching original dumps.",
            "Recreate with Selected Preset": "Assigns the chosen faithfulness preset to the selected textures, then recreates them.",
            "Apply Selected Preset": "Saves a per-texture any enabled workflow-profile override.",
            "Clear Preset Override": "Returns selected textures to the game profile default preset.",
            "Open Dump folder": "Opens the folder containing the selected original dump texture.",
            "Open Load folder": "Opens the output folder for the selected texture.",
        }

        # Also match common label text so entries receive explanations.
        label_explanations = {
            "Dump folder": "Folder where the emulator writes original dumped textures for this game.",
            "Load folder": "Folder where remastered textures must be saved for the emulator to load them.",
            "RGB workflow API JSON": "ComfyUI API-format workflow used for normal color texture processing.",
            "Alpha workflow API JSON": "ComfyUI API-format workflow used only for attached transparency.",
            "ComfyUI URL": "Address of the running ComfyUI server, normally http://127.0.0.1:8188.",
            "ComfyUI start file": "Batch file or executable used by Faithful Remaster to start ComfyUI.",
            "RGB Load node ID": "Node ID of the LoadImage node in the RGB API workflow.",
            "RGB Save node ID": "Node ID of the SaveImage node in the RGB API workflow.",
            "Alpha Load node ID": "Node ID of the LoadImage node in the Alpha API workflow.",
            "Alpha Save node ID": "Node ID of the SaveImage node in the Alpha API workflow.",
            "Game ID": "Emulator-specific game folder or serial used to identify the title.",
            "Game Name": "Readable title shown in profiles and the batch queue.",
        }
        explanations.update(label_explanations)
        self._hover_tooltips = []

        def walk(widget):
            try:
                text = str(widget.cget("text")).strip()
            except Exception:
                text = ""
            help_text = getattr(widget, "_hover_help", "") or explanations.get(text)
            if help_text:
                self._hover_tooltips.append(HoverTooltip(widget, help_text))
            try:
                for child in widget.winfo_children():
                    walk(child)
            except Exception:
                pass

        walk(self)

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
        """Create a consistent modern settings card while preserving legacy callers."""
        outer = tk.Frame(parent, bg="#223141")
        outer.pack(fill="x", padx=12, pady=8)
        shell = tk.Frame(outer, bg="#0b1119")
        shell.pack(fill="both", expand=True, padx=1, pady=1)
        header = tk.Frame(shell, bg="#0d151f", height=36)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header, text=str(title).upper(), bg="#0d151f", fg="#dce8ef",
            font=("Segoe UI", 9, "bold"), anchor="w"
        ).pack(fill="both", expand=True, padx=12)
        body = tk.Frame(shell, bg="#0b1119")
        body.pack(fill="both", expand=True, padx=2, pady=(2, 4))
        return body

    def create_scrollable_tab(self, notebook=None):
        """Create a stable scrollable Notebook page without geometry oscillation."""
        notebook = notebook or self.tabs
        page = tk.Frame(notebook, bg="#070b11")
        canvas = tk.Canvas(page, highlightthickness=0, borderwidth=0, bg="#0b1119")
        scrollbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
        content = tk.Frame(canvas, bg="#0b1119")
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        # Keep the scrollbar mapped permanently. Hiding/showing it changed the
        # canvas width, which could trigger an endless resize feedback loop.
        scrollbar.pack(side="right", fill="y")

        state = {"width": None, "scrollregion": None}

        def update_scroll_region(_event=None):
            bbox = canvas.bbox("all") or (0, 0, 0, 0)
            if bbox != state["scrollregion"]:
                state["scrollregion"] = bbox
                canvas.configure(scrollregion=bbox)

        def resize_content(event):
            width = max(1, int(event.width))
            if state["width"] != width:
                state["width"] = width
                canvas.itemconfigure(window_id, width=width)
            canvas.after_idle(update_scroll_region)

        def activate(_event=None):
            self._active_scroll_canvas = canvas

        def deactivate(_event=None):
            if getattr(self, "_active_scroll_canvas", None) is canvas:
                self._active_scroll_canvas = None

        content.bind("<Configure>", lambda _e: canvas.after_idle(update_scroll_region))
        canvas.bind("<Configure>", resize_content)
        page.bind("<Enter>", activate)
        page.bind("<Leave>", deactivate)
        canvas.bind("<Enter>", activate)
        content.bind("<Enter>", activate)

        if not getattr(self, "_global_scroll_installed", False):
            self._active_scroll_canvas = None

            def global_wheel(event):
                active = getattr(self, "_active_scroll_canvas", None)
                if active is None or not active.winfo_exists():
                    return
                bbox = active.bbox("all")
                if not bbox or bbox[3] <= active.winfo_height() + 2:
                    return
                widget = self.winfo_containing(event.x_root, event.y_root)
                if widget is not None and widget is not active:
                    cls = widget.winfo_class()
                    if cls in {"Listbox", "Text", "Treeview", "TCombobox", "Canvas"}:
                        return
                if event.delta:
                    steps = -1 if event.delta > 0 else 1
                    active.yview_scroll(steps, "units")
                    return "break"

            def global_linux_up(_event):
                active = getattr(self, "_active_scroll_canvas", None)
                if active is not None:
                    active.yview_scroll(-1, "units")
                    return "break"

            def global_linux_down(_event):
                active = getattr(self, "_active_scroll_canvas", None)
                if active is not None:
                    active.yview_scroll(1, "units")
                    return "break"

            self.bind_all("<MouseWheel>", global_wheel, add="+")
            self.bind_all("<Button-4>", global_linux_up, add="+")
            self.bind_all("<Button-5>", global_linux_down, add="+")
            self._global_scroll_installed = True

        page._scroll_canvas = canvas
        page._scroll_content = content
        page._scrollbar = scrollbar
        return page, content

    def build(self):
        # Header
        header = tk.Frame(self, bg="#060a0f", height=76)
        header.pack(fill="x")
        header.pack_propagate(False)

        logo_path = APP_DIR / "assets" / "faithful_remaster_icon.png"
        try:
            if PIL_AVAILABLE and logo_path.exists():
                logo_image = Image.open(logo_path).convert("RGBA")
                logo_image.thumbnail((52, 52), Image.Resampling.LANCZOS)
                self._header_logo_photo = ImageTk.PhotoImage(logo_image)
                tk.Label(
                    header, image=self._header_logo_photo, bg="#060a0f",
                    bd=0, highlightthickness=0
                ).pack(side="left", padx=(14, 9), pady=10)
            else:
                raise RuntimeError("Bundled icon unavailable")
        except Exception:
            # Minimal high-contrast fallback, used only if Pillow/icon loading fails.
            logo = tk.Canvas(header, width=52, height=52, bg="#060a0f", highlightthickness=0)
            logo.pack(side="left", padx=(14, 9), pady=10)
            logo.create_rectangle(3, 3, 49, 49, fill="#0a1822", outline="#35ded0", width=3)
            logo.create_polygon(8, 43, 8, 9, 43, 9, fill="#0b8f87", outline="")
            logo.create_polygon(8, 43, 43, 9, 43, 43, fill="#f0b94e", outline="")
            logo.create_line(8, 43, 43, 9, fill="#f8ffff", width=4)

        title_wrap = tk.Frame(header, bg="#060a0f")
        title_wrap.pack(side="left", pady=12)
        tk.Label(title_wrap, text="Faithful Remaster", font=("Segoe UI", 20, "bold"),
                 fg="#f2f7fa", bg="#060a0f").pack(anchor="w")
        tk.Label(title_wrap, text=f"v{APP_VERSION}  Texture restoration workspace", font=("Segoe UI", 9),
                 fg="#79a9d8", bg="#060a0f").pack(anchor="w", pady=(1, 0))

        # Large, clickable active-profile identity card.
        self.profile_header_name_var = tk.StringVar(value="No profile selected")
        self.profile_header_meta_var = tk.StringVar(value="Open Profiles to configure a game")
        self.profile_header_mode_var = tk.StringVar(value="CLEAN HEART")
        self.profile_header_paths_var = tk.StringVar(value="● Waiting for profile")
        self.profile_header_var = self.profile_header_name_var  # compatibility alias

        self.header_profile_card = tk.Frame(
            header, bg="#0d1822", highlightthickness=1,
            highlightbackground="#26394a", cursor="hand2"
        )
        self.header_profile_card.pack(side="right", padx=14, pady=8, ipadx=3, ipady=2)
        identity = tk.Frame(self.header_profile_card, bg="#0d1822")
        identity.pack(side="left", fill="both", expand=True, padx=(12, 9), pady=7)
        tk.Label(identity, text="ACTIVE PROFILE", bg="#0d1822", fg="#61d8cc",
                 font=("Segoe UI", 7, "bold"), anchor="w").pack(fill="x")
        self.header_profile_name_label = tk.Label(
            identity, textvariable=self.profile_header_name_var, bg="#0d1822", fg="#f4f8fb",
            font=("Segoe UI", 12, "bold"), anchor="w", width=38
        )
        self.header_profile_name_label.pack(fill="x", pady=(1, 0))
        tk.Label(identity, textvariable=self.profile_header_meta_var, bg="#0d1822", fg="#9fb1bf",
                 font=("Segoe UI", 8), anchor="w").pack(fill="x")

        status_col = tk.Frame(self.header_profile_card, bg="#0d1822")
        status_col.pack(side="right", padx=(0, 10), pady=7)
        self.header_profile_mode_label = tk.Label(
            status_col, textvariable=self.profile_header_mode_var, bg="#123b3b", fg="#70f0e3",
            padx=10, pady=3, font=("Segoe UI", 8, "bold")
        )
        self.header_profile_mode_label.pack(anchor="e")
        self.header_profile_paths_label = tk.Label(
            status_col, textvariable=self.profile_header_paths_var, bg="#0d1822", fg="#7edaa0",
            font=("Segoe UI", 7, "bold"), anchor="e"
        )
        self.header_profile_paths_label.pack(anchor="e", pady=(5, 0))
        self._bind_recursive(self.header_profile_card, "<Button-1>", lambda _e: self.tabs.select(self.page_profiles))

        # Daily use first, setup second, multi-game work third, inspection fourth.
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=(6, 8))

        self.page_dashboard = tk.Frame(self.tabs, bg="#070b11")
        self.page_profiles, self.tab_profiles = self.create_scrollable_tab(self.tabs)
        self.page_batch = tk.Frame(self.tabs, bg="#070b11")
        self.tab_batch = self.page_batch
        self.page_manager = tk.Frame(self.tabs, bg="#070b11")
        self.tab_manager = self.page_manager
        self.page_settings = tk.Frame(self.tabs, bg="#070b11")

        self.tabs.add(self.page_dashboard, text="Dashboard")
        self.tabs.add(self.page_profiles, text="Profiles")
        self.tabs.add(self.page_batch, text="Batch Queue")
        self.tabs.add(self.page_manager, text="Texture Manager")
        self.tabs.add(self.page_settings, text="Settings")
        self.tabs.bind("<<NotebookTabChanged>>", self._on_main_tab_changed, add="+")

        self.settings_tabs = ttk.Notebook(self.page_settings, style="Settings.TNotebook")
        self.settings_tabs.pack(fill="both", expand=True, padx=6, pady=6)
        self.page_workflows, self.tab_workflows = self.create_scrollable_tab(self.settings_tabs)
        self.page_processing, self.tab_processing = self.create_scrollable_tab(self.settings_tabs)
        self.page_logs, self.tab_logs = self.create_scrollable_tab(self.settings_tabs)
        self.settings_tabs.add(self.page_workflows, text="Workflows & Backends")
        self.settings_tabs.add(self.page_processing, text="Processing")
        self.settings_tabs.add(self.page_logs, text="Maintenance & Data")
        self.settings_tabs.bind("<<NotebookTabChanged>>", self._on_settings_tab_changed, add="+")

        # Shared live priority variables are created before Batch Queue and Dashboard.
        self.dashboard_manager_priority_var = tk.StringVar(value="0")
        self.dashboard_high_priority_var = tk.StringVar(value="0")
        self.dashboard_low_priority_var = tk.StringVar(value="0")
        self.priority_running_var = tk.StringVar(value="IDLE")
        self._priority_lane_widgets = {"MANAGER": [], "HIGH": [], "LOW": []}

        # Build shared variables before Dashboard binds to them.
        self.build_profiles_tab()
        self.build_batch_queue_section()
        self.build_manager_tab()
        self.build_workflows_tab()
        self.build_processing_tab()
        self.build_logs_tab()
        self.build_dashboard_tab()

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
                self.tabs.select(self.page_profiles)
    def _build_priority_lane_strip(self, parent, context="dashboard", padx=8, pady=5):
        """Build an always-visible Manager > High > Low priority strip."""
        outer, inner = self._dashboard_panel(
            parent, bg="#0b131d", border="#2b3d4e", padx=8, pady=6
        )
        outer.pack(fill="x", padx=padx, pady=pady)
        head = tk.Frame(inner, bg="#0b131d")
        head.pack(fill="x", padx=10, pady=(6, 3))
        tk.Label(head, text="LIVE PRIORITY QUEUE", bg="#0b131d", fg="#dce8ef",
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Label(head, text="Manager  →  High  →  Low", bg="#0b131d", fg="#71879a",
                 font=("Segoe UI", 8)).pack(side="left", padx=(12, 0))
        tk.Label(head, textvariable=self.priority_running_var, bg="#162433", fg="#9cc8f5",
                 padx=9, pady=2, font=("Segoe UI", 7, "bold")).pack(side="right")

        row = tk.Frame(inner, bg="#0b131d")
        row.pack(fill="x", padx=8, pady=(0, 8))
        lane_defs = [
            ("MANAGER", "Texture Manager requests", self.dashboard_manager_priority_var, "#d98cff"),
            ("HIGH", "New dumps + missing outputs", self.dashboard_high_priority_var, "#f0b94e"),
            ("LOW", "Initial backlog", self.dashboard_low_priority_var, "#69a9ff"),
        ]
        for index, (lane, detail, var, color) in enumerate(lane_defs):
            card = tk.Frame(row, bg="#111d29", highlightthickness=1, highlightbackground="#293c4d")
            card.pack(side="left", fill="x", expand=True, padx=3)
            text = tk.Frame(card, bg="#111d29")
            text.pack(side="left", fill="both", expand=True, padx=9, pady=6)
            title = tk.Label(text, text=lane, bg="#111d29", fg=color,
                             font=("Segoe UI", 9, "bold"), anchor="w")
            title.pack(fill="x")
            detail_label = tk.Label(text, text=detail, bg="#111d29", fg="#8ea1b1",
                                    font=("Segoe UI", 7), anchor="w")
            detail_label.pack(fill="x")
            count = tk.Label(card, textvariable=var, bg="#111d29", fg=color,
                             font=("Segoe UI", 18, "bold"), width=4)
            count.pack(side="right", padx=(2, 8), pady=5)
            self._priority_lane_widgets[lane].append((card, text, title, detail_label, count))
            if index < 2:
                tk.Label(row, text="›", bg="#0b131d", fg="#53697b",
                         font=("Segoe UI Symbol", 14, "bold")).pack(side="left", padx=1)
        return outer

    def _refresh_priority_lane_visuals(self):
        active = str(self.stats.get("current_priority_lane", "IDLE") or "IDLE").upper()
        worker_active = bool(self.worker_thread and self.worker_thread.is_alive())
        self.priority_running_var.set(f"RUNNING: {active}" if worker_active and active in {"MANAGER", "HIGH", "LOW"} else "IDLE")
        colors = {"MANAGER": "#d98cff", "HIGH": "#f0b94e", "LOW": "#69a9ff"}
        for lane, groups in self._priority_lane_widgets.items():
            is_active = worker_active and active == lane
            bg = "#1d2636" if is_active else "#111d29"
            border = colors[lane] if is_active else "#293c4d"
            for card, text, title, detail, count in groups:
                card.configure(bg=bg, highlightbackground=border, highlightthickness=2 if is_active else 1)
                for widget in (text, title, detail, count):
                    widget.configure(bg=bg)

    def _update_profile_header_card(self):
        if not hasattr(self, "profile_header_name_var"):
            return
        record = self.profile_data.get("profiles", {}).get(self.current_profile_name)
        if not record:
            emulator = self.profile_emulator_filter_var.get() if hasattr(self, "profile_emulator_filter_var") else "Generic"
            self.profile_header_name_var.set("No profile selected")
            self.profile_header_meta_var.set(f"{emulator}  •  Open Profiles to configure a game")
            self.profile_header_mode_var.set("NO PROFILE")
            self.profile_header_paths_var.set("● Setup required")
            self.header_profile_mode_label.configure(bg="#26313b", fg="#aebcc7")
            self.header_profile_paths_label.configure(fg="#f0b94e")
            return

        game_name = str(record.get("game_name") or self.current_profile_name or "Unnamed profile")
        emulator = str(record.get("emulator") or "Generic")
        game_id = str(record.get("game_id") or "No Game ID")
        settings = dict(DEFAULT_CONFIG)
        settings.update(record.get("settings", {}))
        # Reflect unsaved live edits when the current profile is open.
        if hasattr(self, "vars"):
            game_var = self.vars.get("game_name")
            id_var = self.vars.get("game_id")
            if game_var and game_var.get().strip():
                game_name = game_var.get().strip()
            if id_var and id_var.get().strip():
                game_id = id_var.get().strip()
        if hasattr(self, "emulator_var") and self.emulator_var.get():
            emulator = self.emulator_var.get()
        mode = normalize_faithfulness_preset(
            self.faithfulness_preset_var.get() if hasattr(self, "faithfulness_preset_var") else settings.get("faithfulness_preset", "Clean Heart")
        )
        dump_text = self.vars.get("dump_folder").get().strip() if hasattr(self, "vars") and self.vars.get("dump_folder") else str(settings.get("dump_folder", ""))
        load_text = self.vars.get("load_folder").get().strip() if hasattr(self, "vars") and self.vars.get("load_folder") else str(settings.get("load_folder", ""))
        paths_ready = bool(dump_text and load_text and Path(dump_text).exists())

        self.profile_header_name_var.set(game_name)
        self.profile_header_meta_var.set(f"{emulator}  •  {game_id}")
        self.profile_header_mode_var.set(mode.upper())
        self.profile_header_paths_var.set("● Paths ready" if paths_ready else "● Check folders")
        if mode == "Strong Believer":
            self.header_profile_mode_label.configure(bg="#463617", fg="#ffd477")
        else:
            self.header_profile_mode_label.configure(bg="#123b3b", fg="#70f0e3")
        self.header_profile_paths_label.configure(fg="#7edaa0" if paths_ready else "#f0b94e")

    def _dashboard_panel(self, parent, title=None, bg="#0d151f", border="#223141", padx=10, pady=10):
        outer = tk.Frame(parent, bg=border)
        inner = tk.Frame(outer, bg=bg)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        if title:
            tk.Label(
                inner, text=title.upper(), bg=bg, fg="#dce8ef",
                font=("Segoe UI", 9, "bold"), anchor="w"
            ).pack(fill="x", padx=padx, pady=(pady, 6))
        return outer, inner

    def _bind_recursive(self, widget, sequence, callback):
        try:
            widget.bind(sequence, callback, add="+")
            for child in widget.winfo_children():
                self._bind_recursive(child, sequence, callback)
        except Exception:
            pass

    def _build_mode_card(self, parent, mode, subtitle, detail, symbol):
        card = tk.Frame(parent, bg="#101923", highlightthickness=1, highlightbackground="#2b3948", cursor="hand2")
        card.pack(fill="x", padx=10, pady=5)
        top = tk.Frame(card, bg="#101923")
        top.pack(fill="x", padx=12, pady=(11, 3))
        icon = tk.Label(top, text=symbol, bg="#101923", fg="#8da1b4", font=("Segoe UI Symbol", 22, "bold"), width=2)
        icon.pack(side="left", padx=(0, 8))
        text_col = tk.Frame(top, bg="#101923")
        text_col.pack(side="left", fill="x", expand=True)
        name_label = tk.Label(text_col, text=mode, bg="#101923", fg="#f0f5f8", font=("Segoe UI", 11, "bold"), anchor="w")
        name_label.pack(fill="x")
        subtitle_label = tk.Label(text_col, text=subtitle, bg="#101923", fg="#79d9cf", font=("Segoe UI", 8, "bold"), anchor="w")
        subtitle_label.pack(fill="x", pady=(1, 0))
        check = tk.Label(top, text="", bg="#101923", fg="#dffefa", font=("Segoe UI Symbol", 12, "bold"), width=2)
        check.pack(side="right")
        detail_label = tk.Label(card, text=detail, bg="#101923", fg="#9babb9", font=("Segoe UI", 8), justify="left", anchor="w")
        detail_label.pack(fill="x", padx=54, pady=(1, 11))
        self.dashboard_mode_cards[mode] = {
            "frame": card, "widgets": [card, top, icon, text_col, name_label, subtitle_label, check, detail_label],
            "icon": icon, "check": check, "subtitle": subtitle_label
        }
        self._bind_recursive(card, "<Button-1>", lambda _e, m=mode: self._select_dashboard_mode(m))
        return card

    def _select_dashboard_mode(self, mode):
        mode = normalize_faithfulness_preset(mode)
        if mode not in BUILTIN_WORKFLOW_PROFILE_NAMES:
            return
        self.faithfulness_preset_var.set(mode)
        self.cfg["faithfulness_preset"] = mode
        if hasattr(self, "saved_state_var"):
            self.saved_state_var.set("● Unsaved changes")
        self._refresh_mode_cards()
        self.dashboard_mode_var.set(mode)
        self._update_profile_header_card()
        self.log(f"Remaster mode selected: {mode}")

    def _refresh_mode_cards(self):
        if not hasattr(self, "dashboard_mode_cards"):
            return
        selected = normalize_faithfulness_preset(
            self.faithfulness_preset_var.get() if hasattr(self, "faithfulness_preset_var") else "Clean Heart"
        )
        if getattr(self, "_last_mode_cards_selected", None) == selected:
            return
        self._last_mode_cards_selected = selected
        for mode, data in self.dashboard_mode_cards.items():
            active = mode == selected
            bg = "#0c2a2c" if active else "#101923"
            border = "#32c9bc" if active else "#2b3948"
            for widget in data["widgets"]:
                try:
                    widget.configure(bg=bg)
                except Exception:
                    pass
            data["frame"].configure(highlightbackground=border, highlightcolor=border, highlightthickness=2 if active else 1)
            data["icon"].configure(fg="#d9fffb" if active else "#8092a4")
            data["check"].configure(text="✓" if active else "", fg="#55e3d6")
            data["subtitle"].configure(fg="#5ee2d5" if active else "#7790a4")

    def _dashboard_setting_heading(self, parent, text):
        label = tk.Label(
            parent, text=str(text).upper(), bg="#0d151f", fg="#6f879a",
            font=("Segoe UI", 7, "bold"), anchor="w"
        )
        label.pack(fill="x", padx=11, pady=(7, 2))
        return label

    def _dashboard_setting_row(self, parent, text, variable, command=None, tooltip="", indent=0):
        """Compact dashboard setting: readable label on the left and a small ON/OFF switch."""
        row = tk.Frame(
            parent, bg="#101923", highlightthickness=1,
            highlightbackground="#223141", highlightcolor="#223141"
        )
        row.pack(fill="x", padx=(10 + int(indent), 10), pady=2)

        label = tk.Label(
            row, text=text, bg="#101923", fg="#cbd6df",
            font=("Segoe UI", 8, "bold"), anchor="w", justify="left"
        )
        label.pack(side="left", fill="x", expand=True, padx=(9, 5), pady=6)

        state_text = tk.StringVar(value="ON" if bool(variable.get()) else "OFF")

        def mark_changed():
            if command:
                command()
            if hasattr(self, "saved_state_var"):
                self.saved_state_var.set("● Unsaved changes")

        switch = tk.Checkbutton(
            row, textvariable=state_text, variable=variable, command=mark_changed,
            indicatoron=False, relief="flat", bd=0, width=4,
            padx=4, pady=3, cursor="hand2",
            bg="#182431", fg="#8fa1b1", selectcolor="#0f766e",
            activebackground="#1d3140", activeforeground="#ffffff",
            font=("Segoe UI", 7, "bold")
        )
        switch.pack(side="right", padx=6, pady=4)

        def refresh(*_args):
            enabled = bool(variable.get())
            state_text.set("ON" if enabled else "OFF")
            row.configure(highlightbackground="#2b766f" if enabled else "#223141")
            switch.configure(
                bg="#0f766e" if enabled else "#182431",
                fg="#e9fffc" if enabled else "#8fa1b1",
                selectcolor="#0f766e" if enabled else "#182431"
            )

        variable.trace_add("write", refresh)
        refresh()

        def toggle_from_row(_event=None):
            variable.set(not bool(variable.get()))
            mark_changed()
            return "break"

        row.bind("<Button-1>", toggle_from_row, add="+")
        label.bind("<Button-1>", toggle_from_row, add="+")
        row.configure(cursor="hand2")
        label.configure(cursor="hand2")

        if tooltip:
            row._hover_help = tooltip
            label._hover_help = tooltip
            switch._hover_help = tooltip
        return row

    def _dashboard_settings_menu(self, parent, title, items):
        """Compact advanced-settings button backed by a checkable popup menu."""
        button = tk.Button(
            parent, text="", relief="flat", bd=0, anchor="w", cursor="hand2",
            padx=10, pady=5, bg="#0d151f", fg="#8da1b4",
            activebackground="#121e2a", activeforeground="#dce8ef",
            highlightthickness=1, highlightbackground="#223141",
            font=("Segoe UI", 8, "bold")
        )
        button.pack(fill="x", padx=10, pady=(3, 2))

        menu = tk.Menu(
            button, tearoff=False, bg="#101923", fg="#d4dee6",
            activebackground="#0f766e", activeforeground="#ffffff",
            selectcolor="#43d9ca", relief="solid", bd=1,
            font=("Segoe UI", 9)
        )

        def mark_changed():
            if hasattr(self, "saved_state_var"):
                self.saved_state_var.set("● Unsaved changes")
            refresh_caption()

        for label, variable, help_text in items:
            menu.add_checkbutton(
                label=label, variable=variable, command=mark_changed,
                onvalue=True, offvalue=False
            )
            if help_text:
                # Menus cannot show HoverTooltip reliably; expose the detail in the status log.
                pass

        def refresh_caption(*_args):
            count = sum(1 for _label, variable, _help in items if bool(variable.get()))
            button.configure(text=f"Advanced cleanup  ·  {count} on   ▸")

        def open_menu():
            try:
                menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
            finally:
                try:
                    menu.grab_release()
                except Exception:
                    pass

        button.configure(command=open_menu)
        for _label, variable, _help in items:
            variable.trace_add("write", refresh_caption)
        refresh_caption()
        return button

    def build_dashboard_tab(self):
        page = self.page_dashboard
        page.configure(bg="#070b11")
        page.grid_rowconfigure(0, weight=1)
        page.grid_columnconfigure(0, weight=1)

        main = tk.PanedWindow(
            page, orient="horizontal", bg="#070b11", bd=0,
            sashwidth=6, sashrelief="flat", opaqueresize=True
        )
        main.grid(row=0, column=0, sticky="nsew", padx=3, pady=(3, 0))

        left = tk.Frame(main, bg="#0a1119", width=270)
        center = tk.Frame(main, bg="#070b11")
        right = tk.Frame(main, bg="#0a1119", width=365)
        main.add(left, minsize=245, width=270)
        main.add(center, minsize=560, stretch="always")
        main.add(right, minsize=315, width=365)
        self.dashboard_panes = main

        # LEFT: profile, mode, quick settings, queue summary.
        profile_outer, profile_inner = self._dashboard_panel(left, "Active profile")
        profile_outer.pack(fill="x", padx=7, pady=(7, 5))
        self.dashboard_profile_combo = ttk.Combobox(
            profile_inner, textvariable=self.profile_var, state="readonly"
        )
        self.dashboard_profile_combo.pack(fill="x", padx=10, pady=(0, 10))
        self.dashboard_profile_combo.bind("<<ComboboxSelected>>", self.on_profile_selected)

        mode_outer, mode_inner = self._dashboard_panel(left, "Processing mode")
        mode_outer.pack(fill="x", padx=7, pady=5)
        self.dashboard_mode_cards = {}
        self.dashboard_mode_var = tk.StringVar(value=normalize_faithfulness_preset(self.cfg.get("faithfulness_preset", "Clean Heart")))
        self._build_mode_card(
            mode_inner, "Clean Heart", "RECOMMENDED / DEFAULT",
            "Cleans noisy textures and keeps mixed atlases neat and faithful.", "♡"
        )
        self._build_mode_card(
            mode_inner, "Strong Believer", "STRONGER DETAIL PASS",
            "Preserves micro-detail for rough skin, ornaments and dense materials.", "✦"
        )
        self._refresh_mode_cards()

        quick_outer, quick_inner = self._dashboard_panel(left, "Quick settings", pady=7)
        quick_outer.pack(fill="x", padx=7, pady=5)
        self.live_preview_var = tk.BooleanVar(value=bool(self.cfg.get("live_texture_preview", True)))

        self._dashboard_setting_heading(quick_inner, "Preview")
        self._dashboard_setting_row(
            quick_inner, "Before / after preview", self.live_preview_var,
            self.on_live_preview_toggled,
            "Shows the original and remastered texture in the dashboard while processing."
        )

        self._dashboard_setting_heading(quick_inner, "Dump protection")
        self._dashboard_setting_row(
            quick_inner, "Auto-quarantine EFB + cutscenes", self.auto_quarantine_buffers_var,
            tooltip=(
                "Runs one strict bulk scan before Batch Queue or Start Watching, then groups newly "
                "detected buffers during live watching instead of moving them one by one."
            )
        )
        self._dashboard_setting_row(
            quick_inner, "Skip remaining dynamic EFB", self.dynamic_efb_filter_var,
            tooltip=(
                "Keeps confirmed dynamic EFB and post-processing buffers out of texture processing, "
                "including any buffers waiting for the next bulk quarantine move."
            )
        )

        self._dashboard_settings_menu(
            quick_inner, "Advanced cleanup",
            [
                (
                    "Live cutscene / blank quarantine",
                    self.delete_cutscene_var,
                    "Quarantines skipped cutscene or blank dumps discovered during live watching."
                ),
                (
                    "Blank cleanup at startup",
                    self.auto_cleanup_cutscene_var,
                    "Runs the conservative blank-dump quarantine pass when processing starts."
                ),
            ]
        )

        self._dashboard_setting_heading(quick_inner, "ComfyUI")
        self._dashboard_setting_row(
            quick_inner, "Auto-start when needed", self.auto_start_comfy_var,
            tooltip="Starts the configured ComfyUI launcher automatically when a workflow needs it."
        )

        summary_outer, summary_inner = self._dashboard_panel(left, "Queue summary")
        summary_outer.pack(fill="both", expand=True, padx=7, pady=(5, 7))
        self.dashboard_queued_var = tk.StringVar(value="0")
        self.dashboard_running_var = tk.StringVar(value="0")
        self.dashboard_done_var = tk.StringVar(value="0")
        self.dashboard_failed_var = tk.StringVar(value="0")
        for label, var, color, bullet in [
            ("Queued", self.dashboard_queued_var, "#69a9ff", "≡"),
            ("Running", self.dashboard_running_var, "#51d88a", "▶"),
            ("Done", self.dashboard_done_var, "#48d7c7", "✓"),
            ("Failed", self.dashboard_failed_var, "#ff7c86", "!"),
        ]:
            row = tk.Frame(summary_inner, bg="#0d151f")
            row.pack(fill="x", padx=12, pady=5)
            tk.Label(row, text=bullet, bg="#0d151f", fg=color, font=("Segoe UI Symbol", 11, "bold"), width=2).pack(side="left")
            tk.Label(row, text=label, bg="#0d151f", fg="#cbd6df", font=("Segoe UI", 9), anchor="w").pack(side="left", fill="x", expand=True)
            tk.Label(row, textvariable=var, bg="#0d151f", fg=color, font=("Segoe UI", 11, "bold")).pack(side="right")

        # CENTER: folders/actions, progress, current texture comparison.
        top_outer, top_inner = self._dashboard_panel(center, bg="#0d151f", border="#223141", padx=8, pady=7)
        top_outer.pack(fill="x", padx=5, pady=(7, 5))
        folder_grid = tk.Frame(top_inner, bg="#0d151f")
        folder_grid.pack(fill="x", padx=8, pady=(8, 6))
        folder_grid.grid_columnconfigure(1, weight=1)
        folder_grid.grid_columnconfigure(4, weight=1)
        tk.Label(folder_grid, text="Input", bg="#0d151f", fg="#9fb0be", font=("Segoe UI", 8, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 5))
        tk.Entry(folder_grid, textvariable=self.vars["dump_folder"], bg="#0b121b", relief="flat", bd=0).grid(row=0, column=1, sticky="ew", ipady=5)
        ttk.Button(folder_grid, text="…", width=3, style="Compact.TButton", command=lambda: self.browse("dump_folder", "folder")).grid(row=0, column=2, padx=(4, 10))
        tk.Label(folder_grid, text="Output", bg="#0d151f", fg="#9fb0be", font=("Segoe UI", 8, "bold")).grid(row=0, column=3, sticky="w", padx=(0, 5))
        tk.Entry(folder_grid, textvariable=self.vars["load_folder"], bg="#0b121b", relief="flat", bd=0).grid(row=0, column=4, sticky="ew", ipady=5)
        ttk.Button(folder_grid, text="…", width=3, style="Compact.TButton", command=lambda: self.browse("load_folder", "folder")).grid(row=0, column=5, padx=(4, 0))

        actions = tk.Frame(top_inner, bg="#0d151f")
        actions.pack(fill="x", padx=8, pady=(2, 8))
        ttk.Button(actions, text="▶  Start Watching", style="Accent.TButton", command=self.start).pack(side="left")
        ttk.Button(actions, text="Open Batch Queue", style="Secondary.TButton", command=self.open_batch_queue_tab).pack(side="left", padx=6)
        ttk.Button(actions, text="■  Stop", style="Danger.TButton", command=self.stop_all_processing).pack(side="left")
        ttk.Button(actions, text="Force Scan", style="Compact.TButton", command=self.force_dump_check).pack(side="right")
        ttk.Button(actions, text="Save Profile", style="Compact.TButton", command=self.save_current_profile).pack(side="right", padx=6)

        self._build_priority_lane_strip(center, context="dashboard", padx=5, pady=(5, 4))

        progress_outer, progress_inner = self._dashboard_panel(center, "Progress")
        progress_outer.pack(fill="x", padx=5, pady=5)
        progress_row = tk.Frame(progress_inner, bg="#0d151f")
        progress_row.pack(fill="x", padx=12, pady=(0, 3))
        self.dashboard_progress_text = tk.StringVar(value="Waiting — 0 / 0 textures")
        self.dashboard_progress_pct_text = tk.StringVar(value="0%")
        tk.Label(progress_row, textvariable=self.dashboard_progress_text, bg="#0d151f", fg="#d6e2e9", font=("Segoe UI", 9), anchor="w").pack(side="left", fill="x", expand=True)
        tk.Label(progress_row, textvariable=self.dashboard_progress_pct_text, bg="#0d151f", fg="#43d9ca", font=("Segoe UI", 18, "bold")).pack(side="right")
        self.dashboard_progress_var = tk.DoubleVar(value=0.0)
        ttk.Progressbar(progress_inner, variable=self.dashboard_progress_var, maximum=100, style="Horizontal.TProgressbar").pack(fill="x", padx=12, pady=(0, 5))
        time_row = tk.Frame(progress_inner, bg="#0d151f")
        time_row.pack(fill="x", padx=12, pady=(0, 9))
        self.dashboard_current_job_var = tk.StringVar(value="Current job: —")
        self.dashboard_elapsed_var = tk.StringVar(value="Elapsed 00:00:00")
        self.dashboard_eta_var = tk.StringVar(value="ETA —")
        tk.Label(time_row, textvariable=self.dashboard_current_job_var, bg="#0d151f", fg="#64d9cf", font=("Segoe UI", 8, "bold"), anchor="w").pack(side="left", fill="x", expand=True)
        tk.Label(time_row, textvariable=self.dashboard_elapsed_var, bg="#0d151f", fg="#9fb0be", font=("Consolas", 8)).pack(side="right", padx=(10, 0))
        tk.Label(time_row, textvariable=self.dashboard_eta_var, bg="#0d151f", fg="#9fb0be", font=("Consolas", 8)).pack(side="right", padx=(10, 0))

        preview_outer, preview_inner = self._dashboard_panel(center, "Current texture")
        preview_outer.pack(fill="both", expand=True, padx=5, pady=(5, 7))
        preview_head = tk.Frame(preview_inner, bg="#0d151f")
        preview_head.pack(fill="x", padx=10, pady=(0, 5))
        self.preview_status_var = tk.StringVar(value="Waiting for a texture…")
        tk.Label(preview_head, textvariable=self.preview_status_var, bg="#0d151f", fg="#91a5b6", font=("Segoe UI", 8), anchor="w").pack(side="left", fill="x", expand=True)
        tk.Checkbutton(
            preview_head, text="Preview", variable=self.live_preview_var, command=self.on_live_preview_toggled,
            bg="#0d151f", fg="#aab9c5", selectcolor="#0f766e", activebackground="#0d151f"
        ).pack(side="right")

        preview_body = tk.Frame(preview_inner, bg="#0d151f")
        preview_body.pack(fill="both", expand=True, padx=10, pady=(0, 5))
        preview_body.grid_columnconfigure(0, weight=1, uniform="preview")
        preview_body.grid_columnconfigure(1, weight=1, uniform="preview")
        preview_body.grid_rowconfigure(1, weight=1)
        tk.Label(preview_body, text="Before", bg="#0d151f", fg="#f0f5f8", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, pady=(0, 4))
        tk.Label(preview_body, text="After", bg="#0d151f", fg="#f0f5f8", font=("Segoe UI", 10, "bold")).grid(row=0, column=1, pady=(0, 4))
        self.preview_original_canvas = tk.Canvas(preview_body, bg="#05080c", highlightthickness=1, highlightbackground="#273747")
        self.preview_original_canvas.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        self.preview_enhanced_canvas = tk.Canvas(preview_body, bg="#05080c", highlightthickness=1, highlightbackground="#273747")
        self.preview_enhanced_canvas.grid(row=1, column=1, sticky="nsew", padx=(5, 0))
        self._set_preview_canvas(self.preview_original_canvas, placeholder="No preview")
        self._set_preview_canvas(self.preview_enhanced_canvas, placeholder="Waiting for output")
        self.preview_original_canvas.bind("<Configure>", self._schedule_preview_resize)
        self.preview_enhanced_canvas.bind("<Configure>", self._schedule_preview_resize)

        meta = tk.Frame(preview_inner, bg="#0b121b")
        meta.pack(fill="x", padx=10, pady=(2, 10))
        self.preview_resolution_var = tk.StringVar(value="Resolution  —")
        self.preview_mode_var = tk.StringVar(value="Mode  Clean Heart")
        self.preview_texture_elapsed_var = tk.StringVar(value="Texture  00:00:00")
        self.preview_file_var = tk.StringVar(value="File  —")
        for i, (var, color) in enumerate([
            (self.preview_resolution_var, "#b9c7d1"),
            (self.preview_mode_var, "#4ed8cb"),
            (self.preview_texture_elapsed_var, "#b9c7d1"),
            (self.preview_file_var, "#b9c7d1"),
        ]):
            label = tk.Label(meta, textvariable=var, bg="#0b121b", fg=color, font=("Segoe UI", 8), padx=10, pady=7, anchor="w")
            label.pack(side="left", fill="x", expand=(i == 3))

        # RIGHT: live logs, always visible beside the previews.
        log_outer, log_inner = self._dashboard_panel(right, "Live logs")
        log_outer.pack(fill="both", expand=True, padx=7, pady=7)
        log_controls = tk.Frame(log_inner, bg="#0d151f")
        log_controls.pack(fill="x", padx=9, pady=(0, 5))
        self.log_autoscroll_var = tk.BooleanVar(value=True)
        tk.Checkbutton(log_controls, text="Auto-scroll", variable=self.log_autoscroll_var,
                       bg="#0d151f", fg="#9fb0be", selectcolor="#0f766e", activebackground="#0d151f").pack(side="left")
        ttk.Button(log_controls, text="Clear", style="Compact.TButton", command=self.clear_live_log_view).pack(side="right")
        ttk.Button(log_controls, text="Copy", style="Compact.TButton", command=self.copy_live_logs).pack(side="right", padx=4)

        log_frame = tk.Frame(log_inner, bg="#070c12")
        log_frame.pack(fill="both", expand=True, padx=9, pady=(0, 9))
        self.log_text = tk.Text(
            log_frame, bg="#070c12", fg="#c9d4dc", insertbackground="#ffffff",
            wrap="word", relief="flat", bd=0, padx=8, pady=8,
            font=("Consolas", 8), spacing1=1, spacing3=2
        )
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_text.tag_configure("time", foreground="#62778b")
        self.log_text.tag_configure("info", foreground="#9cc8f5")
        self.log_text.tag_configure("success", foreground="#57d78a")
        self.log_text.tag_configure("warning", foreground="#f0b94e")
        self.log_text.tag_configure("error", foreground="#ff7c86")
        self.log_text.tag_configure("process", foreground="#c99cf6")
        self.log_text.tag_configure("normal", foreground="#c9d4dc")

        # Bottom: whole batch queue progress and a compact health footer.
        bottom = tk.Frame(page, bg="#070b11")
        bottom.grid(row=1, column=0, sticky="ew", padx=3, pady=(4, 0))
        batch_outer, batch_inner = self._dashboard_panel(bottom, bg="#0d151f", border="#223141", padx=8, pady=6)
        batch_outer.pack(fill="x")
        batch_header = tk.Frame(batch_inner, bg="#0d151f")
        batch_header.pack(fill="x", padx=10, pady=(6, 2))
        tk.Label(batch_header, text="BATCH QUEUE PROGRESS OVERVIEW", bg="#0d151f", fg="#dce8ef", font=("Segoe UI", 8, "bold")).pack(side="left")
        self.dashboard_batch_total_var = tk.StringVar(value="Total: 0")
        tk.Label(batch_header, textvariable=self.dashboard_batch_total_var, bg="#0d151f", fg="#cbd6df", font=("Segoe UI", 8, "bold")).pack(side="right")
        self.dashboard_batch_canvas = tk.Canvas(batch_inner, height=27, bg="#111a25", highlightthickness=0)
        self.dashboard_batch_canvas.pack(fill="x", padx=10, pady=(2, 8))
        self.dashboard_batch_canvas.bind("<Configure>", lambda _e: self._redraw_dashboard_batch_bar())
        self._dashboard_batch_values = (0, 0, 0)

        footer = tk.Frame(page, bg="#0a1119", height=34)
        footer.grid(row=2, column=0, sticky="ew", padx=3, pady=(3, 3))
        footer.grid_propagate(False)
        self.dashboard_comfy_footer_var = tk.StringVar(value="Backend: Not checked")
        self.dashboard_mode_footer_var = tk.StringVar(value="Mode: Clean Heart")
        self.dashboard_cleanup_footer_var = tk.StringVar(value="Auto cleanup: Off")
        self.dashboard_vram_footer_var = tk.StringVar(value="VRAM: —")
        for var, color in [
            (self.dashboard_comfy_footer_var, "#71d7ca"),
            (self.dashboard_mode_footer_var, "#9cc8f5"),
            (self.dashboard_cleanup_footer_var, "#57d78a"),
            (self.dashboard_vram_footer_var, "#9cc8f5"),
        ]:
            tk.Label(footer, textvariable=var, bg="#0a1119", fg=color, font=("Segoe UI", 8, "bold"), padx=16).pack(side="left", fill="y")

    def _on_main_tab_changed(self, _event=None):
        """Keep tab changes lightweight; perform expensive manager work asynchronously."""
        if self._preview_resize_after is not None:
            try:
                self.after_cancel(self._preview_resize_after)
            except Exception:
                pass
            self._preview_resize_after = None
        try:
            selected = self.tabs.select()
            if selected == str(self.page_dashboard):
                self.after_idle(self._redraw_preview_canvases)
            elif selected == str(self.page_manager):
                self.after_idle(self._manager_ensure_context)
            elif selected == str(self.page_batch):
                self.after_idle(lambda: self._update_batch_progress())
        except Exception:
            pass

    def _on_settings_tab_changed(self, _event=None):
        # Let Tk settle layout once; do not rebuild or rescan anything.
        try:
            selected = self.settings_tabs.nametowidget(self.settings_tabs.select())
            canvas = getattr(selected, "_scroll_canvas", None)
            if canvas is not None:
                canvas.after_idle(lambda: canvas.configure(scrollregion=canvas.bbox("all") or (0, 0, 0, 0)))
        except Exception:
            pass

    def stop_all_processing(self):
        if getattr(self, "batch_active", False):
            self.stop_batch_queue()
        else:
            self.stop()

    def _schedule_preview_resize(self, _event=None):
        # Canvas Configure fires repeatedly while changing tabs. Re-opening and
        # Lanczos-resizing large texture files on every event froze the UI.
        if self._preview_resize_after is not None:
            try:
                self.after_cancel(self._preview_resize_after)
            except Exception:
                pass
        self._preview_resize_after = self.after(100, self._redraw_preview_canvases)

    def _redraw_preview_canvases(self):
        self._preview_resize_after = None
        try:
            if self.tabs.select() != str(self.page_dashboard):
                return
        except Exception:
            pass
        if hasattr(self, "preview_original_canvas"):
            if self._preview_original_photo is not None:
                self._set_preview_canvas(self.preview_original_canvas, photo=self._preview_original_photo)
            else:
                self._set_preview_canvas(self.preview_original_canvas, placeholder="No preview")
        if hasattr(self, "preview_enhanced_canvas"):
            if self._preview_enhanced_photo is not None:
                self._set_preview_canvas(self.preview_enhanced_canvas, photo=self._preview_enhanced_photo)
            else:
                placeholder = "Processing…" if self.stats.get("current_input_path") else "Waiting for output"
                self._set_preview_canvas(self.preview_enhanced_canvas, placeholder=placeholder)

    @staticmethod
    def _format_duration(seconds):
        try:
            seconds = max(0, int(seconds))
        except Exception:
            seconds = 0
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _current_profile_progress_cached(self, max_age=2.5):
        progress = self._dashboard_metrics_cache.get("progress", {})
        return tuple(progress.get(self.current_profile_name, (0, 0)))

    def _update_dashboard_batch_values(self):
        if not hasattr(self, "dashboard_batch_canvas"):
            return
        progress = self._dashboard_metrics_cache.get("progress", {})
        done = total = 0
        for name in self.batch_queue:
            d, t = progress.get(name, (0, 0))
            done += d
            total += t
        running = 1 if self.batch_active and total > done else 0
        values = (done, total, running)
        if values != getattr(self, "_dashboard_batch_values", None):
            self._dashboard_batch_values = values
            self.dashboard_batch_total_var.set(f"Total: {total}")
            self._redraw_dashboard_batch_bar()
        try:
            self._update_batch_progress()
        except Exception:
            pass

    def _redraw_dashboard_batch_bar(self):
        canvas = getattr(self, "dashboard_batch_canvas", None)
        if canvas is None:
            return
        canvas.delete("all")
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        done, total, running = getattr(self, "_dashboard_batch_values", (0, 0, 0))
        if total <= 0:
            canvas.create_rectangle(0, 0, width, height, fill="#111a25", outline="")
            canvas.create_text(width // 2, height // 2, text="Batch queue is empty", fill="#718397", font=("Segoe UI", 8, "bold"))
            return
        queued = max(0, total - done - running)
        parts = [
            (done, "#2f7bd8", f"Done: {done} ({100*done/total:.0f}%)"),
            (running, "#2aa96b", f"Running: {running}"),
            (queued, "#d99a24", f"Queued: {queued} ({100*queued/total:.0f}%)"),
        ]
        x = 0
        for index, (count, color, label) in enumerate(parts):
            if count <= 0:
                continue
            segment = width * count / total
            x2 = width if index == len(parts) - 1 else x + segment
            canvas.create_rectangle(x, 0, x2, height, fill=color, outline="")
            if segment > 92:
                canvas.create_text((x + x2) / 2, height / 2, text=label, fill="#ffffff" if index < 2 else "#251b06", font=("Segoe UI", 8, "bold"))
            x = x2
        if x < width:
            canvas.create_rectangle(x, 0, width, height, fill="#111a25", outline="")




    def lookup_universal_database(self, game_id, emulator):
        return lookup_local_game_title(game_id, emulator)

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
                self.after(0, lambda: self.game_db_status_var.set(f"{imported:,}"))
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
            ("NMKE", "Nintendo 64 / RMG"),
            ("MK-51058", FLYCAST_EMULATOR),
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
            local = lookup_local_game_title(gid, emu)
            if local:
                if not local.get("region") and emu == "Dolphin":
                    local["region"] = infer_nintendo_region(gid)
                return local
        except Exception as e:
            self.log(f"Local game title lookup failed for {gid}: {e}")

        # Online GameTDB lookup is currently used for Dolphin/GameCube/Wii IDs.
        if allow_online and emu == "Dolphin" and re.fullmatch(r"[A-Z0-9]{6}", gid):
            try:
                url = f"https://www.gametdb.com/Wii/{gid}"
                request = urllib.request.Request(
                    url, headers={"User-Agent": f"Faithful-Remaster/{APP_VERSION}"}
                )
                with urllib.request.urlopen(request, timeout=10) as response:
                    response_text = response.read().decode("utf-8", errors="replace")
                parser = _TitleHTMLParser()
                parser.feed(response_text)
                page_title = "".join(parser.title_text)
                title = clean_gametdb_title(page_title, gid)

                # Reject obvious error/generic titles.
                bad = {"", "gametdb", "game database"}
                if title.lower() not in bad and gid.lower() not in title.lower():
                    region = infer_nintendo_region(gid)
                    titles = load_game_titles()
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
        return format_resolved_game_title(title, region)

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
            "new", "dump", "dumps", "texdump", "texture", "textures",
            "replacement", "replacements", "load", "output", "input"
        }

        candidate = p.name
        if candidate.lower() in ignored_leaf_names and p.parent:
            candidate = p.parent.name

        # Structured per-game technical leaves.
        if emu == "PPSSPP" and p.name.lower() == "new":
            candidate = p.parent.name
        if emu in ("PCSX2", "DuckStation") and p.name.lower() == "dumps":
            candidate = p.parent.name

        # RMG/N64 dump folders are usually <GAME>/GLideNHQ.
        if emu in ("Nintendo 64 / RMG", "Nintendo 64 / Project64") and p.name.lower() == "glidenhq":
            candidate = p.parent.name

        # Dolphin / PCSX2 / DuckStation usually use the game folder itself.
        # Keep the folder name exactly because it may be a game ID.
        candidate = candidate.strip()

        # Convert separators to spaces only when it improves readability.
        if emu in ("Nintendo 64 / RMG", "Nintendo 64 / Project64"):
            readable = humanize_n64_folder_name(candidate)
        else:
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
        if self.emulator_var.get() in ("Nintendo 64 / RMG", "Nintendo 64 / Project64"):
            p = Path(folder_path)
            raw_id = p.parent.name if p.name.lower() == "glidenhq" else p.name
            if game_id_var is not None and not game_id_var.get().strip():
                game_id_var.set(raw_id)
            return
        looks_like_id = (
            5 <= len(compact) <= 16
            and any(ch.isdigit() for ch in compact)
            and compact.replace("-", "").replace("_", "").isalnum()
        )
        if game_id_var is not None and looks_like_id and not game_id_var.get().strip():
            game_id_var.set(compact.upper())

        # Resolve the readable title from the ID, but preserve the exact folder
        # identifier (including serial hyphens) because it is part of the path.
        resolved = False
        exact_folder_id = game_id_var.get().strip() if game_id_var is not None else ""
        if looks_like_id:
            resolved = self.lookup_and_apply_game_title(manual=False)
            if exact_folder_id and game_id_var is not None:
                game_id_var.set(exact_folder_id)

        if not resolved and game_var is not None:
            current = game_var.get().strip()
            if not current or current in {"New Game", "Generic — New Game"}:
                game_var.set(detected)

        if hasattr(self, "saved_state_var"):
            self.saved_state_var.set("● Unsaved changes")


    def _apply_azahar_metadata_to_profiles(self, entries):
        changed = 0
        for profile_name, record in self.profile_data.get("profiles", {}).items():
            if str(record.get("emulator") or "") != "Azahar / Citra":
                continue
            game_id = normalize_azahar_title_id(record.get("game_id"))
            if not game_id:
                dump = str(record.get("settings", {}).get("dump_folder", "") or "")
                game_id = normalize_azahar_title_id(Path(dump).name) if dump else ""
                if game_id and not record.get("game_id"):
                    record["game_id"] = game_id
            metadata = entries.get(game_id, {}) if game_id else {}
            if not metadata.get("title"):
                continue
            old_name = str(record.get("game_name") or "").strip()
            old_source = str(record.get("title_source") or "").strip().casefold()
            replace_name = (
                not old_name or old_name.casefold() == game_id.casefold() or
                old_source in {"folder id fallback", "folder name", "azahar smdh metadata", "azahar metadata cache"}
            )
            local_changed = False
            if replace_name and old_name != metadata["title"]:
                record["game_name"] = metadata["title"]
                local_changed = True
            icon_path = str(metadata.get("icon_path") or "")
            if icon_path and Path(icon_path).is_file() and record.get("azahar_icon_path") != icon_path:
                record["azahar_icon_path"] = icon_path
                local_changed = True
            if record.get("title_source") != metadata.get("source", "Azahar SMDH metadata"):
                record["title_source"] = metadata.get("source", "Azahar SMDH metadata")
                local_changed = True
            publisher = str(metadata.get("publisher") or "")
            if publisher and record.get("publisher") != publisher:
                record["publisher"] = publisher
                local_changed = True
            if local_changed:
                changed += 1
        if changed:
            save_profiles_data(self.profile_data)
        return changed

    def refresh_azahar_metadata(self, manual=True):
        if getattr(self, "_azahar_metadata_scan_running", False):
            if manual:
                self.log("Azahar metadata scan is already running.")
            return
        profiles = self.profile_data.get("profiles", {})
        azahar_profiles = [record for record in profiles.values() if str(record.get("emulator") or "") == "Azahar / Citra"]
        current_is_azahar = hasattr(self, "emulator_var") and self.emulator_var.get() == "Azahar / Citra"
        if not azahar_profiles and not current_is_azahar:
            return
        title_ids, dump_folders = set(), []
        for record in azahar_profiles:
            gid = normalize_azahar_title_id(record.get("game_id"))
            dump = str(record.get("settings", {}).get("dump_folder", "") or "")
            if not gid and dump:
                gid = normalize_azahar_title_id(Path(dump).name)
            if gid: title_ids.add(gid)
            if dump: dump_folders.append(dump)
        if current_is_azahar and hasattr(self, "vars"):
            gid = normalize_azahar_title_id(self.vars.get("game_id").get() if self.vars.get("game_id") else "")
            dump = self.vars.get("dump_folder").get().strip() if self.vars.get("dump_folder") else ""
            if not gid and dump: gid = normalize_azahar_title_id(Path(dump).name)
            if gid: title_ids.add(gid)
            if dump: dump_folders.append(dump)
        if not title_ids:
            return
        self._azahar_metadata_scan_running = True
        if manual:
            self.log(f"Scanning Azahar library metadata for {len(title_ids)} Title ID(s)…")
            if hasattr(self, "scan_status_var"):
                self.scan_status_var.set("Reading Azahar game names and icons…")

        def progress(message):
            if manual and hasattr(self, "scan_status_var"):
                self.after(0, lambda m=message: self.scan_status_var.set(m))

        def worker():
            try:
                entries, stats = scan_azahar_game_metadata(title_ids, dump_folders, progress)
                def finish():
                    self._azahar_metadata_scan_running = False
                    changed = self._apply_azahar_metadata_to_profiles(entries)
                    current = self.profile_data.get("profiles", {}).get(self.current_profile_name, {})
                    if current_is_azahar and hasattr(self, "vars"):
                        current_gid = normalize_azahar_title_id(
                            self.vars.get("game_id").get() if self.vars.get("game_id") else ""
                        )
                        if not current_gid and self.vars.get("dump_folder"):
                            current_gid = normalize_azahar_title_id(Path(self.vars["dump_folder"].get()).name)
                        current_metadata = entries.get(current_gid, {}) if current_gid else {}
                        current_name = current_metadata.get("title") or current.get("game_name")
                        if self.vars.get("game_name") and current_name:
                            self._loading_profile = True
                            self.vars["game_name"].set(current_name)
                            self.vars["game_id"].set(current_gid or current.get("game_id", ""))
                            self._loading_profile = False
                    self.refresh_profile_combo()
                    self.refresh_batch_profiles()
                    self.refresh_batch_queue_list()
                    self._refresh_profile_summary()
                    self._update_profile_header_card()
                    if hasattr(self, "_refresh_artwork_views"):
                        try: self._refresh_artwork_views(force=False)
                        except Exception: pass
                    if hasattr(self, "scan_status_var"):
                        self.scan_status_var.set(f"Azahar metadata: {stats['resolved']} read, {stats['cache_hits']} cached")
                    if manual:
                        self.log(f"Azahar metadata complete: {stats['resolved']} read, {stats['cache_hits']} cached; {changed} profile(s) updated.")
                        messagebox.showinfo(
                            "Azahar metadata",
                            f"Updated {changed} profile(s).\n\n"
                            f"Files inspected: {stats['files']}\n"
                            f"Fresh metadata: {stats['resolved']}\n"
                            f"Cache hits: {stats['cache_hits']}\n\n"
                            "The real 16-digit texture folder names were not changed."
                        )
                self.after(0, finish)
            except Exception as exc:
                def fail():
                    self._azahar_metadata_scan_running = False
                    if hasattr(self, "scan_status_var"):
                        self.scan_status_var.set("Azahar metadata scan failed")
                    if manual:
                        messagebox.showerror("Azahar metadata", str(exc))
                    else:
                        self.log(f"Azahar metadata scan failed: {exc}")
                self.after(0, fail)
        threading.Thread(target=worker, daemon=True).start()

    # ---------- Profiles ----------
    def _profile_set_dirty(self, *_args):
        if getattr(self, "_loading_profile", False):
            return
        if hasattr(self, "saved_state_var"):
            self.saved_state_var.set("● Unsaved changes")
        self._refresh_profile_summary()
        self._update_profile_header_card()

    def _select_profile_mode(self, mode):
        mode = normalize_faithfulness_preset(mode)
        if mode not in BUILTIN_WORKFLOW_PROFILE_NAMES:
            return
        self.faithfulness_preset_var.set(mode)
        self.cfg["faithfulness_preset"] = mode
        self._profile_set_dirty()
        self._refresh_profile_mode_cards()
        if hasattr(self, "dashboard_mode_var"):
            self.dashboard_mode_var.set(mode)
            self._refresh_mode_cards()
        self._update_profile_header_card()

    def _build_profile_mode_card(self, parent, mode, badge, description, symbol):
        card = tk.Frame(
            parent, bg="#101923", highlightthickness=1,
            highlightbackground="#2a3948", cursor="hand2"
        )
        card.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        head = tk.Frame(card, bg="#101923")
        head.pack(fill="x", padx=12, pady=(12, 4))
        icon = tk.Label(
            head, text=symbol, bg="#101923", fg="#91a4b7",
            font=("Segoe UI Symbol", 20, "bold"), width=2
        )
        icon.pack(side="left", padx=(0, 7))
        title_col = tk.Frame(head, bg="#101923")
        title_col.pack(side="left", fill="x", expand=True)
        title = tk.Label(
            title_col, text=mode, bg="#101923", fg="#f0f5f8",
            font=("Segoe UI", 10, "bold"), anchor="w"
        )
        title.pack(fill="x")
        badge_label = tk.Label(
            title_col, text=badge, bg="#101923", fg="#7ddbd1",
            font=("Segoe UI", 8, "bold"), anchor="w"
        )
        badge_label.pack(fill="x", pady=(1, 0))
        check = tk.Label(
            head, text="", bg="#101923", fg="#dffefa",
            font=("Segoe UI Symbol", 12, "bold"), width=2
        )
        check.pack(side="right")
        detail = tk.Label(
            card, text=description, bg="#101923", fg="#9eacb9",
            font=("Segoe UI", 8), justify="left", anchor="w", wraplength=285
        )
        detail.pack(fill="x", padx=50, pady=(1, 12))
        self.profile_mode_cards[mode] = {
            "frame": card,
            "widgets": [card, head, icon, title_col, title, badge_label, check, detail],
            "icon": icon,
            "check": check,
            "badge": badge_label,
        }
        self._bind_recursive(card, "<Button-1>", lambda _e, m=mode: self._select_profile_mode(m))
        return card

    def _refresh_profile_mode_cards(self):
        selected = normalize_faithfulness_preset(
            self.faithfulness_preset_var.get() if hasattr(self, "faithfulness_preset_var") else "Clean Heart"
        )
        for mode, info in getattr(self, "profile_mode_cards", {}).items():
            active = mode == selected
            bg = "#123832" if active else "#101923"
            border = "#27c7b7" if active else "#2a3948"
            fg = "#eafffb" if active else "#91a4b7"
            try:
                info["frame"].configure(bg=bg, highlightbackground=border, highlightcolor=border)
                for widget in info["widgets"]:
                    widget.configure(bg=bg)
                info["icon"].configure(fg=fg)
                info["check"].configure(text="✓" if active else "")
                info["badge"].configure(fg="#7ff0df" if active else "#7ddbd1")
            except Exception:
                pass

    def _refresh_profile_summary(self):
        if not hasattr(self, "profile_summary_title_var"):
            return
        profiles = self.profile_data.get("profiles", {})
        record = profiles.get(self.current_profile_name, {}) if self.current_profile_name else {}
        game_name = ""
        game_id = ""
        emulator = "Generic"
        mode = "Clean Heart"
        try:
            game_name = self.vars.get("game_name").get().strip()
            game_id = self.vars.get("game_id").get().strip()
            emulator = self.emulator_var.get() or "Generic"
            mode = normalize_faithfulness_preset(self.faithfulness_preset_var.get())
        except Exception:
            game_name = str(record.get("game_name", "") or "").strip()
            game_id = str(record.get("game_id", "") or "").strip()
            emulator = str(record.get("emulator", "Generic") or "Generic")
            mode = normalize_faithfulness_preset(record.get("settings", {}).get("faithfulness_preset", "Clean Heart"))
        self.profile_summary_title_var.set(game_name or self.current_profile_name or "New game profile")
        self.profile_summary_platform_var.set(emulator)
        self.profile_summary_id_var.set(game_id or "No Game ID")
        self.profile_summary_mode_var.set(mode)
        try:
            dump = self.vars.get("dump_folder").get().strip()
            load = self.vars.get("load_folder").get().strip()
            ready = bool(dump and load)
        except Exception:
            ready = False
        self.profile_summary_ready_var.set("Ready to process" if ready else "Folders need configuration")
        if hasattr(self, "profile_summary_ready_label"):
            self.profile_summary_ready_label.configure(fg="#7ee8be" if ready else "#f4bd68")

    def build_profiles_tab(self):
        # Page title / purpose. Dashboard remains first for daily use; Profiles is second.
        hero = tk.Frame(self.tab_profiles, bg="#0b1119")
        hero.pack(fill="x", padx=14, pady=(14, 6))
        hero_left = tk.Frame(hero, bg="#0b1119")
        hero_left.pack(side="left", fill="x", expand=True)
        tk.Label(
            hero_left, text="Game Profiles", bg="#0b1119", fg="#f2f7fa",
            font=("Segoe UI", 18, "bold"), anchor="w"
        ).pack(fill="x")
        tk.Label(
            hero_left,
            text="Choose a game, configure its emulator folders, then select the default remaster mode.",
            bg="#0b1119", fg="#91a4b7", font=("Segoe UI", 9), anchor="w"
        ).pack(fill="x", pady=(2, 0))
        self.saved_state_var = tk.StringVar(value="● Saved")
        tk.Label(
            hero, textvariable=self.saved_state_var, bg="#10221f", fg="#86efac",
            padx=12, pady=7, font=("Segoe UI", 9, "bold")
        ).pack(side="right", padx=(12, 0), pady=3)

        workspace = tk.Frame(self.tab_profiles, bg="#0b1119")
        workspace.pack(fill="x", padx=12, pady=4)
        workspace.grid_columnconfigure(0, weight=0, minsize=330)
        workspace.grid_columnconfigure(1, weight=1)

        # Left: profile library + at-a-glance selected game card.
        library_outer, library = self._dashboard_panel(workspace, "Profile library", padx=12, pady=11)
        library_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=4)

        tk.Label(library, text="PLATFORM FILTER", bg="#0d151f", fg="#8194a7",
                 font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", padx=12, pady=(2, 3))
        active_record = self.profile_data.get("profiles", {}).get(self.current_profile_name, {})
        initial_emulator = active_record.get("emulator", "Dolphin")
        self.profile_emulator_filter_var = tk.StringVar(value=initial_emulator)
        self.profile_emulator_filter_combo = ttk.Combobox(
            library, textvariable=self.profile_emulator_filter_var,
            values=EMULATORS, state="readonly"
        )
        self.profile_emulator_filter_combo.pack(fill="x", padx=12, pady=(0, 9))
        self.profile_emulator_filter_combo.bind("<<ComboboxSelected>>", self.on_profile_filter_changed)

        tk.Label(library, text="GAME PROFILE", bg="#0d151f", fg="#8194a7",
                 font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", padx=12, pady=(2, 3))
        self.profile_var = tk.StringVar()
        self.profile_combo = ttk.Combobox(library, textvariable=self.profile_var, state="readonly")
        self.profile_combo.pack(fill="x", padx=12, pady=(0, 9))
        self.profile_combo.bind("<<ComboboxSelected>>", self.on_profile_selected)

        library_actions = tk.Frame(library, bg="#0d151f")
        library_actions.pack(fill="x", padx=9, pady=(0, 9))
        ttk.Button(library_actions, text="＋ New", style="Accent.TButton", command=self.create_profile).pack(side="left", padx=3)
        ttk.Button(library_actions, text="Duplicate", style="Compact.TButton", command=self.duplicate_profile).pack(side="left", padx=3)
        ttk.Button(library_actions, text="Rename", style="Compact.TButton", command=self.rename_profile).pack(side="left", padx=3)
        ttk.Button(library_actions, text="Delete", style="Danger.TButton", command=self.delete_profile).pack(side="right", padx=3)

        summary = tk.Frame(library, bg="#0a1119", highlightthickness=1, highlightbackground="#253545")
        summary.pack(fill="x", padx=12, pady=(3, 12))
        self.profile_summary_title_var = tk.StringVar(value="New game profile")
        self.profile_summary_platform_var = tk.StringVar(value="Generic")
        self.profile_summary_id_var = tk.StringVar(value="No Game ID")
        self.profile_summary_mode_var = tk.StringVar(value="Clean Heart")
        self.profile_summary_ready_var = tk.StringVar(value="Folders need configuration")
        self.profile_validation_var = tk.StringVar(value="Validation: not checked")
        tk.Label(summary, textvariable=self.profile_summary_title_var, bg="#0a1119", fg="#f1f6f8",
                 font=("Segoe UI", 12, "bold"), anchor="w", wraplength=285).pack(fill="x", padx=12, pady=(12, 3))
        meta = tk.Frame(summary, bg="#0a1119")
        meta.pack(fill="x", padx=12, pady=2)
        tk.Label(meta, textvariable=self.profile_summary_platform_var, bg="#162432", fg="#a7d7ff",
                 padx=7, pady=3, font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Label(meta, textvariable=self.profile_summary_id_var, bg="#161e29", fg="#becbd6",
                 padx=7, pady=3, font=("Segoe UI", 8)).pack(side="left", padx=5)
        tk.Label(summary, text="DEFAULT MODE", bg="#0a1119", fg="#788c9e",
                 font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", padx=12, pady=(8, 1))
        tk.Label(summary, textvariable=self.profile_summary_mode_var, bg="#0a1119", fg="#68e0d3",
                 font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x", padx=12)
        self.profile_summary_ready_label = tk.Label(
            summary, textvariable=self.profile_summary_ready_var, bg="#0a1119", fg="#f4bd68",
            font=("Segoe UI", 8, "bold"), anchor="w"
        )
        self.profile_summary_ready_label.pack(fill="x", padx=12, pady=(8, 5))
        self.profile_validation_label = tk.Label(
            summary, textvariable=self.profile_validation_var, bg="#0a1119", fg="#9cc8f5",
            font=("Segoe UI", 8, "bold"), anchor="w", wraplength=285
        )
        self.profile_validation_label.pack(fill="x", padx=12, pady=(0, 12))

        # Right: the settings that define a game profile.
        setup_outer, setup = self._dashboard_panel(workspace, "Game setup", padx=14, pady=11)
        setup_outer.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=4)

        form = tk.Frame(setup, bg="#0d151f")
        form.pack(fill="x", padx=10, pady=(1, 4))
        form.grid_columnconfigure(1, weight=1)

        def field_row(row_index, label, key, browse_kind=None):
            tk.Label(form, text=label, bg="#0d151f", fg="#b8c6d1",
                     font=("Segoe UI", 9, "bold"), anchor="w").grid(
                row=row_index, column=0, sticky="w", padx=(0, 12), pady=6
            )
            var = tk.StringVar(value=str(self.cfg.get(key, "")))
            self.vars[key] = var
            entry = tk.Entry(
                form, textvariable=var, bg="#0b121b", fg="#eef5f8",
                insertbackground="#ffffff", relief="flat", bd=0,
                highlightthickness=1, highlightbackground="#293847",
                highlightcolor="#2db9ab", font=("Segoe UI", 9)
            )
            entry.grid(row=row_index, column=1, sticky="ew", pady=6, ipady=7)
            if browse_kind:
                ttk.Button(
                    form, text="Browse", style="Compact.TButton",
                    command=lambda k=key, kind=browse_kind: self.browse(k, kind)
                ).grid(row=row_index, column=2, padx=(8, 0), pady=6)
            var.trace_add("write", self._profile_set_dirty)
            return entry

        tk.Label(form, text="Emulator", bg="#0d151f", fg="#b8c6d1",
                 font=("Segoe UI", 9, "bold"), anchor="w").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=6)
        self.emulator_var = tk.StringVar(value="Generic")
        self.emulator_combo = ttk.Combobox(form, textvariable=self.emulator_var, values=EMULATORS, state="readonly")
        self.emulator_combo.grid(row=0, column=1, columnspan=2, sticky="ew", pady=6)
        self.emulator_combo.bind("<<ComboboxSelected>>", self.on_profile_emulator_changed)

        field_row(1, "Game name", "game_name")
        field_row(2, "Game ID", "game_id")
        field_row(3, "Dump folder", "dump_folder", "folder")
        field_row(4, "Replacement / Load folder", "load_folder", "folder")
        self.profile_path_hint_var = tk.StringVar(value="")
        tk.Label(
            form, textvariable=self.profile_path_hint_var, bg="#0d151f", fg="#71b9d6",
            font=("Segoe UI", 8), anchor="w", justify="left", wraplength=760
        ).grid(row=5, column=1, columnspan=2, sticky="ew", pady=(0, 5))
        self._update_emulator_path_hint()

        mode_title = tk.Frame(setup, bg="#0d151f")
        mode_title.pack(fill="x", padx=10, pady=(10, 1))
        tk.Label(mode_title, text="DEFAULT REMASTER MODE", bg="#0d151f", fg="#b8c6d1",
                 font=("Segoe UI", 9, "bold"), anchor="w").pack(side="left")
        tk.Label(mode_title, text="Applied unless a texture has its own override", bg="#0d151f", fg="#7f92a3",
                 font=("Segoe UI", 8), anchor="e").pack(side="right")

        self.faithfulness_preset_var = tk.StringVar(
            value=normalize_faithfulness_preset(self.cfg.get("faithfulness_preset", "Clean Heart"))
        )
        # Kept for compatibility with workflow-profile refresh code; mode cards are the visible control.
        self.faithfulness_preset_combo = ttk.Combobox(
            setup, textvariable=self.faithfulness_preset_var,
            values=workflow_profile_names(), state="readonly"
        )
        self.profile_mode_cards = {}
        modes = tk.Frame(setup, bg="#0d151f")
        modes.pack(fill="x", padx=5, pady=(1, 8))
        self._build_profile_mode_card(
            modes, "Clean Heart", "RECOMMENDED / DEFAULT",
            "Cleans and stabilizes most textures while preserving their original structure.", "♡"
        )
        self._build_profile_mode_card(
            modes, "Strong Believer", "STRONGER DETAIL PASS",
            "Use for true micro-detail, rough organic surfaces, faces, ornaments and isolated metal.", "◇"
        )
        self._refresh_profile_mode_cards()

        primary_actions = tk.Frame(setup, bg="#0d151f")
        primary_actions.pack(fill="x", padx=10, pady=(3, 8))
        ttk.Button(primary_actions, text="Save Profile", style="Accent.TButton", command=self.save_current_profile).pack(side="left")
        ttk.Button(primary_actions, text="Auto-fill Folders", style="Secondary.TButton", command=self.auto_fill_profile_folders).pack(side="left", padx=5)
        ttk.Button(primary_actions, text="Detect Game Name", style="Secondary.TButton",
                   command=lambda: self.apply_detected_game_name(self.vars.get("dump_folder").get().strip())).pack(side="left", padx=5)
        ttk.Button(primary_actions, text="Lookup Full Title", style="Secondary.TButton",
                   command=lambda: self.lookup_and_apply_game_title(manual=True)).pack(side="left", padx=5)
        self.refresh_azahar_metadata_button = ttk.Button(
            primary_actions, text="Refresh Azahar Metadata", style="Compact.TButton",
            command=lambda: self.refresh_azahar_metadata(manual=True)
        )
        self.refresh_azahar_metadata_button.pack(side="left", padx=5)
        ttk.Button(primary_actions, text="Validate Profile", style="Compact.TButton",
                   command=self.validate_current_profile).pack(side="right", padx=5)

        folder_actions = tk.Frame(setup, bg="#0d151f")
        folder_actions.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(folder_actions, text="Open Dump Folder", style="Compact.TButton",
                   command=lambda: self.open_cfg_folder("dump_folder")).pack(side="left")
        ttk.Button(folder_actions, text="Open Replacement Folder", style="Compact.TButton",
                   command=lambda: self.open_cfg_folder("load_folder")).pack(side="left", padx=5)
        self.auto_sync_azahar_pack_var = tk.BooleanVar(value=bool(self.cfg.get("auto_sync_azahar_pack_json", True)))
        self.auto_sync_azahar_check = tk.Checkbutton(
            folder_actions, text="Auto-sync Azahar pack.json", variable=self.auto_sync_azahar_pack_var,
            bg="#0d151f", activebackground="#0d151f", selectcolor="#0f766e"
        )
        self.auto_sync_azahar_check.pack(side="right")
        self._update_emulator_specific_controls()

        # Lower utilities: discovery is the primary task, database is a compact companion card.
        utilities = tk.Frame(self.tab_profiles, bg="#0b1119")
        utilities.pack(fill="x", padx=12, pady=(4, 14))
        utilities.grid_columnconfigure(0, weight=2)
        utilities.grid_columnconfigure(1, weight=1)

        scan_outer, scan = self._dashboard_panel(utilities, "Discover games", padx=12, pady=11)
        scan_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=4)
        tk.Label(scan, text="Select one dump location; the emulator, games and replacement folders are detected automatically.",
                 bg="#0d151f", fg="#8fa2b5", font=("Segoe UI", 8), anchor="w").pack(fill="x", padx=12, pady=(0, 7))
        scan_form = tk.Frame(scan, bg="#0d151f")
        scan_form.pack(fill="x", padx=12, pady=2)
        scan_form.grid_columnconfigure(1, weight=1)
        self.scan_emulator_var = tk.StringVar(value=self.profile_emulator_filter_var.get() or "Dolphin")
        self.scan_input_var = tk.StringVar()
        self.scan_detected_var = tk.StringVar(value="Detected emulator: waiting for a folder")
        tk.Label(scan_form, text="Dump folder", bg="#0d151f", fg="#b8c6d1", width=12, anchor="w").grid(row=0, column=0, sticky="w", pady=4)
        self.scan_input_entry = tk.Entry(
            scan_form, textvariable=self.scan_input_var, bg="#0b121b", fg="#eef5f8",
            insertbackground="#fff", relief="flat", highlightthickness=1,
            highlightbackground="#293847"
        )
        self.scan_input_entry.grid(row=0, column=1, sticky="ew", pady=4, ipady=5)
        ttk.Button(
            scan_form, text="Browse", style="Compact.TButton",
            command=lambda: self.browse_scan_root(self.scan_input_var)
        ).grid(row=0, column=2, padx=(7, 0), pady=4)
        tk.Label(
            scan_form, textvariable=self.scan_detected_var, bg="#0d151f", fg="#71b9d6",
            font=("Segoe UI", 8, "bold"), anchor="w"
        ).grid(row=1, column=1, columnspan=2, sticky="w", pady=(1, 3))
        tk.Label(
            scan_form,
            text="Select the emulator's texture dump root or one game's dump folder. Replacement paths are derived automatically.",
            bg="#0d151f", fg="#7f92a3", font=("Segoe UI", 8), anchor="w", justify="left",
            wraplength=650
        ).grid(row=2, column=1, columnspan=2, sticky="ew", pady=(0, 3))
        scan_opts = tk.Frame(scan, bg="#0d151f")
        scan_opts.pack(fill="x", padx=12, pady=(6, 10))
        self.auto_discover_var = tk.BooleanVar(value=bool(self.profile_data.get("auto_discover_games", True)))
        self.auto_add_var = tk.BooleanVar(value=bool(self.profile_data.get("auto_add_discovered_games", False)))
        tk.Checkbutton(scan_opts, text="Auto-discover games", variable=self.auto_discover_var,
                       bg="#0d151f", activebackground="#0d151f", selectcolor="#0f766e").pack(side="left")
        tk.Checkbutton(scan_opts, text="Automatically add discovered games", variable=self.auto_add_var,
                       bg="#0d151f", activebackground="#0d151f", selectcolor="#0f766e").pack(side="left", padx=15)
        self.scan_games_button = ttk.Button(
            scan_opts, text="Scan for Games", style="Accent.TButton", command=self.scan_for_games
        )
        self.scan_games_button.pack(side="right")
        self.scan_status_var = tk.StringVar(value="")
        tk.Label(
            scan, textvariable=self.scan_status_var, bg="#0d151f", fg="#71e4d7",
            font=("Segoe UI", 8, "bold"), anchor="w"
        ).pack(fill="x", padx=12, pady=(0, 8))
        self.scan_input_var.trace_add("write", lambda *_: self.update_scan_emulator_detection())
        self.load_scan_roots()

        db_outer, dbbox = self._dashboard_panel(utilities, "Game title database", padx=12, pady=11)
        db_outer.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=4)
        count = self.get_game_database_count()
        self.game_db_status_var = tk.StringVar(value=f"{count:,}" if count else "Not installed")
        tk.Label(dbbox, textvariable=self.game_db_status_var, bg="#0d151f", fg="#71e4d7",
                 font=("Segoe UI", 22, "bold"), anchor="w").pack(fill="x", padx=12, pady=(2, 0))
        tk.Label(dbbox, text="local game ID entries", bg="#0d151f", fg="#8fa2b5",
                 font=("Segoe UI", 8), anchor="w").pack(fill="x", padx=12, pady=(0, 10))
        ttk.Button(dbbox, text="Update Database", style="Secondary.TButton",
                   command=self.update_universal_game_database).pack(fill="x", padx=12, pady=3)
        ttk.Button(dbbox, text="Lookup Current Game", style="Compact.TButton",
                   command=lambda: self.lookup_and_apply_game_title(manual=True)).pack(fill="x", padx=12, pady=3)
        ttk.Button(dbbox, text="Test Database", style="Compact.TButton",
                   command=self.test_game_database_lookup).pack(fill="x", padx=12, pady=(3, 10))
        tk.Label(dbbox, text="Metadata only — no ROMs or game files.", bg="#0d151f", fg="#718496",
                 font=("Segoe UI", 8), wraplength=245, justify="left", anchor="w").pack(fill="x", padx=12, pady=(0, 11))

        self._refresh_profile_summary()

    def build_batch_queue_section(self):
        page = self.tab_batch
        page.configure(bg="#0b1119")

        hero = tk.Frame(page, bg="#0b1119")
        hero.pack(fill="x", padx=14, pady=(14, 6))
        hero_left = tk.Frame(hero, bg="#0b1119")
        hero_left.pack(side="left", fill="x", expand=True)
        tk.Label(hero_left, text="Batch Queue", bg="#0b1119", fg="#f2f7fa",
                 font=("Segoe UI", 18, "bold"), anchor="w").pack(fill="x")
        tk.Label(
            hero_left,
            text="Arrange multiple game profiles for unattended processing, then track the current game and the whole queue.",
            bg="#0b1119", fg="#91a4b7", font=("Segoe UI", 9), anchor="w"
        ).pack(fill="x", pady=(2, 0))
        self.batch_status_var = tk.StringVar(value="Batch queue idle")
        self.batch_status_badge = tk.Label(
            hero, textvariable=self.batch_status_var, bg="#152431", fg="#9cc8f5",
            padx=12, pady=7, font=("Segoe UI", 9, "bold")
        )
        self.batch_status_badge.pack(side="right", padx=(12, 0), pady=3)

        # At-a-glance cards.
        cards = tk.Frame(page, bg="#0b1119")
        cards.pack(fill="x", padx=10, pady=4)
        for col in range(4):
            cards.grid_columnconfigure(col, weight=1, uniform="batch_cards")
        self.batch_summary_profiles_var = tk.StringVar(value="0")
        self.batch_summary_textures_var = tk.StringVar(value="0")
        self.batch_summary_done_var = tk.StringVar(value="0%")
        self.batch_summary_state_var = tk.StringVar(value="IDLE")
        for col, (title, var, color, icon) in enumerate([
            ("QUEUED PROFILES", self.batch_summary_profiles_var, "#69a9ff", "≡"),
            ("TOTAL TEXTURES", self.batch_summary_textures_var, "#c99cf6", "▦"),
            ("COMPLETED", self.batch_summary_done_var, "#48d7c7", "✓"),
            ("QUEUE STATE", self.batch_summary_state_var, "#f0b94e", "▶"),
        ]):
            outer, inner = self._dashboard_panel(cards, bg="#0d151f", border="#223141", padx=10, pady=8)
            outer.grid(row=0, column=col, sticky="nsew", padx=4, pady=2)
            row = tk.Frame(inner, bg="#0d151f")
            row.pack(fill="x", padx=10, pady=9)
            tk.Label(row, text=icon, bg="#0d151f", fg=color,
                     font=("Segoe UI Symbol", 18, "bold"), width=2).pack(side="left", padx=(0, 8))
            colbox = tk.Frame(row, bg="#0d151f")
            colbox.pack(side="left", fill="x", expand=True)
            tk.Label(colbox, text=title, bg="#0d151f", fg="#8295a8",
                     font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x")
            tk.Label(colbox, textvariable=var, bg="#0d151f", fg=color,
                     font=("Segoe UI", 16, "bold"), anchor="w").pack(fill="x", pady=(2, 0))

        self._build_priority_lane_strip(page, context="batch", padx=14, pady=(4, 4))

        workspace = tk.Frame(page, bg="#0b1119")
        workspace.pack(fill="both", expand=True, padx=10, pady=5)
        workspace.grid_columnconfigure(0, weight=1, uniform="batch_lists")
        workspace.grid_columnconfigure(1, weight=0)
        workspace.grid_columnconfigure(2, weight=1, uniform="batch_lists")
        workspace.grid_rowconfigure(0, weight=1)

        available_outer, available = self._dashboard_panel(workspace, "Available profiles", padx=10, pady=9)
        available_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        filters = tk.Frame(available, bg="#0d151f")
        filters.pack(fill="x", padx=10, pady=(0, 7))
        self.batch_search_var = tk.StringVar()
        self.batch_platform_filter_var = tk.StringVar(value="All platforms")
        tk.Entry(filters, textvariable=self.batch_search_var, bg="#091019", fg="#e6edf3",
                 insertbackground="#ffffff", relief="flat", bd=0).pack(side="left", fill="x", expand=True, ipady=6)
        ttk.Combobox(
            filters, textvariable=self.batch_platform_filter_var,
            values=("All platforms",) + tuple(EMULATORS), state="readonly", width=34
        ).pack(side="left", padx=(7, 0))
        available_list_frame = tk.Frame(available, bg="#070c12")
        available_list_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.batch_available_list = tk.Listbox(
            available_list_frame, selectmode="extended", exportselection=False,
            bg="#070c12", fg="#d7e0e8", selectbackground="#0f766e",
            selectforeground="#ffffff", relief="flat", bd=0,
            activestyle="none", font=("Segoe UI", 9)
        )
        av_scroll = ttk.Scrollbar(available_list_frame, orient="vertical", command=self.batch_available_list.yview)
        self.batch_available_list.configure(yscrollcommand=av_scroll.set)
        av_scroll.pack(side="right", fill="y")
        self.batch_available_list.pack(side="left", fill="both", expand=True)
        self.batch_available_list.bind("<Double-Button-1>", lambda _e: self.batch_add_selected())

        rail = tk.Frame(workspace, bg="#0b1119", width=132)
        rail.grid(row=0, column=1, sticky="ns", padx=5)
        rail.grid_propagate(False)
        tk.Label(rail, text="QUEUE ACTIONS", bg="#0b1119", fg="#8295a8",
                 font=("Segoe UI", 8, "bold")).pack(pady=(38, 8))
        ttk.Button(rail, text="Add  →", style="Accent.TButton", command=self.batch_add_selected).pack(fill="x", pady=4)
        ttk.Button(rail, text="←  Remove", style="Secondary.TButton", command=self.batch_remove_selected).pack(fill="x", pady=4)
        ttk.Separator(rail, orient="horizontal").pack(fill="x", pady=10)
        ttk.Button(rail, text="↑  Move Up", style="Compact.TButton", command=lambda: self.batch_move_selected(-1)).pack(fill="x", pady=3)
        ttk.Button(rail, text="↓  Move Down", style="Compact.TButton", command=lambda: self.batch_move_selected(1)).pack(fill="x", pady=3)
        ttk.Button(rail, text="Clear Queue", style="Danger.TButton", command=self.batch_clear_queue).pack(fill="x", pady=(12, 3))

        queued_outer, queued = self._dashboard_panel(workspace, "Queued profiles", padx=10, pady=9)
        queued_outer.grid(row=0, column=2, sticky="nsew", padx=(5, 0))
        self.batch_queue_hint_var = tk.StringVar(value="Drag order with Move Up / Move Down")
        tk.Label(queued, textvariable=self.batch_queue_hint_var, bg="#0d151f", fg="#8397a8",
                 font=("Segoe UI", 8), anchor="w").pack(fill="x", padx=10, pady=(0, 7))
        queue_list_frame = tk.Frame(queued, bg="#070c12")
        queue_list_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.batch_queue_list = tk.Listbox(
            queue_list_frame, selectmode="extended", exportselection=False,
            bg="#070c12", fg="#d7e0e8", selectbackground="#245a75",
            selectforeground="#ffffff", relief="flat", bd=0,
            activestyle="none", font=("Segoe UI", 9)
        )
        q_scroll = ttk.Scrollbar(queue_list_frame, orient="vertical", command=self.batch_queue_list.yview)
        self.batch_queue_list.configure(yscrollcommand=q_scroll.set)
        q_scroll.pack(side="right", fill="y")
        self.batch_queue_list.pack(side="left", fill="both", expand=True)
        self.batch_queue_list.bind("<Double-Button-1>", lambda _e: self.batch_remove_selected())

        progress_outer, progress = self._dashboard_panel(page, "Queue progress", padx=12, pady=9)
        progress_outer.pack(fill="x", padx=14, pady=(4, 6))
        current_head = tk.Frame(progress, bg="#0d151f")
        current_head.pack(fill="x", padx=12, pady=(0, 3))
        self.batch_current_progress_text = tk.StringVar(value="Current game: 0 / 0 remastered")
        self.batch_current_pct_var = tk.StringVar(value="0.0%")
        tk.Label(current_head, textvariable=self.batch_current_progress_text, bg="#0d151f", fg="#cbd8e1",
                 font=("Segoe UI", 9, "bold"), anchor="w").pack(side="left", fill="x", expand=True)
        tk.Label(current_head, textvariable=self.batch_current_pct_var, bg="#0d151f", fg="#69a9ff",
                 font=("Segoe UI", 12, "bold")).pack(side="right")
        self.batch_current_progress_var = tk.DoubleVar(value=0.0)
        ttk.Progressbar(progress, variable=self.batch_current_progress_var, maximum=100,
                        style="Horizontal.TProgressbar").pack(fill="x", padx=12, pady=(0, 9))

        total_head = tk.Frame(progress, bg="#0d151f")
        total_head.pack(fill="x", padx=12, pady=(0, 3))
        self.batch_total_progress_text = tk.StringVar(value="Whole queue: 0 / 0 remastered")
        self.batch_total_pct_var = tk.StringVar(value="0.0%")
        tk.Label(total_head, textvariable=self.batch_total_progress_text, bg="#0d151f", fg="#cbd8e1",
                 font=("Segoe UI", 9, "bold"), anchor="w").pack(side="left", fill="x", expand=True)
        tk.Label(total_head, textvariable=self.batch_total_pct_var, bg="#0d151f", fg="#48d7c7",
                 font=("Segoe UI", 12, "bold")).pack(side="right")
        self.batch_total_progress_var = tk.DoubleVar(value=0.0)
        ttk.Progressbar(progress, variable=self.batch_total_progress_var, maximum=100,
                        style="Horizontal.TProgressbar").pack(fill="x", padx=12, pady=(0, 9))

        controls_outer, controls = self._dashboard_panel(page, bg="#0d151f", border="#223141", padx=10, pady=7)
        controls_outer.pack(fill="x", padx=14, pady=(0, 14))
        control_row = tk.Frame(controls, bg="#0d151f")
        control_row.pack(fill="x", padx=10, pady=9)
        self.batch_shutdown_var = tk.BooleanVar(value=bool(self.batch_state.get("shutdown_when_finished", False)))
        self.batch_shutdown_var.trace_add("write", lambda *_: self._save_batch_queue_state())
        tk.Checkbutton(
            control_row, text="Shutdown PC when queue finishes", variable=self.batch_shutdown_var,
            bg="#0d151f", fg="#c3d0d9", selectcolor="#0f766e", activebackground="#0d151f"
        ).pack(side="left")
        ttk.Button(control_row, text="Refresh Profiles", style="Compact.TButton",
                   command=self.refresh_batch_profiles).pack(side="left", padx=(12, 4))
        ttk.Button(control_row, text="Clear Queued Caches", style="Compact.TButton",
                   command=self.clear_all_queued_caches).pack(side="left", padx=4)
        self.batch_start_button = ttk.Button(control_row, text="▶  Start Batch Queue", style="Accent.TButton",
                                             command=self.start_batch_queue)
        self.batch_start_button.pack(side="right")
        self.batch_stop_button = ttk.Button(control_row, text="■  Stop Batch", style="Danger.TButton",
                                            command=self.stop_batch_queue)
        self.batch_stop_button.pack(side="right", padx=7)
        self.batch_skip_button = ttk.Button(
            control_row, text="⏭  Skip to next game", style="Secondary.TButton",
            command=self.skip_batch_current_game, state="disabled"
        )
        self.batch_skip_button.pack(side="right", padx=(0, 7))
        self.batch_previous_button = ttk.Button(
            control_row, text="⏮  Previous game", style="Secondary.TButton",
            command=self.previous_batch_game, state="disabled"
        )
        self.batch_previous_button.pack(side="right", padx=(0, 7))

        self.batch_search_var.trace_add("write", lambda *_: self.refresh_batch_profiles())
        self.batch_platform_filter_var.trace_add("write", lambda *_: self.refresh_batch_profiles())
        self.refresh_batch_profiles()
        self.refresh_batch_queue_list()
        self._update_batch_progress()

    def _set_batch_runtime_controls(self, running, skip_enabled=None, previous_enabled=None):
        """Keep Batch Queue buttons in a safe state for run/stop/navigation transitions."""
        try:
            nav_pending = bool(getattr(self, "_batch_skip_requested", False) or getattr(self, "_batch_previous_requested", False))
            has_current = 0 <= int(getattr(self, "batch_current_index", -1)) < len(getattr(self, "batch_queue", []))
            has_previous = has_current and int(getattr(self, "batch_current_index", -1)) > 0
            if hasattr(self, "batch_start_button"):
                self.batch_start_button.config(state="disabled" if running else "normal")
            if hasattr(self, "batch_skip_button"):
                # Skip is enabled only while a profile is actively running, and
                # disabled during a pending navigation request to prevent double requests.
                allow_skip = bool(running and has_current and not nav_pending) if skip_enabled is None else bool(skip_enabled)
                self.batch_skip_button.config(state="normal" if allow_skip else "disabled")
            if hasattr(self, "batch_previous_button"):
                # Previous is available from the second queued profile onward. It does not
                # delete outputs/cache; it simply stops safely and starts the previous profile.
                allow_previous = bool(running and has_previous and not nav_pending) if previous_enabled is None else bool(previous_enabled)
                self.batch_previous_button.config(state="normal" if allow_previous else "disabled")
        except Exception:
            pass

    def _set_profile_switching_enabled(self, enabled):
        state = "readonly" if enabled else "disabled"
        for widget_name in ("profile_combo", "profile_emulator_filter_combo", "emulator_combo"):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                try:
                    widget.configure(state=state)
                except Exception:
                    pass

    @staticmethod
    def _texture_progress_for_settings(settings):
        dump_text = str(settings.get("dump_folder", "") or "").strip()
        load_text = str(settings.get("load_folder", "") or "").strip()
        if not dump_text or not load_text:
            return 0, 0
        dump_folder = Path(dump_text)
        load_folder = Path(load_text)
        if not dump_folder.exists():
            return 0, 0

        total = 0
        done = 0
        process_tmp = bool(settings.get("process_tmp_image_files", True))
        for src in dump_folder.rglob("*"):
            if not src.is_file():
                continue
            if is_inside_alpha_output_folder(src, dump_folder):
                continue
            if not is_image_like(src, process_tmp):
                continue
            total += 1
            rel = src.relative_to(dump_folder)
            normal = load_folder / rel
            if normal.suffix.lower() == ".tmp":
                normal = normal.with_suffix(".png")
            if normal.exists():
                done += 1
        return done, total

    def _profile_texture_progress(self, profile_name):
        record = self.profile_data.get("profiles", {}).get(profile_name, {})
        settings = dict(DEFAULT_CONFIG)
        settings.update(record.get("settings", {}))
        return self._texture_progress_for_settings(settings)

    def _update_batch_progress(self):
        if not hasattr(self, "batch_current_progress_var"):
            return
        progress = self._dashboard_metrics_cache.get("progress", {})
        current_done = current_total = 0
        current_name = ""
        if self.batch_active and 0 <= self.batch_current_index < len(self.batch_queue):
            current_name = self.batch_queue[self.batch_current_index]
            current_done, current_total = progress.get(current_name, (0, 0))
        current_pct = (100.0 * current_done / current_total) if current_total else 0.0
        self.batch_current_progress_var.set(current_pct)
        current_label = self.profile_data.get("profiles", {}).get(current_name, {}).get("game_name") or current_name or "Waiting"
        self.batch_current_progress_text.set(f"Current game — {current_label}: {current_done:,} / {current_total:,} remastered")
        if hasattr(self, "batch_current_pct_var"):
            self.batch_current_pct_var.set(f"{current_pct:.1f}%")

        whole_done = whole_total = 0
        for name in self.batch_queue:
            done, total = progress.get(name, (0, 0))
            whole_done += done
            whole_total += total
        whole_pct = (100.0 * whole_done / whole_total) if whole_total else 0.0
        self.batch_total_progress_var.set(whole_pct)
        self.batch_total_progress_text.set(f"Whole queue: {whole_done:,} / {whole_total:,} remastered")
        if hasattr(self, "batch_total_pct_var"):
            self.batch_total_pct_var.set(f"{whole_pct:.1f}%")
        self._update_batch_visual_summary(whole_done, whole_total, whole_pct)

    def _update_batch_visual_summary(self, whole_done=None, whole_total=None, whole_pct=None):
        if not hasattr(self, "batch_summary_profiles_var"):
            return
        progress = self._dashboard_metrics_cache.get("progress", {})
        if whole_done is None or whole_total is None:
            whole_done = whole_total = 0
            for name in self.batch_queue:
                done, total = progress.get(name, (0, 0))
                whole_done += done
                whole_total += total
        if whole_pct is None:
            whole_pct = (100.0 * whole_done / whole_total) if whole_total else 0.0
        self.batch_summary_profiles_var.set(str(len(self.batch_queue)))
        self.batch_summary_textures_var.set(f"{whole_total:,}")
        self.batch_summary_done_var.set(f"{whole_pct:.0f}%")
        state = "RUNNING" if self.batch_active else ("READY" if self.batch_queue else "IDLE")
        self.batch_summary_state_var.set(state)
        if hasattr(self, "batch_status_badge"):
            colors = {
                "RUNNING": ("#123832", "#7ff0df"),
                "READY": ("#152b3d", "#9cc8f5"),
                "IDLE": ("#25202a", "#c9b3d8"),
            }
            bg, fg = colors.get(state, ("#152431", "#9cc8f5"))
            self.batch_status_badge.configure(bg=bg, fg=fg)

    def batch_clear_queue(self):
        if self.batch_active:
            messagebox.showinfo("Batch running", "Stop the Batch Queue before clearing its profile list.")
            return
        if not self.batch_queue:
            return
        if not messagebox.askyesno("Clear Batch Queue", "Remove every profile from the Batch Queue?\n\nNo textures or profiles will be deleted."):
            return
        self.batch_queue.clear()
        self.batch_current_index = -1
        self.refresh_batch_queue_list()
        self._save_batch_queue_state("idle")
        self._request_dashboard_metrics_refresh(force=True)

    def open_batch_queue_tab(self):
        try:
            self.tabs.select(self.page_batch)
            self.after_idle(self._update_batch_progress)
        except Exception:
            pass

    def _save_batch_queue_state(self, status=None):
        try:
            shutdown_value = bool(self.batch_shutdown_var.get()) if hasattr(self, "batch_shutdown_var") else bool(self.batch_state.get("shutdown_when_finished", False))
            save_batch_queue_state(
                self.batch_queue,
                shutdown_value,
                status or ("running" if self.batch_active else "idle"),
                self.batch_current_index
            )
        except Exception as exc:
            try:
                self.log(f"Could not save Batch Queue state: {exc}")
            except Exception:
                pass

    def refresh_batch_profiles(self):
        if not hasattr(self, "batch_available_list"):
            return
        self.batch_available_list.delete(0, "end")
        self.batch_available_names = []
        profiles = self.profile_data.get("profiles", {})
        query = self.batch_search_var.get().strip().casefold() if hasattr(self, "batch_search_var") else ""
        platform = self.batch_platform_filter_var.get() if hasattr(self, "batch_platform_filter_var") else "All platforms"
        for name in sorted(profiles, key=lambda n: ((profiles[n].get("emulator") or ""), (profiles[n].get("game_name") or n).casefold())):
            rec = profiles[name]
            emulator = rec.get("emulator", "Generic")
            game = rec.get("game_name") or name
            if platform and platform != "All platforms" and emulator != platform:
                continue
            haystack = f"{emulator} {game} {name}".casefold()
            if query and query not in haystack:
                continue
            label = f"{emulator}  •  {game}"
            self.batch_available_names.append(name)
            self.batch_available_list.insert("end", label)
        self._update_batch_visual_summary()

    def refresh_batch_queue_list(self):
        if not hasattr(self, "batch_queue_list"):
            return
        self.batch_queue_list.delete(0, "end")
        profiles = self.profile_data.get("profiles", {})
        for i, name in enumerate(self.batch_queue):
            rec = profiles.get(name, {})
            prefix = "▶  " if self.batch_active and i == self.batch_current_index else f"{i + 1:02d}.  "
            self.batch_queue_list.insert("end", f"{prefix}{rec.get('emulator','Generic')}  •  {rec.get('game_name') or name}")
        if hasattr(self, "batch_queue_hint_var"):
            self.batch_queue_hint_var.set(
                "The highlighted item is currently processing." if self.batch_active else
                "Profiles run from top to bottom. Use the center controls to reorder."
            )
        self._update_batch_visual_summary()

    def batch_add_selected(self):
        for idx in self.batch_available_list.curselection():
            name = self.batch_available_names[idx]
            if name not in self.batch_queue:
                self.batch_queue.append(name)
        self.refresh_batch_queue_list()
        self._save_batch_queue_state("idle")
        self._request_dashboard_metrics_refresh(force=True)

    def batch_remove_selected(self):
        if self.batch_active:
            messagebox.showinfo("Batch running", "Stop the batch before changing the queue.")
            return
        for idx in reversed(self.batch_queue_list.curselection()):
            if 0 <= idx < len(self.batch_queue):
                self.batch_queue.pop(idx)
        self.refresh_batch_queue_list()
        self._save_batch_queue_state("idle")
        self._request_dashboard_metrics_refresh(force=True)

    def batch_move_selected(self, direction):
        if self.batch_active:
            return
        sel = list(self.batch_queue_list.curselection())
        if len(sel) != 1:
            return
        i = sel[0]; j = i + direction
        if 0 <= j < len(self.batch_queue):
            self.batch_queue[i], self.batch_queue[j] = self.batch_queue[j], self.batch_queue[i]
            self.refresh_batch_queue_list()
            self.batch_queue_list.selection_set(j)
            self._save_batch_queue_state("idle")

    def clear_all_queued_caches(self):
        if self.batch_active:
            messagebox.showinfo("Batch running", "Stop the Batch Queue before clearing queued caches.")
            return
        if not self.batch_queue:
            messagebox.showinfo("Batch Queue", "There are no queued profiles.")
            return

        profile_labels = [self.profile_display_label(name) for name in self.batch_queue]
        preview = "\n".join(f"• {label}" for label in profile_labels[:12])
        if len(profile_labels) > 12:
            preview += f"\n• ... and {len(profile_labels) - 12} more"
        if not messagebox.askyesno(
            "Clear all queued caches",
            f"Delete the hash cache and completed-file history for all {len(self.batch_queue)} queued profile(s)?\n\n{preview}"
        ):
            return

        cleared = 0
        failed = []
        for name in self.batch_queue:
            try:
                record = self.profile_data.get("profiles", {}).get(name, {})
                stored_profile_dir = str(record.get("profile_dir", "") or "").strip()
                profile_dir = Path(stored_profile_dir) if stored_profile_dir else (PROFILES_DIR / safe_profile_name(name))
                cache_dir = profile_dir / "_hash_cache"
                processed_log = profile_dir / "processed.txt"

                if cache_dir.exists():
                    shutil.rmtree(cache_dir)
                cache_dir.mkdir(parents=True, exist_ok=True)
                processed_log.unlink(missing_ok=True)
                cleared += 1
            except Exception as exc:
                failed.append(f"{name}: {exc}")

        self.log(f"Cleared hash caches and processed logs for {cleared}/{len(self.batch_queue)} queued profile(s).")
        if failed:
            messagebox.showwarning(
                "Queued cache cleanup",
                f"Cleared {cleared} cache(s), but {len(failed)} failed.\n\n" + "\n".join(failed[:8])
            )
        else:
            messagebox.showinfo(
                "Queued cache cleanup",
                f"Cleared the cache and completed-file history for all {cleared} queued profile(s)."
            )

    def start_batch_queue(self):
        if self.batch_active:
            return
        if not self.batch_queue:
            messagebox.showinfo("Batch Queue", "Add at least one profile to the queue.")
            return
        if self.worker_thread and self.worker_thread.is_alive():
            self.stop()
        self.save_current_profile(quiet=True)
        self.batch_active = True
        self.batch_current_index = -1
        self.batch_shutdown_requested = bool(self.batch_shutdown_var.get())
        self._batch_skip_requested = False
        self._batch_previous_requested = False
        self._batch_last_started_profile = ""
        self._save_batch_queue_state("running")
        self._set_batch_runtime_controls(True)
        self._set_profile_switching_enabled(False)
        self._update_batch_progress()
        self.log(f"Batch queue started: {len(self.batch_queue)} profile(s)")
        self.after(500, self._batch_start_next)

    def _batch_start_next(self):
        if not self.batch_active:
            return
        self.batch_current_index += 1
        if self.batch_current_index >= len(self.batch_queue):
            self._batch_finish()
            return

        self._batch_skip_requested = False
        self._batch_previous_requested = False
        name = self.batch_queue[self.batch_current_index]
        self._batch_last_started_profile = name
        self.load_profile(name, save_current=False)
        self.batch_status_var.set(f"Running {self.batch_current_index + 1}/{len(self.batch_queue)}: {self.profile_display_label(name)}")
        self.refresh_batch_queue_list()
        self._set_batch_runtime_controls(True)

        self._batch_launching = True
        try:
            started = self.start()
        finally:
            self._batch_launching = False
        if started is False:
            self.batch_active = False
            self._batch_skip_requested = False
            self._batch_previous_requested = False
            self._set_batch_runtime_controls(False)
            self._set_profile_switching_enabled(True)
            self.batch_status_var.set(f"Batch stopped: could not start {self.profile_display_label(name)}")
            self._save_batch_queue_state("start_failed")
            self.refresh_batch_queue_list()
            self._update_batch_progress()
            self.log(f"Batch queue stopped because profile could not start: {name}")
            return
        self.after(1000, self._batch_poll_current)

    def _batch_poll_current(self):
        if not self.batch_active:
            return
        self._update_batch_progress()
        if self.worker_thread and self.worker_thread.is_alive():
            self.after(1500, self._batch_poll_current)
            return

        current_name = self.batch_queue[self.batch_current_index] if 0 <= self.batch_current_index < len(self.batch_queue) else self._batch_last_started_profile or "current profile"
        if getattr(self, "_batch_previous_requested", False):
            previous_index = self.batch_current_index - 1
            previous_name = self.batch_queue[previous_index] if 0 <= previous_index < len(self.batch_queue) else "previous profile"
            self.log(f"Batch profile stopped for Previous: {current_name}")
            self.log(f"Batch returning to previous profile: {previous_name}")
            self._batch_previous_requested = False
            self._batch_skip_requested = False
            # _batch_start_next increments the index before launching. Move the cursor
            # back two slots so the next increment lands on the previous profile.
            self.batch_current_index = max(-1, previous_index - 1)
        elif getattr(self, "_batch_skip_requested", False):
            self.log(f"Batch profile skipped: {current_name}")
            self._batch_skip_requested = False
        else:
            self.log(f"Batch profile finished: {current_name}")
        self._update_batch_progress()
        self.after(400, self._batch_start_next)

    def skip_batch_current_game(self):
        """Safely stop the active batch profile and continue with the next queued game."""
        if not self.batch_active:
            messagebox.showinfo("Batch Queue", "Start the Batch Queue before using Skip to next game.")
            return
        if not (0 <= self.batch_current_index < len(self.batch_queue)):
            return
        if getattr(self, "_batch_skip_requested", False) or getattr(self, "_batch_previous_requested", False):
            return

        current_name = self.batch_queue[self.batch_current_index]
        self._batch_skip_requested = True
        self._set_batch_runtime_controls(True, skip_enabled=False, previous_enabled=False)
        self.batch_status_var.set(f"Skipping {self.batch_current_index + 1}/{len(self.batch_queue)}: {self.profile_display_label(current_name)}")
        if hasattr(self, "batch_queue_hint_var"):
            self.batch_queue_hint_var.set("Skip requested. Faithful Remaster will finish the active texture safely, then move to the next game.")
        self.log(f"Skip requested for batch profile: {current_name}")
        self.stop()

    def previous_batch_game(self):
        """Safely stop the active batch profile and return to the previous queued game."""
        if not self.batch_active:
            messagebox.showinfo("Batch Queue", "Start the Batch Queue before using Previous game.")
            return
        if not (0 <= self.batch_current_index < len(self.batch_queue)):
            return
        if self.batch_current_index <= 0:
            messagebox.showinfo("Batch Queue", "There is no previous game before the first queued profile.")
            return
        if getattr(self, "_batch_skip_requested", False) or getattr(self, "_batch_previous_requested", False):
            return

        current_name = self.batch_queue[self.batch_current_index]
        previous_name = self.batch_queue[self.batch_current_index - 1]
        self._batch_previous_requested = True
        self._set_batch_runtime_controls(True, skip_enabled=False, previous_enabled=False)
        self.batch_status_var.set(
            f"Returning to previous game: {self.profile_display_label(previous_name)}"
        )
        if hasattr(self, "batch_queue_hint_var"):
            self.batch_queue_hint_var.set(
                "Previous requested. Faithful Remaster will finish the active texture safely, then return to the previous queued game."
            )
        self.log(f"Previous requested from batch profile: {current_name} -> {previous_name}")
        self.stop()

    def stop_batch_queue(self):
        if not self.batch_active:
            return
        self.batch_active = False
        self._batch_skip_requested = False
        self._batch_previous_requested = False
        self.stop()
        self._set_batch_runtime_controls(False)
        self._set_profile_switching_enabled(True)
        self.batch_status_var.set("Batch stopped")
        self._save_batch_queue_state("stopped")
        self.refresh_batch_queue_list()
        self._update_batch_progress()
        self.log("Batch queue stopped by user.")

    def _batch_finish(self):
        self.batch_active = False
        self._batch_skip_requested = False
        self._batch_previous_requested = False
        self._set_batch_runtime_controls(False)
        self._set_profile_switching_enabled(True)
        self.batch_status_var.set(f"Batch complete: {len(self.batch_queue)} profile(s)")
        self._save_batch_queue_state("complete")
        self.refresh_batch_queue_list()
        self._update_batch_progress()
        self.log("Batch queue complete.")
        if self.batch_shutdown_requested:
            self.log("Shutting down Windows in 60 seconds. Run 'shutdown /a' to cancel.")
            try:
                subprocess.Popen(
                    ["shutdown", "/s", "/t", "60"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **_hidden_subprocess_kwargs(),
                )
            except Exception as exc:
                self.log(f"Shutdown failed: {exc}")

    def _validate_profile_settings(self, settings=None, record=None):
        """Return (ok, lines) for the active profile without mutating settings.

        This is intentionally read-only and mirrors the worker's safety checks:
        paths, emulator identity, RGB workflow route, Alpha workflow route and
        cache/process-log targets. It does not contact ComfyUI and does not run
        any processing.
        """
        settings = dict(DEFAULT_CONFIG if settings is None else settings)
        record = record or {}
        lines = []
        ok = True

        emulator = str(record.get("emulator") or (self.emulator_var.get() if hasattr(self, "emulator_var") else "Generic") or "Generic")
        game_id = str(record.get("game_id") or (self.vars.get("game_id").get().strip() if hasattr(self, "vars") and self.vars.get("game_id") else "") or "").strip()
        lines.append(f"Emulator: {emulator or 'Generic'}" + (f"  •  Game ID: {game_id}" if game_id else "  •  Game ID missing"))

        dump_text = str(settings.get("dump_folder", "") or "").strip()
        load_text = str(settings.get("load_folder", "") or "").strip()
        dump_folder = Path(dump_text) if dump_text else None
        load_folder = Path(load_text) if load_text else None
        if dump_folder and dump_folder.exists():
            lines.append(f"✓ Dump folder exists: {dump_folder}")
        else:
            ok = False
            lines.append(f"✗ Dump folder missing: {dump_text or '(not set)'}")
        if load_folder:
            parent_ok = load_folder.exists() or load_folder.parent.exists()
            if parent_ok:
                lines.append(f"✓ Replacement/Load folder target: {load_folder}")
            else:
                ok = False
                lines.append(f"✗ Replacement/Load parent missing: {load_folder}")
        else:
            ok = False
            lines.append("✗ Replacement/Load folder is not set")

        try:
            profile = find_workflow_profile(settings.get("faithfulness_preset", "Clean Heart"))
            if not profile:
                raise WorkflowValidationError(f"Mode not found: {settings.get('faithfulness_preset')}")
            backend = find_backend_profile(profile.get("backend_id") or settings.get("active_backend_id"), settings)
            if str(backend.get("type") or "comfyui").lower() == "comfyui":
                info = validate_comfy_api_workflow(
                    Path(str(profile.get("api_path") or "")),
                    str(profile.get("load_node") or ""),
                    str(profile.get("save_node") or ""),
                    require_reachable=True,
                )
                lines.append(f"✓ RGB mode route valid: {profile.get('name')}  Load {info['load_node']} → Save {info['save_node']}")
            else:
                if not str(backend.get("command_template") or "").strip():
                    raise WorkflowValidationError("External backend has no command template")
                lines.append(f"✓ RGB mode uses external backend: {backend.get('name')}")
        except Exception as exc:
            ok = False
            lines.append(f"✗ RGB workflow route invalid: {exc}")

        if bool(settings.get("enable_separate_alpha_workflow", True)):
            try:
                info = validate_alpha_comfy_api_workflow(
                    Path(str(settings.get("alpha_workflow_api_json") or "")),
                    str(settings.get("alpha_load_image_node_id") or ""),
                    str(settings.get("alpha_save_image_node_id") or ""),
                    require_reachable=True,
                )
                lines.append(f"✓ Alpha route valid: Load {info['load_node']} → Save {info['save_node']}")
                if bool(settings.get("alpha_workflow_invert_output", False)):
                    lines.append("! Alpha invert output is ON")
            except Exception as exc:
                ok = False
                lines.append(f"✗ Alpha workflow route invalid: {exc}")
        else:
            lines.append("! Separate Alpha workflow disabled")

        processed_log = Path(str(settings.get("processed_log") or "processed.txt"))
        if not processed_log.is_absolute():
            processed_log = PROFILES_DIR / safe_profile_name(self.current_profile_name or "default") / processed_log
        lines.append(f"✓ Processed log target: {processed_log}")
        lines.append(f"✓ Hash cache: {'enabled' if settings.get('enable_hash_cache', True) else 'disabled'}")
        return ok, lines

    def validate_current_profile(self):
        try:
            settings = self.collect()
            record = self.profile_record_from_ui() if hasattr(self, "vars") else {}
            ok, lines = self._validate_profile_settings(settings, record)
            if hasattr(self, "profile_validation_var"):
                self.profile_validation_var.set("Validation: OK" if ok else "Validation: needs attention")
            if hasattr(self, "profile_validation_label"):
                self.profile_validation_label.configure(fg="#7ee8be" if ok else "#ffb37a")
            title = "Profile validation OK" if ok else "Profile validation needs attention"
            messagebox.showinfo(title, "\n".join(lines[:30]))
            self.log(title + ": " + ("; ".join(lines[:8])))
            return ok
        except Exception as exc:
            if hasattr(self, "profile_validation_var"):
                self.profile_validation_var.set("Validation failed")
            if hasattr(self, "profile_validation_label"):
                self.profile_validation_label.configure(fg="#ff7c86")
            messagebox.showerror("Profile validation", str(exc))
            return False

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
            self._refresh_profile_summary()
            self.after(50, lambda: self._request_dashboard_metrics_refresh(force=True))
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

        # Hide an old auto-generated Azahar key such as
        # "Azahar / Citra — 0004000000033500"; the real ID is already shown.
        generated_azahar_key = (
            str(record.get("emulator") or "") == "Azahar / Citra" and game_id and
            normalize_game_id(profile_name) in {normalize_game_id(game_id), normalize_game_id(f"Azahar Citra {game_id}")}
        )
        # Show the internal profile name only when it adds useful distinction.
        if not generated_azahar_key and profile_name.casefold() not in {base.casefold(), game_name.casefold(), game_id.casefold()}:
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
        if hasattr(self, "dashboard_profile_combo"):
            self.dashboard_profile_combo["values"] = labels
        if self.current_profile_name in names:
            self.profile_var.set(self.profile_name_to_display.get(self.current_profile_name, self.current_profile_name))
        elif hasattr(self, "profile_var"):
            self.profile_var.set("")
        return names

    def on_profile_filter_changed(self, _event=None):
        if getattr(self, "batch_active", False):
            messagebox.showinfo("Batch running", "Stop the Batch Queue before changing profiles.")
            return
        selected_emulator = self.profile_emulator_filter_var.get()
        if hasattr(self, "scan_emulator_var"):
            self.scan_emulator_var.set(selected_emulator)
            self.load_scan_roots(selected_emulator)
        names = self.refresh_profile_combo()
        if self.current_profile_name in names:
            return
        if self.current_profile_name:
            self.save_current_profile(quiet=True)
        if names:
            self.load_profile(names[0], save_current=False)
        else:
            self.current_profile_name = ""
            self._update_profile_header_card()
            self.log(f"No {selected_emulator} profiles yet. Click New or Scan for Games.")

    def _update_emulator_path_hint(self):
        if not hasattr(self, "profile_path_hint_var"):
            return
        emulator = self.emulator_var.get() if hasattr(self, "emulator_var") else "Generic"
        hints = {
            FLYCAST_EMULATOR: (
                r"Flycast: data\texdump\<Game ID>  →  data\textures\<Game ID>. "
                r"Select the per-game texdump folder; Faithful Remaster preserves every relative filename."
            ),
            "Dolphin": r"Dolphin: Dump\Textures\<Game ID>  →  Load\Textures\<Game ID>.",
            "PCSX2": r"PCSX2: textures\<Game Serial>\dumps  →  textures\<Game Serial>\replacements.",
            "DuckStation": r"DuckStation: textures\<Game Serial>\dumps  →  textures\<Game Serial>\replacements.",
            "PPSSPP": r"PPSSPP: PSP\TEXTURES\<Game ID>\new  →  PSP\TEXTURES\<Game ID>.",
            "Azahar / Citra": r"Azahar: dump\textures\<Title ID>  →  load\textures\<Title ID>.",
        }
        self.profile_path_hint_var.set(hints.get(emulator, ""))

    def _update_emulator_specific_controls(self):
        emulator = self.emulator_var.get() if hasattr(self, "emulator_var") else "Generic"
        is_azahar = emulator == "Azahar / Citra"
        check = getattr(self, "auto_sync_azahar_check", None)
        if check is not None:
            try:
                if is_azahar:
                    if not check.winfo_manager():
                        check.pack(side="right")
                else:
                    check.pack_forget()
            except Exception:
                pass
        # Azahar metadata reads 3DS SMDH/title icons only; hide this action on
        # all non-Azahar profiles so the Profiles toolbar stays emulator-specific.
        azahar_button = getattr(self, "refresh_azahar_metadata_button", None)
        if azahar_button is not None:
            try:
                if is_azahar:
                    if not azahar_button.winfo_manager():
                        azahar_button.pack(side="left", padx=5)
                else:
                    azahar_button.pack_forget()
            except Exception:
                pass

    def on_profile_emulator_changed(self, _event=None):
        if self._loading_profile:
            return
        selected = self.emulator_var.get() or "Generic"
        self.profile_emulator_filter_var.set(selected)
        if hasattr(self, "scan_emulator_var"):
            self.scan_emulator_var.set(selected)
            self.load_scan_roots(selected)
        self._update_emulator_path_hint()
        self._update_emulator_specific_controls()
        self.auto_fill_profile_folders(only_when_empty=True)
        self.refresh_profile_combo()
        if hasattr(self, "saved_state_var"):
            self.saved_state_var.set("● Unsaved changes")
        self._refresh_profile_summary()

    def auto_fill_profile_folders(self, only_when_empty=False):
        emulator = self.emulator_var.get() or self.profile_emulator_filter_var.get()
        dump_var = self.vars.get("dump_folder")
        load_var = self.vars.get("load_folder")
        game_id_var = self.vars.get("game_id")
        if not dump_var or not load_var:
            return
        dump_text = dump_var.get().strip()
        load_text = load_var.get().strip()
        game_id = game_id_var.get().strip() if game_id_var else ""

        if dump_text:
            detected = detect_emulator_from_dump_path(dump_text, emulator)
            if detected["confidence"] >= 70 and detected["emulator"] != emulator:
                emulator = detected["emulator"]
                self.emulator_var.set(emulator)
                self.profile_emulator_filter_var.set(emulator)
                self._update_emulator_path_hint()
                self._update_emulator_specific_controls()
            dump, load, inferred_id = profile_paths_from_dump_selection(emulator, dump_text, game_id)
            if dump and str(dump) != dump_text and (not only_when_empty or not load_text):
                dump_var.set(str(dump))
            if inferred_id and game_id_var and not game_id:
                game_id_var.set(inferred_id)
                game_id = inferred_id
            if load and (not only_when_empty or not load_text):
                load_var.set(str(load))
        elif not only_when_empty:
            dump_root, _load_root = default_emulator_roots(emulator)
            if dump_root:
                dump, load, _resolved_id = profile_paths_from_dump_selection(emulator, dump_root, game_id)
                dump_var.set(str(dump or dump_root))
                if load:
                    load_var.set(str(load))

        self._update_emulator_path_hint()
        self._refresh_profile_summary()
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
        self._update_emulator_path_hint()
        self._update_emulator_specific_controls()
        self.profile_emulator_filter_var.set(loaded_emulator)
        if hasattr(self, "scan_emulator_var"):
            self.scan_emulator_var.set(loaded_emulator)
            self.load_scan_roots(loaded_emulator)
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
            "dynamic_efb_filter_var": "skip_dynamic_efb_postprocess",
            "delete_cutscene_var": "delete_skipped_cutscene_buffers",
            "auto_cleanup_cutscene_var": "auto_scan_delete_cutscene_buffers_on_start",
            "auto_quarantine_buffers_var": "auto_quarantine_efb_cutscenes",
            "comfy_monitor_var": "enable_comfy_status",
            "pause_comfy_var": "pause_when_comfy_offline",
            "auto_start_comfy_var": "auto_start_comfy_when_watching",
            "auto_missing_var": "auto_check_missing_load",
            "alpha_workflow_var": "enable_separate_alpha_workflow",
            "alpha_wf_invert_var": "alpha_workflow_invert_output",
            "vram_var": "enable_vram_protection",
            "auto_sync_azahar_pack_var": "auto_sync_azahar_pack_json",
            "live_preview_var": "live_texture_preview"
        }
        for attr, key in bool_map.items():
            if hasattr(self, attr):
                getattr(self, attr).set(bool(settings.get(key, DEFAULT_CONFIG.get(key, False))))
        if hasattr(self, "faithfulness_preset_var"):
            self.faithfulness_preset_var.set(normalize_faithfulness_preset(settings.get("faithfulness_preset", "Clean Heart")))
        if hasattr(self, "manager_sort_var"):
            self._manager_set_sort_group_vars_from_cfg(settings)
        self._progress_cache = {"at": 0.0, "profile": "", "done": 0, "total": 0}
        if hasattr(self, "dashboard_mode_var"):
            self.dashboard_mode_var.set(normalize_faithfulness_preset(settings.get("faithfulness_preset", "Clean Heart")))
            self._refresh_mode_cards()

        self.profile_var.set(self.profile_name_to_display.get(name, self.profile_display_label(name)))
        self._update_profile_header_card()
        self.saved_state_var.set("● Saved")
        self._refresh_profile_mode_cards()
        self._refresh_profile_summary()
        save_profiles_data(self.profile_data)
        self._loading_profile = False
        self.log(f"Profile loaded: {name}")
        self.after(50, lambda: self._request_dashboard_metrics_refresh(force=True))

    def on_profile_selected(self, _event=None):
        if getattr(self, "batch_active", False):
            messagebox.showinfo("Batch running", "Stop the Batch Queue before changing profiles.")
            self.profile_var.set(self.profile_name_to_display.get(self.current_profile_name, self.current_profile_name))
            return
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
        emulator = self.profile_emulator_filter_var.get() or "Generic"
        self.profile_data["profiles"][name] = {
            "emulator": emulator,
            "game_name": "",
            "game_id": "",
            "settings": new_profile_settings_for_emulator(emulator)
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
        initial = var.get().strip() if var else ""
        path = filedialog.askdirectory(initialdir=initial if initial and Path(initial).exists() else None)
        if path:
            var.set(path)
            self.update_scan_emulator_detection()

    def update_scan_emulator_detection(self):
        if not hasattr(self, "scan_input_var"):
            return None
        path = self.scan_input_var.get().strip()
        fallback = self.scan_emulator_var.get() if hasattr(self, "scan_emulator_var") else ""
        detected = detect_emulator_from_dump_path(path, fallback)
        if detected["confidence"] >= 35:
            self.scan_emulator_var.set(detected["emulator"])
            text = f"Detected emulator: {detected['emulator']}"
            if detected.get("reason"):
                text += f"  ·  {detected['reason']}"
        else:
            text = "Detected emulator: uncertain — the current profile emulator will be used"
        if hasattr(self, "scan_detected_var"):
            self.scan_detected_var.set(text)
        return detected

    def load_scan_roots(self, emulator=None):
        if not hasattr(self, "scan_input_var"):
            return
        emu = emulator or (self.scan_emulator_var.get() if hasattr(self, "scan_emulator_var") else "Dolphin")
        roots = self.profile_data.get("scan_roots", {}).get(emu, {})
        default_dump, _default_load = default_emulator_roots(emu)
        candidate = roots.get("dump") or roots.get("input") or (str(default_dump) if default_dump else "")
        self.scan_input_var.set(candidate)
        if hasattr(self, "scan_emulator_var"):
            self.scan_emulator_var.set(emu)
        self.update_scan_emulator_detection()

    def save_scan_roots(self):
        if not hasattr(self, "scan_input_var"):
            return
        detected = self.update_scan_emulator_detection() or {}
        emu = detected.get("emulator") or (self.scan_emulator_var.get() if hasattr(self, "scan_emulator_var") else "Generic")
        self.profile_data.setdefault("scan_roots", {})[emu] = {
            "dump": self.scan_input_var.get().strip()
        }

    def choose_scan_emulator(self, suggested="Generic"):
        win = tk.Toplevel(self)
        win.title("Select Emulator")
        win.geometry("520x170")
        win.transient(self); win.grab_set()
        tk.Label(
            win,
            text="This dump folder uses an ambiguous layout. Select the emulator once so replacement folders are derived correctly.",
            justify="left", wraplength=480, anchor="w"
        ).pack(fill="x", padx=14, pady=(14, 8))
        choices = [emu for emu in EMULATORS if emu != "Generic"]
        var = tk.StringVar(value=suggested if suggested in choices else choices[0])
        combo = ttk.Combobox(win, textvariable=var, values=choices, state="readonly")
        combo.pack(fill="x", padx=14, pady=4)
        result = {"value": ""}
        def accept():
            result["value"] = var.get()
            win.destroy()
        ttk.Button(win, text="Continue", style="Accent.TButton", command=accept).pack(side="right", padx=14, pady=12)
        ttk.Button(win, text="Cancel", command=win.destroy).pack(side="right", pady=12)
        win.bind("<Return>", lambda _event: accept())
        self.wait_window(win)
        return result["value"]

    def scan_for_games(self):
        if getattr(self, "_scan_games_running", False):
            self.log("Game-folder scan is already running.")
            return

        dump_text = self.scan_input_var.get().strip()
        input_root = Path(dump_text) if dump_text else Path()
        if not dump_text or not input_root.exists():
            messagebox.showerror("Scan", "Select an existing texture dump folder.")
            return

        detected = self.update_scan_emulator_detection() or {}
        emu = detected.get("emulator", "Generic")
        if detected.get("confidence", 0) < 55 or emu == "Generic":
            fallback = self.profile_emulator_filter_var.get() if hasattr(self, "profile_emulator_filter_var") else emu
            chosen = self.choose_scan_emulator(fallback)
            if not chosen:
                return
            emu = chosen
            self.scan_emulator_var.set(emu)
            if hasattr(self, "scan_detected_var"):
                self.scan_detected_var.set(f"Selected emulator: {emu}  ·  ambiguous folder layout")

        self.save_scan_roots()
        self._scan_games_running = True
        if hasattr(self, "scan_games_button"):
            self.scan_games_button.configure(state="disabled", text="Scanning…")
        if hasattr(self, "scan_status_var"):
            self.scan_status_var.set(f"Scanning {emu} dump folders…")

        existing_ids = {
            (str(record.get("emulator", "")), normalize_game_id(record.get("game_id", "")))
            for record in self.profile_data.get("profiles", {}).values()
            if record.get("game_id")
        }

        def worker():
            try:
                if emu == FLYCAST_EMULATOR and db_platform_count(emu) == 0:
                    self.after(0, lambda: self.scan_status_var.set("Installing Dreamcast title metadata…"))
                    imported, _downloaded, errors = import_platform_game_database(emu, self.log)
                    if imported:
                        self.log(f"Dreamcast title metadata ready: {imported} ID entries.")
                    elif errors:
                        self.log("Dreamcast title metadata could not be installed; folder IDs will be used as fallback names.")

                discovered = discover_game_folders(emu, input_root)
                found = []
                reserved_names = set(self.profile_data.get("profiles", {}))
                azahar_metadata = {}
                if emu == "Azahar / Citra":
                    target_ids = [game_id for game_id, _dump, _load in discovered if is_azahar_title_id(game_id)]
                    azahar_metadata, _az_stats = scan_azahar_game_metadata(
                        target_ids, [input_root],
                        lambda message: self.after(0, lambda m=message: self.scan_status_var.set(m))
                    )

                for game_id, dump, load in discovered:
                    normalized_id = normalize_game_id(game_id)
                    if (emu, normalized_id) in existing_ids:
                        continue
                    if load is None:
                        self.log(f"Skipped {game_id}: could not derive a safe replacement folder from {dump}")
                        continue

                    if emu == "Azahar / Citra" and normalize_azahar_title_id(game_id) in azahar_metadata:
                        az = azahar_metadata[normalize_azahar_title_id(game_id)]
                        resolved = {
                            "title": az.get("title", ""), "region": az.get("region", ""),
                            "source": az.get("source", "Azahar SMDH metadata"), "icon_path": az.get("icon_path", "")
                        }
                    else:
                        resolved = lookup_local_game_title(game_id, emu)
                    if resolved:
                        game_name = format_resolved_game_title(resolved.get("title", ""), resolved.get("region", "")) or game_id
                        region = resolved.get("region", "")
                        source = resolved.get("source", "local title database")
                    elif emu in ("Nintendo 64 / RMG", "Nintendo 64 / Project64"):
                        game_name = humanize_n64_folder_name(game_id)
                        region = ""
                        source = "folder name"
                    else:
                        game_name = game_id
                        region = ""
                        source = "folder ID fallback"

                    base_profile_name = f"{emu} — {game_name}"
                    profile_name = base_profile_name if base_profile_name not in reserved_names else f"{base_profile_name} — {game_id}"
                    suffix = 2
                    unique_name = profile_name
                    while unique_name in reserved_names:
                        unique_name = f"{profile_name} ({suffix})"
                        suffix += 1
                    profile_name = unique_name
                    reserved_names.add(profile_name)
                    found.append((profile_name, game_id, dump, load, game_name, region, source))

                self.after(0, lambda: self._finish_scan_for_games(emu, found))
            except Exception as exc:
                self.after(0, lambda e=exc: self._finish_scan_for_games(emu, [], error=e))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_scan_for_games(self, emu, found, error=None):
        self._scan_games_running = False
        if hasattr(self, "scan_games_button"):
            self.scan_games_button.configure(state="normal", text="Scan for Games")
        if hasattr(self, "game_db_status_var"):
            try:
                self.game_db_status_var.set(f"{self.get_game_database_count():,}")
            except Exception:
                pass

        if error is not None:
            if hasattr(self, "scan_status_var"):
                self.scan_status_var.set("Scan failed")
            messagebox.showerror("Scan", str(error))
            return
        if not found:
            if hasattr(self, "scan_status_var"):
                self.scan_status_var.set("No new game folders found")
            messagebox.showinfo("Scan", "No new game folders were found.")
            return

        resolved_count = sum(1 for item in found if item[6] not in {"folder ID fallback", "folder name"})
        if hasattr(self, "scan_status_var"):
            self.scan_status_var.set(f"Found {len(found)} game(s) — {resolved_count} title name(s) resolved")

        selected = found if self.auto_add_var.get() else self.review_discovered_games(found)
        for profile_name, game_id, dump, load, game_name, _region, source in selected:
            cfg = new_profile_settings_for_emulator(emu)
            cfg.update({
                "dump_folder": str(dump),
                "load_folder": str(load),
                "workflow_api_json": self.cfg.get("workflow_api_json", ""),
                "alpha_workflow_api_json": self.cfg.get("alpha_workflow_api_json", ""),
                "comfy_url": self.cfg.get("comfy_url", "http://127.0.0.1:8188"),
                "comfy_start_file": self.cfg.get("comfy_start_file", "")
            })
            record = {
                "emulator": emu,
                "game_name": game_name,
                "game_id": game_id,
                "title_source": source,
                "settings": cfg
            }
            if emu == "Azahar / Citra":
                metadata = load_azahar_metadata_cache().get(normalize_azahar_title_id(game_id), {})
                if metadata.get("icon_path") and Path(str(metadata.get("icon_path"))).is_file():
                    record["azahar_icon_path"] = metadata.get("icon_path")
                if metadata.get("publisher"):
                    record["publisher"] = metadata.get("publisher")
            self.profile_data["profiles"][profile_name] = record

        save_profiles_data(self.profile_data)
        self.profile_emulator_filter_var.set(emu)
        self.refresh_profile_combo()
        if selected:
            self.load_profile(selected[0][0], save_current=True)
            self.log(f"Added {len(selected)} discovered profile(s) with automatic title lookup.")

    def review_discovered_games(self, found):
        win = tk.Toplevel(self)
        win.title("Discovered Games")
        win.geometry("980x520")
        win.transient(self); win.grab_set()
        tk.Label(
            win,
            text="Select game folders to add. Resolved titles are shown before the original folder ID.",
            font="SegoeUI 11 bold"
        ).pack(anchor="w", padx=12, pady=(12, 4))
        tk.Label(
            win,
            text="Unknown IDs remain selectable and use the folder ID as a safe fallback name.",
            fg="#7f8c99"
        ).pack(anchor="w", padx=12, pady=(0, 8))
        lb = tk.Listbox(win, selectmode="extended", font=("Segoe UI", 9))
        lb.pack(fill="both", expand=True, padx=12, pady=6)
        for item in found:
            _profile_name, game_id, dump, _load, game_name, _region, source = item
            marker = "Resolved" if source not in {"folder ID fallback", "folder name"} else "Fallback"
            lb.insert("end", f"[{marker}]  {game_name}  ←  {game_id}    |    {dump}")
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
        manager = self.section(self.tab_workflows, "Workflow Profile Manager")
        tk.Label(manager, text="Game modes and hidden task routes use external UI/API files. Clean Heart and Strong Believer remain the visible modes; N64 Strip Safe is an automatic hidden route. Every workflow can select its own backend or inherit the active backend.", fg="#9ca3af", anchor="w", justify="left").pack(fill="x", padx=12, pady=(6,4))
        list_frame=tk.Frame(manager); list_frame.pack(fill="both", padx=12, pady=6)
        profile_list=tk.Listbox(list_frame, height=7, exportselection=False)
        profile_list.pack(side="left", fill="both", expand=True)
        self.workflow_profile_listbox = profile_list
        self._workflow_profile_ids = []
        profile_scroll=tk.Scrollbar(list_frame, command=profile_list.yview); profile_scroll.pack(side="right", fill="y")
        profile_list.config(yscrollcommand=profile_scroll.set)

        def refresh_profile_list(select_id=None):
            profiles=load_workflow_profiles(); profile_list.delete(0,"end")
            self._workflow_profile_ids = [str(p.get("id") or "") for p in profiles]
            selected=0
            for i,p in enumerate(profiles):
                marker="Built-in" if p.get("builtin") else "Custom"
                enabled="" if p.get("enabled",True) else " [Disabled]"
                profile_list.insert("end", f"{p['name']}  —  {marker}{enabled}")
                if select_id and p.get("id")==select_id: selected=i
            if profiles:
                profile_list.selection_set(selected); profile_list.see(selected)
                # Keep the visible replacement API fields synchronized after
                # Refresh/Edit/Delete/Restore, not only after a mouse click.
                try:
                    profile_list.event_generate("<<ListboxSelect>>")
                except Exception:
                    pass
            if hasattr(self,"faithfulness_preset_combo"):
                self.faithfulness_preset_combo.configure(values=workflow_profile_names())

        def selected_profile():
            sel = profile_list.curselection()
            if not sel:
                return None
            index = int(sel[0])
            ids = getattr(self, "_workflow_profile_ids", [])
            if index >= len(ids):
                return None
            selected_id = str(ids[index])
            return next(
                (item for item in load_workflow_profiles() if str(item.get("id")) == selected_id),
                None,
            )

        def edit_profile_dialog(existing=None, duplicate=False):
            base=dict(existing or {})
            win=tk.Toplevel(self); win.title("Workflow Profile"); win.geometry("860x575"); win.transient(self); win.grab_set()
            fields={}
            defaults={
                "name": (base.get("name","") + (" Copy" if duplicate else "")),
                "ui_path":base.get("ui_path",""), "api_path":base.get("api_path",""),
                "load_node":base.get("load_node","1"), "save_node":base.get("save_node","4"),
                "backend_id":base.get("backend_id",""),
                "task_type":base.get("task_type","standard"),
                "output_scale":base.get("output_scale",4),
            }
            rows=(
                ("name","Profile name"),("ui_path","UI workflow JSON"),("api_path","API workflow JSON"),
                ("load_node","LoadImage node ID"),("save_node","SaveImage node ID"),
                ("backend_id","Backend ID (blank = active)"),("task_type","Semantic task type"),
                ("output_scale","Expected output scale"),
            )
            backend_ids=[x.get("id","") for x in load_backend_profiles().get("backends",[])] if 'load_backend_profiles' in globals() else []
            for row,(key,label) in enumerate(rows):
                tk.Label(win,text=label,width=27,anchor="w").grid(row=row,column=0,padx=10,pady=7,sticky="w")
                var=tk.StringVar(value=str(defaults[key])); fields[key]=var
                if key=="backend_id":
                    widget=ttk.Combobox(win,textvariable=var,values=("",)+tuple(backend_ids),state="readonly")
                elif key=="task_type":
                    widget=ttk.Combobox(win,textvariable=var,values=("standard","n64_strip_safe","alpha","custom"),state="normal")
                else:
                    widget=tk.Entry(win,textvariable=var)
                widget.grid(row=row,column=1,padx=6,pady=7,sticky="ew")
                if key in ("ui_path","api_path"):
                    tk.Button(win,text="Browse",command=lambda v=var: (lambda x: v.set(x) if x else None)(filedialog.askopenfilename(filetypes=[("JSON files","*.json"),("All files","*.*")]))).grid(row=row,column=2,padx=6)
            enabled=tk.BooleanVar(value=bool(base.get("enabled",True)))
            show_as_mode=tk.BooleanVar(value=bool(base.get("show_as_mode",base.get("task_type","standard")=="standard")))
            toggles=tk.Frame(win); toggles.grid(row=8,column=1,sticky="w",padx=6,pady=5)
            tk.Checkbutton(toggles,text="Enabled",variable=enabled).pack(side="left")
            tk.Checkbutton(toggles,text="Show as game mode",variable=show_as_mode).pack(side="left",padx=(18,0))
            win.columnconfigure(1,weight=1)
            result={}
            def auto_detect():
                try:
                    info=detect_comfy_nodes_from_api(Path(fields['api_path'].get().strip()))
                    load_id = info.get('best_load')
                    save_id = info.get('best_save')
                    if not load_id or not save_id:
                        details = "\n".join(info.get("warnings", [])) or "No usable LoadImage/SaveImage route was found."
                        raise WorkflowValidationError(details)
                    fields['load_node'].set(str(load_id))
                    fields['save_node'].set(str(save_id))
                    validate_comfy_api_workflow(
                        Path(fields['api_path'].get().strip()), str(load_id), str(save_id),
                        require_reachable=True
                    )
                    messagebox.showinfo("Auto detect", "A connected LoadImage → SaveImage route was detected and validated.", parent=win)
                except Exception as e:
                    messagebox.showerror("Auto detect", str(e), parent=win)
            tk.Button(win,text="Auto Detect Nodes",command=auto_detect).grid(row=9,column=1,sticky="w",padx=6,pady=8)
            def save_it():
                name = fields['name'].get().strip()
                api_text = fields['api_path'].get().strip()
                if not name:
                    return messagebox.showerror("Workflow Profile", "Enter a profile name.", parent=win)
                try:
                    backend = find_backend_profile(
                        fields['backend_id'].get().strip() or self.cfg.get("active_backend_id"),
                        self.cfg,
                    )
                    backend_kind = str(backend.get("type") or "comfyui").lower()
                    if backend_kind == "comfyui":
                        if not api_text:
                            raise WorkflowValidationError("Select an API workflow JSON file for this ComfyUI mode.")
                        validate_comfy_api_workflow(
                            Path(api_text), fields['load_node'].get().strip(), fields['save_node'].get().strip(),
                            require_reachable=True,
                        )
                    elif backend_kind == "external_command":
                        if not str(backend.get("command_template") or "").strip():
                            raise WorkflowValidationError(
                                f"External backend {backend.get('name')!r} has no command template."
                            )
                        if api_text and not Path(api_text).is_file():
                            raise WorkflowValidationError(
                                "The optional API/template file configured for this external backend does not exist."
                            )
                    else:
                        raise WorkflowValidationError(f"Unsupported backend type: {backend_kind}")
                    if fields['ui_path'].get().strip():
                        validate_comfy_ui_workflow(fields['ui_path'].get().strip())
                    if int(float(fields['output_scale'].get())) < 1:
                        raise ValueError("Output scale must be at least 1")
                except Exception as e:
                    return messagebox.showerror("Workflow Profile", f"Invalid workflow route or scale:\n{e}", parent=win)
                result.update({k:v.get().strip() for k,v in fields.items()})
                result['output_scale']=int(float(result['output_scale']))
                result['enabled']=enabled.get(); result['show_as_mode']=show_as_mode.get(); win.destroy()
            btn=tk.Frame(win); btn.grid(row=10,column=0,columnspan=3,sticky="e",padx=10,pady=10)
            tk.Button(btn,text="Cancel",command=win.destroy).pack(side="right",padx=4)
            tk.Button(btn,text="Save Profile",command=save_it).pack(side="right",padx=4)
            self.wait_window(win)
            if not result: return
            profiles=load_workflow_profiles()
            if existing and not duplicate:
                target=next((x for x in profiles if x['id']==existing['id']),None)
                if target: target.update(result)
                pid=existing['id']
            else:
                slug=re.sub(r'[^a-z0-9]+','_',result['name'].casefold()).strip('_') or uuid.uuid4().hex[:8]
                existing_ids={x['id'] for x in profiles}; pid=slug; n=2
                while pid in existing_ids: pid=f"{slug}_{n}"; n+=1
                result.update({'id':pid,'builtin':False}); profiles.append(result)
            save_workflow_profiles(profiles); refresh_profile_list(pid)

        def delete_profile():
            item=selected_profile()
            if not item: return
            if item.get('builtin'):
                return messagebox.showinfo("Workflow Profile","Built-in profiles cannot be deleted. They can be edited or disabled.")
            if not messagebox.askyesno("Delete Workflow Profile",f"Delete {item['name']}?",parent=self): return
            save_workflow_profiles([x for x in load_workflow_profiles() if x['id']!=item['id']]); refresh_profile_list()

        def restore_builtin():
            item=selected_profile()
            if not item or not item.get('builtin'): return
            defaults={x['id']:x for x in _builtin_workflow_profiles()}; profiles=load_workflow_profiles()
            for i,x in enumerate(profiles):
                if x['id']==item['id']: profiles[i]=defaults[item['id']]
            save_workflow_profiles(profiles); refresh_profile_list(item['id'])

        def validate_selected_profile():
            item = selected_profile()
            if not item:
                return messagebox.showerror("Validate Workflow", "Select a workflow profile first.", parent=self)
            try:
                backend = find_backend_profile(
                    item.get("backend_id") or self.cfg.get("active_backend_id"), self.cfg
                )
                backend_kind = str(backend.get("type") or "comfyui").lower()
                if backend_kind == "comfyui":
                    info = validate_comfy_api_workflow(
                        Path(str(item.get("api_path") or "")),
                        str(item.get("load_node") or ""),
                        str(item.get("save_node") or ""),
                        require_reachable=True,
                    )
                    route_text = (
                        f"LoadImage: {info['load_node']}\nSaveImage: {info['save_node']}"
                    )
                elif backend_kind == "external_command":
                    if not str(backend.get("command_template") or "").strip():
                        raise WorkflowValidationError(
                            f"External backend {backend.get('name')!r} has no command template."
                        )
                    optional_api = str(item.get("api_path") or "").strip()
                    if optional_api and not Path(optional_api).is_file():
                        raise WorkflowValidationError(
                            "The optional API/template file configured for this external backend does not exist."
                        )
                    route_text = f"External command backend: {backend.get('name')}"
                else:
                    raise WorkflowValidationError(f"Unsupported backend type: {backend_kind}")
                if str(item.get("ui_path") or "").strip():
                    validate_comfy_ui_workflow(item.get("ui_path"))
                scale = int(item.get("output_scale", 0))
                if scale < 1:
                    raise WorkflowValidationError("Expected output scale must be at least 1.")
            except Exception as exc:
                return messagebox.showerror("Validate Workflow", f"Validation failed:\n{exc}", parent=self)
            messagebox.showinfo(
                "Validate Workflow",
                f"{item.get('name', item.get('id'))} is valid.\n\n"
                f"{route_text}\nExpected scale: {scale}×",
                parent=self,
            )

        controls=tk.Frame(manager); controls.pack(fill="x",padx=12,pady=(0,10))
        tk.Button(controls,text="Add Custom Profile",command=lambda:edit_profile_dialog()).pack(side="left")
        tk.Button(controls,text="Edit Selected",command=lambda:edit_profile_dialog(selected_profile())).pack(side="left",padx=4)
        tk.Button(controls,text="Duplicate",command=lambda:edit_profile_dialog(selected_profile(),True)).pack(side="left",padx=4)
        tk.Button(controls,text="Delete Custom",command=delete_profile).pack(side="left",padx=4)
        tk.Button(controls,text="Restore Built-in",command=restore_builtin).pack(side="left",padx=4)
        tk.Button(controls,text="Validate Selected",command=validate_selected_profile).pack(side="left",padx=4)
        tk.Button(controls,text="Refresh",command=refresh_profile_list).pack(side="right")
        refresh_profile_list()

        rgb = self.section(self.tab_workflows, "Apply RGB API to the selected workflow mode")
        tk.Label(
            rgb,
            text=("This edits the actual workflow selected in Workflow Profile Manager above. "
                  "Every game profile and per-texture override using that mode will use the new API. "
                  "It no longer writes a disconnected legacy per-game field."),
            fg="#71b9d6", anchor="w", justify="left", wraplength=1050
        ).pack(fill="x", padx=12, pady=(6, 2))
        self.workflow_selected_mode_var = tk.StringVar(value="Selected mode: —")
        tk.Label(
            rgb, textvariable=self.workflow_selected_mode_var,
            fg="#8adbd1", anchor="w", font=("Segoe UI", 9, "bold")
        ).pack(fill="x", padx=12, pady=(2, 0))
        api_row = tk.Frame(rgb); api_row.pack(fill="x", padx=12, pady=6)
        tk.Label(api_row, text="Replacement RGB workflow API JSON", width=28, anchor="w").pack(side="left")
        self.rgb_workflow_api_var = tk.StringVar(value="")
        tk.Entry(api_row, textvariable=self.rgb_workflow_api_var).pack(side="left", fill="x", expand=True, padx=6)
        tk.Button(
            api_row, text="Browse",
            command=lambda: (lambda value: self.rgb_workflow_api_var.set(value) if value else None)(
                filedialog.askopenfilename(filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
            )
        ).pack(side="right")
        ids = tk.Frame(rgb); ids.pack(fill="x", padx=12, pady=6)
        tk.Label(ids, text="Load Image node ID", width=28, anchor="w").pack(side="left")
        self.rgb_load_node_var = tk.StringVar(value="1")
        tk.Entry(ids, textvariable=self.rgb_load_node_var, width=10).pack(side="left", padx=6)
        tk.Label(ids, text="Save Image node ID").pack(side="left", padx=(25, 5))
        self.rgb_save_node_var = tk.StringVar(value="4")
        tk.Entry(ids, textvariable=self.rgb_save_node_var, width=10).pack(side="left", padx=6)
        tk.Button(ids, text="Auto Detect RGB Nodes", command=self.auto_detect_rgb_nodes).pack(side="right")

        bulk = tk.Frame(rgb); bulk.pack(fill="x", padx=12, pady=(2, 10))
        tk.Button(
            bulk,
            text="Apply API to Selected Workflow Mode",
            command=self.apply_rgb_api_to_all_profiles
        ).pack(side="left")
        tk.Label(
            bulk,
            text="Updates the selected mode itself; all games using that mode inherit it immediately.",
            fg="#9ca3af", anchor="w"
        ).pack(side="left", padx=10, fill="x", expand=True)

        def populate_selected_workflow_route(_event=None):
            item = selected_profile()
            if not item:
                self.workflow_selected_mode_var.set("Selected mode: —")
                return
            self.workflow_selected_mode_var.set(
                f"Selected mode: {item.get('name', item.get('id'))}  •  "
                f"{item.get('task_type', 'standard')}  •  {item.get('output_scale', 4)}×"
            )
            self.rgb_workflow_api_var.set(str(item.get("api_path") or ""))
            self.rgb_load_node_var.set(str(item.get("load_node") or "1"))
            self.rgb_save_node_var.set(str(item.get("save_node") or "4"))

        profile_list.bind("<<ListboxSelect>>", populate_selected_workflow_route, add="+")
        populate_selected_workflow_route()

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

        alpha_bulk = tk.Frame(alpha); alpha_bulk.pack(fill="x", padx=12, pady=(2, 10))
        tk.Button(
            alpha_bulk,
            text="Apply Alpha API to All Profiles",
            command=self.apply_alpha_api_to_all_profiles
        ).pack(side="left")
        tk.Label(
            alpha_bulk,
            text="Copies the selected Alpha API path, node IDs, enable state, and invert setting to every game profile and saves them.",
            fg="#9ca3af", anchor="w"
        ).pack(side="left", padx=10, fill="x", expand=True)

        comfy = self.section(self.tab_workflows, "Legacy ComfyUI adapter")
        self.labeled_entry(comfy, "ComfyUI URL", "comfy_url")
        self.labeled_entry(comfy, "ComfyUI start file", "comfy_start_file", "start_file")
        buttons = tk.Frame(comfy); buttons.pack(fill="x", padx=12, pady=8)
        tk.Button(buttons, text="Start Backend", command=self.start_comfy_ui).pack(side="left")
        tk.Button(buttons, text="Check Backend", command=self.check_comfy_now).pack(side="left", padx=6)
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
        """Detect nodes from the visible replacement API field for the selected mode."""
        path = str(self.rgb_workflow_api_var.get() or "").strip()
        if not path:
            self.log("Auto-detect RGB workflow: no replacement API selected.")
            return
        info = detect_comfy_nodes_from_api(path)
        if info.get("best_load"):
            self.rgb_load_node_var.set(str(info["best_load"]))
        if info.get("best_save"):
            self.rgb_save_node_var.set(str(info["best_save"]))
        self.log(
            f"Selected RGB mode nodes: Load={info.get('best_load') or '?'} "
            f"Save={info.get('best_save') or '?'}"
        )
        for warning in info.get("warnings", []):
            self.log(f"Selected RGB mode: {warning}")

    def apply_rgb_api_to_all_profiles(self):
        """Update the ComfyUI workflow profile selected in the manager."""
        listbox = getattr(self, "workflow_profile_listbox", None)
        selected = listbox.curselection() if listbox is not None else ()
        if not selected:
            messagebox.showerror("Apply RGB API", "Select the workflow mode to update in Workflow Profile Manager first.")
            return
        index = int(selected[0])
        ids = getattr(self, "_workflow_profile_ids", [])
        if index >= len(ids):
            messagebox.showerror("Apply RGB API", "The selected workflow list is out of date. Click Refresh and try again.")
            return
        selected_id = str(ids[index])
        profiles = load_workflow_profiles()
        target = next((item for item in profiles if str(item.get("id")) == selected_id), None)
        if target is None:
            messagebox.showerror("Apply RGB API", "The selected workflow profile no longer exists.")
            return

        backend = find_backend_profile(
            target.get("backend_id") or self.cfg.get("active_backend_id"), self.cfg
        )
        if str(backend.get("type") or "comfyui").lower() != "comfyui":
            messagebox.showerror(
                "Apply RGB API",
                "The selected mode uses an External Command backend. Use Edit Selected in "
                "Workflow Profile Manager to configure its optional template/API file.",
            )
            return

        path_text = str(self.rgb_workflow_api_var.get() or "").strip()
        if not path_text:
            messagebox.showerror("Apply RGB API", "Select a replacement RGB workflow API JSON file first.")
            return
        workflow_path = Path(path_text).expanduser()
        load_id = str(self.rgb_load_node_var.get()).strip()
        save_id = str(self.rgb_save_node_var.get()).strip()
        try:
            validate_comfy_api_workflow(workflow_path, load_id, save_id, require_reachable=True)
        except Exception as exc:
            messagebox.showerror("Apply RGB API", f"Invalid workflow route:\n{exc}")
            return

        normalized_path = str(workflow_path.resolve())
        mode_name = str(target.get("name") or selected_id)
        if not messagebox.askyesno(
            "Apply API to Workflow Mode",
            f"Update the actual workflow mode '{mode_name}'?\n\n{normalized_path}\n\n"
            f"LoadImage: {load_id}    SaveImage: {save_id}\n\n"
            "Every game profile and per-texture override using this mode will use the new route immediately.",
        ):
            return

        target["api_path"] = normalized_path
        target["load_node"] = load_id
        target["save_node"] = save_id
        save_workflow_profiles(profiles)

        # Keep legacy fields synchronized only for backward compatibility. The
        # Worker resolves the selected workflow profile directly.
        self.cfg["workflow_api_json"] = normalized_path
        self.cfg["load_image_node_id"] = load_id
        self.cfg["save_image_node_id"] = save_id
        save_config(self.cfg)
        self.rgb_workflow_api_var.set(normalized_path)
        self.rgb_load_node_var.set(load_id)
        self.rgb_save_node_var.set(save_id)
        self.saved_state_var.set("● Saved")
        self.log(f"Workflow mode '{mode_name}' updated: {normalized_path} (Load {load_id} → Save {save_id})")
        messagebox.showinfo(
            "Apply RGB API",
            f"Updated workflow mode: {mode_name}\n\nLoadImage: {load_id}    SaveImage: {save_id}",
        )

    def apply_alpha_api_to_all_profiles(self):
        path_text = self.vars.get("alpha_workflow_api_json").get().strip()
        if not path_text:
            messagebox.showerror("Apply Alpha API", "Select an Alpha workflow API JSON file first.")
            return
        workflow_path = Path(path_text).expanduser()
        load_id = str(self.vars.get("alpha_load_image_node_id").get()).strip()
        save_id = str(self.vars.get("alpha_save_image_node_id").get()).strip()
        try:
            validate_alpha_comfy_api_workflow(workflow_path, load_id, save_id, require_reachable=True)
        except Exception as exc:
            messagebox.showerror("Apply Alpha API", f"Invalid Alpha workflow route:\n{exc}")
            return

        profiles = self.profile_data.setdefault("profiles", {})
        if not profiles:
            messagebox.showinfo("Apply Alpha API", "There are no game profiles to update.")
            return

        enabled = bool(self.alpha_workflow_var.get()) if hasattr(self, "alpha_workflow_var") else True
        invert_output = bool(self.alpha_wf_invert_var.get()) if hasattr(self, "alpha_wf_invert_var") else False
        normalized_path = str(workflow_path.resolve())
        if not messagebox.askyesno(
            "Apply Alpha API to All Profiles",
            f"Apply this validated Alpha route to all {len(profiles)} profile(s)?\n\n{normalized_path}\n\n"
            f"LoadImage: {load_id}    SaveImage: {save_id}\n"
            f"Separate alpha: {'On' if enabled else 'Off'}    Invert output: {'On' if invert_output else 'Off'}\n\n"
            "The change will be saved immediately.",
        ):
            return

        for record in profiles.values():
            settings = record.setdefault("settings", {})
            settings["alpha_workflow_api_json"] = normalized_path
            settings["alpha_load_image_node_id"] = load_id
            settings["alpha_save_image_node_id"] = save_id
            settings["alpha_workflow_invert_output"] = invert_output
            settings["enable_separate_alpha_workflow"] = enabled

        self.profile_data["active_profile"] = self.current_profile_name
        save_profiles_data(self.profile_data)
        self.cfg["alpha_workflow_api_json"] = normalized_path
        self.cfg["alpha_load_image_node_id"] = load_id
        self.cfg["alpha_save_image_node_id"] = save_id
        self.cfg["alpha_workflow_invert_output"] = invert_output
        self.cfg["enable_separate_alpha_workflow"] = enabled
        save_config(self.cfg)
        self.vars["alpha_workflow_api_json"].set(normalized_path)
        self.vars["alpha_load_image_node_id"].set(load_id)
        self.vars["alpha_save_image_node_id"].set(save_id)
        self.saved_state_var.set("● Saved")
        self.log(f"Validated Alpha API applied to all {len(profiles)} profile(s): {normalized_path}")
        messagebox.showinfo(
            "Apply Alpha API",
            f"Updated and saved {len(profiles)} profile(s).\n\nLoadImage: {load_id}    SaveImage: {save_id}",
        )

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

        cutscene = self.section(self.tab_processing, "Cutscene, EFB and post-processing buffer filters")
        self.cutscene_filter_var = tk.BooleanVar(value=bool(self.cfg.get("skip_cutscene_buffers", True)))
        self.dynamic_efb_filter_var = tk.BooleanVar(value=bool(self.cfg.get("skip_dynamic_efb_postprocess", True)))
        self.delete_cutscene_var = tk.BooleanVar(value=bool(self.cfg.get("delete_skipped_cutscene_buffers", False)))
        self.auto_cleanup_cutscene_var = tk.BooleanVar(value=bool(self.cfg.get("auto_scan_delete_cutscene_buffers_on_start", False)))
        self.auto_quarantine_buffers_var = tk.BooleanVar(value=bool(self.cfg.get("auto_quarantine_efb_cutscenes", False)))
        tk.Checkbutton(cutscene, text="Skip cutscene buffers and empty/black dumps", variable=self.cutscene_filter_var).pack(anchor="w", padx=12, pady=4)
        tk.Checkbutton(cutscene, text="Skip dynamic EFB / post-processing dumps", variable=self.dynamic_efb_filter_var).pack(anchor="w", padx=12, pady=4)
        tk.Checkbutton(
            cutscene,
            text="Auto-quarantine strict EFB + cutscenes before watching / each Batch profile",
            variable=self.auto_quarantine_buffers_var
        ).pack(anchor="w", padx=12, pady=4)
        bulk_row = tk.Frame(cutscene); bulk_row.pack(fill="x", padx=32, pady=(0, 4))
        tk.Label(bulk_row, text="Live bulk threshold", width=22, anchor="w").pack(side="left")
        self.vars["auto_quarantine_live_threshold"] = tk.StringVar(
            value=str(self.cfg.get("auto_quarantine_live_threshold", 12))
        )
        tk.Entry(bulk_row, textvariable=self.vars["auto_quarantine_live_threshold"], width=8).pack(side="left")
        tk.Label(bulk_row, text="files; flush smaller batch after", padx=8).pack(side="left")
        self.vars["auto_quarantine_live_idle_seconds"] = tk.StringVar(
            value=str(self.cfg.get("auto_quarantine_live_idle_seconds", 5.0))
        )
        tk.Entry(bulk_row, textvariable=self.vars["auto_quarantine_live_idle_seconds"], width=8).pack(side="left")
        tk.Label(bulk_row, text="seconds idle").pack(side="left", padx=6)
        tk.Checkbutton(cutscene, text="Quarantine skipped cutscene / blank dumps (legacy per-file mode)", variable=self.delete_cutscene_var).pack(anchor="w", padx=12, pady=4)
        tk.Checkbutton(
            cutscene,
            text="Safe startup blank-dump quarantine",
            variable=self.auto_cleanup_cutscene_var
        ).pack(anchor="w", padx=12, pady=4)
        tk.Label(
            cutscene,
            text="Strict auto mode is reversible and uses one startup scan plus batched live moves. It does not use Effects/Masks or Safe Blank Cleanup.",
            fg="#65d8cd"
        ).pack(anchor="w", padx=32, pady=(0, 5))

        protection = self.section(self.tab_processing, "Status and VRAM protection")
        self.comfy_monitor_var = tk.BooleanVar(value=bool(self.cfg.get("enable_comfy_status", True)))
        self.pause_comfy_var = tk.BooleanVar(value=bool(self.cfg.get("pause_when_comfy_offline", True)))
        self.auto_start_comfy_var = tk.BooleanVar(value=bool(self.cfg.get("auto_start_comfy_when_watching", True)))
        self.auto_missing_var = tk.BooleanVar(value=bool(self.cfg.get("auto_check_missing_load", True)))
        self.vram_var = tk.BooleanVar(value=bool(self.cfg.get("enable_vram_protection", False)))
        for text, var in [
            ("Monitor processing backend status", self.comfy_monitor_var),
            ("Pause while the processing backend is offline", self.pause_comfy_var),
            ("If the processing backend is offline, start it when watching begins", self.auto_start_comfy_var),
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

        auto_start_row = tk.Frame(self.tab_monitor)
        auto_start_row.pack(fill="x", padx=18, pady=(0, 8))
        tk.Checkbutton(
            auto_start_row,
            text="If ComfyUI is offline, start it automatically",
            variable=self.auto_start_comfy_var
        ).pack(anchor="w")

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
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 2: width = 430
        if height <= 2: height = 300
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
            self.preview_status_var.set(stage + (f" — {Path(input_path).name}" if input_path else ""))
            return

        self._preview_last_input = input_path
        self._preview_last_output = output_path

        try:
            ow = self.preview_original_canvas.winfo_width()
            oh = self.preview_original_canvas.winfo_height()
            ew = self.preview_enhanced_canvas.winfo_width()
            eh = self.preview_enhanced_canvas.winfo_height()
            original_size = (max(220, ow - 4) if ow > 10 else 430, max(180, oh - 4) if oh > 10 else 300)
            enhanced_size = (max(220, ew - 4) if ew > 10 else 430, max(180, eh - 4) if eh > 10 else 300)

            input_dims = None
            output_dims = None
            if input_path:
                if PIL_AVAILABLE:
                    try:
                        with Image.open(input_path) as img:
                            input_dims = img.size
                    except Exception:
                        pass
                photo = self._make_preview_photo(input_path, original_size)
                if photo:
                    self._preview_original_photo = photo
                    self._set_preview_canvas(self.preview_original_canvas, photo=photo)
                else:
                    self._set_preview_canvas(self.preview_original_canvas, placeholder="Preview unavailable")
            else:
                self._preview_original_photo = None
                self._set_preview_canvas(self.preview_original_canvas, placeholder="No preview")

            if output_path and Path(output_path).is_file():
                if PIL_AVAILABLE:
                    try:
                        with Image.open(output_path) as img:
                            output_dims = img.size
                    except Exception:
                        pass
                photo = self._make_preview_photo(output_path, enhanced_size)
                if photo:
                    self._preview_enhanced_photo = photo
                    self._set_preview_canvas(self.preview_enhanced_canvas, photo=photo)
                else:
                    self._set_preview_canvas(self.preview_enhanced_canvas, placeholder="Preview unavailable")
            else:
                self._preview_enhanced_photo = None
                self._set_preview_canvas(self.preview_enhanced_canvas, placeholder="Processing…" if input_path else "Waiting for output")

            filename = Path(input_path).name if input_path else ""
            self.preview_status_var.set(stage + (f" — {filename}" if filename else ""))
            if hasattr(self, "preview_file_var"):
                self.preview_file_var.set(f"File  {filename or '—'}")
                if input_dims and output_dims:
                    self.preview_resolution_var.set(f"Resolution  {input_dims[0]}×{input_dims[1]} → {output_dims[0]}×{output_dims[1]}")
                elif input_dims:
                    self.preview_resolution_var.set(f"Resolution  {input_dims[0]}×{input_dims[1]}")
                else:
                    self.preview_resolution_var.set("Resolution  —")
        except Exception as exc:
            self.preview_status_var.set(f"Preview error: {exc}")

    # ---------- Texture manager ----------
    def build_manager_tab(self):
        page = self.tab_manager
        page.configure(bg="#0b1119")
        page.grid_rowconfigure(2, weight=1)
        page.grid_columnconfigure(0, weight=1)

        hero = tk.Frame(page, bg="#0b1119")
        hero.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 6))
        hero_left = tk.Frame(hero, bg="#0b1119")
        hero_left.pack(side="left", fill="x", expand=True)
        tk.Label(hero_left, text="Texture Manager", bg="#0b1119", fg="#f2f7fa",
                 font=("Segoe UI", 18, "bold"), anchor="w").pack(fill="x")
        tk.Label(
            hero_left,
            text="Inspect dump/output pairs, choose per-texture modes, recreate results and manage exceptions without opening another window.",
            bg="#0b1119", fg="#91a4b7", font=("Segoe UI", 9), anchor="w"
        ).pack(fill="x", pady=(2, 0))
        self.manager_scan_badge_var = tk.StringVar(value="Open this tab to scan")
        self.manager_scan_badge = tk.Label(
            hero, textvariable=self.manager_scan_badge_var, bg="#152431", fg="#9cc8f5",
            padx=12, pady=7, font=("Segoe UI", 9, "bold")
        )
        self.manager_scan_badge.pack(side="right", padx=(8, 0), pady=3)
        self.manager_priority_badge_var = tk.StringVar(value="Manager priority: 0")
        self.manager_priority_badge = tk.Label(
            hero, textvariable=self.manager_priority_badge_var, bg="#152431", fg="#91a8ba",
            padx=12, pady=7, font=("Segoe UI", 9, "bold")
        )
        self.manager_priority_badge.pack(side="right", padx=(12, 0), pady=3)
        HoverTooltip(
            self.manager_priority_badge,
            "Texture Manager recreation requests are the highest queue lane. The current ComfyUI job finishes safely, then Manager requests run before High and Low priority textures."
        )

        toolbar_outer, toolbar = self._dashboard_panel(page, bg="#0d151f", border="#223141", padx=8, pady=7)
        toolbar_outer.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 6))
        row = tk.Frame(toolbar, bg="#0d151f")
        row.pack(fill="x", padx=10, pady=9)
        self.manager_profile_label_var = tk.StringVar(value="Profile: —")
        tk.Label(row, textvariable=self.manager_profile_label_var, bg="#0d151f", fg="#65d8cd",
                 font=("Segoe UI", 9, "bold"), anchor="w").pack(side="left", padx=(0, 12))
        self.manager_search_var = tk.StringVar()
        search = tk.Entry(row, textvariable=self.manager_search_var, bg="#091019", fg="#e6edf3",
                          insertbackground="#ffffff", relief="flat", bd=0)
        search.pack(side="left", fill="x", expand=True, ipady=6)
        # One compact multi-filter menu replaces the old Exceptions-only toggle.
        # Exceptions and Changed Profiles are AND filters. Masks/Grayscale and
        # Color/RGB form a visual-content group, while Has Transparency is an
        # independent channel-property filter.
        self.manager_filter_vars = {
            "exceptions": tk.BooleanVar(value=False),
            "changed_profiles": tk.BooleanVar(value=False),
            "missing_output": tk.BooleanVar(value=False),
            "existing_output": tk.BooleanVar(value=False),
            "orphaned_output": tk.BooleanVar(value=False),
            "mask_grayscale": tk.BooleanVar(value=False),
            "color_rgb": tk.BooleanVar(value=False),
            "has_transparency": tk.BooleanVar(value=False),
            "quarantined": tk.BooleanVar(value=False),
        }
        self.manager_filter_button_var = tk.StringVar(value="Filter")
        self.manager_filter_button = ttk.Menubutton(
            row, textvariable=self.manager_filter_button_var, style="Compact.TButton"
        )
        self.manager_filter_menu = tk.Menu(
            self.manager_filter_button, tearoff=False,
            bg="#101a25", fg="#e4edf4", activebackground="#17443f",
            activeforeground="#ffffff", selectcolor="#1fd1bd", bd=0
        )
        self.manager_filter_menu.add_checkbutton(
            label="Exceptions", variable=self.manager_filter_vars["exceptions"],
            command=self._manager_filter_changed
        )
        self.manager_filter_menu.add_checkbutton(
            label="Changed profiles", variable=self.manager_filter_vars["changed_profiles"],
            command=self._manager_filter_changed
        )
        self.manager_filter_menu.add_separator()
        self.manager_filter_menu.add_checkbutton(
            label="Missing output", variable=self.manager_filter_vars["missing_output"],
            command=self._manager_filter_changed
        )
        self.manager_filter_menu.add_checkbutton(
            label="Existing output", variable=self.manager_filter_vars["existing_output"],
            command=self._manager_filter_changed
        )
        self.manager_filter_menu.add_checkbutton(
            label="Orphaned output", variable=self.manager_filter_vars["orphaned_output"],
            command=self._manager_filter_changed
        )
        self.manager_filter_menu.add_separator()
        self.manager_filter_menu.add_checkbutton(
            label="Masks / Grayscale", variable=self.manager_filter_vars["mask_grayscale"],
            command=self._manager_filter_changed
        )
        self.manager_filter_menu.add_checkbutton(
            label="Color / RGB", variable=self.manager_filter_vars["color_rgb"],
            command=self._manager_filter_changed
        )
        self.manager_filter_menu.add_separator()
        self.manager_filter_menu.add_checkbutton(
            label="Has Transparency", variable=self.manager_filter_vars["has_transparency"],
            command=self._manager_filter_changed
        )
        self.manager_filter_menu.add_separator()
        self.manager_filter_menu.add_checkbutton(
            label="Quarantined", variable=self.manager_filter_vars["quarantined"],
            command=self._manager_filter_changed
        )
        self.manager_filter_menu.add_separator()
        self.manager_filter_menu.add_command(label="Clear filters", command=self._manager_clear_filters)
        self.manager_filter_button.configure(menu=self.manager_filter_menu)
        self.manager_filter_button.pack(side="left", padx=6)
        HoverTooltip(
            self.manager_filter_button,
            "Filter active textures by exceptions, output status, visual type or transparency. Orphaned output and Quarantined switch to separate viewers and never mix moved files into active dumps."
        )

        self._manager_sort_group_loading = True
        self.manager_sort_var = tk.StringVar(value=manager_sort_label(self.cfg.get("manager_sort_by", "modified_newest")))
        self.manager_group_var = tk.StringVar(value=manager_group_label(self.cfg.get("manager_group_by", "none")))
        tk.Label(row, text="Sort", bg="#0d151f", fg="#8194a7",
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(8, 3))
        self.manager_sort_combo = ttk.Combobox(
            row, textvariable=self.manager_sort_var, values=tuple(label for _code, label in MANAGER_SORT_OPTIONS),
            state="readonly", width=18
        )
        self.manager_sort_combo.pack(side="left", padx=(0, 5))
        tk.Label(row, text="Group", bg="#0d151f", fg="#8194a7",
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(4, 3))
        self.manager_group_combo = ttk.Combobox(
            row, textvariable=self.manager_group_var, values=tuple(label for _code, label in MANAGER_GROUP_OPTIONS),
            state="readonly", width=15
        )
        self.manager_group_combo.pack(side="left", padx=(0, 6))
        self._manager_sort_group_loading = False
        self.manager_sort_var.trace_add("write", self._manager_sort_group_changed)
        self.manager_group_var.trace_add("write", self._manager_sort_group_changed)
        HoverTooltip(
            self.manager_sort_combo,
            "Sort the visible Texture Manager list without changing files or processing state."
        )
        HoverTooltip(
            self.manager_group_combo,
            "Group visible rows by status, type, alpha, resolution, mode override, file size or quarantine reason."
        )

        ttk.Button(row, text="Refresh", style="Secondary.TButton", command=lambda: self._manager_refresh_async(force=True)).pack(side="left", padx=3)
        ttk.Button(row, text="Open Input", style="Compact.TButton", command=lambda: self.open_cfg_folder("dump_folder")).pack(side="left", padx=3)
        ttk.Button(row, text="Open Output", style="Compact.TButton", command=lambda: self.open_cfg_folder("load_folder")).pack(side="left", padx=3)

        # Adjustable split: texture names on the left, previews/actions on the right.
        main = tk.PanedWindow(
            page, orient="horizontal", bg="#0b1119", bd=0,
            sashwidth=8, sashrelief="flat", opaqueresize=True
        )
        main.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.manager_main_panes = main

        left_host = tk.Frame(main, bg="#0b1119", width=430)
        right_host = tk.Frame(main, bg="#0b1119")
        main.add(left_host, minsize=300, width=430)
        main.add(right_host, minsize=570, stretch="always")

        list_outer, list_panel = self._dashboard_panel(left_host, "Textures", padx=10, pady=9)
        list_outer.pack(fill="both", expand=True)
        list_head = tk.Frame(list_panel, bg="#0d151f")
        list_head.pack(fill="x", padx=10, pady=(0, 7))
        self.manager_count_var = tk.StringVar(value="0 textures")
        tk.Label(list_head, textvariable=self.manager_count_var, bg="#0d151f", fg="#8ea2b3",
                 font=("Segoe UI", 8), anchor="w").pack(side="left", fill="x", expand=True)
        tk.Label(list_head, text="Ctrl/Shift: multi-select", bg="#0d151f", fg="#65798b",
                 font=("Segoe UI", 8)).pack(side="right")
        list_frame = tk.Frame(list_panel, bg="#070c12")
        list_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.manager_list = tk.Listbox(
            list_frame, selectmode="extended", exportselection=False,
            bg="#070c12", fg="#d7e0e8", selectbackground="#0f766e",
            selectforeground="#ffffff", relief="flat", bd=0,
            activestyle="none", font=("Consolas", 9)
        )
        list_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.manager_list.yview)
        self.manager_list.configure(yscrollcommand=list_scroll.set)
        list_scroll.pack(side="right", fill="y")
        self.manager_list.pack(side="left", fill="both", expand=True)
        self.manager_list.bind("<<ListboxSelect>>", self._manager_on_select)

        # Adjustable vertical split on the right lets the user give more room to previews or actions.
        right_panes = tk.PanedWindow(
            right_host, orient="vertical", bg="#0b1119", bd=0,
            sashwidth=8, sashrelief="flat", opaqueresize=True
        )
        right_panes.pack(fill="both", expand=True)
        self.manager_right_panes = right_panes
        preview_host = tk.Frame(right_panes, bg="#0b1119", height=405)
        actions_host = tk.Frame(right_panes, bg="#0b1119", height=285)
        right_panes.add(preview_host, minsize=280, height=405, stretch="always")
        right_panes.add(actions_host, minsize=245, height=285)

        preview_outer, preview = self._dashboard_panel(preview_host, "Selected texture", padx=10, pady=9)
        preview_outer.pack(fill="both", expand=True)
        info_row = tk.Frame(preview, bg="#0d151f")
        info_row.pack(fill="x", padx=10, pady=(0, 7))
        self.manager_info_var = tk.StringVar(value="Select a texture to inspect it.")
        tk.Label(info_row, textvariable=self.manager_info_var, bg="#0d151f", fg="#aab9c5",
                 font=("Segoe UI", 8), anchor="w").pack(side="left", fill="x", expand=True)
        ttk.Button(
            info_row, text="Compare modes", style="Accent.TButton",
            command=self._manager_compare_modes
        ).pack(side="right", padx=(8, 0))
        self.manager_preset_var = tk.StringVar(value=TEXTURE_PRESET_INHERIT)
        self.manager_preset_combo = ttk.Combobox(
            info_row, textvariable=self.manager_preset_var,
            values=(TEXTURE_PRESET_INHERIT,) + workflow_profile_names(), state="readonly", width=21
        )
        self.manager_preset_combo.pack(side="right", padx=(8, 0))
        tk.Label(info_row, text="Mode", bg="#0d151f", fg="#8194a7",
                 font=("Segoe UI", 8, "bold")).pack(side="right")

        preview_grid = tk.Frame(preview, bg="#0d151f")
        preview_grid.pack(fill="both", expand=True, padx=10, pady=(0, 7))
        preview_grid.grid_columnconfigure(0, weight=1, uniform="manager_preview")
        preview_grid.grid_columnconfigure(1, weight=1, uniform="manager_preview")
        preview_grid.grid_rowconfigure(1, weight=1)
        tk.Label(preview_grid, text="DUMP / BEFORE", bg="#0d151f", fg="#f0f5f8",
                 font=("Segoe UI", 9, "bold")).grid(row=0, column=0, pady=(0, 4))
        tk.Label(preview_grid, text="LOAD / AFTER", bg="#0d151f", fg="#f0f5f8",
                 font=("Segoe UI", 9, "bold")).grid(row=0, column=1, pady=(0, 4))
        self.manager_dump_canvas = tk.Canvas(preview_grid, bg="#05080c", highlightthickness=1, highlightbackground="#273747")
        self.manager_dump_canvas.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        self.manager_load_canvas = tk.Canvas(preview_grid, bg="#05080c", highlightthickness=1, highlightbackground="#273747")
        self.manager_load_canvas.grid(row=1, column=1, sticky="nsew", padx=(5, 0))
        self._manager_draw_canvas(self.manager_dump_canvas, None, "No texture selected")
        self._manager_draw_canvas(self.manager_load_canvas, None, "No output")
        self.manager_dump_canvas.bind("<Configure>", self._manager_schedule_preview_resize)
        self.manager_load_canvas.bind("<Configure>", self._manager_schedule_preview_resize)

        self.manager_preset_status_var = tk.StringVar(value="Game default: Clean Heart")
        tk.Label(preview, textvariable=self.manager_preset_status_var, bg="#0b121b", fg="#65d8cd",
                 font=("Segoe UI", 8, "bold"), anchor="w", padx=10, pady=7).pack(fill="x", padx=10, pady=(0, 9))

        actions_outer, actions = self._dashboard_panel(actions_host, "Actions", padx=10, pady=8)
        actions_outer.pack(fill="both", expand=True)
        grid = tk.Frame(actions, bg="#0d151f")
        grid.pack(fill="both", expand=True, padx=10, pady=(0, 9))
        for col in range(4):
            grid.grid_columnconfigure(col, weight=1, uniform="manager_actions")
        labels = [(0, "PROCESSING"), (2, "ORGANIZATION"), (4, "FILES & CLEANUP")]
        for row_index, label in labels:
            tk.Label(grid, text=label, bg="#0d151f", fg="#72879a",
                     font=("Segoe UI", 8, "bold"), anchor="w").grid(
                         row=row_index, column=0, columnspan=4, sticky="ew", pady=(4 if row_index else 0, 3)
                     )
        buttons = [
            (1, 0, "Apply Selected Mode", "Accent.TButton", lambda: self._manager_apply_preset(clear=False)),
            (1, 1, "Recreate with Mode", "Secondary.TButton", lambda: self._manager_recreate_selected(apply_mode=True)),
            (1, 2, "Recreate Selected", "Secondary.TButton", self._manager_recreate_selected),
            (1, 3, "Delete Output", "Danger.TButton", self._manager_delete_load),
            (3, 0, "Add Exception", "Compact.TButton", self._manager_add_exception),
            (3, 1, "Remove Exception", "Compact.TButton", self._manager_remove_exception),
            (3, 2, "Clear Mode Override", "Compact.TButton", lambda: self._manager_apply_preset(clear=True)),
            (3, 3, "Refresh List", "Compact.TButton", lambda: self._manager_refresh_async(force=True)),
            (5, 0, "Open Dump Folder", "Compact.TButton", self._manager_open_dump_folder),
            (5, 1, "Open Output Folder", "Compact.TButton", self._manager_open_load_folder),
            (5, 2, "Quarantine EFB + Cutscenes", "Secondary.TButton", lambda: self.quarantine_efb_cutscene_dumps(refresh_callback=lambda: self._manager_refresh_async(force=True))),
            (5, 3, "Safe Blank Cleanup", "Compact.TButton", lambda: self.mass_delete_cutscene_black_dumps(refresh_callback=lambda: self._manager_refresh_async(force=True))),
            (6, 0, "Restore Selected", "Accent.TButton", self._manager_restore_selected),
            (6, 1, "Restore All Quarantined", "Secondary.TButton", self._manager_restore_all),
            (6, 2, "Open Quarantine", "Compact.TButton", self._manager_open_quarantine_folder),
            (6, 3, "Clear Search", "Compact.TButton", lambda: self.manager_search_var.set("")),
        ]
        for r, c, label, style, command in buttons:
            ttk.Button(grid, text=label, style=style, command=command).grid(row=r, column=c, sticky="ew", padx=3, pady=3)

        self.manager_all_files = []
        self.manager_quarantined_files = []
        self.manager_orphaned_outputs = []
        self.manager_quarantine_metadata = {}
        self.manager_visible_files = []
        self.manager_visible_entries = []
        self.manager_output_status = {}
        self.manager_visual_status = {}
        self.manager_transparency_status = {}
        self.manager_file_stats = {}
        self.manager_filter_generation = 0
        self.manager_scan_generation = 0
        self.manager_scan_running = False
        self.manager_last_context = None
        self.manager_dump_pil = None
        self.manager_load_pil = None
        self.manager_dump_photo = None
        self.manager_load_photo = None
        self.manager_preview_generation = 0
        self.manager_preview_jobs = 0
        self.manager_preview_resize_after = None
        self.manager_async_q = queue.Queue()
        self.manager_async_poll_after = None
        self.manager_compare_window = None
        # Live Texture Manager refresh state. The manager index is now kept
        # in sync with watcher/batch activity instead of requiring manual
        # Refresh List presses after new dumps, outputs, quarantine moves or
        # cleanup actions. Refreshes are throttled and reuse cached metadata
        # for unchanged files so large packs stay responsive.
        self.manager_auto_refresh_after = None
        self.manager_dirty = False
        self.manager_last_auto_refresh_at = 0.0
        self.manager_last_dirty_at = 0.0
        self.manager_search_var.trace_add("write", lambda *_: self._manager_apply_filter())
        self.after(1800, self._manager_auto_refresh_tick)

    def open_texture_manager(self):
        """Open the integrated Texture Manager tab instead of a separate Toplevel window."""
        try:
            self.tabs.select(self.page_manager)
            self.after_idle(self._manager_ensure_context)
        except Exception as exc:
            messagebox.showerror("Texture Manager", str(exc))

    def _manager_context(self, show_errors=False):
        try:
            cfg = self.configure_profile_runtime_paths(self.collect())
            dump_text = str(cfg.get("dump_folder", "") or "").strip()
            load_text = str(cfg.get("load_folder", "") or "").strip()
            if not dump_text or not load_text:
                raise ValueError("Configure both Dump and Load folders in Profiles first.")
            dump_folder = Path(dump_text)
            load_folder = Path(load_text)
            if not dump_folder.exists():
                raise ValueError("The configured Dump folder does not exist.")
            profile_dir = PROFILES_DIR / safe_profile_name(self.current_profile_name)
            profile_dir.mkdir(parents=True, exist_ok=True)
            return cfg, dump_folder, load_folder, profile_dir
        except Exception as exc:
            if show_errors:
                messagebox.showerror("Texture Manager", str(exc))
            if hasattr(self, "manager_scan_badge_var"):
                self.manager_scan_badge_var.set(str(exc))
            return None

    def _manager_ensure_context(self, force=False):
        ctx = self._manager_context(show_errors=False)
        if not ctx:
            return
        cfg, dump_folder, load_folder, profile_dir = ctx
        context_key = (self.current_profile_name, str(dump_folder), str(load_folder))
        self.manager_profile_label_var.set(f"Profile: {self.profile_display_label(self.current_profile_name) if self.current_profile_name else '—'}")
        context_changed = context_key != self.manager_last_context
        if context_changed:
            self.manager_last_context = context_key
            self._manager_set_sort_group_vars_from_cfg(self.cfg)
            self._manager_refresh_async(force=True)
        elif force:
            self._manager_refresh_async(force=True)
        elif not self.manager_all_files and not self.manager_scan_running:
            self._manager_refresh_async(force=False)

    def _manager_set_sort_group_vars_from_cfg(self, settings=None):
        if not hasattr(self, "manager_sort_var") or not hasattr(self, "manager_group_var"):
            return
        settings = settings or getattr(self, "cfg", {}) or {}
        self._manager_sort_group_loading = True
        try:
            self.manager_sort_var.set(manager_sort_label(settings.get("manager_sort_by", "modified_newest")))
            self.manager_group_var.set(manager_group_label(settings.get("manager_group_by", "none")))
        finally:
            self._manager_sort_group_loading = False

    def _manager_sort_group_changed(self, *_args):
        if getattr(self, "_manager_sort_group_loading", False):
            return
        sort_code = manager_sort_code(self.manager_sort_var.get() if hasattr(self, "manager_sort_var") else "modified_newest")
        group_code = manager_group_code(self.manager_group_var.get() if hasattr(self, "manager_group_var") else "none")
        try:
            self.cfg["manager_sort_by"] = sort_code
            self.cfg["manager_group_by"] = group_code
        except Exception:
            pass
        try:
            if self.current_profile_name in self.profile_data.get("profiles", {}):
                settings = self.profile_data["profiles"][self.current_profile_name].setdefault("settings", {})
                settings["manager_sort_by"] = sort_code
                settings["manager_group_by"] = group_code
                save_profiles_data(self.profile_data)
        except Exception:
            pass
        try:
            cfg = dict(getattr(self, "cfg", {}) or {})
            cfg["manager_sort_by"] = sort_code
            cfg["manager_group_by"] = group_code
            save_config(cfg)
        except Exception:
            pass
        self._manager_apply_filter()

    def _manager_mark_dirty(self, delay_ms=900):
        """Mark the Texture Manager index stale and schedule a throttled refresh.

        This is intentionally lightweight: it only schedules a refresh if the
        Texture Manager tab has been built and a valid profile context exists.
        The real scan still runs on a background thread.
        """
        if not hasattr(self, "manager_search_var"):
            return
        self.manager_dirty = True
        self.manager_last_dirty_at = time.time()
        if getattr(self, "manager_auto_refresh_after", None) is not None:
            return
        try:
            self.manager_auto_refresh_after = self.after(delay_ms, self._manager_auto_refresh_tick)
        except Exception:
            self.manager_auto_refresh_after = None

    def _manager_tab_is_visible(self):
        try:
            return str(self.tabs.select()) == str(self.page_manager)
        except Exception:
            return False

    def _manager_auto_refresh_tick(self):
        """Keep Texture Manager counts and lists synchronized during watching.

        Manual Refresh is still available, but live dumping/output creation now
        invalidates the index automatically. The ticker refreshes only when the
        manager tab is visible or a worker/batch session is active, and never
        starts a second scan while one is already running.
        """
        self.manager_auto_refresh_after = None
        if not hasattr(self, "manager_search_var"):
            return
        try:
            watching = bool(getattr(self, "worker_thread", None) and self.worker_thread.is_alive())
        except Exception:
            watching = False
        active = watching or bool(getattr(self, "batch_active", False))
        visible = self._manager_tab_is_visible()
        if not visible and not active:
            return
        now = time.time()
        min_interval = 2.5 if visible else 5.0
        due_to_dirty = bool(getattr(self, "manager_dirty", False))
        periodic_due = active and (now - float(getattr(self, "manager_last_auto_refresh_at", 0.0) or 0.0) >= 6.0)
        if (due_to_dirty or periodic_due) and not getattr(self, "manager_scan_running", False):
            try:
                self.manager_dirty = False
                self.manager_last_auto_refresh_at = now
                self._manager_refresh_async(force=False)
            except Exception:
                pass
        # Keep a low-frequency poll running while the manager is visible or a
        # watcher/batch is active, because some emulator dumps may appear without
        # a log line being emitted by the worker yet.
        try:
            self.manager_auto_refresh_after = self.after(int(min_interval * 1000), self._manager_auto_refresh_tick)
        except Exception:
            self.manager_auto_refresh_after = None

    def _manager_refresh_async(self, force=False):
        ctx = self._manager_context(show_errors=force)
        if not ctx:
            return
        cfg, dump_folder, load_folder, profile_dir = ctx
        if self.manager_scan_running and not force:
            return
        self.manager_scan_generation += 1
        generation = self.manager_scan_generation
        self.manager_scan_running = True
        self.manager_scan_badge_var.set("Scanning textures…")
        self.manager_scan_badge.configure(bg="#2a2517", fg="#f0c56b")
        process_tmp = bool(cfg.get("process_tmp_image_files", True))
        previous_file_stats = dict(getattr(self, "manager_file_stats", {}) or {})
        previous_visual_status = dict(getattr(self, "manager_visual_status", {}) or {})
        previous_transparency_status = dict(getattr(self, "manager_transparency_status", {}) or {})

        def scan_worker():
            items = []
            output_status = {}
            visual_status = {}
            transparency_status = {}
            file_stats = {}
            quarantine_items = []
            quarantine_metadata = {}
            expected_output_paths = set()
            orphan_items = []
            try:
                for p in dump_folder.rglob("*"):
                    if not p.is_file() or is_inside_alpha_output_folder(p, dump_folder):
                        continue
                    if not is_image_like(p, process_tmp):
                        continue
                    try:
                        stat = p.stat()
                        stamp = stat.st_mtime
                        size_bytes = stat.st_size
                    except Exception:
                        stamp = 0
                        size_bytes = 0
                    key = str(p)
                    cached = previous_file_stats.get(key)
                    reuse_metadata = (
                        isinstance(cached, dict) and
                        cached.get("mtime") == stamp and
                        cached.get("size") == size_bytes and
                        cached.get("dimensions") is not None
                    )
                    if reuse_metadata:
                        file_stats[key] = dict(cached)
                        visual_status[key] = bool(previous_visual_status.get(key, False))
                        transparency_status[key] = bool(previous_transparency_status.get(key, False))
                    else:
                        file_stats[key] = {"mtime": stamp, "size": size_bytes, "dimensions": None, "pixels": 0}
                    items.append((stamp, p))
                    try:
                        expected_out = routed_output_path_for_input(p, dump_folder, load_folder, cfg)
                        output_status[key] = expected_out.exists()
                        expected_output_paths.add(str(expected_out.resolve()).casefold())
                    except Exception:
                        output_status[key] = False
                    # Visual-content and transparency classifications are calculated
                    # off the UI thread and cached. Grayscale classification ignores
                    # transparent background pixels so colored cutouts remain Color/RGB.
                    if not reuse_metadata:
                        try:
                            classification = classify_texture_visual_type(p)
                            visual_status[key] = bool(classification.get("mask_grayscale", False))
                            transparency_status[key] = bool(classification.get("has_transparency", False))
                            file_stats[key]["dimensions"] = classification.get("dimensions")
                            file_stats[key]["pixels"] = int(classification.get("pixels") or 0)
                        except Exception:
                            visual_status[key] = False
                            transparency_status[key] = False
                items.sort(key=lambda item: item[0], reverse=True)
                result = [p for _stamp, p in items]

                # Orphaned outputs are replacement/load files that no longer have
                # a matching dump texture in the active Dump folder. They are a
                # separate Texture Manager source: useful for pack cleanup, but
                # never mixed with active dumps or queued for processing.
                if load_folder.exists():
                    for out_path in load_folder.rglob("*"):
                        if not out_path.is_file() or is_inside_alpha_output_folder(out_path, load_folder):
                            continue
                        # Some emulators keep their dump folder inside the
                        # replacement/load root. PPSSPP is the common case:
                        #   PSP/TEXTURES/<GAME>/new      -> dumps
                        #   PSP/TEXTURES/<GAME>          -> replacements
                        # When scanning the load root for orphaned outputs, never
                        # treat files inside the active dump folder as outputs.
                        # Otherwise every PPSSPP dump appears as an orphaned
                        # replacement even though the original file exists.
                        if is_path_within(out_path, dump_folder):
                            continue
                        if not is_image_like(out_path, process_tmp):
                            continue
                        try:
                            resolved_key = str(out_path.resolve()).casefold()
                        except Exception:
                            resolved_key = str(out_path).casefold()
                        if resolved_key in expected_output_paths:
                            continue
                        try:
                            stat = out_path.stat()
                            ostamp = stat.st_mtime
                            osize = stat.st_size
                        except Exception:
                            ostamp = 0
                            osize = 0
                        key = str(out_path)
                        cached = previous_file_stats.get(key)
                        reuse_metadata = (
                            isinstance(cached, dict) and
                            cached.get("mtime") == ostamp and
                            cached.get("size") == osize and
                            cached.get("dimensions") is not None
                        )
                        if reuse_metadata:
                            file_stats[key] = dict(cached)
                            visual_status[key] = bool(previous_visual_status.get(key, False))
                            transparency_status[key] = bool(previous_transparency_status.get(key, False))
                        else:
                            file_stats[key] = {"mtime": ostamp, "size": osize, "dimensions": None, "pixels": 0}
                        orphan_items.append((ostamp, out_path))
                        if not reuse_metadata:
                            try:
                                classification = classify_texture_visual_type(out_path)
                                visual_status[key] = bool(classification.get("mask_grayscale", False))
                                transparency_status[key] = bool(classification.get("has_transparency", False))
                                file_stats[key]["dimensions"] = classification.get("dimensions")
                                file_stats[key]["pixels"] = int(classification.get("pixels") or 0)
                            except Exception:
                                visual_status[key] = False
                                transparency_status[key] = False

                for stamp, qpath, metadata in iter_quarantined_images(
                    profile_dir, dump_folder, process_tmp
                ):
                    try:
                        qstat = qpath.stat()
                        qsize = qstat.st_size
                        qmtime = qstat.st_mtime
                    except Exception:
                        qsize = 0
                        qmtime = stamp
                    key = str(qpath)
                    cached = previous_file_stats.get(key)
                    reuse_metadata = (
                        isinstance(cached, dict) and
                        cached.get("mtime") == qmtime and
                        cached.get("size") == qsize and
                        cached.get("dimensions") is not None
                    )
                    if reuse_metadata:
                        file_stats[key] = dict(cached)
                        visual_status[key] = bool(previous_visual_status.get(key, False))
                        transparency_status[key] = bool(previous_transparency_status.get(key, False))
                    else:
                        file_stats[key] = {"mtime": qmtime, "size": qsize, "dimensions": None, "pixels": 0}
                    quarantine_items.append((stamp, qpath))
                    quarantine_metadata[key] = metadata
                    if not reuse_metadata:
                        try:
                            classification = classify_texture_visual_type(qpath)
                            visual_status[key] = bool(classification.get("mask_grayscale", False))
                            transparency_status[key] = bool(classification.get("has_transparency", False))
                            file_stats[key]["dimensions"] = classification.get("dimensions")
                            file_stats[key]["pixels"] = int(classification.get("pixels") or 0)
                        except Exception:
                            visual_status[key] = False
                            transparency_status[key] = False
                orphan_items.sort(key=lambda item: item[0], reverse=True)
                orphaned_result = [p for _stamp, p in orphan_items]
                quarantined_result = [p for _stamp, p in quarantine_items]
                error = None
            except Exception as exc:
                result = []
                output_status = {}
                visual_status = {}
                transparency_status = {}
                file_stats = {}
                quarantined_result = []
                orphaned_result = []
                quarantine_metadata = {}
                error = str(exc)
            self.manager_async_q.put((
                "scan", generation, result, quarantined_result, orphaned_result, quarantine_metadata,
                output_status, visual_status, transparency_status, file_stats, error
            ))

        self._manager_start_async_poll()
        threading.Thread(target=scan_worker, daemon=True, name="TextureManagerScan").start()

    def _manager_start_async_poll(self):
        if self.manager_async_poll_after is None:
            self.manager_async_poll_after = self.after(50, self._manager_poll_async)

    def _manager_poll_async(self):
        self.manager_async_poll_after = None
        handled = 0
        while handled < 20:
            try:
                item = self.manager_async_q.get_nowait()
            except queue.Empty:
                break
            handled += 1
            kind = item[0]
            if kind == "scan":
                (_kind, generation, files, quarantined_files, orphaned_outputs, quarantine_metadata,
                 output_status, visual_status, transparency_status, file_stats, error) = item
                self._manager_finish_scan(
                    generation, files, quarantined_files, orphaned_outputs, quarantine_metadata,
                    output_status, visual_status, transparency_status, file_stats, error
                )
            elif kind == "preview":
                _kind, generation, dump_img, load_img, dump_dims, load_dims = item
                self.manager_preview_jobs = max(0, self.manager_preview_jobs - 1)
                self._manager_finish_preview(generation, dump_img, load_img, dump_dims, load_dims)
        if self.manager_scan_running or self.manager_preview_jobs or not self.manager_async_q.empty():
            self.manager_async_poll_after = self.after(50, self._manager_poll_async)

    def _manager_finish_scan(
        self, generation, files, quarantined_files, orphaned_outputs, quarantine_metadata,
        output_status, visual_status, transparency_status, file_stats=None, error=None
    ):
        if generation != self.manager_scan_generation:
            return
        self.manager_scan_running = False
        if error:
            self.manager_scan_badge_var.set("Scan failed")
            self.manager_scan_badge.configure(bg="#3b2024", fg="#ff9ba3")
            self.log(f"Texture Manager scan failed: {error}")
            return
        self.manager_all_files = files
        self.manager_quarantined_files = list(quarantined_files or [])
        self.manager_orphaned_outputs = list(orphaned_outputs or [])
        self.manager_quarantine_metadata = dict(quarantine_metadata or {})
        self.manager_output_status = dict(output_status or {})
        self.manager_visual_status = dict(visual_status or {})
        self.manager_transparency_status = dict(transparency_status or {})
        self.manager_file_stats = dict(file_stats or {})
        mask_count = sum(1 for value in self.manager_visual_status.values() if value)
        transparent_count = sum(1 for value in self.manager_transparency_status.values() if value)
        existing_count = sum(1 for path in files if self.manager_output_status.get(str(path), False))
        missing_count = max(0, len(files) - existing_count)
        self.manager_scan_badge_var.set(
            f"{len(files):,} active  •  {existing_count:,} existing  •  {missing_count:,} missing  •  "
            f"{len(self.manager_orphaned_outputs):,} orphaned  •  {len(self.manager_quarantined_files):,} quarantined"
        )
        self.manager_scan_badge.configure(bg="#123832", fg="#7ff0df")
        self._manager_apply_filter()

    def _manager_filter_changed(self):
        # Quarantined and Orphaned are separate data sources, not attributes of
        # active dump textures. Keep them exclusive so moved/stale files can
        # never be mixed with active queueable dumps.
        if getattr(self, "_manager_filter_changing", False):
            return
        self._manager_filter_changing = True
        try:
            filter_vars = getattr(self, "manager_filter_vars", {})
            quarantined = filter_vars.get("quarantined")
            orphaned = filter_vars.get("orphaned_output")
            if quarantined is not None and quarantined.get():
                for key, var in filter_vars.items():
                    if key != "quarantined":
                        var.set(False)
            elif orphaned is not None and orphaned.get():
                for key, var in filter_vars.items():
                    if key not in {"orphaned_output", "mask_grayscale", "color_rgb", "has_transparency"}:
                        var.set(False)
        finally:
            self._manager_filter_changing = False
        self._manager_update_filter_button()
        self._manager_apply_filter()

    def _manager_clear_filters(self):
        for var in getattr(self, "manager_filter_vars", {}).values():
            var.set(False)
        self._manager_update_filter_button()
        self._manager_apply_filter()

    def _manager_update_filter_button(self):
        if not hasattr(self, "manager_filter_button_var"):
            return
        labels = []
        mapping = (
            ("exceptions", "Exceptions"),
            ("changed_profiles", "Changed"),
            ("missing_output", "Missing"),
            ("existing_output", "Existing"),
            ("orphaned_output", "Orphaned"),
            ("mask_grayscale", "Masks/Gray"),
            ("color_rgb", "Color/RGB"),
            ("has_transparency", "Transparency"),
            ("quarantined", "Quarantined"),
        )
        for key, label in mapping:
            var = self.manager_filter_vars.get(key)
            if var is not None and var.get():
                labels.append(label)
        if not labels:
            self.manager_filter_button_var.set("Filter")
        elif len(labels) == 1:
            self.manager_filter_button_var.set(f"Filter: {labels[0]}")
        else:
            self.manager_filter_button_var.set(f"Filter ({len(labels)})")

    def _manager_toggle_exceptions(self):
        # Compatibility helper for older keyboard bindings/plugins.
        var = getattr(self, "manager_filter_vars", {}).get("exceptions")
        if var is not None:
            var.set(not var.get())
            self._manager_filter_changed()

    def _manager_apply_filter(self):
        if not hasattr(self, "manager_list"):
            return
        self.manager_filter_generation += 1
        generation = self.manager_filter_generation
        query = self.manager_search_var.get().strip().casefold()
        exception_patterns = load_exception_patterns()
        filter_vars = getattr(self, "manager_filter_vars", {})
        filter_exceptions = bool(filter_vars.get("exceptions") and filter_vars["exceptions"].get())
        filter_changed = bool(filter_vars.get("changed_profiles") and filter_vars["changed_profiles"].get())
        filter_missing = bool(filter_vars.get("missing_output") and filter_vars["missing_output"].get())
        filter_existing = bool(filter_vars.get("existing_output") and filter_vars["existing_output"].get())
        filter_orphaned = bool(filter_vars.get("orphaned_output") and filter_vars["orphaned_output"].get())
        filter_masks = bool(filter_vars.get("mask_grayscale") and filter_vars["mask_grayscale"].get())
        filter_color = bool(filter_vars.get("color_rgb") and filter_vars["color_rgb"].get())
        filter_transparency = bool(filter_vars.get("has_transparency") and filter_vars["has_transparency"].get())
        filter_quarantined = bool(filter_vars.get("quarantined") and filter_vars["quarantined"].get())
        sort_code = manager_sort_code(self.manager_sort_var.get() if hasattr(self, "manager_sort_var") else self.cfg.get("manager_sort_by", "modified_newest"))
        group_code = manager_group_code(self.manager_group_var.get() if hasattr(self, "manager_group_var") else self.cfg.get("manager_group_by", "none"))

        ctx = self._manager_context(show_errors=False)
        dump_folder = None
        override_keys = set()
        if ctx:
            cfg, dump_folder, _load_folder, profile_dir = ctx
            try:
                override_keys = set(load_texture_preset_overrides(profile_dir).keys())
            except Exception:
                override_keys = set()

        def exception_flag(path):
            name, stem, rel = path.name, path.stem, str(path).replace("\\", "/")
            for pattern in exception_patterns:
                pat = pattern.replace("\\", "/")
                if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(stem, pat) or fnmatch.fnmatch(rel, pat):
                    return True
            return False

        def file_stat(path):
            meta = dict(self.manager_file_stats.get(str(path), {}) or {})
            if "mtime" not in meta or "size" not in meta:
                try:
                    st = path.stat()
                    meta.setdefault("mtime", st.st_mtime)
                    meta.setdefault("size", st.st_size)
                except Exception:
                    meta.setdefault("mtime", 0)
                    meta.setdefault("size", 0)
            dims = meta.get("dimensions")
            if isinstance(dims, (list, tuple)) and len(dims) >= 2:
                try:
                    w, h = int(dims[0]), int(dims[1])
                    meta["dimensions"] = (w, h)
                    meta["pixels"] = int(meta.get("pixels") or (w * h))
                except Exception:
                    meta["dimensions"] = None
                    meta["pixels"] = int(meta.get("pixels") or 0)
            else:
                meta["dimensions"] = None
                meta["pixels"] = int(meta.get("pixels") or 0)
            return meta

        records = []
        if filter_quarantined:
            source_files = self.manager_quarantined_files
        elif filter_orphaned:
            source_files = getattr(self, "manager_orphaned_outputs", [])
        else:
            source_files = self.manager_all_files
        for p in source_files:
            haystack = f"{p.name} {p}".casefold()
            if query and query not in haystack:
                continue
            is_exc = exception_flag(p)
            is_mask = bool(self.manager_visual_status.get(str(p), False))
            is_transparent = bool(self.manager_transparency_status.get(str(p), False))
            changed = bool((not filter_quarantined) and (not filter_orphaned) and dump_folder and texture_override_key(p, dump_folder) in override_keys)
            quarantine_meta = self.manager_quarantine_metadata.get(str(p), {}) if filter_quarantined else {}
            output_ready = True if filter_orphaned else bool(self.manager_output_status.get(str(p), False))

            if filter_quarantined or filter_orphaned:
                is_exc = False
            if filter_exceptions and not is_exc:
                continue
            if filter_changed and not changed:
                continue
            # Missing/Existing are an OR category for active dump textures.
            # Selecting neither or both shows both; selecting exactly one isolates
            # missing outputs or already-created outputs.
            if (not filter_quarantined) and (not filter_orphaned):
                if filter_missing and not filter_existing and output_ready:
                    continue
                if filter_existing and not filter_missing and not output_ready:
                    continue
            # Masks/Grayscale and Color/RGB are an OR category. Selecting neither
            # or both shows both; selecting exactly one isolates that visual type.
            if filter_masks and not filter_color and not is_mask:
                continue
            if filter_color and not filter_masks and is_mask:
                continue
            if filter_transparency and not is_transparent:
                continue

            meta = file_stat(p)
            category = str(quarantine_meta.get("category", "quarantined")).replace("_", " ").title() if filter_quarantined else ""
            records.append({
                "path": p,
                "name": p.name,
                "name_key": p.name.casefold(),
                "mtime": float(meta.get("mtime") or 0),
                "size": int(meta.get("size") or 0),
                "dimensions": meta.get("dimensions"),
                "pixels": int(meta.get("pixels") or 0),
                "is_exception": bool(is_exc),
                "is_mask": bool(is_mask),
                "is_transparent": bool(is_transparent),
                "changed": bool(changed),
                "output_ready": output_ready,
                "quarantine_meta": quarantine_meta,
                "quarantine_category": category,
                "filter_quarantined": filter_quarantined,
                "filter_orphaned": filter_orphaned,
            })

        def sort_key(rec):
            name = rec["name_key"]
            if sort_code == "modified_oldest":
                return (rec["mtime"], name)
            if sort_code == "name_az":
                return (name,)
            if sort_code == "name_za":
                return tuple([-ord(c) for c in name[:64]]) + (len(name),)
            if sort_code == "resolution_largest":
                return (-rec["pixels"], name)
            if sort_code == "resolution_smallest":
                return (rec["pixels"], name)
            if sort_code == "file_largest":
                return (-rec["size"], name)
            if sort_code == "file_smallest":
                return (rec["size"], name)
            if sort_code == "unprocessed_first":
                return (1 if rec["output_ready"] else 0, name)
            if sort_code == "processed_first":
                return (0 if rec["output_ready"] else 1, name)
            if sort_code == "alpha_first":
                return (0 if rec["is_transparent"] else 1, name)
            if sort_code == "opaque_first":
                return (0 if not rec["is_transparent"] else 1, name)
            if sort_code == "masks_first":
                return (0 if rec["is_mask"] else 1, name)
            if sort_code == "color_first":
                return (0 if not rec["is_mask"] else 1, name)
            if sort_code == "mode_override_first":
                return (0 if rec["changed"] else 1, name)
            if sort_code == "exceptions_first":
                return (0 if rec["is_exception"] else 1, name)
            return (-rec["mtime"], name)

        records.sort(key=sort_key)

        def resolution_label(rec):
            dims = rec.get("dimensions")
            if isinstance(dims, (list, tuple)) and len(dims) >= 2 and dims[0] and dims[1]:
                return f"{int(dims[0])}×{int(dims[1])}"
            return "Unknown resolution"

        def group_label_for(rec):
            if group_code == "status":
                if rec["filter_quarantined"]:
                    return "Quarantined"
                if rec.get("filter_orphaned"):
                    return "Orphaned output"
                return "Existing output" if rec["output_ready"] else "Missing output"
            if group_code == "type":
                return "Masks / Grayscale" if rec["is_mask"] else "Color / RGB"
            if group_code == "alpha":
                return "Has transparency" if rec["is_transparent"] else "Opaque"
            if group_code == "resolution":
                return resolution_label(rec)
            if group_code == "mode":
                if rec["filter_quarantined"]:
                    return "Quarantined"
                if rec.get("filter_orphaned"):
                    return "Orphaned outputs"
                return "Texture mode override" if rec["changed"] else "Game default mode"
            if group_code == "size":
                return _manager_size_bucket(rec["size"])
            if group_code == "quarantine":
                if rec["filter_quarantined"]:
                    return rec["quarantine_category"]
                if rec.get("filter_orphaned"):
                    return "Orphaned outputs"
                return "Active textures"
            return ""

        def format_row(rec):
            p = rec["path"]
            kind = "MASK" if rec["is_mask"] else "COLOR"
            tags = []
            if rec["is_transparent"]:
                tags.append("A")
            if rec["filter_quarantined"]:
                category = (rec["quarantine_category"] or "Quarantined").upper().replace("_", " ")
                dims = resolution_label(rec)
                return f"[QUAR] [{category[:12]:<12}] [{kind:<5}] [{dims:<9}]  {p.name}"
            if rec.get("filter_orphaned"):
                dims = resolution_label(rec)
                return f"[ORPH] [{kind:<5}]{' A' if rec['is_transparent'] else '':<13} [{dims:<9}]  {p.name}"
            status = "DONE" if rec["output_ready"] else "MISS"
            if rec["changed"]:
                tags.append("MODE")
            if rec["is_exception"]:
                tags.append("EXC")
            tag_text = (" " + "/".join(tags)) if tags else ""
            dims = resolution_label(rec)
            return f"[{status}] [{kind:<5}]{tag_text:<13} [{dims:<9}]  {p.name}"

        rows = []
        entries = []
        if group_code == "none":
            for rec in records:
                entries.append(rec["path"])
                rows.append(format_row(rec))
        else:
            grouped = []
            current_label = object()
            for rec in records:
                label = group_label_for(rec)
                if not grouped or grouped[-1][0] != label:
                    grouped.append((label, []))
                grouped[-1][1].append(rec)
            for label, group_records in grouped:
                entries.append(None)
                rows.append(f"──── {label} ({len(group_records):,}) ────")
                for rec in group_records:
                    entries.append(rec["path"])
                    rows.append(format_row(rec))

        max_rows = 10000
        limited_rows = rows[:max_rows]
        limited_entries = entries[:max_rows]
        self.manager_visible_entries = limited_entries
        self.manager_visible_files = [e for e in limited_entries if e is not None]
        shown = len(self.manager_visible_files)
        total = len(source_files)
        suffix = " (first 10,000 rows shown)" if len(rows) > len(limited_rows) else ""
        active_parts = []
        if filter_exceptions:
            active_parts.append("Exceptions")
        if filter_changed:
            active_parts.append("Changed profiles")
        if filter_missing and not filter_existing:
            active_parts.append("Missing output")
        elif filter_existing and not filter_missing:
            active_parts.append("Existing output")
        if filter_orphaned:
            active_parts.append("Orphaned output")
        if filter_masks and not filter_color:
            active_parts.append("Masks / Grayscale")
        elif filter_color and not filter_masks:
            active_parts.append("Color / RGB")
        if filter_transparency:
            active_parts.append("Has Transparency")
        if filter_quarantined:
            active_parts.append("Quarantined viewer")
        active_parts.append(f"Sort: {manager_sort_label(sort_code)}")
        if group_code != "none":
            active_parts.append(f"Group: {manager_group_label(group_code)}")
        filter_suffix = f"  •  {' + '.join(active_parts)}" if active_parts else ""
        self.manager_count_var.set(f"Loading {shown:,} textures…")
        self.manager_list.delete(0, "end")
        if shown == 0:
            self.manager_count_var.set(f"0 shown / {total:,} total{filter_suffix}")
            self._manager_clear_preview(
                "No quarantined files match the current filter." if filter_quarantined
                else ("No orphaned outputs match the current filter." if filter_orphaned else "No textures match the current filter.")
            )
            return
        self._manager_insert_rows(generation, limited_rows, 0, total, suffix + filter_suffix, shown)

    def _manager_insert_rows(self, generation, rows, start, total, suffix, texture_count=None):
        if generation != self.manager_filter_generation:
            return
        end = min(len(rows), start + 350)
        for row in rows[start:end]:
            self.manager_list.insert("end", row)
        self.manager_count_var.set(f"{end:,} / {len(rows):,} rows loaded")
        if end < len(rows):
            self.after(1, lambda: self._manager_insert_rows(generation, rows, end, total, suffix, texture_count))
            return
        shown = texture_count if texture_count is not None else len(rows)
        self.manager_count_var.set(f"{shown:,} shown / {total:,} total{suffix}")
        if self.manager_list.size() and not self.manager_list.curselection():
            first_index = None
            for idx, entry in enumerate(getattr(self, "manager_visible_entries", [])):
                if entry is not None:
                    first_index = idx
                    break
            if first_index is not None:
                self.manager_list.selection_set(first_index)
                self.manager_list.see(first_index)
                self._manager_on_select()

    def _manager_selected_paths(self):
        out = []
        entries = getattr(self, "manager_visible_entries", None)
        if entries is None:
            entries = getattr(self, "manager_visible_files", [])
        for idx in self.manager_list.curselection():
            if 0 <= idx < len(entries):
                entry = entries[idx]
                if entry is not None:
                    out.append(entry)
        return out

    def _manager_on_select(self, _event=None):
        paths = self._manager_selected_paths()
        if not paths:
            return
        p = paths[0]
        ctx = self._manager_context(show_errors=False)
        if not ctx:
            return
        cfg, dump_folder, load_folder, profile_dir = ctx
        quarantine_meta = self.manager_quarantine_metadata.get(str(p))
        multi = f"  •  {len(paths)} selected" if len(paths) > 1 else ""
        texture_kind = "Masks / Grayscale candidate" if self.manager_visual_status.get(str(p), False) else "Color / RGB"
        transparency = "yes" if self.manager_transparency_status.get(str(p), False) else "no"
        if str(p) in {str(x) for x in getattr(self, "manager_orphaned_outputs", [])}:
            self.manager_preset_var.set(TEXTURE_PRESET_INHERIT)
            self.manager_preset_status_var.set("Orphaned output: present in Load/Replacement folder but missing from Dump folder")
            self.manager_info_var.set(
                f"{p.name}{multi}  •  ORPHANED OUTPUT  •  {texture_kind}  •  "
                f"Transparency: {transparency}  •  No matching dump texture found"
            )
            self._manager_load_preview_async(None, p)
            return
        if quarantine_meta:
            original_relative = Path(str(quarantine_meta.get("original_relative") or p.name))
            original_dump_path = dump_folder / original_relative
            out = routed_output_path_for_input(original_dump_path, dump_folder, load_folder, cfg)
            category = str(quarantine_meta.get("category", "quarantined")).replace("_", " ").title()
            reason = str(quarantine_meta.get("reason", "") or "Detector reason unavailable")
            self.manager_preset_var.set(TEXTURE_PRESET_INHERIT)
            self.manager_preset_status_var.set(
                f"Quarantined: {category}  •  Restore target: {original_relative.as_posix()}"
            )
            self.manager_info_var.set(
                f"{p.name}{multi}  •  QUARANTINED {category}  •  {texture_kind}  •  "
                f"Transparency: {transparency}  •  {reason}"
            )
            self._manager_load_preview_async(p, out)
            return

        out = routed_output_path_for_input(p, dump_folder, load_folder, cfg)
        overrides = load_texture_preset_overrides(profile_dir)
        key = texture_override_key(p, dump_folder)
        override = overrides.get(key)
        override_profile = find_workflow_profile(override) if override else None
        game_default = normalize_faithfulness_preset(cfg.get("faithfulness_preset", "Clean Heart"))
        effective = override_profile["name"] if override_profile else game_default
        self.manager_preset_var.set(override_profile["name"] if override_profile else TEXTURE_PRESET_INHERIT)
        self.manager_preset_status_var.set(
            f"Effective mode: {effective}" + ("  •  texture override" if override_profile else "  •  game default")
        )
        self.manager_info_var.set(
            f"{p.name}{multi}  •  {texture_kind}  •  Transparency: {transparency}  •  "
            f"Output: {'ready' if out.exists() else 'missing'}  •  "
            f"Exception: {'yes' if is_exception_texture(p) else 'no'}  •  "
            f"Changed profile: {'yes' if override_profile else 'no'}"
        )
        self._manager_load_preview_async(p, out)

    def _manager_load_preview_async(self, dump_path, load_path):
        self.manager_preview_generation += 1
        self.manager_preview_jobs += 1
        generation = self.manager_preview_generation
        self.manager_info_var.set(self.manager_info_var.get() + "  •  Loading preview…")

        def worker():
            dump_img = load_img = None
            dump_dims = load_dims = None
            if PIL_AVAILABLE:
                try:
                    if dump_path is not None and Path(dump_path).is_file():
                        with Image.open(dump_path) as img:
                            dump_dims = img.size
                            dump_img = img.convert("RGBA").copy()
                except Exception:
                    pass
                try:
                    if Path(load_path).is_file():
                        with Image.open(load_path) as img:
                            load_dims = img.size
                            load_img = img.convert("RGBA").copy()
                except Exception:
                    pass
            self.manager_async_q.put(("preview", generation, dump_img, load_img, dump_dims, load_dims))

        self._manager_start_async_poll()
        threading.Thread(target=worker, daemon=True, name="TextureManagerPreview").start()

    def _manager_finish_preview(self, generation, dump_img, load_img, dump_dims, load_dims):
        if generation != self.manager_preview_generation:
            return
        self.manager_dump_pil = dump_img
        self.manager_load_pil = load_img
        base = self.manager_info_var.get().replace("  •  Loading preview…", "")
        dims = ""
        if dump_dims and load_dims:
            dims = f"  •  {dump_dims[0]}×{dump_dims[1]} → {load_dims[0]}×{load_dims[1]}"
        elif dump_dims:
            dims = f"  •  {dump_dims[0]}×{dump_dims[1]}"
        elif load_dims:
            dims = f"  •  Output {load_dims[0]}×{load_dims[1]}"
        self.manager_info_var.set(base + dims)
        self._manager_render_previews()

    def _manager_schedule_preview_resize(self, _event=None):
        if self.manager_preview_resize_after is not None:
            try:
                self.after_cancel(self.manager_preview_resize_after)
            except Exception:
                pass
        self.manager_preview_resize_after = self.after(120, self._manager_render_previews)

    def _manager_draw_canvas(self, canvas, pil_image, placeholder):
        try:
            canvas.delete("all")
            width = max(40, canvas.winfo_width())
            height = max(40, canvas.winfo_height())
            if pil_image is None or not PIL_AVAILABLE:
                canvas.create_text(width // 2, height // 2, text=placeholder, fill="#6f8293",
                                   font=("Segoe UI", 10), width=max(100, width - 30))
                return None
            image = pil_image.copy()
            image.thumbnail((max(20, width - 16), max(20, height - 16)), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            canvas.create_image(width // 2, height // 2, image=photo, anchor="center")
            return photo
        except Exception:
            return None

    def _manager_render_previews(self):
        self.manager_preview_resize_after = None
        self.manager_dump_photo = self._manager_draw_canvas(
            self.manager_dump_canvas, self.manager_dump_pil, "Preview unavailable"
        )
        self.manager_load_photo = self._manager_draw_canvas(
            self.manager_load_canvas, self.manager_load_pil, "Output not created yet"
        )

    def _manager_clear_preview(self, message="Select a texture to inspect it."):
        self.manager_dump_pil = None
        self.manager_load_pil = None
        self.manager_info_var.set(message)
        self.manager_preset_status_var.set("Game default follows the active profile")
        self._manager_render_previews()

    def _manager_compare_modes(self):
        paths = self._manager_selected_paths()
        if len(paths) != 1:
            messagebox.showinfo(
                "Compare modes",
                "Select exactly one active texture to compare Original, Clean Heart and Strong Believer."
            )
            return
        source = Path(paths[0])
        if str(source) in self.manager_quarantine_metadata:
            messagebox.showinfo("Compare modes", "Restore the quarantined texture before comparing modes.")
            return
        if str(source) in {str(x) for x in getattr(self, "manager_orphaned_outputs", [])}:
            messagebox.showinfo("Compare modes", "Orphaned outputs have no source dump to compare.")
            return
        if not source.is_file():
            messagebox.showerror("Compare modes", "The selected dump file no longer exists.")
            return
        ctx = self._manager_context(show_errors=True)
        if not ctx:
            return
        cfg, _dump_folder, _load_folder, _profile_dir = ctx
        existing = getattr(self, "manager_compare_window", None)
        if existing is not None:
            try:
                if existing.winfo_exists() and existing.source_path == source:
                    existing.lift()
                    existing.focus_force()
                    return
                existing._close()
            except Exception:
                pass
        self.manager_compare_window = TextureComparisonViewer(self, source, cfg)

    def _manager_apply_preset(self, clear=False, quiet=False):
        paths = self._manager_selected_paths()
        if any(str(p) in self.manager_quarantine_metadata for p in paths):
            messagebox.showinfo("Texture mode", "Restore quarantined files before using this action.")
            return False
        orphaned_set = {str(x) for x in getattr(self, "manager_orphaned_outputs", [])}
        if any(str(p) in orphaned_set for p in paths):
            messagebox.showinfo("Texture mode", "Orphaned outputs have no source dump to assign a texture mode.")
            return False
        if not paths:
            if not quiet:
                messagebox.showinfo("Texture mode", "Select one or more textures first.")
            return False
        ctx = self._manager_context(show_errors=True)
        if not ctx:
            return False
        cfg, dump_folder, _load_folder, profile_dir = ctx
        overrides = load_texture_preset_overrides(profile_dir)
        chosen = self.manager_preset_var.get()
        if clear or chosen == TEXTURE_PRESET_INHERIT:
            for p in paths:
                overrides.pop(texture_override_key(p, dump_folder), None)
            action = "Cleared mode override for"
        else:
            chosen = normalize_faithfulness_preset(chosen)
            chosen_profile = find_workflow_profile(chosen)
            if not chosen_profile:
                messagebox.showerror("Workflow profile", f"Profile not found: {chosen}")
                return False
            for p in paths:
                overrides[texture_override_key(p, dump_folder)] = chosen_profile["id"]
            action = f"Assigned {chosen_profile['name']} to"
        save_texture_preset_overrides(profile_dir, overrides)
        if getattr(self, "worker", None) is not None:
            try:
                self.worker.texture_preset_overrides = dict(overrides)
            except Exception:
                pass
        self.log(f"{action} {len(paths)} texture(s).")
        changed_filter = bool(
            getattr(self, "manager_filter_vars", {}).get("changed_profiles")
            and self.manager_filter_vars["changed_profiles"].get()
        )
        if changed_filter:
            self._manager_apply_filter()
        else:
            self._manager_on_select()
        return True

    def _manager_recreate_selected(self, apply_mode=False):
        paths = self._manager_selected_paths()
        if any(str(p) in self.manager_quarantine_metadata for p in paths):
            messagebox.showinfo("Recreate textures", "Restore quarantined files before using this action.")
            return False
        orphaned_set = {str(x) for x in getattr(self, "manager_orphaned_outputs", [])}
        if any(str(p) in orphaned_set for p in paths):
            messagebox.showinfo("Recreate textures", "Orphaned outputs have no source dump to recreate.")
            return False
        if not paths:
            messagebox.showinfo("Recreate textures", "Select one or more textures first.")
            return
        if apply_mode and not self._manager_apply_preset(clear=False, quiet=True):
            return
        if not messagebox.askyesno(
            "Recreate selected textures",
            f"Prepare {len(paths)} selected texture(s) for recreation?\n\n"
            "Their current outputs and matching hash-cache entries will be removed. Original dump files will remain untouched."
        ):
            return
        ctx = self._manager_context(show_errors=True)
        if not ctx:
            return
        cfg, dump_folder, load_folder, _profile_dir = ctx
        processed_log = Path(cfg.get("processed_log") or (DATA_DIR / "processed.txt"))
        if not processed_log.is_absolute():
            processed_log = DATA_DIR / processed_log
        processed_lines = []
        if processed_log.exists():
            try:
                processed_lines = [x.strip() for x in processed_log.read_text(encoding="utf-8").splitlines() if x.strip()]
            except Exception:
                processed_lines = []
        selected_strings = {str(p) for p in paths}
        selected_resolved = {str(Path(p).resolve()) for p in paths}
        selected_names = {Path(p).name for p in paths}
        remaining_lines = [
            x for x in processed_lines
            if x not in selected_strings and str(Path(x).resolve()) not in selected_resolved
        ]
        cache_index = load_cache_index()
        deleted_outputs = 0
        cleared_cache = 0
        failures = []
        for p in paths:
            try:
                out = routed_output_path_for_input(p, dump_folder, load_folder, cfg)
                if out.exists():
                    out.unlink()
                    deleted_outputs += 1
                if self.worker is not None:
                    try:
                        self.worker.unmark_processed(p)
                        self.worker.known_files.discard(str(p))
                    except Exception:
                        pass
            except Exception as exc:
                failures.append(f"{p.name}: {exc}")

        cache_digests_to_remove = []
        for cache_digest, metadata in list(cache_index.items()):
            metadata = metadata if isinstance(metadata, dict) else {}
            recorded_paths = set()
            if metadata.get("source_path"):
                recorded_paths.add(str(Path(str(metadata["source_path"])).resolve()))
            if isinstance(metadata.get("source_paths"), list):
                for value in metadata["source_paths"]:
                    try:
                        recorded_paths.add(str(Path(str(value)).resolve()))
                    except Exception:
                        pass
            legacy_name_match = not recorded_paths and str(metadata.get("source_name") or "") in selected_names
            if recorded_paths.intersection(selected_resolved) or legacy_name_match:
                cache_digests_to_remove.append(str(cache_digest))

        for cache_digest in cache_digests_to_remove:
            metadata = cache_index.pop(cache_digest, {})
            cache_name = metadata.get("cached_file") if isinstance(metadata, dict) else None
            cache_file = CACHE_DIR / (str(cache_name) if cache_name else f"{cache_digest}.png")
            if cache_file.exists():
                cache_file.unlink()
                cleared_cache += 1
            if self.worker is not None:
                try:
                    self.worker.cache_index.pop(cache_digest, None)
                except Exception:
                    pass
        try:
            processed_log.parent.mkdir(parents=True, exist_ok=True)
            processed_log.write_text("\n".join(remaining_lines) + ("\n" if remaining_lines else ""), encoding="utf-8")
            save_cache_index(cache_index)
        except Exception as exc:
            failures.append(str(exc))
        active_worker = bool(self.worker is not None and self.worker_thread and self.worker_thread.is_alive())
        manager_queued = 0
        if active_worker:
            manager_queued = self.worker.enqueue_manager_tasks(paths)
        self.force_missing_event.set()
        self.force_scan_event.set()
        self.log(
            f"Recreate requested for {len(paths)} texture(s): {deleted_outputs} output(s) deleted, "
            f"{cleared_cache} cache file(s) cleared, {manager_queued} queued at MANAGER priority."
        )
        self._manager_refresh_async(force=True)
        if active_worker:
            queue_note = (
                f"\nManager priority queued: {manager_queued}"
                "\n\nThe current ComfyUI texture will finish safely. These requests will then run before High and Low priority textures."
            )
        else:
            queue_note = "\n\nStart Watching to process them. They will be detected as missing outputs."
        messagebox.showinfo(
            "Recreate selected textures",
            f"Prepared: {len(paths)}\nDeleted outputs: {deleted_outputs}\n"
            f"Cleared cache files: {cleared_cache}\nFailures: {len(failures)}" + queue_note
        )

    def _manager_add_exception(self):
        paths = self._manager_selected_paths()
        if any(str(p) in self.manager_quarantine_metadata for p in paths):
            messagebox.showinfo("Exceptions", "Restore quarantined files before using this action.")
            return False
        orphaned_set = {str(x) for x in getattr(self, "manager_orphaned_outputs", [])}
        if any(str(p) in orphaned_set for p in paths):
            messagebox.showinfo("Exceptions", "Orphaned outputs have no source dump for exception rules.")
            return False
        orphaned_set = {str(x) for x in getattr(self, "manager_orphaned_outputs", [])}
        if any(str(p) in orphaned_set for p in paths):
            messagebox.showinfo("Exceptions", "Orphaned outputs have no source dump for exception rules.")
            return False
        if not paths:
            return
        for p in paths:
            add_exception_pattern(p.name)
        self.log(f"Added {len(paths)} texture(s) to exceptions.")
        self._manager_apply_filter()
        self._manager_on_select()

    def _manager_remove_exception(self):
        paths = self._manager_selected_paths()
        if any(str(p) in self.manager_quarantine_metadata for p in paths):
            messagebox.showinfo("Exceptions", "Restore quarantined files before using this action.")
            return False
        if not paths:
            return
        removed = 0
        for p in paths:
            removed += len(remove_exception_patterns_for_texture(p))
        self.log(f"Removed {removed} exception pattern(s) from {len(paths)} texture(s).")
        self._manager_apply_filter()
        if self.manager_list.curselection():
            self._manager_on_select()

    def _manager_delete_load(self):
        paths = self._manager_selected_paths()
        if any(str(p) in self.manager_quarantine_metadata for p in paths):
            messagebox.showinfo("Delete outputs", "Restore quarantined files before using this action.")
            return False
        if not paths:
            return
        ctx = self._manager_context(show_errors=True)
        if not ctx:
            return
        cfg, dump_folder, load_folder, _profile_dir = ctx
        if not messagebox.askyesno("Delete outputs", f"Delete the remastered output for {len(paths)} selected texture(s)?"):
            return
        deleted = 0
        orphaned_set = {str(x) for x in getattr(self, "manager_orphaned_outputs", [])}
        for p in paths:
            try:
                out = p if str(p) in orphaned_set else routed_output_path_for_input(p, dump_folder, load_folder, cfg)
                if out.exists():
                    out.unlink()
                    deleted += 1
            except Exception as exc:
                self.log(f"Could not delete output for {p.name}: {exc}")
        self.log(f"Deleted {deleted} remastered output(s).")
        self._manager_refresh_async(force=True)

    def _manager_restore_paths(self, paths):
        paths = [Path(p) for p in paths if str(p) in self.manager_quarantine_metadata]
        if not paths:
            messagebox.showinfo("Restore quarantined files", "Select one or more quarantined files first.")
            return
        ctx = self._manager_context(show_errors=True)
        if not ctx:
            return
        cfg, dump_folder, _load_folder, profile_dir = ctx
        conflicts = []
        for path in paths:
            metadata = self.manager_quarantine_metadata.get(str(path)) or {}
            target = dump_folder / Path(str(metadata.get("original_relative") or path.name))
            if target.exists():
                conflicts.append(target)
        overwrite = False
        if conflicts:
            overwrite = messagebox.askyesno(
                "Restore conflicts",
                f"{len(conflicts):,} restore target(s) already exist in the Dump folder.\n\n"
                "Overwrite those existing files? Choosing No will skip conflicts."
            )
        restored = skipped = failed = 0
        failures = []
        restored_targets = []
        for path in paths:
            try:
                status, target = restore_quarantined_dump(
                    path, profile_dir, dump_folder, overwrite=overwrite
                )
                if status == "restored":
                    restored += 1
                    restored_targets.append(Path(target))
                    if restored <= 20:
                        self.log(f"Restored quarantined file: {path.name} -> {target}")
                else:
                    skipped += 1
            except Exception as exc:
                failed += 1
                failures.append(f"{path.name}: {exc}")
        for item in failures[:20]:
            self.log(f"Quarantine restore failed: {item}")

        if restored_targets:
            processed_log = Path(cfg.get("processed_log") or (profile_dir / "processed.txt"))
            if not processed_log.is_absolute():
                processed_log = DATA_DIR / processed_log
            restored_keys = {str(path) for path in restored_targets}
            restored_keys.update(str(path.resolve()) for path in restored_targets)
            try:
                lines = [
                    line.strip() for line in processed_log.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ] if processed_log.exists() else []
                kept = [line for line in lines if line not in restored_keys and str(Path(line).resolve()) not in restored_keys]
                _atomic_write_text(processed_log, "\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            except Exception as exc:
                self.log(f"Could not update processed log after restore: {exc}")
            active_worker = getattr(self, "worker", None)
            if active_worker is not None:
                for target in restored_targets:
                    try:
                        active_worker.unmark_processed(target)
                        active_worker.known_files.discard(str(target))
                    except Exception:
                        pass
            self.force_scan_event.set()

        self._manager_refresh_async(force=True)
        messagebox.showinfo(
            "Restore complete",
            f"Restored: {restored:,}\nSkipped conflicts: {skipped:,}\nFailed: {failed:,}"
        )

    def _manager_restore_selected(self):
        self._manager_restore_paths(self._manager_selected_paths())

    def _manager_restore_all(self):
        if not self.manager_quarantined_files:
            messagebox.showinfo("Restore quarantined files", "This profile has no quarantined files.")
            return
        if not messagebox.askyesno(
            "Restore all quarantined files",
            f"Restore all {len(self.manager_quarantined_files):,} quarantined file(s) to their exact Dump-folder paths?"
        ):
            return
        self._manager_restore_paths(self.manager_quarantined_files)

    def _manager_open_quarantine_folder(self):
        ctx = self._manager_context(show_errors=True)
        if not ctx:
            return
        _cfg, _dump_folder, _load_folder, profile_dir = ctx
        selected = self._manager_selected_paths()
        if selected and str(selected[0]) in self.manager_quarantine_metadata:
            os.startfile(str(selected[0].parent))
            return
        buffer_root = profile_dir / "_buffer_quarantine"
        cleanup_root = profile_dir / "_cleanup_quarantine"
        target = buffer_root if buffer_root.exists() else cleanup_root
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(target))

    def _manager_open_dump_folder(self):
        paths = self._manager_selected_paths()
        if not paths:
            self.open_cfg_folder("dump_folder")
            return
        ctx = self._manager_context(show_errors=True)
        if not ctx:
            return
        _cfg, dump_folder, _load_folder, _profile_dir = ctx
        metadata = self.manager_quarantine_metadata.get(str(paths[0]))
        orphaned_set = {str(x) for x in getattr(self, "manager_orphaned_outputs", [])}
        if str(paths[0]) in orphaned_set:
            os.startfile(str(dump_folder))
        elif metadata:
            relative = Path(str(metadata.get("original_relative") or paths[0].name))
            target_parent = (dump_folder / relative).parent
            target_parent.mkdir(parents=True, exist_ok=True)
            os.startfile(str(target_parent))
        else:
            os.startfile(str(paths[0].parent))

    def _manager_open_load_folder(self):
        paths = self._manager_selected_paths()
        ctx = self._manager_context(show_errors=True)
        if not ctx:
            return
        cfg, dump_folder, load_folder, _profile_dir = ctx
        if paths:
            source_path = paths[0]
            metadata = self.manager_quarantine_metadata.get(str(source_path))
            orphaned_set = {str(x) for x in getattr(self, "manager_orphaned_outputs", [])}
            if str(source_path) in orphaned_set:
                os.startfile(str(source_path.parent))
                return
            if metadata:
                source_path = dump_folder / Path(str(metadata.get("original_relative") or source_path.name))
            out = routed_output_path_for_input(source_path, dump_folder, load_folder, cfg)
            out.parent.mkdir(parents=True, exist_ok=True)
            os.startfile(str(out.parent))
        else:
            self.open_cfg_folder("load_folder")

    def quarantine_efb_cutscene_dumps(self, refresh_callback=None):
        """Move only detector-confirmed Dynamic EFB and grayscale cutscene buffers.

        This action intentionally does not call the blank-dump detector, the visual
        mask/effect classifier, or any output cleanup routine. Ordinary effects,
        masks, UI textures and other active dumps are never selected by category.
        """
        if self.batch_active or (self.worker_thread and self.worker_thread.is_alive()):
            messagebox.showinfo(
                "Buffer quarantine unavailable",
                "Stop watching and stop the Batch Queue before moving active dumps."
            )
            return
        try:
            cfg = self.collect()
            dump_text = str(cfg.get("dump_folder", "") or "").strip()
            if not dump_text:
                messagebox.showerror("Buffer quarantine", "No Dump folder is configured for this profile.")
                return
            dump_folder = Path(dump_text)
            if not dump_folder.exists():
                messagebox.showerror("Buffer quarantine", "The configured Dump folder does not exist.")
                return
        except Exception as exc:
            messagebox.showerror("Buffer quarantine", str(exc))
            return
        if not PIL_AVAILABLE:
            messagebox.showerror("Buffer quarantine", "Pillow is required to identify EFB and cutscene buffers.")
            return
        if not messagebox.askyesno(
            "Quarantine EFB + Cutscenes",
            "Scan this profile's active Dump folder and move only:\n\n"
            "• detector-confirmed Dynamic EFB / post-processing render targets\n"
            "• detector-confirmed grayscale cutscene/frame buffers\n\n"
            "A precision protection pass keeps UI portraits, sprites and masks active.\n"
            "Safe blank cleanup and ordinary effect/mask classifications are NOT used.\n"
            "Nothing is permanently deleted; every moved file keeps restore metadata."
        ):
            return

        candidates = []
        protected = []
        scanned = 0
        scan_cfg = dict(cfg)
        # A manual review action must work even if the live skip toggles were
        # temporarily disabled. This does not change the saved profile settings.
        scan_cfg["skip_dynamic_efb_postprocess"] = True
        scan_cfg["skip_cutscene_buffers"] = True
        process_tmp = bool(cfg.get("process_tmp_image_files", True))
        load_text = str(cfg.get("load_folder", "") or "").strip()
        load_folder = Path(load_text) if load_text else None
        exclude_load_tree = bool(load_folder and is_path_within(load_folder, dump_folder))
        try:
            try:
                self.configure(cursor="wait")
            except Exception:
                pass
            self.update_idletasks()
            self.log(f"Strict EFB/Cutscene quarantine scan: {dump_folder}")
            for path in dump_folder.rglob("*"):
                if not path.is_file() or is_inside_alpha_output_folder(path, dump_folder):
                    continue
                if exclude_load_tree and is_path_within(path, load_folder):
                    continue
                if not is_image_like(path, process_tmp):
                    continue
                scanned += 1
                category, reason = classify_strict_buffer_quarantine_candidate(path, scan_cfg)
                if category:
                    candidates.append((path, category, reason))
                elif str(reason).startswith("protected "):
                    protected.append((path, reason))
                    if len(protected) <= 20:
                        self.log(f"Quarantine protection kept active: {path.name} ({reason})")
                if scanned % 100 == 0:
                    self.title(f"{APP_TITLE} — Scanning {scanned:,} files…")
                    self.update_idletasks()
        finally:
            try:
                self.title(APP_TITLE)
                self.configure(cursor="")
            except Exception:
                pass

        efb_count = sum(1 for _p, category, _r in candidates if category == "dynamic_efb")
        cutscene_count = len(candidates) - efb_count
        if not candidates:
            messagebox.showinfo(
                "Buffer quarantine",
                f"Scanned {scanned:,} active image files.\n"
                f"Protected UI / sprites / masks kept active: {len(protected):,}\n\n"
                "No high-confidence EFB or cutscene buffers were found."
            )
            return
        if not messagebox.askyesno(
            "Confirm EFB + Cutscene quarantine",
            f"Scanned: {scanned:,}\nDynamic EFB: {efb_count:,}\nCutscene buffers: {cutscene_count:,}\n"
            f"Protected UI / sprites / masks kept active: {len(protected):,}\n\n"
            f"Move these {len(candidates):,} high-confidence files out of the active Dump folder?"
        ):
            return

        profile_dir = PROFILES_DIR / safe_profile_name(self.current_profile_name)
        session = profile_dir / "_buffer_quarantine" / f"buffers-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        moved_rows, failure_rows = quarantine_dumps_bulk(
            candidates, dump_folder, session, manifest_flush_every=250
        )
        moved = len(moved_rows)
        failed = len(failure_rows)
        moved_efb = sum(1 for _s, _d, category, _r in moved_rows if category == "dynamic_efb")
        moved_cutscene = sum(1 for _s, _d, category, _r in moved_rows if category == "cutscene")
        failures = [f"{path}: {error}" for path, error in failure_rows]
        for source, destination, category, reason in moved_rows[:20]:
            self.log(f"Buffer quarantined: {source.name} ({category}; {reason}) -> {destination}")
        for item in failures[:20]:
            self.log(f"Buffer quarantine failed: {item}")
        self.log(
            f"Strict buffer quarantine complete: scanned={scanned}, EFB={efb_count}, "
            f"cutscenes={cutscene_count}, protected={len(protected)}, moved={moved}, failed={failed}"
        )
        if callable(refresh_callback):
            try:
                refresh_callback()
            except Exception:
                pass
        messagebox.showinfo(
            "EFB + Cutscene quarantine complete",
            f"Dynamic EFB moved: {moved_efb:,}\nCutscene buffers moved: {moved_cutscene:,}\n"
            f"Total moved: {moved:,}\nFailed: {failed:,}\n\n"
            "Open Texture Manager → Filter → Quarantined to preview or restore them."
        )

    def mass_delete_cutscene_black_dumps(self, refresh_callback=None):
        """Scan the current dump tree and quarantine provably blank dumps.

        Dynamic EFB/post-processing candidates are never included in this cleanup.
        """
        if self.batch_active or (self.worker_thread and self.worker_thread.is_alive()):
            messagebox.showinfo(
                "Cleanup unavailable",
                "Stop watching and stop the Batch Queue before moving dumps to quarantine."
            )
            return

        try:
            cfg = self.collect()
            dump_text = str(cfg.get("dump_folder", "") or "").strip()
            if not dump_text:
                messagebox.showerror("Safe cleanup", "No dump folder is configured for this profile.")
                return
            dump_folder = Path(dump_text)
            if not dump_folder.exists():
                messagebox.showerror("Safe cleanup", "The configured dump folder does not exist.")
                return
        except Exception as exc:
            messagebox.showerror("Safe cleanup", str(exc))
            return

        if not PIL_AVAILABLE:
            messagebox.showerror("Safe cleanup", "Pillow is required to detect cutscene and black dumps.")
            return

        if not messagebox.askyesno(
            "Safe blank-dump cleanup",
            "Scan the current game's dump folder for fully transparent and near-solid black dumps?\n\n"
            "Dynamic EFB and post-processing candidates are EXCLUDED.\n"
            "Nothing will be permanently deleted; selected files will be moved to a profile quarantine folder."
        ):
            return

        candidates = []
        scanned = 0
        errors = []
        process_tmp = bool(cfg.get("process_tmp_image_files", True))
        load_text = str(cfg.get("load_folder", "") or "").strip()
        load_folder = Path(load_text) if load_text else None
        exclude_load_tree = bool(load_folder and is_path_within(load_folder, dump_folder))

        try:
            self.configure(cursor="wait")
            self.update_idletasks()
            self.log(f"Safe cleanup scanning: {dump_folder}")
            self.log("Dynamic EFB/post-processing dumps are excluded from this cleanup.")

            for path in dump_folder.rglob("*"):
                if not path.is_file():
                    continue
                if is_inside_alpha_output_folder(path, dump_folder):
                    continue
                if exclude_load_tree and is_path_within(path, load_folder):
                    continue
                if not is_image_like(path, process_tmp):
                    continue
                scanned += 1
                is_dump, reason = detect_safe_blank_dump(path, cfg)
                if is_dump:
                    candidates.append((path, reason))
                if scanned % 100 == 0:
                    try:
                        self.title(f"{APP_TITLE} — Scanning {scanned:,} files…")
                        self.update_idletasks()
                    except Exception:
                        pass
        finally:
            try:
                self.title(APP_TITLE)
                self.configure(cursor="")
            except Exception:
                pass

        if not candidates:
            messagebox.showinfo(
                "Safe cleanup",
                f"Scanned {scanned:,} image files.\n\nNo fully transparent or near-solid-black dumps were detected.\nDynamic EFB candidates were not considered."
            )
            return

        if not messagebox.askyesno(
            "Confirm quarantine",
            f"Found {len(candidates):,} safe blank dumps out of {scanned:,} scanned files.\n\n"
            "Move them to quarantine now? They can be restored by copying them back to the dump folder."
        ):
            return

        profile_dir = PROFILES_DIR / safe_profile_name(self.current_profile_name)
        quarantine_session = profile_dir / "_cleanup_quarantine" / time.strftime("manual-%Y%m%d-%H%M%S")
        quarantined = 0
        for index, (path, reason) in enumerate(candidates, start=1):
            try:
                destination = quarantine_dump(path, dump_folder, quarantine_session, category="blank", reason=reason)
                quarantined += 1
                if quarantined <= 20:
                    self.log(f"Cleanup quarantined: {path.name} ({reason}) -> {destination}")
            except Exception as exc:
                errors.append(f"{path}: {exc}")
            if index % 100 == 0:
                try:
                    self.title(f"{APP_TITLE} — Quarantined {quarantined:,} / {len(candidates):,}")
                    self.update_idletasks()
                except Exception:
                    pass

        try:
            self.title(APP_TITLE)
        except Exception:
            pass

        self.log(
            f"Safe cleanup finished: scanned={scanned}, detected={len(candidates)}, "
            f"quarantined={quarantined}, failed={len(errors)}"
        )
        if quarantined:
            self.log(f"Quarantine folder: {quarantine_session}")
        for item in errors[:20]:
            self.log(f"Cleanup quarantine failed: {item}")

        if callable(refresh_callback):
            try:
                refresh_callback()
            except Exception:
                pass

        text = (
            f"Scanned: {scanned:,}\n"
            f"Detected: {len(candidates):,}\n"
            f"Moved to quarantine: {quarantined:,}\n\n"
            f"Quarantine folder:\n{quarantine_session}"
        )
        if errors:
            text += f"\nFailed: {len(errors):,}\n\nThe first failures were written to the live log."
        messagebox.showinfo("Safe cleanup complete", text)

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
                current_emu = self.emulator_var.get() or "Generic"
                detected = detect_emulator_from_dump_path(path, current_emu)
                if detected["confidence"] < 55 and current_emu == "Generic" and Path(path).name.casefold() in {"dumps", "new", "glidenhq"}:
                    chosen = self.choose_scan_emulator(detected.get("emulator", "Generic"))
                    if chosen:
                        current_emu = chosen
                        detected = {"emulator": chosen, "confidence": 100, "reason": "selected by user"}
                if detected["confidence"] >= 70 and detected["emulator"] != current_emu:
                    current_emu = detected["emulator"]
                    self.emulator_var.set(current_emu)
                    self.profile_emulator_filter_var.set(current_emu)
                    self._update_emulator_path_hint()
                    self._update_emulator_specific_controls()
                    self.log(f"Emulator detected from dump folder: {current_emu}")
                game_id = self.vars.get("game_id").get().strip() if self.vars.get("game_id") else ""
                dump, load, inferred_id = profile_paths_from_dump_selection(current_emu, path, game_id)
                if dump:
                    self.vars["dump_folder"].set(str(dump))
                    path = str(dump)
                if inferred_id and self.vars.get("game_id") and not game_id:
                    self.vars["game_id"].set(inferred_id)
                self.apply_detected_game_name(path)
                if load and "load_folder" in self.vars:
                    self.vars["load_folder"].set(str(load))
                if current_emu == "Azahar / Citra":
                    self.after(50, lambda: self.refresh_azahar_metadata(manual=False))

            if hasattr(self, "saved_state_var"):
                self.saved_state_var.set("● Unsaved changes")

    def collect(self):
        cfg = dict(self.cfg)
        for key, var in self.vars.items():
            cfg[key] = var.get()
        cfg["load_image_node_id"] = str(cfg.get("load_image_node_id", "1")).strip()
        cfg["save_image_node_id"] = str(cfg.get("save_image_node_id", "4")).strip()
        cfg["alpha_load_image_node_id"] = str(cfg.get("alpha_load_image_node_id", "1")).strip()
        cfg["alpha_save_image_node_id"] = str(cfg.get("alpha_save_image_node_id", "5")).strip()
        cfg["overwrite"] = bool(self.overwrite_var.get())
        cfg["preserve_alpha"] = bool(self.alpha_var.get())
        cfg["process_tmp_image_files"] = bool(self.tmp_var.get())
        cfg["enable_hash_cache"] = bool(self.hash_var.get())
        cfg["ignore_existing_silently"] = bool(self.ignore_existing_var.get())
        cfg["prioritize_new_dumps"] = bool(self.priority_var.get())
        cfg["skip_cutscene_buffers"] = bool(self.cutscene_filter_var.get())
        cfg["skip_dynamic_efb_postprocess"] = bool(self.dynamic_efb_filter_var.get())
        cfg["delete_skipped_cutscene_buffers"] = bool(self.delete_cutscene_var.get())
        cfg["auto_scan_delete_cutscene_buffers_on_start"] = bool(self.auto_cleanup_cutscene_var.get())
        cfg["auto_quarantine_efb_cutscenes"] = bool(
            self.auto_quarantine_buffers_var.get() if hasattr(self, "auto_quarantine_buffers_var") else False
        )
        cfg["auto_quarantine_live_threshold"] = max(2, int(float(cfg.get("auto_quarantine_live_threshold", 12) or 12)))
        cfg["auto_quarantine_live_idle_seconds"] = max(1.0, float(cfg.get("auto_quarantine_live_idle_seconds", 5.0) or 5.0))
        cfg["enable_comfy_status"] = bool(self.comfy_monitor_var.get())
        cfg["pause_when_comfy_offline"] = bool(self.pause_comfy_var.get())
        cfg["auto_start_comfy_when_watching"] = bool(self.auto_start_comfy_var.get())
        cfg["auto_check_missing_load"] = bool(self.auto_missing_var.get())
        cfg["enable_separate_alpha_workflow"] = bool(self.alpha_workflow_var.get())
        cfg["alpha_workflow_invert_output"] = bool(self.alpha_wf_invert_var.get())
        cfg["enable_vram_protection"] = bool(self.vram_var.get())
        cfg["auto_sync_azahar_pack_json"] = bool(self.auto_sync_azahar_pack_var.get())
        cfg["faithfulness_preset"] = normalize_faithfulness_preset(self.faithfulness_preset_var.get() if hasattr(self, "faithfulness_preset_var") else cfg.get("faithfulness_preset", "Clean Heart"))
        cfg["live_texture_preview"] = bool(self.live_preview_var.get()) if hasattr(self, "live_preview_var") else True
        cfg["emulator"] = self.emulator_var.get() or "Generic"
        cfg["max_vram_gb"] = float(cfg.get("max_vram_gb", 10.0))
        cfg["vram_resume_margin_gb"] = float(cfg.get("vram_resume_margin_gb", 0.5))
        cfg["alpha_resize_method"] = "nearest"
        cfg["alpha_source"] = "original"
        cfg["alpha_feather_radius"] = 0.0
        cfg["fix_alpha_edge_bleed"] = False
        if hasattr(self, "manager_sort_var"):
            cfg["manager_sort_by"] = manager_sort_code(self.manager_sort_var.get())
        else:
            cfg["manager_sort_by"] = manager_sort_code(cfg.get("manager_sort_by", "modified_newest"))
        if hasattr(self, "manager_group_var"):
            cfg["manager_group_by"] = manager_group_code(self.manager_group_var.get())
        else:
            cfg["manager_group_by"] = manager_group_code(cfg.get("manager_group_by", "none"))
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
        cfg["profile_dir"] = str(profile_dir)
        return cfg

    def start(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self.log("Already running.")
            return False
        try:
            # Node IDs are validated, not silently rewritten, when watching starts.
            self.cfg = self.collect()
            self.cfg["batch_queue_mode"] = bool(getattr(self, "_batch_launching", False))
            self.validate(self.cfg)
            self.save_current_profile(quiet=True)
            self.cfg = self.configure_profile_runtime_paths(self.cfg)
            if self.emulator_var.get() == "Azahar / Citra" and self.cfg.get("auto_sync_azahar_pack_json", True):
                sync_azahar_pack_json(self.cfg["dump_folder"], self.cfg["load_folder"], self.log)
            save_config(self.cfg)

            self._progress_cache = {"at": 0.0, "profile": "", "done": 0, "total": 0}
            initial_done, _initial_total = self._current_profile_progress_cached(max_age=0)
            self._session_started_at = time.time()
            self._session_initial_done = initial_done
            self._session_last_elapsed = 0.0

            threading.Thread(
                target=self.ensure_comfy_started_for_watching,
                args=(dict(self.cfg),),
                daemon=True
            ).start()

            self.stop_event.clear()
            self.force_scan_event.clear()
            self.force_missing_event.clear()
            self.stats.update({
                "processed": 0, "failed": 0, "cache_hits": 0, "comfy_jobs": 0,
                "queue_len": 0, "manager_queue_len": 0, "high_queue_len": 0, "low_queue_len": 0,
                "peak_vram_mb": 0, "exceptions_skipped": 0,
                "cutscene_buffers_skipped": 0, "cutscene_buffers_deleted": 0,
                "startup_cleanup_scanned": 0, "startup_cleanup_detected": 0,
                "startup_cleanup_deleted": 0, "startup_cleanup_failed": 0,
                "startup_cleanup_recent_skipped": 0,
                "auto_buffer_quarantine_scanned": 0,
                "auto_buffer_quarantine_detected": 0,
                "auto_buffer_quarantine_moved": 0,
                "auto_buffer_quarantine_failed": 0,
                "auto_buffer_quarantine_recent_skipped": 0,
                "current_input_path": "", "current_output_path": "",
                "current_texture_stage": "Waiting", "current_texture_started_at": 0.0,
                "current_faithfulness_preset": normalize_faithfulness_preset(self.faithfulness_preset_var.get()),
                "current_priority_lane": "IDLE", "status": "RUNNING"
            })
            self._preview_last_input = ""
            self._preview_last_output = ""
            worker = Worker(
                self.cfg, self.log_q, self.stop_event,
                self.force_scan_event, self.force_missing_event, self.stats
            )
            self.worker = worker
            self.worker_thread = threading.Thread(target=worker.run, daemon=True)
            self.worker_thread.start()
            self._update_profile_header_card()
            self.log(f"Started watching profile: {self.current_profile_name}")
            return True
        except Exception as e:
            messagebox.showerror("Start failed", str(e))
            return False

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


# v11.9 features intentionally live outside the core GUI file.
try:
    import sys as _sys
    import faithful_universal as _faithful_universal
    _faithful_universal.install(_sys.modules[__name__])
except Exception as _universal_error:
    try:
        APP_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with APP_LOG_PATH.open("a", encoding="utf-8") as _log_file:
            _log_file.write(f"Universal feature layer failed: {_universal_error}\n")
    except Exception:
        pass

if __name__ == "__main__":
    V11App().mainloop()
