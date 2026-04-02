"""
auditor.py — Генерация персонализированных аудитов через Gemini API + PDF.

Для каждого обогащённого лида:
1. Формирует промпт с данными компании
2. Генерирует аудит через Gemini API
3. Создаёт PDF (reportlab) с таблицами
4. Сохраняет результат в audits.json и помечает лида в БД
"""

import os
import json
import logging
import random
import re
import tempfile
import textwrap
import time
import requests
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable,
    Table, TableStyle, KeepTogether,
)
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics.shapes import Drawing, Rect, String

from db import init_db, get_leads_for_audit, mark_audit_generated

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PDF_DIR = os.path.join(PROJECT_ROOT, "agent-runtime", "outputs", "pdfs")
SHARED_DIR = os.path.join(PROJECT_ROOT, "agent-runtime", "shared")
AUDITS_JSON = os.path.join(SHARED_DIR, "audits.json")
AUDIT_LOG = os.path.join(SHARED_DIR, "audit-log.md")

NICHE_STATS = {
    "Шиномонтаж": {"avg_check": 2500, "mobile_search": 73, "conversion_low": 0.05, "conversion_high": 0.08},
    "Клининг": {"avg_check": 4000, "mobile_search": 81, "conversion_low": 0.08, "conversion_high": 0.12},
    "Автосервис": {"avg_check": 5000, "mobile_search": 76, "conversion_low": 0.06, "conversion_high": 0.09},
    "Ремонт телефонов": {"avg_check": 3500, "mobile_search": 85, "conversion_low": 0.10, "conversion_high": 0.15},
    "Массаж": {"avg_check": 3000, "mobile_search": 77, "conversion_low": 0.08, "conversion_high": 0.12},
}

logger = logging.getLogger("auditor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

BLACK = HexColor("#222222")
GRAY = HexColor("#777777")
LGRAY = HexColor("#aaaaaa")
LINE = HexColor("#d0d5dd")
LBGR = HexColor("#f5f5f5")
RED = HexColor("#cc3333")
GREEN = HexColor("#2d8a4e")
LRED = HexColor("#fdecea")
LGREEN = HexColor("#e8f5e9")
LYELLOW = HexColor("#fff8e1")

_FONT = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"

for _regular, _bold_path in [
    ("C:/Windows/Fonts/times.ttf", "C:/Windows/Fonts/timesbd.ttf"),
    (os.path.join(PROJECT_ROOT, "fonts", "times.ttf"), os.path.join(PROJECT_ROOT, "fonts", "timesbd.ttf")),
]:
    if os.path.exists(_regular):
        try:
            pdfmetrics.registerFont(TTFont("TNR", _regular))
            _FONT = "TNR"
            if os.path.exists(_bold_path):
                pdfmetrics.registerFont(TTFont("TNRB", _bold_path))
                _FONT_BOLD = "TNRB"
            else:
                _FONT_BOLD = "TNR"
            logger.info("Font loaded: Times New Roman from %s", _regular)
        except Exception as exc:
            logger.warning("Font error: %s", exc)
        break


def _make_styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("TT", fontName=_FONT_BOLD, fontSize=16, leading=20, alignment=1, textColor=BLACK, spaceAfter=2*mm))
    ss.add(ParagraphStyle("SB", fontName=_FONT, fontSize=11, leading=14, alignment=1, textColor=GRAY, spaceAfter=5*mm))
    ss.add(ParagraphStyle("HH", fontName=_FONT_BOLD, fontSize=13, leading=16, textColor=BLACK, spaceBefore=4*mm, spaceAfter=2*mm))
    ss.add(ParagraphStyle("BB", fontName=_FONT, fontSize=12, leading=16, alignment=TA_JUSTIFY, textColor=BLACK, spaceAfter=1.5*mm))
    ss.add(ParagraphStyle("FF", fontName=_FONT, fontSize=8, leading=10, alignment=1, textColor=LGRAY))
    ss.add(ParagraphStyle("CT", fontName=_FONT_BOLD, fontSize=11, leading=14, alignment=1, textColor=BLACK, spaceBefore=3*mm, spaceAfter=3*mm))
    return ss


