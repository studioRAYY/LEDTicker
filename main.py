
import sys, os, shutil, subprocess, json, datetime
from math import gcd
from dataclasses import dataclass
from typing import List, Tuple, Optional

import numpy as np
from PySide6.QtCore import Qt, QTimer, QRect
from PySide6.QtGui import QPainter, QImage, QColor, QFont, QFontMetrics, QGuiApplication
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QLineEdit, QPushButton, QColorDialog,
                               QSpinBox, QFileDialog, QVBoxLayout, QHBoxLayout, QMainWindow, QMessageBox,
                               QTextEdit, QCheckBox)

# ----------------------------- Config -----------------------------
@dataclass
class CFG:
    text: str = "STUDIO RAYY — FHD50 TICKER — "
    font_family: str = "Arial"
    font_pt: int = 72
    text_rgb: Tuple[int,int,int] = (255,255,255)
    bg_rgb: Tuple[int,int,int] = (0,0,0)
    width: int = 1920
    height: int = 1080
    fps: int = 50
    pixels_per_frame: int = 4  # integer px/frame -> exact loop

# --------------------------- Text Renderer ---------------------------
class TextStrip:
    def __init__(self, text: str, font_family: str, font_pt: int, text_rgb: Tuple[int,int,int], bg_rgb: Tuple[int,int,int], out_h: int):
        self.text = text if text else " "
        self.font_family = font_family
        self.font_pt = font_pt
        self.text_rgb = text_rgb
        self.bg_rgb = bg_rgb
        self.out_h = out_h
        self.font = QFont(self.font_family, self.font_pt)
        self.rebuild()

    def rebuild(self):
        fm = QFontMetrics(self.font)
        self.text_h = fm.height()
        self.text_w = max(8, fm.horizontalAdvance(self.text))
        h = self.out_h
        self.single = QImage(self.text_w, h, QImage.Format_RGB888)
        self.single.fill(QColor(*self.bg_rgb))
        p = QPainter(self.single)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        p.setPen(QColor(*self.text_rgb))
        p.setFont(self.font)
        baseline = (h + fm.ascent() - fm.descent()) // 2
        p.drawText(0, baseline, self.text)
        p.end()
        # doubled for seamless copy
        self.double = QImage(self.text_w*2, h, QImage.Format_RGB888)
        p2 = QPainter(self.double)
        p2.drawImage(0,0,self.single)
        p2.drawImage(self.text_w,0,self.single)
        p2.end()

    def period_frames(self, ppf: int) -> int:
        return self.text_w // gcd(self.text_w, ppf)

    def window(self, offset_px: int, out_w: int, out_h: int) -> QImage:
        x = offset_px % self.text_w
        view = QImage(out_w, out_h, QImage.Format_RGB888)
        view.fill(QColor(*self.bg_rgb))
        p = QPainter(view)
        src = QRect(x, 0, out_w, out_h)
        p.drawImage(QRect(0,0,out_w,out_h), self.double, src)
        p.end()
        return view

# ----------------------------- ROI PRESET -----------------------------
@dataclass
class PortROI:
    port_id: str
    rects: List[Tuple[int,int,int,int]]  # (x,y,w,h)

def build_twoport_zigzag_rois() -> List[PortROI]:
    ys_bottom_up = [824, 568, 312, 56]   # bottom->top
    ys_top_down  = [0,   256, 512, 768]  # top->bottom
    def make_rects(x: int):
        rects = []
        rects += [(x,y,128,256) for y in ys_bottom_up]  # block A
        rects += [(x,y,128,256) for y in ys_top_down]   # block B
        rects += [(x,y,128,256) for y in ys_bottom_up]  # block C
        return rects  # 12 modules
    return [PortROI("port1", make_rects(0)), PortROI("port2", make_rects(128))]

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

# ----------------------------- Scheduler & Crossfade -----------------------------
def parse_time(s: str) -> datetime.time:
    return datetime.time.fromisoformat(s)

