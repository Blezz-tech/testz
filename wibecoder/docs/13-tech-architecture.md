# 13. Техническая архитектура

## 13.1. Стек

| Слой | Выбор | Альтернатива |
| :--- | :--- | :--- |
| Движок | **Unity 2022 LTS (URP, 2D)** | Godot 4.x (если хотим лёгкий билд и FOSS) |
| Язык | C# | GDScript |
| Анимации | DOTween | LeanTween |
| Аудио | FMOD Studio | Unity Audio + crossfade-контроллер |
| Конфиги баланса | ScriptableObjects + JSON-экспорт | Google Sheets → JSON пайплайн |
| Аналитика | Firebase Analytics / GameAnalytics | — |
| Облако (опц.) | PlayFab / Steam Cloud | — |
| Сборка | Addressables (ассеты), IL2CPP (мобилки) | — |

## 13.2. Структура проекта

```
Assets/
  _Project/
    Scripts/
      Core/          # GameClock, GameManager, SaveSystem, EventBus
      Orders/        # OrderSystem, OrderGenerator, ReputationSystem
      Generation/    # GenerationSystem, ModelRegistry, TokenSystem, BugSystem
      Quality/       # QualitySystem, ProjectState
      Economy/       # EconomySystem, BillsSystem, CreditSystem, MarketSystem
      Stats/         # StatsSystem (health/energy/mood/stress)
      Events/        # EventManager, EventDefs
      Hardware/      # HardwareSystem, VramSystem, PowerSystem
      Skills/        # SkillSystem, PerkSystem
      UI/            # Скрипты экранов, виджеты
    Data/            # JSON-конфиги (см. §13.5)
    Art/ Audio/ Prefabs/ Scenes/
```

## 13.3. Модули и связи

```
                    ┌──────────────┐
                    │  GameManager │
                    └──────┬───────┘
     ┌─────────────┬───────┴────────┬─────────────┐
     ▼             ▼                ▼             ▼
 OrderSystem  GenerationSystem  EconomySystem  EventManager
     │             │                │             │
     ▼             ▼                ▼             ▼
 QualitySystem  ModelRegistry   BillsSystem   StatsSystem
     │          TokenSystem     CreditSystem      │
     ▼          BugSystem       MarketSystem      ▼
 Reputation    HardwareSystem ─────────────► SkillSystem
               VramSystem
```

Правила:

- Системы общаются через **EventBus** (сигналы: `OrderAccepted`, `GenerationDone`, `BillPaid`, `EventTriggered`...), без прямых ссылок UI→Gameplay.
- Все числа баланса — из `Data/*.json` (см. [14](14-balance.md)), **не хардкод**.
- UI подписывается на события; отдельный слой `UIPresenter` мапит состояние в виджеты.

## 13.4. GameClock (игровое время)

```csharp
// 1 игровой день = 8 минут реального времени (настраивается: 5–15 мин)
// Соотношение: 1 игровой час = 20 сек реального времени
class GameClock {
    float realSecondsPerGameDay = 480f;
    int day; float hour;           // day++, hour 0..24
    void Tick(float dt);           // час += dt / (realSecondsPerGameDay/24)
    event Action OnNightTick;      // счета, ивенты, биржа (см. [09] §9.6)
}
```

- Ускорение времени (x2/x3) — только для «ждунов» (локальная генерация), не для активных действий.
- Offline: при загрузке считаем `elapsed_real_time` (макс. 8 игровых часов): ночники, счётчики, фоновая генерация.

## 13.5. Схемы данных (JSON)

### OrderDef (шаблон заказа)

```json
{
  "id": "order_cat_puzzle_t1",
  "tier": 1,
  "type": "game",
  "name": "Пазл про котиков",
  "flavor": "«А можно как в рекламе, но без рекламы»",
  "base_price": 45,
  "deadline_days": 3,
  "requirements": { "total": 25, "sub": { "music": 15 } },
  "preview_art": "previews/cat_puzzle.png",
  "complexity": 1.0
}
```

### AiModelDef

```json
{
  "id": "L0",
  "name": "Лампочка-0.5",
  "kind": "api",
  "qm": 8,
  "consumption": 0.7,
  "token_price_per_1k": 0.10,
  "error_rate": 0.35,
  "vram_need_gb": 0,
  "access": "start"
}
```

