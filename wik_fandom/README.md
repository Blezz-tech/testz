# Sword of Coming — Полное зеркало Fandom-вики (`wik_fandom/`)

Эта папка содержит полный перенос англоязычной энциклопедии **[Sword of Coming Wiki](https://sword-of-coming.fandom.com/wiki/Special:AllPages)** (*Jian Lai* / *Unsheathed* / 剑来) в чистый Markdown с сохранением всех карточек персонажей (Infoboxes), таблиц, подстраниц, категорий, редиректов и полного манифеста медиафайлов.

> **Важно:** папка `wik_fandom/` полностью отделена от внутреннего проектного глоссария перевода в `wiki/`.

## Состав и статистика (`Special:AllPages` + `allimages`)

| Раздел | Кол-во | Путь |
| :--- | ---: | :--- |
| Все страницы основного пространства (`ns=0`) | **183** | [`pages/`](pages/) |
| — полноценные статьи (включая 13 вложенных подстраниц) | **163** | `pages/*.md`, `pages/Chen_Ping_An/`, `pages/Ning_Yao/`, `pages/Donghua/`, `pages/Light_Novel/` |
| — страницы-перенаправления (`#REDIRECT`) | **20** | `pages/*.md` (все ссылки внутри статей автоматически ведут на целевые страницы/разделы) |
| Категории (включая шаблонные категории `prop=categories`) | **47** | [`categories/`](categories/) |
| Медиафайлы Fandom (`list=allimages`: 222 изображения + 23 видео-превью) | **245** | [`images_manifest.json`](images_manifest.json) · [`GALLERY.md`](GALLERY.md) |
| Исходный снимок MediaWiki API (wikitext + шаблоны + метаданные) | **1** | [`_raw/fandom_dump.json`](_raw/fandom_dump.json) |

Быстрый вход в энциклопедию:
- **[`INDEX.md`](INDEX.md)** — полный тематический и алфавитный указатель всех 183 страниц и 47 категорий.
- **[`pages/Sword_of_Coming_Wiki.md`](pages/Sword_of_Coming_Wiki.md)** — заглавная страница вики (с раскрытыми шаблонами, постерами, трейлерами и карточками героев).
- **[`GALLERY.md`](GALLERY.md)** — сводный каталог всех 245 изображений и видео-превью с размерами и привязкой к статьям.

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

*(Примечание: `wik_fandom/images/` можно добавить в `.gitignore` или закоммитить по желанию, в зависимости от того, нужно ли хранить ~145 МБ бинарных картинок прямо в Git).*

---

## Повторная сборка и синхронизация со свежей версией Fandom

1. **Пересобрать Markdown из сохранённого дампа (`_raw/fandom_dump.json`)** (работает полностью офлайн + проверяет отсутствие битых относительных ссылок):
   ```bash
   python3 scripts/build_fandom_wiki.py
   ```
2. **Подтянуть свежие правки напрямую с `sword-of-coming.fandom.com/api.php`**, пересобрать вики и скачать новые картинки:
   ```bash
   python3 scripts/sync_fandom_wiki.py --build --download-images --rewrite-local
   ```
