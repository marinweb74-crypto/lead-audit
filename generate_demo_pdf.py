"""Generate demo PDF with Gemini text + tables."""
import sys, os, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable,
    Table, TableStyle, KeepTogether,
)
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import requests

pdfmetrics.registerFont(TTFont("DJ", "fonts/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DJB", "fonts/DejaVuSans-Bold.ttf"))

BLACK = HexColor("#222222")
GRAY = HexColor("#777777")
LGRAY = HexColor("#aaaaaa")
LINE = HexColor("#d0d5dd")
LBGR = HexColor("#f5f5f5")

doc = SimpleDocTemplate(
    "demo_audit_gemini.pdf", pagesize=A4,
    topMargin=18*mm, bottomMargin=18*mm, leftMargin=20*mm, rightMargin=20*mm,
)

ss = getSampleStyleSheet()
ss.add(ParagraphStyle("TT", fontName="DJB", fontSize=15, leading=19, alignment=1, textColor=BLACK, spaceAfter=2*mm))
ss.add(ParagraphStyle("SB", fontName="DJ", fontSize=10, leading=13, alignment=1, textColor=GRAY, spaceAfter=8*mm))
ss.add(ParagraphStyle("HH", fontName="DJB", fontSize=11, leading=14, textColor=BLACK, spaceBefore=6*mm, spaceAfter=3*mm))
ss.add(ParagraphStyle("BB", fontName="DJ", fontSize=9.5, leading=13.5, alignment=TA_JUSTIFY, textColor=BLACK, spaceAfter=2*mm))
ss.add(ParagraphStyle("FF", fontName="DJ", fontSize=7.5, leading=10, alignment=1, textColor=LGRAY))
ss.add(ParagraphStyle("CT", fontName="DJB", fontSize=9.5, leading=13, alignment=1, textColor=BLACK, spaceBefore=4*mm, spaceAfter=4*mm))

