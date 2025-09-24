# main.py (v2 — semicolon if-fix, preset fallback, ffmpeg, gui)
import sys, os, shutil, subprocess, json, datetime
from math import gcd
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict

from PySide6.QtCore import Qt, QTimer, QRect
from PySide6.QtGui import QPainter, QImage, QColor, QFont, QFontMetrics, QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QColorDialog, QDoubleSpinBox, QFileDialog,
    QVBoxLayout, QHBoxLayout, QMainWindow, QMessageBox, QCheckBox, QFontComboBox, QListWidget, QListWidgetItem,
    QTabWidget, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView
)

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
    speed_px_per_frame: float = 4.0

class PortROI:
    def __init__(self, port_id: str, rects: List[Tuple[int,int,int,int]]):
        self.port_id = port_id
        self.rects = rects

def build_rois_from_preset(preset) -> List[PortROI]:
    module_w = preset.get("module", {}).get("w", 128)
    module_h = preset.get("module", {}).get("h", 256)
    ports = preset.get("ports")
    ports_out: List[PortROI] = []
    if not ports:
        ports = [
            {"id":"port1","x":0,"blocks":[{"order":"bottom_up","count":4},{"order":"top_down","count":4},{"order":"bottom_up","count":4}]},
            {"id":"port2","x":128,"blocks":[{"order":"bottom_up","count":4},{"order":"top_down","count":4},{"order":"bottom_up","count":4}]}
        ]
        preset["ports"] = ports
        preset["module"] = {"w": module_w, "h": module_h}
    for port in ports:
        x = port["x"]
        rects = []
        for blk in port["blocks"]:
            order = blk["order"]
            cnt = blk["count"]
            ys_bottom_up = [824, 568, 312, 56]
            ys_top_down  = [0, 256, 512, 768]
            ys = ys_bottom_up if order == "bottom_up" else ys_top_down
            rects += [(x, y, module_w, module_h) for y in ys[:cnt]]
        ports_out.append(PortROI(port["id"], rects))
    return ports_out

def draw_roi_overlay(img: QImage, rois: List[PortROI]) -> QImage:
    out = img.copy()
    p = QPainter(out)
    colors = [QColor(0,255,0), QColor(0,180,255)]
    for idx, port in enumerate(rois):
        p.setPen(colors[idx % len(colors)])
        for i,(x,y,w,h) in enumerate(port.rects):
            p.drawRect(x,y,w,h)
            p.drawText(x+4,y+18,f"{port.port_id}:{i+1}")
    p.end()
    return out

class MasterStrip:
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
        h = self.module_h
        self.single = QImage(self.text_w, h, QImage.Format_RGB888)
        self.single.fill(QColor(*self.bg_rgb))
        p = QPainter(self.single)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        p.setPen(QColor(*self.text_rgb))
        p.setFont(self.font)
        baseline = (h + fm.ascent() - fm.descent()) // 2
        p.drawText(0, baseline, self.text)
        p.end()
        self.double = QImage(self.text_w*2, h, QImage.Format_RGB888)
        p2 = QPainter(self.double)
        p2.drawImage(0,0,self.single)
        p2.drawImage(self.text_w,0,self.single)
        p2.end()

    def period_frames(self, int_speed_px_per_frame: int) -> int:
        return self.text_w // gcd(self.text_w, max(1, int_speed_px_per_frame))

    def tile_src_rect(self, offset_px: float, module_index: int) -> QRect:
        x = int((offset_px + module_index * self.module_w)) % self.text_w
        return QRect(x, 0, self.module_w, self.module_h)

def parse_time(s: str) -> datetime.time:
    return datetime.time.fromisoformat(s)

def in_range(t: datetime.time, start: datetime.time, end: datetime.time) -> bool:
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end

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

