# Studio Rayy — LED Ticker (FHD50, Two-Port ZigZag, Scheduler + Crossfade)

Full-HD **1920×1080 @ 50fps** Laufticker mit GUI (PySide6), **Zickzack-Mapping** (2 Ports à 128×256-Module),
**zeitgesteuertem Textwechsel** (daily/weekday + date-Overrides) und **Crossfade** zwischen Texten.
Export: **MP4/H.264 ~20 Mbit/s @ 50fps** (loop-sicher).

## Setup
```bash
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows
# .venv\Scripts\activate
pip install -r requirements.txt
# FFmpeg installieren und in PATH verfügbar machen
python main.py
```

## Nutzung (Kurz)
- Text/Font/Größe/Farben setzen, Pixel/Frame (Integer) und Fade (ms) wählen.
- **Schedule (JSON)** in der GUI bearbeiten → „Apply Settings“.
- Preview mit ROI-Overlay prüfen; Fullscreen-Out liefert cleanes Full-HD.
- Export MP4 erzeugt genau **eine Loop-Periode** (perfekt nahtlos).

### Schedule-JSON Beispiel
```json
{
  "fade_ms": 800,
  "entries": [
    {"type":"daily","weekdays":[0,1,2,3,4],"start":"08:00","end":"18:00","text":"WORKDAY — "},
    {"type":"daily","weekdays":[5,6],"start":"10:00","end":"22:00","text":"WEEKEND — "},
    {"type":"date","date":"2025-12-31","start":"00:00","end":"23:59","text":"NYE SPECIAL — "}
  ]
}
```

## GitHub (Push)
```bash
git init
git add .
git commit -m "feat: FHD50 zigzag two-port ticker with scheduler + crossfade"
git branch -M main
git remote add origin https://github.com/<USER>/<REPO>.git
git push -u origin main
```