def in_range(t: datetime.time, start: datetime.time, end: datetime.time) -> bool:
    # supports ranges that may cross midnight (e.g., 22:00 - 02:00)
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end

class TextScheduler:
    def __init__(self, entries: List[dict], fade_ms: int = 800):
        self.entries = entries or []
        self.fade_ms = fade_ms

    def pick_text(self, now: Optional[datetime.datetime] = None) -> Optional[str]:
        now = now or datetime.datetime.now()
        t = now.time()
        wd = now.weekday()  # 0=Mon..6=Sun
        # specific date overrides first
        for e in self.entries:
            if e.get("type") == "date" and e.get("date") == now.date().isoformat():
                if in_range(t, parse_time(e["start"]), parse_time(e["end"])):
                    return e["text"]
        # daily by weekday
        for e in self.entries:
            if e.get("type") == "daily" and wd in (e.get("weekdays") or []):
                if in_range(t, parse_time(e["start"]), parse_time(e["end"])):
                    return e["text"]
        return None

# ----------------------------- Widgets -----------------------------
class Preview(QWidget):
    def __init__(self, w: int, h: int):
        super().__init__()
        self.setMinimumSize(800, 300)
        self.img = QImage(w,h,QImage.Format_RGB888); self.img.fill(Qt.black)
    def set_frame(self, img: QImage): self.img = img; self.update()
    def paintEvent(self, e):
        p = QPainter(self); r = self.rect(); p.fillRect(r, Qt.black)
        scaled = self.img.scaled(r.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = (r.width() - scaled.width()) // 2; y = (r.height() - scaled.height()) // 2
        p.drawImage(x,y,scaled); p.end()

class OutputWindow(QWidget):
    def __init__(self, w: int, h: int):
        super().__init__()
        self.setWindowTitle("Ticker Output")
        self.frame = QImage(w,h,QImage.Format_RGB888); self.frame.fill(Qt.black)
    def set_frame(self, img: QImage): self.frame = img
    def paintEvent(self, e):
        p = QPainter(self); r = self.rect(); p.fillRect(r, Qt.black)
        scaled = self.frame.scaled(r.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = (r.width() - scaled.width()) // 2; y = (r.height() - scaled.height()) // 2
        p.drawImage(x,y,scaled); p.end()

# ----------------------------- Main Window -----------------------------
class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Studio Rayy — FHD50 Ticker (Two-Port ZigZag + Scheduler)")
        self.cfg = CFG()

        # load default preset (mapping + schedule)
        self.rois = build_twoport_zigzag_rois()
        preset_path = os.path.join("presets","fhd50_two_ports_128x256_zigzag_schedule.json")
        self.schedule_entries = []
        self.fade_ms = 800
        if os.path.exists(preset_path):
            try:
                data = json.load(open(preset_path,"r"))
                sch = data.get("schedule", {})
                self.schedule_entries = sch.get("entries", [])
                self.fade_ms = int(sch.get("fade_ms", 800))
            except Exception as e:
                print("Preset load failed:", e)

        self.scheduler = TextScheduler(self.schedule_entries, self.fade_ms)

        # current and next text (for crossfade)
        self.current_text = self.scheduler.pick_text() or self.cfg.text
        self.next_text = None
        self.crossfade_active = False
        self.crossfade_start = None  # datetime

        # strips for rendering
        self.strip_curr = TextStrip(self.current_text, self.cfg.font_family, self.cfg.font_pt, self.cfg.text_rgb, self.cfg.bg_rgb, self.cfg.height)
        self.strip_next = None
        self.offset = 0

        # UI
        v = QVBoxLayout()
        # Row 1: basic text & colors
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Fallback-Text:"))
        self.text_in = QLineEdit(self.cfg.text); r1.addWidget(self.text_in, 1)
        self.txt_color_btn = QPushButton("Textfarbe"); self.bg_color_btn = QPushButton("Hintergrund")
        self.txt_color_btn.clicked.connect(lambda: self.pick_color(True))
        self.bg_color_btn.clicked.connect(lambda: self.pick_color(False))
        r1.addWidget(self.txt_color_btn); r1.addWidget(self.bg_color_btn)
        v.addLayout(r1)

        # Row 2: font & speed & fade & overlay
        r2 = QHBoxLayout()
        self.size_sb = QSpinBox(); self.size_sb.setRange(8, 256); self.size_sb.setValue(self.cfg.font_pt)
        r2.addWidget(QLabel("FontSize")); r2.addWidget(self.size_sb)
        self.ppf_sb = QSpinBox(); self.ppf_sb.setRange(1, 100); self.ppf_sb.setValue(self.cfg.pixels_per_frame)
        r2.addWidget(QLabel("Pixel/Frame")); r2.addWidget(self.ppf_sb)
        self.fade_sb = QSpinBox(); self.fade_sb.setRange(0, 10000); self.fade_sb.setValue(self.fade_ms)
        r2.addWidget(QLabel("Fade (ms)")); r2.addWidget(self.fade_sb)
        self.overlay_chk = QCheckBox("ROI-Overlay (Preview)"); self.overlay_chk.setChecked(True); r2.addWidget(self.overlay_chk)
        v.addLayout(r2)

        # Row 3: schedule editor (JSON)
        v.addWidget(QLabel("Schedule (JSON):"))
        self.schedule_edit = QTextEdit()
        seed = json.dumps({"fade_ms": self.fade_ms, "entries": self.schedule_entries}, indent=2)
        self.schedule_edit.setPlainText(seed)
        v.addWidget(self.schedule_edit, 1)

        # Row 4: buttons
        r4 = QHBoxLayout()
        self.apply_btn = QPushButton("Apply Settings"); r4.addWidget(self.apply_btn)
        self.live_btn = QPushButton("Live"); r4.addWidget(self.live_btn)
        self.stop_btn = QPushButton("Stop"); r4.addWidget(self.stop_btn)
        self.full_btn = QPushButton("Fullscreen Out"); r4.addWidget(self.full_btn)
        self.exp_btn = QPushButton("Export MP4"); r4.addWidget(self.exp_btn)
        self.load_preset_btn = QPushButton("Preset laden"); r4.addWidget(self.load_preset_btn)
        self.save_preset_btn = QPushButton("Preset speichern"); r4.addWidget(self.save_preset_btn)
        v.addLayout(r4)

        self.preview = Preview(self.cfg.width, self.cfg.height)
        v.addWidget(QLabel("Preview:"))
        v.addWidget(self.preview, 1)

        w = QWidget(); w.setLayout(v); self.setCentralWidget(w)

        # connections
        self.apply_btn.clicked.connect(self.apply_cfg)
        self.live_btn.clicked.connect(self.start_live)
        self.stop_btn.clicked.connect(self.stop_live)
        self.full_btn.clicked.connect(self.fullscreen_out)
        self.exp_btn.clicked.connect(self.export_video)
        self.load_preset_btn.clicked.connect(self.load_preset)
        self.save_preset_btn.clicked.connect(self.save_preset)

        self.out_win = None
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick)

    # ---------- preset io ----------
    def load_preset(self):
        path, _ = QFileDialog.getOpenFileName(self, "Preset laden", "presets", "JSON (*.json)")
        if not path: return
        try:
            data = json.load(open(path,"r"))
            sch = data.get("schedule", {})
            self.schedule_entries = sch.get("entries", [])
            self.fade_ms = int(sch.get("fade_ms", 800))
            self.schedule_edit.setPlainText(json.dumps({"fade_ms": self.fade_ms, "entries": self.schedule_entries}, indent=2))
            QMessageBox.information(self, "OK", "Preset geladen.")
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Preset konnte nicht geladen werden: {e}")

    def save_preset(self):
        try:
            sch = json.loads(self.schedule_edit.toPlainText())
            fade_ms = int(sch.get("fade_ms", self.fade_ms))
            entries = sch.get("entries", [])
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Schedule-JSON ungültig: {e}")
            return
        data = {
            "name": "Custom_FHD50_TwoPorts_128x256_ZigZag_withSchedule",
            "output": {"width": 1920, "height": 1080, "fps": 50},
            "module": {"w": 128, "h": 256},
            "ports": [
                {"id": "port1", "x": 0,
                 "blocks": [{"order": "bottom_up", "count": 4},
                            {"order": "top_down",  "count": 4},
                            {"order": "bottom_up", "count": 4}]},
                {"id": "port2", "x": 128,
                 "blocks": [{"order": "bottom_up", "count": 4},
                            {"order": "top_down",  "count": 4},
                            {"order": "bottom_up", "count": 4}]}
            ],
            "schedule": {"fade_ms": fade_ms, "entries": entries}
        }
        path, _ = QFileDialog.getSaveFileName(self, "Preset speichern", "presets/custom_fhd50_schedule.json", "JSON (*.json)")
        if not path: return
        try:
            json.dump(data, open(path,"w"), indent=2)
            QMessageBox.information(self, "OK", "Preset gespeichert.")
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Konnte Preset nicht speichern: {e}")

    # ---------- helpers ----------
    def pick_color(self, text=True):
        current = QColor(*self.cfg.text_rgb if text else self.cfg.bg_rgb)
        c = QColorDialog.getColor(current, self, "Farbe wählen")
        if c.isValid():
            if text: self.cfg.text_rgb = (c.red(), c.green(), c.blue())
            else: self.cfg.bg_rgb = (c.red(), c.green(), c.blue())
            # rebuild strips with new colors
            self.strip_curr = TextStrip(self.current_text, self.cfg.font_family, self.cfg.font_pt, self.cfg.text_rgb, self.cfg.bg_rgb, self.cfg.height)
            if self.strip_next is not None:
                self.strip_next = TextStrip(self.next_text or "", self.cfg.font_family, self.cfg.font_pt, self.cfg.text_rgb, self.cfg.bg_rgb, self.cfg.height)

    def apply_cfg(self):
        self.cfg.text = self.text_in.text() or " "
        self.cfg.font_pt = self.size_sb.value()
        self.cfg.pixels_per_frame = self.ppf_sb.value()
        self.fade_ms = self.fade_sb.value()
        # schedule
        try:
            sch = json.loads(self.schedule_edit.toPlainText())
            self.fade_ms = int(sch.get("fade_ms", self.fade_ms))
            self.schedule_entries = sch.get("entries", [])
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Schedule-JSON ungültig: {e}")
            return
        self.scheduler = TextScheduler(self.schedule_entries, self.fade_ms)

        # reset strips
        self.current_text = self.scheduler.pick_text() or self.cfg.text
        self.strip_curr = TextStrip(self.current_text, self.cfg.font_family, self.cfg.font_pt, self.cfg.text_rgb, self.cfg.bg_rgb, self.cfg.height)
        self.strip_next = None
        self.crossfade_active = False
        self.crossfade_start = None
        self.offset = 0
        self.tick(force=True)

    def start_live(self): self.timer.start(int(1000 / self.cfg.fps))
    def stop_live(self): self.timer.stop()

    def fullscreen_out(self):
        screens = QGuiApplication.screens()
        if len(screens) < 2:
            QMessageBox.information(self, "Info", "Kein zweiter Bildschirm erkannt."); return
        if not hasattr(self, "out_win") or self.out_win is None:
            self.out_win = OutputWindow(self.cfg.width, self.cfg.height)
        g = screens[1].geometry()
        self.out_win.setGeometry(g); self.out_win.showFullScreen()

    # ---------- rendering ----------
    def composite_crossfade(self, img_a: QImage, img_b: QImage, alpha: float) -> QImage:
        out = QImage(img_a.size(), QImage.Format_RGB888)
        p = QPainter(out)
        p.drawImage(0,0, img_a)
        p.setOpacity(alpha)
        p.drawImage(0,0, img_b)
        p.setOpacity(1.0)
        p.end()
        return out

    def advance_schedule(self):
        want = self.scheduler.pick_text()
        fallback = self.cfg.text
        target_text = want if want is not None else fallback
        if target_text != self.current_text and (not self.crossfade_active):
            self.next_text = target_text
            self.strip_next = TextStrip(self.next_text, self.cfg.font_family, self.cfg.font_pt, self.cfg.text_rgb, self.cfg.bg_rgb, self.cfg.height)
            self.crossfade_active = True
            self.crossfade_start = datetime.datetime.now()

    def build_frame(self, offset) -> QImage:
        base_curr = self.strip_curr.window(offset, self.cfg.width, self.cfg.height)
        if self.crossfade_active and self.strip_next is not None and self.crossfade_start is not None:
            elapsed_ms = (datetime.datetime.now() - self.crossfade_start).total_seconds() * 1000.0
            a = min(max(elapsed_ms / max(1, self.fade_ms), 0.0), 1.0)
            base_next = self.strip_next.window(offset, self.cfg.width, self.cfg.height)
            return self.composite_crossfade(base_curr, base_next, a)
        return base_curr

    def finalize_crossfade_if_done(self):
        if self.crossfade_active and self.crossfade_start is not None:
            elapsed_ms = (datetime.datetime.now() - self.crossfade_start).total_seconds() * 1000.0
            if elapsed_ms >= self.fade_ms:
                self.current_text = self.next_text
                self.strip_curr = self.strip_next
                self.strip_next = None
                self.crossfade_active = False
                self.crossfade_start = None

    def tick(self, force=False):
        # schedule check
        self.advance_schedule()
        # move ticker
        if not force:
            self.offset += self.cfg.pixels_per_frame
        frame = self.build_frame(self.offset)
        # overlay in preview only
        if self.overlay_chk.isChecked():
            frame_vis = draw_roi_overlay(frame, self.rois)
        else:
            frame_vis = frame
        self.preview.set_frame(frame_vis)
        # clean frame to output
        if hasattr(self, "out_win") and self.out_win and self.out_win.isVisible():
            clean = self.build_frame(self.offset)
            self.out_win.set_frame(clean); self.out_win.update()
        # finalize fade if done
        self.finalize_crossfade_if_done()

    # ---------- export ----------
    def export_video(self):
        if shutil.which("ffmpeg") is None:
            QMessageBox.critical(self, "Fehler", "FFmpeg nicht gefunden. Bitte in PATH aufnehmen."); return
        # For loop-safety, export the current strip only (no crossfade).
        period = self.strip_curr.period_frames(self.cfg.pixels_per_frame)
        secs = period / self.cfg.fps
        path, _ = QFileDialog.getSaveFileName(self, "Export", "ticker_fhd50.mp4", "MP4 (*.mp4)")
        if not path: return
        cmd = ["ffmpeg","-y",
               "-f","rawvideo","-pix_fmt","rgb24","-s","1920x1080","-r","50",
               "-i","pipe:0",
               "-c:v","libx264","-pix_fmt","yuv420p","-preset","veryfast",
               "-b:v","20M","-maxrate","20M","-bufsize","40M","-movflags","+faststart",
               path]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            local = 0
            for _ in range(period):
                frame = self.strip_curr.window(local, self.cfg.width, self.cfg.height)
                ptr = frame.bits(); ptr.setsize(frame.byteCount())
                proc.stdin.write(bytes(ptr))
                local += self.cfg.pixels_per_frame
            proc.stdin.close(); proc.wait()
            QMessageBox.information(self, "Fertig", f"Export abgeschlossen. Dauer ~{secs:.2f}s, Frames {period}")
        except Exception as e:
            QMessageBox.critical(self, "Exportfehler", str(e))

def main():
    app = QApplication(sys.argv)
    win = Main(); win.resize(1200, 850); win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
