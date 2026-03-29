# Pipeline: LeadAudit — Business Digital Audit & Outreach

## Обзор

5 агентов работают последовательно в tmux. Каждый агент создаёт артефакт и передаёт работу следующему через SendMessage. Reviewer проверяет код после каждого этапа.

## Схема

```
coordinator (lead)
     │
     ▼
 parser ──→ agent-runtime/shared/leads.json
     │       2ГИС: название, телефон, email, рейтинг, отзывы
     │       Brave Search: видимость в поиске
     │       Keywords Everywhere: объём запросов в нише
     │       Google Maps: рейтинг, отзывы
     │       NetworkCalc: свободен ли домен
     │       Подсчёт конкурентов с/без сайта
     │
     ├──→ reviewer проверяет код и данные parser
     │
     ▼
 auditor ──→ agent-runtime/shared/audits.json + PDF-файлы
     │       Claude API генерирует персональный аудит:
     │         • Видимость в интернете (5 пунктов)
     │         • Рынок ниши с цифрами
     │         • Расчёт ежедневных потерь в рублях
     │         • Конкуренты с сайтами (имена, позиции)
     │         • Домен: свободен или занят
     │         • Репутация: где видны отзывы, где нет
     │         • Решения: что мы сделаем (без цен)
     │       PDF-отчёт 2-3 страницы для каждой компании
     │       Текстовая версия для отправки в чат
     │
     ├──→ reviewer проверяет качество аудитов
     │
     ▼
 sender ──→ agent-runtime/shared/outreach.json
     │       Telethon: поиск ТГ по телефону + отправка PDF + текст
     │       SMTP: email с PDF-вложением
     │       Логика:
     │         • Проверка дублей перед отправкой
     │         • Максимум 3 касания на лида
     │         • Задержка 30-60 сек между сообщениями
     │         • Сообщение 1: PDF + "если сомневаетесь — скинем текстом"
     │         • Сообщение 2 (через 3 дня): часть аудита текстом
     │         • Сообщение 3 (через 3 дня): последнее касание
     │         • Если ответил — стоп, лид в CRM
     │
     ├──→ reviewer проверяет логику рассылки
     │
     ▼
 coordinator ──→ agent-runtime/outputs/report.md
              Итоговый отчёт:
                • Сколько компаний спарсено
                • Сколько аудитов сгенерировано
                • Сколько отправлено (ТГ / email)
                • Сколько ответили
                • Ошибки и проблемы
```

## Агенты

| # | Агент | Файл | Входные данные | Выходные данные |
|---|-------|------|----------------|-----------------|
| 1 | coordinator | `.claude/agents/coordinator.md` | Задание от пользователя | plan.md, status.md, report.md |
| 2 | parser | `.claude/agents/parser.md` | Города, категории | leads.json, parser-log.md |
| 3 | auditor | `.claude/agents/auditor.md` | leads.json | audits.json, PDF-файлы, audit-log.md |
| 4 | sender | `.claude/agents/sender.md` | audits.json | outreach.json, sender-log.md |
| 5 | reviewer | `.claude/agents/reviewer.md` | Код всех агентов | review-report.md |

## Коммуникация между агентами

```
coordinator ──SendMessage──→ parser
                             "Спарси 2ГИС: Казань, Самара. Категории: Шиномонтаж, Клининг. Лимит: 50"

parser      ──SendMessage──→ reviewer
                             "Готово: 48 компаний в leads.json. Проверь код и данные"

reviewer    ──SendMessage──→ coordinator
                             "Parser проверен. 2 замечания исправлены. Код чистый. Можно передавать auditor"

coordinator ──SendMessage──→ auditor
                             "Сгенерируй аудиты для 48 компаний из leads.json"

auditor     ──SendMessage──→ reviewer
                             "Готово: 48 аудитов + PDF. Проверь качество текстов и цифр"

reviewer    ──SendMessage──→ coordinator
                             "Auditor проверен. Все цифры корректны. Можно отправлять"

coordinator ──SendMessage──→ sender
                             "Отправь аудиты: 48 компаний. Каналы: TG + email"

sender      ──SendMessage──→ reviewer
                             "Отправлено: 32 в ТГ, 16 на email. Проверь логику дедупликации"

reviewer    ──SendMessage──→ coordinator
                             "Sender проверен. Дублей нет. Всё корректно"

coordinator ──→ Финальный отчёт в outputs/report.md
```