TS = TableStyle([
    ("FONTNAME", (0,0), (-1,0), "DJB"), ("FONTNAME", (0,1), (-1,-1), "DJ"),
    ("FONTSIZE", (0,0), (-1,-1), 8.5), ("LEADING", (0,0), (-1,-1), 12),
    ("BACKGROUND", (0,0), (-1,0), LBGR), ("TEXTCOLOR", (0,0), (-1,-1), BLACK),
    ("GRID", (0,0), (-1,-1), 0.5, LINE), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("TOPPADDING", (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3),
    ("LEFTPADDING", (0,0), (-1,-1), 4),
])


def sanitize(text):
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`(.+?)`", r"<b>\1</b>", text)
    return text


def add_text(story, text, style):
    for para in text.split("\n"):
        p = para.strip()
        if not p:
            story.append(Spacer(1, 1.5*mm))
        else:
            story.append(Paragraph(sanitize(p), style))


def split_sections(text):
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
        if re.match(r"^\**1[\.\)]", lower) and "видимость" in lower:
            current = "vis"; continue
        elif re.match(r"^\**2[\.\)]", lower) and "конкурент" in lower:
            current = "comp"; continue
        elif re.match(r"^\**3[\.\)]", lower) and ("поток" in lower or "оценка" in lower):
            current = "flow"; continue
        elif re.match(r"^\**4[\.\)]", lower) and "домен" in lower:
            current = "domain"; continue
        elif re.match(r"^\**5[\.\)]", lower) and ("репутац" in lower or "отзыв" in lower):
            current = "rep"; continue
        elif "что мы сделаем" in lower:
            current = "solutions"; continue
        sections.setdefault(current, []).append(clean)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


# --- Get audit from Gemini ---
key = "AIzaSyAMoEyfUMOLi7SaFGeaYROT3WIwkr-Ly7Q"
prompt = """Ты эксперт по digital-маркетингу. Напиши развернутый аудит цифрового присутствия компании.

ДАННЫЕ:
- Компания: АвтоПрофи, шиномонтаж, Казань
- Рейтинг 2ГИС: 4.6, 28 отзывов
- В поиске Яндекс/Google: не найдена
- Google Maps: не заявлен
- Яндекс Карты: не оформлен
- Конкурентов всего: 47, с сайтом: 32 (68%)
- Запросов в месяц по нише: 3400
- Средний чек: 2500 руб
- 73% запросов с мобильных
- Домен avtoprofi-kazan.ru: свободен
- Конверсия в нише: 5-8%
- Потенциальных обращений: 170-272/мес

ФОРМАТ — 5 разделов + блок "Что мы сделаем". Для каждого раздела пиши
2-3 абзаца по 4-6 предложений. Текст должен быть живым, не шаблонным,
с конкретными деталями привязанными именно к этой компании и к Казани.

НЕ начинай с приветствия. Начни сразу с вводного абзаца.
ОБЯЗАТЕЛЬНО обращайся к читателю на "вы" напрямую:
"вы не видны в поиске", "ваши конкуренты", "вы упускаете клиентов".
НИКОГДА не пиши в третьем лице: "компания не представлена",
"АвтоПрофи не видна в поиске". Пиши как будто разговариваешь
с владельцем бизнеса лично.

1. Видимость в интернете
Расскажи где компанию можно найти, где нельзя. Объясни почему 2ГИС
это только часть картины. Упомяни что в Казани высокий спрос на
шиномонтаж особенно в межсезонье. Про мобильный трафик — человеку
на дороге нужна помощь быстро, он ищет с телефона.

2. Конкурентная среда
Контекст — что значит 68% конкурентов с сайтом для тех кто без.
Как меняется рынок. Почему раньше хватало вывески а сейчас нет.

3. Оценка потенциального потока
Откуда цифры, почему это ориентир а не гарантия. Сезонность
шиномонтажа — весна/осень спрос в 2-3 раза выше. Даже скромная
доля потока уже ощутима.

4. Доменное имя
Мы подобрали несколько вариантов доменов. Объясни почему первый
вариант лучший. Что бывает когда домен забирают перекупщики.

5. Репутация
4.6 это хорошо но только на одной площадке. Как клиент принимает
решение — сравнивает несколько источников.

ЧТО МЫ СДЕЛАЕМ:
Пиши от нашего лица — "мы создадим", "мы настроим".
ГЛАВНЫЙ АКЦЕНТ — САЙТ. Подробно 5-8 предложений про сайт:
что на нем будет, как он приведет клиентов, мобильная версия,
кнопка звонка, прайс, отзывы, формы заявок.
В конце 1-2 предложения что также поможем оформить карточки на картах.
НЕ расписывай рекламу и справочники подробно.
НЕ указывай цены.

Общий объем: минимум 5000 символов. Пиши на русском."""

print("Requesting Gemini...")
resp = requests.post(
    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}",
    json={"contents": [{"parts": [{"text": prompt}]}]},
    headers={"Content-Type": "application/json"},
    timeout=90,
)
resp.raise_for_status()
audit_raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
print(f"Gemini: {len(audit_raw)} chars")

secs = split_sections(audit_raw)
for k, v in secs.items():
    print(f"  section '{k}': {len(v)} chars")

# --- Build PDF ---
s = []
s.append(Paragraph("Аудит цифрового присутствия", ss["TT"]))
s.append(Paragraph("АвтоПрофи — Казань, Шиномонтаж", ss["SB"]))
s.append(HRFlowable(width="100%", thickness=0.6, color=LINE))
s.append(Spacer(1, 5*mm))

if secs.get("intro"):
    add_text(s, secs["intro"], ss["BB"])
    s.append(Spacer(1, 2*mm))

# 1
s.append(Paragraph("1. Видимость в интернете", ss["HH"]))
if secs.get("vis"):
    add_text(s, secs["vis"], ss["BB"])