### EventDef

```json
{
  "id": "E01",
  "name": "Поранился",
  "type": "instant",
  "weight_group": "health",
  "conditions": {},
  "effects": { "health": -18, "energy": -10 },
  "cooldown_days": 3,
  "text": "Ты порезался о разбитую мышь. Да, об мышь."
}
```

### SaveGame (фрагмент)

```json
{
  "version": 3,
  "day": 14, "hour": 21.5,
  "money": 812.4, "tokens": 15200, "reputation": 27,
  "stats": { "health": 64, "energy": 51, "mood": 70, "stress": 33 },
  "life_level": 2,
  "skills": { "promptsmithing": 7, "debug": 3, "negotiation": 2, "hardware": 1, "survival": 0 },
  "owned_models": ["L0", "L2", "H0"],
  "active_model": "H0",
  "hardware": { "gpu": "H0", "vram_used_gb": 3.1, "wear": 42, "ups": false },
  "active_project": { "order_id": "order_shop_t2", "q": {"graphics": 31, "gameplay": 44}, "bugs_left": 2 },
  "market": { "token_price_mult": 1.05, "tariff": 0.05 },
  "history": { "orders_done": 23, "golden": 4, "events_log": ["E01@9", "E13@12"] }
}
```

## 13.6. Сохранения

- Формат: JSON (+опционально AES-обфускация от читов), 3 слота + автосейв на ночном тике.
- **Версионирование схемы** (`version`) + миграции (напр., v2→v3 добавляет `market`).
- Облако: Steam Cloud / Play Games (конфликт — «какой сейв новее»).

## 13.7. Производительность и бюджет

| Метрика | Цель |
| :--- | :--- |
| FPS | 60 на средних мобилах (Snapdragon 6xx / A12) |
| Память | ≤ 400 МБ RAM, аудио ≤ 30 МБ |
| Размер билда | ≤ 150 МБ (Android), ассеты через Addressables |
| Время холодного старта | ≤ 5 сек |
| UI | пулы объектов, один Canvas на экран, без аллокаций в кадре |

Особое место — **экран генерации**: много анимаций + лог; лог печатается батчами (не по символу в кадр), полоски — материальные шейдеры, не Instantiate в цикле.

## 13.8. Логирование и аналитика

События (минимум): `order_taken`, `order_completed{tier,quality,payout}`, `tokens_spent{model,subsystem}`, `model_rented`, `gpu_bought`, `bill_paid{kind}`, `event_fired{id,choice}`, `credit_taken`, `game_over{kind}`, `session_start`.

- Фаннел туториала (5 шагов), когорты по D1/D7, ARPDAU по источникам.
- Внутренний **баланс-симулятор** (§13.9) гоняет те же формулы без UI.

## 13.9. Тесты

| Тип | Что покрываем |
| :--- | :--- |
| EditMode-юниты | Формулы качества (§[04](04-generation-ai.md)), оплаты (§[05](05-quality.md)), счетов (§[07](07-economy.md)) — фиксированные кейсы из [14](14-balance.md) |
| Симулятор баланса | Headless-прогон 30 игровых дней «типичным игроком» (скрипты поведения), лог траектории денег/качества |
| Смоук | Загрузка всех JSON-конфигов, миграции сейвов |
| Устройства | Список 10 референсных девайсов, чек-лист FPS/памяти |

## 13.10. Локализация

- Все строки — в таблицах (CSV/JSON + Unity Localization или свой парсер).
- RU/EN на старте; тексты юмора переводятся адаптацией (не дословно).
- Числа/валюта: форматтер `$1.2k`, `₽`-подстановка в RU.

## 13.11. Риски и как их гасим

| Риск | Гашение |
| :--- | :--- |
| Баланс «перекошен» на токенах | Симулятор + сейв-флаги коррекции («обновление рынка») |
| Много текста юмора — сложно локализовать | Отдельный контент-пайплайн, лимит 2 строки на карточку |
| Экран генерации лагает на слабых телефонах | Профиль «Low FX», пул лога, лимит частиц |
| Игроки читают сейв | Обфускация + серверная валидация только для ИАП-покупок |
