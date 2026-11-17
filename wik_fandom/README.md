# Sword of Coming — Полное зеркало Fandom-вики (`wik_fandom/`)

Эта папка содержит полный, верифицированный перенос англоязычной энциклопедии **[Sword of Coming Wiki](https://sword-of-coming.fandom.com/wiki/Special:AllPages)** (*Jian Lai* / *Unsheathed* / 剑来) по всем пространствам имён MediaWiki (**624** страницы в `statistics.pages`) в чистый Markdown с сохранением всех многовкладочных портретов и постеров (`<gallery>`), карточек персонажей (Infoboxes), таблиц техник и боёв, нумерованных сносок (`## References`), блог-постов, правил вики, дерева навигации, категорий, редиректов и полного манифеста медиафайлов.

> **Важно:** папка `wik_fandom/` полностью отделена от внутреннего русскоязычного глоссария перевода в `wiki/`.

## Состав и полная сверка по `Special:AllPages` (все 624 страницы)

| Пространство имён | Название | На Fandom | Статус и расположение в `wik_fandom/` |
| ---: | :--- | ---: | :--- |
| `ns=0` | Основное `(Main)` | **183** | **183/183** в [`pages/`](pages/) (`163` статьи, включая `13` вложенных подстраниц + `20` `#REDIRECT`) |
| `ns=2` | Участники `User` | **1** | **1/1** в [`project/User_RyuSeongX.md`](project/User_RyuSeongX.md) |
| `ns=4` | Проект `Sword of Coming Wiki` | **1** | **1/1** в [`project/Wiki_rules.md`](project/Wiki_rules.md) (`Wiki rules`) |
| `ns=6` | Файлы `File` (`allimages`) | **246** | **245/245** загруженных файлов (`222` изображения + `23` YouTube-превью) в [`GALLERY.md`](GALLERY.md) и [`images_manifest.json`](images_manifest.json) (`+1` пустая стартовая заглушка Fandom `File:Example.jpg`) |
| `ns=8` | Системное `MediaWiki` | **14** | Официальное дерево навигации и мета-описание в [`project/Wiki_navigation.md`](project/Wiki_navigation.md) и [`project/Wiki_description_site_meta.md`](project/Wiki_description_site_meta.md) (`+12` дефолтных CSS/системных сообщений движка) |
| `ns=10` | Шаблоны `Template` | **132** | **22** контентных и портальных шаблона сохранены в [`_raw/fandom_dump.json`](_raw/fandom_dump.json) и раскрыты прямо внутри статей (`+110` дефолтных стартовых шаблонов/документации Fandom) |
| `ns=14` | Категории `Category` | **25** | **25/25** явных категорий + **22** неявные статейные категории (`Alive`, `Dead`, `Male`, `Female`, `Human` и др.) = **47** индексных страниц в [`categories/`](categories/) |
| `ns=500` | Блоги `User blog` | **3** | **3/3** блог-поста в [`blogs/`](blogs/) (`Season 2 Announcement`, `The First Theatrical Spoiler`, `Season 2 Officially Released`) |
| `ns=502` | Лента `Blog` | **1** | Автолента `Blog:Recent posts` представлена в [`categories/Blog_posts.md`](categories/Blog_posts.md) |
| `ns=828` | Модули `Module` | **18** | Стандартные Scribunto Lua-модули Fandom (`Navbox`, `MessageBox` и др.) обрабатываются напрямую генератором `scripts/build_fandom_wiki.py` |
| **Итого** | **`statistics.pages`** | **624** | **100% учтено и перенесено (`239` сгенерированных `.md` файлов + `README.md` + `images_manifest.json` + `_raw/fandom_dump.json`)** |

Быстрый вход в энциклопедию:
- **[`INDEX.md`](INDEX.md)** — полный тематический и алфавитный указатель всех статей, подстраниц, блогов, правил, категорий и редиректов.
- **[`pages/Sword_of_Coming_Wiki.md`](pages/Sword_of_Coming_Wiki.md)** — заглавная страница вики (с раскрытыми слайдерами, синопсисом, 4 карточками героев, превью эпизодов 1–2 сезонов, трейлерами, 32 постерами и 4 обложками).
- **[`project/Wiki_navigation.md`](project/Wiki_navigation.md)** — курируемое меню навигации вики по персонажам, фракциям, мирам, локациям, дунхуа и новелле.
- **[`GALLERY.md`](GALLERY.md)** — сводный каталог всех 245 изображений и видео-превью с размерами и ссылками на использующие их страницы (`174` файла задействованы напрямую в статьях и блогах, `71` — неиспользуемые загрузки на самом Fandom).

---

## Работа с изображениями (онлайн CDN и 100% офлайн-режим)

По умолчанию в сгенерированных `.md` файлах встроены прямые ссылки на официальный CDN Fandom (`https://static.wikia.nocookie.net/sword-of-coming/images/...`), поэтому при просмотре на GitHub, в VS Code или любом браузере с доступом в интернет **все портреты, постеры и превью серий отображаются сразу без раздувания Git-репозитория бинарными файлами** (~145 МБ оригинальных PNG/JPG/WebP).

В файле **[`images_manifest.json`](images_manifest.json)** сохранены точные прямые ссылки, размеры, разрешения, MIME-типы и контрольные суммы **SHA-1** для всех **245** файлов.

### Скачать все 245 картинок локально и переключить ссылки на офлайн-пути

На любой машине с прямым выходом в интернет выполните из корня репозитория:

```bash
# Скачать все 245 файлов в wik_fandom/images/ с проверкой SHA-1
# и переписать ссылки во всех .md на локальные относительные пути images/...
python3 scripts/download_fandom_images.py --workers 8 --rewrite-local
```

Дополнительные команды:
```bash
# Проверить целостность уже скачанных локальных файлов по SHA-1:
python3 scripts/download_fandom_images.py --verify-only

# Вернуть в Markdown прямые ссылки на Fandom CDN:
python3 scripts/download_fandom_images.py --restore-cdn
```

---

## Повторная сборка и синхронизация со свежей версией Fandom

1. **Пересобрать Markdown из сохранённого дампа (`_raw/fandom_dump.json`)** (работает полностью офлайн + проверяет отсутствие битых относительных ссылок):
   ```bash
   python3 scripts/build_fandom_wiki.py
   ```
2. **Подтянуть свежие правки напрямую с `sword-of-coming.fandom.com/api.php`** (включая `ns=0, 2, 4, 8, 10, 14, 500`), пересобрать вики и скачать новые картинки:
   ```bash
   python3 scripts/sync_fandom_wiki.py --build --download-images --rewrite-local
   ```