t1 = Table([
    ["Площадка", "Статус", "Комментарий"],
    ["Поиск Яндекс/Google", "Не найдены", "Нет сайта — нет позиций"],
    ["Google Maps", "Не заявлен", "Карточка не оформлена"],
    ["Яндекс Карты", "Не оформлен", "Карточка отсутствует"],
    ["2ГИС", "Есть карточка", "Рейтинг 4.6, 28 отзывов"],
], colWidths=[48*mm, 38*mm, 79*mm])
t1.setStyle(TS)
s.append(KeepTogether([Spacer(1, 2*mm), t1, Spacer(1, 3*mm)]))

# 2
s.append(Paragraph("2. Конкурентная среда", ss["HH"]))
if secs.get("comp"):
    add_text(s, secs["comp"], ss["BB"])
t2 = Table([
    ["Показатель", "Значение"],
    ["Всего компаний в нише", "47"],
    ["Из них с сайтом", "32 (68%)"],
    ["Без сайта", "15"],
], colWidths=[85*mm, 80*mm])
t2.setStyle(TS)
s.append(KeepTogether([Spacer(1, 2*mm), t2, Spacer(1, 3*mm)]))

# 3
s.append(Paragraph("3. Оценка потенциального потока клиентов", ss["HH"]))
if secs.get("flow"):
    add_text(s, secs["flow"], ss["BB"])
t3 = Table([
    ["Показатель", "Значение"],
    ["Запросов в месяц", "~3 400"],
    ["Конверсия", "5-8%"],
    ["Потенциальных обращений", "170-272/мес"],
    ["Средний чек", "2 500 руб."],
    ["Потенциальная выручка", "~550 000 руб./мес"],
], colWidths=[85*mm, 80*mm])
t3.setStyle(TS)
s.append(KeepTogether([Spacer(1, 2*mm), t3, Spacer(1, 3*mm)]))

# 4
s.append(Paragraph("4. Доменное имя", ss["HH"]))
if secs.get("domain"):
    add_text(s, secs["domain"], ss["BB"])
t4 = Table([
    ["Домен", "Статус", "Комментарий"],
    ["avtoprofi-kazan.ru", "Свободен", "Название + город, лучший вариант"],
    ["avtoprofi16.ru", "Свободен", "Название + код региона"],
    ["shinomontazh-avtoprofi.ru", "Свободен", "Услуга + название"],
], colWidths=[55*mm, 35*mm, 75*mm])
t4.setStyle(TS)
s.append(KeepTogether([Spacer(1, 2*mm), t4, Spacer(1, 3*mm)]))

# 5
s.append(Paragraph("5. Репутация и отзывы", ss["HH"]))
if secs.get("rep"):
    add_text(s, secs["rep"], ss["BB"])
t5 = Table([
    ["Площадка", "Рейтинг", "Отзывов", "Комментарий"],
    ["2ГИС", "4.6 из 5", "28", "Хороший результат"],
    ["Google Maps", "—", "0", "Не заявлен"],
    ["Яндекс Карты", "—", "0", "Не оформлен"],
], colWidths=[42*mm, 30*mm, 26*mm, 67*mm])
t5.setStyle(TS)
s.append(KeepTogether([Spacer(1, 2*mm), t5, Spacer(1, 3*mm)]))

# Solutions
s.append(Spacer(1, 3*mm))
s.append(HRFlowable(width="100%", thickness=0.6, color=LINE))
s.append(Paragraph("Что мы сделаем", ss["HH"]))
if secs.get("solutions"):
    add_text(s, secs["solutions"], ss["BB"])

s.append(Spacer(1, 6*mm))
s.append(HRFlowable(width="100%", thickness=0.5, color=LINE))
s.append(Spacer(1, 4*mm))
s.append(Paragraph(
    "Если хотите обсудить детали или посмотреть примеры в вашей нише — "
    "просто ответьте на это сообщение. Это бесплатно и ни к чему не обязывает.",
    ss["CT"],
))
s.append(Spacer(1, 6*mm))
s.append(Paragraph("Аудит подготовлен на основе открытых данных | Конфиденциально", ss["FF"]))
s.append(Paragraph("27.03.2026", ss["FF"]))

doc.build(s)
sz = os.path.getsize("demo_audit_gemini.pdf")
print(f"PDF OK: {sz} bytes")
