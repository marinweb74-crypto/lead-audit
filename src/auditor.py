"""
auditor.py — Генерация персонализированных аудитов через Gemini API + PDF.
Чёрно-белый дизайн, 5-6 страниц, профессиональный тон.
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
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable, PageBreak,
    Table, TableStyle, KeepTogether, ListFlowable, ListItem,
)
from reportlab.lib.colors import HexColor, black, white
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

MONTHS_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}

logger = logging.getLogger("auditor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

BK = HexColor("#111111")
DG = HexColor("#444444")
MG = HexColor("#888888")
LG = HexColor("#cccccc")
VLG = HexColor("#f2f2f2")
TBL_LINE = HexColor("#999999")
BAR_DARK = HexColor("#333333")
BAR_MED = HexColor("#999999")
BAR_LIGHT = HexColor("#cccccc")

_FONT = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"

for _regular, _bold_path in [
    ("C:/Windows/Fonts/times.ttf", "C:/Windows/Fonts/timesbd.ttf"),
    ("/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf",
     "/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman_Bold.ttf"),
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
        except Exception:
            pass
        break

def _s():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("H1", fontName=_FONT_BOLD, fontSize=14, leading=18, textColor=BK, spaceBefore=6*mm, spaceAfter=3*mm))
    ss.add(ParagraphStyle("H2", fontName=_FONT_BOLD, fontSize=12, leading=16, textColor=BK, spaceBefore=4*mm, spaceAfter=2*mm))
    ss.add(ParagraphStyle("B", fontName=_FONT, fontSize=11, leading=15, alignment=TA_JUSTIFY, textColor=DG, spaceAfter=2*mm))
    ss.add(ParagraphStyle("SM", fontName=_FONT, fontSize=9, leading=12, textColor=MG))
    ss.add(ParagraphStyle("FT", fontName=_FONT, fontSize=8, leading=10, alignment=TA_CENTER, textColor=LG))
    ss.add(ParagraphStyle("BL", fontName=_FONT, fontSize=11, leading=15, textColor=DG, leftIndent=6*mm, spaceAfter=1.5*mm))
    return ss

TBL = TableStyle([
    ("FONTNAME", (0,0), (-1,0), _FONT_BOLD), ("FONTNAME", (0,1), (-1,-1), _FONT),
    ("FONTSIZE", (0,0), (-1,-1), 10), ("LEADING", (0,0), (-1,-1), 14),
    ("BACKGROUND", (0,0), (-1,0), VLG), ("TEXTCOLOR", (0,0), (-1,-1), BK),
    ("LINEBELOW", (0,0), (-1,0), 0.8, TBL_LINE),
    ("LINEBELOW", (0,1), (-1,-2), 0.3, LG),
    ("LINEBELOW", (0,-1), (-1,-1), 0.5, TBL_LINE),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("TOPPADDING", (0,0), (-1,-1), 4), ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ("LEFTPADDING", (0,0), (-1,-1), 5),
])

MAX_RETRIES = 3
RETRY_BACKOFF = 2
DELAY_BETWEEN_CALLS = 1.5

def _safe_int(v, d=0):
    if v is None: return d
    try: return int(v)
    except: return d

def _safe_float(v, d=0.0):
    if v is None: return d
    try: return float(v)
    except: return d

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def _build_prompt(lead: dict) -> str:
    niche = NICHE_STATS.get(lead.get("category", ""), {"avg_check": 3000, "mobile_search": 75, "conversion_low": 0.06, "conversion_high": 0.10})
    competitors_raw = lead.get("competitors_in_search", "[]")
    if isinstance(competitors_raw, str):
        try: competitors_list = json.loads(competitors_raw)
        except: competitors_list = []
    else:
        competitors_list = competitors_raw or []
    monthly = _safe_int(lead.get("monthly_searches"))

    data = textwrap.dedent(f"""\
        Компания: {lead.get('name', 'N/A')}
        Город: {lead.get('city', 'N/A')}
        Категория: {lead.get('category', 'N/A')}
        Видим в поиске: {'да, позиция ' + str(lead.get('search_position')) if lead.get('search_visible') else 'нет, не найдена в первых 10 результатах'}
        Запросов в месяц: ~{monthly}
        Конкурентов в выдаче: {_safe_int(lead.get('competitors_total'))}
        Из них с сайтом: {_safe_int(lead.get('competitors_with_site'))}
        Конкуренты: {', '.join(competitors_list[:5]) if competitors_list else 'нет данных'}
        Мобильный поиск: {niche['mobile_search']}%
    """)

    return textwrap.dedent(f"""\
        Ты — аналитик digital-маркетинга. Напиши развёрнутый анализ цифрового
        присутствия компании на русском языке.

        ПРАВИЛА:
        - Пиши от лица "мы" (команда аналитиков).
        - Тон: спокойный, экспертный, как консультант. НЕ как продавец.
        - НЕ начинай с приветствия.
        - Обращайся на "вы" к владельцу.
        - ЗАПРЕЩЕНО: восклицательные знаки, обещания денег/выручки,
          "огромный потенциал", "вы теряете", давление, манипуляции.
        - НЕ считай деньги за клиента.
        - НЕ упоминай рейтинги, отзывы, Яндекс Карты.

        Формат — 3 раздела по 5-7 предложений каждый:

        1. Видимость в поиске
        - Нашли ли компанию в поисковой выдаче, на какой позиции.
        - {niche['mobile_search']}% запросов идут с мобильных.
        - 2ГИС — это каталог, он не заменяет сайт.
        - Клиенты часто оценивают компанию по наличию сайта — без него
          доверие ниже. Объясни почему: нет цен, нет портфолио, нет отзывов
          в одном месте, нет удобной формы связи.
        - Расскажи что первое впечатление формируется онлайн.

        2. Конкурентная среда
        - Назови конкретных конкурентов из выдачи.
        - Сколько конкурентов с сайтом. Что у них есть на сайте (цены,
          портфолио, онлайн-запись, отзывы).
        - Объясни как наличие сайта влияет на выбор клиента: когда
          человек сравнивает две компании — одну с сайтом и одну без.

        3. Спрос в нише
        - Объём запросов (оценка, не точные данные).
        - Распиши поведение клиента: человек вводит запрос, видит
          результаты, переходит на сайт, смотрит цены/портфолио,
          звонит или оставляет заявку. Без сайта вы выпадаете из
          этой цепочки.
        - Упомяни что сайт работает 24/7 и принимает заявки
          даже когда вы закрыты.
        - Упомяни что сайт — это настройка под продвижение в поисковых
          системах (SEO), что даёт бесплатный трафик.

        Объём: 2000-3000 символов. Развёрнуто, но без воды.

        === ДАННЫЕ ===
        {data}
    """)


def generate_audit_text(api_key, model: str, lead: dict) -> str:
    prompt = _build_prompt(lead)
    key = api_key if isinstance(api_key, str) else api_key[0]

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                json={
                    "model": model,
                    "max_tokens": 2048,
                    "messages": [{"role": "user", "content": prompt}],
                },
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                },
                timeout=90,
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"]
        except requests.exceptions.HTTPError as e:
            st = e.response.status_code if e.response is not None else 0
            if st == 429 and attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF ** attempt
                logger.warning("Claude 429, retry in %ds", wait)
                time.sleep(wait); continue
            if st >= 500 and attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF ** attempt); continue
            raise
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF ** attempt); continue
            raise
    raise RuntimeError("Claude API failed after retries")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _san(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`(.+?)`", r"<b>\1</b>", text)
    return text

def _add(story, text, style):
    for p in text.split("\n"):
        p = p.strip()
        if not p: story.append(Spacer(1, 2*mm))
        else: story.append(Paragraph(_san(p), style))

def _sections(text: str) -> dict:
    secs = {}; cur = "intro"; secs[cur] = []
    for line in text.split("\n"):
        s = line.strip()
        if not s: secs.setdefault(cur, []).append(""); continue
        c = re.sub(r"^#{1,4}\s*", "", s)
        lo = c.lower()
        if re.match(r"^\**1[\.\)]", lo) and any(w in lo for w in ("видимость","поиск","интернет","онлайн")):
            cur = "vis"; continue
        elif re.match(r"^\**2[\.\)]", lo) and any(w in lo for w in ("конкурент","среда","рынок")):
            cur = "comp"; continue
        elif re.match(r"^\**3[\.\)]", lo) and any(w in lo for w in ("спрос","поток","клиент","запрос","ниш")):
            cur = "demand"; continue
        secs.setdefault(cur, []).append(c)
    return {k: "\n".join(v).strip() for k, v in secs.items()}


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def generate_pdf(lead: dict, audit_text: str, channel: str = "telegram") -> str:
    os.makedirs(PDF_DIR, exist_ok=True)
    safe = re.sub(r"[^\w\s-]", "", lead.get("name", "audit")).strip().replace(" ", "_")
    pdf_path = os.path.join(PDF_DIR, f"Анализ_{safe[:40]}.pdf")
    ss = _s()
    doc = SimpleDocTemplate(pdf_path, pagesize=A4,
        topMargin=20*mm, bottomMargin=18*mm, leftMargin=22*mm, rightMargin=22*mm)

    niche = NICHE_STATS.get(lead.get("category", ""), {"avg_check": 3000, "mobile_search": 75, "conversion_low": 0.06, "conversion_high": 0.10})
    cat = lead.get("category", "")
    city = lead.get("city", "")
    name = lead.get("name", "")
    secs = _sections(audit_text)
    s = []
    now = datetime.now()
    month_ru = MONTHS_RU.get(now.month, "")

    # ==================== PAGE 1 — COVER ====================
    s.append(Spacer(1, 70*mm))
    s.append(Paragraph("АНАЛИЗ ЦИФРОВОГО ПРИСУТСТВИЯ",
        ParagraphStyle("C1", fontName=_FONT_BOLD, fontSize=20, leading=26, alignment=TA_CENTER, textColor=BK, spaceAfter=8*mm)))
    s.append(Paragraph(_san(name),
        ParagraphStyle("C2", fontName=_FONT_BOLD, fontSize=16, leading=22, alignment=TA_CENTER, textColor=BK, spaceAfter=4*mm)))
    s.append(Paragraph(f"{cat}  |  {city}",
        ParagraphStyle("C3", fontName=_FONT, fontSize=12, leading=16, alignment=TA_CENTER, textColor=MG, spaceAfter=3*mm)))
    s.append(Paragraph(f"{month_ru} {now.year}",
        ParagraphStyle("C4", fontName=_FONT, fontSize=11, leading=14, alignment=TA_CENTER, textColor=MG, spaceAfter=40*mm)))
    s.append(Paragraph("Подготовлено командой STUN Agency",
        ParagraphStyle("C5", fontName=_FONT, fontSize=11, leading=14, alignment=TA_CENTER, textColor=MG, spaceAfter=2*mm)))
    s.append(Paragraph("stun.website",
        ParagraphStyle("C6", fontName=_FONT, fontSize=10, leading=13, alignment=TA_CENTER, textColor=LG)))

    # ==================== PAGE 2 — VISIBILITY ====================
    s.append(PageBreak())
    s.append(Paragraph("1. Видимость в поиске", ss["H1"]))
    if secs.get("vis"):
        _add(s, secs["vis"], ss["B"])
    s.append(Spacer(1, 4*mm))

    # ==================== PAGE 3 — COMPETITORS ====================
    s.append(Paragraph("2. Конкурентная среда", ss["H1"]))
    if secs.get("comp"):
        _add(s, secs["comp"], ss["B"])
    s.append(Spacer(1, 3*mm))

    total = _safe_int(lead.get("competitors_total"), 1) or 1
    ws = _safe_int(lead.get("competitors_with_site"))
    pct = round(ws / total * 100) if total else 0
    t = Table([
        ["Показатель", "Значение", "Источник"],
        ["Компаний в категории", str(total), "2ГИС"],
        ["Из них с собственным сайтом", f"{ws} ({pct}%)", "Поисковая выдача"],
        ["Без сайта (включая вас)", str(max(total - ws, 0)), "—"],
    ], colWidths=[65*mm, 45*mm, 55*mm])
    t.setStyle(TBL)
    s.append(KeepTogether([Spacer(1, 2*mm), t, Spacer(1, 4*mm)]))

    # ==================== PAGE 4 — DEMAND + SEASONALITY ====================
    s.append(Paragraph("3. Спрос в нише", ss["H1"]))
    if secs.get("demand"):
        _add(s, secs["demand"], ss["B"])
    s.append(Spacer(1, 3*mm))

    ms = _safe_int(lead.get("monthly_searches"))
    t2 = Table([
        ["Показатель", "Значение", "Источник"],
        ["Запросов в месяц", f"~{ms:,}".replace(",", " "), "Оценка (Яндекс Вордстат)"],
        ["Мобильный трафик", f"{niche['mobile_search']}%", "Среднее по нише"],
    ], colWidths=[65*mm, 45*mm, 55*mm])
    t2.setStyle(TBL)
    s.append(KeepTogether([Spacer(1, 2*mm), t2, Spacer(1, 5*mm)]))

    s.append(Paragraph("Сезонность спроса", ss["H2"]))
    s.append(Paragraph(
        f"Динамика запросов по категории «{cat}» по месяцам, % от пика (Яндекс Вордстат):",
        ss["SM"]))
    s.append(Spacer(1, 2*mm))
    s.append(_chart(cat))
    s.append(Spacer(1, 5*mm))

    # ==================== PAGE 5 — WHY SITE + CHECKLIST ====================
    s.append(Paragraph("4. Зачем нужен сайт", ss["H1"]))
    s.append(Paragraph(
        "Сайт — это не просто визитка в интернете. Это рабочий инструмент, "
        "который выполняет несколько задач одновременно:", ss["B"]))
    s.append(Spacer(1, 2*mm))

    reasons = [
        "<b>Доверие клиентов.</b> Когда человек выбирает между двумя компаниями — одной с сайтом и одной без — "
        "в большинстве случаев он выберет ту, где можно посмотреть цены, портфолио, контакты. "
        "Сайт — это подтверждение того, что бизнес работает серьёзно.",
        "<b>Приём заявок 24/7.</b> Сайт работает, когда вы закрыты. Клиент может оставить заявку "
        "в 11 вечера, а утром вы уже перезвоните. Без сайта этот клиент уйдёт к конкуренту.",
        "<b>Продвижение в поиске (SEO).</b> Сайт, настроенный под поисковые системы, получает "
        "бесплатный трафик из Яндекса и Google. Это не реклама — за эти переходы вы не платите.",
        "<b>Все услуги в одном месте.</b> Прайс-лист, фотографии работ, карта проезда, "
        "график работы, контакты — клиент получает всю информацию за 30 секунд, "
        "не листая каталоги и мессенджеры.",
        "<b>Конкурентное преимущество.</b> У большинства ваших конкурентов сайт уже есть. "
        "Без него вы объективно проигрываете в онлайне — даже если качество услуг у вас выше.",
    ]
    for r in reasons:
        s.append(Paragraph(f"\u2022  {r}", ss["BL"]))
    s.append(Spacer(1, 4*mm))

    s.append(Paragraph("Что обычно включает сайт для вашей ниши", ss["H2"]))
    checklist = [
        "Адаптивный дизайн (удобно с телефона и компьютера)",
        "Кнопка звонка в один клик",
        "Форма онлайн-заявки / записи",
        "Страница услуг с ценами",
        "Фотографии работ / портфолио",
        "Карта проезда и контакты",
        f"Настройка под поисковые системы (SEO под «{cat.lower()} {city.lower()}»)",
        "Подключение Яндекс Метрики для отслеживания посетителей",
    ]
    for item in checklist:
        s.append(Paragraph(f"\u2713  {_san(item)}", ss["BL"]))
    s.append(Spacer(1, 5*mm))

    # ==================== PAGE 6 — CTA ====================
    s.append(Paragraph("Что дальше", ss["H1"]))
    s.append(Paragraph(
        f"Если вам интересно обсудить, как сайт мог бы работать "
        f"для «{_san(name)}» — напишите нам. Мы расскажем что входит "
        f"в типовое решение для вашей ниши, сроки и стоимость. "
        f"Без обязательств — просто разговор.", ss["B"]))
    s.append(Spacer(1, 4*mm))

    ct = Table([
        ["Сайт:", "stun.website"],
        ["Telegram:", "@stunagncy"],
        ["Канал:", "t.me/stun_agency"],
    ], colWidths=[28*mm, 80*mm])
    ct.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), _FONT), ("FONTSIZE", (0,0), (-1,-1), 11),
        ("TEXTCOLOR", (0,0), (0,-1), MG), ("TEXTCOLOR", (1,0), (1,-1), BK),
        ("LEFTPADDING", (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3), ("TOPPADDING", (0,0), (-1,-1), 3),
    ]))
    s.append(ct)
    s.append(Spacer(1, 8*mm))

    s.append(Paragraph(
        "P.S. Это не массовая рассылка. Мы вручную собрали данные "
        "по вашей компании и подготовили этот отчёт.",
        ParagraphStyle("PS", fontName=_FONT, fontSize=10, leading=13, textColor=MG)))
    s.append(Spacer(1, 12*mm))

    s.append(Paragraph(
        "Оценки основаны на открытых данных (2ГИС, поисковая выдача, Яндекс Вордстат) "
        "и могут отличаться от фактических показателей.", ss["FT"]))
    s.append(Paragraph(f"STUN Agency  |  stun.website  |  {now.strftime('%d.%m.%Y')}", ss["FT"]))

    doc.build(s)
    logger.info("PDF: %s", pdf_path)
    return pdf_path


# ---------------------------------------------------------------------------
# Chart (grayscale)
# ---------------------------------------------------------------------------

SEASON = {
    "Шиномонтаж":      [40,30,90,100,60,30,25,30,50,100,90,35],
    "Клининг":          [90,60,70,80,70,60,50,55,65,70,80,100],
    "Автосервис":       [70,65,80,90,85,75,70,70,80,90,85,60],
    "Ремонт телефонов": [80,70,75,70,65,60,55,60,80,75,70,100],
    "Массаж":           [70,65,75,70,65,60,55,60,70,75,80,85],
}
MO = ["Янв","Фев","Мар","Апр","Май","Июн","Июл","Авг","Сен","Окт","Ноя","Дек"]

def _chart(cat: str) -> Drawing:
    data = SEASON.get(cat, [70]*12)
    w, h = 480, 110
    d = Drawing(w, h)
    bw = 32; gap = 8; mx = max(data)
    for i, v in enumerate(data):
        x = i * (bw + gap) + 10
        bh = (v / mx) * 75
        c = HexColor("#2d8a4e") if v >= 80 else (HexColor("#f5a623") if v >= 50 else HexColor("#cc6666"))
        d.add(Rect(x, 18, bw, bh, fillColor=c, strokeColor=None))
        d.add(String(x+bw/2, 4, MO[i], fontSize=7, fontName=_FONT, textAnchor="middle", fillColor=MG))
        d.add(String(x+bw/2, 20+bh, f"{v}%", fontSize=6, fontName=_FONT, textAnchor="middle", fillColor=BK))
    return d


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_audits() -> list[dict]:
    if os.path.exists(AUDITS_JSON):
        with open(AUDITS_JSON, "r", encoding="utf-8") as f: return json.load(f)
    return []

def _save_audits(audits: list[dict]):
    os.makedirs(os.path.dirname(AUDITS_JSON), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(AUDITS_JSON), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f: json.dump(audits, f, ensure_ascii=False, indent=2)
        os.replace(tmp, AUDITS_JSON)
    except:
        if os.path.exists(tmp): os.unlink(tmp)
        raise

def _append_log(lead, path):
    os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(f"- [{datetime.now().strftime('%Y-%m-%d %H:%M')}] **{lead.get('name')}** ({lead.get('city')}, {lead.get('category')}) -> `{os.path.basename(path)}`\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(config: dict):
    init_db()
    api_key = config.get("anthropic_api_key", "")
    if not api_key or api_key.startswith("YOUR_"):
        logger.error("Anthropic API key not set in config.json"); return
    model = config.get("anthropic_model", "claude-haiku-4-20250414")
    leads = get_leads_for_audit()
    if not leads: logger.info("No leads for audit"); return
    logger.info("%d leads for audit", len(leads))
    audits = _load_audits()
    done = 0
    for lead in leads:
        lid = lead["id"]; nm = lead.get("name", "???")
        logger.info("[%d/%d] %s", done+1, len(leads), nm)
        try:
            txt = generate_audit_text(api_key, model, lead)
            pdf = generate_pdf(lead, txt)
            openers = [
                f"Добрый день. Мы — команда STUN Agency, занимаемся созданием сайтов для бизнеса.",
                f"Добрый день. Мы — веб-студия STUN Agency, делаем сайты для малого бизнеса.",
            ]
            bodies = [
                f"Мы подготовили анализ цифрового присутствия вашей компании «{nm}» — во вложении.",
                f"Мы проанализировали онлайн-присутствие «{nm}» и подготовили отчёт — во вложении.",
            ]
            closers = [
                "Если не хотите открывать файл — напишите, продублируем текстом.",
                "Если неудобно открывать PDF — скажите, отправим текстом.",
            ]
            msg = f"{random.choice(openers)}\n\n{random.choice(bodies)}\n\n{random.choice(closers)}"
            rec = {"lead_id": lid, "name": nm, "city": lead.get("city",""), "category": lead.get("category",""),
                   "audit_text": txt, "audit_pdf_path": pdf, "message_text": msg, "generated_at": datetime.now().isoformat()}
            audits.append(rec); _save_audits(audits)
            mark_audit_generated(lid); _append_log(lead, pdf)
            done += 1; logger.info("OK: %s", nm)
        except requests.exceptions.HTTPError as e:
            logger.error("API error %s: HTTP %s", nm, e.response.status_code if e.response else "?"); continue
        except Exception as e:
            logger.error("Error %s: %s", nm, type(e).__name__, exc_info=True); continue
        time.sleep(DELAY_BETWEEN_CALLS)
    logger.info("Done: %d/%d", done, len(leads))

if __name__ == "__main__":
    with open(os.path.join(PROJECT_ROOT, "config.json"), "r", encoding="utf-8") as f: cfg = json.load(f)
    run(cfg)
