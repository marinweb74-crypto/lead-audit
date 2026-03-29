# LeadAudit — AI-система глубокого аудита бизнеса и персонализированной рассылки

Мультиагентная система для парсинга бизнесов из 2ГИС, проведения глубокого аудита цифрового присутствия каждой компании и персонализированной рассылки через Telegram и Email.

## Обязательный режим запуска

Запуск через `tmux` обязателен. Все агенты работают в split-pane режиме.

## Обязательные настройки проекта

- в `.claude/settings.json` должен быть включен `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`
- в `.claude/settings.json` должен быть установлен `teammateMode: "tmux"`

## Назначение проекта

Pipeline из 5 агентов для автоматизированного digital-аудита бизнесов:

- `coordinator`: lead-агент, управляет pipeline, контролирует качество, выдаёт итоговый отчёт
- `parser`: парсит 2ГИС + обогащает данные через MCP (Brave Search, Keywords Everywhere, Google Maps, NetworkCalc)
- `auditor`: генерирует персонализированный аудит для каждой компании через Claude API + PDF
- `sender`: отправляет аудиты через Telegram (Telethon) и Email (SMTP)
- `reviewer`: проверяет код каждого агента на каждой стадии, ловит баги до продакшна

Роли агентов лежат в `.claude/agents/`.

## Pipeline

```
coordinator → parser → auditor → sender → coordinator (итог)
                ↑                    ↑
             reviewer            reviewer
        (проверка после      (проверка после
         каждого этапа)       каждого этапа)
```

1. coordinator получает задание (города, категории, лимиты)
2. parser собирает компании из 2ГИС + обогащает данными из MCP → `shared/leads.json`
3. reviewer проверяет код parser и данные
4. auditor генерирует персональные аудиты → `shared/audits.json` + PDF-файлы
5. reviewer проверяет код auditor и качество аудитов
6. sender отправляет аудиты в Telegram/Email → `shared/outreach.json`
7. reviewer проверяет код sender и логику дедупликации
8. coordinator собирает итоговый отчёт → `outputs/report.md`

## Обязательная структура работы

- Общие рабочие файлы: `agent-runtime/shared/`
- Сообщения между агентами: `agent-runtime/messages/`
- План и статусы: `agent-runtime/state/`
- Финальные результаты: `agent-runtime/outputs/`

Каждый агент создаёт артефакт и отправляет handoff через SendMessage.

## Правила качества

- Ни одна строка кода не идёт в продакшн без проверки reviewer
- Все цифры в аудитах должны быть обоснованы реальными данными
- Максимум 3 касания на одного лида
- Дедупликация обязательна: по телефону, email, source_id
- Токены и ключи — только в config.json, никогда в коде

## Существующая база

В проекте `C:\Users\minli\Desktop\lead-finder\leads.db` уже 644 компании из 8 городов.
Новые лиды проверяются на дубли с этой базой.

## Рекомендуемый порядок запуска

1. Открыть tmux:
```bash
tmux new -s leadaudit
```

2. Запустить Claude Code внутри tmux:
```bash
cd /mnt/c/VS\ Studio/lead-audit && claude
```

3. Дать lead-инструкцию:
```text
Создай Agent Team для аудита бизнесов.
Используй роли coordinator, parser, auditor, sender и reviewer.
Города: Казань, Самара. Категории: Шиномонтаж, Клининг.
```
