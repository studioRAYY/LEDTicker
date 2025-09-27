# main.py — Studio Rayy Ticker (Mapping-Editor, vertikale/horizontale Laufrichtung,
# Fullscreen-Fix, Pfadmodus-Umbenennung, Block-Dropdowns, freie Ausgabeauflösung,
# Export-Zuschnitt/Crop, FFmpeg-Export, **Live-Content-Auswahl**)
#
# Tabs:
# - Contents: mehrere Texte/Styles definieren (Font, Größe, Farben)
# - Scheduler: zeitgesteuerter Wechsel (daily/date, Transition, Fade)
# - Output: Live/Stop/Fullscreen, ROI-Overlay, Geschwindigkeit, Ausgabe-W/H/FPS,
#           **Live-Content-Dropdown (Scheduler oder manuell)**,
#           sowie Export MP4 inkl. frei wählbarem Crop (X,Y,W,H)
# - Preset: laden/speichern/aktualisieren
# - Mapping: Ports/Module/Blöcke konfigurieren (Mode, Pfadmodus "snake/reset",
#            Block-Direction per Dropdown)
#
# Hinweise:
# - "Pfadmodus" ersetzt das frühere "ZigZag"-Flag. (Die eigentliche Schlangen-Phasenlogik
#   kann auf Wunsch zusätzlich eingebaut werden; aktuell bleibt die Phasenführung wie zuvor.)
# - Vertikale Textdarstellung: Fix durch translate + rotate, damit Text sichtbar ist.
# - tile_src_rect_v nutzt Modulo über 2*text_w (robusteres Sampling gegen double_v).
# - Export unterstützt frei einstellbaren Zuschnitt (Crop) und dynamische Ausgabegröße/FPS.
# - **Neu:** Live-Output kann explizit einen Content wählen (Dropdown). Fonts/Farben/Größe
#   werden sofort übernommen, sobald Änderungen übernommen oder Content gewechselt wird.

import sys, os, shutil, subprocess, json, datetime
from math import gcd
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict
from collections import deque

from PySide6.QtCore import Qt, QTimer, QRect
from PySide6.QtGui import QPainter, QImage, QColor, QFont, QFontMetrics, QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QColorDialog, QDoubleSpinBox, QFileDialog,
    QSpinBox, QVBoxLayout, QHBoxLayout, QMainWindow, QMessageBox, QCheckBox, QFontComboBox, QListWidget, QListWidgetItem,
    QTabWidget, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView
)

# ----------------------------- Utils -----------------------------
def resource_path(*parts):
    base = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base, *parts)

def resolve_ffmpeg():
    cand = resource_path("ffmpeg")
    cand_exe = resource_path("ffmpeg.exe")
    if os.path.isfile(cand) and os.access(cand, os.X_OK):
        return cand
    if os.path.isfile(cand_exe):
        return cand_exe
    return shutil.which("ffmpeg")

# ----------------------------- Data Models -----------------------------
@dataclass
class ContentItem:
    name: str
    text: str
    font_family: str
    font_pt: int
    text_rgb: Tuple[int,int,int]
    bg_rgb: Tuple[int,int,int]

@dataclass
class CFG:
    width: int = 1920
    height: int = 1080
    fps: int = 50
    speed_px_per_frame: float = 4.0  # "Geschwindigkeit"

# ----------------------------- Mapping Structures -----------------------------
class PortROI:
    def __init__(self, port_id: str, rects: List[Tuple[int,int,int,int,str]]):
        self.port_id = port_id
        # rects: (x,y,w,h, dir) with dir in {"left_right","right_left","top_down","bottom_up"}
        self.rects = rects

def _gen_rects_new_port(port: dict, module_w: int, module_h: int) -> List[Tuple[int,int,int,int,str]]:
    """
    Neues Mapping-Format:
      {
        "id": "port1",
        "start": {"x": 0, "y": 824},
        "mode": "vertical"|"horizontal",
        "path_mode": "snake"|"reset",   # (ersetzt früheres "zigzag")
        "blocks": [ {"dir":"bottom_up","count":4}, {"dir":"top_down","count":4}, ... ]
      }
    Regeln (Geometrie):
     - mode=vertical: jeder Block beschreibt eine Spalte; innerhalb davon werden count Module in y-Richtung gestapelt.
       Danach springt X um module_w weiter (neue Spalte).
     - mode=horizontal: analog für Zeilen.
     - dir steuert außerdem die Textlaufrichtung pro Kachel (links->rechts, rechts->links, unten->oben, oben->unten).
    """
    sx = int(port.get("start", {}).get("x", 0))
    sy = int(port.get("start", {}).get("y", 0))
    mode = port.get("mode", "vertical")
    _path_mode = port.get("path_mode", "snake")
    blocks = port.get("blocks", [])
    rects: List[Tuple[int,int,int,int,str]] = []
    cur_x, cur_y = sx, sy

    for blk in blocks:
        dirv = blk.get("dir", "bottom_up" if mode == "vertical" else "left_right")
        count = int(blk.get("count", 0))
        if count <= 0:
            if mode == "vertical":
                cur_x += module_w
            else:
                cur_y += module_h
            continue

        if mode == "vertical":
            if dirv == "bottom_up":
                for k in range(count):
                    y = cur_y - k*module_h
                    rects.append( (cur_x, y, module_w, module_h, "bottom_up") )
                cur_x += module_w
            elif dirv == "top_down":
                for k in range(count):
                    y = cur_y + k*module_h
                    rects.append( (cur_x, y, module_w, module_h, "top_down") )
                cur_x += module_w
            else:
                for k in range(count):
                    y = cur_y + k*module_h
                    rects.append( (cur_x, y, module_w, module_h, "top_down") )
                cur_x += module_w
        else:
            if dirv == "left_right":
                for k in range(count):
                    x = cur_x + k*module_w
                    rects.append( (x, cur_y, module_w, module_h, "left_right") )
                cur_y += module_h
            elif dirv == "right_left":
                for k in range(count):
                    x = cur_x - k*module_w
                    rects.append( (x, cur_y, module_w, module_h, "right_left") )
                cur_y += module_h
            else:
                for k in range(count):
                    x = cur_x + k*module_w
                    rects.append( (x, cur_y, module_w, module_h, "left_right") )
                cur_y += module_h

    return rects

def build_rois_from_preset(preset) -> List[PortROI]:
    """Unterstützt neues Mapping-Format; migriert ggf. sehr alte Struktur."""
    module_w = preset.get("module", {}).get("w", 128)
    module_h = preset.get("module", {}).get("h", 256)
    ports = preset.get("ports", [])
    ports_out: List[PortROI] = []

    if not ports:
        ports = [
            {"id":"port1","start":{"x":0,"y":824},"mode":"vertical","path_mode":"snake",
             "blocks":[{"dir":"bottom_up","count":4},{"dir":"top_down","count":4},{"dir":"bottom_up","count":4}]},
            {"id":"port2","start":{"x":128,"y":824},"mode":"vertical","path_mode":"snake",
             "blocks":[{"dir":"bottom_up","count":4},{"dir":"top_down","count":4},{"dir":"bottom_up","count":4}]}
        ]
        preset["ports"] = ports
        preset["module"] = {"w": module_w, "h": module_h}

    for port in ports:
        if "start" in port or "mode" in port:
            rects = _gen_rects_new_port(port, module_w, module_h)
        else:
            # extreme Altlast – einfache Migration
            x = int(port.get("x", 0))
            rects_tmp = []
            for blk in port.get("blocks", []):
                order = blk.get("order","bottom_up")
                cnt = int(blk.get("count", 0))
                ys_bottom_up = [824, 568, 312, 56]
                ys_top_down  = [0, 256, 512, 768]
                ys = ys_bottom_up if order == "bottom_up" else ys_top_down
                for y in ys[:cnt]:
                    rects_tmp.append((x, y, module_w, module_h, "bottom_up" if order=="bottom_up" else "top_down"))
                x += module_w
            rects = rects_tmp
        ports_out.append(PortROI(port.get("id","port"), rects))
    return ports_out

