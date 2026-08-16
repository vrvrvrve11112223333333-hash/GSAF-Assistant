from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import queue
import random
import re
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
import winsound
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox

import cv2
import numpy as np
from PIL import Image


APP_NAME = "GSAF Assistant"
APP_VERSION = "5.4.0"


def default_game_path() -> Path:
    """Resolve the game without embedding a machine-specific user path."""
    configured = os.environ.get("GSAF_GAME_PATH")
    if configured:
        return Path(configured).expanduser().resolve()

    executable_dir = Path(sys.executable).resolve().parent
    source_dir = Path(__file__).resolve().parent
    candidates = (
        executable_dir / "gsaf-v-1-0-0.exe",
        executable_dir.parent / "gsaf-v-1-0-0.exe",
        source_dir / "gsaf-v-1-0-0.exe",
        source_dir.parent / "gsaf-v-1-0-0.exe",
        Path.cwd() / "gsaf-v-1-0-0.exe",
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


GAME_PATH = default_game_path()
GAME_SHA256 = "D0C45CAB60567765AE30E5DA70EE58F48234D8107809464A6BF2B4DC6BC795F5"
GAME_SIZE = (1344, 756)
MATCH_SIZE = (448, 252)
SCENE_SIZE = (168, 94)
OBSERVER_INTERVAL = 0.55
MEMORY_INTERVAL = 0.06
AUTO_OVERDUE_GRACE = 10.0
EXACT_CONFIRM_SAMPLES = 2
EXACT_CLEAR_SAMPLES = 2

BG = "#090b10"
PANEL = "#11151d"
PANEL_2 = "#171d27"
TEXT = "#f3f4f6"
MUTED = "#9aa4b2"
GREEN = "#58d68d"
YELLOW = "#f2c94c"
ORANGE = "#f2994a"
RED = "#ff4d5a"
BLUE = "#56a8ff"
PURPLE = "#bb86fc"
CALM_SOUND_THRESHOLD = 95
CALM_CHIME_HZ = 480
CALM_CHIME_MS = 45
BERRY_MUSIC_COUNTER_OI = 146
BERRY_MUSIC_VISUAL_OI = 144
BERRY_MUSIC_COUNTER_MAX = 9100
BERRY_MUSIC_END_POSITION = 9050
BERRY_MUSIC_STOPPED_IMAGE = 484
BERRY_MUSIC_PLAYING_IMAGE = 524
BERRY_MUSIC_WARNING_SECONDS = 15.0
BERRY_MUSIC_CRITICAL_SECONDS = 6.0
BERRY_RESTART_GRACE_SECONDS = 1.25


@dataclass(frozen=True)
class Threat:
    name: str
    label: str
    first_night: int
    interval_by_night: tuple[int, int, int, int, int]
    first_delay_by_night: tuple[int, int, int, int, int]
    action: str
    short_action: str
    node: str
    color: str
    hotkey: str

    def interval(self, night: int) -> int:
        return self.interval_by_night[night - 1]

    def first_delay(self, night: int) -> int:
        return self.first_delay_by_night[night - 1]


THREATS: dict[str, Threat] = {
    "bonnie": Threat(
        "bonnie", "Бонни", 1,
        (34, 30, 27, 24, 20), (26, 23, 21, 18, 16),
        "Если появилось DISTRACT HIM — нажми Alarm. У окна опусти ставню и держи до звука ухода.",
        "Alarm по DISTRACT HIM; у окна — ставня.", "window", "#e88bb5", "1",
    ),
    "chica": Threat(
        "chica", "Чика", 2,
        (99, 40, 35, 30, 25), (99, 31, 27, 23, 20),
        "Когда опустишь камеры и Чика окажется в офисе — закрой ставню. Не включай свет.",
        "Ставня. Свет НЕ включать.", "office", "#f1c75b", "2",
    ),
    "cream": Threat(
        "cream", "Cream", 2,
        (99, 43, 37, 32, 27), (99, 34, 29, 25, 21),
        "Следи за MGNTOFFICE 07. Alarm только в 3-й фазе, когда изо рта дёргаются щупальца.",
        "CAM 07: Alarm только в финальной фазе.", "mgmt", "#ff8f8f", "3",
    ),
    "berry": Threat(
        "berry", "Berry", 3,
        (99, 99, 24, 20, 16), (99, 99, 16, 14, 12),
        "На PLAYROOM 05 держи музыку включённой. Не давай Berry полностью выйти из укрытия.",
        "PLAYROOM 05: поддерживай музыку.", "playroom", "#75a7ff", "4",
    ),
    "slithers": Threat(
        "slithers", "Slithers", 4,
        (99, 99, 99, 38, 30), (99, 99, 99, 30, 24),
        "При звуке и появлении Slithers НЕ переключай камеру. Опусти монитор и подожди, пока он уйдёт.",
        "Не переключай камеру; опусти монитор и жди.", "camera", "#7fe0a0", "5",
    ),
    "freddy": Threat(
        "freddy", "Фредди", 5,
        (99, 99, 99, 99, 14), (99, 99, 99, 99, 12),
        "Срочно найди Фредди на камерах и смотри на него, чтобы остановить движение. Затем нажми Ctrl+Alt+F.",
        "Найди на камерах, смотри, затем Ctrl+Alt+F.", "camera", RED, "Ф",
    ),
}

CALM_TARGET_COLORS = {
    "bonnie": "#e8a4c2",
    "chica": "#ead183",
    "cream": "#efaaaa",
    "berry": "#98b9f7",
    "slithers": "#96d8aa",
    "freddy": "#ff8f96",
}


MAP_NODES = {
    "stage": (52, 42, "01\nSTAGE"),
    "party": (145, 33, "02\nPARTY"),
    "dining": (242, 55, "03\nDINING"),
    "kitchen": (270, 125, "04\nKITCHEN"),
    "playroom": (225, 205, "05\nPLAY"),
    "hall": (125, 195, "06\nHALL"),
    "mgmt": (45, 175, "07\nMGMT"),
    "window": (78, 257, "ОКНО"),
    "office": (190, 270, "ОФИС"),
    "camera": (165, 115, "КАМЕРЫ"),
}

MAP_EDGES = [
    ("stage", "party"), ("party", "dining"), ("dining", "kitchen"),
    ("kitchen", "playroom"), ("playroom", "hall"), ("hall", "mgmt"),
    ("mgmt", "window"), ("window", "office"), ("camera", "stage"),
    ("camera", "playroom"), ("camera", "mgmt"),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


def find_game_window() -> int | None:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    found: list[int] = []
    target = GAME_PATH.name.casefold()
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return True
        try:
            size = ctypes.c_ulong(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                if Path(buffer.value).name.casefold() == target:
                    found.append(int(hwnd))
                    return False
        finally:
            kernel32.CloseHandle(handle)
        return True

    user32.EnumWindows(enum_proc, 0)
    return found[0] if found else None


def game_window_exists() -> bool:
    return find_game_window() is not None


def game_process_id() -> int | None:
    hwnd = find_game_window()
    if hwnd is None:
        return None
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value) or None


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def capture_game_window(hwnd: int) -> Image.Image | None:
    """Capture only the game's pixels, even if the assistant is on top."""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width < 320 or height < 200:
        return None
    source_dc = user32.GetWindowDC(hwnd)
    if not source_dc:
        return None
    memory_dc = gdi32.CreateCompatibleDC(source_dc)
    bitmap = gdi32.CreateCompatibleBitmap(source_dc, width, height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory_dc, 0x00000002):
            return None
        pixels = ctypes.create_string_buffer(width * height * 4)
        bitmap_info = ctypes.create_string_buffer(40)
        ctypes.memset(bitmap_info, 0, 40)
        ctypes.cast(bitmap_info, ctypes.POINTER(ctypes.c_uint32))[0] = 40
        ctypes.cast(bitmap_info, ctypes.POINTER(ctypes.c_int32))[1] = width
        ctypes.cast(bitmap_info, ctypes.POINTER(ctypes.c_int32))[2] = -height
        ctypes.cast(bitmap_info, ctypes.POINTER(ctypes.c_uint16))[6] = 1
        ctypes.cast(bitmap_info, ctypes.POINTER(ctypes.c_uint16))[7] = 32
        if not gdi32.GetDIBits(memory_dc, bitmap, 0, height, pixels, bitmap_info, 0):
            return None
        return Image.frombuffer(
            "RGB", (width, height), pixels, "raw", "BGRX", 0, 1
        ).copy()
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, source_dc)


def launch_game() -> tuple[bool, str]:
    if game_window_exists():
        return True, "Игра уже запущена"
    if not GAME_PATH.exists():
        return False, f"Игра не найдена:\n{GAME_PATH}"
    actual = sha256(GAME_PATH)
    if actual != GAME_SHA256:
        return False, "Файл игры изменился. Запуск остановлен для безопасности."
    try:
        subprocess.Popen([str(GAME_PATH)], cwd=str(GAME_PATH.parent))
        return True, "Игра запущена"
    except OSError as exc:
        return False, f"Не удалось запустить игру: {exc}"


class GameObserver:
    def __init__(self, start: bool = True) -> None:
        self.assets = resource_path("gsaf_assets")
        self.results: queue.SimpleQueue[dict[str, object]] = queue.SimpleQueue()
        self.stop_event = threading.Event()
        self.templates: list[dict[str, object]] = []
        self.scenes: list[dict[str, object]] = []
        self.load_error: str | None = None
        try:
            self._load_assets()
        except Exception as exc:  # the manual fallback remains usable
            self.load_error = str(exc)
        self.thread = threading.Thread(
            target=self._loop, name="GSAF-AutoObserver", daemon=True
        )
        if start:
            self.thread.start()

    @staticmethod
    def _read_image(path: Path, flags: int) -> np.ndarray:
        data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        image = cv2.imdecode(data, flags)
        if image is None:
            raise ValueError(f"cannot read {path.name}")
        return image

    def _load_assets(self) -> None:
        manifest_path = self.assets / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["templates"]:
            rgba = self._read_image(self.assets / item["file"], cv2.IMREAD_UNCHANGED)
            if rgba.ndim == 2:
                gray = rgba
                alpha = None
            elif rgba.shape[2] == 4:
                gray = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_BGR2GRAY)
                alpha = rgba[:, :, 3]
                alpha = np.where(alpha >= 32, 255, 0).astype(np.uint8)
                if np.all(alpha == 255):
                    alpha = None
            else:
                gray = cv2.cvtColor(rgba, cv2.COLOR_BGR2GRAY)
                alpha = None
            use_edges = item["id"] == "bonnie_distract"
            if use_edges:
                gray = cv2.Canny(gray, 35, 105)
                alpha = None
            self.templates.append(
                {**item, "gray": gray, "mask": alpha, "use_edges": use_edges}
            )
        for item in manifest["scenes"]:
            bgr = self._read_image(self.assets / item["file"], cv2.IMREAD_COLOR)
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            self.scenes.append(
                {**item, "gray": gray, "edges": cv2.Canny(gray, 35, 105)}
            )

    @staticmethod
    def _normalize(image: Image.Image) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        rgb = np.asarray(image.convert("RGB"))
        height, width = rgb.shape[:2]
        target_aspect = GAME_SIZE[0] / GAME_SIZE[1]
        aspect = width / height
        if aspect > target_aspect:
            keep = round(height * target_aspect)
            left = max(0, (width - keep) // 2)
            rgb = rgb[:, left : left + keep]
        elif aspect < target_aspect:
            keep = round(width / target_aspect)
            top = max(0, (height - keep) // 2)
            rgb = rgb[top : top + keep, :]
        full = cv2.resize(rgb, GAME_SIZE, interpolation=cv2.INTER_AREA)
        full_gray = cv2.cvtColor(full, cv2.COLOR_RGB2GRAY)
        match_gray = cv2.resize(full_gray, MATCH_SIZE, interpolation=cv2.INTER_AREA)
        scene_gray = cv2.resize(full_gray, SCENE_SIZE, interpolation=cv2.INTER_AREA)
        return full_gray, match_gray, scene_gray

    def _classify_scene(self, scene_gray: np.ndarray) -> tuple[str, int | None, float]:
        current_edges = cv2.Canny(scene_gray, 35, 105)
        best_ident = "other"
        best_camera: int | None = None
        best_score = -1.0
        for scene in self.scenes:
            intensity = float(
                cv2.matchTemplate(
                    scene_gray, scene["gray"], cv2.TM_CCOEFF_NORMED
                )[0, 0]
            )
            edges = float(
                cv2.matchTemplate(
                    current_edges, scene["edges"], cv2.TM_CCOEFF_NORMED
                )[0, 0]
            )
            score = intensity * 0.72 + edges * 0.28
            if score > best_score:
                best_score = score
                best_ident = str(scene["id"])
                best_camera = scene.get("camera")
        if best_score < 0.25:
            return "other", None, best_score
        if best_ident == "office":
            return "office", None, best_score
        return "camera", int(best_camera) if best_camera else None, best_score

    @staticmethod
    def _template_score(
        frame: np.ndarray, template: np.ndarray, mask: np.ndarray | None
    ) -> float:
        if template.shape[0] > frame.shape[0] or template.shape[1] > frame.shape[1]:
            return -1.0
        if mask is not None and cv2.countNonZero(mask) >= 8:
            result = cv2.matchTemplate(
                frame, template, cv2.TM_CCORR_NORMED, mask=mask
            )
        else:
            result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
        finite = result[np.isfinite(result)]
        return float(finite.max()) if finite.size else -1.0

    def analyze_image(self, image: Image.Image) -> dict[str, object]:
        full_gray, match_gray, scene_gray = self._normalize(image)
        full_edges = cv2.Canny(full_gray, 35, 105)
        scene, camera, scene_score = self._classify_scene(scene_gray)
        best_by_threat: dict[str, dict[str, object]] = {}
        candidates: list[dict[str, object]] = []
        for item in self.templates:
            if item["use_edges"]:
                frame = full_edges
            else:
                frame = full_gray if float(item["scale"]) > 0.75 else match_gray
            score = self._template_score(frame, item["gray"], item["mask"])
            candidate = {
                "id": item["id"],
                "threat": item["threat"],
                "signal": item["signal"],
                "score": round(score, 4),
                "threshold": item["threshold"],
                "contexts": item.get("contexts", ["office", "camera"]),
                "cameras": item.get("cameras", []),
            }
            candidates.append(candidate)
            threat = str(item["threat"])
            if threat not in best_by_threat or score > float(best_by_threat[threat]["score"]):
                best_by_threat[threat] = candidate
        valid_by_threat: dict[str, dict[str, object]] = {}
        for item in candidates:
            valid = (
                scene in ("office", "camera")
                and scene in item.get("contexts", ("office", "camera"))
                and (
                    not item.get("cameras")
                    or (camera is not None and camera in item["cameras"])
                )
                and float(item["score"]) >= float(item["threshold"])
            )
            if not valid:
                continue
            threat = str(item["threat"])
            previous = valid_by_threat.get(threat)
            if previous is None or float(item["score"]) > float(previous["score"]):
                valid_by_threat[threat] = item
        detections = list(valid_by_threat.values())
        candidates.sort(key=lambda item: float(item["score"]), reverse=True)
        return {
            "scene": scene,
            "camera": camera,
            "scene_score": round(scene_score, 4),
            "detections": detections,
            "candidates": candidates[:8],
        }

    def scan_once(self) -> dict[str, object]:
        started = time.monotonic()
        hwnd = find_game_window()
        if not hwnd:
            return {
                "timestamp": started,
                "window_found": False,
                "scene": "missing",
                "camera": None,
                "scene_score": 0.0,
                "detections": [],
                "candidates": [],
            }
        image = capture_game_window(hwnd)
        if image is None:
            return {
                "timestamp": started,
                "window_found": True,
                "scene": "unavailable",
                "camera": None,
                "scene_score": 0.0,
                "detections": [],
                "candidates": [],
            }
        result = self.analyze_image(image)
        return {
            "timestamp": started,
            "window_found": True,
            **result,
            "scan_ms": round((time.monotonic() - started) * 1000),
        }

    def _loop(self) -> None:
        if self.load_error:
            self.results.put(
                {
                    "timestamp": time.monotonic(),
                    "window_found": game_window_exists(),
                    "scene": "error",
                    "camera": None,
                    "scene_score": 0.0,
                    "detections": [],
                    "error": self.load_error,
                }
            )
            return
        while not self.stop_event.is_set():
            loop_started = time.monotonic()
            try:
                self.results.put(self.scan_once())
            except Exception as exc:
                self.results.put(
                    {
                        "timestamp": loop_started,
                        "window_found": game_window_exists(),
                        "scene": "error",
                        "camera": None,
                        "scene_score": 0.0,
                        "detections": [],
                        "error": str(exc),
                    }
                )
            remaining = OBSERVER_INTERVAL - (time.monotonic() - loop_started)
            self.stop_event.wait(max(0.05, remaining))

    def poll_latest(self) -> dict[str, object] | None:
        latest = None
        while True:
            try:
                latest = self.results.get_nowait()
            except queue.Empty:
                return latest

    def close(self) -> None:
        self.stop_event.set()


class FusionMemoryObserver:
    """Read Clickteam Fusion alterable values without writing to the game."""

    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    MEM_COMMIT = 0x1000
    MEM_PRIVATE = 0x20000
    PAGE_GUARD = 0x100
    PAGE_NOACCESS = 0x01
    PAGE_READWRITE = 0x04
    TARGETS = {68: "misc", 121: "ai", 147: "ai2"}

    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_void_p),
            ("AllocationBase", ctypes.c_void_p),
            ("AllocationProtect", ctypes.c_ulong),
            ("PartitionId", ctypes.c_ushort),
            ("RegionSize", ctypes.c_size_t),
            ("State", ctypes.c_ulong),
            ("Protect", ctypes.c_ulong),
            ("Type", ctypes.c_ulong),
        ]

    def __init__(self, start: bool = True) -> None:
        self.kernel32 = ctypes.windll.kernel32
        self.results: queue.SimpleQueue[dict[str, object]] = queue.SimpleQueue()
        self.stop_event = threading.Event()
        self.handle = None
        self.pid: int | None = None
        self.objects: dict[str, int] = {}
        self.last_resolve = 0.0
        self.thread = threading.Thread(
            target=self._loop, name="GSAF-ReadOnlyMemory", daemon=True
        )
        if start:
            self.thread.start()

    def _close_handle(self) -> None:
        if self.handle:
            self.kernel32.CloseHandle(self.handle)
        self.handle = None
        self.pid = None
        self.objects.clear()

    def _attach(self, pid: int) -> bool:
        self._close_handle()
        handle = self.kernel32.OpenProcess(
            self.PROCESS_QUERY_INFORMATION | self.PROCESS_VM_READ, False, pid
        )
        if not handle:
            return False
        self.handle = handle
        self.pid = pid
        return True

    def _read(self, address: int, size: int) -> bytes:
        if not self.handle or address < 0x10000 or size <= 0:
            return b""
        buffer = ctypes.create_string_buffer(size)
        read = ctypes.c_size_t()
        ok = self.kernel32.ReadProcessMemory(
            self.handle,
            ctypes.c_void_p(address),
            buffer,
            size,
            ctypes.byref(read),
        )
        if not ok and read.value == 0:
            return b""
        return buffer.raw[: read.value]

    def _regions(self):
        address = 0
        info = self.MEMORY_BASIC_INFORMATION()
        while address < 0x80000000 and self.handle:
            result = self.kernel32.VirtualQueryEx(
                self.handle,
                ctypes.c_void_p(address),
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not result:
                break
            base = int(info.BaseAddress or 0)
            size = int(info.RegionSize)
            protection = int(info.Protect)
            if (
                info.State == self.MEM_COMMIT
                and info.Type == self.MEM_PRIVATE
                and protection & 0xFF == self.PAGE_READWRITE
                and not protection & self.PAGE_GUARD
                and not protection & self.PAGE_NOACCESS
            ):
                yield base, size
            next_address = base + max(size, 0x1000)
            if next_address <= address:
                break
            address = next_address

    @staticmethod
    def _header_candidate(data: bytes, offset: int, address: int, oi: int) -> bool:
        if offset < 0 or offset + 160 > len(data):
            return False
        size = struct.unpack_from("<i", data, offset + 4)[0]
        self_pointer = struct.unpack_from("<I", data, offset + 12)[0]
        object_info = struct.unpack_from("<h", data, offset + 18)[0]
        object_type = struct.unpack_from("<h", data, offset + 24)[0]
        value_offset = struct.unpack_from("<i", data, offset + 128)[0]
        return (
            self_pointer == address
            and object_info == oi
            and object_type == 2
            and 136 <= size <= 0x4000
            and 136 <= value_offset < size
        )

    @staticmethod
    def _counter_header_candidate(data: bytes, offset: int, address: int, oi: int) -> bool:
        if offset < 0 or offset + 704 > len(data):
            return False
        size = struct.unpack_from("<i", data, offset + 4)[0]
        self_pointer = struct.unpack_from("<I", data, offset + 12)[0]
        object_info = struct.unpack_from("<h", data, offset + 18)[0]
        object_type = struct.unpack_from("<h", data, offset + 24)[0]
        return (
            self_pointer == address
            and object_info == oi
            and object_type == 7
            and 704 <= size <= 0x4000
        )

    @staticmethod
    def _active_header_candidate(data: bytes, offset: int, address: int, oi: int) -> bool:
        if offset < 0 or offset + 224 > len(data):
            return False
        size = struct.unpack_from("<i", data, offset + 4)[0]
        self_pointer = struct.unpack_from("<I", data, offset + 12)[0]
        object_info = struct.unpack_from("<h", data, offset + 18)[0]
        object_type = struct.unpack_from("<h", data, offset + 24)[0]
        return (
            self_pointer == address
            and object_info == oi
            and object_type == 2
            and 224 <= size <= 0x4000
        )

    def _read_music_box_counter(self, address: int) -> dict[str, int | float] | None:
        """Decode the game's own Visual-MusicBoxTimer counter without writing memory."""
        header = self._read(address, 704)
        if len(header) < 704 or struct.unpack_from("<I", header, 12)[0] != address:
            return None
        if struct.unpack_from("<h", header, 18)[0] != BERRY_MUSIC_COUNTER_OI:
            return None
        maximum = struct.unpack_from("<i", header, 684)[0]
        raw_position = struct.unpack_from("<i", header, 696)[0]
        if not 8500 <= maximum <= 10000:
            return None
        position = max(0, min(maximum, abs(raw_position)))
        end_position = min(maximum, BERRY_MUSIC_END_POSITION)
        return {
            "position": position,
            "maximum": maximum,
            "end_position": end_position,
            "elapsed_seconds": round(position / 100.0, 2),
            "remaining_seconds": round(max(0, end_position - position) / 100.0, 2),
        }

    def _read_music_box_visual(self, address: int) -> dict[str, object] | None:
        """Read the exact PLAY/STOP image shown by the game's Music Box button."""
        header = self._read(address, 224)
        if len(header) < 224 or struct.unpack_from("<I", header, 12)[0] != address:
            return None
        if struct.unpack_from("<h", header, 18)[0] != BERRY_MUSIC_VISUAL_OI:
            return None
        if struct.unpack_from("<h", header, 24)[0] != 2:
            return None
        image = struct.unpack_from("<i", header, 220)[0]
        playing: bool | None
        if image == BERRY_MUSIC_PLAYING_IMAGE:
            playing = True
        elif image == BERRY_MUSIC_STOPPED_IMAGE:
            playing = False
        else:
            playing = None
        return {
            "image": image,
            "playing": playing,
            "button": "stop" if playing is True else "play" if playing is False else "unknown",
        }

    def _resolve_objects(self) -> bool:
        candidates: dict[int, list[tuple[int, int, int]]] = {
            oi: [] for oi in self.TARGETS
        }
        music_candidates: dict[int, list[tuple[int, int]]] = {}
        music_visual_candidates: dict[int, list[tuple[int, int]]] = {}
        for base, size in self._regions():
            if size > 256 * 1024 * 1024:
                continue
            data = self._read(base, size)
            if len(data) < 160:
                continue
            for oi in self.TARGETS:
                pattern = struct.pack("<h", oi)
                start = 18
                while True:
                    found = data.find(pattern, start)
                    if found < 0:
                        break
                    offset = found - 18
                    address = base + offset
                    if self._header_candidate(data, offset, address, oi):
                        run = struct.unpack_from("<I", data, offset + 8)[0]
                        creation = struct.unpack_from("<H", data, offset + 26)[0]
                        candidates[oi].append((run, address, creation))
                    start = found + 2

            pattern = struct.pack("<h", BERRY_MUSIC_COUNTER_OI)
            start = 18
            while True:
                found = data.find(pattern, start)
                if found < 0:
                    break
                offset = found - 18
                address = base + offset
                if self._counter_header_candidate(
                    data, offset, address, BERRY_MUSIC_COUNTER_OI
                ):
                    run = struct.unpack_from("<I", data, offset + 8)[0]
                    creation = struct.unpack_from("<H", data, offset + 26)[0]
                    music_candidates.setdefault(run, []).append((address, creation))
                start = found + 2

            pattern = struct.pack("<h", BERRY_MUSIC_VISUAL_OI)
            start = 18
            while True:
                found = data.find(pattern, start)
                if found < 0:
                    break
                offset = found - 18
                address = base + offset
                if self._active_header_candidate(
                    data, offset, address, BERRY_MUSIC_VISUAL_OI
                ):
                    run = struct.unpack_from("<I", data, offset + 8)[0]
                    creation = struct.unpack_from("<H", data, offset + 26)[0]
                    music_visual_candidates.setdefault(run, []).append(
                        (address, creation)
                    )
                start = found + 2

        runs: dict[int, dict[int, tuple[int, int]]] = {}
        for oi, items in candidates.items():
            for run, address, creation in items:
                runs.setdefault(run, {})[oi] = (address, creation)
        complete = [item for item in runs.items() if len(item[1]) == len(self.TARGETS)]
        if not complete:
            self.objects.clear()
            return False
        ranked: list[tuple[int, int, int, dict[int, tuple[int, int]]]] = []
        counts = {"misc": 40, "ai": 32, "ai2": 16}
        for run, selected in complete:
            temporary = {self.TARGETS[oi]: selected[oi][0] for oi in self.TARGETS}
            snapshot: dict[str, list[int | float | None]] = {}
            for name, address in temporary.items():
                result = self._read_values(address, counts[name])
                if result is None:
                    snapshot.clear()
                    break
                snapshot[name] = result[0]
            score = self._plausibility_score(snapshot)
            ranked.append(
                (score, sum(value[1] > 0 for value in selected.values()), run, selected)
            )
        if not ranked:
            self.objects.clear()
            return False
        score, _created, selected_run, selected = max(ranked, key=lambda item: item[:3])
        if score < 8:
            self.objects.clear()
            return False
        self.objects = {self.TARGETS[oi]: selected[oi][0] for oi in self.TARGETS}
        for address, _creation in sorted(
            music_visual_candidates.get(selected_run, []),
            key=lambda item: item[1],
            reverse=True,
        ):
            visual = self._read_music_box_visual(address)
            if visual is not None and visual.get("playing") is not None:
                self.objects["music_box_visual"] = address
                break
        for address, _creation in sorted(
            music_candidates.get(selected_run, []), key=lambda item: item[1], reverse=True
        ):
            if self._read_music_box_counter(address) is not None:
                self.objects["music_box"] = address
                break
        if "music_box_visual" not in self.objects:
            # The actual PLAY/STOP button is authoritative. Berry's phase timer is not.
            self.objects.clear()
            return False
        return True

    @staticmethod
    def _plausibility_score(snapshot: dict[str, list[int | float | None]]) -> int:
        """Prefer the live Gameplay object set over stale Clickteam allocations."""
        misc = snapshot.get("misc")
        ai = snapshot.get("ai")
        ai2 = snapshot.get("ai2")
        if not isinstance(misc, list) or not isinstance(ai, list) or not isinstance(ai2, list):
            return -100

        def integer(values: list, index: int) -> int | None:
            value = values[index] if index < len(values) else None
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                return None
            converted = int(value)
            return converted if abs(float(value) - converted) < 0.001 else None

        score = 0
        checks = (
            (integer(misc, 18), range(0, 8), 2),
            (integer(misc, 23), range(0, 61), 2),
            (integer(misc, 24), {12, 1, 2, 3, 4, 5, 6}, 3),
            (integer(ai, 0), range(0, 11), 2),
            (integer(ai, 1), range(0, 11), 2),
            (integer(ai, 2), range(0, 9), 2),
            (integer(ai, 3), range(-1, 9), 2),
            (integer(ai, 4), range(0, 7), 2),
            (integer(ai, 8), range(0, 11), 3),
            (integer(ai2, 0), range(0, 6), 2),
        )
        for value, allowed, weight in checks:
            score += weight if value in allowed else -weight * 3
        return score

    def _read_values(self, address: int, count: int) -> tuple[list[int | float | None], int] | None:
        header = self._read(address, 160)
        if len(header) < 136 or struct.unpack_from("<I", header, 12)[0] != address:
            return None
        value_offset = struct.unpack_from("<i", header, 128)[0]
        if value_offset < 136 or value_offset >= struct.unpack_from("<i", header, 4)[0]:
            return None
        runtime_values = self._read(address + value_offset, 112)
        if len(runtime_values) < 108:
            return None
        values_pointer = struct.unpack_from("<I", runtime_values, 0)[0]
        flags = struct.unpack_from("<I", runtime_values, 104)[0]
        raw = self._read(values_pointer, count * 16)
        if len(raw) < count * 16:
            return None
        values: list[int | float | None] = []
        for index in range(count):
            value_type = struct.unpack_from("<I", raw, index * 16)[0]
            if value_type == 0:
                values.append(struct.unpack_from("<i", raw, index * 16 + 8)[0])
            elif value_type == 2:
                values.append(struct.unpack_from("<d", raw, index * 16 + 8)[0])
            else:
                values.append(None)
        return values, flags

    def scan_once(self) -> dict[str, object]:
        pid = game_process_id()
        if pid is None:
            self._close_handle()
            return {"exact": False, "status": "missing", "timestamp": time.monotonic()}
        if pid != self.pid or not self.handle:
            if not self._attach(pid):
                return {"exact": False, "status": "denied", "pid": pid, "timestamp": time.monotonic()}
        now = time.monotonic()
        if not self.objects:
            if now - self.last_resolve < 1.5:
                return {"exact": False, "status": "waiting", "pid": pid, "timestamp": now}
            self.last_resolve = now
            if not self._resolve_objects():
                return {"exact": False, "status": "not_gameplay", "pid": pid, "timestamp": now}

        counts = {"misc": 40, "ai": 32, "ai2": 16}
        snapshot: dict[str, object] = {}
        flag_snapshot: dict[str, int] = {}
        for name in counts:
            address = self.objects.get(name)
            if address is None:
                self.objects.clear()
                return {"exact": False, "status": "reconnect", "pid": pid, "timestamp": now}
            result = self._read_values(address, counts[name])
            if result is None:
                self.objects.clear()
                return {"exact": False, "status": "reconnect", "pid": pid, "timestamp": now}
            snapshot[name], flag_snapshot[name] = result
        if self._plausibility_score(snapshot) < 8:
            self.objects.clear()
            return {"exact": False, "status": "reconnect", "pid": pid, "timestamp": now}
        visual_address = self.objects.get("music_box_visual")
        if visual_address is None:
            self.objects.clear()
            self.last_resolve = 0.0
            return {
                "exact": False,
                "status": "reconnect_music_box_visual",
                "pid": pid,
                "timestamp": now,
            }
        music_box_visual = self._read_music_box_visual(visual_address)
        if music_box_visual is None or music_box_visual.get("playing") is None:
            self.objects.clear()
            self.last_resolve = 0.0
            return {
                "exact": False,
                "status": "reconnect_music_box_visual",
                "pid": pid,
                "timestamp": now,
            }
        snapshot["music_box_visual"] = music_box_visual
        music_address = self.objects.get("music_box")
        if music_address is not None:
            music_box = self._read_music_box_counter(music_address)
            if music_box is not None:
                snapshot["music_box"] = music_box
            else:
                # PLAY/STOP remains exact even if the display counter is recreated.
                self.objects.pop("music_box", None)
        return {
            "exact": True,
            "status": "gameplay",
            "pid": pid,
            "timestamp": now,
            "flags": flag_snapshot,
            **snapshot,
        }

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self.results.put(self.scan_once())
            except Exception as exc:
                self.objects.clear()
                self.results.put(
                    {"exact": False, "status": "error", "error": str(exc), "timestamp": started}
                )
            delay = MEMORY_INTERVAL if self.objects else 0.35
            self.stop_event.wait(max(0.03, delay - (time.monotonic() - started)))

    def poll_latest(self) -> dict[str, object] | None:
        latest = None
        while True:
            try:
                latest = self.results.get_nowait()
            except queue.Empty:
                return latest

    def close(self) -> None:
        self.stop_event.set()
        self._close_handle()


THREAT_ABBR = {
    "bonnie": "БОННИ",
    "chica": "ЧИКА",
    "cream": "КРИМ",
    "berry": "БЕРРИ",
    "slithers": "СЛИЗЕРС",
    "freddy": "ФРЕДДИ",
}


def threat_level(score: int) -> tuple[str, str]:
    """Return an explicit human threat band and its UI colour."""
    score = max(0, min(100, int(score)))
    if score >= 95:
        return "КРИТИЧЕСКИЙ", RED
    if score >= 80:
        return "ВЫСОКИЙ", ORANGE
    if score >= 60:
        return "СРЕДНИЙ", YELLOW
    if score >= 35:
        return "НИЗКИЙ", BLUE
    return "СПОКОЙНО", GREEN


FREDDY_CHECK_CAMERAS = {0: 1, 1: 1, 2: 2, 3: 3, 4: 4}
FREDDY_CHECK_FLAG_INDEX = 2
FREDDY_WARNING_TICKS = 5
FREDDY_CONFIRM_GRACE_SECONDS = 0.8


def freddy_check_state(state: dict[str, object]) -> dict[str, object]:
    """Decode Freddy's real hourly camera check and the game's acknowledgement flag."""
    ai = state.get("ai")
    misc = state.get("misc")
    if not isinstance(ai, list) or not isinstance(misc, list):
        return {"valid": False, "mode": "unknown"}

    def number(values: list, index: int, default: int = 0) -> int:
        value = values[index] if index < len(values) else default
        return int(value) if isinstance(value, (int, float)) else default

    phase = number(ai, 2)
    timer = number(ai, 21, 100)
    target_tick = number(ai, 23, 30)
    camera = number(misc, 18)
    current_tick = number(misc, 23)
    hour = number(misc, 24, 12)
    flags = state.get("flags")
    ai_flags = 0
    if isinstance(flags, dict):
        raw_flags = flags.get("ai", 0)
        if isinstance(raw_flags, (int, float)):
            ai_flags = int(raw_flags)
    handled = bool(ai_flags & (1 << FREDDY_CHECK_FLAG_INDEX))
    expected_camera = FREDDY_CHECK_CAMERAS.get(phase)
    eligible = expected_camera is not None and hour != 12 and target_tick in (30, 31)
    delta = target_tick - current_tick if eligible else None

    if handled and eligible:
        mode = "handled"
    elif eligible and isinstance(delta, int) and 0 <= delta <= FREDDY_WARNING_TICKS:
        mode = "due"
    elif eligible and isinstance(delta, int) and delta < 0:
        mode = "missed"
    elif eligible:
        mode = "scheduled"
    elif phase == 5:
        mode = "approach"
    elif phase == 8:
        mode = "final_safe"
    else:
        mode = "inactive"

    return {
        "valid": True,
        "mode": mode,
        "phase": phase,
        "timer": max(0, timer),
        "camera": camera,
        "expected_camera": expected_camera,
        "current_tick": current_tick,
        "target_tick": target_tick,
        "hour": hour,
        "delta": delta,
        "handled": handled,
        "game_acknowledged": handled,
        "ai_flags": ai_flags,
    }


class FreddyWatchTracker:
    """Track continuous viewing while deferring completion to Freddy's own game flag."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.key: tuple[int, int, int] | None = None
        self.watch_seconds = 0.0
        self.confirmed_seconds = 0.0
        self.last_time: float | None = None
        self.was_watching = False
        self.was_handled = False

    def update(self, state: dict[str, object], now: float | None = None) -> dict[str, object]:
        moment = time.monotonic() if now is None else float(now)
        info = freddy_check_state(state)
        if not info.get("valid"):
            self.reset()
            return info

        key = (
            int(info.get("hour", 12)),
            int(info.get("target_tick", 30)),
            int(info.get("phase", 0)),
        )
        if key != self.key:
            self.key = key
            self.watch_seconds = 0.0
            self.confirmed_seconds = 0.0
            self.was_watching = False
            self.was_handled = False
            self.last_time = moment

        scene = str(state.get("screen_scene", ""))
        camera_visible = scene == "camera" or (
            not scene and int(info.get("camera", 0)) == int(info.get("expected_camera") or -1)
        )
        watching = (
            info.get("mode") == "due"
            and camera_visible
            and int(info.get("camera", 0)) == int(info.get("expected_camera") or -1)
        )
        if watching:
            if self.was_watching and self.last_time is not None:
                self.watch_seconds += max(0.0, min(0.25, moment - self.last_time))
        elif info.get("mode") == "due":
            self.watch_seconds = 0.0

        handled = bool(info.get("handled"))
        if handled and not self.was_handled:
            self.confirmed_seconds = self.watch_seconds
        self.was_handled = handled
        self.was_watching = watching
        self.last_time = moment

        delta = info.get("delta")
        remaining = None
        if isinstance(delta, int) and delta >= 0:
            remaining = max(0.4, float(delta) + FREDDY_CONFIRM_GRACE_SECONDS)
        info.update(
            {
                "watching": watching,
                "watch_seconds": round(self.watch_seconds, 1),
                "confirmed_seconds": round(self.confirmed_seconds, 1),
                "remaining_seconds": round(remaining, 1) if remaining is not None else None,
            }
        )
        return info


class BerryMusicTracker:
    """Track Berry's music, including the game's one-frame restart acknowledgement."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.last_position: int | None = None
        self.last_click_guard = 0
        self.last_auxiliary_timer: int | None = None
        self.last_visual_playing: bool | None = None
        self.last_progress_at: float | None = None
        self.ended_latched = False
        self.restart_confirmed_until = 0.0

    def update(
        self, state: dict[str, object], now: float | None = None
    ) -> dict[str, object]:
        moment = time.monotonic() if now is None else now
        ai2 = state.get("ai2")
        misc = state.get("misc")
        phase = 0
        phase_timer = 100
        auxiliary_timer = 0
        camera = 0
        click_guard = 0
        if isinstance(ai2, list):
            if len(ai2) > 0 and isinstance(ai2[0], (int, float)):
                phase = int(ai2[0])
            if len(ai2) > 1 and isinstance(ai2[1], (int, float)):
                phase_timer = int(ai2[1])
            if len(ai2) > 2 and isinstance(ai2[2], (int, float)):
                auxiliary_timer = int(ai2[2])
        if isinstance(misc, list):
            if len(misc) > 4 and isinstance(misc[4], (int, float)):
                click_guard = max(0, int(misc[4]))
            if len(misc) > 18 and isinstance(misc[18], (int, float)):
                camera = int(misc[18])

        visual_raw = state.get("music_box_visual")
        visual_playing: bool | None = None
        visual_image: int | None = None
        if isinstance(visual_raw, dict):
            value = visual_raw.get("playing")
            if isinstance(value, bool):
                visual_playing = value
            image = visual_raw.get("image")
            if isinstance(image, (int, float)):
                visual_image = int(image)

        raw = state.get("music_box")
        position: int | None = None
        maximum = BERRY_MUSIC_COUNTER_MAX
        if isinstance(raw, dict):
            position_raw = raw.get("position")
            maximum_raw = raw.get("maximum")
            if isinstance(maximum_raw, (int, float)):
                maximum = max(1, int(maximum_raw))
            if isinstance(position_raw, (int, float)):
                position = max(0, min(maximum, int(position_raw)))
        end_position = min(maximum, BERRY_MUSIC_END_POSITION)
        reset_edge = (
            position is not None
            and
            self.last_position is not None
            and self.last_position >= end_position * 0.90
            and position <= end_position * 0.02
        )
        reached_end = position is not None and position >= end_position
        progressed = (
            position is not None
            and self.last_position is not None
            and position > self.last_position
        )
        if progressed:
            self.last_progress_at = moment
        if reset_edge or reached_end:
            self.ended_latched = True

        visual_started = visual_playing is True and self.last_visual_playing is False
        visual_stopped = visual_playing is False
        if visual_stopped:
            self.ended_latched = True
            self.restart_confirmed_until = 0.0

        click_edge = click_guard > 0 and (
            self.last_click_guard <= 0 or click_guard > self.last_click_guard
        )
        auxiliary_changed = (
            self.last_auxiliary_timer is not None
            and auxiliary_timer >= 100
            and auxiliary_timer > self.last_auxiliary_timer + 10
        )
        restart_signal = visual_started or (
            self.ended_latched
            and camera == 5
            and (click_edge or auxiliary_changed)
        )
        if restart_signal:
            self.ended_latched = False
            self.restart_confirmed_until = moment + BERRY_RESTART_GRACE_SECONDS
        elif visual_playing is True or (
            position is not None and 1 < position < end_position and not reset_edge
        ):
            self.ended_latched = False

        restart_confirmed = moment < self.restart_confirmed_until
        if visual_playing is not None:
            playing: bool | None = visual_playing
            source = "play-stop-image"
        elif restart_confirmed:
            playing = True
            source = "restart-signal"
        elif position is None:
            playing = None
            source = "unavailable"
        elif self.ended_latched or reset_edge or reached_end or position <= 1:
            playing = False
            source = "counter-stop"
        else:
            playing = True
            source = "counter-progress"

        remaining: float | None
        if playing and position is not None:
            remaining = max(0.0, (end_position - position) / 100.0)
        elif playing:
            remaining = None
        else:
            remaining = 0.0
        if phase >= 5:
            mode = "failed"
        elif playing is None:
            mode = "unknown"
        elif not playing:
            mode = "expired"
        elif remaining is not None and remaining <= BERRY_MUSIC_CRITICAL_SECONDS:
            mode = "critical"
        elif remaining is not None and remaining <= BERRY_MUSIC_WARNING_SECONDS:
            mode = "warning"
        else:
            mode = "healthy"

        if position is not None:
            self.last_position = position
        self.last_click_guard = click_guard
        self.last_auxiliary_timer = auxiliary_timer
        self.last_visual_playing = visual_playing
        return {
            "valid": visual_playing is not None or position is not None,
            "mode": mode,
            "playing": playing,
            "source": source,
            "phase": phase,
            "phase_timer": phase_timer,
            "auxiliary_timer": auxiliary_timer,
            "click_guard": click_guard,
            "restart_confirmed": restart_confirmed,
            "position": position,
            "maximum": maximum,
            "end_position": end_position,
            "visual_image": visual_image,
            "elapsed_seconds": round(position / 100.0, 1) if position is not None else None,
            "remaining_seconds": round(remaining, 1) if remaining is not None else None,
        }


def instruction_steps(alert: dict[str, object]) -> tuple[str, str, str]:
    """Expand one engine alert into an unambiguous three-step instruction."""
    name = str(alert.get("name", ""))
    title = str(alert.get("title", "")).upper()
    action = str(alert.get("action", ""))

    if name == "bonnie":
        if "ALARM СЕЙЧАС" in title:
            return (
                "Не переключай текущую камеру.",
                "Нажми ALARM один раз прямо сейчас.",
                "Убедись, что DISTRACT HIM исчез; затем выполняй следующую команду.",
            )
        if "ЖДИ НА CAM" in title:
            return (
                "Останься на указанной камере.",
                "ALARM пока НЕ нажимай — жди надпись DISTRACT HIM.",
                "Когда помощник покажет «ALARM СЕЙЧАС», нажми ALARM один раз.",
            )
        if "ГОТОВИТ ALARM" in title:
            return (
                "Доведи цикл камер до указанной CAM.",
                "Останься там и жди DISTRACT HIM.",
                "Нажми ALARM только по команде «ALARM СЕЙЧАС».",
            )
        if "ОКНА" in title or "ОКНУ" in title:
            return (
                "Сразу опусти монитор.",
                "Закрой ставню.",
                "Держи ставню закрытой до отчётливого звука ухода.",
            )

    if name == "chica":
        return (
            "Опусти монитор до её входа в офис.",
            "Закрой ставню и НЕ включай свет.",
            "Держи ставню до звука ухода, затем проверь следующую угрозу.",
        )

    if name == "cream":
        if "ФИНАЛЬНАЯ" in title or "ALARM" in title:
            return (
                "Открой или оставь CAM 07.",
                "Нажми ALARM один раз сейчас.",
                "Убедись, что финальная фаза сброшена, и переходи к следующей угрозе.",
            )
        return (
            "Подготовь CAM 07 заранее.",
            "ALARM пока НЕ нажимай.",
            "Жди финальную фазу со щупальцами и команду помощника.",
        )

    if name == "berry":
        if "СКОРО" in title or "ГОТОВЬ" in title:
            return (
                "Перейди на CAM 05 и оставь её готовой.",
                "Пока музыка играет, Play Music НЕ нажимай — это остановит её.",
                "Как только помощник напишет «ВКЛЮЧИ СЕЙЧАС», нажми Play Music один раз.",
            )
        return (
            "Открой или оставь CAM 05.",
            "Нажми Play Music один раз сейчас.",
            "Убедись, что музыка заиграла и 90-секундный счётчик снова пошёл.",
        )

    if name == "slithers":
        return (
            "НЕ переключай текущую камеру.",
            "Сразу опусти монитор.",
            "Жди второй звук; только после него возвращайся к камерам.",
        )

    if name == "freddy":
        if "СТАВН" in title:
            return (
                "Сразу опусти монитор.",
                "Закрой ставню.",
                "Держи её до звука ухода Freddy.",
            )
        if "ИДЁТ" in title:
            return (
                "Заранее опусти монитор.",
                "Приготовь ставню, но не трать время на другие камеры.",
                "Как только он окажется у ставни — закрой её.",
            )
        if "СМОТРИ CAM" in title or "ДЕРЖИ CAM" in title:
            match = re.search(r"CAM\s*(\d+)", title)
            camera_label = f"CAM {int(match.group(1)):02d}" if match else "нужной CAM"
            first_step = (
                f"Оставайся на {camera_label}."
                if "ДЕРЖИ CAM" in title
                else f"Открой {camera_label} с Freddy."
            )
            return (
                first_step,
                action,
                "После «ПРОВЕРЕН» сразу выполняй следующую угрозу.",
            )
        return (
            "Следи за назначенной отметкой проверки Freddy.",
            "Камеру заранее не удерживай без команды помощника.",
            "Смотри только после команды «ФРЕДДИ: СМОТРИ CAM».",
        )

    return (
        action or "Выполни показанную команду.",
        "Проверь, что состояние угрозы изменилось.",
        "Затем переходи к следующей строке очереди.",
    )


def exact_guidance_all(state: dict[str, object]) -> list[dict[str, object]]:
    if not state.get("exact"):
        return []
    ai = state.get("ai")
    ai2 = state.get("ai2")
    misc = state.get("misc")
    if not isinstance(ai, list) or not isinstance(ai2, list) or not isinstance(misc, list):
        return []

    def number(values: list, index: int, default: int = 0) -> int:
        value = values[index] if index < len(values) else default
        return int(value) if isinstance(value, (int, float)) else default

    camera = number(misc, 18)
    alarm_phase = number(misc, 33)
    danger = number(ai, 8)
    alerts: dict[str, dict[str, object]] = {}

    def put(
        name: str,
        title: str,
        action: str,
        priority: int,
        node: str | None = None,
        timer: int | None = None,
        source: str = "state",
        repel: bool = True,
    ) -> None:
        candidate: dict[str, object] = {
            "name": name,
            "title": title,
            "action": action,
            "priority": priority,
            "urgent": priority >= 80,
            "critical": priority >= 90,
            "node": node or THREATS[name].node,
            "source": source,
            "repel": repel,
        }
        if timer is not None:
            candidate["timer"] = max(0, timer)
        previous = alerts.get(name)
        if previous is None or priority > int(previous.get("priority", 0)):
            alerts[name] = candidate

    # A non-zero danger value is the game's own confirmed failure-path marker.
    danger_map = {
        1: ("bonnie", "БОННИ У ОКНА", "Закрой ставню и держи до звука ухода."),
        2: ("chica", "ЧИКА В ОФИСЕ", "Закрой ставню. Свет НЕ включай."),
        3: ("bonnie", "БОННИ У ОКНА", "Закрой ставню немедленно."),
        4: ("bonnie", "БОННИ У ОКНА", "Закрой ставню немедленно."),
        5: ("cream", "КРИМ: ALARM", "Нажми ALARM немедленно."),
        6: ("berry", "БЕРРИ: МУЗЫКА", "CAM 05 — включи Play Music немедленно."),
        7: ("freddy", "ФРЕДДИ У СТАВНИ", "Опусти монитор и закрой ставню."),
        8: ("slithers", "СЛИЗЕРС НА КАМЕРЕ", "Не переключай камеру. Опусти монитор и жди второй звук."),
    }
    if danger in danger_map:
        name, title, action = danger_map[danger]
        put(name, title, action, 100, "window" if danger in {1, 2, 3, 4, 7} else None, source="danger")

    bonnie = number(ai, 0)
    bonnie_timer = number(ai, 7, 100)
    if bonnie == 9:
        put("bonnie", "БОННИ У ОКНА", "Монитор вниз. Закрой ставню и держи до звука ухода.", 98, "window", bonnie_timer)
    elif bonnie == 7:
        put("bonnie", "БОННИ У ОКНА", "Закрой ставню сейчас и держи до звука ухода.", 96, "window", bonnie_timer)
    elif bonnie == 6:
        put("bonnie", "БОННИ ИДЁТ К ОКНУ", "Заранее опусти монитор и приготовь ставню.", 82 if bonnie_timer <= 20 else 72, "window", bonnie_timer)
    elif bonnie in (4, 5):
        alarm_camera = 5 if bonnie == 4 else 6
        node = "playroom" if alarm_camera == 5 else "hall"
        if camera == alarm_camera and alarm_phase == 3:
            put("bonnie", "БОННИ: ALARM СЕЙЧАС", "Нажми ALARM — игра уже показала момент DISTRACT HIM.", 97, node, bonnie_timer)
        elif camera == alarm_camera:
            put("bonnie", f"БОННИ: ЖДИ НА CAM {alarm_camera:02d}", "Не переключай камеру. Нажми ALARM только когда появится DISTRACT HIM.", 78, node, bonnie_timer)
        else:
            put("bonnie", f"БОННИ ГОТОВИТ ALARM · CAM {alarm_camera:02d}", f"Заранее доведи цикл до CAM {alarm_camera:02d}; там жди DISTRACT HIM.", 76 if bonnie_timer <= 30 else 64, node, bonnie_timer)

    chica = number(ai, 1)
    chica_timer = number(ai, 12, 100)
    if chica >= 9:
        put("chica", "ЧИКА В ОФИСЕ", "Монитор вниз. Закрой ставню. Свет НЕ включай.", 99, "office", chica_timer)
    elif chica == 8:
        put("chica", "ЧИКА У ОКНА", "Опусти монитор и закрой ставню. Свет НЕ включай.", 95, "window", chica_timer)
    elif chica == 7:
        put("chica", "ЧИКА БЕЖИТ К ОКНУ", "Опусти монитор заранее; приготовь ставню, свет не трогай.", 84, "window", chica_timer)
    elif chica in (5, 6) and chica_timer <= 20:
        put("chica", "ЧИКА ПРИБЛИЖАЕТСЯ", "Скоро монитор вниз и ставня. Свет НЕ включай.", 66, "hall", chica_timer)

    cream = number(ai, 4)
    cream_timer = number(ai, 19, 100)
    if cream >= 3:
        action = "Нажми ALARM сейчас." if camera == 7 else "Доведи камеры до CAM 07 и сразу нажми ALARM."
        put("cream", "КРИМ: ФИНАЛЬНАЯ ФАЗА", action, 96, "mgmt", cream_timer)
    elif cream == 2:
        put("cream", "КРИМ ПОЧТИ ГОТОВ", "Подготовь CAM 07. ALARM — только в следующей, финальной фазе.", 70, "mgmt", cream_timer)

    berry = number(ai2, 0)
    berry_timer = number(ai2, 1, 100)
    berry_info_raw = state.get("berry_music")
    berry_info = berry_info_raw if isinstance(berry_info_raw, dict) else {}
    berry_mode = str(berry_info.get("mode", "unknown"))
    berry_remaining_raw = berry_info.get("remaining_seconds")
    berry_remaining = (
        max(0.0, float(berry_remaining_raw))
        if isinstance(berry_remaining_raw, (int, float))
        else None
    )
    berry_countdown = int(math.ceil(berry_remaining)) if berry_remaining is not None else None
    if berry_mode == "failed" or berry >= 5:
        put(
            "berry",
            "БЕРРИ ПОКИНУЛА СЦЕНУ",
            "Следуй аварийному сигналу игры; шкатулка уже не удерживает Берри.",
            99,
            "playroom",
            berry_timer,
            source="music-counter",
        )
    elif berry_mode == "expired":
        priority = 96 if berry >= 3 or berry_timer <= 30 else 92
        put(
            "berry",
            "БЕРРИ: ВКЛЮЧИ ШКАТУЛКУ СЕЙЧАС",
            "CAM 05 — нажми Play Music один раз: музыка уже выключилась.",
            priority,
            "playroom",
            0,
            source="music-counter",
        )
    elif berry_mode == "critical" and berry_countdown is not None:
        put(
            "berry",
            f"БЕРРИ: ГОТОВЬ PLAY MUSIC · {berry_countdown}с",
            "Останься на CAM 05. Пока НЕ нажимай; включи сразу после фактического отключения.",
            82,
            "playroom",
            berry_countdown,
            source="music-counter",
            repel=False,
        )
    elif berry_mode == "warning" and berry_countdown is not None:
        put(
            "berry",
            f"БЕРРИ: ШКАТУЛКА СКОРО КОНЧИТСЯ · {berry_countdown}с",
            "Заранее перейди на CAM 05. Play Music нажми только когда музыка выключится.",
            68,
            "playroom",
            berry_countdown,
            source="music-counter",
            repel=False,
        )

    slithers = number(ai, 3)
    slithers_timer = number(ai2, 6, 100)
    if slithers in (7, 8):
        put("slithers", "СЛИЗЕРС: МОНИТОР ВНИЗ", "Камеру НЕ переключай. Опусти монитор и жди второго звука.", 99, "camera", slithers_timer)
    elif 1 <= slithers <= 6 and camera == slithers:
        put("slithers", f"СЛИЗЕРС НА CAM {camera:02d}", "Не переключай камеру. Опусти монитор сейчас и жди второго звука.", 97, "camera", slithers_timer)
    elif slithers == -1 and slithers_timer <= 20:
        put("slithers", "СЛИЗЕРС СКОРО ПОЯВИТСЯ", "Если увидишь его, не переключай камеру — опусти монитор.", 68, "camera", slithers_timer)

    freddy = number(ai, 2)
    freddy_timer = number(ai, 21, 100)
    freddy_info_raw = state.get("freddy_control")
    freddy_info = freddy_info_raw if isinstance(freddy_info_raw, dict) else freddy_check_state(state)
    if freddy == 7:
        put("freddy", "ФРЕДДИ У СТАВНИ", "Опусти монитор и закрой ставню сейчас.", 98, "window", freddy_timer)
    elif freddy == 6:
        put("freddy", "ФРЕДДИ ИДЁТ К СТАВНЕ", "Заранее опусти монитор и приготовь ставню.", 86 if freddy_timer <= 20 else 74, "window", freddy_timer)
    elif freddy == 5 and freddy_timer <= 30:
        put(
            "freddy",
            "ФРЕДДИ ИДЁТ К СТАВНЕ",
            "Просмотр уже не нужен. Подготовься опустить монитор и закрыть ставню.",
            78 if freddy_timer <= 12 else 66,
            "window",
            freddy_timer,
        )
    elif freddy_info.get("mode") == "due":
        freddy_camera = int(freddy_info.get("expected_camera") or 1)
        node = {1: "stage", 2: "party", 3: "dining", 4: "kitchen"}[freddy_camera]
        delta = int(freddy_info.get("delta") or 0)
        remaining = float(freddy_info.get("remaining_seconds") or max(0.4, delta + FREDDY_CONFIRM_GRACE_SECONDS))
        if freddy_info.get("watching"):
            watched = float(freddy_info.get("watch_seconds") or 0.0)
            put(
                "freddy",
                f"ФРЕДДИ: ДЕРЖИ CAM {freddy_camera:02d}",
                f"Засчитано {watched:.1f} с · держи ещё ~{remaining:.1f} с до «ПРОВЕРЕН».",
                88 if delta <= 1 else 76,
                node,
                freddy_timer,
            )
        else:
            put(
                "freddy",
                f"ФРЕДДИ: СМОТРИ CAM {freddy_camera:02d}",
                f"Держи ~{remaining:.1f} с, пока статус не станет «ПРОВЕРЕН».",
                95 if delta <= 1 else 86 if delta <= 3 else 72,
                node,
                freddy_timer,
            )

    # If nothing requires action, show the two smallest live engine timers. Freddy is
    # deliberately excluded here: his passive route timer used to pin a false/stale
    # warning. His tracker stays green until the real hourly check window opens.
    if not alerts:
        upcoming = [
            (bonnie_timer, "bonnie", bonnie, "Подготовь ALARM; у окна — ставня."),
            (chica_timer, "chica", chica, "При рывке: монитор вниз, ставня, без света."),
            (cream_timer, "cream", cream, "Следи за CAM 07; ALARM только в финальной фазе."),
            (
                berry_countdown if berry_countdown is not None else berry_timer,
                "berry",
                berry,
                "Шкатулка читается напрямую; при предупреждении заранее открой CAM 05.",
            ),
            (slithers_timer, "slithers", slithers, "При появлении не меняй камеру — монитор вниз."),
        ]
        for timer, name, position, action in sorted(upcoming, key=lambda item: item[0])[:2]:
            put(
                name,
                f"СЛЕДОМ: {THREATS[name].label.upper()}",
                f"Счётчик игры {max(0, timer)} · состояние {position}. {action}",
                30,
                timer=timer,
                source="timer",
            )

    ordered = sorted(
        alerts.values(),
        key=lambda item: (-int(item.get("priority", 0)), int(item.get("timer", 999)), list(THREATS).index(str(item["name"]))),
    )
    for item in ordered:
        level, color = threat_level(int(item.get("priority", 0)))
        item["level"] = level
        item["level_color"] = color
        item["steps"] = instruction_steps(item)
    return ordered


def exact_guidance(state: dict[str, object]) -> dict[str, object] | None:
    """Compatibility wrapper for diagnostics that expect one primary instruction."""
    alerts = exact_guidance_all(state)
    return alerts[0] if alerts else None


def priority_plan(alerts: list[dict[str, object]]) -> dict[str, object]:
    """Resolve simultaneous alerts into one safe, ordered 1→2→3 plan."""
    if not alerts:
        level, color = threat_level(0)
        return {
            "score": 0,
            "level": level,
            "color": color,
            "title": "СЕЙЧАС НЕТ АКТИВНОЙ УГРОЗЫ",
            "steps": (
                "Не меняй текущую безопасную позицию без причины.",
                "Следи за шестью индикаторами аниматроников.",
                "Выполняй новую команду сразу после появления панели.",
            ),
            "queue": (),
            "reason": "Точное состояние игры не требует действия.",
        }

    ordered = sorted(
        alerts,
        key=lambda item: (
            -int(item.get("priority", 0)),
            int(item.get("timer", 999)),
            list(THREATS).index(str(item.get("name"))),
        ),
    )
    score = max(int(item.get("priority", 0)) for item in ordered)
    level, color = threat_level(score)
    queue_names = tuple(
        f"{THREAT_ABBR.get(str(item.get('name')), str(item.get('name')).upper())} {int(item.get('priority', 0))}"
        for item in ordered[:3]
    )
    immediate = [item for item in ordered if int(item.get("priority", 0)) >= 80]
    slithers_lock = next(
        (
            item
            for item in immediate
            if item.get("name") == "slithers" and int(item.get("priority", 0)) >= 95
        ),
        None,
    )
    shutter = [
        item
        for item in immediate
        if str(item.get("node")) in {"window", "office"}
        and int(item.get("priority", 0)) >= 90
    ]

    if slithers_lock and shutter:
        names = " + ".join(THREAT_ABBR[str(item["name"])] for item in shutter)
        steps = (
            "НЕ переключай камеру — сразу опусти монитор.",
            f"Закрой ставню: одновременно у окна {names}.",
            "Жди второй звук Slithers и звук ухода у ставни; потом продолжай очередь.",
        )
        title = "СНАЧАЛА: МОНИТОР ВНИЗ → СТАВНЯ"
        reason = "Совмещённый безопасный ответ на запрет переключения камеры и атаку у окна."
    elif slithers_lock:
        next_item = next((item for item in ordered if item is not slithers_lock), None)
        follow = (
            str(next_item.get("action"))
            if next_item is not None
            else "После второго звука вернись к обычному наблюдению."
        )
        steps = (
            "НЕ переключай текущую камеру.",
            "Сразу опусти монитор и жди второй звук Slithers.",
            f"Только после второго звука: {follow}",
        )
        title = "СНАЧАЛА: НЕ ПЕРЕКЛЮЧАЙ КАМЕРУ"
        reason = "Slithers блокирует любые переходы между камерами до второго звука."
    elif shutter:
        next_item = next((item for item in ordered if item not in shutter), None)
        final_step = (
            f"После звука ухода: {str(next_item.get('action'))}"
            if next_item is not None
            else "Держи ставню до звука ухода, затем возобнови наблюдение."
        )
        steps = (
            "Сразу опусти монитор.",
            "Закрой ставню; свет НЕ включай.",
            final_step,
        )
        title = "СНАЧАЛА: МОНИТОР ВНИЗ → СТАВНЯ"
        reason = "Одна ставня закрывает все одновременные угрозы у окна и в офисе."
    else:
        primary = ordered[0]
        steps = tuple(primary.get("steps") or instruction_steps(primary))
        title = f"СНАЧАЛА: {THREAT_ABBR.get(str(primary.get('name')), str(primary.get('name')).upper())}"
        reason = str(primary.get("action", ""))

    return {
        "score": score,
        "level": level,
        "color": color,
        "title": title,
        "steps": steps,
        "queue": queue_names,
        "reason": reason,
    }


def repel_target(alerts: list[dict[str, object]]) -> dict[str, object]:
    """Return a calm, explicit label for whom the player should handle now."""
    ordered = sorted(
        alerts,
        key=lambda item: (
            -int(item.get("priority", 0)),
            int(item.get("timer", 999)),
            list(THREATS).index(str(item.get("name"))),
        ),
    )
    actionable = [
        item
        for item in ordered
        if int(item.get("priority", 0)) >= 60 and bool(item.get("repel", True))
    ]
    if not actionable:
        berry_wait = next(
            (
                item
                for item in ordered
                if item.get("name") == "berry"
                and int(item.get("priority", 0)) >= 60
                and not bool(item.get("repel", True))
            ),
            None,
        )
        if berry_wait is not None:
            return {
                "text": "БЕРРИ: ЖДИ · ПОКА НЕ НАЖИМАТЬ",
                "color": CALM_TARGET_COLORS["berry"],
                "names": (),
                "score": int(berry_wait.get("priority", 0)),
            }
        return {
            "text": "ОТПУГИВАТЬ: НИКОГО",
            "color": GREEN,
            "names": (),
            "score": 0,
        }

    primary = actionable[0]
    selected = [primary]
    for item in actionable[1:]:
        if len(selected) >= 2:
            break
        if int(item.get("priority", 0)) >= 80:
            selected.append(item)
    names = tuple(str(item.get("name")) for item in selected)
    labels = " + ".join(THREAT_ABBR[name] for name in names)
    primary_name = names[0]
    return {
        "text": f"ОТПУГИВАТЬ: {labels}",
        "color": CALM_TARGET_COLORS[primary_name],
        "names": names,
        "score": int(primary.get("priority", 0)),
    }


def calm_sound_signature(alerts: list[dict[str, object]]) -> tuple[tuple[object, ...], ...]:
    """Only true critical alerts may trigger one short, low calm chime."""
    return tuple(
        (item.get("name"), item.get("title"), item.get("priority"))
        for item in alerts
        if int(item.get("priority", 0)) >= CALM_SOUND_THRESHOLD
    )


def exact_threat_statuses(
    state: dict[str, object], alerts: list[dict[str, object]] | None = None
) -> list[dict[str, object]]:
    """Describe all six animatronics, including quiet ones, from live engine values."""
    ai = state.get("ai")
    ai2 = state.get("ai2")
    misc = state.get("misc")
    if not isinstance(ai, list) or not isinstance(ai2, list) or not isinstance(misc, list):
        return []

    def number(values: list, index: int, default: int = 0) -> int:
        value = values[index] if index < len(values) else default
        return int(value) if isinstance(value, (int, float)) else default

    def pressure(timer: int, low: int, high: int, window: int = 40) -> int:
        timer = max(0, timer)
        if timer >= window:
            return low
        return round(low + (window - timer) / window * (high - low))

    live_alerts = alerts if alerts is not None else exact_guidance_all(state)
    alert_by_name = {str(item.get("name")): item for item in live_alerts}
    camera = number(misc, 18)
    freddy_info_raw = state.get("freddy_control")
    freddy_info = freddy_info_raw if isinstance(freddy_info_raw, dict) else freddy_check_state(state)
    berry_info_raw = state.get("berry_music")
    berry_info = berry_info_raw if isinstance(berry_info_raw, dict) else {}

    values = {
        "bonnie": (number(ai, 0), number(ai, 7, 100)),
        "chica": (number(ai, 1), number(ai, 12, 100)),
        "cream": (number(ai, 4), number(ai, 19, 100)),
        "berry": (number(ai2, 0), number(ai2, 1, 100)),
        "slithers": (number(ai, 3), number(ai2, 6, 100)),
        "freddy": (number(ai, 2), number(ai, 21, 100)),
    }

    statuses: list[dict[str, object]] = []
    for name in THREATS:
        phase, timer = values[name]
        passive_action = THREATS[name].short_action
        if name == "bonnie":
            if phase == 0:
                score, state_text = 8, "спокоен"
            elif phase in (4, 5):
                score, state_text = pressure(timer, 52, 76), f"ALARM CAM {5 if phase == 4 else 6:02d}"
            elif phase == 6:
                score, state_text = pressure(timer, 72, 86, 25), "идёт к окну"
            elif phase in (7, 9):
                score, state_text = 96, "у окна"
            else:
                score, state_text = pressure(timer, 24, 58), f"маршрут · фаза {phase}"
        elif name == "chica":
            if phase == 0:
                score, state_text = 8, "спокойна"
            elif phase >= 9:
                score, state_text = 99, "в офисе"
            elif phase == 8:
                score, state_text = 95, "у окна"
            elif phase == 7:
                score, state_text = 84, "бежит к окну"
            elif phase in (5, 6):
                score, state_text = pressure(timer, 52, 70), "приближается"
            else:
                score, state_text = pressure(timer, 22, 52), f"маршрут · фаза {phase}"
        elif name == "cream":
            if phase <= 0:
                score, state_text = 8, "спокоен"
            elif phase == 1:
                score, state_text = pressure(timer, 32, 55), "ранняя фаза"
            elif phase == 2:
                score, state_text = 70, "предфинальная фаза"
            else:
                score, state_text = 96, "финальная фаза"
        elif name == "berry":
            music_mode = str(berry_info.get("mode", "unknown"))
            restart_confirmed = bool(berry_info.get("restart_confirmed"))
            remaining_raw = berry_info.get("remaining_seconds")
            remaining = (
                max(0, int(math.ceil(float(remaining_raw))))
                if isinstance(remaining_raw, (int, float))
                else None
            )
            if remaining is not None:
                timer = remaining
            if music_mode == "healthy" and remaining is not None:
                state_text = (
                    f"ЗАПУЩЕНА ✓ · {remaining}с"
                    if restart_confirmed
                    else f"музыка играет · {remaining}с"
                )
                score = 8
                passive_action = (
                    "Запуск Play Music подтверждён; ничего больше не нажимай."
                    if restart_confirmed
                    else "Ничего не нажимай; жди раннего предупреждения перед концом трека."
                )
            elif music_mode == "warning" and remaining is not None:
                score, state_text = 68, f"шкатулка скоро кончится · {remaining}с"
                passive_action = "Заранее открой CAM 05, но Play Music пока не нажимай."
            elif music_mode == "critical" and remaining is not None:
                score, state_text = 82, f"готовь Play Music · {remaining}с"
                passive_action = "Останься на CAM 05; нажми только после отключения музыки."
            elif music_mode == "expired":
                score = 96 if phase >= 3 or number(ai2, 1, 100) <= 30 else 92
                state_text = f"МУЗЫКА ВЫКЛЮЧЕНА · фаза {phase}"
                passive_action = "CAM 05: нажми Play Music один раз сейчас."
            elif music_mode == "failed" or phase >= 5:
                score, state_text = 99, "покинула сцену"
                passive_action = "Следуй аварийному сигналу игры."
            else:
                score, state_text = 8, f"счётчик музыки подключается · фаза {phase}"
                passive_action = "Не нажимай Play Music без команды; помощник переподключает счётчик."
        elif name == "slithers":
            if phase == 0:
                score, state_text = 8, "не проявился"
            elif phase == -1:
                score, state_text = pressure(timer, 34, 68, 25), "ожидается"
            elif phase in (7, 8):
                score, state_text = 99, "монитор вниз"
            elif 1 <= phase <= 6:
                score = 97 if camera == phase else pressure(timer, 56, 82, 25)
                state_text = f"CAM {phase:02d}"
            else:
                score, state_text = 35, f"фаза {phase}"
        else:  # Freddy
            mode = str(freddy_info.get("mode", "unknown"))
            freddy_camera = freddy_info.get("expected_camera")
            if phase == 7:
                score, state_text = 98, "у ставни"
            elif phase == 6:
                score, state_text = pressure(timer, 74, 88, 25), "идёт к ставне"
            elif mode == "handled":
                score, state_text = 8, "ПРОВЕРЕН"
                confirmed = float(freddy_info.get("confirmed_seconds") or 0.0)
                passive_action = (
                    f"Игра подтвердила просмотр ({confirmed:.1f} с). Выполняй следующую угрозу."
                    if confirmed > 0
                    else "Игра подтвердила просмотр. Выполняй следующую угрозу."
                )
            elif mode == "due" and isinstance(freddy_camera, int):
                delta = int(freddy_info.get("delta") or 0)
                watched = float(freddy_info.get("watch_seconds") or 0.0)
                if freddy_info.get("watching"):
                    score, state_text = (88 if delta <= 1 else 76), f"CAM {freddy_camera:02d} · {watched:.1f}с"
                    passive_action = "Продолжай смотреть до подтверждения игры."
                else:
                    score, state_text = (95 if delta <= 1 else 86 if delta <= 3 else 72), f"CAM {freddy_camera:02d} · через {delta}с"
                    passive_action = f"Открой CAM {freddy_camera:02d} сейчас."
            elif mode == "scheduled":
                delta = max(0, int(freddy_info.get("delta") or 0))
                score, state_text = 8, f"проверка через {delta}с"
                passive_action = (
                    f"Следующая проверка Freddy на отметке {int(freddy_info.get('target_tick') or 30)}/60."
                )
            elif mode == "missed":
                score, state_text = 8, "окно прошло"
                passive_action = "Не зацикливайся на Freddy; жди нового состояния или следующего часа."
            elif mode == "approach":
                score, state_text = pressure(timer, 46, 78, 35), "идёт к ставне"
                passive_action = "Просмотр больше не нужен; готовь ставню."
            elif mode == "final_safe":
                score, state_text = 10, "контроль не нужен"
                passive_action = "На этой фазе отдельный просмотр Freddy не требуется."
            else:
                score, state_text = 8, "проверка не нужна"
                passive_action = "Жди точного окна проверки Freddy."

        alert = alert_by_name.get(name)
        if alert is not None:
            # The active alert is the single canonical urgency value shown in every UI surface.
            score = int(alert.get("priority", 0))
            action = str(alert.get("action", THREATS[name].short_action))
        else:
            action = passive_action
        level, color = threat_level(score)
        statuses.append(
            {
                "name": name,
                "label": THREATS[name].label,
                "score": max(0, min(100, score)),
                "level": level,
                "color": color,
                "state": state_text,
                "phase": phase,
                "timer": max(0, timer),
                "action": action,
            }
        )
    return statuses


class Hotkeys:
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class MSG(ctypes.Structure):
        pass

    MSG._fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_ulong),
        ("pt", POINT),
        ("lPrivate", ctypes.c_ulong),
    ]

    def __init__(self) -> None:
        self.user32 = ctypes.windll.user32
        self.mapping = {1: "toggle", 2: "reset", 3: "cycle", 4: "freddy", 5: "current"}
        self.registered: list[int] = []
        self.actions: queue.SimpleQueue[str] = queue.SimpleQueue()
        self.thread_id = 0
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._message_loop, name="GSAF-Hotkeys", daemon=True)
        self.thread.start()
        self.ready.wait(timeout=2.0)

    def _message_loop(self) -> None:
        kernel32 = ctypes.windll.kernel32
        self.thread_id = int(kernel32.GetCurrentThreadId())
        # Creating the queue before signalling readiness makes PostThreadMessage reliable.
        msg = self.MSG()
        self.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        # Ctrl+Alt combinations avoid collisions with the game's own function keys.
        MOD_ALT = 0x0001
        MOD_CONTROL = 0x0002
        MOD_NOREPEAT = 0x4000
        modifiers = MOD_ALT | MOD_CONTROL | MOD_NOREPEAT
        for ident, vk in ((1, 0x48), (2, 0x52), (3, 0x4E), (4, 0x46), (5, 0x20)):
            if self.user32.RegisterHotKey(None, ident, modifiers, vk):
                self.registered.append(ident)
        self.ready.set()
        while self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self.WM_HOTKEY:
                action = self.mapping.get(int(msg.wParam))
                if action:
                    self.actions.put(action)
        for ident in self.registered:
            self.user32.UnregisterHotKey(None, ident)

    def poll(self) -> list[str]:
        result: list[str] = []
        while True:
            try:
                result.append(self.actions.get_nowait())
            except queue.Empty:
                break
        return result

    def close(self) -> None:
        if self.thread_id:
            self.user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)