TABLE_STYLE = TableStyle([
    ("FONTNAME", (0,0), (-1,0), _FONT_BOLD), ("FONTNAME", (0,1), (-1,-1), _FONT),
    ("FONTSIZE", (0,0), (-1,-1), 10), ("LEADING", (0,0), (-1,-1), 14),
    ("BACKGROUND", (0,0), (-1,0), LBGR), ("TEXTCOLOR", (0,0), (-1,-1), BLACK),
    ("GRID", (0,0), (-1,-1), 0.5, LINE), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("TOPPADDING", (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3),
    ("LEFTPADDING", (0,0), (-1,-1), 4),
])

MAX_RETRIES = 3
RETRY_BACKOFF = 2
DELAY_BETWEEN_CALLS = 1.5


def _safe_int(value, default=0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _safe_float(value, default=0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _build_prompt(lead: dict) -> str:
    niche = NICHE_STATS.get(lead.get("category", ""), {
        "avg_check": 3000, "mobile_search": 75,
        "conversion_low": 0.06, "conversion_high": 0.10,
    })

    competitors_raw = lead.get("competitors_in_search", "[]")
    if isinstance(competitors_raw, str):
        try:
            competitors_list = json.loads(competitors_raw)
        except json.JSONDecodeError:
            competitors_list = []
    else:
        competitors_list = competitors_raw or []

    conv_low = int(niche["conversion_low"] * 100)
    conv_high = int(niche["conversion_high"] * 100)
    monthly_loss = _safe_float(lead.get("monthly_loss"))
    monthly_searches = _safe_int(lead.get("monthly_searches"))
    lost_low = _safe_int(lead.get("lost_clients_low"))
    lost_high = _safe_int(lead.get("lost_clients_high"))

    data_block = textwrap.dedent(f"""\
        Компания: {lead.get('name', 'N/A')}
        Город: {lead.get('city', 'N/A')}
        Категория: {lead.get('category', 'N/A')}
        Видим в поиске: {'да' if lead.get('search_visible') else 'нет'}
        Запросов в месяц: {monthly_searches or 'нет данных'}
        Конкурентов всего: {_safe_int(lead.get('competitors_total'))}
        Конкурентов с сайтом: {_safe_int(lead.get('competitors_with_site'))}
        Конкуренты в выдаче: {', '.join(competitors_list[:5]) if competitors_list else 'нет данных'}
        Упущенных клиентов/мес: {lost_low}-{lost_high}
        Потенциальная выручка/мес: {monthly_loss:.0f} руб.
        Средний чек: {niche['avg_check']} руб.
        Конверсия: {conv_low}-{conv_high}%
        Мобильный поиск: {niche['mobile_search']}%
    """)

    prompt = textwrap.dedent(f"""\
        Ты — эксперт по digital-маркетингу. Напиши аудит цифрового
        присутствия компании на русском языке.
        Тон — спокойный, экспертный, без давления и агрессии.

        КРИТИЧЕСКИЕ ПРАВИЛА:
        - Продаём САЙТ. Весь аудит подводит к этому.
        - НЕ начинай с приветствия. Сразу к делу.
        - ОБРАЩАЙСЯ на "вы" напрямую к владельцу.
        - СТРОГО 3 предложения на раздел. Не больше. Каждое — факт.
        - НЕ используй вводные фразы типа "важно понимать", "стоит отметить".
        - НЕ повторяй одни и те же мысли.
        - НЕ упоминай рейтинги, отзывы, Яндекс Карты.

        Формат — 3 раздела + решение. СТРОГО 3 предложения на раздел:

        1. Видимость в интернете (3 предложения)
        Без сайта вас не находят. {niche['mobile_search']}% ищут с телефона.
        2ГИС — справочник, а не замена сайту.

        2. Конкурентная среда (3 предложения)
        Сколько конкурентов с сайтом. Назови конкретных если есть.
        Они забирают клиентов из поиска.

        3. Поток клиентов (3 предложения)
        Цифры по запросам. Ориентир, не гарантия.
        Эти клиенты уходят к конкурентам с сайтами.

        ЧТО МЫ СДЕЛАЕМ (4-5 предложений):
        Про САЙТ: мобильная версия, кнопка звонка, прайс,
        формы заявки, скорость, портфолио.
        НЕ УКАЗЫВАЙ ЦЕНЫ.

        Объём: 1500-2500 символов. НЕ БОЛЬШЕ. Коротко и по делу.

        === ДАННЫЕ ===
        {data_block}
    """)
    return prompt


def generate_audit_text(api_key, model: str, lead: dict) -> str:
    """Generate audit text. api_key can be a single key or list of keys (rotation on 429)."""
    prompt = _build_prompt(lead)
    keys = api_key if isinstance(api_key, list) else [api_key]

    for i, key in enumerate(keys):
        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": key,
                    },
                    timeout=90,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status == 429 and i < len(keys) - 1:
                    logger.warning("Key %d hit 429, switching to key %d", i + 1, i + 2)
                    break
                if status >= 500 and attempt < MAX_RETRIES - 1:
                    wait = RETRY_BACKOFF ** attempt
                    logger.warning("Gemini %d error, retry in %ds (attempt %d/%d)",
                                   status, wait, attempt + 1, MAX_RETRIES)
                    time.sleep(wait)
                    continue
                raise
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_BACKOFF ** attempt
                    logger.warning("Gemini connection error, retry in %ds: %s", wait, type(e).__name__)
                    time.sleep(wait)
                    continue
                raise
        else:
            continue
        continue

    raise RuntimeError("All API keys exhausted")


def _sanitize(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`(.+?)`", r"<b>\1</b>", text)
    return text


def _add_text(story, text, style):
    for para in text.split("\n"):
        p = para.strip()
        if not p:
            story.append(Spacer(1, 1.5 * mm))
        else:
            story.append(Paragraph(_sanitize(p), style))


def _split_sections(text: str) -> dict:
    sections = {}
    current = "intro"
    sections[current] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            sections.setdefault(current, []).append("")
            continue
        clean = re.sub(r"^#{1,4}\s*", "", stripped)
        lower = clean.lower()
        if re.match(r"^\**1[\.\)]", lower) and ("видимость" in lower or "интернет" in lower or "онлайн" in lower):
            current = "vis"; continue
        elif re.match(r"^\**2[\.\)]", lower) and ("конкурент" in lower or "среда" in lower or "рынок" in lower):
            current = "comp"; continue
        elif re.match(r"^\**3[\.\)]", lower) and ("поток" in lower or "оценка" in lower or "клиент" in lower):
            current = "flow"; continue
        elif "что мы сделаем" in lower or "что мы предлагаем" in lower or "решение" in lower:
            current = "solutions"; continue
        sections.setdefault(current, []).append(clean)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def generate_pdf(lead: dict, audit_text: str, channel: str = "telegram") -> str:
    os.makedirs(PDF_DIR, exist_ok=True)

    safe_name = re.sub(r"[^\w\s-]", "", lead.get("name", "audit")).strip().replace(" ", "_")
    filename = f"Аудит_{safe_name[:40]}.pdf"
    pdf_path = os.path.join(PDF_DIR, filename)

    ss = _make_styles()
    doc = SimpleDocTemplate(
        pdf_path, pagesize=A4,
        topMargin=18*mm, bottomMargin=18*mm, leftMargin=20*mm, rightMargin=20*mm,
    )

    niche = NICHE_STATS.get(lead.get("category", ""), {
        "avg_check": 3000, "mobile_search": 75,
        "conversion_low": 0.06, "conversion_high": 0.10,
    })
    category = lead.get("category", "")
    secs = _split_sections(audit_text)
    s = []

    s.append(Spacer(1, 60*mm))

    s.append(Paragraph(
        "Хотите",
        ParagraphStyle("HL1", fontName=_FONT_BOLD, fontSize=26, leading=32,
                       alignment=TA_CENTER, textColor=BLACK, spaceAfter=1*mm)
    ))
    s.append(Paragraph(
        "от +500 000 \u20BD выручки?",
        ParagraphStyle("HL2", fontName=_FONT_BOLD, fontSize=26, leading=32,
                       alignment=TA_CENTER, textColor=BLACK, spaceAfter=16*mm)
    ))

    s.append(Paragraph(
        "Мы поможем !",
        ParagraphStyle("Help", fontName=_FONT_BOLD, fontSize=18, leading=24,
                       alignment=TA_CENTER, textColor=GREEN, spaceAfter=4*mm)
    ))
    s.append(Paragraph(
        "<i>50+ сайтов для бизнеса</i>",
        ParagraphStyle("Sub1", fontName=_FONT, fontSize=13, leading=18,
                       alignment=TA_CENTER, textColor=BLACK, spaceAfter=2*mm)
    ))
    s.append(Paragraph(
        "<i>до 50 заявок в день у наших клиентов</i>",
        ParagraphStyle("Sub2", fontName=_FONT, fontSize=13, leading=18,
                       alignment=TA_CENTER, textColor=BLACK, spaceAfter=60*mm)
    ))

    s.append(Paragraph(
        "<b>Некоторые кейсы: t.me/stun_agency</b>",
        ParagraphStyle("Cases", fontName=_FONT_BOLD, fontSize=15, leading=20,
                       alignment=TA_CENTER, textColor=BLACK)
    ))

    s.append(Spacer(1, 12*mm))
    s.append(HRFlowable(width="100%", thickness=0.6, color=LINE))
    s.append(Spacer(1, 4*mm))

    s.append(Paragraph(
        f"Аудит: {lead.get('name', '')} — {lead.get('city', '')}, {category}",
        ss["TT"]
    ))
    s.append(Spacer(1, 3*mm))

    s.append(Paragraph("1. Видимость в интернете", ss["HH"]))
    if secs.get("vis"):
        _add_text(s, secs["vis"], ss["BB"])

    s.append(Paragraph("2. Конкурентная среда", ss["HH"]))
    if secs.get("comp"):
        _add_text(s, secs["comp"], ss["BB"])

    total = _safe_int(lead.get("competitors_total"), 1) or 1
    with_site = _safe_int(lead.get("competitors_with_site"))
    pct = round(with_site / total * 100) if total else 0
    t2 = Table([
        ["Показатель", "Значение", "Источник"],
        ["Компаний в нише", str(total), "2ГИС"],
        ["Из них с сайтом", f"{with_site} ({pct}%)", "Brave Search"],
        ["Без сайта (вкл. вас)", str(total - with_site), ""],
    ], colWidths=[65*mm, 50*mm, 50*mm])
    t2.setStyle(TABLE_STYLE)
    s.append(KeepTogether([Spacer(1, 2*mm), t2, Spacer(1, 3*mm)]))

    s.append(Paragraph("3. Потенциальный поток клиентов", ss["HH"]))
    if secs.get("flow"):
        _add_text(s, secs["flow"], ss["BB"])

    conv_low = int(niche.get("conversion_low", 0.05) * 100)
    conv_high = int(niche.get("conversion_high", 0.08) * 100)
    monthly = _safe_float(lead.get("monthly_loss"))
    monthly_searches = _safe_int(lead.get("monthly_searches"))

    t3 = Table([
        ["Показатель", "Значение", "Источник"],
        ["Запросов в месяц", f"~{monthly_searches}", "Яндекс Вордстат"],
        ["Конверсия в нише", f"{conv_low}-{conv_high}%", "Среднее по рынку"],
        ["Потенц. обращений", f"{_safe_int(lead.get('lost_clients_low'))}-{_safe_int(lead.get('lost_clients_high'))}/мес", "Расчёт"],
        ["Средний чек", f"{niche['avg_check']:,} ₽".replace(",", " "), "Среднее по рынку"],
        ["Потенц. выручка", f"~{int(monthly):,} ₽/мес".replace(",", " "), "Расчёт"],
    ], colWidths=[65*mm, 50*mm, 50*mm])

    t3_style = TableStyle([
        ("FONTNAME", (0,0), (-1,0), _FONT_BOLD), ("FONTNAME", (0,1), (-1,-1), _FONT),
        ("FONTSIZE", (0,0), (-1,-1), 10), ("LEADING", (0,0), (-1,-1), 14),
        ("BACKGROUND", (0,0), (-1,0), LBGR), ("TEXTCOLOR", (0,0), (-1,-1), BLACK),
        ("GRID", (0,0), (-1,-1), 0.5, LINE),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("TEXTCOLOR", (2, 1), (2, -1), GRAY),
        ("FONTSIZE", (2, 1), (2, -1), 8),
        ("TEXTCOLOR", (1, 5), (1, 5), GREEN),
        ("FONTNAME", (0, 5), (1, 5), _FONT_BOLD),
    ])
    t3.setStyle(t3_style)
    s.append(KeepTogether([Spacer(1, 2*mm), t3, Spacer(1, 3*mm)]))

    s.append(Paragraph("Воронка клиентов", ss["HH"]))
    lost_low = _safe_int(lead.get("lost_clients_low"))
    lost_high = _safe_int(lead.get("lost_clients_high"))
    site_visitors = int(monthly_searches * 0.35) if monthly_searches else 0
    avg_conv = (niche.get("conversion_low", 0.05) + niche.get("conversion_high", 0.08)) / 2
    clients = int(monthly_searches * avg_conv * 0.7) if monthly_searches else 0

    funnel_rows = [
        ["Этап воронки", "Потенциал", "У вас сейчас"],
        ["Ищут в Яндексе/Google", f"{monthly_searches}/мес", f"{monthly_searches}/мес"],
        ["Переходят на сайт", f"~{site_visitors}", "0"],
        ["Оставляют заявку", f"~{lost_low}-{lost_high}", "0"],
        ["Становятся клиентами", f"~{clients}", "0"],
    ]
    t_funnel = Table(funnel_rows, colWidths=[60*mm, 50*mm, 55*mm])
    funnel_style = TableStyle([
        ("FONTNAME", (0,0), (-1,0), _FONT_BOLD), ("FONTNAME", (0,1), (-1,-1), _FONT),
        ("FONTSIZE", (0,0), (-1,-1), 10), ("LEADING", (0,0), (-1,-1), 14),
        ("BACKGROUND", (0,0), (-1,0), LBGR), ("TEXTCOLOR", (0,0), (-1,-1), BLACK),
        ("GRID", (0,0), (-1,-1), 0.5, LINE),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("TEXTCOLOR", (1, 1), (1, 4), GREEN),
        ("TEXTCOLOR", (2, 2), (2, 4), RED),
        ("BACKGROUND", (2, 2), (2, 4), LRED),
    ])
    t_funnel.setStyle(funnel_style)
    s.append(KeepTogether([Spacer(1, 2*mm), t_funnel, Spacer(1, 3*mm)]))

    s.append(Paragraph("Три сценария развития", ss["HH"]))
    avg_check = niche["avg_check"]
    half_clients = max(clients // 2, 1)
    scenario_rows = [
        ["Сценарий", "Клиентов/мес", "Выручка/мес"],
        ["Сейчас (без сайта)", "0 из поиска", "0 ₽"],
        ["Сайт", f"{half_clients}-{clients}", f"{half_clients*avg_check:,}-{clients*avg_check:,} ₽".replace(",", " ")],
        ["Сайт + реклама", f"{clients}-{int(clients*1.8)}", f"{clients*avg_check:,}-{int(clients*1.8)*avg_check:,} ₽".replace(",", " ")],
    ]
    t_scen = Table(scenario_rows, colWidths=[60*mm, 50*mm, 55*mm])
    scen_style = TableStyle([
        ("FONTNAME", (0,0), (-1,0), _FONT_BOLD), ("FONTNAME", (0,1), (-1,-1), _FONT),
        ("FONTSIZE", (0,0), (-1,-1), 10), ("LEADING", (0,0), (-1,-1), 14),
        ("BACKGROUND", (0,0), (-1,0), LBGR), ("TEXTCOLOR", (0,0), (-1,-1), BLACK),
        ("GRID", (0,0), (-1,-1), 0.5, LINE),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("BACKGROUND", (0, 1), (-1, 1), LRED),
        ("TEXTCOLOR", (1, 1), (2, 1), RED),
        ("BACKGROUND", (0, 2), (-1, 2), LGREEN),
        ("TEXTCOLOR", (1, 2), (2, 2), GREEN),
        ("BACKGROUND", (0, 3), (-1, 3), LGREEN),
        ("TEXTCOLOR", (1, 3), (2, 3), GREEN),
        ("FONTNAME", (0, 3), (-1, 3), _FONT_BOLD),
    ])
    t_scen.setStyle(scen_style)
    s.append(KeepTogether([Spacer(1, 2*mm), t_scen, Spacer(1, 3*mm)]))

    s.append(Paragraph("Сезонность спроса", ss["HH"]))
    s.append(Paragraph(
        f"Уровень запросов «{category}» по месяцам (% от пика):",
        ss["BB"]
    ))
    chart = _make_season_chart(category)
    s.append(KeepTogether([Spacer(1, 2*mm), chart, Spacer(1, 3*mm)]))

    s.append(Spacer(1, 6*mm))
    s.append(HRFlowable(width="100%", thickness=0.6, color=LINE))
    s.append(Paragraph("Что мы сделаем", ss["HH"]))
    if secs.get("solutions"):
        _add_text(s, secs["solutions"], ss["BB"])

    s.append(Spacer(1, 8*mm))
    s.append(HRFlowable(width="100%", thickness=0.5, color=LINE))
    s.append(Spacer(1, 4*mm))

    if channel == "email":
        s.append(Paragraph(
            "Просто свяжитесь с нами ответным письмом или в ТГ <b>@stunagncy</b>",
            ss["CT"],
        ))

    s.append(Spacer(1, 4*mm))
    s.append(Paragraph("STUN Agency | t.me/stun_agency", ss["FF"]))
    s.append(Paragraph(datetime.now().strftime("%d.%m.%Y"), ss["FF"]))

    doc.build(s)
    logger.info("PDF создан: %s", pdf_path)
    return pdf_path


SEASON_DATA = {
    "Шиномонтаж":      [40, 30, 90,100, 60, 30, 25, 30, 50,100, 90, 35],
    "Клининг":          [90, 60, 70, 80, 70, 60, 50, 55, 65, 70, 80,100],
    "Автосервис":       [70, 65, 80, 90, 85, 75, 70, 70, 80, 90, 85, 60],
    "Ремонт телефонов": [80, 70, 75, 70, 65, 60, 55, 60, 80, 75, 70,100],
    "Массаж":           [70, 65, 75, 70, 65, 60, 55, 60, 70, 75, 80, 85],
}
MONTHS = ["Янв","Фев","Мар","Апр","Май","Июн","Июл","Авг","Сен","Окт","Ноя","Дек"]


def _make_season_chart(category: str) -> Drawing:
    data = SEASON_DATA.get(category, [70]*12)
    w, h = 480, 120
    d = Drawing(w, h)
    bar_w = 32
    gap = 8
    max_val = max(data)
    for i, val in enumerate(data):
        x = i * (bar_w + gap) + 10
        bar_h = (val / max_val) * 80
        color = GREEN if val >= 80 else (HexColor("#f5a623") if val >= 50 else RED)
        d.add(Rect(x, 20, bar_w, bar_h, fillColor=color, strokeColor=None))
        d.add(String(x + bar_w/2, 5, MONTHS[i], fontSize=7, fontName=_FONT, textAnchor="middle", fillColor=GRAY))
        d.add(String(x + bar_w/2, 22 + bar_h, f"{val}%", fontSize=6, fontName=_FONT, textAnchor="middle", fillColor=BLACK))
    return d


def _load_audits() -> list[dict]:
    if os.path.exists(AUDITS_JSON):
        with open(AUDITS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_audits(audits: list[dict]):
    """Atomic save: write to temp file then replace."""
    os.makedirs(os.path.dirname(AUDITS_JSON), exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(AUDITS_JSON), suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(audits, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, AUDITS_JSON)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _append_log(lead: dict, pdf_path: str):
    os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(
            f"- [{datetime.now().strftime('%Y-%m-%d %H:%M')}] "
            f"**{lead.get('name')}** ({lead.get('city')}, {lead.get('category')}) → "
            f"`{os.path.basename(pdf_path)}`\n"
        )


def run(config: dict):
    init_db()

    api_keys = config.get("gemini_api_keys", [])
    api_key = api_keys if api_keys else config.get("gemini_api_key", "")
    if not api_key or (isinstance(api_key, str) and api_key.startswith("YOUR_")):
        logger.error("Gemini API key не задан в config.json")
        return

    model = config.get("gemini_model", "gemini-2.5-flash")

    leads = get_leads_for_audit()
    if not leads:
        logger.info("Нет лидов для аудита")
        return

    logger.info("Найдено %d лидов для аудита", len(leads))

    audits = _load_audits()
    processed = 0

    for lead in leads:
        lead_id = lead["id"]
        name = lead.get("name", "???")
        logger.info("[%d/%d] Генерация аудита: %s", processed + 1, len(leads), name)

        try:
            audit_text = generate_audit_text(api_key, model, lead)
            logger.info("Текст: %d символов", len(audit_text))

            pdf_path = generate_pdf(lead, audit_text)

            greetings = ["Здравствуйте", "Добрый день", "Приветствуем"]
            agency = ["диджитал агентство", "digital-агентство", "веб-студия"]
            action = ["по продвижению и созданию сайтов", "по разработке сайтов и продвижению бизнеса", "по созданию и продвижению сайтов"]
            did = ["Мы провели", "Мы подготовили", "Подготовили для вас"]
            audit_word = ["экспресс-аудит", "анализ", "аудит"]
            presence = ["цифрового присутствия", "онлайн-присутствия", "цифровой видимости"]
            doubt = [
                "Если не хотите открывать файл — напишите, отправим всё в текстовом формате.",
                "Если сомневаетесь в файле — напишите, отправим текстом.",
                "Если не хотите открывать файл — скажите, продублируем текстом.",
            ]
            message_text = (
                f"{random.choice(greetings)}, мы {random.choice(agency)} "
                f"{random.choice(action)}. "
                f"{random.choice(did)} {random.choice(audit_word)} "
                f"{random.choice(presence)} вашей компании\n\n"
                f"\U0001F4CE Аудит во вложении.\n\n"
                f"{random.choice(doubt)}"
            )

            audit_record = {
                "lead_id": lead_id,
                "name": name,
                "city": lead.get("city", ""),
                "category": lead.get("category", ""),
                "audit_text": audit_text,
                "audit_pdf_path": pdf_path,
                "message_text": message_text,
                "generated_at": datetime.now().isoformat(),
            }
            audits.append(audit_record)
            _save_audits(audits)

            mark_audit_generated(lead_id)
            _append_log(lead, pdf_path)

            processed += 1
            logger.info("Аудит сохранён: «%s»", name)

        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            logger.error("Gemini API error «%s»: HTTP %s", name, status)
            continue
        except Exception as exc:
            logger.error("Ошибка «%s»: %s", name, type(exc).__name__, exc_info=True)
            continue

        time.sleep(DELAY_BETWEEN_CALLS)

    logger.info("Готово: %d/%d аудитов", processed, len(leads))


if __name__ == "__main__":
    config_path = os.path.join(PROJECT_ROOT, "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    run(cfg)