def draw_roi_overlay(img: QImage, rois: List[PortROI]) -> QImage:
    out = img.copy()
    p = QPainter(out)
    colors = [QColor(0,255,0), QColor(0,180,255), QColor(255,180,0), QColor(255,0,180)]
    for idx, port in enumerate(rois):
        p.setPen(colors[idx % len(colors)])
        for i,(x,y,w,h,dirv) in enumerate(port.rects):
            p.drawRect(x,y,w,h)
            p.drawText(x+4,y+18,f"{port.port_id}:{i+1} {dirv}")
    p.end()
    return out

# ----------------------------- Master Strip (H & V) -----------------------------
class MasterStrip:
    """Erzeugt horizontale UND vertikale Textquellen.
       Horizontal: single_h (w=text_w, h=module_h), double_h (2x)
       Vertikal:   single_v (w=module_w, h=text_w) – Text um -90° rotiert; double_v (2x in y)
    """
    def __init__(self, text: str, font_family: str, font_pt: int, text_rgb: Tuple[int,int,int], bg_rgb: Tuple[int,int,int],
                 module_w: int, module_h: int, num_modules: int):
        self.text = text if text else " "
        self.font = QFont(font_family, font_pt)
        self.text_rgb = text_rgb
        self.bg_rgb = bg_rgb
        self.module_w = module_w
        self.module_h = module_h
        self.num_modules = num_modules
        self.rebuild()

    def rebuild(self):
        fm = QFontMetrics(self.font)
        self.text_w = max(8, fm.horizontalAdvance(self.text))

        # Horizontal Basetext
        h = self.module_h
        self.single_h = QImage(self.text_w, h, QImage.Format_RGB888)
        self.single_h.fill(QColor(*self.bg_rgb))
        p = QPainter(self.single_h)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        p.setPen(QColor(*self.text_rgb))
        p.setFont(self.font)
        baseline = (h + fm.ascent() - fm.descent()) // 2
        p.drawText(0, baseline, self.text)
        p.end()
        self.double_h = QImage(self.text_w*2, h, QImage.Format_RGB888)
        p2 = QPainter(self.double_h)
        p2.drawImage(0,0,self.single_h)
        p2.drawImage(self.text_w,0,self.single_h)
        p2.end()

        # Vertikal Basetext (sichtbar durch translate + rotate)
        self.single_v = QImage(self.module_w, self.text_w, QImage.Format_RGB888)
        self.single_v.fill(QColor(*self.bg_rgb))
        p3 = QPainter(self.single_v)
        p3.setRenderHint(QPainter.TextAntialiasing, True)
        p3.setPen(QColor(*self.text_rgb))
        p3.setFont(self.font)
        # Wichtig: Ursprung verschieben und dann drehen, damit Text in der Fläche landet
        p3.translate(0, self.text_w)
        p3.rotate(-90)
        baseline_v = (self.module_w + fm.ascent() - fm.descent()) // 2
        p3.drawText(0, baseline_v, self.text)
        p3.end()
        self.double_v = QImage(self.module_w, self.text_w*2, QImage.Format_RGB888)
        p4 = QPainter(self.double_v)
        p4.drawImage(0,0,self.single_v)
        p4.drawImage(0,self.text_w,self.single_v)
        p4.end()

    def period_frames(self, int_speed_px_per_frame: int) -> int:
        return self.text_w // gcd(self.text_w, max(1, int_speed_px_per_frame))

    # Horizontal Sampling (links->rechts)
    def tile_src_rect_h(self, offset_px: float, module_index: int, reverse=False) -> QRect:
        if reverse:
            # reverse: sowohl Offset als auch modulare Verschiebung negativ
            x = int((-offset_px - module_index * self.module_w)) % self.text_w
        else:
            x = int(( offset_px + module_index * self.module_w)) % self.text_w
        return QRect(x, 0, self.module_w, self.module_h)

    # Vertikal Sampling (oben->unten). Modulo über double-Höhe robuster.
    def tile_src_rect_v(self, offset_px: float, module_index: int, reverse=False) -> QRect:
        period = 2 * self.text_w
        if reverse:
            y = int((-offset_px - module_index * self.module_h)) % period
        else:
            y = int(( offset_px + module_index * self.module_h)) % period
        return QRect(0, y, self.module_w, self.module_h)
# ----------------------------- Scheduler -----------------------------
def parse_time(s: str) -> datetime.time:
    return datetime.time.fromisoformat(s)

def in_range(t: datetime.time, start: datetime.time, end: datetime.time) -> bool:
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end  # Mitternachtsüberlauf

class Scheduler:
    def __init__(self, contents: Dict[str, ContentItem], entries: List[dict] = None):
        self.contents = contents
        self.entries = entries or []

    def pick_content_name(self, now: Optional[datetime.datetime] = None) -> Optional[str]:
        now = now or datetime.datetime.now()
        t = now.time()
        wd = now.weekday()
        for e in self.entries:
            if e.get("type") == "date" and e.get("date") == now.date().isoformat():
                if in_range(t, parse_time(e["start"]), parse_time(e["end"])):
                    return e.get("content")
        for e in self.entries:
            if e.get("type") == "daily" and wd in (e.get("weekdays") or []):
                if in_range(t, parse_time(e["start"]), parse_time(e["end"])):
                    return e.get("content")
        return None