class AssistantApp:
    def __init__(self, root: tk.Tk, auto_launch: bool = True) -> None:
        self.root = root
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self.root.overrideredirect(True)
        self.width = 390
        self.height = 850
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(8, screen_w - self.width - 12)
        y = max(8, min(24, screen_h - self.height - 8))
        self.root.geometry(f"{self.width}x{self.height}+{x}+{y}")

        self.night = 5
        self.running = False
        self.shift_started = time.monotonic()
        self.due: dict[str, float] = {}
        self.handled_count: dict[str, int] = {name: 0 for name in THREATS}
        self.current_name: str | None = None
        self.last_beep = 0.0
        self.hidden = False
        self.drag_x = 0
        self.drag_y = 0
        self.game_running = False
        self.hotkeys = Hotkeys()
        self.observer = GameObserver()
        self.memory = FusionMemoryObserver()
        self.memory_state: dict[str, object] = {"exact": False, "status": "waiting"}
        self.memory_guidance: dict[str, object] | None = None
        self.memory_exact = False
        self.exact_signature = ""
        self.observer_scene = "missing"
        self.observer_camera: int | None = None
        self.in_gameplay = False
        self.gameplay_streak = 0
        self.non_gameplay_streak = 0
        self.detection_streak: dict[str, int] = {name: 0 for name in THREATS}
        self.detection_last_seen: dict[str, float] = {name: 0.0 for name in THREATS}
        self.detection_info: dict[str, dict[str, object]] = {}
        self.active_detections: set[str] = set()
        self.direct_name: str | None = None
        self.random = random.Random(7505)

        self._build_ui()
        self._set_night(5, reset=False)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(50, self._pump_hotkeys)
        self.root.after(150, self._tick)
        self.root.after(100, self._pump_observer)
        self.root.after(80, self._pump_memory)
        if auto_launch:
            self.root.after(800, self.start_game)

    def _frame(self, parent, **kwargs) -> tk.Frame:
        return tk.Frame(parent, bg=kwargs.pop("bg", PANEL), **kwargs)

    def _label(self, parent, text="", **kwargs) -> tk.Label:
        return tk.Label(parent, text=text, bg=kwargs.pop("bg", PANEL), fg=kwargs.pop("fg", TEXT), **kwargs)

    def _button(self, parent, text, command, **kwargs) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command, bg=kwargs.pop("bg", PANEL_2),
            fg=kwargs.pop("fg", TEXT), activebackground=kwargs.pop("activebackground", "#242d3b"),
            activeforeground=TEXT, relief="flat", bd=0, cursor="hand2",
            font=kwargs.pop("font", ("Segoe UI", 9, "bold")), **kwargs,
        )

    def _build_ui(self) -> None:
        header = self._frame(self.root, bg="#0d1118", height=48)
        header.pack(fill="x")
        header.pack_propagate(False)
        header.bind("<ButtonPress-1>", self._drag_start)
        header.bind("<B1-Motion>", self._drag_move)

        title = self._label(header, "GSAF  //  ТОЧНЫЙ ПОМОЩНИК", bg="#0d1118", font=("Segoe UI", 11, "bold"))
        title.pack(side="left", padx=14)
        title.bind("<ButtonPress-1>", self._drag_start)
        title.bind("<B1-Motion>", self._drag_move)
        self._button(header, "×", self.close, bg="#0d1118", fg=MUTED, font=("Segoe UI", 14, "bold"), width=3).pack(side="right")
        self._button(header, "—", self.toggle, bg="#0d1118", fg=MUTED, font=("Segoe UI", 12, "bold"), width=3).pack(side="right")

        status_row = self._frame(self.root, bg=BG, height=42)
        status_row.pack(fill="x", padx=12, pady=(9, 4))
        status_row.pack_propagate(False)
        self.game_dot = self._label(status_row, "●", bg=BG, fg=ORANGE, font=("Segoe UI", 12))
        self.game_dot.pack(side="left")
        self.game_label = self._label(status_row, "проверяю игру…", bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self.game_label.pack(side="left", padx=(4, 0))
        self.timer_label = self._label(status_row, "00:00", bg=BG, fg=TEXT, font=("Consolas", 12, "bold"))
        self.timer_label.pack(side="right")

        night_row = self._frame(self.root, bg=BG)
        night_row.pack(fill="x", padx=12, pady=(0, 7))
        self.auto_label = self._label(
            night_row, "AUTO", bg="#173629", fg=GREEN,
            font=("Segoe UI", 8, "bold"), padx=8,
        )
        self.auto_label.pack(side="left", fill="y", padx=(0, 4))
        self.night_buttons: list[tk.Button] = []
        for value in range(1, 6):
            button = self._button(night_row, str(value), lambda n=value: self._set_night(n), width=4)
            button.pack(side="left", expand=True, fill="x", padx=(0 if value == 1 else 3, 0))
            self.night_buttons.append(button)

        self.alert_frame = self._frame(self.root, bg=PANEL_2, height=100)
        self.alert_frame.pack(fill="x", padx=12, pady=(0, 8))
        self.alert_frame.pack_propagate(False)
        self.alert_tag = self._label(self.alert_frame, "АВТОПИЛОТ ГОТОВ", bg=PANEL_2, fg=MUTED, font=("Segoe UI", 8, "bold"))
        self.alert_tag.pack(anchor="w", padx=12, pady=(9, 1))
        self.alert_name = self._label(self.alert_frame, "—", bg=PANEL_2, font=("Segoe UI", 18, "bold"), anchor="w")
        self.alert_name.pack(fill="x", padx=12)
        self.alert_action = self._label(self.alert_frame, "Начало ночи определится автоматически", bg=PANEL_2, fg=MUTED, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=350)
        self.alert_action.pack(fill="x", padx=12, pady=(1, 7))

        controls = self._frame(self.root, bg=BG)
        controls.pack(fill="x", padx=12, pady=(0, 8))
        self.current_button = self._button(controls, "ЗАПАС: ГОТОВО", self.handle_current, bg="#264a3b", activebackground="#32614d", font=("Segoe UI", 8, "bold"))
        self.current_button.pack(side="left", expand=True, fill="x", ipady=7, padx=(0, 4))
        self.freddy_button = self._button(controls, "ЗАПАС: ФРЕДДИ", self.mark_freddy, bg="#5a2630", activebackground="#78333f", font=("Segoe UI", 8, "bold"))
        self.freddy_button.pack(side="left", expand=True, fill="x", ipady=7, padx=(4, 0))

        map_panel = self._frame(self.root, bg=PANEL, height=314)
        map_panel.pack(fill="x", padx=12, pady=(0, 8))
        map_panel.pack_propagate(False)
        map_title = self._label(map_panel, "АВТО-КАРТА И ПРИОРИТЕТЫ", fg=MUTED, font=("Segoe UI", 8, "bold"))
        map_title.pack(anchor="w", padx=12, pady=(8, 0))
        self.canvas = tk.Canvas(map_panel, width=350, height=280, bg=PANEL, highlightthickness=0)
        self.canvas.pack(padx=8)
        self._draw_map()

        roster = self._frame(self.root, bg=PANEL, height=178)
        roster.pack(fill="x", padx=12, pady=(0, 8))
        roster.pack_propagate(False)
        roster_header = self._frame(roster, bg=PANEL)
        roster_header.pack(fill="x", padx=10, pady=(8, 4))
        self._label(roster_header, "РУЧНОЙ ЗАПАСНОЙ РЕЖИМ", fg=MUTED, font=("Segoe UI", 8, "bold")).pack(side="left")
        self._label(roster_header, "обычно не нужен", fg=MUTED, font=("Segoe UI", 8)).pack(side="right")
        self.threat_buttons: dict[str, tk.Button] = {}
        grid = self._frame(roster, bg=PANEL)
        grid.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        for index, threat in enumerate(THREATS.values()):
            button = self._button(
                grid, f"{threat.hotkey}  {threat.label}",
                lambda name=threat.name: self.mark_handled(name),
                font=("Segoe UI", 8, "bold"),
            )
            button.grid(row=index // 3, column=index % 3, sticky="nsew", padx=3, pady=3, ipady=5)
            grid.columnconfigure(index % 3, weight=1)
            grid.rowconfigure(index // 3, weight=1)
            self.threat_buttons[threat.name] = button

        footer = self._frame(self.root, bg=BG)
        footer.pack(fill="x", padx=12, pady=(0, 8))
        self._button(footer, "ЗАПАС: СИНХРОНИЗИРОВАТЬ", lambda: self.reset_shift(), font=("Segoe UI", 7, "bold")).pack(side="left", expand=True, fill="x", ipady=5, padx=(0, 3))
        self._button(footer, "ЗАПУСТИТЬ ИГРУ", self.start_game, font=("Segoe UI", 8, "bold")).pack(side="left", expand=True, fill="x", ipady=5, padx=(3, 0))

        note = self._label(self.root, "ПАМЯТЬ (чтение) → точное состояние → команда; экран — запасной режим", bg=BG, fg="#6f7885", font=("Segoe UI", 7))
        note.pack(pady=(0, 5))

    def _drag_start(self, event) -> None:
        self.drag_x = event.x_root - self.root.winfo_x()
        self.drag_y = event.y_root - self.root.winfo_y()

    def _drag_move(self, event) -> None:
        x = event.x_root - self.drag_x
        y = event.y_root - self.drag_y
        self.root.geometry(f"+{x}+{y}")

    def _draw_map(self) -> None:
        self.canvas.delete("all")
        for left, right in MAP_EDGES:
            x1, y1, _ = MAP_NODES[left]
            x2, y2, _ = MAP_NODES[right]
            self.canvas.create_line(x1, y1, x2, y2, fill="#303947", width=2)
        for name, (x, y, label) in MAP_NODES.items():
            size = 24 if name not in ("window", "office", "camera") else 28
            fill = "#1c2430"
            outline = "#3a4657"
            self.canvas.create_oval(x - size, y - 17, x + size, y + 17, fill=fill, outline=outline, width=2, tags=(f"node_{name}",))
            self.canvas.create_text(x, y, text=label, fill=MUTED, font=("Segoe UI", 6, "bold"), justify="center", tags=(f"text_{name}",))

    def _set_night(self, night: int, reset: bool = True) -> None:
        self.night = night
        self.auto_label.configure(text=f"AUTO · НОЧЬ {night}")
        for index, button in enumerate(self.night_buttons, start=1):
            if index == night:
                button.configure(bg="#3d5575", activebackground="#4b6991")
            else:
                button.configure(bg=PANEL_2, activebackground="#242d3b")
        for name, button in self.threat_buttons.items():
            threat = THREATS[name]
            enabled = night >= threat.first_night
            button.configure(state="normal" if enabled else "disabled", fg=TEXT if enabled else "#555d68")
        self.freddy_button.configure(state="normal" if night == 5 else "disabled")
        if reset:
            self.reset_shift()

    def reset_shift(self, beep: bool = True, automatic: bool = False) -> None:
        now = time.monotonic()
        self.shift_started = now
        self.running = True
        self.due.clear()
        self.handled_count = {name: 0 for name in THREATS}
        self.random.seed(7505 + self.night)
        for name, threat in THREATS.items():
            if self.night >= threat.first_night:
                self.due[name] = now + threat.first_delay(self.night)
        self.direct_name = None
        self.active_detections.clear()
        if automatic:
            self.auto_label.configure(text=f"AUTO · НОЧЬ {self.night}", bg="#173629", fg=GREEN)
        if beep:
            winsound.MessageBeep(winsound.MB_OK)

    def _next_delay(self, threat: Threat, count: int) -> float:
        base = threat.interval(self.night)
        # Stable, small variance prevents all reminders from bunching together.
        jitter = (self.random.random() - 0.5) * min(8.0, base * 0.22)
        fatigue = min(5.0, count * 0.35)
        return max(7.0, base + jitter - fatigue)

    def mark_handled(self, name: str, quiet: bool = False) -> None:
        if name not in self.due:
            return
        self.handled_count[name] += 1
        self.due[name] = time.monotonic() + self._next_delay(THREATS[name], self.handled_count[name])
        if not quiet:
            winsound.MessageBeep(winsound.MB_OK)

    def mark_freddy(self) -> None:
        if self.night != 5:
            return
        self.mark_handled("freddy")

    def handle_current(self) -> None:
        if self.current_name:
            self.mark_handled(self.current_name)

    def _priority(self, now: float) -> tuple[str | None, float]:
        if not self.due:
            return None, 0.0
        name = min(self.due, key=lambda key: self.due[key] - now)
        return name, self.due[name] - now

    def _tick(self) -> None:
        if self.memory_guidance:
            now = time.monotonic()
            elapsed = max(0, int(now - self.shift_started))
            self.timer_label.configure(text=f"{elapsed // 60:02d}:{elapsed % 60:02d}")
            guidance = self.memory_guidance
            name = str(guidance["name"])
            urgent = bool(guidance.get("urgent"))
            frame_bg = "#551923" if urgent else "#183226"
            self.alert_frame.configure(bg=frame_bg)
            self.alert_tag.configure(
                text="ТОЧНОЕ СОСТОЯНИЕ ИГРЫ — ДЕЙСТВУЙ" if urgent else "ТОЧНОЕ СОСТОЯНИЕ ИГРЫ",
                bg=frame_bg,
                fg="#ffb2b9" if urgent else GREEN,
            )
            self.alert_name.configure(text=str(guidance["title"]), bg=frame_bg, fg=TEXT)
            self.alert_action.configure(text=str(guidance["action"]), bg=frame_bg, fg="#f0f3f6")
            self.current_name = name
            self.current_button.configure(text="ПАМЯТЬ: ТОЛЬКО ЧТЕНИЕ")
            self._update_map(name, -1.0, str(guidance.get("node") or THREATS[name].node))
            signature = f"{name}|{guidance['title']}|{guidance['action']}|{urgent}"
            if signature != self.exact_signature:
                self.exact_signature = signature
                if urgent:
                    try:
                        winsound.Beep(1250, 150)
                        winsound.Beep(1550, 170)
                    except RuntimeError:
                        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            self.root.after(120, self._tick)
            return
        if not self.running:
            self.timer_label.configure(text="--:--")
            self.alert_frame.configure(bg=PANEL_2)
            self.alert_tag.configure(text="АВТОПИЛОТ", bg=PANEL_2, fg=GREEN)
            self.alert_name.configure(text="Жду начало ночи", bg=PANEL_2, fg=TEXT)
            self.alert_action.configure(
                text="Запусти ночь — синхронизация произойдёт сама.",
                bg=PANEL_2,
                fg=MUTED,
            )
            self.root.after(200, self._tick)
            return
        now = time.monotonic()
        elapsed = max(0, int(now - self.shift_started))
        self.timer_label.configure(text=f"{elapsed // 60:02d}:{elapsed % 60:02d}")

        if self.direct_name:
            name = self.direct_name
            threat = THREATS[name]
            info = self.detection_info.get(name, {})
            frame_bg = "#551923" if name == "freddy" else "#4b251d"
            self.alert_frame.configure(bg=frame_bg)
            self.alert_tag.configure(
                text="РАСПОЗНАНО НА ЭКРАНЕ — ДЕЙСТВУЙ",
                bg=frame_bg,
                fg="#ffb2b9",
            )
            self.alert_name.configure(
                text=f"{threat.label}  ·  СЕЙЧАС", bg=frame_bg, fg=TEXT
            )
            self.alert_action.configure(
                text=self._live_action(name, info), bg=frame_bg, fg="#f5d8d8"
            )
            self.current_name = name
            self.current_button.configure(text=f"ЗАПАС: {threat.label} ГОТОВО")
            self._update_map(name, -1.0)
            if now - self.last_beep > 2.8:
                self.last_beep = now
                try:
                    winsound.Beep(1250 if name == "freddy" else 930, 170)
                except RuntimeError:
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            self.root.after(200, self._tick)
            return

        for name, due_at in list(self.due.items()):
            if now - due_at > AUTO_OVERDUE_GRACE:
                self.handled_count[name] += 1
                self.due[name] = now + self._next_delay(
                    THREATS[name], self.handled_count[name]
                )
        name, remaining = self._priority(now)
        self.current_name = name
        if name:
            threat = THREATS[name]
            urgent = remaining <= 0
            soon = remaining <= 6
            if urgent:
                frame_bg = "#531923" if name == "freddy" else "#4b251d"
                tag = "СРОЧНО — ДЕЙСТВУЙ СЕЙЧАС"
                label = f"{threat.label}  ·  СЕЙЧАС"
                tag_color = "#ffadb5" if name == "freddy" else "#ffc0a2"
            elif soon:
                frame_bg = "#4a3a18"
                tag = "ПРИГОТОВЬСЯ"
                label = f"{threat.label}  ·  {max(0, math.ceil(remaining))} с"
                tag_color = "#ffe39a"
            else:
                frame_bg = PANEL_2
                tag = "СЛЕДУЮЩАЯ ВЕРОЯТНАЯ УГРОЗА"
                label = f"{threat.label}  ·  ≈{math.ceil(remaining)} с"
                tag_color = MUTED
            self.alert_frame.configure(bg=frame_bg)
            self.alert_tag.configure(text=tag, bg=frame_bg, fg=tag_color)
            self.alert_name.configure(text=label, bg=frame_bg, fg=TEXT)
            self.alert_action.configure(text=threat.short_action, bg=frame_bg, fg="#d4d9e0")
            self.current_button.configure(text=f"ЗАПАС: {threat.label} ГОТОВО")
            if urgent and now - self.last_beep > (3.0 if name == "freddy" else 5.0):
                self.last_beep = now
                try:
                    if name == "freddy":
                        winsound.Beep(1150, 170)
                        winsound.Beep(1450, 170)
                    else:
                        winsound.Beep(850, 150)
                except RuntimeError:
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            self._update_map(name, remaining)
        self.root.after(200, self._tick)

    def _update_map(self, current: str, remaining: float, target_override: str | None = None) -> None:
        for name, (_x, _y, _label) in MAP_NODES.items():
            self.canvas.itemconfigure(f"node_{name}", outline="#3a4657", width=2)
            self.canvas.itemconfigure(f"text_{name}", fill=MUTED)
        self.canvas.delete("threat_dot")
        target = target_override or THREATS[current].node
        self.canvas.itemconfigure(f"node_{target}", outline=THREATS[current].color, width=3)
        self.canvas.itemconfigure(f"text_{target}", fill=TEXT)
        now = time.monotonic()
        for name, due_at in self.due.items():
            threat = THREATS[name]
            x, y, _ = MAP_NODES[threat.node]
            delta = due_at - now
            radius = 7 if delta <= 0 else 5
            color = RED if delta <= 0 else threat.color
            offset = (list(THREATS).index(name) % 3 - 1) * 10
            self.canvas.create_oval(
                x + offset - radius, y - 29 - radius, x + offset + radius, y - 29 + radius,
                fill=color, outline="#10131a", width=1, tags="threat_dot",
            )
            self.canvas.create_text(
                x + offset, y - 42, text=threat.label[:2].upper(), fill=color,
                font=("Segoe UI", 6, "bold"), tags="threat_dot",
            )

    def _pump_hotkeys(self) -> None:
        for action in self.hotkeys.poll():
            if action == "toggle":
                self.toggle()
            elif action == "reset":
                self.reset_shift()
            elif action == "cycle":
                self._set_night(1 if self.night == 5 else self.night + 1)
            elif action == "freddy":
                self.mark_freddy()
            elif action == "current":
                self.handle_current()
        self.root.after(50, self._pump_hotkeys)

    @staticmethod
    def _live_action(name: str, info: dict[str, object]) -> str:
        ident = str(info.get("id", ""))
        if name == "bonnie" and ident == "bonnie_distract":
            return "Нажми ALARM сейчас — на экране DISTRACT HIM."
        if name == "bonnie":
            return "Bonnie у окна: опусти ставню и держи до звука ухода."
        if name == "chica":
            return "Закрой ставню. Свет НЕ включай."
        if name == "cream":
            return "Финальная фаза на CAM 07 — нажми ALARM сейчас."
        if name == "berry":
            return "PLAYROOM 05: держи музыку включённой."
        if name == "slithers":
            return "Не переключай камеру. Опусти монитор и жди, пока он уйдёт."
        if name == "freddy":
            return "СМОТРИ НА ФРЕДДИ и удерживай взгляд, пока он не остановится."
        return THREATS[name].short_action

    def _pump_observer(self) -> None:
        result = self.observer.poll_latest()
        if result is not None:
            now = time.monotonic()
            self.game_running = bool(result.get("window_found"))
            scene = str(result.get("scene", "error"))
            camera = result.get("camera")
            self.observer_scene = scene
            self.observer_camera = int(camera) if camera is not None else None

            if scene == "missing":
                self.game_dot.configure(fg=ORANGE)
                self.game_label.configure(text="AUTO: жду запуск игры", fg=MUTED)
            elif scene == "other":
                self.game_dot.configure(fg=BLUE)
                self.game_label.configure(text="AUTO: меню · жду ночь", fg=BLUE)
            elif scene == "office":
                self.game_dot.configure(fg=GREEN)
                self.game_label.configure(text="AUTO: офис распознан", fg=GREEN)
            elif scene == "camera":
                suffix = f" · CAM {self.observer_camera:02d}" if self.observer_camera else ""
                self.game_dot.configure(fg=GREEN)
                self.game_label.configure(text=f"AUTO: камеры{suffix}", fg=GREEN)
            else:
                self.game_dot.configure(fg=RED)
                self.game_label.configure(text="AUTO: проверяю изображение…", fg=MUTED)

            gameplay_now = scene in ("office", "camera")
            if gameplay_now:
                self.gameplay_streak += 1
                self.non_gameplay_streak = 0
                if not self.in_gameplay and self.gameplay_streak >= 2:
                    self.in_gameplay = True
                    self.reset_shift(beep=False, automatic=True)
            else:
                self.gameplay_streak = 0
                if self.in_gameplay:
                    self.non_gameplay_streak += 1
                    if self.non_gameplay_streak >= 5:
                        self.in_gameplay = False
                        self.running = False
                        self.due.clear()
                        self.direct_name = None
                        self.active_detections.clear()
                else:
                    self.non_gameplay_streak = 0

            seen_now: set[str] = set()
            for detected in result.get("detections", []):
                name = str(detected.get("threat", ""))
                if name not in THREATS:
                    continue
                seen_now.add(name)
                gap = now - self.detection_last_seen[name]
                self.detection_streak[name] = (
                    self.detection_streak[name] + 1
                    if gap < OBSERVER_INTERVAL * 2.6
                    else 1
                )
                self.detection_last_seen[name] = now
                self.detection_info[name] = detected
                if self.detection_streak[name] >= 2:
                    self.active_detections.add(name)

            for name in THREATS:
                if name not in seen_now:
                    self.detection_streak[name] = 0

            for name in tuple(self.active_detections):
                if now - self.detection_last_seen[name] > 1.45:
                    self.active_detections.remove(name)
                    if self.running and name in self.due:
                        self.mark_handled(name, quiet=True)

            signal_priority = {"urgent": 3, "watch": 2, "visible": 1}
            if self.active_detections:
                self.direct_name = max(
                    self.active_detections,
                    key=lambda name: (
                        signal_priority.get(
                            str(self.detection_info.get(name, {}).get("signal", "visible")),
                            0,
                        ),
                        float(self.detection_info.get(name, {}).get("score", 0.0)),
                    ),
                )
            else:
                self.direct_name = None

        self.root.after(100, self._pump_observer)

    def _pump_memory(self) -> None:
        result = self.memory.poll_latest()
        if result is not None:
            was_exact = self.memory_exact
            self.memory_state = result
            self.memory_exact = bool(result.get("exact"))
            self.memory_guidance = exact_guidance(result)
            if self.memory_exact:
                if not was_exact:
                    self.in_gameplay = True
                    self.reset_shift(beep=False, automatic=True)
                self.running = True
            elif was_exact:
                self.memory_guidance = None
                self.exact_signature = ""

        if self.memory_exact:
            self.game_dot.configure(fg=GREEN)
            self.game_label.configure(text="ТОЧНО: читаю состояние игры", fg=GREEN)
            self.auto_label.configure(text=f"ПАМЯТЬ · НОЧЬ {self.night}", bg="#173629", fg=GREEN)
        else:
            status = str(self.memory_state.get("status", "waiting"))
            if status in {"not_gameplay", "waiting", "reconnect"} and self.game_running:
                self.game_label.configure(text="AUTO: жду начало ночи · экран включён", fg=BLUE)
        self.root.after(80, self._pump_memory)

    def toggle(self) -> None:
        if self.hidden:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.hidden = False
        else:
            self.root.withdraw()
            self.hidden = True

    def start_game(self) -> None:
        ok, message = launch_game()
        if not ok:
            messagebox.showerror(APP_NAME, message, parent=self.root)
        self.game_dot.configure(fg=BLUE if ok else RED)
        self.game_label.configure(text=message, fg=BLUE if ok else RED)

    def _update_game_status(self, temporary: str | None = None) -> None:
        self.game_running = game_window_exists()
        if self.game_running:
            self.game_dot.configure(fg=GREEN)
            self.game_label.configure(text="игра запущена", fg=GREEN)
        else:
            self.game_dot.configure(fg=ORANGE)
            self.game_label.configure(text=temporary or "игра не запущена", fg=MUTED)

    def close(self) -> None:
        self.observer.close()
        self.memory.close()
        self.hotkeys.close()
        self.root.destroy()


class CompactAssistantApp:
    """Small no-focus overlay driven by independent, simultaneous threat alerts."""

    WIDTH = 356
    CARD_HEIGHT = 62
    WAIT_HEIGHT = 213
    BASE_HEIGHT = 280
    DETAIL_WIDTH = 300
    DETAIL_HEIGHT = 186

    def __init__(self, root: tk.Tk, auto_launch: bool = True, demo: bool = False) -> None:
        self.root = root
        self.demo = demo
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.98)
        self.root.resizable(False, False)
        self.root.overrideredirect(not demo)

        screen_w = self.root.winfo_screenwidth()
        self.window_x = max(8, screen_w - self.WIDTH - 14)
        self.window_y = 18
        self.window_height = self.BASE_HEIGHT
        self.root.geometry(f"{self.WIDTH}x{self.window_height}+{self.window_x}+{self.window_y}")

        self.drag_x = 0
        self.drag_y = 0
        self.hidden = False
        self.hotkeys = Hotkeys()
        self.observer = GameObserver(start=not demo)
        self.memory = FusionMemoryObserver(start=not demo)
        self.freddy_tracker = FreddyWatchTracker()
        self.berry_music_tracker = BerryMusicTracker()
        self.memory_state: dict[str, object] = {"exact": False, "status": "waiting"}
        self.memory_exact = False
        self.memory_misses = 0
        self.last_memory_at = 0.0
        self.pending_signature: tuple = ()
        self.pending_samples = 0
        self.alerts: list[dict[str, object]] = []
        self.last_alert_signature: tuple = ()
        self.last_urgent_signature: tuple = ()
        self.observer_scene = "missing"
        self.observer_camera: int | None = None
        self.detection_streak = {name: 0 for name in THREATS}
        self.detection_last_seen = {name: 0.0 for name in THREATS}
        self.detection_info: dict[str, dict[str, object]] = {}
        self.active_detections: set[str] = set()
        self.threat_statuses: list[dict[str, object]] = []
        self.detail_visible = False

        self._build_ui()
        self._build_detail_ui()
        self._render_waiting()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        if not demo:
            self.root.after(0, self._apply_no_activate_style)
            self.root.after(0, lambda: self._apply_no_activate_style(self.detail))
        self.root.after(50, self._pump_hotkeys)
        if demo:
            self.root.after(120, self._show_demo)
        else:
            self.root.after(80, self._pump_memory)
            self.root.after(100, self._pump_observer)
            self.root.after(250, self._watchdog)
            if auto_launch:
                self.root.after(700, self.start_game)

    def _frame(self, parent, **kwargs) -> tk.Frame:
        return tk.Frame(parent, bg=kwargs.pop("bg", PANEL), **kwargs)

    def _label(self, parent, text="", **kwargs) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            bg=kwargs.pop("bg", PANEL),
            fg=kwargs.pop("fg", TEXT),
            **kwargs,
        )

    def _button(self, parent, text, command, **kwargs) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=kwargs.pop("bg", "#0d1118"),
            fg=kwargs.pop("fg", MUTED),
            activebackground=kwargs.pop("activebackground", "#202835"),
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            cursor="hand2",
            font=kwargs.pop("font", ("Segoe UI", 10, "bold")),
            **kwargs,
        )

    def _build_ui(self) -> None:
        outer = self._frame(self.root, bg="#232b38", padx=1, pady=1)
        outer.pack(fill="both", expand=True)
        body = self._frame(outer, bg=BG)
        body.pack(fill="both", expand=True)

        header = self._frame(body, bg="#0d1118", height=34)
        header.pack(fill="x")
        header.pack_propagate(False)
        for widget in (header,):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)

        accent = self._frame(header, bg=PURPLE, width=4)
        accent.pack(side="left", fill="y")
        title = self._label(
            header,
            "GSAF  ·  LIVE",
            bg="#0d1118",
            font=("Segoe UI Semibold", 9, "bold"),
        )
        title.pack(side="left", padx=(10, 7))
        title.bind("<ButtonPress-1>", self._drag_start)
        title.bind("<B1-Motion>", self._drag_move)
        self.mode_badge = self._label(
            header,
            "ПОДКЛЮЧЕНИЕ",
            bg="#252c39",
            fg=MUTED,
            font=("Segoe UI", 6, "bold"),
            padx=5,
            pady=2,
        )
        self.mode_badge.pack(side="left")
        self._button(header, "×", self.close, width=3, font=("Segoe UI", 13, "bold")).pack(side="right", fill="y")
        self._button(header, "—", self.toggle, width=3, font=("Segoe UI", 11, "bold")).pack(side="right", fill="y")

        status = self._frame(body, bg=BG, height=28)
        status.pack(fill="x", padx=10)
        status.pack_propagate(False)
        self.status_dot = self._label(status, "●", bg=BG, fg=ORANGE, font=("Segoe UI", 10))
        self.status_dot.pack(side="left")
        self.status_label = self._label(status, "Жду игру", bg=BG, fg=MUTED, font=("Segoe UI", 8))
        self.status_label.pack(side="left", padx=(4, 0))
        self.clock_label = self._label(status, "-- AM", bg=BG, fg=TEXT, font=("Consolas", 9, "bold"))
        self.clock_label.pack(side="right")

        self.summary = self._frame(body, bg="#15201d", height=34)
        self.summary.pack(fill="x", padx=8, pady=(0, 4))
        self.summary.pack_propagate(False)
        self.summary_label = self._label(
            self.summary,
            "ОТПУГИВАТЬ: НИКОГО",
            bg="#15201d",
            fg=GREEN,
            font=("Segoe UI Semibold", 9, "bold"),
            anchor="w",
            justify="left",
            wraplength=324,
        )
        self.summary_label.pack(fill="both", expand=True, padx=9)

        self.cards_host = self._frame(body, bg=BG)
        self.cards_host.pack(fill="x", padx=8)
        self.cards: list[dict[str, tk.Widget]] = []
        for _index in range(3):
            frame = self._frame(self.cards_host, bg=PANEL_2, height=self.CARD_HEIGHT)
            frame.pack(fill="x", pady=(0, 4))
            frame.pack_propagate(False)
            bar = self._frame(frame, bg=BLUE, width=4)
            bar.pack(side="left", fill="y")
            number = self._label(frame, "1", bg=PANEL_2, fg=MUTED, font=("Consolas", 9, "bold"), width=2)
            number.pack(side="left", fill="y", padx=(4, 1))
            text_host = self._frame(frame, bg=PANEL_2)
            text_host.pack(side="left", fill="both", expand=True, padx=(0, 7), pady=4)
            name = self._label(
                text_host,
                "—",
                bg=PANEL_2,
                font=("Segoe UI Semibold", 9, "bold"),
                anchor="w",
            )
            name.pack(fill="x")
            action = self._label(
                text_host,
                "",
                bg=PANEL_2,
                fg="#c9d1dc",
                font=("Segoe UI", 8),
                anchor="w",
                justify="left",
                wraplength=268,
            )
            action.pack(fill="x", pady=(1, 0))
            self.cards.append({"frame": frame, "bar": bar, "number": number, "host": text_host, "name": name, "action": action})

        tracker_title = self._label(
            body,
            "ВСЕ АНИМАТРОНИКИ · УРОВЕНЬ УГРОЗЫ",
            bg=BG,
            fg="#697381",
            font=("Segoe UI", 7, "bold"),
            anchor="w",
        )
        tracker_title.pack(fill="x", padx=10, pady=(0, 1))
        self.tracker_host = self._frame(body, bg=BG)
        self.tracker_host.pack(fill="x", padx=8, pady=(0, 3))
        self.tracker_widgets: dict[str, dict[str, tk.Widget]] = {}
        threat_names = list(THREATS)
        for row_index in range(2):
            row = self._frame(self.tracker_host, bg=BG)
            row.pack(fill="x", pady=(0, 2))
            for name in threat_names[row_index * 3 : row_index * 3 + 3]:
                chip = self._frame(row, bg="#111820", height=32, highlightthickness=1, highlightbackground="#202936")
                chip.pack(side="left", fill="x", expand=True, padx=(0, 3))
                chip.pack_propagate(False)
                label = self._label(
                    chip,
                    f"{THREAT_ABBR[name]} --\nнет данных",
                    bg="#111820",
                    fg=MUTED,
                    font=("Segoe UI", 6, "bold"),
                    justify="left",
                    anchor="w",
                )
                label.pack(fill="both", expand=True, padx=5, pady=1)
                self.tracker_widgets[name] = {"frame": chip, "label": label}

        footer = self._frame(body, bg=BG, height=18)
        footer.pack(fill="x", padx=10)
        footer.pack_propagate(False)
        self.more_label = self._label(footer, "", bg=BG, fg=MUTED, font=("Segoe UI", 7))
        self.more_label.pack(side="left")
        self._label(footer, "Ctrl+Alt+H", bg=BG, fg="#697381", font=("Segoe UI", 7)).pack(side="right")

    def _build_detail_ui(self) -> None:
        self.detail = tk.Toplevel(self.root)
        self.detail.withdraw()
        self.detail.configure(bg="#252f3d")
        self.detail.attributes("-topmost", True)
        self.detail.attributes("-alpha", 0.99)
        self.detail.resizable(False, False)
        self.detail.overrideredirect(True)

        outer = self._frame(self.detail, bg="#252f3d", padx=1, pady=1)
        outer.pack(fill="both", expand=True)
        body = self._frame(outer, bg="#0b0e14")
        body.pack(fill="both", expand=True)

        header = self._frame(body, bg="#111722", height=28)
        header.pack(fill="x")
        header.pack_propagate(False)
        self.detail_accent = self._frame(header, bg=GREEN, width=4)
        self.detail_accent.pack(side="left", fill="y")
        self._label(
            header,
            "СПОКОЙНЫЙ ПЛАН · 1 → 2 → 3",
            bg="#111722",
            font=("Segoe UI Semibold", 7, "bold"),
        ).pack(side="left", padx=8)
        self.detail_level = self._label(
            header,
            "УГРОЗА --",
            bg="#252c39",
            fg=MUTED,
            font=("Segoe UI", 7, "bold"),
            padx=6,
            pady=1,
        )
        self.detail_level.pack(side="right", padx=6)

        self.detail_title = self._label(
            body,
            "СНАЧАЛА: —",
            bg="#0b0e14",
            fg=TEXT,
            font=("Segoe UI Semibold", 9, "bold"),
            anchor="w",
            justify="left",
            wraplength=276,
        )
        self.detail_title.pack(fill="x", padx=9, pady=(5, 3))

        self.detail_steps: list[dict[str, tk.Widget]] = []
        for index in range(3):
            row = self._frame(body, bg="#151b25", height=34)
            row.pack(fill="x", padx=7, pady=(0, 2))
            row.pack_propagate(False)
            number = self._label(
                row,
                str(index + 1),
                bg="#151b25",
                fg=RED,
                font=("Consolas", 11, "bold"),
                width=2,
            )
            number.pack(side="left", fill="y", padx=(4, 1))
            text = self._label(
                row,
                "—",
                bg="#151b25",
                fg="#e7ebf1",
                font=("Segoe UI", 8, "bold" if index == 0 else "normal"),
                anchor="w",
                justify="left",
                wraplength=240,
            )
            text.pack(side="left", fill="both", expand=True, padx=(0, 8))
            self.detail_steps.append({"row": row, "number": number, "text": text})

        self.detail_queue = self._label(
            body,
            "ОЧЕРЕДЬ: —",
            bg="#0b0e14",
            fg=MUTED,
            font=("Segoe UI", 7),
            anchor="w",
            justify="left",
            wraplength=276,
        )
        self.detail_queue.pack(fill="x", padx=10, pady=(1, 0))

    def _apply_no_activate_style(self, target: tk.Misc | None = None) -> None:
        try:
            window = target or self.root
            hwnd = int(window.winfo_id())
            parent = int(ctypes.windll.user32.GetParent(hwnd) or 0)
            if parent:
                hwnd = parent
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_NOACTIVATE = 0x08000000
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOACTIVATE = 0x0010
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
            ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
        except (AttributeError, OSError, ValueError):
            pass

    def _drag_start(self, event) -> None:
        self.drag_x = event.x_root - self.root.winfo_x()
        self.drag_y = event.y_root - self.root.winfo_y()

    def _drag_move(self, event) -> None:
        self.window_x = event.x_root - self.drag_x
        self.window_y = event.y_root - self.drag_y
        self.root.geometry(f"+{self.window_x}+{self.window_y}")
        self._position_detail()

    @staticmethod
    def _alert_signature(alerts: list[dict[str, object]]) -> tuple:
        return tuple(
            (
                item.get("name"),
                item.get("title"),
                item.get("priority"),
                item.get("node"),
            )
            for item in alerts
        )

    def _accept_exact(self, result: dict[str, object]) -> None:
        enriched = dict(result)
        enriched["screen_scene"] = self.observer_scene
        timestamp = enriched.get("timestamp")
        moment = float(timestamp) if isinstance(timestamp, (int, float)) else None
        enriched["freddy_control"] = self.freddy_tracker.update(enriched, now=moment)
        enriched["berry_music"] = self.berry_music_tracker.update(enriched, now=moment)
        self.memory_state = enriched
        alerts = exact_guidance_all(enriched)
        statuses = exact_threat_statuses(enriched, alerts)
        signature = self._alert_signature(alerts)
        if signature == self.pending_signature:
            self.pending_samples += 1
        else:
            self.pending_signature = signature
            self.pending_samples = 1
        critical = any(bool(item.get("critical")) for item in alerts)
        if critical or self.pending_samples >= EXACT_CONFIRM_SAMPLES:
            self.alerts = alerts
            self.threat_statuses = statuses
            self._render(alerts, exact=True, statuses=statuses)

    def _pump_memory(self) -> None:
        result = self.memory.poll_latest()
        if result is not None:
            self.memory_state = result
            if result.get("exact"):
                self.last_memory_at = time.monotonic()
                self.memory_misses = 0
                self.memory_exact = True
                self._accept_exact(result)
            else:
                self.memory_misses += 1
                if self.memory_misses >= EXACT_CLEAR_SAMPLES:
                    self.memory_exact = False
        self.root.after(70, self._pump_memory)

    @staticmethod
    def _screen_action(name: str, info: dict[str, object]) -> str:
        ident = str(info.get("id", ""))
        if name == "bonnie" and ident == "bonnie_distract":
            return "Нажми ALARM сейчас — обнаружен DISTRACT HIM."
        if name == "bonnie":
            return "Опусти монитор и закрой ставню."
        if name == "chica":
            return "Закрой ставню. Свет НЕ включай."
        if name == "cream":
            return "CAM 07: нажми ALARM сейчас."
        if name == "berry":
            return "CAM 05: включи Play Music."
        if name == "slithers":
            return "Не переключай камеру. Опусти монитор."
        if name == "freddy":
            return "Держи текущую камеру на Фредди."
        return THREATS[name].short_action

    @staticmethod
    def _fallback_statuses(alerts: list[dict[str, object]]) -> list[dict[str, object]]:
        by_name = {str(item.get("name")): item for item in alerts}
        statuses: list[dict[str, object]] = []
        for name, threat in THREATS.items():
            alert = by_name.get(name)
            if alert is None:
                score, state_text, action = 0, "нет точных данных", threat.short_action
            else:
                score = int(alert.get("priority", 0))
                state_text = "обнаружен экраном"
                action = str(alert.get("action", threat.short_action))
            level, color = threat_level(score)
            statuses.append(
                {
                    "name": name,
                    "label": threat.label,
                    "score": score,
                    "level": level,
                    "color": color,
                    "state": state_text,
                    "timer": 0,
                    "action": action,
                }
            )
        return statuses

    def _pump_observer(self) -> None:
        result = self.observer.poll_latest()
        if result is not None:
            now = time.monotonic()
            self.observer_scene = str(result.get("scene", "error"))
            camera = result.get("camera")
            self.observer_camera = int(camera) if camera is not None else None
            seen: set[str] = set()
            for detected in result.get("detections", []):
                name = str(detected.get("threat", ""))
                if name not in THREATS:
                    continue
                seen.add(name)
                gap = now - self.detection_last_seen[name]
                self.detection_streak[name] = self.detection_streak[name] + 1 if gap < OBSERVER_INTERVAL * 2.7 else 1
                self.detection_last_seen[name] = now
                self.detection_info[name] = detected
                if self.detection_streak[name] >= 2 or str(detected.get("signal")) == "urgent":
                    self.active_detections.add(name)
            for name in THREATS:
                if name not in seen:
                    self.detection_streak[name] = 0
            for name in tuple(self.active_detections):
                if now - self.detection_last_seen[name] > 1.35:
                    self.active_detections.remove(name)
            if not self.memory_exact:
                fallback: list[dict[str, object]] = []
                signal_priority = {"urgent": 94, "watch": 76, "visible": 72}
                for name in self.active_detections:
                    info = self.detection_info.get(name, {})
                    priority = signal_priority.get(str(info.get("signal", "visible")), 70)
                    fallback.append(
                        {
                            "name": name,
                            "title": f"{THREATS[name].label.upper()} · ЭКРАН",
                            "action": self._screen_action(name, info),
                            "priority": priority,
                            "urgent": priority >= 80,
                            "critical": priority >= 90,
                            "source": "screen",
                        }
                    )
                fallback.sort(key=lambda item: (-int(item["priority"]), list(THREATS).index(str(item["name"]))))
                for item in fallback:
                    level, color = threat_level(int(item.get("priority", 0)))
                    item["level"] = level
                    item["level_color"] = color
                    item["steps"] = instruction_steps(item)
                if fallback:
                    statuses = self._fallback_statuses(fallback)
                    self.threat_statuses = statuses
                    self._render(fallback, exact=False, statuses=statuses)
                else:
                    self._render_waiting()
        self.root.after(100, self._pump_observer)

    def _watchdog(self) -> None:
        if self.memory_exact and time.monotonic() - self.last_memory_at > 0.8:
            self.memory_exact = False
            self.freddy_tracker.reset()
            self.berry_music_tracker.reset()
            self._render_waiting("Переподключаю точное чтение…")
        self.root.after(250, self._watchdog)

    def _clock_text(self) -> str:
        misc = self.memory_state.get("misc")
        if not isinstance(misc, list):
            return "-- AM"
        hour = misc[24] if len(misc) > 24 else None
        tick = misc[23] if len(misc) > 23 else None
        if not isinstance(hour, (int, float)) or not isinstance(tick, (int, float)):
            return "-- AM"
        return f"{int(hour)} AM · {max(0, min(60, int(tick))):02d}/60"

    def _render(
        self,
        alerts: list[dict[str, object]],
        exact: bool,
        statuses: list[dict[str, object]] | None = None,
    ) -> None:
        signature = self._alert_signature(alerts)
        self.last_alert_signature = signature
        urgent_items = [item for item in alerts if int(item.get("priority", 0)) >= 80]
        active_names = {str(item.get("name")) for item in urgent_items}
        plan = priority_plan(alerts)
        plan_score = int(plan.get("score", 0))
        target = repel_target(alerts)

        self.mode_badge.configure(
            text="ТОЧНО · ПАМЯТЬ" if exact else "РЕЗЕРВ · ЭКРАН",
            bg="#173629" if exact else "#29374a",
            fg=GREEN if exact else BLUE,
        )
        self.clock_label.configure(text=self._clock_text() if exact else (f"CAM {self.observer_camera:02d}" if self.observer_camera else "ЭКРАН"))
        summary_text = str(target.get("text", "ОТПУГИВАТЬ: НИКОГО"))
        target_color = str(target.get("color", GREEN))
        if plan_score >= 95:
            summary_bg, summary_fg, dot = "#2d1a20", target_color, RED
        elif plan_score >= 80:
            summary_bg, summary_fg, dot = "#2b2119", target_color, ORANGE
        elif plan_score >= 60:
            summary_bg, summary_fg, dot = "#292719", target_color, YELLOW
        elif plan_score >= 35:
            summary_bg, summary_fg, dot = "#18222d", target_color, BLUE
        else:
            summary_bg, summary_fg, dot = "#14221d", GREEN, GREEN
        self.summary.configure(bg=summary_bg)
        self.summary_label.configure(text=summary_text, bg=summary_bg, fg=summary_fg)
        self.status_dot.configure(fg=dot)
        status_prefix = "6/6 · точные команды" if exact else "резервное распознавание"
        if len(active_names) >= 2:
            status_prefix = f"{status_prefix} · одновременно {len(active_names)}"
        self.status_label.configure(text=status_prefix, fg=GREEN if exact else BLUE)

        plan_steps = tuple(plan.get("steps") or ())
        visible = (
            [
                {
                    "title": "ПЕРВОЕ ДЕЙСТВИЕ",
                    "action": str(plan_steps[0]) if plan_steps else str(plan.get("reason", "")),
                    "priority": plan_score,
                }
            ]
            if alerts
            else []
        )
        for index, card in enumerate(self.cards):
            frame = card["frame"]
            if index >= len(visible):
                if frame.winfo_manager():
                    frame.pack_forget()
                continue
            if not frame.winfo_manager():
                frame.pack(fill="x", pady=(0, 4))
            item = visible[index]
            priority = int(item.get("priority", 0))
            level, color = threat_level(priority)
            if priority >= 95:
                card_bg, color = "#35171e", RED
            elif priority >= 80:
                card_bg, color = "#362417", ORANGE
            elif priority >= 60:
                card_bg, color = "#332f17", YELLOW
            elif priority >= 35:
                card_bg, color = "#172536", BLUE
            else:
                card_bg, color = PANEL_2, GREEN
            for key in ("frame", "number", "host", "name", "action"):
                card[key].configure(bg=card_bg)
            card["bar"].configure(bg=color)
            card["number"].configure(text=str(index + 1), fg=color)
            card["name"].configure(
                text=f"{str(item.get('title', '—'))} · {level} {priority}/100",
                fg=TEXT,
            )
            card["action"].configure(text=str(item.get("action", "")), fg="#e1e6ed")

        tracker_data = statuses if statuses is not None else self._fallback_statuses(alerts)
        self._render_trackers(tracker_data, exact)
        extra = max(0, len(alerts) - 1)
        footer_text = f"Ещё угроз в очереди: {extra} · v{APP_VERSION}" if extra else f"Все 6 под контролем · v{APP_VERSION}"
        self.more_label.configure(text=footer_text)
        wanted_height = self.BASE_HEIGHT + max(0, len(visible) - 1) * (self.CARD_HEIGHT + 4)
        self._set_height(wanted_height)
        self._render_detail(plan, alerts)

        sound_signature = calm_sound_signature(alerts)
        if sound_signature and sound_signature != self.last_urgent_signature:
            self.last_urgent_signature = sound_signature
            threading.Thread(target=self._beep, daemon=True).start()
        elif not sound_signature:
            self.last_urgent_signature = ()

    def _render_trackers(self, statuses: list[dict[str, object]], exact: bool) -> None:
        by_name = {str(item.get("name")): item for item in statuses}
        for name, widgets in self.tracker_widgets.items():
            item = by_name.get(name)
            if item is None:
                score, state_text, level, color = 0, "нет данных", "—", MUTED
            else:
                score = int(item.get("score", 0))
                state_text = str(item.get("state", "нет данных"))
                level = str(item.get("level", threat_level(score)[0]))
                color = str(item.get("color", MUTED)) if exact or score else MUTED
            if len(state_text) > 11:
                state_text = state_text[:10] + "…"
            bg = "#24141a" if score >= 95 else "#241d13" if score >= 80 else "#1b1c14" if score >= 60 else "#111820"
            widgets["frame"].configure(bg=bg, highlightbackground=color if score >= 60 else "#202936")
            widgets["label"].configure(
                text=f"{THREAT_ABBR[name]} · {score:02d}/100\n{level} · {state_text}",
                bg=bg,
                fg=color,
            )

    @staticmethod
    def _detail_coordinates(
        root_x: int,
        root_y: int,
        root_height: int,
        screen_width: int,
        screen_height: int,
        detail_width: int,
        detail_height: int,
    ) -> tuple[int, int]:
        """Place the detail panel directly below the main window, never beside it."""
        max_x = max(8, screen_width - detail_width - 8)
        max_y = max(8, screen_height - detail_height - 8)
        x = min(max(8, root_x), max_x)
        y = min(max(8, root_y + root_height + 6), max_y)
        return x, y

    def _position_detail(self) -> None:
        if not getattr(self, "detail_visible", False):
            return
        self.root.update_idletasks()
        root_x = self.root.winfo_x()
        root_y = self.root.winfo_y()
        x, y = self._detail_coordinates(
            root_x,
            root_y,
            self.window_height,
            self.root.winfo_screenwidth(),
            self.root.winfo_screenheight(),
            self.DETAIL_WIDTH,
            self.DETAIL_HEIGHT,
        )
        self.detail.geometry(f"{self.DETAIL_WIDTH}x{self.DETAIL_HEIGHT}+{x}+{y}")

    def _render_detail(self, plan: dict[str, object], alerts: list[dict[str, object]]) -> None:
        score = int(plan.get("score", 0))
        show = score >= 60 or len([item for item in alerts if int(item.get("priority", 0)) >= 80]) >= 2
        if not show:
            if self.detail_visible:
                self.detail.withdraw()
                self.detail_visible = False
            return

        color = str(plan.get("color", YELLOW))
        level = str(plan.get("level", "—"))
        self.detail_accent.configure(bg=color)
        self.detail_level.configure(text=f"{level} · {score}/100", fg=color, bg="#252c39")
        self.detail_title.configure(text=str(plan.get("title", "СНАЧАЛА: —")), fg=color)
        steps = tuple(plan.get("steps") or ())
        for index, widgets in enumerate(self.detail_steps):
            text = str(steps[index]) if index < len(steps) else "—"
            widgets["number"].configure(fg=color)
            widgets["text"].configure(text=text)
        queue_text = " → ".join(str(item) for item in tuple(plan.get("queue") or ())) or "нет активной очереди"
        self.detail_queue.configure(text=f"ПОРЯДОК УГРОЗ: {queue_text}")
        if not self.detail_visible:
            self.detail_visible = True
            self._position_detail()
            self.detail.deiconify()
            self.detail.attributes("-topmost", True)
            self.detail.after(0, lambda: self._apply_no_activate_style(self.detail))
        else:
            self._position_detail()

    @staticmethod
    def _beep() -> None:
        try:
            winsound.Beep(CALM_CHIME_HZ, CALM_CHIME_MS)
        except RuntimeError:
            pass

    def _render_waiting(self, message: str | None = None) -> None:
        self.mode_badge.configure(text="ПОДКЛЮЧЕНИЕ", bg="#252c39", fg=MUTED)
        self.status_dot.configure(fg=ORANGE)
        if message:
            status = message
        elif self.observer_scene == "missing":
            status = "Жду запуск игры"
        elif self.observer_scene == "other":
            status = "Жду начало ночи"
        else:
            status = "Читаю состояние…"
        self.status_label.configure(text=status, fg=MUTED)
        self.clock_label.configure(text="-- AM")
        self.summary.configure(bg="#171d27")
        self.summary_label.configure(text="ОТПУГИВАТЬ: НИКОГО", bg="#171d27", fg=GREEN)
        for card in self.cards:
            if card["frame"].winfo_manager():
                card["frame"].pack_forget()
        self._render_trackers(self._fallback_statuses([]), exact=False)
        self.more_label.configure(text=f"Все 6 ожидают данные · v{APP_VERSION}")
        if self.detail_visible:
            self.detail.withdraw()
            self.detail_visible = False
        self._set_height(self.WAIT_HEIGHT)

    def _set_height(self, height: int) -> None:
        if height == self.window_height:
            return
        self.window_height = height
        self.window_x = self.root.winfo_x()
        self.window_y = self.root.winfo_y()
        self.root.geometry(f"{self.WIDTH}x{height}+{self.window_x}+{self.window_y}")
        self._position_detail()

    def _show_demo(self) -> None:
        ai = [0] * 32
        ai2 = [0] * 16
        misc = [0] * 40
        ai[2] = 3
        ai[21] = 42
        ai[23] = 30
        ai2[1] = 62
        misc[18] = 3
        misc[23] = 28
        misc[24] = 5
        self.memory_state = {
            "exact": True,
            "status": "demo",
            "timestamp": time.monotonic(),
            "screen_scene": "camera",
            "flags": {"ai": 0, "ai2": 0, "misc": 0},
            "ai": ai,
            "ai2": ai2,
            "misc": misc,
        }
        self.memory_state["freddy_control"] = self.freddy_tracker.update(self.memory_state)
        self.memory_state["berry_music"] = self.berry_music_tracker.update(self.memory_state)
        self.memory_exact = True
        alerts = exact_guidance_all(self.memory_state)
        statuses = exact_threat_statuses(self.memory_state, alerts)
        self._render(alerts, exact=True, statuses=statuses)

    def _pump_hotkeys(self) -> None:
        for action in self.hotkeys.poll():
            if action == "toggle":
                self.toggle()
            elif action == "reset":
                self.pending_signature = ()
                self.pending_samples = 0
                self.freddy_tracker.reset()
                self.berry_music_tracker.reset()
                self.memory.objects.clear()
                self._render_waiting("Переподключаюсь…")
        self.root.after(50, self._pump_hotkeys)

    def toggle(self) -> None:
        if self.hidden:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.after(0, self._apply_no_activate_style)
            self.hidden = False
            if self.detail_visible:
                self.detail.deiconify()
                self._position_detail()
                self.detail.after(0, lambda: self._apply_no_activate_style(self.detail))
        else:
            if self.detail_visible:
                self.detail.withdraw()
            self.root.withdraw()
            self.hidden = True

    def start_game(self) -> None:
        ok, message = launch_game()
        self.status_dot.configure(fg=BLUE if ok else RED)
        self.status_label.configure(text=message, fg=BLUE if ok else RED)

    def close(self) -> None:
        self.observer.close()
        self.memory.close()
        self.hotkeys.close()
        self.root.destroy()


