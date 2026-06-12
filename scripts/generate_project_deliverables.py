# -*- coding: utf-8 -*-
"""Generate professional Word and PowerPoint project deliverables for AirPulse.

The project environment does not require python-docx or python-pptx. This
script writes minimal, valid Office Open XML packages directly with the Python
standard library so the deliverables can be regenerated offline.
"""

from __future__ import annotations

import csv
import json
import math
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
DOCX_PATH = ARTIFACTS_DIR / "AirPulse_Global_Profesyonel_Proje_Raporu.docx"
PPTX_PATH = ARTIFACTS_DIR / "AirPulse_Global_Proje_Sunumu.pptx"


def xml(text: object) -> str:
    return escape("" if text is None else str(text), {'"': "&quot;", "'": "&apos;"})


def read_csv_dicts(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def count_files(path: Path, pattern: str) -> int:
    return len(list(path.glob(pattern))) if path.exists() else 0


def load_project_stats() -> dict[str, object]:
    stations = read_csv_dicts(PROJECT_ROOT / "stations.csv")
    metrics = read_csv_dicts(ARTIFACTS_DIR / "offline_forecast_metrics_summary.csv")
    quality = read_csv_dicts(ARTIFACTS_DIR / "data_quality_report.csv")

    reviewed = sum(1 for row in stations if str(row.get("needs_review", "")).strip() == "0")
    needs_review = sum(1 for row in stations if str(row.get("needs_review", "")).strip() == "1")
    tier1 = sum(1 for row in quality if row.get("tier") == "Tier-1")

    return {
        "generated_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "src_modules": count_files(PROJECT_ROOT / "src" / "airpulse", "**/*.py"),
        "scripts": count_files(PROJECT_ROOT / "scripts", "*.py"),
        "notebooks": count_files(PROJECT_ROOT / "notebooks", "*.ipynb"),
        "raw_csv": count_files(PROJECT_ROOT / "data" / "raw", "*.csv"),
        "station_total": len(stations),
        "station_reviewed": reviewed,
        "station_needs_review": needs_review,
        "quality_rows": len(quality),
        "quality_tier1": tier1,
        "metrics": metrics,
    }


def fmt_num(value: object, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if math.isnan(number):
        return "-"
    return f"{number:.{digits}f}"


def docx_run(text: str, *, bold: bool = False, color: str | None = None, size: int | None = None) -> str:
    props = []
    if bold:
        props.append("<w:b/>")
    if color:
        props.append(f'<w:color w:val="{xml(color)}"/>')
    if size:
        props.append(f'<w:sz w:val="{size * 2}"/>')
    rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    return f"<w:r>{rpr}<w:t xml:space=\"preserve\">{xml(text)}</w:t></w:r>"


def docx_paragraph(
    text: str = "",
    *,
    style: str = "Normal",
    bold: bool = False,
    color: str | None = None,
    size: int | None = None,
    align: str | None = None,
) -> str:
    ppr_parts = [f'<w:pStyle w:val="{style}"/>'] if style != "Normal" else []
    if align:
        ppr_parts.append(f'<w:jc w:val="{align}"/>')
    ppr = f"<w:pPr>{''.join(ppr_parts)}</w:pPr>" if ppr_parts else ""
    return f"<w:p>{ppr}{docx_run(text, bold=bold, color=color, size=size)}</w:p>"


def docx_table(headers: list[str], rows: list[list[object]]) -> str:
    table = [
        "<w:tbl>",
        "<w:tblPr><w:tblStyle w:val=\"AirPulseTable\"/><w:tblW w:w=\"0\" w:type=\"auto\"/>"
        "<w:tblLook w:val=\"04A0\" w:firstRow=\"1\" w:noHBand=\"0\" w:noVBand=\"1\"/></w:tblPr>",
        "<w:tblGrid>",
    ]
    for _ in headers:
        table.append("<w:gridCol w:w=\"2400\"/>")
    table.append("</w:tblGrid>")

    def row_xml(values: list[object], header: bool = False) -> str:
        cells = []
        for value in values:
            fill = '<w:shd w:fill="D9EAFD"/>' if header else ""
            cells.append(
                "<w:tc><w:tcPr>"
                f"{fill}<w:tcW w:w=\"2400\" w:type=\"dxa\"/>"
                "</w:tcPr>"
                f"{docx_paragraph(str(value), bold=header, color='1D1D1F')}"
                "</w:tc>"
            )
        return f"<w:tr>{''.join(cells)}</w:tr>"

    table.append(row_xml(headers, header=True))
    for row in rows:
        table.append(row_xml(row))
    table.append("</w:tbl>")
    return "".join(table)


def docx_styles() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:qFormat/>
    <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/><w:sz w:val="22"/></w:rPr>
    <w:pPr><w:spacing w:after="160" w:line="276" w:lineRule="auto"/></w:pPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/>
    <w:rPr><w:b/><w:color w:val="007AFF"/><w:sz w:val="52"/></w:rPr>
    <w:pPr><w:spacing w:after="260"/></w:pPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Subtitle">
    <w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/><w:qFormat/>
    <w:rPr><w:color w:val="5856D6"/><w:sz w:val="28"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:qFormat/>
    <w:rPr><w:b/><w:color w:val="0F172A"/><w:sz w:val="34"/></w:rPr>
    <w:pPr><w:spacing w:before="360" w:after="160"/><w:outlineLvl w:val="0"/></w:pPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:qFormat/>
    <w:rPr><w:b/><w:color w:val="007AFF"/><w:sz w:val="26"/></w:rPr>
    <w:pPr><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="1"/></w:pPr>
  </w:style>
  <w:style w:type="table" w:styleId="AirPulseTable">
    <w:name w:val="AirPulse Table"/>
    <w:tblPr><w:tblBorders>
      <w:top w:val="single" w:sz="4" w:color="CBD5E1"/>
      <w:left w:val="single" w:sz="4" w:color="CBD5E1"/>
      <w:bottom w:val="single" w:sz="4" w:color="CBD5E1"/>
      <w:right w:val="single" w:sz="4" w:color="CBD5E1"/>
      <w:insideH w:val="single" w:sz="4" w:color="CBD5E1"/>
      <w:insideV w:val="single" w:sz="4" w:color="CBD5E1"/>
    </w:tblBorders></w:tblPr>
  </w:style>
</w:styles>
"""


def build_docx_document(stats: dict[str, object]) -> str:
    metrics = stats["metrics"]
    metric_rows = [
        [
            row.get("pollutant", "").upper(),
            row.get("best_model_name", ""),
            row.get("train_rows", ""),
            row.get("test_rows", ""),
            fmt_num(row.get("mae")),
            fmt_num(row.get("rmse")),
            fmt_num(row.get("r2"), 3),
            fmt_num(row.get("mape")),
        ]
        for row in metrics
    ]

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:o="urn:schemas-microsoft-com:office:office" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
        'xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:w10="urn:schemas-microsoft-com:office:word" '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
        'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml" '
        'mc:Ignorable="w14 w15 wp14"><w:body>',
        docx_paragraph("AirPulse Global", style="Title"),
        docx_paragraph("Profesyonel Proje Raporu ve Teknik AnlatÄ±m", style="Subtitle"),
        docx_paragraph(f"HazÄ±rlanma tarihi: {stats['generated_at']} | Ã‡Ä±ktÄ± klasÃ¶rÃ¼: artifacts", color="64748B"),
        docx_paragraph(
            "AirPulse Global; canlÄ± hava kalitesi izleme, istasyon keÅŸfi, rÃ¼zgar baÄŸlamÄ±, "
            "kirletici tahmini, kiÅŸiselleÅŸtirilmiÅŸ aksiyon Ã¶nerileri ve raporlama Ã¶zelliklerini "
            "tek bir Streamlit uygulamasÄ±nda birleÅŸtiren Ã§evresel zeka platformudur."
        ),
        docx_paragraph("1. YÃ¶netici Ã–zeti", style="Heading1"),
        docx_paragraph(
            "Proje, Ä°stanbul merkezli baÅŸlayÄ±p kÃ¼resel istasyon aÄŸÄ±na geniÅŸleyebilen bir hava kalitesi "
            "karar destek sistemi olarak kurgulanmÄ±ÅŸtÄ±r. KullanÄ±cÄ± uygulamayÄ± aÃ§tÄ±ÄŸÄ±nda gÃ¼ncel AQI, PM2.5, "
            "PM10, O3, NO2, SO2 ve CO deÄŸerlerini; harita Ã¼zerinde istasyon daÄŸÄ±lÄ±mÄ±nÄ±; rÃ¼zgarÄ±n kirlilik "
            "taÅŸÄ±nÄ±mÄ±na etkisini; gelecek gÃ¼nlere iliÅŸkin tahminleri; gÃ¼nlÃ¼k davranÄ±ÅŸ Ã¶nerilerini ve dÄ±ÅŸa "
            "aktarÄ±labilir raporlarÄ± aynÄ± deneyim iÃ§inde gÃ¶rebilir."
        ),
        docx_table(
            ["GÃ¶sterge", "DeÄŸer"],
            [
                ["Python modÃ¼lÃ¼", stats["src_modules"]],
                ["YardÄ±mcÄ± script", stats["scripts"]],
                ["AraÅŸtÄ±rma notebook'u", stats["notebooks"]],
                ["Ham istasyon CSV dosyasÄ±", stats["raw_csv"]],
                ["Ä°stasyon kayÄ±tlarÄ±", stats["station_total"]],
                ["KoordinatÄ± doÄŸrulanmÄ±ÅŸ istasyon", stats["station_reviewed"]],
                ["Ä°nceleme bekleyen istasyon", stats["station_needs_review"]],
                ["Veri kalite raporu satÄ±rÄ±", stats["quality_rows"]],
                ["Tier-1 kalite profili", stats["quality_tier1"]],
            ],
        ),
        docx_paragraph("2. Projenin Hedefleri", style="Heading1"),
    ]

    goals = [
        "CanlÄ± hava kalitesi verisini anlaÅŸÄ±lÄ±r, gÃ¼venilir ve aksiyon alÄ±nabilir hale getirmek.",
        "Ä°stanbul ve seÃ§ili global ÅŸehirler iÃ§in istasyon bazlÄ± hava kalitesi gÃ¶rÃ¼nÃ¼rlÃ¼ÄŸÃ¼ saÄŸlamak.",
        "PM2.5, PM10, O3 ve NO2 gibi temel kirleticiler iÃ§in tahmin ve doÄŸrulama altyapÄ±sÄ± kurmak.",
        "RÃ¼zgar, ulaÅŸÄ±m tercihi ve karbon ayak izi gibi baÄŸlamsal deÄŸiÅŸkenleri kullanÄ±cÄ± kararlarÄ±na baÄŸlamak.",
        "Akademik/profesyonel sunumlarda kullanÄ±labilecek rapor, gÃ¶rsel kart ve CSV Ã§Ä±ktÄ±larÄ± Ã¼retmek.",
        "Gizli anahtar yÃ¶netimi, cache stratejisi ve modÃ¼ler mimari ile Ã¼retim ortamÄ±na hazÄ±r bir temel sunmak.",
    ]
    parts.extend(docx_paragraph(f"â€¢ {goal}") for goal in goals)

    parts.extend(
        [
            docx_paragraph("3. Sistem Mimarisi ve Katmanlar", style="Heading1"),
            docx_paragraph(
                "AirPulse Ã¼Ã§ ana katmana ayrÄ±lmÄ±ÅŸtÄ±r: kullanÄ±cÄ±larÄ±n gÃ¶rdÃ¼ÄŸÃ¼ runtime uygulama katmanÄ±, "
                "notebook ve model deneylerinin yer aldÄ±ÄŸÄ± araÅŸtÄ±rma/modelleme katmanÄ±, ve veri, gizli anahtar, "
                "raporlama, Ã§eviri ve yapÄ±landÄ±rma gibi destek servisleri."
            ),
            docx_table(
                ["Katman", "Sorumluluk", "BaÅŸlÄ±ca dosyalar"],
                [
                    [
                        "Runtime uygulama",
                        "Streamlit sayfalarÄ±, canlÄ± dashboard, harita, tahmin, aksiyon, analitik ve rapor ekranlarÄ±.",
                        "app.py, src/airpulse/legacy_app.py, pages/about.py, components/maps.py",
                    ],
                    [
                        "Forecasting ve analitik",
                        "WAQI forecast Ã¶nceliÄŸi, fallback tahmin, offline champion metrikleri, temporal ve dispersiyon analizleri.",
                        "forecasting.py, analytics_engine.py, forecast_evaluation.py",
                    ],
                    [
                        "Veri ve istasyon pipeline",
                        "Ham geÃ§miÅŸlerin yÃ¼klenmesi, istasyon seÃ§imi, istasyon geniÅŸletme, supervised veri seti Ã¼retimi.",
                        "storage.py, station_selection.py, station_expansion.py, dataset_builder.py",
                    ],
                    [
                        "Destek servisleri",
                        "Secrets, yapÄ±landÄ±rma, raporlama, Ã§eviri, ziyaretÃ§i sayacÄ± ve ortak yardÄ±mcÄ±lar.",
                        "config.py, services/secrets.py, services/reporting.py, i18n.py, visitor.py",
                    ],
                ],
            ),
            docx_paragraph("4. Proje YapÄ±sÄ±", style="Heading1"),
            docx_paragraph(
                "KÃ¶k dizindeki app.py yalnÄ±zca Streamlit giriÅŸ noktasÄ±dÄ±r. AsÄ±l Ã¼rÃ¼n deneyimi src/airpulse "
                "paketi iÃ§indedir. data klasÃ¶rÃ¼ ham, harici ve iÅŸlenmiÅŸ veri katmanlarÄ±nÄ±; artifacts modeli, "
                "kalite raporlarÄ±nÄ± ve Ã¼retilebilir sunum/dokÃ¼man Ã§Ä±ktÄ±larÄ±nÄ±; notebooks klasÃ¶rÃ¼ ise araÅŸtÄ±rma "
                "aÅŸamalarÄ±nÄ± barÄ±ndÄ±rÄ±r."
            ),
            docx_paragraph("AirPulse_Global/", style="Heading2"),
        ]
    )

    tree_lines = [
        "|-- app.py: Streamlit giriÅŸ noktasÄ±",
        "|-- src/airpulse/: Ã¼rÃ¼n kodu, forecasting, analytics, servisler ve sayfalar",
        "|-- data/raw/: ham istasyon geÃ§miÅŸleri",
        "|-- data/processed/: cache, validasyon ve istasyon geniÅŸletme Ã§Ä±ktÄ±larÄ±",
        "|-- data/external/waqi/: WAQI katalog ve canlÄ± snapshot verileri",
        "|-- artifacts/: model metrikleri, raporlar ve Ã¼retilecek Ã§Ä±ktÄ± dosyalarÄ±",
        "|-- notebooks/: keÅŸif, pipeline, WAQI entegrasyonu, istasyon geniÅŸletme ve forecasting Ã§alÄ±ÅŸmalarÄ±",
        "|-- scripts/: backtest, offline forecast replay ve model eÄŸitim scriptleri",
        "|-- locales/: TÃ¼rkÃ§e/Ä°ngilizce UI metinleri",
        "`-- .streamlit/: uygulama konfigÃ¼rasyonu ve secrets ÅŸablonlarÄ±",
    ]
    parts.extend(docx_paragraph(line, color="334155") for line in tree_lines)

    parts.extend(
        [
            docx_paragraph("5. Veri KaynaklarÄ± ve Veri AkÄ±ÅŸÄ±", style="Heading1"),
            docx_paragraph(
                "CanlÄ± veri akÄ±ÅŸÄ± WAQI/AQICN ÅŸehir, istasyon, geo ve map bounds endpoint'leri Ã¼zerinden kurulur. "
                "RÃ¼zgar baÄŸlamÄ± Tomorrow.io ile, tarihsel hava ve hava kalitesi fallback baÄŸlamÄ± Open-Meteo ile "
                "desteklenir. Uygulama cache sÃ¼relerini config.py iÃ§inde merkezi olarak yÃ¶netir; bu sayede hem "
                "kullanÄ±cÄ± deneyimi hÄ±zlanÄ±r hem de API limitlerine saygÄ±lÄ± bir Ã§alÄ±ÅŸma dÃ¼zeni korunur."
            ),
            docx_table(
                ["Kaynak", "KullanÄ±m amacÄ±", "Projedeki karÅŸÄ±lÄ±ÄŸÄ±"],
                [
                    ["WAQI / AQICN", "CanlÄ± AQI, istasyon arama, bounds tabanlÄ± harita ve resmi gÃ¼nlÃ¼k forecast.", "waqi_integration.py, legacy_app.py"],
                    ["Tomorrow.io", "RÃ¼zgar hÄ±zÄ±, yÃ¶nÃ¼ ve gust bilgisiyle dispersiyon baÄŸlamÄ±.", "tomorrow_wind(), services/secrets.py"],
                    ["Open-Meteo", "Anahtar gerektirmeyen tarihsel hava kalitesi ve hava durumu fallback verisi.", "weather_integration.py"],
                    ["Yerel CSV/parquet", "Offline eÄŸitim, backtest, cache ve validasyon kayÄ±tlarÄ±.", "data/raw, data/processed, artifacts"],
                ],
            ),
            docx_paragraph("6. Tahmin ve Modelleme YaklaÅŸÄ±mÄ±", style="Heading1"),
            docx_paragraph(
                "Ãœretim uygulamasÄ± kaynak ÅŸeffaflÄ±ÄŸÄ±nÄ± Ã¶nceleyen muhafazakar bir tahmin stratejisi kullanÄ±r. "
                "Ã–ncelikle WAQI'nin resmi gÃ¼nlÃ¼k forecast verisi tercih edilir. Bu veri yoksa gerÃ§ek gÃ¶zlem "
                "geÃ§miÅŸi ve canlÄ± deÄŸerlerle desteklenen Holt-Winters fallback tahmini devreye girer. AraÅŸtÄ±rma "
                "katmanÄ±nda Prophet, Random Forest, Gradient Boosting ve ensemble modelleriyle offline benchmark "
                "Ã§alÄ±ÅŸmalarÄ± yapÄ±lmÄ±ÅŸtÄ±r."
            ),
            docx_table(
                ["Kirletici", "En iyi offline model", "Train", "Test", "MAE", "RMSE", "R2", "MAPE"],
                metric_rows,
            ),
            docx_paragraph("7. Uygulama SayfalarÄ± ve KullanÄ±cÄ± Deneyimi", style="Heading1"),
        ]
    )

    pages = [
        ("Dashboard", "GÃ¼ncel AQI, temel kirleticiler, rÃ¼zgar baÄŸlamÄ±, global karÅŸÄ±laÅŸtÄ±rma ve ÅŸehir kartlarÄ±."),
        ("Global Station Map", "WAQI istasyon aramasÄ±, harita katmanlarÄ±, yakÄ±n istasyonlar ve istasyon sabitleme."),
        ("Forecast", "PM2.5/PM10/O3/NO2 iÃ§in 7 gÃ¼nlÃ¼k tahmin, kaynak etiketi, belirsizlik bandÄ± ve diagnostik aÃ§Ä±klama."),
        ("Take Action", "AkÄ±llÄ± ulaÅŸÄ±m, karbon ayak izi, gÃ¼nlÃ¼k checklist, aksiyon skoru ve kiÅŸisel Ã¶neriler."),
        ("Reports", "A4 PDF, sosyal paylaÅŸÄ±m JPEG kartÄ± ve CSV indirme Ã§Ä±ktÄ±larÄ±."),
        ("Analytics", "Temporal pattern, rÃ¼zgar dispersiyonu, forecast validasyonu, ÅŸehir karÅŸÄ±laÅŸtÄ±rmasÄ± ve anomali analizi."),
        ("About", "Platform tanÄ±tÄ±mÄ±, teknoloji yÄ±ÄŸÄ±nÄ±, Ã¶zellikler ve konfigÃ¼rasyon bilgisi."),
    ]
    parts.extend(docx_paragraph(f"â€¢ {name}: {desc}") for name, desc in pages)

    parts.extend(
        [
            docx_paragraph("8. TasarÄ±m Sistemi", style="Heading1"),
            docx_paragraph(
                "ArayÃ¼z tasarÄ±mÄ±nda gÃ¼venilir, sakin, analitik ve operasyonel bir ton hedeflenmiÅŸtir. "
                "Renk sistemi AQI risk anlamÄ±yla tutarlÄ±dÄ±r: iyi seviye yeÅŸil, orta seviye sarÄ±, hassas gruplar "
                "iÃ§in saÄŸlÄ±ksÄ±z turuncu, saÄŸlÄ±ksÄ±z kÄ±rmÄ±zÄ± ve daha yÃ¼ksek riskler mor/maroon skalasÄ±yla gÃ¶sterilir. "
                "Kart sistemi, yoÄŸun veriyi okunabilir yÃ¼zeylere bÃ¶ler; grafiklerde Plotly, haritalarda Folium/Leaflet kullanÄ±lÄ±r."
            ),
            docx_paragraph("9. GÃ¼venlik, KonfigÃ¼rasyon ve YayÄ±na Alma", style="Heading1"),
            docx_paragraph(
                "API anahtarlarÄ± kod iÃ§ine gÃ¶mÃ¼lmek yerine Streamlit secrets, ortam deÄŸiÅŸkenleri veya yerel fallback "
                "dosyalarÄ± Ã¼zerinden okunur. .gitignore gerÃ§ek secrets dosyalarÄ±nÄ±, token dosyalarÄ±nÄ±, loglarÄ± ve yerel "
                "runtime dosyalarÄ±nÄ± dÄ±ÅŸarÄ±da bÄ±rakacak ÅŸekilde dÃ¼zenlenmiÅŸtir. YayÄ±n iÃ§in app.py giriÅŸ noktasÄ±, "
                "requirements.txt baÄŸÄ±mlÄ±lÄ±k listesi ve .streamlit/secrets.toml.template onboarding ÅŸablonu hazÄ±rdÄ±r."
            ),
            docx_paragraph("10. GÃ¼Ã§lÃ¼ YÃ¶nler, SÄ±nÄ±rlÄ±lÄ±klar ve Yol HaritasÄ±", style="Heading1"),
            docx_paragraph("GÃ¼Ã§lÃ¼ yÃ¶nler", style="Heading2"),
        ]
    )

    strengths = [
        "Tek uygulamada canlÄ± izleme, forecast, aksiyon ve raporlama akÄ±ÅŸÄ±nÄ±n birleÅŸmesi.",
        "Kaynak ÅŸeffaflÄ±ÄŸÄ±: provider verisi, fallback tahmin ve offline benchmark ayrÄ±mÄ± UI'da aÃ§Ä±kÃ§a etiketlenir.",
        "ModÃ¼ler veri/model katmanÄ± sayesinde yeni ÅŸehir ve istasyonlarÄ±n eklenebilmesi.",
        "API cache ve hata toleransÄ± ile daha stabil kullanÄ±cÄ± deneyimi.",
    ]
    parts.extend(docx_paragraph(f"â€¢ {item}") for item in strengths)

    parts.append(docx_paragraph("SÄ±nÄ±rlÄ±lÄ±klar", style="Heading2"))
    limitations = [
        "BazÄ± istasyon kayÄ±tlarÄ±nda koordinat ve attribution alanlarÄ± inceleme beklemektedir.",
        "Analytics Lab bazÄ± gÃ¶rÃ¼nÃ¼mlerde tarihsel backend eksik olduÄŸunda deterministik sentetik sinyaller kullanÄ±r.",
        "Ãœretim tahmini, notebook modellerinden daha muhafazakar davranÄ±r; bunun nedeni canlÄ± veri gÃ¼venilirliÄŸi ve kaynak ÅŸeffaflÄ±ÄŸÄ±dÄ±r.",
    ]
    parts.extend(docx_paragraph(f"â€¢ {item}") for item in limitations)

    parts.append(docx_paragraph("Ã–nerilen yol haritasÄ±", style="Heading2"))
    roadmap = [
        "Ä°stanbul istasyon koordinatlarÄ±nÄ±n ve attribution alanlarÄ±nÄ±n tam doÄŸrulanmasÄ±.",
        "Forecast validation kaydÄ±nÄ±n dÃ¼zenli Ã§alÄ±ÅŸan bir gÃ¶revle bÃ¼yÃ¼tÃ¼lmesi.",
        "Offline champion modellerinin kontrollÃ¼ A/B mantÄ±ÄŸÄ±yla Ã¼retim tahmin yoluna daha fazla entegre edilmesi.",
        "Rapor Ã§Ä±ktÄ±larÄ±nÄ±n kurum logosu, tarih aralÄ±ÄŸÄ± ve otomatik yorumlama bÃ¶lÃ¼mleriyle Ã¶zelleÅŸtirilmesi.",
        "Deployment sonrasÄ± izleme: API hata oranÄ±, cache hit oranÄ±, sayfa yÃ¼kleme sÃ¼resi ve forecast sapmasÄ±.",
    ]
    parts.extend(docx_paragraph(f"â€¢ {item}") for item in roadmap)

    parts.extend(
        [
            docx_paragraph("11. SonuÃ§", style="Heading1"),
            docx_paragraph(
                "AirPulse Global, hava kalitesi verisini yalnÄ±zca gÃ¶steren deÄŸil, yorumlayan ve kullanÄ±cÄ±yÄ± "
                "daha iyi karar almaya yÃ¶nlendiren bir platform olarak yapÄ±landÄ±rÄ±lmÄ±ÅŸtÄ±r. Kod organizasyonu, "
                "veri katmanlarÄ±, modelleme artefaktlarÄ±, tasarÄ±m sistemi ve raporlama yetenekleri birlikte "
                "deÄŸerlendirildiÄŸinde proje; hem teknik bir bitirme/prototip Ã§alÄ±ÅŸmasÄ± hem de geliÅŸtirilebilir "
                "bir Ã§evresel zeka Ã¼rÃ¼nÃ¼ niteliÄŸi taÅŸÄ±r."
            ),
            '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>',
            "</w:body></w:document>",
        ]
    )
    return "".join(parts)


def write_docx(stats: dict[str, object]) -> None:
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
"""
    core = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>AirPulse Global Profesyonel Proje Raporu</dc:title>
  <dc:subject>Hava kalitesi izleme, tahmin, analitik ve raporlama platformu</dc:subject>
  <dc:creator>Codex</dc:creator>
  <cp:lastModifiedBy>Codex</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{datetime.utcnow().isoformat()}Z</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{datetime.utcnow().isoformat()}Z</dcterms:modified>
</cp:coreProperties>
"""
    app = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>AirPulse Deliverable Generator</Application>
</Properties>
"""
    with zipfile.ZipFile(DOCX_PATH, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("_rels/.rels", rels)
        package.writestr("docProps/core.xml", core)
        package.writestr("docProps/app.xml", app)
        package.writestr("word/styles.xml", docx_styles())
        package.writestr("word/document.xml", build_docx_document(stats))


EMU = 914400


def emu(inches: float) -> int:
    return int(inches * EMU)


def ppt_text_runs(lines: list[str], *, font_size: int, color: str, bullet: bool = False) -> str:
    paragraphs = []
    for line in lines:
        ppr = ""
        if bullet:
            ppr = '<a:pPr marL="285750" indent="-171450"><a:buChar char="â€¢"/></a:pPr>'
        paragraphs.append(
            f"<a:p>{ppr}<a:r><a:rPr lang=\"tr-TR\" sz=\"{font_size * 100}\">"
            f"<a:solidFill><a:srgbClr val=\"{xml(color)}\"/></a:solidFill>"
            f"</a:rPr><a:t>{xml(line)}</a:t></a:r></a:p>"
        )
    return "".join(paragraphs)


def ppt_shape(
    shape_id: int,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    text: str | list[str] = "",
    fill: str = "FFFFFF",
    line: str = "FFFFFF",
    radius: bool = False,
    font_size: int = 18,
    color: str = "1D1D1F",
    bold: bool = False,
    bullet: bool = False,
    name: str = "Shape",
) -> str:
    lines = text if isinstance(text, list) else [text]
    geom = "roundRect" if radius else "rect"
    body_pr = '<a:bodyPr wrap="square" anchor="mid"><a:spAutoFit/></a:bodyPr>'
    if bullet:
        body_pr = '<a:bodyPr wrap="square"><a:spAutoFit/></a:bodyPr>'
    run_xml = ppt_text_runs(lines, font_size=font_size, color=color, bullet=bullet)
    if bold and not bullet:
        run_xml = "".join(
            f"<a:p><a:r><a:rPr lang=\"tr-TR\" b=\"1\" sz=\"{font_size * 100}\">"
            f"<a:solidFill><a:srgbClr val=\"{xml(color)}\"/></a:solidFill>"
            f"</a:rPr><a:t>{xml(line)}</a:t></a:r></a:p>"
            for line in lines
        )
    return f"""
<p:sp>
  <p:nvSpPr><p:cNvPr id="{shape_id}" name="{xml(name)}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
  <p:spPr>
    <a:xfrm><a:off x="{emu(x)}" y="{emu(y)}"/><a:ext cx="{emu(w)}" cy="{emu(h)}"/></a:xfrm>
    <a:prstGeom prst="{geom}"><a:avLst/></a:prstGeom>
    <a:solidFill><a:srgbClr val="{xml(fill)}"/></a:solidFill>
    <a:ln><a:solidFill><a:srgbClr val="{xml(line)}"/></a:solidFill></a:ln>
  </p:spPr>
  <p:txBody>{body_pr}<a:lstStyle/>{run_xml}</p:txBody>
</p:sp>
"""


def ppt_slide_xml(slide: dict[str, object], index: int) -> str:
    bg = slide.get("bg", "F5F7FB")
    shapes = [
        ppt_shape(2, 0, 0, 13.333, 0.22, fill=slide.get("accent", "007AFF"), line=slide.get("accent", "007AFF")),
        ppt_shape(3, 0.62, 0.42, 7.7, 0.62, text=slide["title"], fill=bg, line=bg, font_size=25, color="0F172A", bold=True),
        ppt_shape(4, 11.35, 0.5, 1.25, 0.34, text=f"{index:02d}", fill=slide.get("accent", "007AFF"), line=slide.get("accent", "007AFF"), radius=True, font_size=12, color="FFFFFF", bold=True),
    ]
    if slide.get("subtitle"):
        shapes.append(
            ppt_shape(5, 0.66, 1.02, 9.5, 0.42, text=slide["subtitle"], fill=bg, line=bg, font_size=12, color="64748B")
        )
    next_id = 6
    for item in slide.get("cards", []):
        shapes.append(
            ppt_shape(
                next_id,
                item["x"],
                item["y"],
                item["w"],
                item["h"],
                text=item["text"],
                fill=item.get("fill", "FFFFFF"),
                line=item.get("line", "E2E8F0"),
                radius=True,
                font_size=item.get("font_size", 15),
                color=item.get("color", "1D1D1F"),
                bold=item.get("bold", False),
                bullet=item.get("bullet", False),
                name=item.get("name", "Card"),
            )
        )
        next_id += 1
    for item in slide.get("bars", []):
        shapes.append(
            ppt_shape(
                next_id,
                item["x"],
                item["y"],
                item["w"],
                item["h"],
                fill=item.get("fill", "007AFF"),
                line=item.get("fill", "007AFF"),
                radius=True,
            )
        )
        next_id += 1

    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:bg><p:bgPr><a:solidFill><a:srgbClr val="{xml(bg)}"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>
    <p:spTree>
      <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
      <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
      {''.join(shapes)}
    </p:spTree>
  </p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>
"""


def ppt_theme() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="AirPulse">
  <a:themeElements>
    <a:clrScheme name="AirPulse">
      <a:dk1><a:srgbClr val="0F172A"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>
      <a:dk2><a:srgbClr val="1D1D1F"/></a:dk2><a:lt2><a:srgbClr val="F5F7FB"/></a:lt2>
      <a:accent1><a:srgbClr val="007AFF"/></a:accent1><a:accent2><a:srgbClr val="34C759"/></a:accent2>
      <a:accent3><a:srgbClr val="FF9500"/></a:accent3><a:accent4><a:srgbClr val="FF3B30"/></a:accent4>
      <a:accent5><a:srgbClr val="5856D6"/></a:accent5><a:accent6><a:srgbClr val="AF52DE"/></a:accent6>
      <a:hlink><a:srgbClr val="007AFF"/></a:hlink><a:folHlink><a:srgbClr val="5856D6"/></a:folHlink>
    </a:clrScheme>
    <a:fontScheme name="AirPulse"><a:majorFont><a:latin typeface="Aptos Display"/></a:majorFont><a:minorFont><a:latin typeface="Aptos"/></a:minorFont></a:fontScheme>
    <a:fmtScheme name="AirPulse">
      <a:fillStyleLst>
        <a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
        <a:gradFill rotWithShape="1"><a:gsLst><a:gs pos="0"><a:schemeClr val="phClr"/></a:gs><a:gs pos="100000"><a:schemeClr val="phClr"><a:tint val="80000"/></a:schemeClr></a:gs></a:gsLst><a:lin ang="5400000" scaled="0"/></a:gradFill>
        <a:gradFill rotWithShape="1"><a:gsLst><a:gs pos="0"><a:schemeClr val="phClr"><a:tint val="80000"/></a:schemeClr></a:gs><a:gs pos="100000"><a:schemeClr val="phClr"><a:shade val="80000"/></a:schemeClr></a:gs></a:gsLst><a:lin ang="5400000" scaled="0"/></a:gradFill>
      </a:fillStyleLst>
      <a:lnStyleLst>
        <a:ln w="6350" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>
        <a:ln w="12700" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>
        <a:ln w="19050" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>
      </a:lnStyleLst>
      <a:effectStyleLst>
        <a:effectStyle><a:effectLst/></a:effectStyle>
        <a:effectStyle><a:effectLst><a:outerShdw blurRad="40000" dist="20000" dir="5400000" rotWithShape="0"><a:srgbClr val="000000"><a:alpha val="18000"/></a:srgbClr></a:outerShdw></a:effectLst></a:effectStyle>
        <a:effectStyle><a:effectLst><a:outerShdw blurRad="57150" dist="38100" dir="5400000" rotWithShape="0"><a:srgbClr val="000000"><a:alpha val="23000"/></a:srgbClr></a:outerShdw></a:effectLst></a:effectStyle>
      </a:effectStyleLst>
      <a:bgFillStyleLst>
        <a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
        <a:solidFill><a:schemeClr val="phClr"><a:tint val="95000"/></a:schemeClr></a:solidFill>
        <a:gradFill rotWithShape="1"><a:gsLst><a:gs pos="0"><a:schemeClr val="phClr"/></a:gs><a:gs pos="100000"><a:schemeClr val="phClr"><a:tint val="90000"/></a:schemeClr></a:gs></a:gsLst><a:lin ang="5400000" scaled="0"/></a:gradFill>
      </a:bgFillStyleLst>
    </a:fmtScheme>
  </a:themeElements>
</a:theme>
"""


def build_slides(stats: dict[str, object]) -> list[dict[str, object]]:
    metrics = {row.get("pollutant", ""): row for row in stats["metrics"]}
    pm25_rmse = fmt_num(metrics.get("pm25", {}).get("rmse"))
    pm10_rmse = fmt_num(metrics.get("pm10", {}).get("rmse"))
    o3_rmse = fmt_num(metrics.get("o3", {}).get("rmse"))
    no2_rmse = fmt_num(metrics.get("no2", {}).get("rmse"))

    return [
        {
            "title": "AirPulse Global",
            "subtitle": "CanlÄ± hava kalitesi zekasÄ±, tahmin, aksiyon ve raporlama platformu",
            "accent": "007AFF",
            "cards": [
                {"x": 0.75, "y": 1.65, "w": 5.9, "h": 2.05, "text": "Problem: hava kalitesi verisi Ã§oÄŸu kullanÄ±cÄ± iÃ§in daÄŸÄ±nÄ±k, teknik ve aksiyona uzak.", "fill": "E0F2FE", "line": "BAE6FD", "font_size": 20, "bold": True},
                {"x": 6.9, "y": 1.65, "w": 5.55, "h": 2.05, "text": "Ã‡Ã¶zÃ¼m: canlÄ± Ã¶lÃ§Ã¼m, istasyon haritasÄ±, forecast, rÃ¼zgar baÄŸlamÄ± ve Ã¶nerileri tek deneyimde birleÅŸtirmek.", "fill": "EEF2FF", "line": "C7D2FE", "font_size": 19, "bold": True},
                {"x": 0.85, "y": 4.25, "w": 2.35, "h": 1.1, "text": f"{stats['src_modules']} modÃ¼l", "fill": "FFFFFF", "line": "CBD5E1", "font_size": 19, "bold": True},
                {"x": 3.45, "y": 4.25, "w": 2.35, "h": 1.1, "text": f"{stats['raw_csv']} ham CSV", "fill": "FFFFFF", "line": "CBD5E1", "font_size": 19, "bold": True},
                {"x": 6.05, "y": 4.25, "w": 2.35, "h": 1.1, "text": f"{stats['station_total']} istasyon", "fill": "FFFFFF", "line": "CBD5E1", "font_size": 19, "bold": True},
                {"x": 8.65, "y": 4.25, "w": 2.95, "h": 1.1, "text": "7 Ã¼rÃ¼n sayfasÄ±", "fill": "FFFFFF", "line": "CBD5E1", "font_size": 19, "bold": True},
            ],
        },
        {
            "title": "Projenin Ana Hedefi",
            "subtitle": "Veriyi gÃ¶stermekten karar destek sistemine geÃ§iÅŸ",
            "accent": "34C759",
            "cards": [
                {"x": 0.8, "y": 1.55, "w": 3.55, "h": 3.9, "text": ["CanlÄ± AQI ve kirletici izleme", "Ä°stasyon ve ÅŸehir karÅŸÄ±laÅŸtÄ±rmasÄ±", "Risk seviyesini sadeleÅŸtirme"], "fill": "ECFDF5", "line": "BBF7D0", "bullet": True},
                {"x": 4.85, "y": 1.55, "w": 3.55, "h": 3.9, "text": ["Forecast ve kaynak ÅŸeffaflÄ±ÄŸÄ±", "WAQI Ã¶nceliÄŸi, fallback etiketi", "Offline benchmark ile araÅŸtÄ±rma desteÄŸi"], "fill": "EFF6FF", "line": "BFDBFE", "bullet": True},
                {"x": 8.9, "y": 1.55, "w": 3.55, "h": 3.9, "text": ["KiÅŸisel aksiyon Ã¶nerileri", "Karbon ayak izi ve checklist", "PDF/JPEG/CSV rapor Ã¼retimi"], "fill": "FFF7ED", "line": "FED7AA", "bullet": True},
            ],
        },
        {
            "title": "Mimari: 3 KatmanlÄ± YapÄ±",
            "subtitle": "ÃœrÃ¼n deneyimi, araÅŸtÄ±rma/modelleme ve destek servisleri ayrÄ±ÅŸtÄ±rÄ±ldÄ±",
            "accent": "5856D6",
            "cards": [
                {"x": 0.75, "y": 1.45, "w": 3.65, "h": 4.25, "text": "Runtime Application\napp.py -> legacy_app.py\nDashboard, harita, forecast, aksiyon, rapor ve analytics sayfalarÄ±", "fill": "EEF2FF", "line": "C7D2FE", "font_size": 17, "bold": True},
                {"x": 4.85, "y": 1.45, "w": 3.65, "h": 4.25, "text": "Research & Modeling\nnotebooks + forecasting.py\nStation expansion, feature engineering, backtest ve offline modeller", "fill": "F0FDFA", "line": "99F6E4", "font_size": 17, "bold": True},
                {"x": 8.95, "y": 1.45, "w": 3.65, "h": 4.25, "text": "Support Layer\nconfig, secrets, reporting, i18n\nCache TTL, API anahtarlarÄ±, Ã§Ä±ktÄ± Ã¼retimi ve Ã§eviri", "fill": "F8FAFC", "line": "CBD5E1", "font_size": 17, "bold": True},
            ],
        },
        {
            "title": "Proje KlasÃ¶r YapÄ±sÄ±",
            "subtitle": "KÃ¶k giriÅŸ sade, iÅŸ mantÄ±ÄŸÄ± src/airpulse paketinde toplandÄ±",
            "accent": "007AFF",
            "cards": [
                {"x": 0.75, "y": 1.35, "w": 5.8, "h": 4.75, "text": ["app.py: Streamlit giriÅŸ noktasÄ±", "src/airpulse: Ã¼rÃ¼n ve model kodu", "data/raw: ham istasyon CSV'leri", "data/processed: cache ve validasyon", "artifacts: metrikler ve raporlar"], "fill": "FFFFFF", "line": "CBD5E1", "bullet": True, "font_size": 16},
                {"x": 6.85, "y": 1.35, "w": 5.7, "h": 4.75, "text": ["notebooks: keÅŸif ve model araÅŸtÄ±rmasÄ±", "scripts: backtest ve eÄŸitim otomasyonu", "locales: TR/EN arayÃ¼z metinleri", ".streamlit: config ve secrets ÅŸablonu", "DESIGN_SYSTEM.md: UI ilkeleri"], "fill": "F8FAFC", "line": "CBD5E1", "bullet": True, "font_size": 16},
            ],
        },
        {
            "title": "Veri KaynaklarÄ± ve AkÄ±ÅŸ",
            "subtitle": "CanlÄ± API, tarihsel fallback ve yerel artefaktlar birlikte Ã§alÄ±ÅŸÄ±r",
            "accent": "FF9500",
            "cards": [
                {"x": 0.75, "y": 1.35, "w": 3.3, "h": 4.65, "text": "WAQI / AQICN\nCanlÄ± AQI, istasyon, bounds haritasÄ± ve resmi gÃ¼nlÃ¼k forecast", "fill": "FFF7ED", "line": "FED7AA", "font_size": 17, "bold": True},
                {"x": 4.25, "y": 1.35, "w": 3.3, "h": 4.65, "text": "Tomorrow.io\nRÃ¼zgar hÄ±zÄ±/yÃ¶nÃ¼ ve dispersiyon baÄŸlamÄ±", "fill": "EFF6FF", "line": "BFDBFE", "font_size": 17, "bold": True},
                {"x": 7.75, "y": 1.35, "w": 3.3, "h": 4.65, "text": "Open-Meteo\nAnahtarsÄ±z tarihsel hava ve hava kalitesi fallback'i", "fill": "ECFDF5", "line": "BBF7D0", "font_size": 17, "bold": True},
                {"x": 11.25, "y": 1.35, "w": 1.05, "h": 4.65, "text": "Cache", "fill": "0F172A", "line": "0F172A", "font_size": 16, "color": "FFFFFF", "bold": True},
            ],
        },
        {
            "title": "Forecast Stratejisi",
            "subtitle": "Ãœretimde istikrar, ÅŸeffaflÄ±k ve fallback gÃ¼venliÄŸi Ã¶ncelikli",
            "accent": "AF52DE",
            "cards": [
                {"x": 0.85, "y": 1.4, "w": 3.35, "h": 3.8, "text": "1\nWAQI Native Forecast\nResmi saÄŸlayÄ±cÄ± verisi varsa doÄŸrudan kullanÄ±lÄ±r.", "fill": "F5F3FF", "line": "DDD6FE", "font_size": 18, "bold": True},
                {"x": 4.95, "y": 1.4, "w": 3.35, "h": 3.8, "text": "2\nObserved History\nOpen-Meteo geÃ§miÅŸi canlÄ± WAQI deÄŸeriyle ankore edilir.", "fill": "EFF6FF", "line": "BFDBFE", "font_size": 18, "bold": True},
                {"x": 9.05, "y": 1.4, "w": 3.35, "h": 3.8, "text": "3\nHolt-Winters Fallback\nForecast sÃ¼rekliliÄŸi iÃ§in muhafazakar tahmin Ã¼retilir.", "fill": "FFF7ED", "line": "FED7AA", "font_size": 18, "bold": True},
            ],
        },
        {
            "title": "Offline Model Metrikleri",
            "subtitle": "Notebook araÅŸtÄ±rma katmanÄ±ndan Ã¼retime rehberlik eden benchmark sonuÃ§larÄ±",
            "accent": "007AFF",
            "cards": [
                {"x": 0.75, "y": 1.35, "w": 2.9, "h": 1.2, "text": f"PM2.5\nRMSE {pm25_rmse}", "fill": "E0F2FE", "line": "BAE6FD", "font_size": 18, "bold": True},
                {"x": 3.95, "y": 1.35, "w": 2.9, "h": 1.2, "text": f"PM10\nRMSE {pm10_rmse}", "fill": "ECFDF5", "line": "BBF7D0", "font_size": 18, "bold": True},
                {"x": 7.15, "y": 1.35, "w": 2.9, "h": 1.2, "text": f"O3\nRMSE {o3_rmse}", "fill": "FEFCE8", "line": "FEF08A", "font_size": 18, "bold": True},
                {"x": 10.35, "y": 1.35, "w": 2.15, "h": 1.2, "text": f"NO2\nRMSE {no2_rmse}", "fill": "FEE2E2", "line": "FECACA", "font_size": 18, "bold": True},
                {"x": 1.0, "y": 3.2, "w": 11.0, "h": 2.15, "text": ["PM2.5 iÃ§in tree_ensemble_diverse modeli R2=0.771 ile gÃ¼Ã§lÃ¼ sonuÃ§ verdi.", "PM10 ve O3 tarafÄ±nda random forest benchmark Ã¶ne Ã§Ä±ktÄ±.", "NO2 iÃ§in tree_ensemble_stress_mix modeli dÃ¼ÅŸÃ¼k RMSE ile seÃ§ildi.", "Ãœretimde bu araÅŸtÄ±rma Ã§Ä±ktÄ±larÄ± ÅŸeffaf ve kontrollÃ¼ entegrasyon iÃ§in referans olarak tutuluyor."], "fill": "FFFFFF", "line": "CBD5E1", "bullet": True, "font_size": 15},
            ],
        },
        {
            "title": "KullanÄ±cÄ± Deneyimi",
            "subtitle": "Dashboard'dan rapora uzanan uÃ§tan uca akÄ±ÅŸ",
            "accent": "34C759",
            "cards": [
                {"x": 0.75, "y": 1.25, "w": 3.8, "h": 1.05, "text": "Dashboard\nAnlÄ±k durum", "fill": "ECFDF5", "line": "BBF7D0", "font_size": 17, "bold": True},
                {"x": 4.75, "y": 1.25, "w": 3.8, "h": 1.05, "text": "Station Map\nKonumsal keÅŸif", "fill": "EFF6FF", "line": "BFDBFE", "font_size": 17, "bold": True},
                {"x": 8.75, "y": 1.25, "w": 3.8, "h": 1.05, "text": "Forecast\nGelecek risk", "fill": "F5F3FF", "line": "DDD6FE", "font_size": 17, "bold": True},
                {"x": 0.75, "y": 3.0, "w": 3.8, "h": 1.05, "text": "Take Action\nKiÅŸisel Ã¶neri", "fill": "FFF7ED", "line": "FED7AA", "font_size": 17, "bold": True},
                {"x": 4.75, "y": 3.0, "w": 3.8, "h": 1.05, "text": "Analytics\nDerin analiz", "fill": "F8FAFC", "line": "CBD5E1", "font_size": 17, "bold": True},
                {"x": 8.75, "y": 3.0, "w": 3.8, "h": 1.05, "text": "Reports\nPDF/JPEG/CSV", "fill": "FEE2E2", "line": "FECACA", "font_size": 17, "bold": True},
            ],
        },
        {
            "title": "Analytics Lab",
            "subtitle": "Hava kalitesi davranÄ±ÅŸÄ±nÄ± farklÄ± lenslerle aÃ§Ä±klama",
            "accent": "5856D6",
            "cards": [
                {"x": 0.85, "y": 1.35, "w": 5.4, "h": 4.45, "text": ["Temporal pattern: saat/gÃ¼n yapÄ±sÄ±", "Wind dispersion: rÃ¼zgarla taÅŸÄ±nÄ±m", "Forecast accuracy: validasyon ve drift", "Comparative analysis: ÅŸehirler arasÄ± profil", "Anomaly detection: sapma ve risk sinyali"], "fill": "EEF2FF", "line": "C7D2FE", "bullet": True, "font_size": 17},
                {"x": 6.75, "y": 1.35, "w": 5.6, "h": 4.45, "text": "Not: Tarihsel analytics backend eksik olduÄŸunda motor deterministik, lokasyonla seed'lenmiÅŸ sinyaller Ã¼retir. Bu yaklaÅŸÄ±m demo stabilitesini korurken UI'nin boÅŸ kalmasÄ±nÄ± engeller.", "fill": "FFFFFF", "line": "CBD5E1", "font_size": 19, "bold": True},
            ],
        },
        {
            "title": "Aksiyon ve Raporlama",
            "subtitle": "Verinin kullanÄ±cÄ± davranÄ±ÅŸÄ±na ve paylaÅŸÄ±labilir Ã§Ä±ktÄ±lara dÃ¶nÃ¼ÅŸmesi",
            "accent": "FF3B30",
            "cards": [
                {"x": 0.85, "y": 1.45, "w": 3.6, "h": 3.95, "text": "Action Engine\nAQI, dominant pollutant, rÃ¼zgar, profil bayraklarÄ± ve checklist Ã¼zerinden ilk 3 Ã¶neriyi sÄ±ralar.", "fill": "FFF7ED", "line": "FED7AA", "font_size": 17, "bold": True},
                {"x": 4.85, "y": 1.45, "w": 3.6, "h": 3.95, "text": "Carbon & Commute\nUlaÅŸÄ±m, et tÃ¼ketimi, uÃ§uÅŸ, elektrik ve Ä±sÄ±nma Ã¼zerinden kiÅŸisel etki hesabÄ± yapÄ±lÄ±r.", "fill": "ECFDF5", "line": "BBF7D0", "font_size": 17, "bold": True},
                {"x": 8.85, "y": 1.45, "w": 3.6, "h": 3.95, "text": "Export Ready\nA4 PDF, sosyal medya JPEG kartÄ± ve CSV Ã§Ä±ktÄ±sÄ± tek akÄ±ÅŸta Ã¼retilir.", "fill": "EFF6FF", "line": "BFDBFE", "font_size": 17, "bold": True},
            ],
        },
        {
            "title": "GÃ¼venlik ve YayÄ±na Alma",
            "subtitle": "Anahtar yÃ¶netimi, cache ve deployment hazÄ±rlÄ±ÄŸÄ±",
            "accent": "0F172A",
            "cards": [
                {"x": 0.85, "y": 1.35, "w": 5.55, "h": 4.6, "text": ["WAQI_TOKEN ve TOMORROW_IO_API_KEY kod iÃ§ine yazÄ±lmaz.", "Streamlit secrets, environment variable ve yerel fallback desteklenir.", ".gitignore gerÃ§ek secrets, log ve runtime dosyalarÄ±nÄ± dÄ±ÅŸarÄ±da bÄ±rakÄ±r.", "Cache TTL deÄŸerleri config.py iÃ§inde merkezi yÃ¶netilir."], "fill": "F8FAFC", "line": "CBD5E1", "bullet": True, "font_size": 16},
                {"x": 6.9, "y": 1.35, "w": 5.45, "h": 4.6, "text": ["Deployment checklist: app.py, requirements.txt, secrets template.", "Ãœretim yaklaÅŸÄ±mÄ±: saÄŸlayÄ±cÄ± verisi Ã¶nce, fallback aÃ§Ä±kÃ§a etiketli.", "API limitlerine saygÄ±lÄ± session reuse ve retry mantÄ±ÄŸÄ±.", "Rapor Ã§Ä±ktÄ±larÄ± artifacts ve uygulama indirme akÄ±ÅŸÄ±nda kullanÄ±labilir."], "fill": "EFF6FF", "line": "BFDBFE", "bullet": True, "font_size": 16},
            ],
        },
        {
            "title": "Yol HaritasÄ± ve SonuÃ§",
            "subtitle": "Proje geliÅŸtirilebilir bir Ã§evresel zeka Ã¼rÃ¼nÃ¼ temelindedir",
            "accent": "007AFF",
            "cards": [
                {"x": 0.75, "y": 1.35, "w": 5.8, "h": 4.65, "text": ["Ä°stanbul istasyon metadata doÄŸrulamasÄ±nÄ± tamamlama", "Forecast validation store'u dÃ¼zenli bÃ¼yÃ¼tme", "Offline champion modellerini kontrollÃ¼ ÅŸekilde Ã¼retime alma", "Kurumsal rapor ÅŸablonlarÄ± ve otomatik yorumlama ekleme"], "fill": "FFFFFF", "line": "CBD5E1", "bullet": True, "font_size": 16},
                {"x": 6.95, "y": 1.35, "w": 5.55, "h": 4.65, "text": "SonuÃ§\nAirPulse Global; canlÄ± veri, modelleme, aksiyon Ã¶nerisi ve raporlamayÄ± bir araya getirerek hava kalitesi bilgisini karar destek Ã¼rÃ¼nÃ¼ne dÃ¶nÃ¼ÅŸtÃ¼rÃ¼r.", "fill": "E0F2FE", "line": "BAE6FD", "font_size": 20, "bold": True},
            ],
        },
    ]


def ppt_master_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>
  <p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>
  <p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>
  <p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles>
</p:sldMaster>
"""


def ppt_layout_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank" preserve="1">
  <p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sldLayout>
"""


def write_pptx(stats: dict[str, object]) -> None:
    slides = build_slides(stats)
    overrides = "\n".join(
        f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        for i in range(1, len(slides) + 1)
    )
    content_types = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
  <Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
  <Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
  {overrides}
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
"""
    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
"""
    slide_ids = "\n".join(f'<p:sldId id="{255 + i}" r:id="rId{i + 1}"/>' for i in range(1, len(slides) + 1))
    rel_items = [
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
    ]
    rel_items.extend(
        f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>'
        for i in range(1, len(slides) + 1)
    )
    presentation = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>
  <p:sldIdLst>{slide_ids}</p:sldIdLst>
  <p:sldSz cx="12192000" cy="6858000" type="wide"/>
  <p:notesSz cx="6858000" cy="9144000"/>
</p:presentation>
"""
    pres_rels = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  {''.join(rel_items)}
</Relationships>
"""
    master_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>
</Relationships>
"""
    layout_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>
</Relationships>
"""
    core = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>AirPulse Global Proje Sunumu</dc:title>
  <dc:creator>Codex</dc:creator>
  <cp:lastModifiedBy>Codex</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{datetime.utcnow().isoformat()}Z</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{datetime.utcnow().isoformat()}Z</dcterms:modified>
</cp:coreProperties>
"""
    app = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>AirPulse Deliverable Generator</Application>
  <Slides>{len(slides)}</Slides>
</Properties>
"""

    with zipfile.ZipFile(PPTX_PATH, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("_rels/.rels", root_rels)
        package.writestr("docProps/core.xml", core)
        package.writestr("docProps/app.xml", app)
        package.writestr("ppt/presentation.xml", presentation)
        package.writestr("ppt/_rels/presentation.xml.rels", pres_rels)
        package.writestr("ppt/slideMasters/slideMaster1.xml", ppt_master_xml())
        package.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", master_rels)
        package.writestr("ppt/slideLayouts/slideLayout1.xml", ppt_layout_xml())
        package.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", layout_rels)
        package.writestr("ppt/theme/theme1.xml", ppt_theme())
        for i, slide in enumerate(slides, start=1):
            package.writestr(f"ppt/slides/slide{i}.xml", ppt_slide_xml(slide, i))
            package.writestr(
                f"ppt/slides/_rels/slide{i}.xml.rels",
                """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
</Relationships>
""",
            )


def main() -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    stats = load_project_stats()
    write_docx(stats)
    write_pptx(stats)
    print(json.dumps({"docx": str(DOCX_PATH), "pptx": str(PPTX_PATH)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
