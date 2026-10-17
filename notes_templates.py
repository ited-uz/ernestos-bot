"""Note templates: what a new note starts with.

A template is app text, so it follows the interface language (uz/en/ru).
What the person then writes is theirs and is never translated.

`{date}` is the person's local date, filled in when the note is made.
"""
from __future__ import annotations

#: key -> (note kind, icon)
META = {
    "blank":    ("note", "📝"),
    "study":    ("study", "🎓"),
    "learning": ("study", "📚"),
    "thought":  ("thought", "💭"),
    "idea":     ("idea", "💡"),
    "resource": ("resource", "🔗"),
    "project":  ("note", "📁"),
    "daily":    ("note", "📅"),
    "weekly":   ("note", "🗓"),
    "decision": ("thought", "⚖️"),
    "person":   ("note", "👤"),
    "ai_tool":  ("resource", "🤖"),
    "vocab":    ("study", "🔤"),
}

ORDER = list(META)

_T = {
    "en": {
        "blank": ("", ""),
        "study": ("Lecture — ", "## Course / topic\n\n## Key ideas\n- \n\n## Definitions\n- \n\n"
                  "## Examples\n- \n\n## Questions to ask\n- \n\n## Summary in my words\n\n"),
        "learning": ("Learning — ", "## What I want to learn\n\n## Source\n\n## Notes\n- \n\n"
                     "## How I will use it\n\n## Review on\n{date}\n"),
        "thought": ("", "## Thought\n\n## Why it matters\n\n## What follows\n"),
        "idea": ("Idea — ", "## The idea\n\n## Problem it solves\n\n## Who pays\n\n"
                 "## First test (cheap, this week)\n- [ ] \n\n## Risks\n- \n"),
        "resource": ("", "## Link\n\n## Why save it\n\n## Key points\n- \n"),
        "project": ("Project context — ", "## Goal\n\n## Current state\n\n## Decisions so far\n- \n\n"
                    "## Open questions\n- \n\n## Next actions\n- [ ] \n"),
        "daily": ("Daily — {date}", "## Top 3\n- [ ] \n- [ ] \n- [ ] \n\n## Notes\n\n"
                  "## Wins\n- \n\n## Tomorrow\n- \n"),
        "weekly": ("Weekly review — {date}", "## What went well\n- \n\n## What did not\n- \n\n"
                   "## Numbers\n- \n\n## Lessons\n- \n\n## Next week focus\n- [ ] \n"),
        "decision": ("Decision — ", "## Decision\n\n## Context\n\n## Options\n1. \n2. \n\n"
                     "## Chosen and why\n\n## Revisit on\n{date}\n"),
        "person": ("", "## Who\n\n## How we met\n\n## What matters to them\n- \n\n"
                   "## Follow-ups\n- [ ] \n"),
        "ai_tool": ("AI tool — ", "## Tool\n\n## Link\n\n## What it does best\n- \n\n"
                    "## Prompt that worked\n\n```\n\n```\n\n## Price / limits\n"),
        "vocab": ("Vocabulary — ", "| Word | Meaning | Example |\n|---|---|---|\n|  |  |  |\n"),
    },
    "uz": {
        "blank": ("", ""),
        "study": ("Ma’ruza — ", "## Fan / mavzu\n\n## Asosiy g‘oyalar\n- \n\n## Ta’riflar\n- \n\n"
                  "## Misollar\n- \n\n## Savollar\n- \n\n## O‘z so‘zlarim bilan xulosa\n\n"),
        "learning": ("O‘rganish — ", "## Nimani o‘rganmoqchiman\n\n## Manba\n\n## Qaydlar\n- \n\n"
                     "## Qanday qo‘llayman\n\n## Takrorlash sanasi\n{date}\n"),
        "thought": ("", "## Fikr\n\n## Nega muhim\n\n## Bundan nima kelib chiqadi\n"),
        "idea": ("G‘oya — ", "## G‘oya\n\n## Qaysi muammoni yechadi\n\n## Kim to‘laydi\n\n"
                 "## Birinchi sinov (arzon, shu hafta)\n- [ ] \n\n## Xavflar\n- \n"),
        "resource": ("", "## Havola\n\n## Nega saqladim\n\n## Asosiy fikrlar\n- \n"),
        "project": ("Loyiha konteksti — ", "## Maqsad\n\n## Hozirgi holat\n\n## Qabul qilingan qarorlar\n- \n\n"
                    "## Ochiq savollar\n- \n\n## Keyingi qadamlar\n- [ ] \n"),
        "daily": ("Kundalik — {date}", "## Bugungi 3 ta asosiy ish\n- [ ] \n- [ ] \n- [ ] \n\n## Qaydlar\n\n"
                  "## Yutuqlar\n- \n\n## Ertaga\n- \n"),
        "weekly": ("Haftalik tahlil — {date}", "## Nima yaxshi bo‘ldi\n- \n\n## Nima bo‘lmadi\n- \n\n"
                   "## Raqamlar\n- \n\n## Saboqlar\n- \n\n## Keyingi hafta fokusi\n- [ ] \n"),
        "decision": ("Qaror — ", "## Qaror\n\n## Kontekst\n\n## Variantlar\n1. \n2. \n\n"
                     "## Tanlov va sababi\n\n## Qayta ko‘rib chiqish sanasi\n{date}\n"),
        "person": ("", "## Kim\n\n## Qanday tanishdik\n\n## U uchun nima muhim\n- \n\n"
                   "## Keyingi qadamlar\n- [ ] \n"),
        "ai_tool": ("AI vosita — ", "## Vosita\n\n## Havola\n\n## Eng yaxshi qiladigan ishi\n- \n\n"
                    "## Ishlagan prompt\n\n```\n\n```\n\n## Narx / limitlar\n"),
        "vocab": ("Lug‘at — ", "| So‘z | Ma’nosi | Misol |\n|---|---|---|\n|  |  |  |\n"),
    },
    "ru": {
        "blank": ("", ""),
        "study": ("Лекция — ", "## Предмет / тема\n\n## Ключевые идеи\n- \n\n## Определения\n- \n\n"
                  "## Примеры\n- \n\n## Вопросы\n- \n\n## Вывод своими словами\n\n"),
        "learning": ("Изучение — ", "## Что хочу изучить\n\n## Источник\n\n## Заметки\n- \n\n"
                     "## Как применю\n\n## Повторить\n{date}\n"),
        "thought": ("", "## Мысль\n\n## Почему важно\n\n## Что из этого следует\n"),
        "idea": ("Идея — ", "## Идея\n\n## Какую проблему решает\n\n## Кто платит\n\n"
                 "## Первый тест (дёшево, на этой неделе)\n- [ ] \n\n## Риски\n- \n"),
        "resource": ("", "## Ссылка\n\n## Зачем сохранил\n\n## Главное\n- \n"),
        "project": ("Контекст проекта — ", "## Цель\n\n## Текущее состояние\n\n## Принятые решения\n- \n\n"
                    "## Открытые вопросы\n- \n\n## Следующие шаги\n- [ ] \n"),
        "daily": ("Дневник — {date}", "## Топ-3 на сегодня\n- [ ] \n- [ ] \n- [ ] \n\n## Заметки\n\n"
                  "## Победы\n- \n\n## Завтра\n- \n"),
        "weekly": ("Обзор недели — {date}", "## Что получилось\n- \n\n## Что нет\n- \n\n"
                   "## Цифры\n- \n\n## Уроки\n- \n\n## Фокус следующей недели\n- [ ] \n"),
        "decision": ("Решение — ", "## Решение\n\n## Контекст\n\n## Варианты\n1. \n2. \n\n"
                     "## Выбор и почему\n\n## Пересмотреть\n{date}\n"),
        "person": ("", "## Кто\n\n## Как познакомились\n\n## Что для него важно\n- \n\n"
                   "## Дальнейшие шаги\n- [ ] \n"),
        "ai_tool": ("AI-инструмент — ", "## Инструмент\n\n## Ссылка\n\n## Что делает лучше всего\n- \n\n"
                    "## Рабочий промпт\n\n```\n\n```\n\n## Цена / лимиты\n"),
        "vocab": ("Словарь — ", "| Слово | Значение | Пример |\n|---|---|---|\n|  |  |  |\n"),
    },
}


def render(key: str, lang: str, day) -> tuple[str, str, str]:
    """(title, body, kind) for a new note from template `key`."""
    if key not in META:
        key = "blank"
    table = _T.get(lang) or _T["en"]
    title, body = table[key]
    stamp = day.isoformat()
    return title.replace("{date}", stamp), body.replace("{date}", stamp), META[key][0]


def listing(lang: str, day=None) -> list[dict]:
    """Every template; with `day`, the title and body ready to start a note."""
    table = _T.get(lang) or _T["en"]
    out = []
    for k in ORDER:
        row = {"key": k, "kind": META[k][0], "icon": META[k][1], "preview": table[k][1][:160]}
        if day is not None:
            row["title"], row["body"], _ = render(k, lang, day)
        out.append(row)
    return out