def self_test() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    def freddy_state(
        *,
        phase: int = 3,
        camera: int = 3,
        tick: int = 28,
        target: int = 30,
        hour: int = 5,
        timer: int = 42,
        ai_flags: int = 0,
        scene: str = "camera",
    ) -> dict[str, object]:
        ai = [0] * 32
        ai2 = [0] * 16
        misc = [0] * 40
        ai[2] = phase
        ai[21] = timer
        ai[23] = target
        ai2[1] = 100
        misc[18] = camera
        misc[23] = tick
        misc[24] = hour
        return {
            "exact": True,
            "screen_scene": scene,
            "flags": {"ai": ai_flags, "ai2": 0, "misc": 0},
            "ai": ai,
            "ai2": ai2,
            "misc": misc,
        }

    def berry_state(
        *,
        position: int,
        phase: int = 0,
        phase_timer: int = 100,
        camera: int = 5,
        chica: int = 0,
        click_guard: int = 0,
        auxiliary_timer: int = 50,
        visual_playing: bool | None = None,
    ) -> dict[str, object]:
        ai = [0] * 32
        ai2 = [0] * 16
        misc = [0] * 40
        ai[1] = chica
        ai[7] = ai[12] = ai[19] = ai[21] = 100
        ai2[0] = phase
        ai2[1] = phase_timer
        ai2[2] = auxiliary_timer
        ai2[6] = 100
        misc[18] = camera
        misc[4] = click_guard
        misc[23] = 10
        misc[24] = 5
        result: dict[str, object] = {
            "exact": True,
            "screen_scene": "camera",
            "flags": {"ai": 0, "ai2": 0, "misc": 0},
            "ai": ai,
            "ai2": ai2,
            "misc": misc,
            "music_box": {
                "position": position,
                "maximum": BERRY_MUSIC_COUNTER_MAX,
            },
        }
        if visual_playing is not None:
            result["music_box_visual"] = {
                "playing": visual_playing,
                "image": (
                    BERRY_MUSIC_PLAYING_IMAGE
                    if visual_playing
                    else BERRY_MUSIC_STOPPED_IMAGE
                ),
            }
        return result

    if not GAME_PATH.exists():
        warnings.append(f"game not available for hash check: {GAME_PATH}")
    else:
        digest = sha256(GAME_PATH)
        if digest != GAME_SHA256:
            errors.append(f"game hash mismatch: {digest}")
    expected_nights = {
        1: {"bonnie"},
        2: {"bonnie", "chica", "cream"},
        3: {"bonnie", "chica", "cream", "berry"},
        4: {"bonnie", "chica", "cream", "berry", "slithers"},
        5: set(THREATS),
    }
    for night, expected in expected_nights.items():
        actual = {name for name, threat in THREATS.items() if threat.first_night <= night}
        if actual != expected:
            errors.append(f"night {night}: {sorted(actual)} != {sorted(expected)}")
    for threat in THREATS.values():
        if not threat.action or not threat.short_action or threat.node not in MAP_NODES:
            errors.append(f"invalid threat: {threat.name}")

    counter_bytes = bytearray(704)
    counter_address = 0x12345000
    struct.pack_into("<I", counter_bytes, 12, counter_address)
    struct.pack_into("<h", counter_bytes, 18, BERRY_MUSIC_COUNTER_OI)
    struct.pack_into("<i", counter_bytes, 684, BERRY_MUSIC_COUNTER_MAX)
    struct.pack_into("<i", counter_bytes, 696, -7650)
    counter_probe = object.__new__(FusionMemoryObserver)
    counter_probe._read = lambda address, size: bytes(counter_bytes[:size]) if address == counter_address else b""  # type: ignore[method-assign]
    decoded_counter = counter_probe._read_music_box_counter(counter_address)
    if (
        not decoded_counter
        or decoded_counter.get("position") != 7650
        or decoded_counter.get("remaining_seconds") != 14.0
    ):
        errors.append("Berry native music counter decoder failed")

    visual_bytes = bytearray(224)
    visual_address = 0x12346000
    struct.pack_into("<i", visual_bytes, 4, 752)
    struct.pack_into("<I", visual_bytes, 12, visual_address)
    struct.pack_into("<h", visual_bytes, 18, BERRY_MUSIC_VISUAL_OI)
    struct.pack_into("<h", visual_bytes, 24, 2)
    struct.pack_into("<i", visual_bytes, 220, BERRY_MUSIC_STOPPED_IMAGE)
    visual_probe = object.__new__(FusionMemoryObserver)
    visual_probe._read = lambda address, size: bytes(visual_bytes[:size]) if address == visual_address else b""  # type: ignore[method-assign]
    decoded_visual = visual_probe._read_music_box_visual(visual_address)
    if not decoded_visual or decoded_visual.get("playing") is not False:
        errors.append("Berry PLAY/STOP visual decoder failed")

    stopped_tracker = BerryMusicTracker()
    stopped_at_start = berry_state(position=1, visual_playing=False)
    stopped_at_start["berry_music"] = stopped_tracker.update(stopped_at_start, now=1.0)
    stopped_alert = next(
        (
            item
            for item in exact_guidance_all(stopped_at_start)
            if item.get("name") == "berry"
        ),
        None,
    )
    if (
        stopped_at_start["berry_music"].get("mode") != "expired"
        or not stopped_alert
        or "ВКЛЮЧИ ШКАТУЛКУ СЕЙЧАС" not in str(stopped_alert.get("title"))
    ):
        errors.append("Berry stopped-at-night-start detection failed")

    started_from_play = berry_state(
        position=1,
        click_guard=10,
        visual_playing=True,
    )
    started_from_play["berry_music"] = stopped_tracker.update(
        started_from_play, now=1.1
    )
    if (
        started_from_play["berry_music"].get("mode") != "healthy"
        or not started_from_play["berry_music"].get("restart_confirmed")
    ):
        errors.append("Berry PLAY image acknowledgement failed")

    berry_tracker = BerryMusicTracker()
    healthy_berry = berry_state(position=1000)
    healthy_berry["berry_music"] = berry_tracker.update(healthy_berry)
    healthy_alerts = exact_guidance_all(healthy_berry)
    if any(
        item.get("name") == "berry" and int(item.get("priority", 0)) >= 60
        for item in healthy_alerts
    ):
        errors.append("healthy Berry music creates a false active warning")
    healthy_status = next(
        item
        for item in exact_threat_statuses(healthy_berry, healthy_alerts)
        if item.get("name") == "berry"
    )
    if "музыка играет" not in str(healthy_status.get("state")):
        errors.append("healthy Berry music countdown is missing")

    warning_berry = berry_state(position=7700)
    warning_berry["berry_music"] = berry_tracker.update(warning_berry)
    warning_alert = next(
        (item for item in exact_guidance_all(warning_berry) if item.get("name") == "berry"),
        None,
    )
    if (
        not warning_alert
        or "СКОРО КОНЧИТСЯ" not in str(warning_alert.get("title"))
        or "НЕ нажимай" not in str(tuple(warning_alert.get("steps") or ())[1])
    ):
        errors.append("Berry early music warning failed")

    critical_berry = berry_state(position=8600)
    critical_berry["berry_music"] = berry_tracker.update(critical_berry)
    critical_alert = next(
        (item for item in exact_guidance_all(critical_berry) if item.get("name") == "berry"),
        None,
    )
    if not critical_alert or int(critical_alert.get("priority", 0)) != 82:
        errors.append("Berry critical prepare window failed")

    ending_tracker = BerryMusicTracker()
    near_end = berry_state(position=9050)
    ending_tracker.update(near_end)
    expired_berry = berry_state(position=0, phase=2, phase_timer=100)
    expired_berry["berry_music"] = ending_tracker.update(expired_berry)
    expired_alerts = exact_guidance_all(expired_berry)
    expired_alert = next(
        (item for item in expired_alerts if item.get("name") == "berry"), None
    )
    if (
        not expired_alert
        or "ВКЛЮЧИ ШКАТУЛКУ СЕЙЧАС" not in str(expired_alert.get("title"))
        or "Нажми Play Music один раз сейчас" not in str(tuple(expired_alert.get("steps") or ())[1])
    ):
        errors.append("Berry exact music-stop edge failed")

    natural_stop_tracker = BerryMusicTracker()
    natural_stop_tracker.update(
        berry_state(position=9050, auxiliary_timer=130), now=20.0
    )
    naturally_stopped = berry_state(
        position=0,
        phase=2,
        phase_timer=92,
        auxiliary_timer=129,
    )
    naturally_stopped["berry_music"] = natural_stop_tracker.update(
        naturally_stopped, now=20.1
    )
    still_stopped = berry_state(
        position=0,
        phase=2,
        phase_timer=91,
        auxiliary_timer=128,
    )
    still_stopped["berry_music"] = natural_stop_tracker.update(
        still_stopped, now=20.2
    )
    if still_stopped["berry_music"].get("mode") != "expired":
        errors.append("Berry natural timer change falsely acknowledges Play Music")

    replay_click = berry_state(
        position=0,
        phase=2,
        phase_timer=93,
        click_guard=8,
        auxiliary_timer=126,
    )
    replay_click["berry_music"] = ending_tracker.update(replay_click, now=10.0)
    if replay_click["berry_music"].get("mode") != "healthy" or not replay_click[
        "berry_music"
    ].get("restart_confirmed"):
        errors.append("Berry Play Music acknowledgement was not detected")
    if any(
        item.get("name") == "berry" and int(item.get("priority", 0)) >= 60
        for item in exact_guidance_all(replay_click)
    ):
        errors.append("Berry warning remains visible on the Play Music click")

    replayed_berry = berry_state(
        position=120,
        phase=2,
        phase_timer=93,
        auxiliary_timer=126,
    )
    replayed_berry["berry_music"] = ending_tracker.update(replayed_berry, now=10.2)
    if any(
        item.get("name") == "berry" and int(item.get("priority", 0)) >= 60
        for item in exact_guidance_all(replayed_berry)
    ):
        errors.append("Berry warning remains stuck after Play Music")

    warning_target = repel_target(exact_guidance_all(warning_berry))
    if warning_target.get("names") or "НЕ НАЖИМАТЬ" not in str(
        warning_target.get("text")
    ):
        errors.append("Berry early warning is mislabeled as an active repel target")

    simultaneous_tracker = BerryMusicTracker()
    simultaneous_tracker.update(berry_state(position=9050, phase=2))
    simultaneous_berry = berry_state(position=0, phase=2, phase_timer=100, chica=8)
    simultaneous_berry["berry_music"] = simultaneous_tracker.update(simultaneous_berry)
    simultaneous_alerts = exact_guidance_all(simultaneous_berry)
    if [str(item.get("name")) for item in simultaneous_alerts[:2]] != ["chica", "berry"]:
        errors.append("Berry simultaneous-threat priority is unsafe")
    simultaneous_target = repel_target(simultaneous_alerts)
    if set(simultaneous_target.get("names") or ()) != {"chica", "berry"}:
        errors.append("Berry simultaneous repel targets are incomplete")

    fallback_berry = berry_state(position=0, phase=1, phase_timer=93)
    fallback_berry.pop("music_box", None)
    fallback_alert = next(
        (
            item
            for item in exact_guidance_all(fallback_berry)
            if item.get("name") == "berry" and int(item.get("priority", 0)) >= 60
        ),
        None,
    )
    fallback_status = next(
        item
        for item in exact_threat_statuses(fallback_berry, exact_guidance_all(fallback_berry))
        if item.get("name") == "berry"
    )
    if fallback_alert is not None or "подключается" not in str(fallback_status.get("state")):
        errors.append("Berry missing-counter safety fallback failed")
    sample_state = freddy_state(camera=1, tick=27)
    guidance = exact_guidance(sample_state)
    if (
        not guidance
        or guidance.get("name") != "freddy"
        or "CAM 03" not in str(guidance.get("title"))
        or "CAM 03" not in str(tuple(guidance.get("steps") or ())[0])
    ):
        errors.append("exact Freddy due-window guidance failed")
    elif repel_target([guidance]).get("text") != "ОТПУГИВАТЬ: ФРЕДДИ":
        errors.append("highlighted Freddy repel target failed")
    if guidance and calm_sound_signature([guidance]):
        errors.append("non-critical Freddy alert is still audible")
    critical_probe = [{"name": "freddy", "title": "critical", "priority": 95}]
    if not calm_sound_signature(critical_probe):
        errors.append("critical calm chime gate failed")
    if CALM_CHIME_HZ > 600 or CALM_CHIME_MS > 60:
        errors.append("calm chime profile is too sharp or too long")

    signature_probe = [{"name": "freddy", "title": "hold", "action": "0.1 s", "priority": 76, "node": "dining"}]
    signature_probe_updated = [{**signature_probe[0], "action": "0.2 s"}]
    if CompactAssistantApp._alert_signature(signature_probe) != CompactAssistantApp._alert_signature(
        signature_probe_updated
    ):
        errors.append("dynamic countdown causes rapid alert switching")
    if CompactAssistantApp.WIDTH >= 388 or CompactAssistantApp.DETAIL_WIDTH >= 330:
        errors.append("compact windows were not reduced")
    default_root_x = 1920 - CompactAssistantApp.WIDTH - 14
    detail_position = CompactAssistantApp._detail_coordinates(
        default_root_x,
        18,
        CompactAssistantApp.BASE_HEIGHT,
        1920,
        1080,
        CompactAssistantApp.DETAIL_WIDTH,
        CompactAssistantApp.DETAIL_HEIGHT,
    )
    if detail_position != (default_root_x, 18 + CompactAssistantApp.BASE_HEIGHT + 6):
        errors.append("detail panel is not positioned directly below the main window")

    freddy_early_state = freddy_state(camera=1, tick=12)
    freddy_early_alerts = exact_guidance_all(freddy_early_state)
    if any(item.get("name") == "freddy" for item in freddy_early_alerts):
        errors.append("early Freddy warning is not suppressed")
    if repel_target(freddy_early_alerts).get("text") != "ОТПУГИВАТЬ: НИКОГО":
        errors.append("quiet state shows a false repel target")
    freddy_early_status = next(
        item
        for item in exact_threat_statuses(freddy_early_state, freddy_early_alerts)
        if item.get("name") == "freddy"
    )
    if freddy_early_status.get("level") != "СПОКОЙНО" or "проверка через 18с" not in str(
        freddy_early_status.get("state")
    ):
        errors.append("scheduled Freddy tracker is not calm")

    tracker = FreddyWatchTracker()
    watching_state = freddy_state(camera=3, tick=28, scene="camera")
    tracked = tracker.update(watching_state, now=10.0)
    for sample in range(1, 11):
        tracked = tracker.update(watching_state, now=10.0 + sample / 10)
    if not tracked.get("watching") or float(tracked.get("watch_seconds") or 0.0) < 0.9:
        errors.append("Freddy continuous-watch timer failed")
    watching_state["freddy_control"] = tracked
    watching_guidance = exact_guidance(watching_state)
    if (
        not watching_guidance
        or "Оставайся на CAM 03" not in str(tuple(watching_guidance.get("steps") or ())[0])
        or "Засчитано 1.0 с" not in str(tuple(watching_guidance.get("steps") or ())[1])
    ):
        errors.append("Freddy counted-watch instructions failed")

    handled_state = freddy_state(camera=3, tick=30, ai_flags=1 << FREDDY_CHECK_FLAG_INDEX)
    handled_state["freddy_control"] = tracker.update(handled_state, now=11.1)
    handled_alerts = exact_guidance_all(handled_state)
    if any(item.get("name") == "freddy" for item in handled_alerts):
        errors.append("game-acknowledged Freddy warning is stuck")
    handled_status = next(
        item
        for item in exact_threat_statuses(handled_state, handled_alerts)
        if item.get("name") == "freddy"
    )
    if handled_status.get("state") != "ПРОВЕРЕН" or handled_status.get("level") != "СПОКОЙНО":
        errors.append("Freddy game acknowledgement is not shown")

    next_threat_state = freddy_state(camera=3, tick=30, ai_flags=1 << FREDDY_CHECK_FLAG_INDEX)
    next_ai = next_threat_state["ai"]
    assert isinstance(next_ai, list)
    next_ai[1] = 8
    next_threat_state["freddy_control"] = tracker.update(next_threat_state, now=11.2)
    next_alerts = exact_guidance_all(next_threat_state)
    if not next_alerts or next_alerts[0].get("name") != "chica" or any(
        item.get("name") == "freddy" for item in next_alerts
    ):
        errors.append("next threat does not take priority after Freddy acknowledgement")

    missed_state = freddy_state(camera=1, tick=33)
    missed_alerts = exact_guidance_all(missed_state)
    if any(item.get("name") == "freddy" for item in missed_alerts):
        errors.append("missed Freddy check window remains stuck")

    phase_five_state = freddy_state(phase=5, camera=5, tick=28, timer=12)
    phase_five_guidance = exact_guidance(phase_five_state)
    if (
        not phase_five_guidance
        or phase_five_guidance.get("name") != "freddy"
        or "СТАВН" not in str(phase_five_guidance.get("title"))
        or "CAM 05" in str(phase_five_guidance.get("title"))
    ):
        errors.append("Freddy phase-five shutter transition failed")
    bonnie_alarm_state = {
        "exact": True,
        "ai": [4] + [0] * 31,
        "ai2": [0, 100] + [0] * 14,
        "misc": [0] * 18 + [5] + [0] * 14 + [3] + [0] * 6,
    }
    guidance = exact_guidance(bonnie_alarm_state)
    if not guidance or guidance.get("name") != "bonnie" or "ALARM СЕЙЧАС" not in str(guidance.get("title")):
        errors.append("exact Bonnie alarm guidance failed")
    bonnie_early_state = {
        "exact": True,
        "ai": [5] + [0] * 31,
        "ai2": [0, 100] + [0] * 14,
        "misc": [0] * 18 + [1] + [0] * 21,
    }
    guidance = exact_guidance(bonnie_early_state)
    if not guidance or guidance.get("name") != "bonnie" or "CAM 06" not in str(guidance.get("action")):
        errors.append("early Bonnie warning failed")
    bonnie_wait_state = {
        "exact": True,
        "ai": [4] + [0] * 31,
        "ai2": [0, 100] + [0] * 14,
        "misc": [0] * 18 + [5] + [0] * 14 + [2] + [0] * 6,
    }
    guidance = exact_guidance(bonnie_wait_state)
    if not guidance or "ЖДИ" not in str(guidance.get("title")) or "ALARM СЕЙЧАС" in str(guidance.get("title")):
        errors.append("Bonnie alarm phase guard failed")
    dual_state = {
        "exact": True,
        "ai": [7, 8] + [0] * 30,
        "ai2": [0, 100] + [0] * 14,
        "misc": [0] * 18 + [1] + [0] * 4 + [18, 5] + [0] * 15,
    }
    dual_alerts = exact_guidance_all(dual_state)
    if {str(item.get("name")) for item in dual_alerts[:2]} != {"bonnie", "chica"}:
        errors.append("simultaneous Bonnie + Chica guidance failed")
    dual_target = repel_target(dual_alerts)
    if set(dual_target.get("names") or ()) != {"bonnie", "chica"}:
        errors.append("simultaneous highlighted repel targets failed")
    if any(len(tuple(item.get("steps") or ())) != 3 for item in dual_alerts):
        errors.append("three-step instructions are incomplete")
    dual_plan = priority_plan(dual_alerts)
    dual_steps = tuple(dual_plan.get("steps") or ())
    if (
        len(dual_steps) != 3
        or "МОНИТОР" not in str(dual_steps[0]).upper()
        or "СТАВН" not in str(dual_steps[1]).upper()
        or "МОНИТОР ВНИЗ" not in str(dual_plan.get("title", "")).upper()
    ):
        errors.append("simultaneous window threat arbitration failed")
    dual_statuses = exact_threat_statuses(dual_state, dual_alerts)
    if {str(item.get("name")) for item in dual_statuses} != set(THREATS):
        errors.append("all-animatronic tracker is incomplete")
    if any("level" not in item or "score" not in item for item in dual_statuses):
        errors.append("threat tracker levels are missing")
    dual_alert_scores = {str(item.get("name")): int(item.get("priority", 0)) for item in dual_alerts}
    if any(
        str(item.get("name")) in dual_alert_scores
        and int(item.get("score", 0)) != dual_alert_scores[str(item.get("name"))]
        for item in dual_statuses
    ):
        errors.append("threat score differs between alert card and all-six tracker")
    slithers_cream_state = {
        "exact": True,
        "ai": [0, 0, 0, 7, 3] + [0] * 27,
        "ai2": [0, 55, 0, 0, 0, 0, 5] + [0] * 9,
        "misc": [0] * 18 + [7] + [0] * 4 + [18, 5] + [0] * 15,
    }
    slithers_cream_alerts = exact_guidance_all(slithers_cream_state)
    if not {"slithers", "cream"}.issubset(
        {str(item.get("name")) for item in slithers_cream_alerts}
    ):
        errors.append("simultaneous Slithers + Cream detection failed")
    slithers_plan = priority_plan(slithers_cream_alerts)
    if "НЕ ПЕРЕКЛЮЧАЙ" not in str(tuple(slithers_plan.get("steps") or ())[0]).upper():
        errors.append("camera-lock conflict arbitration failed")
    expected_levels = {
        0: "СПОКОЙНО",
        35: "НИЗКИЙ",
        60: "СРЕДНИЙ",
        80: "ВЫСОКИЙ",
        95: "КРИТИЧЕСКИЙ",
    }
    for score, expected_level in expected_levels.items():
        if threat_level(score)[0] != expected_level:
            errors.append(f"threat level {score} is not {expected_level}")
    freddy_window_state = {
        "exact": True,
        "ai": [0, 0, 7] + [0] * 29,
        "ai2": [0, 100] + [0] * 14,
        "misc": [0] * 18 + [4] + [0] * 4 + [8, 5] + [0] * 15,
    }
    guidance = exact_guidance(freddy_window_state)
    if not guidance or guidance.get("name") != "freddy" or "СТАВН" not in str(guidance.get("title")):
        errors.append("Freddy shutter guidance failed")
    plausible = {
        "misc": [0] * 18 + [5] + [0] * 4 + [12, 5] + [0] * 15,
        "ai": [0] * 32,
        "ai2": [0] * 16,
    }
    if FusionMemoryObserver._plausibility_score(plausible) < 8:
        errors.append("memory plausibility filter rejected valid state")
    manifest_path = resource_path("gsaf_assets", "manifest.json")
    if not manifest_path.exists():
        errors.append(f"observer assets missing: {manifest_path}")
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if len(manifest.get("templates", [])) < 30:
                errors.append("observer template set is incomplete")
        except (OSError, ValueError) as exc:
            errors.append(f"observer manifest invalid: {exc}")
    report = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "game": str(GAME_PATH),
        "game_hash_expected": GAME_SHA256,
        "threats": list(THREATS),
        "tracking": "6/6 live values, exact Berry PLAY/STOP plus native counter, calm critical chime, highlighted repel target, ordered 1-2-3 arbitration",
        "warnings": warnings,
        "errors": errors,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors else 0


def observer_test() -> int:
    observer = GameObserver(start=False)
    if observer.load_error:
        print(json.dumps({"error": observer.load_error}, ensure_ascii=False, indent=2))
        return 1
    report = observer.scan_once()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("window_found") else 2


def memory_test() -> int:
    observer = FusionMemoryObserver(start=False)
    try:
        report = observer.scan_once()
        if report.get("exact"):
            report["berry_music"] = BerryMusicTracker().update(report)
            report["guidance"] = exact_guidance(report)
            report["ai"] = report.get("ai", [])[:24]
            report["ai2"] = report.get("ai2", [])[:8]
            report["misc"] = report.get("misc", [])[:36]
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("exact") else 2
    finally:
        observer.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--observer-test", action="store_true")
    parser.add_argument("--memory-test", action="store_true")
    parser.add_argument("--no-game", action="store_true", help="do not auto-launch the game")
    parser.add_argument("--demo", action="store_true", help="show a dual-threat UI demo")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.observer_test:
        return observer_test()
    if args.memory_test:
        return memory_test()
    root = tk.Tk()
    CompactAssistantApp(root, auto_launch=not args.no_game and not args.demo, demo=args.demo)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