class Preview(QWidget):
    def __init__(self, w: int, h: int):
        super().__init__()
        self.setMinimumSize(800, 320)
        self.img = QImage(w,h,QImage.Format_RGB888)
        self.img.fill(Qt.black)
    def set_frame(self, img: QImage):
        self.img = img
        self.update()
    def paintEvent(self, e):
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
        self.frame = QImage(w,h,QImage.Format_RGB888)
        self.frame.fill(Qt.black)
    def set_frame(self, img: QImage):
        self.frame = img
    def paintEvent(self, e):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, Qt.black)
        scaled = self.frame.scaled(r.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p.drawImage((r.width()-scaled.width())//2,(r.height()-scaled.height())//2, scaled)
        p.end()

class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Studio Rayy — Multi-Content Ticker (Master-Strip)")
        self.cfg = CFG()
        self.offset = 0.0

        self.preset = self.load_initial_preset()

        self.module_w = self.preset["module"]["w"]
        self.module_h = self.preset["module"]["h"]
        self.concat_order = self.preset.get("concat_port_order", ["port1","port2"])
        self.rois_ports = build_rois_from_preset(self.preset)
        port_map = {p.port_id: p for p in self.rois_ports}
        self.dest_sequence = []
        for pid in self.concat_order:
            self.dest_sequence += port_map[pid].rects
        self.num_modules = len(self.dest_sequence)

        self.contents: Dict[str, ContentItem] = {}
        if not self.preset.get("contents"):
            self.preset["contents"] = [{
                "name":"Default","text":"STUDIO RAYY — ","font_family":"Arial","font_pt":72,
                "text_rgb":[255,255,255],"bg_rgb":[0,0,0]
            }]
        for c in self.preset.get("contents", []):
            item = ContentItem(c["name"], c["text"], c["font_family"], int(c["font_pt"]),
                               tuple(c["text_rgb"]), tuple(c["bg_rgb"]))
            self.contents[item.name] = item

        self.scheduler = Scheduler(self.contents, self.preset.get("scheduler", {}).get("entries", []))
        self.current_content_name = self.scheduler.pick_content_name() or self.preset["contents"][0]["name"]
        self.next_content_name: Optional[str] = None
        self.crossfade_active = False
        self.crossfade_start: Optional[datetime.datetime] = None
        self.crossfade_ms = 800

        self.strip_curr = self.make_strip(self.current_content_name)
        self.strip_next: Optional[MasterStrip] = None

        tabs = QTabWidget()

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
        right.addWidget(self.btn_add)
        right.addWidget(self.btn_dup)
        right.addWidget(self.btn_del)
        v1.addLayout(form)
        v1.addLayout(right)
        tabs.addTab(contents_tab, "Contents")

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

        out_tab = QWidget()
        v3 = QVBoxLayout(out_tab)
        ctl = QHBoxLayout()
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.1, 200.0)
        self.speed.setDecimals(1)
        self.speed.setSingleStep(0.1)
        self.speed.setValue(self.cfg.speed_px_per_frame)
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
        self.preview = Preview(self.cfg.width, self.cfg.height)
        v3.addWidget(self.preview, 1)
        tabs.addTab(out_tab, "Output")

        preset_tab = QWidget()
        v4 = QVBoxLayout(preset_tab)
        self.load_preset_btn = QPushButton("Preset laden")
        self.save_preset_btn = QPushButton("Preset speichern")
        v4.addWidget(self.load_preset_btn)
        v4.addWidget(self.save_preset_btn)
        tabs.addTab(preset_tab, "Preset")

        self.setCentralWidget(tabs)

        self.contents_list.currentItemChanged.connect(self.on_select_content)
        self.btn_add.clicked.connect(self.add_content)
        self.btn_dup.clicked.connect(self.dup_content)
        self.btn_del.clicked.connect(self.del_content)
        self.live_btn.clicked.connect(self.start_live)
        self.stop_btn.clicked.connect(self.stop_live)
        self.full_btn.clicked.connect(self.fullscreen_toggle)
        self.exp_btn.clicked.connect(self.export_video)
        self.load_preset_btn.clicked.connect(self.load_preset)
        self.save_preset_btn.clicked.connect(self.save_preset)

        if self.contents_list.count() > 0:
            self.contents_list.setCurrentRow(0)
        self.load_scheduler_into_table()

        self.out_win = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.resize(1280, 900)

    def load_initial_preset(self) -> dict:
        candidates = [
            resource_path("presets", "fhd50_two_ports_128x256_zigzag_multicontent.json"),
            os.path.join("presets", "fhd50_two_ports_128x256_zigzag_multicontent.json")
        ]
        for p in candidates:
            if os.path.exists(p):
                try:
                    return json.load(open(p,"r"))
                except Exception:
                    pass
        return {
            "name": "Default_FHD50",
            "output": {"width": 1920, "height": 1080, "fps": 50},
            "module": {"w": 128, "h": 256},
            "ports": [
                {"id":"port1","x":0,"blocks":[{"order":"bottom_up","count":4},{"order":"top_down","count":4},{"order":"bottom_up","count":4}]},
                {"id":"port2","x":128,"blocks":[{"order":"bottom_up","count":4},{"order":"top_down","count":4},{"order":"bottom_up","count":4}]}
            ],
            "concat_port_order": ["port1","port2"],
            "contents": [{
                "name":"Default","text":"STUDIO RAYY — ","font_family":"Arial","font_pt":72,
                "text_rgb":[255,255,255],"bg_rgb":[0,0,0]
            }],
            "scheduler": {"entries": []}
        }

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
        cur_name = self.c_name.text().strip()
        if cur_name not in self.contents:
            return
        citem = self.contents[cur_name]
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

    def del_content(self):
        cur = self.contents_list.currentItem()
        if not cur:
            return
        name = cur.text()
        self.contents.pop(name, None)
        self.contents_list.takeItem(self.contents_list.currentRow())

    def make_strip(self, name: Optional[str]) -> Optional[MasterStrip]:
        if not name or name not in self.contents:
            return None
        c = self.contents[name]
        return MasterStrip(c.text, c.font_family, int(c.font_pt), c.text_rgb, c.bg_rgb,
                           self.module_w, self.module_h, self.num_modules)

    def load_scheduler_into_table(self):
        self.tbl.setRowCount(0)
        for e in self.scheduler.entries:
            self.add_sched_row(e)

    def add_sched_row(self, e: dict = None):
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        type_cb = QComboBox()
        type_cb.addItems(["daily","date"])
        if e:
            type_cb.setCurrentText(e.get("type","daily"))
        self.tbl.setCellWidget(r, 0, type_cb)

        self.tbl.setItem(r, 1, QTableWidgetItem(",".join(map(str, e.get("weekdays", []))) if e else ""))
        self.tbl.setItem(r, 2, QTableWidgetItem(e.get("date","") if e else ""))
        self.tbl.setItem(r, 3, QTableWidgetItem(e.get("start","08:00") if e else "08:00"))
        self.tbl.setItem(r, 4, QTableWidgetItem(e.get("end","18:00") if e else "18:00"))

        content_cb = QComboBox()
        content_cb.addItems(list(self.contents.keys()) or [""])
        if e and e.get("content") in self.contents:
            content_cb.setCurrentText(e["content"])
        self.tbl.setCellWidget(r, 5, content_cb)

        trans_cb = QComboBox()
        trans_cb.addItems(["crossfade","cut"])
        if e:
            trans_cb.setCurrentText(e.get("transition","crossfade"))
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

    def render_mapped_frame(self, strip: MasterStrip, offset_px: float) -> QImage:
        frame = QImage(self.cfg.width, self.cfg.height, QImage.Format_RGB888)
        bg = (0,0,0)
        if self.current_content_name in self.contents:
            bg = self.contents[self.current_content_name].bg_rgb
        frame.fill(QColor(*bg))
        p = QPainter(frame)
        for idx, (dx,dy,dw,dh) in enumerate(self.dest_sequence):
            src = strip.tile_src_rect(offset_px, idx)
            p.drawImage(QRect(dx,dy,dw,dh), strip.double, src)
        p.end()
        return frame

    def start_live(self):
        self.timer.start(int(1000 / self.cfg.fps))

    def stop_live(self):
        self.timer.stop()

    def fullscreen_toggle(self):
        screens = QGuiApplication.screens()
        if not hasattr(self, "out_win") or self.out_win is None:
            self.out_win = OutputWindow(self.cfg.width, self.cfg.height)
        if self.out_win.isVisible():
            ret = QMessageBox.question(self, "Fullscreen beenden?",
                                       "Fullscreen wirklich ausschalten?",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret == QMessageBox.Yes:
                self.out_win.hide()
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

        self.cfg.speed_px_per_frame = float(self.speed.value())
        self.offset += self.cfg.speed_px_per_frame

        base_curr = self.render_mapped_frame(self.strip_curr, self.offset)
        frame = base_curr
        if self.crossfade_active and self.strip_next is not None and self.crossfade_start is not None:
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

        vis = draw_roi_overlay(frame, self.rois_ports) if self.overlay_chk.isChecked() else frame
        self.preview.set_frame(vis)
        if hasattr(self, "out_win") and self.out_win and self.out_win.isVisible():
            self.out_win.set_frame(frame)
            self.out_win.update()

    def load_preset(self):
        start_dir = resource_path("presets")
        path, _ = QFileDialog.getOpenFileName(self, "Preset laden", start_dir, "JSON (*.json)")
        if not path:
            return
        try:
            p = json.load(open(path,"r"))
            self.apply_preset(p)
            QMessageBox.information(self, "OK", "Preset geladen.")
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Preset konnte nicht geladen werden: {e}")

    def save_preset(self):
        self.commit_current_content_edits()
        entries = self.read_scheduler_from_table()
        data = {
            "name": self.preset.get("name","Custom_FHD50_MultiContent"),
            "output": self.preset.get("output", {"width":1920,"height":1080,"fps":50}),
            "module": self.preset.get("module", {"w":128,"h":256}),
            "ports": self.preset.get("ports", []),
            "concat_port_order": self.preset.get("concat_port_order", ["port1","port2"]),
            "contents": [
                {"name": c.name, "text": c.text, "font_family": c.font_family, "font_pt": int(c.font_pt),
                 "text_rgb": list(c.text_rgb), "bg_rgb": list(c.bg_rgb)}
                for c in self.contents.values()
            ],
            "scheduler": {"entries": entries}
        }
        default_path = os.path.join(os.path.expanduser("~"), "custom_multicontent.json")
        path, _ = QFileDialog.getSaveFileName(self, "Preset speichern", default_path, "JSON (*.json)")
        if not path:
            return
        try:
            json.dump(data, open(path,"w"), indent=2)
            QMessageBox.information(self, "OK", "Preset gespeichert.")
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Konnte Preset nicht speichern: {e}")

    def apply_preset(self, p: dict):
        self.preset = p
        self.module_w = p.get("module",{}).get("w",128)
        self.module_h = p.get("module",{}).get("h",256)
        self.concat_order = p.get("concat_port_order", ["port1","port2"])
        self.rois_ports = build_rois_from_preset(p)
        port_map = {r.port_id: r for r in self.rois_ports}
        self.dest_sequence = []
        for pid in self.concat_order:
            self.dest_sequence += port_map[pid].rects
        self.num_modules = len(self.dest_sequence)

        self.contents.clear()
        self.contents_list.clear()
        if not p.get("contents"):
            p["contents"] = [{
                "name":"Default","text":"STUDIO RAYY — ","font_family":"Arial","font_pt":72,
                "text_rgb":[255,255,255],"bg_rgb":[0,0,0]
            }]
        for c in p.get("contents", []):
            item = ContentItem(c["name"], c["text"], c["font_family"], int(c["font_pt"]),
                               tuple(c["text_rgb"]), tuple(c["bg_rgb"]))
            self.contents[item.name] = item
            self.contents_list.addItem(item.name)
        if self.contents_list.count() > 0:
            self.contents_list.setCurrentRow(0)

        self.scheduler = Scheduler(self.contents, p.get("scheduler", {}).get("entries", []))
        self.tbl.setRowCount(0)
        self.load_scheduler_into_table()

        self.current_content_name = self.scheduler.pick_content_name() or p["contents"][0]["name"]
        self.strip_curr = self.make_strip(self.current_content_name)
        self.strip_next = None
        self.crossfade_active = False
        self.crossfade_start = None
        self.offset = 0.0

    def commit_current_content_edits(self):
        name = self.c_name.text().strip()
        if not name:
            return
        cur_item = self.contents_list.currentItem()
        if cur_item and name != cur_item.text():
            if name in self.contents:
                QMessageBox.warning(self, "Hinweis", "Name existiert bereits.")
                return
            old_name = cur_item.text()
            c = self.contents.pop(old_name)
            c.name = name
            self.contents[name] = c
            cur_item.setText(name)
        if name not in self.contents:
            self.contents[name] = ContentItem(name, "", "Arial", 72, (255,255,255), (0,0,0))
        c = self.contents[name]
        c.text = self.c_text.text()
        c.font_family = self.c_font.currentFont().family()
        c.font_pt = int(self.c_size.value())

    def export_video(self):
        ffmpeg = resolve_ffmpeg()
        if not ffmpeg or not os.path.isfile(ffmpeg):
            QMessageBox.critical(self, "FFmpeg fehlt", "FFmpeg wurde nicht gefunden/bündelt.")
            return
        if not self.current_content_name:
            QMessageBox.critical(self, "Fehler", "Kein Content ausgewählt.")
            return
        int_speed = max(1, int(round(self.cfg.speed_px_per_frame)))
        period = self.strip_curr.period_frames(int_speed)
        secs = period / self.cfg.fps
        path, _ = QFileDialog.getSaveFileName(self, "Export", os.path.join(os.path.expanduser("~"), "ticker_fhd50.mp4"), "MP4 (*.mp4)")
        if not path:
            return
        cmd = [ffmpeg,"-y",
               "-f","rawvideo","-pix_fmt","rgb24","-s","1920x1080","-r",str(self.cfg.fps),
               "-i","pipe:0",
               "-c:v","libx264","-pix_fmt","yuv420p","-preset","veryfast",
               "-b:v","20M","-maxrate","20M","-bufsize","40M","-movflags","+faststart",
               path]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            local = 0.0
            for _ in range(period):
                frame = self.render_mapped_frame(self.strip_curr, local)
                ptr = frame.bits()
                ptr.setsize(frame.byteCount())
                proc.stdin.write(bytes(ptr))
                local += int_speed
            proc.stdin.close()
            proc.wait()
            QMessageBox.information(self, "Fertig", f"Export abgeschlossen. ~{secs:.2f}s, Frames {period} (Speed {int_speed}px/frame).")
        except Exception as e:
            QMessageBox.critical(self, "Exportfehler", str(e))

def main():
    app = QApplication(sys.argv)
    win = Main()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
