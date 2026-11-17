# Шаблоны карточек (`wiki/_templates/`)

Скелеты для новых статей. Скопируйте нужный файл в целевую папку и замените заглушки.

| Шаблон | Куда копировать | `type` |
| :--- | :--- | :--- |
| [`character.md`](character.md) | `wiki/characters/` | `character` |
| [`location.md`](location.md) | `wiki/locations/` | `location` |
| [`cultivation.md`](cultivation.md) | `wiki/cultivation/` | `cultivation` |
| [`general_entity.md`](general_entity.md) | `wiki/factions/`, `wiki/artifacts/`, `wiki/concepts/` | `faction` / `artifact` / `concept` |
| [`idiom_allusion.md`](idiom_allusion.md) | `wiki/idioms/` | `idiom` |

> **Ссылки-заглушки.** Внутри шаблонов стоят адреса вида `other-slug.md`,
> `../locations/location-slug.md`, `README.md`. Они **намеренно** не разрешаются:
> шаблон лежит в `_templates/`, а ссылки рассчитаны на будущее место файла.
> Поэтому `scripts/wiki_tool.py check` пропускает папку `_templates` целиком.

## Порядок работы с шаблоном

1. Скопировать файл в нужную папку, переименовать в `slug.md` (латиница, дефисы).
2. Заменить `id`, `zh`, `ru`, `first_chapter`. Без них `build` упадёт с внятной ошибкой.
3. Заполнить `## Правила перевода` — это главный раздел, ради него существует вики.
4. Заменить все заглушки на реальные ссылки. Минимум три.
5. `python3 scripts/wiki_tool.py build && python3 scripts/wiki_tool.py check`.

Полная схема полей — [wiki/README.md](../README.md).
