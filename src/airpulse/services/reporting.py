"""Report export helpers for PDF and social cards."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas

from airpulse.constants import PROJECT_ROOT

_PDF_FONT_REGISTERED = False


def resolve_font_path() -> str | None:
    candidates = [
        PROJECT_ROOT / "assets" / "fonts" / "DejaVuSans.ttf",
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        try:
            if path.exists():
                return str(path)
        except Exception:
            continue
    return None


def ensure_pdf_font() -> str:
    global _PDF_FONT_REGISTERED
    font_path = resolve_font_path()
    if font_path and not _PDF_FONT_REGISTERED:
        try:
            pdfmetrics.registerFont(TTFont("AirPulseUnicode", font_path))
            _PDF_FONT_REGISTERED = True
        except Exception:
            pass
    return "AirPulseUnicode" if _PDF_FONT_REGISTERED else "Helvetica"


def generate_pdf_report(city, waqi_data, stations, wind_data, commute_mode, commute_km, commute_saved, footprint_monthly, checklist_score, *, aqi_info, wind_dir_label, translate, format_datetime):
    buf = io.BytesIO()
    width, height = A4
    canvas = rl_canvas.Canvas(buf, pagesize=A4)
    font_name = ensure_pdf_font()

    def pdf_safe(text) -> str:
        if text is None:
            return "-"
        safe = str(text)
        replacements = {"m??": "m3", "m?": "m3", "O???": "O3", "O?": "O3", "NO???": "NO2", "NO?": "NO2", "SO???": "SO2", "SO?": "SO2", "??": "-", "?": "-"}
        for old, new in replacements.items():
            safe = safe.replace(old, new)
        return safe

    aqi_val = float(waqi_data.get("aqi", 0) if waqi_data else 0)
    info = aqi_info(aqi_val)
    col_main = HexColor(info["color"])
    col_blue = HexColor("#007AFF")
    col_dark = HexColor("#1D1D1F")
    col_gray = HexColor("#8E8E93")
    col_bg = HexColor("#F2F2F7")
    col_card = HexColor("#FFFFFF")

    canvas.setFillColor(col_bg)
    canvas.rect(0, 0, width, height, fill=1, stroke=0)
    banner_h = 130
    canvas.setFillColor(HexColor("#1a1a2e"))
    canvas.rect(0, height - banner_h, width, banner_h, fill=1, stroke=0)
    canvas.setFillColor(col_main)
    canvas.rect(0, height - banner_h, width, 5, fill=1, stroke=0)
    canvas.setFillColor(white)
    canvas.setFont(font_name, 24)
    canvas.drawString(32, height - 55, "AirPulse Global")
    canvas.setFont(font_name, 11)
    canvas.setFillColor(HexColor("#b0b8d4"))
    canvas.drawString(32, height - 74, pdf_safe(translate("reports.title")))

    user_name = waqi_data.get("user_name", "") if waqi_data else ""
    if user_name:
        canvas.setFillColor(col_main)
        canvas.setFont(font_name, 10)
        canvas.drawString(32, height - 90, pdf_safe(f"PREPARED FOR: {user_name.upper()}"))

    canvas.setFillColor(white)
    canvas.setFont(font_name, 14)
    canvas.drawRightString(width - 32, height - 55, pdf_safe(city.split(",")[0]))
    canvas.setFont(font_name, 9)
    canvas.setFillColor(HexColor("#b0b8d4"))
    canvas.drawRightString(width - 32, height - 72, pdf_safe(format_datetime(datetime.now())))

    y0 = height - banner_h - 20
    canvas.setFillColor(col_card)
    canvas.roundRect(24, y0 - 100, width - 48, 95, 12, fill=1, stroke=0)
    cx, cy = 80, y0 - 53
    canvas.setFillColor(col_main)
    canvas.circle(cx, cy, 34, fill=1, stroke=0)
    canvas.setFillColor(white)
    canvas.setFont(font_name, 22)
    canvas.drawCentredString(cx, cy - 8, str(int(aqi_val)))
    canvas.setFont(font_name, 7)
    canvas.drawCentredString(cx, cy + 16, "AQI")
    canvas.setFillColor(col_dark)
    canvas.setFont(font_name, 16)
    canvas.drawString(130, y0 - 38, pdf_safe(info["name"]))
    canvas.setFont(font_name, 9)
    canvas.setFillColor(col_gray)
    canvas.drawString(130, y0 - 56, pdf_safe(info["desc"][:80]))
    canvas.drawString(130, y0 - 72, pdf_safe(f"Primary pollutant: {(waqi_data.get('dominentpol') or 'pm25').upper()}"))

    def metric_card(x, y, label, value, unit):
        canvas.setFillColor(col_card)
        canvas.roundRect(x, y - 56, 100, 54, 8, fill=1, stroke=0)
        canvas.setFillColor(col_gray)
        canvas.setFont(font_name, 7)
        canvas.drawString(x + 8, y - 14, pdf_safe(label.upper()))
        canvas.setFillColor(col_dark)
        canvas.setFont(font_name, 18)
        canvas.drawString(x + 8, y - 34, pdf_safe(value))
        canvas.setFillColor(col_gray)
        canvas.setFont(font_name, 8)
        canvas.drawString(x + 8, y - 50, pdf_safe(unit))

    pm = waqi_data or {}
    y1 = y0 - 128
    for i, (label, key, unit) in enumerate([("PM2.5", "pm25", "ug/m3"), ("PM10", "pm10", "ug/m3"), ("O3", "o3", "ug/m3"), ("NO2", "no2", "ug/m3"), ("SO2", "so2", "ug/m3")]):
        value = f"{float(pm.get(key, 0)):.1f}" if pm.get(key) is not None else "-"
        metric_card(24 + i * 110, y1, label, value, unit)

    y2 = y1 - 74
    canvas.setFillColor(col_card)
    canvas.roundRect(24, y2 - 54, width - 48, 52, 10, fill=1, stroke=0)
    canvas.setFillColor(col_blue)
    canvas.setFont(font_name, 11)
    canvas.drawString(36, y2 - 20, "Wind Context")
    canvas.setFillColor(col_dark)
    canvas.setFont(font_name, 10)
    if wind_data and wind_data.get("speed"):
        spd = wind_data.get("speed") or "-"
        dir_ = wind_data.get("direction") or 0
        gst = wind_data.get("gust") or "-"
        canvas.drawString(36, y2 - 40, pdf_safe(f"Speed: {spd} m/s   Direction: {wind_dir_label(dir_)} ({dir_} deg)   Gust: {gst} m/s"))
    else:
        canvas.setFillColor(col_gray)
        canvas.drawString(36, y2 - 40, "Wind data not available - configure a Tomorrow.io API key in secrets")

    y3 = y2 - 78
    canvas.setFillColor(col_card)
    canvas.roundRect(24, y3 - 70, (width - 60) / 2, 68, 10, fill=1, stroke=0)
    canvas.setFillColor(col_dark)
    canvas.setFont(font_name, 11)
    canvas.drawString(36, y3 - 20, "Commute CO2 Savings")
    canvas.setFont(font_name, 10)
    canvas.drawString(36, y3 - 38, pdf_safe(f"Mode: {commute_mode}  |  Distance: {commute_km} km/day"))
    canvas.setFillColor(HexColor("#34C759"))
    canvas.setFont(font_name, 14)
    canvas.drawString(36, y3 - 58, f"Daily saving: {(commute_saved or 0):.2f} kg CO2")

    x2 = 24 + (width - 60) / 2 + 12
    canvas.setFillColor(col_card)
    canvas.roundRect(x2, y3 - 70, (width - 60) / 2, 68, 10, fill=1, stroke=0)
    canvas.setFillColor(col_dark)
    canvas.setFont(font_name, 11)
    canvas.drawString(x2 + 12, y3 - 20, "Carbon Footprint")
    canvas.setFont(font_name, 10)
    fp_val = waqi_data.get("fp_total") or footprint_monthly
    if fp_val:
        canvas.drawString(x2 + 12, y3 - 54, pdf_safe(f"Total: {fp_val:.2f} t/mo  {waqi_data.get('fp_label', '')}"))
    else:
        canvas.setFillColor(col_gray)
        canvas.drawString(x2 + 12, y3 - 38, "Not yet calculated")

    y4 = y3 - 94
    canvas.setFillColor(col_card)
    canvas.roundRect(24, y4 - 60, width - 48, 58, 10, fill=1, stroke=0)
    canvas.setFillColor(col_dark)
    canvas.setFont(font_name, 11)
    canvas.drawString(36, y4 - 18, "Daily Action Score")
    canvas.setFont(font_name, 10)
    action_score_val = waqi_data.get("action_score") if waqi_data else None
    top_actions = waqi_data.get("top_actions") if waqi_data else None
    score_line = f"Completed: {checklist_score}/8 actions today"
    if action_score_val is not None:
        score_line += f"  |  Action score: {int(action_score_val)}"
    canvas.drawString(36, y4 - 36, pdf_safe(score_line))
    bar_x, bar_y, bar_w = 36, y4 - 50, width - 96
    canvas.setFillColor(HexColor("#E5E5EA"))
    canvas.rect(bar_x, bar_y, bar_w, 7, fill=1, stroke=0)
    canvas.setFillColor(HexColor("#34C759"))
    canvas.rect(bar_x, bar_y, bar_w * (checklist_score / 8), 7, fill=1, stroke=0)
    if isinstance(top_actions, list) and top_actions:
        canvas.setFillColor(col_gray)
        canvas.setFont(font_name, 8)
        canvas.drawString(36, y4 - 61, pdf_safe(f"Top actions: {' | '.join(top_actions[:3])[:92]}"))

    canvas.setFillColor(HexColor("#1a1a2e"))
    canvas.rect(0, 0, width, 36, fill=1, stroke=0)
    canvas.setFillColor(white)
    canvas.setFont(font_name, 8)
    canvas.drawString(32, 13, "AirPulse Global - Environmental Intelligence Platform")
    canvas.save()
    buf.seek(0)
    return buf.read()


def generate_social_card(city, aqi_val, aqi_name, aqi_color_hex, pm25, pm10, wind_speed, commute_saved, action_score, *, translate, format_date, format_datetime):
    width, height = 1080, 566
    img = Image.new("RGB", (width, height), color=(238, 243, 249))
    draw = ImageDraw.Draw(img)

    for i in range(height):
        ratio = i / max(height - 1, 1)
        draw.line(
            [(0, i), (width, i)],
            fill=(
                int(238 * (1 - ratio) + 247 * ratio),
                int(243 * (1 - ratio) + 250 * ratio),
                int(249 * (1 - ratio) + 253 * ratio),
            ),
        )

    try:
        accent = tuple(int(aqi_color_hex.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        accent = (52, 199, 89)

    navy = (18, 28, 45)
    slate = (71, 85, 105)
    text_dark = (15, 23, 42)
    muted = (148, 163, 184)
    border = (226, 232, 240)

    draw.rounded_rectangle([28, 24, width - 28, height - 24], radius=36, fill=(255, 255, 255))
    draw.rounded_rectangle([28, 24, width - 28, 118], radius=36, fill=navy)
    draw.rectangle([28, 84, width - 28, 118], fill=navy)

    _font_cache = {}

    def get_font(size=24, bold=False):
        key = (size, bold)
        if key in _font_cache:
            return _font_cache[key]
        font_path = resolve_font_path()
        candidates = []
        if font_path:
            candidates.append(font_path)
        candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ])
        for candidate in candidates:
            try:
                font = ImageFont.truetype(candidate, size)
                _font_cache[key] = font
                return font
            except Exception:
                continue
        font = ImageFont.load_default()
        _font_cache[key] = font
        return font

    def fit_font(text, max_width, preferred_size, min_size=10, bold=False):
        size = preferred_size
        while size >= min_size:
            font = get_font(size, bold=bold)
            bbox = draw.textbbox((0, 0), str(text), font=font)
            if bbox[2] - bbox[0] <= max_width:
                return font
            size -= 1
        return get_font(min_size, bold=bold)

    def txt(x, y, text, size=24, color=(255, 255, 255), bold=False, anchor="la", max_width=None):
        content = str(text)
        font = fit_font(content, max_width, size, bold=bold) if max_width else get_font(size, bold=bold)
        draw.text((x, y), content, font=font, fill=color, anchor=anchor)
        return font

    def metric_card(x, y, w, h, label, value, unit, accent_color=None):
        accent_color = accent_color or accent
        draw.rounded_rectangle([x, y, x + w, y + h], radius=20, fill=(255, 255, 255), outline=border, width=2)
        draw.rounded_rectangle([x, y, x + w, y + 6], radius=20, fill=accent_color)

        label_font = get_font(12, bold=True)
        value_font = fit_font(str(value), w - 36, 22, min_size=18, bold=True)
        unit_font = get_font(11, bold=False)

        label_y = y + 14
        value_y = y + 44
        unit_y = y + h - 20

        draw.text((x + 18, label_y), str(label).upper(), font=label_font, fill=slate, anchor="la")
        draw.text((x + 18, value_y), str(value), font=value_font, fill=text_dark, anchor="la")
        draw.text((x + 18, unit_y), str(unit), font=unit_font, fill=muted, anchor="la")

    txt(64, 58, "AirPulse Global", 26, (255, 255, 255), bold=True)
    txt(64, 88, translate("reports.title"), 14, (183, 194, 214), max_width=420)
    txt(width - 64, 62, format_datetime(datetime.now()), 16, (198, 208, 226), anchor="ra")

    city_short = city.split(",")[0].strip()
    txt(88, 210, city_short, 52, text_dark, bold=True, max_width=520)

    circle_x = width - 170
    circle_y = 228
    circle_r = 88
    draw.ellipse([circle_x - circle_r, circle_y - circle_r, circle_x + circle_r, circle_y + circle_r], fill=accent)
    txt(circle_x, circle_y - 18, f"{int(aqi_val)}", 42, (255, 255, 255), bold=True, anchor="mm", max_width=120)
    txt(circle_x, circle_y + 34, aqi_name, 22, (255, 255, 255), bold=True, anchor="mm", max_width=145)

    card_y = 388
    card_w = 212
    card_h = 86
    gap = 18
    start_x = 88
    metric_card(start_x, card_y, card_w, card_h, "PM2.5", f"{float(pm25):.1f}", "ug/m3")
    metric_card(start_x + (card_w + gap), card_y, card_w, card_h, "PM10", f"{float(pm10):.1f}", "ug/m3")
    metric_card(start_x + 2 * (card_w + gap), card_y, card_w, card_h, "Wind", f"{float(wind_speed):.1f}", "m/s", accent_color=(0, 122, 255))
    metric_card(start_x + 3 * (card_w + gap), card_y, card_w, card_h, "Action Score", f"{int(action_score)}/100", "daily score", accent_color=(88, 86, 214))

    footer_y = 514
    txt(84, footer_y, f"AirPulse Global · {format_date(datetime.now())}", 14, text_dark)
    txt(width - 84, footer_y, f"CO2 Saved {float(commute_saved):.2f} kg/day", 14, slate, anchor="ra")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92, optimize=True)
    buf.seek(0)
    return buf.read()