## Структура agent-runtime/

```
agent-runtime/
├── shared/
│   ├── leads.json              # Компании от parser
│   ├── leads-enriched.json     # Обогащённые данные (Brave, KW, Maps)
│   ├── audits.json             # Сгенерированные аудиты
│   ├── outreach.json           # Статусы рассылки
│   ├── parser-log.md           # Лог parser
│   ├── audit-log.md            # Лог auditor
│   └── sender-log.md           # Лог sender
├── state/
│   ├── plan.md                 # План работы (от coordinator)
│   └── status.md               # Текущий статус каждого этапа
├── messages/                   # Handoff-сообщения между агентами
└── outputs/
    ├── report.md               # Финальный отчёт (от coordinator)
    ├── review-report.md        # Отчёт ревьюера
    └── pdfs/                   # PDF-аудиты для каждой компании
```

## Формула аудита — 5 ударов по болевым точкам

1. **«Вы невидимы»** — данные Brave Search, позиция в выдаче
2. **«Конкуренты забирают ваших клиентов»** — имена конкурентов с сайтами, их трафик
3. **«Вот сколько вы теряете каждый день»** — расчёт в рублях на основе объёма запросов и среднего чека
4. **«Ваш домен ещё свободен»** — NetworkCalc WHOIS проверка
5. **«Репутация работает против вас»** — рейтинги на картах, где видны отзывы, где нет

+ **Блок решений** — что мы сделаем (без цен)

## Сообщение при отправке

```
Добрый день! Мы — digital-агентство, провели
экспресс-аудит цифрового присутствия вашей
компании «{name}».

📎 Аудит во вложении.

Если сомневаетесь в файле — не открывайте,
это нормально. Напишите, и мы отправим
всё в текстовом формате.
```

## Технические зависимости

| Компонент | Назначение |
|-----------|-----------|
| Playwright | Парсинг 2ГИС |
| Brave Search MCP | Проверка видимости в поиске |
| Keywords Everywhere MCP | Объём запросов, стоимость клика |
| Google Maps MCP | Рейтинг, отзывы |
| NetworkCalc MCP | WHOIS/DNS проверка доменов |
| Claude API | Генерация персонализированных аудитов |
| Gen-PDF MCP | Генерация PDF-отчётов |
| Telethon | Поиск ТГ по номеру + отправка сообщений |
| SMTP (Яндекс/Mail.ru) | Email-рассылка |
| SQLite | База лидов + outreach трекинг |

## Существующая база

- 644 компании из 8 городов (Тверь, Рязань, Тула, Ижевск, Барнаул, Омск, Тюмень, Воронеж)
- 9 категорий: Шиномонтаж, Ателье, Ремонт обуви, Ремонт телефонов, Массаж, Фотограф, Грузоперевозки, Клининг, Автосервис
- 7 городов ещё не спарсены: Казань, Самара, Уфа, Челябинск, Красноярск, Новосибирск, Екатеринбург

## Нишевые статистики (заготовленные средние)

```json
{
  "Шиномонтаж": {"traffic_loss": "30-40%", "mobile_search": "73%", "avg_check": 2500, "conversion": "5-8%"},
  "Клининг": {"traffic_loss": "45-55%", "mobile_search": "81%", "avg_check": 4000, "conversion": "8-12%"},
  "Ателье": {"traffic_loss": "35-45%", "mobile_search": "68%", "avg_check": 3000, "conversion": "6-10%"},
  "Ремонт обуви": {"traffic_loss": "25-35%", "mobile_search": "71%", "avg_check": 1500, "conversion": "7-10%"},
  "Ремонт телефонов": {"traffic_loss": "50-60%", "mobile_search": "85%", "avg_check": 3500, "conversion": "10-15%"},
  "Массаж": {"traffic_loss": "40-50%", "mobile_search": "77%", "avg_check": 3000, "conversion": "8-12%"},
  "Фотограф": {"traffic_loss": "55-65%", "mobile_search": "62%", "avg_check": 5000, "conversion": "5-8%"},
  "Грузоперевозки": {"traffic_loss": "35-45%", "mobile_search": "58%", "avg_check": 8000, "conversion": "4-7%"},
  "Автосервис": {"traffic_loss": "40-50%", "mobile_search": "76%", "avg_check": 5000, "conversion": "6-9%"}
}
```