# ----------------------------- UI Widgets -----------------------------
class Preview(QWidget):
    def __init__(self, w: int, h: int):
        super().__init__()
        self.setMinimumSize(800, 320)
        self.img = QImage(w,h,QImage.Format_RGB888)
        self.img.fill(Qt.black)
    def set_frame(self, img: QImage):
        self.img = img
        self.update()
    def paintEvent(self, _):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, Qt.black)
        scaled = self.img.scaled(r.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p.drawImage((r.width()-scaled.width())//2,(r.height()-scaled.height())//2, scaled)
        p.end()

class OutputWindow(QWidget):
    def __init__(self, w: int, h: int):
        super().__init__()
        self.setWindowTitle("Ticker Output")
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.frame = QImage(w,h,QImage.Format_RGB888)
        self.frame.fill(Qt.black)
    def set_frame(self, img: QImage):
        self.frame = img
    def paintEvent(self, _):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, Qt.black)
        scaled = self.frame.scaled(r.size(), Qt.KeepAspectRatio, Qt.FastTransformation)
        p.drawImage((r.width()-scaled.width())//2,(r.height()-scaled.height())//2, scaled)
        p.end()

# ----------------------------- Main Window -----------------------------
class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Studio Rayy — Multi-Content Ticker (Master-Strip)")
        self.cfg = CFG()
        self.offset = 0.0
        # Offscreen frame buffer reused each frame
        self.frame_buffer = QImage(self.cfg.width, self.cfg.height, QImage.Format_RGB888)
        self.frame_buffer.fill(Qt.black)
        # FPS tracking
        self._fps_times = deque(maxlen=180)
        self._fps_value = 0.0

        self.current_preset_path: Optional[str] = None

        # Preset robust laden (Fallback: in-code Default)
        self.preset = self.load_initial_preset()

        self.module_w = self.preset["module"]["w"]
        self.module_h = self.preset["module"]["h"]
        self.concat_order = self.preset.get("concat_port_order", ["port1","port2"])
        self.rois_ports = build_rois_from_preset(self.preset)
        port_map = {p.port_id: p for p in self.rois_ports}
        self.dest_sequence = []
        for pid in self.concat_order:
            if pid in port_map:
                self.dest_sequence += port_map[pid].rects
        self.num_modules = max(1, len(self.dest_sequence))

        # Contents
        self.contents: Dict[str, ContentItem] = {}
        if not self.preset.get("contents"):
            self.preset["contents"] = [{
                "name":"Default","text":"STUDIO RAYY — ","font_family":"Arial","font_pt":72,
                "text_rgb":[255,255,255],"bg_rgb":[0,0,0]
            }]
        for c in self.preset["contents"]:
            item = ContentItem(c["name"], c["text"], c["font_family"], int(c["font_pt"]),
                               tuple(c["text_rgb"]), tuple(c["bg_rgb"]))
            self.contents[item.name] = item

        # Output (aus Preset übernehmen)
        out = self.preset.get("output", {})
        self.cfg.width  = int(out.get("width",  self.cfg.width))
        self.cfg.height = int(out.get("height", self.cfg.height))
        self.cfg.fps    = int(out.get("fps",    self.cfg.fps))
        self.cfg.speed_px_per_frame = float(out.get("speed_px_per_frame", self.cfg.speed_px_per_frame))

        # Scheduler
        self.scheduler = Scheduler(self.contents, self.preset.get("scheduler", {}).get("entries", []))
        self.current_content_name = self.scheduler.pick_content_name() or self.preset["contents"][0]["name"]

        # Live-Quelle (neu): "scheduler" | "manual"
        self.live_source = "scheduler"
        self.live_content_name: Optional[str] = None

        # Crossfade
        self.next_content_name: Optional[str] = None
        self.crossfade_active = False
        self.crossfade_start: Optional[datetime.datetime] = None
        self.crossfade_ms = 800

        # Strips
        self.strip_curr = self.make_strip(self.current_content_name)
        self.strip_next: Optional[MasterStrip] = None

        # Tabs
        tabs = QTabWidget()

        # -------- Contents Tab --------
        contents_tab = QWidget()
        v1 = QVBoxLayout(contents_tab)
        self.contents_list = QListWidget()
        for name in self.contents.keys():
            self.contents_list.addItem(name)
        v1.addWidget(QLabel("Contents (mehrere Presets für Text+Style):"))
        v1.addWidget(self.contents_list, 1)

        form = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("Name"))
        self.c_name = QLineEdit()
        left.addWidget(self.c_name)
        left.addWidget(QLabel("Text"))
        self.c_text = QLineEdit()
        left.addWidget(self.c_text)
        left.addWidget(QLabel("Font"))
        self.c_font = QFontComboBox()
        left.addWidget(self.c_font)
        left.addWidget(QLabel("Size"))
        self.c_size = QDoubleSpinBox()
        self.c_size.setRange(8,256)
        self.c_size.setDecimals(0)
        self.c_size.setSingleStep(1.0)
        self.c_size.setValue(72)
        left.addWidget(self.c_size)
        colorrow = QHBoxLayout()
        self.c_text_color = QPushButton("Textfarbe")
        self.c_bg_color = QPushButton("Hintergrund")
        self.c_text_color.clicked.connect(lambda: self.pick_content_color(True))
        self.c_bg_color.clicked.connect(lambda: self.pick_content_color(False))
        colorrow.addWidget(self.c_text_color)
        colorrow.addWidget(self.c_bg_color)
        left.addLayout(colorrow)
        form.addLayout(left, 1)
        right = QVBoxLayout()
        self.btn_add = QPushButton("Neu")
        self.btn_dup = QPushButton("Duplizieren")
        self.btn_del = QPushButton("Löschen")
        self.btn_content_commit = QPushButton("Änderungen übernehmen")
        right.addWidget(self.btn_add)
        right.addWidget(self.btn_dup)
        right.addWidget(self.btn_del)
        right.addWidget(self.btn_content_commit)
        v1.addLayout(form)
        v1.addLayout(right)
        tabs.addTab(contents_tab, "Contents")

        # -------- Scheduler Tab --------
        sched_tab = QWidget()
        v2 = QVBoxLayout(sched_tab)
        self.tbl = QTableWidget(0, 8)
        self.tbl.setHorizontalHeaderLabels(["Type","Weekdays","Date","Start","End","Content","Transition","Fade(ms)"])
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        v2.addWidget(QLabel("Scheduler (GUI):"))
        v2.addWidget(self.tbl, 1)
        rowb = QHBoxLayout()
        self.btn_add_row = QPushButton("Add Entry")
        self.btn_del_row = QPushButton("Delete Entry")
        self.btn_add_row.clicked.connect(self.add_sched_row)
        self.btn_del_row.clicked.connect(self.del_sched_row)
        rowb.addWidget(self.btn_add_row)
        rowb.addWidget(self.btn_del_row)
        v2.addLayout(rowb)
        tabs.addTab(sched_tab, "Scheduler")

        # -------- Output Tab --------
        out_tab = QWidget()
        v3 = QVBoxLayout(out_tab)

        # Control Row 1: Ausgabe/Live
        ctl = QHBoxLayout()

        # Ausgabeauflösung + FPS
        self.out_w = QSpinBox(); self.out_w.setRange(64, 8192); self.out_w.setValue(self.cfg.width)
        self.out_h = QSpinBox(); self.out_h.setRange(64, 8192); self.out_h.setValue(self.cfg.height)
        self.fps_box = QSpinBox(); self.fps_box.setRange(1, 240); self.fps_box.setValue(self.cfg.fps)
        ctl.addWidget(QLabel("W"));  ctl.addWidget(self.out_w)
        ctl.addWidget(QLabel("H"));  ctl.addWidget(self.out_h)
        ctl.addWidget(QLabel("FPS")); ctl.addWidget(self.fps_box)

        # **Neu: Live-Content-Auswahl**
        self.live_content_cb = QComboBox()
        ctl.addWidget(QLabel("Live-Content"))
        ctl.addWidget(self.live_content_cb)

        # Laufgeschwindigkeit & Overlay & Controls
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.01, 200.0)
        self.speed.setDecimals(1)
        self.speed.setSingleStep(0.05)
        self.speed.setValue(self.cfg.speed_px_per_frame)
        self.speed.valueChanged.connect(self.on_speed_changed)
        self.overlay_chk = QCheckBox("ROI-Overlay")
        self.overlay_chk.setChecked(True)
        self.live_btn = QPushButton("Live")
        self.stop_btn = QPushButton("Stop")
        self.full_btn = QPushButton("Fullscreen An/Aus")
        self.exp_btn = QPushButton("Export MP4")
        ctl.addWidget(QLabel("Geschwindigkeit (px/Frame)"))
        ctl.addWidget(self.speed)
        ctl.addWidget(self.overlay_chk)
        ctl.addWidget(self.live_btn)
        ctl.addWidget(self.stop_btn)
        ctl.addWidget(self.full_btn)
        ctl.addWidget(self.exp_btn)

        v3.addLayout(ctl)

        # Control Row 2: Crop
        crop_row = QHBoxLayout()
        self.crop_enable = QCheckBox("Zuschnitt aktiv")
        self.crop_x = QSpinBox(); self.crop_x.setRange(0, 32768); self.crop_x.setValue(0)
        self.crop_y = QSpinBox(); self.crop_y.setRange(0, 32768); self.crop_y.setValue(0)
        self.crop_w = QSpinBox(); self.crop_w.setRange(1, 32768); self.crop_w.setValue(self.cfg.width)
        self.crop_h = QSpinBox(); self.crop_h.setRange(1, 32768); self.crop_h.setValue(self.cfg.height)
        crop_row.addWidget(self.crop_enable)
        crop_row.addWidget(QLabel("X")); crop_row.addWidget(self.crop_x)
        crop_row.addWidget(QLabel("Y")); crop_row.addWidget(self.crop_y)
        crop_row.addWidget(QLabel("W")); crop_row.addWidget(self.crop_w)
        crop_row.addWidget(QLabel("H")); crop_row.addWidget(self.crop_h)
        v3.addLayout(crop_row)

        self.preview = Preview(self.cfg.width, self.cfg.height)
        v3.addWidget(self.preview, 1)
        tabs.addTab(out_tab, "Output")

        # -------- Preset Tab (Load/Save/Update) --------
        preset_tab = QWidget()
        v4 = QVBoxLayout(preset_tab)
        self.load_preset_btn = QPushButton("Preset laden")
        self.save_preset_btn = QPushButton("Preset speichern als…")
        self.update_preset_btn = QPushButton("Preset aktualisieren (überschreiben)")
        v4.addWidget(self.load_preset_btn)
        v4.addWidget(self.save_preset_btn)
        v4.addWidget(self.update_preset_btn)
        tabs.addTab(preset_tab, "Preset")

        # -------- Mapping Tab --------
        map_tab = QWidget()
        v5 = QVBoxLayout(map_tab)

        # Globale Modulgröße
        grow = QHBoxLayout()
        grow.addWidget(QLabel("Module W"))
        self.mod_w = QSpinBox(); self.mod_w.setRange(8, 8192); self.mod_w.setValue(self.module_w)
        grow.addWidget(self.mod_w)
        grow.addWidget(QLabel("Module H"))
        self.mod_h = QSpinBox(); self.mod_h.setRange(8, 8192); self.mod_h.setValue(self.module_h)
        grow.addWidget(self.mod_h)
        v5.addLayout(grow)

        # Ports Liste
        v5.addWidget(QLabel("Ports"))
        self.ports_list = QListWidget()
        v5.addWidget(self.ports_list, 1)

        prow = QHBoxLayout()
        self.btn_port_add = QPushButton("Port hinzufügen")
        self.btn_port_del = QPushButton("Port löschen")
        prow.addWidget(self.btn_port_add)
        prow.addWidget(self.btn_port_del)
        v5.addLayout(prow)

        # Port-Details
        det = QHBoxLayout()
        colL = QVBoxLayout()
        self.p_id = QLineEdit(); colL.addWidget(QLabel("Port-ID")); colL.addWidget(self.p_id)
        self.p_start_x = QSpinBox(); self.p_start_x.setRange(-8192, 8192)
        self.p_start_y = QSpinBox(); self.p_start_y.setRange(-8192, 8192)
        colL.addWidget(QLabel("Start X")); colL.addWidget(self.p_start_x)
        colL.addWidget(QLabel("Start Y")); colL.addWidget(self.p_start_y)
        det.addLayout(colL)
        colM = QVBoxLayout()
        self.p_mode = QComboBox(); self.p_mode.addItems(["vertical","horizontal"])
        self.p_path_mode = QComboBox(); self.p_path_mode.addItems(["snake","reset"])
        colM.addWidget(QLabel("Mode")); colM.addWidget(self.p_mode)
        colM.addWidget(QLabel("Pfadmodus")); colM.addWidget(self.p_path_mode)
        det.addLayout(colM)

        # Blocks Tabelle
        colR = QVBoxLayout()
        self.blocks_tbl = QTableWidget(0, 2)
        self.blocks_tbl.setHorizontalHeaderLabels(["dir","count"])
        self.blocks_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        colR.addWidget(QLabel("Blocks (Reihenfolge)"))
        colR.addWidget(self.blocks_tbl, 1)
        bbtn = QHBoxLayout()
        self.btn_blk_add = QPushButton("Block +")
        self.btn_blk_del = QPushButton("Block -")
        bbtn.addWidget(self.btn_blk_add); bbtn.addWidget(self.btn_blk_del)
        colR.addLayout(bbtn)
        det.addLayout(colR)
        v5.addLayout(det)

        # Actions
        actrow = QHBoxLayout()
        self.btn_apply_mapping = QPushButton("Mapping anwenden")
        self.btn_save_mapping_into_preset = QPushButton("Mapping ins Preset schreiben (nicht speichern)")
        actrow.addWidget(self.btn_apply_mapping)
        actrow.addWidget(self.btn_save_mapping_into_preset)
        v5.addLayout(actrow)

        tabs.addTab(map_tab, "Mapping")
        self.setCentralWidget(tabs)

        # Connections
        self.contents_list.currentItemChanged.connect(self.on_select_content)
        self.btn_add.clicked.connect(self.add_content)
        self.btn_dup.clicked.connect(self.dup_content)
        self.btn_del.clicked.connect(self.del_content)
        self.btn_content_commit.clicked.connect(self.commit_current_content_edits)

        self.live_btn.clicked.connect(self.start_live)
        self.stop_btn.clicked.connect(self.stop_live)
        self.full_btn.clicked.connect(self.fullscreen_toggle)
        self.exp_btn.clicked.connect(self.export_video)

        self.load_preset_btn.clicked.connect(self.load_preset)
        self.save_preset_btn.clicked.connect(self.save_preset_as)
        self.update_preset_btn.clicked.connect(self.update_preset)

        self.ports_list.currentItemChanged.connect(self.on_select_port)
        self.btn_port_add.clicked.connect(self.add_port)
        self.btn_port_del.clicked.connect(self.del_port)
        self.btn_blk_add.clicked.connect(self.add_block)
        self.btn_blk_del.clicked.connect(self.del_block)
        self.btn_apply_mapping.clicked.connect(self.apply_mapping_runtime)
        self.btn_save_mapping_into_preset.clicked.connect(self.commit_mapping_into_preset)

        # Ausgabe-Änderungen live übernehmen
        self.out_w.valueChanged.connect(self.on_out_res_changed)
        self.out_h.valueChanged.connect(self.on_out_res_changed)
        self.fps_box.valueChanged.connect(self.on_fps_changed)

        # **Neu: Live-Content-Änderung**
        self.live_content_cb.currentTextChanged.connect(self.on_live_content_changed)
        self.refresh_live_content_cb()  # Dropdown initial befüllen

        if self.contents_list.count() > 0:
            self.contents_list.setCurrentRow(0)
        self.load_scheduler_into_table()
        self.load_mapping_into_gui()

        self.out_win = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.resize(1450, 1000)

    # -------- Initial Preset Load --------
    def load_initial_preset(self) -> dict:
        candidates = [
            resource_path("presets", "fhd50_two_ports_128x256_zigzag_multicontent.json"),
            os.path.join("presets", "fhd50_two_ports_128x256_zigzag_multicontent.json")
        ]
        for p in candidates:
            if os.path.exists(p):
                try:
                    self.current_preset_path = p
                    return json.load(open(p,"r"))
                except Exception:
                    pass
        # Fallback – in-code Default
        self.current_preset_path = None
        return {
            "name": "Default_FHD50",
            "output": {"width": 1920, "height": 1080, "fps": 50, "speed_px_per_frame": 1.0},
            "module": {"w": 128, "h": 256},
            "ports": [
                {"id":"port1","start":{"x":0,"y":824},"mode":"vertical","path_mode":"snake",
                 "blocks":[{"dir":"bottom_up","count":4},{"dir":"top_down","count":4},{"dir":"bottom_up","count":4}]},
                {"id":"port2","start":{"x":128,"y":824},"mode":"vertical","path_mode":"snake",
                 "blocks":[{"dir":"bottom_up","count":4},{"dir":"top_down","count":4},{"dir":"bottom_up","count":4}]}
            ],
            "concat_port_order": ["port1","port2"],
            "contents": [{
                "name":"Default","text":"STUDIO RAYY — ","font_family":"Arial","font_pt":72,
                "text_rgb":[255,255,255],"bg_rgb":[0,0,0]
            }],
            "scheduler": {"entries": []}
        }

    # -------- Content mgmt --------
    def on_select_content(self, cur: QListWidgetItem, _):
        if not cur:
            return
        c = self.contents.get(cur.text())
        if not c:
            return
        self.c_name.setText(c.name)
        self.c_text.setText(c.text)
        self.c_font.setCurrentFont(QFont(c.font_family))
        self.c_size.setValue(float(c.font_pt))

    def pick_content_color(self, text=True):
        name = self.c_name.text().strip()
        if name not in self.contents:
            return
        citem = self.contents[name]
        current = QColor(*(citem.text_rgb if text else citem.bg_rgb))
        chosen = QColorDialog.getColor(current, self, "Farbe wählen")
        if chosen.isValid():
            if text:
                citem.text_rgb = (chosen.red(), chosen.green(), chosen.blue())
            else:
                citem.bg_rgb = (chosen.red(), chosen.green(), chosen.blue())

    def add_content(self):
        base = ContentItem(f"Content{len(self.contents)+1}", "NEUER TEXT — ", "Arial", 72, (255,255,255), (0,0,0))
        self.contents[base.name] = base
        self.contents_list.addItem(base.name)
        self.contents_list.setCurrentRow(self.contents_list.count()-1)
        self.refresh_live_content_cb()

    def dup_content(self):
        cur = self.contents_list.currentItem()
        if not cur:
            return
        old = self.contents[cur.text()]
        i = 1
        new_name = old.name + "_copy"
        while new_name in self.contents:
            i += 1
            new_name = f"{old.name}_copy{i}"
        self.contents[new_name] = ContentItem(new_name, old.text, old.font_family, old.font_pt, old.text_rgb, old.bg_rgb)
        self.contents_list.addItem(new_name)
        self.contents_list.setCurrentRow(self.contents_list.count()-1)
        self.refresh_live_content_cb()

    def del_content(self):
        cur = self.contents_list.currentItem()
        if not cur:
            return
        name = cur.text()
        self.contents.pop(name, None)
        self.contents_list.takeItem(self.contents_list.currentRow())
        # Live-Auswahl korrigieren, falls gelöscht
        if self.live_source == "manual" and self.live_content_name == name:
            self.live_source = "scheduler"
            self.live_content_name = None
        if self.current_content_name == name:
            self.current_content_name = next(iter(self.contents.keys()), None) or ""
            self.strip_curr = self.make_strip(self.current_content_name)
        self.refresh_live_content_cb()

    def make_strip(self, name: Optional[str]) -> Optional[MasterStrip]:
        if not name or name not in self.contents:
            return None
        c = self.contents[name]
        return MasterStrip(c.text, c.font_family, int(c.font_pt), c.text_rgb, c.bg_rgb,
                           self.module_w, self.module_h, self.num_modules)

    # **Neu:** Live-Content Dropdown befüllen/aktualisieren
    def refresh_live_content_cb(self):
        current = self.live_content_cb.currentText() if self.live_content_cb.count() else None
        self.live_content_cb.blockSignals(True)
        self.live_content_cb.clear()
        self.live_content_cb.addItem("Scheduler (auto)")
        for name in self.contents.keys():
            self.live_content_cb.addItem(name)
        # Wiederherstellen
        if self.live_source == "manual" and self.live_content_name in self.contents:
            self.live_content_cb.setCurrentText(self.live_content_name)
        else:
            self.live_content_cb.setCurrentIndex(0)
        self.live_content_cb.blockSignals(False)

    # **Neu:** Reaktion auf Auswahlwechsel im Live-Content
    def on_live_content_changed(self, text: str):
        if text.startswith("Scheduler"):
            self.live_source = "scheduler"
            self.live_content_name = None
            # Optional: sofort auf Scheduler-Inhalt wechseln (Cut)
            target = self.scheduler.pick_content_name() or next(iter(self.contents.keys()), None)
            if target and target != self.current_content_name:
                self.current_content_name = target
                self.strip_curr = self.make_strip(self.current_content_name)
                self.crossfade_active = False; self.crossfade_start = None; self.strip_next = None
            return

        # manueller Inhalt
        self.live_source = "manual"
        self.live_content_name = text
        if self.live_content_name != self.current_content_name:
            self.current_content_name = self.live_content_name
            self.strip_curr = self.make_strip(self.current_content_name)
            self.crossfade_active = False; self.crossfade_start = None; self.strip_next = None
        else:
            # ggf. Fonts/Farben aktualisieren, falls geändert
            self.strip_curr = self.make_strip(self.current_content_name)

    # -------- Scheduler Table --------
    def load_scheduler_into_table(self):
        self.tbl.setRowCount(0)
        for e in self.scheduler.entries:
            self.add_sched_row(e)

    def add_sched_row(self, e: dict = None):
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        type_cb = QComboBox(); type_cb.addItems(["daily","date"])
        if e: type_cb.setCurrentText(e.get("type","daily"))
        self.tbl.setCellWidget(r, 0, type_cb)
        self.tbl.setItem(r, 1, QTableWidgetItem(",".join(map(str, e.get("weekdays", []))) if e else ""))
        self.tbl.setItem(r, 2, QTableWidgetItem(e.get("date","") if e else ""))
        self.tbl.setItem(r, 3, QTableWidgetItem(e.get("start","08:00") if e else "08:00"))
        self.tbl.setItem(r, 4, QTableWidgetItem(e.get("end","18:00") if e else "18:00"))
        content_cb = QComboBox(); content_cb.addItems(list(self.contents.keys()) or [""])
        if e and e.get("content") in self.contents: content_cb.setCurrentText(e["content"])
        self.tbl.setCellWidget(r, 5, content_cb)
        trans_cb = QComboBox(); trans_cb.addItems(["crossfade","cut"])
        if e: trans_cb.setCurrentText(e.get("transition","crossfade"))
        self.tbl.setCellWidget(r, 6, trans_cb)
        self.tbl.setItem(r, 7, QTableWidgetItem(str(e.get("fade_ms", 800)) if e else "800"))

    def del_sched_row(self):
        r = self.tbl.currentRow()
        if r >= 0:
            self.tbl.removeRow(r)

    def read_scheduler_from_table(self) -> List[dict]:
        entries = []
        for r in range(self.tbl.rowCount()):
            typ = self.tbl.cellWidget(r,0).currentText()
            wd_text = self.tbl.item(r,1).text().strip() if self.tbl.item(r,1) else ""
            weekdays = [int(x) for x in wd_text.split(",") if x.strip().isdigit()] if wd_text else []
            date = self.tbl.item(r,2).text().strip() if self.tbl.item(r,2) else ""
            start = self.tbl.item(r,3).text().strip() if self.tbl.item(r,3) else "00:00"
            end   = self.tbl.item(r,4).text().strip() if self.tbl.item(r,4) else "23:59"
            content = self.tbl.cellWidget(r,5).currentText()
            transition = self.tbl.cellWidget(r,6).currentText()
            fade_ms = int(self.tbl.item(r,7).text().strip()) if self.tbl.item(r,7) and self.tbl.item(r,7).text().strip().isdigit() else 800
            ent = {"type": typ, "start": start, "end": end, "content": content, "transition": transition, "fade_ms": fade_ms}
            if typ == "daily":
                ent["weekdays"] = weekdays
            else:
                ent["date"] = date
            entries.append(ent)
        return entries

    # -------- Mapping GUI --------
    def load_mapping_into_gui(self):
        self.mod_w.setValue(self.preset.get("module",{}).get("w", self.module_w))
        self.mod_h.setValue(self.preset.get("module",{}).get("h", self.module_h))
        self.ports_list.clear()
        for p in self.preset.get("ports", []):
            self.ports_list.addItem(p.get("id","port"))

        if self.ports_list.count() > 0:
            self.ports_list.setCurrentRow(0)
        else:
            self.preset.setdefault("ports", []).append({
                "id":"port1", "start":{"x":0,"y":824}, "mode":"vertical", "path_mode":"snake",
                "blocks":[{"dir":"bottom_up","count":4}]
            })
            self.ports_list.addItem("port1")
            self.ports_list.setCurrentRow(0)

    def current_port_ref(self) -> Optional[dict]:
        row = self.ports_list.currentRow()
        if row < 0: return None
        return self.preset.get("ports", [])[row]

    def on_select_port(self, cur: QListWidgetItem, _):
        p = self.current_port_ref()
        if not p: return
        self.p_id.setText(p.get("id",""))
        sx = p.get("start",{}).get("x", 0)
        sy = p.get("start",{}).get("y", 0)
        self.p_start_x.setValue(int(sx))
        self.p_start_y.setValue(int(sy))
        self.p_mode.setCurrentText(p.get("mode","vertical"))
        self.p_path_mode.setCurrentText(p.get("path_mode","snake"))
        # Blocks
        blks = p.get("blocks", [])
        self.blocks_tbl.setRowCount(0)
        for b in blks:
            r = self.blocks_tbl.rowCount()
            self.blocks_tbl.insertRow(r)
            dir_cb = QComboBox()
            dir_cb.addItems(["bottom_up","top_down","left_right","right_left"])
            dir_cb.setCurrentText(str(b.get("dir","bottom_up")))
            self.blocks_tbl.setCellWidget(r, 0, dir_cb)
            cnt_item = QTableWidgetItem(str(b.get("count",1)))
            self.blocks_tbl.setItem(r, 1, cnt_item)

    def add_port(self):
        base = {"id": f"port{len(self.preset.get('ports',[]))+1}",
                "start":{"x":0,"y":824},"mode":"vertical","path_mode":"snake",
                "blocks":[{"dir":"bottom_up","count":4}]}
        self.preset.setdefault("ports", []).append(base)
        self.ports_list.addItem(base["id"])
        self.ports_list.setCurrentRow(self.ports_list.count()-1)

    def del_port(self):
        row = self.ports_list.currentRow()
        if row < 0: return
        self.preset.get("ports", []).pop(row)
        self.ports_list.takeItem(row)
        if self.ports_list.count() > 0:
            self.ports_list.setCurrentRow(0)

    def add_block(self):
        r = self.blocks_tbl.rowCount()
        self.blocks_tbl.insertRow(r)
        dir_cb = QComboBox()
        dir_cb.addItems(["bottom_up","top_down","left_right","right_left"])
        dir_cb.setCurrentIndex(0)
        self.blocks_tbl.setCellWidget(r, 0, dir_cb)
        self.blocks_tbl.setItem(r, 1, QTableWidgetItem("4"))

    def del_block(self):
        r = self.blocks_tbl.currentRow()
        if r >= 0:
            self.blocks_tbl.removeRow(r)

    def commit_mapping_from_gui(self):
        self.preset.setdefault("module",{})["w"] = int(self.mod_w.value())
        self.preset.setdefault("module",{})["h"] = int(self.mod_h.value())
        p = self.current_port_ref()
        if not p:
            return
        old_id = p.get("id","")
        new_id = self.p_id.text().strip() or old_id
        p["id"] = new_id
        cpo = self.preset.setdefault("concat_port_order", [])
        for i, pid in enumerate(cpo):
            if pid == old_id:
                cpo[i] = new_id

        p.setdefault("start",{})["x"] = int(self.p_start_x.value())
        p.setdefault("start",{})["y"] = int(self.p_start_y.value())
        p["mode"] = self.p_mode.currentText()
        p["path_mode"] = self.p_path_mode.currentText()

        blks = []
        for r in range(self.blocks_tbl.rowCount()):
            dir_widget = self.blocks_tbl.cellWidget(r, 0)
            dirv = dir_widget.currentText() if isinstance(dir_widget, QComboBox) else "bottom_up"
            cnt_item = self.blocks_tbl.item(r, 1)
            cnt  = cnt_item.text().strip() if cnt_item else "1"
            try:
                cnti = max(0, int(cnt))
            except:
                cnti = 0
            blks.append({"dir": dirv, "count": cnti})
        p["blocks"] = blks

        row = self.ports_list.currentRow()
        if row >= 0:
            self.ports_list.item(row).setText(new_id)

    def commit_mapping_into_preset(self):
        self.commit_mapping_from_gui()
        QMessageBox.information(self, "Mapping", "Mapping wurde ins Preset übernommen (noch nicht gespeichert).")

    def apply_mapping_runtime(self):
        self.commit_mapping_from_gui()
        self.apply_preset(self.preset)
        QMessageBox.information(self, "Mapping", "Mapping angewendet.")

    # -------- Ausgabe-Änderungen --------
    def on_out_res_changed(self):
        self.cfg.width = int(self.out_w.value())
        self.cfg.height = int(self.out_h.value())
        # Recreate frame buffer to match resolution
        self.frame_buffer = QImage(self.cfg.width, self.cfg.height, QImage.Format_RGB888)
        self.frame_buffer.fill(Qt.black)

    def on_fps_changed(self):
        self.cfg.fps = int(self.fps_box.value())
        if self.timer.isActive():
            self.timer.start(int(1000 / max(1, self.cfg.fps)))
        self.preset.setdefault("output", {})["fps"] = self.cfg.fps
    # NEU: Speed-Änderungen fortschreiben
    def on_speed_changed(self):
        self.cfg.speed_px_per_frame = float(self.speed.value())
        self.preset.setdefault("output", {})["speed_px_per_frame"] = self.cfg.speed_px_per_frame
        
    # -------- Render mapping --------
    @staticmethod
    def _draw_wrapped_h(p: QPainter, src_img: QImage, dst_x: int, dst_y: int, dst_w: int, dst_h: int, start_x: int):
        W = src_img.width()  # erwartet strip.double_h => 2*text_w
        x = start_x % W
        remaining = dst_w
        dx = dst_x
        while remaining > 0:
            take = min(remaining, W - x)
            p.drawImage(QRect(dx, dst_y, take, dst_h), src_img, QRect(x, 0, take, src_img.height()))
            remaining -= take
            dx += take
            x = 0
    
    @staticmethod
    def _draw_wrapped_v(p: QPainter, src_img: QImage, dst_x: int, dst_y: int, dst_w: int, dst_h: int, start_y: int):
        H = src_img.height()  # erwartet strip.double_v => 2*text_w
        y = start_y % H
        remaining = dst_h
        dy = dst_y
        while remaining > 0:
            take = min(remaining, H - y)
            p.drawImage(QRect(dst_x, dy, dst_w, take), src_img, QRect(0, y, src_img.width(), take))
            remaining -= take
            dy += take
            y = 0
    
    def render_mapped_frame(self, strip: Optional[MasterStrip], offset_px: float) -> QImage:
        # reuse offscreen buffer
        frame = self.frame_buffer
        bg = (0,0,0)
        if self.current_content_name in self.contents:
            bg = self.contents[self.current_content_name].bg_rgb
        frame.fill(QColor(*bg))
        if strip is None:
            return frame
    
        p = QPainter(frame)
        for idx, (dx,dy,dw,dh,dirv) in enumerate(self.dest_sequence):
            if dirv in ("left_right","right_left"):
                reverse = (dirv == "right_left")
                start_x = strip.tile_src_rect_h(offset_px, idx, reverse=reverse).x()
                self._draw_wrapped_h(p, strip.double_h, dx, dy, dw, dh, start_x)
    
            elif dirv in ("top_down","bottom_up"):
                reverse = (dirv == "bottom_up")
                start_y = strip.tile_src_rect_v(offset_px, idx, reverse=reverse).y()
                self._draw_wrapped_v(p, strip.double_v, dx, dy, dw, dh, start_y)
    
            else:
                start_x = strip.tile_src_rect_h(offset_px, idx, reverse=False).x()
                self._draw_wrapped_h(p, strip.double_h, dx, dy, dw, dh, start_x)
        p.end()
        return frame

    def draw_fps_overlay(self, img: QImage, fps: float):
        p = QPainter(img)
        p.setRenderHint(QPainter.TextAntialiasing, False)
        # small semi-transparent box
        rect_w, rect_h = 110, 36
        margin = 8
        x = img.width() - rect_w - margin
        y = margin
        # background
        p.fillRect(QRect(x, y, rect_w, rect_h), QColor(0,0,0,160))
        p.setPen(QColor(255,255,255))
        p.setFont(QFont("Arial", 12))
        text = f"FPS: {fps:0.1f}"
        p.drawText(x+10, y+22, text)
        p.end()

    # -------- Loop --------
    def start_live(self):
        self.timer.start(int(1000 / self.cfg.fps))

    def stop_live(self):
        self.timer.stop()

    def fullscreen_toggle(self):
        screens = QGuiApplication.screens()
        if not hasattr(self, "out_win") or self.out_win is None:
            self.out_win = OutputWindow(self.cfg.width, self.cfg.height)
        if self.out_win.isVisible():
            ret = QMessageBox.question(
                self, "Fullscreen beenden?", "Fullscreen wirklich ausschalten?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if ret == QMessageBox.Yes:
                self.out_win.close()
                self.out_win = None
        else:
            if len(screens) < 2:
                QMessageBox.information(self, "Info", "Kein zweiter Bildschirm erkannt.")
                return
            g = screens[1].geometry()
            self.out_win.setGeometry(g)
            self.out_win.showFullScreen()

    def composite_crossfade(self, a: QImage, b: QImage, alpha: float) -> QImage:
        out = QImage(a.size(), QImage.Format_RGB888)
        p = QPainter(out)
        p.drawImage(0,0,a)
        p.setOpacity(alpha)
        p.drawImage(0,0,b)
        p.setOpacity(1.0)
        p.end()
        return out

    def choose_target_content(self) -> Tuple[Optional[str], Optional[dict]]:
        now = datetime.datetime.now()
        for e in self.scheduler.entries:
            if e.get("type") == "date" and e.get("date") == now.date().isoformat():
                if in_range(now.time(), parse_time(e["start"]), parse_time(e["end"])):
                    return e.get("content"), e
        for e in self.scheduler.entries:
            if e.get("type") == "daily" and now.weekday() in (e.get("weekdays") or []):
                if in_range(now.time(), parse_time(e["start"]), parse_time(e["end"])):
                    return e.get("content"), e
        return None, None

    def tick(self):
        # Quelle bestimmen
        if self.live_source == "manual":
            desired = self.live_content_name or next(iter(self.contents.keys()), None) or self.current_content_name
            if desired != self.current_content_name:
                self.current_content_name = desired
                self.strip_curr = self.make_strip(self.current_content_name)
                self.strip_next = None
                self.crossfade_active = False
                self.crossfade_start = None
        else:
            # Scheduler-Modus
            target_name, entry = self.choose_target_content()
            fallback = next(iter(self.contents.keys()), None)
            desired = target_name or fallback or self.current_content_name
            if desired != self.current_content_name and not self.crossfade_active:
                trans = (entry or {}).get("transition","crossfade")
                if trans == "cut":
                    self.current_content_name = desired
                    self.strip_curr = self.make_strip(self.current_content_name)
                else:
                    self.next_content_name = desired
                    self.strip_next = self.make_strip(self.next_content_name)
                    self.crossfade_ms = int((entry or {}).get("fade_ms", 800))
                    self.crossfade_active = True
                    self.crossfade_start = datetime.datetime.now()

        # Bewegung
        self.cfg.speed_px_per_frame = float(self.speed.value())
        self.offset += self.cfg.speed_px_per_frame

        base_curr = self.render_mapped_frame(self.strip_curr, self.offset)
        frame = base_curr
        if self.live_source == "scheduler" and self.crossfade_active and self.strip_next is not None and self.crossfade_start is not None:
            elapsed = (datetime.datetime.now() - self.crossfade_start).total_seconds()*1000.0
            a = max(0.0, min(1.0, elapsed / max(1, self.crossfade_ms)))
            base_next = self.render_mapped_frame(self.strip_next, self.offset)
            frame = self.composite_crossfade(base_curr, base_next, a)
            if elapsed >= self.crossfade_ms:
                self.current_content_name = self.next_content_name
                self.strip_curr = self.strip_next
                self.strip_next = None
                self.crossfade_active = False
                self.crossfade_start = None

        # --- FPS tracking ---
        now_ms = datetime.datetime.now().timestamp() * 1000.0
        self._fps_times.append(now_ms)
        if len(self._fps_times) >= 6:
            span = self._fps_times[-1] - self._fps_times[0]
            if span > 0:
                self._fps_value = 1000.0 * (len(self._fps_times)-1) / span

        # Draw FPS overlay onto the display frame (non-invasive to export pipeline)
        self.draw_fps_overlay(frame, self._fps_value)

        vis = draw_roi_overlay(frame, self.rois_ports) if self.overlay_chk.isChecked() else frame
        self.preview.set_frame(vis)
        if hasattr(self, "out_win") and self.out_win and self.out_win.isVisible():
            self.out_win.set_frame(frame)
            self.out_win.update()

    # -------- Preset IO --------
    def load_preset(self):
        start_dir = resource_path("presets")
        path, _ = QFileDialog.getOpenFileName(self, "Preset laden", start_dir, "JSON (*.json)")
        if not path:
            return
        try:
            p = json.load(open(path,"r"))
            self.current_preset_path = path
            self.apply_preset(p)
            QMessageBox.information(self, "OK", f"Preset geladen:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Preset konnte nicht geladen werden: {e}")

    def snapshot_preset_from_state(self) -> dict:
        # Inhalte & Scheduler übernehmen
        self.commit_current_content_edits()
        entries = self.read_scheduler_from_table()
        # Mapping übernehmen
        self.commit_mapping_from_gui()
        return {
            "name": self.preset.get("name","Custom_FHD50_MultiContent"),
            "output": {
                "width": int(self.out_w.value()),
                "height": int(self.out_h.value()),
                "fps": int(self.fps_box.value()),
                "speed_px_per_frame": float(self.speed.value())
            },
            "module": {"w": int(self.mod_w.value()), "h": int(self.mod_h.value())},
            "ports": self.preset.get("ports", []),
            "concat_port_order": self.preset.get("concat_port_order", ["port1","port2"]),
            "contents": [
                {"name": c.name, "text": c.text, "font_family": c.font_family, "font_pt": int(c.font_pt),
                 "text_rgb": list(c.text_rgb), "bg_rgb": list(c.bg_rgb)}
                for c in self.contents.values()
            ],
            "scheduler": {"entries": entries}
        }

    def save_preset_as(self):
        data = self.snapshot_preset_from_state()
        default_path = os.path.join(os.path.expanduser("~"), f"{data.get('name','preset')}.json")
        path, _ = QFileDialog.getSaveFileName(self, "Preset speichern unter…", default_path, "JSON (*.json)")
        if not path:
            return
        try:
            json.dump(data, open(path,"w"), indent=2)
            self.current_preset_path = path
            QMessageBox.information(self, "OK", f"Preset gespeichert:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Konnte Preset nicht speichern: {e}")

    def update_preset(self):
        """Speichern-Button: Änderungen in DIE aktuell geladene Preset-Datei schreiben."""
        data = self.snapshot_preset_from_state()
        if not self.current_preset_path:
            return self.save_preset_as()
        try:
            json.dump(data, open(self.current_preset_path,"w"), indent=2)
            QMessageBox.information(self, "OK", f"Preset aktualisiert:\n{self.current_preset_path}")
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Konnte Preset nicht aktualisieren: {e}")

    def apply_preset(self, p: dict):
        self.preset = p
        self.module_w = p.get("module",{}).get("w",128)
        self.module_h = p.get("module",{}).get("h",256)
        self.concat_order = p.get("concat_port_order", ["port1","port2"])
        self.rois_ports = build_rois_from_preset(p)
        port_map = {r.port_id: r for r in self.rois_ports}
        self.dest_sequence = []
        for pid in self.concat_order:
            if pid in port_map:
                self.dest_sequence += port_map[pid].rects
        self.num_modules = max(1, len(self.dest_sequence))

        # Contents
        self.contents.clear()
        self.contents_list.clear()
        if not p.get("contents"):
            p["contents"] = [{"name":"Default","text":"STUDIO RAYY — ","font_family":"Arial","font_pt":72,"text_rgb":[255,255,255],"bg_rgb":[0,0,0]}]
        for c in p["contents"]:
            item = ContentItem(c["name"], c["text"], c["font_family"], int(c["font_pt"]),
                               tuple(c["text_rgb"]), tuple(c["bg_rgb"]))
            self.contents[item.name] = item
            self.contents_list.addItem(item.name)
        if self.contents_list.count() > 0:
            self.contents_list.setCurrentRow(0)

        self.scheduler = Scheduler(self.contents, p.get("scheduler", {}).get("entries", []))
        self.tbl.setRowCount(0)
        self.load_scheduler_into_table()
        self.load_mapping_into_gui()

        # Output übernehmen
        out = p.get("output", {})
        self.cfg.width  = int(out.get("width",  self.cfg.width))
        self.cfg.height = int(out.get("height", self.cfg.height))
        self.cfg.fps    = int(out.get("fps",    self.cfg.fps))
        self.out_w.setValue(self.cfg.width)
        self.out_h.setValue(self.cfg.height)
        self.fps_box.setValue(self.cfg.fps)
        self.speed.setValue(self.cfg.speed_px_per_frame)
        
        # Live-Content-Liste aktualisieren (Dropdown)
        self.refresh_live_content_cb()

        self.current_content_name = self.scheduler.pick_content_name() or p["contents"][0]["name"]
        self.strip_curr = self.make_strip(self.current_content_name)
        self.strip_next = None
        self.crossfade_active = False
        self.crossfade_start = None
        self.offset = 0.0
        # Offscreen frame buffer reused each frame
        self.frame_buffer = QImage(self.cfg.width, self.cfg.height, QImage.Format_RGB888)
        self.frame_buffer.fill(Qt.black)
        # FPS tracking
        self._fps_times = deque(maxlen=180)
        self._fps_value = 0.0


    def commit_current_content_edits(self):
        name = self.c_name.text().strip()
        if not name:
            return
        cur_item = self.contents_list.currentItem()
        # Rename-Logik
        if cur_item and name != cur_item.text():
            if name in self.contents:
                QMessageBox.warning(self, "Hinweis", "Name existiert bereits.")
                return
            old_name = cur_item.text()
            c = self.contents.pop(old_name)
            c.name = name
            self.contents[name] = c
            cur_item.setText(name)
            # **Neu:** Live-/Current-Namen fortschreiben
            if self.current_content_name == old_name:
                self.current_content_name = name
            if self.live_source == "manual" and self.live_content_name == old_name:
                self.live_content_name = name
        # Sicherstellen, dass Eintrag existiert
        if name not in self.contents:
            self.contents[name] = ContentItem(name, "", "Arial", 72, (255,255,255), (0,0,0))
        c = self.contents[name]
        # Änderungen übernehmen
        c.text = self.c_text.text()
        c.font_family = self.c_font.currentFont().family()
        c.font_pt = int(self.c_size.value())

        # **Neu:** Falls der aktuell sichtbare Content geändert wurde → Strip neu bauen (Fonts/Farben sofort live)
        if self.current_content_name == name:
            self.strip_curr = self.make_strip(self.current_content_name)

        # Dropdown aktualisieren
        self.refresh_live_content_cb()

    # -------- Export --------
    def export_video(self):
        ffmpeg = resolve_ffmpeg()
        if not ffmpeg or not os.path.isfile(ffmpeg):
            QMessageBox.critical(self, "FFmpeg fehlt", "FFmpeg wurde nicht gefunden/bündelt.")
            return
        if not self.current_content_name:
            QMessageBox.critical(self, "Fehler", "Kein Content ausgewählt.")
            return
        int_speed = max(1, int(round(self.cfg.speed_px_per_frame)))
        period = self.strip_curr.period_frames(int_speed) if self.strip_curr else int(self.cfg.fps)
        path, _ = QFileDialog.getSaveFileName(self, "Export", os.path.join(os.path.expanduser("~"), "ticker_export.mp4"), "MP4 (*.mp4)")
        if not path:
            return

        # Zielgröße: ganze Stage oder Crop
        full_w, full_h = self.cfg.width, self.cfg.height
        exp_x = int(self.crop_x.value())
        exp_y = int(self.crop_y.value())
        exp_w = int(self.crop_w.value())
        exp_h = int(self.crop_h.value())
        if not self.crop_enable.isChecked():
            exp_x, exp_y, exp_w, exp_h = 0, 0, full_w, full_h

        # Clamp
        exp_x = max(0, min(exp_x, full_w-1))
        exp_y = max(0, min(exp_y, full_h-1))
        exp_w = max(1, min(exp_w, full_w - exp_x))
        exp_h = max(1, min(exp_h, full_h - exp_y))

        cmd = [
            ffmpeg, "-y",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", f"{exp_w}x{exp_h}",
            "-r", str(self.cfg.fps),
            "-i", "pipe:0",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "veryfast",
            "-b:v", "20M", "-maxrate", "20M", "-bufsize", "40M",
            "-movflags", "+faststart",
            path
        ]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            local = 0.0
            for _ in range(period):
                frame = self.render_mapped_frame(self.strip_curr, self.offset + local)  # volle Stage
                # Crop anwenden
                crop_rect = QRect(exp_x, exp_y, exp_w, exp_h)
                crop_frame = frame.copy(crop_rect)
                # in Pipe schreiben
                ptr = crop_frame.bits()
                ptr.setsize(crop_frame.sizeInBytes())
                proc.stdin.write(bytes(ptr))
                local += int_speed
            proc.stdin.close()
            proc.wait()
            QMessageBox.information(
                self, "Fertig",
                f"Export abgeschlossen. Frames {period}, Größe {exp_w}x{exp_h}@{self.cfg.fps}.\n"
                f"Crop: {'aktiv' if self.crop_enable.isChecked() else 'aus'} ({exp_x},{exp_y},{exp_w},{exp_h})"
            )
        except Exception as e:
            QMessageBox.critical(self, "Exportfehler", str(e))

def main():
    app = QApplication(sys.argv)
    win = Main()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
