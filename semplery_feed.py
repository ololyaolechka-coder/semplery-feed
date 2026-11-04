#!/usr/bin/env python3
"""
Генератор YML-фида для semplery.ru без доступа к админке.

Обходит разделы каталога (с пагинацией ?PAGEN_1=N), собирает ссылки на товары,
заходит в каждую карточку и берёт данные из блока #catalogDetailData
(там лежат все размеры-предложения с ценой, наличием и фото).

Один размер = один <offer>, размеры одной модели связаны через group_id.

Установка:  pip install requests beautifulsoup4 lxml
Запуск:     python semplery_feed.py
Результат:  semplery_feed.xml рядом со скриптом
"""

import re
import sys
import time
import html
import random
from datetime import datetime
from urllib.parse import urljoin

try:
    import requests
    from bs4 import BeautifulSoup
    import lxml  # noqa: F401
except ImportError:
    # Библиотек нет — ставим сами (без прав администратора, в профиль пользователя)
    import subprocess
    print("Устанавливаю библиотеки, это один раз...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--user",
                           "requests", "beautifulsoup4", "lxml"])
    import site, importlib
    importlib.reload(site)
    sys.path.append(site.getusersitepackages())
    import requests
    from bs4 import BeautifulSoup

BASE = "https://semplery.ru"
import os
OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "semplery_feed.xml")
DELAY = (0.5, 1.0)          # пауза между запросами, сек (чтобы не забанили)
SKIP_UNAVAILABLE = False    # True — не выгружать размеры не в наличии
SKIP_SAMPLES = True         # не выгружать образцы и товары с дефектами
ALWAYS_INCLUDE = {"4053"}   # id товаров (из URL), которые фильтр образцов ошибочно отсекает: Брюки СОН
SALES_NOTES = "Оплата картой онлайн. Доставка СДЭК по России"   # до 50 символов

# Разделы каталога. Порядок = приоритет при выборе категории товара:
# сначала товарные типы, потом подборки/коллекции.
CATEGORIES = [
    ("platya-carafany",          "Платья и сарафаны"),
    ("kostyumy-komlpekty",       "Костюмы и комплекты"),
    ("zhakety-bombery",          "Жакеты и бомберы"),
    ("bluzy-topy",               "Блузы, топы, рубашки"),
    ("futbolki",                 "Футболки, майки, лонгсливы"),
    ("bryuki",                   "Брюки"),
    ("yubki",                    "Юбки и шорты"),
    ("aksessuary",               "Аксессуары"),
    ("sport",                    "Спорт"),
    # подборки — только чтобы не потерять товары, которых нет в типах выше
    ("vechernyaya-kollektsiya",  "Вечерняя коллекция"),
    ("semplery-volchek-",        "SEMPLERY × VOLCHEK"),
    ("osen-26",                  "Осень 26"),
    ("base",                     "База"),
    ("v-otpusk",                 "В отпуск"),
    ("spetsialnoe-predlozhenie", "Специальное предложение"),
    ("",                         "Новинки"),   # /catalog/woman/
]
ROOT_CATEGORY = (1, "Женщины")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

session = requests.Session()
session.headers.update(HEADERS)


def get(url, tries=3):
    for i in range(tries):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                time.sleep(random.uniform(*DELAY))
                return r.text
            print(f"  [{r.status_code}] {url}", file=sys.stderr)
            if r.status_code == 404:
                return None
        except requests.RequestException as e:
            print(f"  [ошибка] {url}: {e}", file=sys.stderr)
        time.sleep(3 * (i + 1))
    return None


def to_int(price_text):
    digits = re.sub(r"\D", "", price_text or "")
    return int(digits) if digits else None


# ---------- 1. Список товаров из разделов ----------

def parse_listing(page_html):
    """Возвращает список URL карточек на странице раздела."""
    soup = BeautifulSoup(page_html, "lxml")
    urls = []
    for a in soup.select("a.b-catalog-element__title[href]"):
        urls.append(urljoin(BASE, a["href"].split("?")[0]))
    return urls


def crawl_section(slug):
    section_url = f"{BASE}/catalog/woman/{slug + '/' if slug else ''}"
    found, page = [], 1
    while True:
        url = section_url if page == 1 else f"{section_url}?PAGEN_1={page}"
        page_html = get(url)
        if not page_html:
            break
        urls = parse_listing(page_html)
        new = [u for u in urls if u not in found]
        if not new:           # Битрикс на «лишней» странице отдаёт последнюю
            break
        found.extend(new)
        page += 1
    return found


# ---------- Правка опечаток в названиях ----------
# Род товара по первому слову: цвет согласуем с ним («Платье черный» -> «Платье черное»)
GENDER_RULES = [
    (r"^(платье|платье-\S+)\b", "n"),
    (r"^(брюки|шорты|штаны|джинсы|джог+еры|леггинсы|помятые)\b", "pl"),
    (r"^(рубашка|юбка|юбка-\S+|блуза|футболка|майка|сумка|жилетка|тройка|двойка|"
     r"топ-двойка|комбинация|помятая|туника)\b", "f"),
]
ADJ_END = {  # окончание м.р. -> (ж.р., ср.р., мн.ч.)
    "ый": ("ая", "ое", "ые"), "ой": ("ая", "ое", "ые"), "ий": ("яя", "ее", "ие"),
}
HARD_IY = re.compile(r"[гкхжшщч]ий$")  # «легкий» -> «легкая», не «легкяя»


def noun_gender(name):
    n = name.lower().lstrip("«\"")
    for rx, g in GENDER_RULES:
        if re.match(rx, n):
            return g
    return "m"


def agree(word, g):
    low = word.lower()
    if g == "m" or not COLOR_RE.match(low):
        return word
    for end, (f, n, pl) in ADJ_END.items():
        if low.endswith(end):
            if end == "ий" and HARD_IY.search(low):
                f, n, pl = "ая", "ое", "ие"
            new = {"f": f, "n": n, "pl": pl}[g]
            return word[:-2] + new
    return word


def fix_name(name):
    name = " ".join(name.split())
    name = re.sub(r"(?<=[а-яё])\d+$", "", name)          # «бежевый1» -> «бежевый»
    g = noun_gender(name)
    words = name.split()
    # согласуем последнее слово-цвет («серо-бежевый» -> «серо-бежевая»)
    # цвет должен стоять в конце (допускается только «(образец)» после него):
    # «Футболка серый мелаж» не трогаем — там «серый» относится к «мелаж»
    i = len(words) - 1
    while i > 0 and words[i].startswith("(") :
        i -= 1
    for i in [i] if i > 0 else []:
        parts = words[i].split("-")
        if COLOR_RE.match(parts[-1].lower()):
            parts[-1] = agree(parts[-1], g)
            words[i] = "-".join(parts)
            break
    return " ".join(words)


# ---------- 2. Карточка товара ----------

DESC_MAX = 81    # Директ показывает 81 символ текста (знаки препинания не считаются)

PUNCT = set(".,:;!?«»\"'()—–-…")
WEAK_END = {"и", "а", "но", "или", "с", "со", "из", "в", "во", "на", "по", "для", "к", "ко",
            "у", "о", "об", "от", "до", "без", "за", "под", "над", "при", "что", "как",
            "который", "которая", "которое", "которые", "которым", "благодаря", "не"}


def dlen(text):
    """Длина по правилам Директа: без знаков препинания."""
    return sum(1 for ch in text if ch not in PUNCT)


def shorten(text, limit=DESC_MAX):
    """Целые предложения, если влезают; иначе — до запятой или по словам, без «висячих» предлогов."""
    text = re.sub(r"\s*\([^)]*\)", "", text)              # скобки — первыми под нож
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if x.strip()]
    out = ""
    for sent in sentences:
        cand = (out + " " + sent).strip()
        if dlen(cand) <= limit:
            out = cand
        else:
            break
    if out:
        return out
    first = sentences[0] if sentences else text
    # пробуем закончить на запятой/двоеточии
    parts = re.split(r"(?<=[,;:—])\s+", first)
    out = ""
    for part in parts:
        cand = (out + " " + part).strip()
        if dlen(cand) <= limit:
            out = cand
        else:
            break
    if not out or dlen(out) < limit * 0.5:          # слишком коротко — режем по словам
        out = ""
        for w in first.split():
            cand = (out + " " + w).strip()
            if dlen(cand) > limit:
                break
            out = cand
    words = out.rstrip(",;:—– ").split()
    while len(words) > 1 and words[-1].lower().strip(",") in WEAK_END:
        words.pop()
    out = " ".join(words).rstrip(",;:—– ")
    return out if out.endswith((".", "!", "?")) else out + "."


# Пометки про образцы и дефекты — в рекламе не нужны, вырезаем
DEFECT_RE = re.compile(r"проработк|изделие из проработки|образец|образца|неизданн|дефект|деффект|затяжк|"
                       r"зацепк|брак|клея|бирк|без лент|без завязок|ресничек|нет нижней|"
                       r"на поясе \d+ сло|фото\s*№?\s*\d", re.I)


def strip_defects(text):
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if not DEFECT_RE.search(sent):
            out.append(sent)
            continue
        # «Проработка, без дефектов Костюм из …» — пометка без точки перед основным текстом
        for part in re.split(r"(?<=[а-яё)\d])\s+(?=[А-ЯЁ][а-яё])", sent):
            if not DEFECT_RE.search(part):
                out.append(part)
    return " ".join(out).strip()


# Ручные описания для моделей, где первое предложение не влезает в 81 символ.
# Ключ — начало исходного описания на сайте (работает для всех цветов модели).
DESC_OVERRIDES = {
    "Кружевное платье-мини с бахромой":          "Кружевное мини-платье с бахромой из итальянского кружева с цветочным узором.",
    "Контрастная бельевая вставка":              "Платье прямого кроя с бельевой вставкой, тонкими бретелями и высоким разрезом.",
    "Платье макси в бельевом стиле - Отделка":   "Платье макси в бельевом стиле с отделкой кружевом, полуприлегающий крой.",
    "Платье: -Глубокий вырез":                   "Платье миди с глубоким вырезом, драпировкой, подплечниками и поясом.",
    "Платье-комбинация трансформер":             "Платье-комбинация трансформер из 100% вискозы, длина макси, А-силуэт.",
    "Костюм-тройка Марго":                       "Костюм-тройка: удлиненный пиджак-пальто, топ-жилет халтер и брюки оверсайз.",
    "Костюм из приталенного жакета с акцентом":  "Костюм из приталенного жакета и брюк-алладинов с расслабленным силуэтом.",
    "Комплект из двух самостоятельных изделий":  "Комплект: полупрозрачный бадлон с коротким рукавом и бельевой топ на бретелях.",
    "Комбинезон свободного кроя из футера":      "Комбинезон свободного кроя из мягкого футера с акцентом на талии.",
    "Легкий и воздушный комплект из тенсела":    "Комплект из тенсела: укороченная рубашка с рукавами-фонариками и мини-юбка.",
    "Костюм из блузы и штанов":                  "Костюм из укороченной блузы с рукавом 3/4 и свободных штанов на резинке.",
    "Костюм: топ-майка с открытой спинкой":      "Костюм: топ-майка с открытой спинкой и юбка миди на резинке с разрезом.",
    "Эффектный вечерний топ длины в пол":        "Вечерний топ длины в пол, расшитый мерцающими пайетками и бахромой.",
    "Свободный и лаконичный силуэт делают":      "Рубашка свободного силуэта в полоску для повседневных и деловых образов.",
    "Чистые линии, лаконичный силуэт":           "Рубашка свободного силуэта для повседневных и деловых образов.",
    "-Прямой крой -Отложной воротник":           "Рубашка прямого кроя с отложным воротником и накладным карманом на груди.",
    "Образец юбки миди с разрезом":              "Юбка миди с разрезом сзади и талией на резинке.",
    "Микро-сумка Beans":                         "Микро-сумка Beans из коллаборации SEMPLERY X VOLCHEK, закрывается на магниты.",
}


def clean_description(node):
    if node is None:
        return ""
    for t in node.find_all("table"):   # таблица обмеров в фиде не нужна
        t.decompose()
    text = node.get_text("\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    text = re.sub(r"^Арт\.\s*-?\s*\S*\s*", "", text) if text.startswith("Арт") else text
    # только вводный абзац — до характеристик, обмеров и параметров модели
    text = re.split(r"Характеристики|Обмеры изделия|Размер на модели|Параметры модели", text)[0]
    text = " ".join(text.split())
    for prefix, short in DESC_OVERRIDES.items():
        if text.startswith(prefix) or strip_defects(text).startswith(prefix):
            return short
    text = strip_defects(text)
    return shorten(text) if text else ""


# Категория по типу товара из названия (точнее, чем раздел сайта:
# юбка из костюмного раздела уйдёт в «Юбки», товары из «Спецпредложения» — по типам)
TYPE_RULES = [
    (r"^(платье|сарафан)",                                   "Платья и сарафаны"),
    (r"^(костюм|комплект|тройка|двойка|топ-двойка)",         "Костюмы и комплекты"),
    (r"^(жакет|бомбер|анорак|кардиган|жилет|пиджак|пальто|тренч|куртк)", "Жакеты и бомберы"),
    (r"^(блуз|топ|рубашк|комбинация|боди|корсет)",           "Блузы, топы, рубашки"),
    (r"^(футболк|майк|лонгслив|свитшот|худи)",               "Футболки, майки, лонгсливы"),
    (r"^(брюки|штаны|джинсы|джог+еры|комбинезон)",     "Брюки"),
    (r"^(юбк|шорты)",                                        "Юбки и шорты"),
    (r"^(пояс|сумк|платок|шарф|ремень)",                     "Аксессуары"),
]

COLOR_RE = re.compile(
    r"(черн|бел|молочн|ванил|розов|красн|желт|зелен|голуб|син|бежев|коричн|шоколадн|"
    r"сер|графит|оливк|бордов|фиолет|лаванд|мятн|хаки|пудров|айвори|кремов|золот|"
    r"серебр|какао|латте|разноцветн)\w*$", re.I)


def type_category(name):
    n = name.lower().lstrip("«\"")
    n = re.sub(r"^(помят\w*|вязан\w*|льнян\w*)\s+", "", n)   # «Помятые брюки» -> «брюки»
    for rx, cat in TYPE_RULES:
        if re.match(rx, n):
            return cat
    return None


def color_from_name(name):
    for w in reversed(re.sub(r"[()«»\"]", " ", name).split()):
        m = COLOR_RE.search(w)
        if m and m.start() == 0:
            return w.rstrip("0123456789").capitalize()
    return ""


def parse_product(page_html, url):
    soup = BeautifulSoup(page_html, "lxml")
    data = soup.select_one("#catalogDetailData")
    if not data:
        print(f"  нет данных о размерах на странице: {url}", file=sys.stderr)
        return None

    product_id = re.search(r"/(\d+)\.html", url).group(1)
    h1 = soup.select_one(".b-catalog-detail h1, h1")
    name = " ".join(h1.get_text(" ").split()) if h1 else ""

    color_a = soup.select_one(".b-catalog-detail__color_ref a.i-active")
    color = color_a.get("title", "").strip() if color_a else ""

    old_price_node = soup.select_one(".b-catalog-detail__old-price s")
    old_price = to_int(old_price_node.get_text()) if old_price_node else None

    desc_node = soup.select_one('[itemtype="http://schema.org/Product"] [itemprop="description"]')
    raw_desc = " ".join(desc_node.get_text(" ").split()) if desc_node else ""
    raw_desc = re.sub(r"^Арт\.\s*-?\s*\S*\s*", "", raw_desc)
    first_sentence = re.split(r"(?<=[.!?])\s+", raw_desc)[0][:150]
    # образец: «(образец)» в названии или описание начинается с пометки об образце/дефекте
    is_sample = "образец" in name.lower() or bool(DEFECT_RE.search(first_sentence))
    if SKIP_SAMPLES and is_sample and product_id not in ALWAYS_INCLUDE:
        print(f"  пропущен как образец: {name}", file=sys.stderr)
        return []
    description = clean_description(desc_node)

    offers = []
    for d in data.find_all("div", attrs={"data-id": True}, recursive=False):
        photos = d.get("data-big-photo") or d.get("data-photo") or ""
        pictures = [urljoin(BASE, p.strip()) for p in photos.split(";") if p.strip()]
        size = " ".join((d.get("data-list-sizes_clothes") or "").split())
        m = re.search(r"\s*рост\s*([\d\-–]+)", size, re.I)
        height = m.group(1) if m else ""
        if m:
            size = size[:m.start()].strip()
        offers.append({
            "id": d["data-id"],
            "group_id": product_id,
            "url": f"{BASE}/catalog/woman/{product_id}.html?product_id={d['data-id']}",
            "name": fix_name(d.get("data-title") or name),
            "price": to_int(d.get("data-price")),
            "old_price": to_int(d.get("data-old-price")) or old_price,
            "available": d.get("data-available") == "Y",
            "vendor_code": (d.get("data-artnumber") or "").strip(),
            "size": size,
            "height": height,
            "color": color or color_from_name(d.get("data-title") or name),
            "pictures": pictures[:10],
            "description": description,
        })
    return offers


# ---------- 3. Сборка YML ----------

def esc(s):
    return html.escape(str(s), quote=True)


def build_yml(categories, offers):
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+03:00")
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<yml_catalog date="{now}">',
        "<shop>",
        "<name>SEMPLERY</name>",
        "<company>SEMPLERY</company>",
        f"<url>{BASE}/</url>",
        '<currencies><currency id="RUB" rate="1"/></currencies>',
        "<categories>",
        f'<category id="{ROOT_CATEGORY[0]}">{esc(ROOT_CATEGORY[1])}</category>',
    ]
    for cid, cname in categories:
        out.append(f'<category id="{cid}" parentId="{ROOT_CATEGORY[0]}">{esc(cname)}</category>')
    out += ["</categories>", "<offers>"]

    for o in offers:
        if not o["price"]:
            continue
        if SKIP_UNAVAILABLE and not o["available"]:
            continue
        avail = "true" if o["available"] else "false"
        out.append(f'<offer id="{o["id"]}" group_id="{o["group_id"]}" available="{avail}">')
        out.append(f'<url>{esc(o["url"])}</url>')
        out.append(f'<name>{esc(o["name"])}</name>')
        out.append("<vendor>SEMPLERY</vendor>")
        if o["vendor_code"]:
            out.append(f'<vendorCode>{esc(o["vendor_code"])}</vendorCode>')
        out.append(f'<price>{o["price"]}</price>')
        if o["old_price"] and o["old_price"] > o["price"]:
            out.append(f'<oldprice>{o["old_price"]}</oldprice>')
        out.append("<currencyId>RUB</currencyId>")
        out.append(f'<categoryId>{o["category_id"]}</categoryId>')
        if SALES_NOTES:
            out.append(f"<sales_notes>{esc(SALES_NOTES)}</sales_notes>")
        for p in o["pictures"]:
            out.append(f"<picture>{esc(p)}</picture>")
        if o["description"]:
            out.append(f'<description><![CDATA[{o["description"]}]]></description>')
        if o["size"]:
            unit = "" if o["size"].lower() == "one size" else ' unit="INT"'
            out.append(f'<param name="Размер"{unit}>{esc(o["size"])}</param>')
        if o["height"]:
            out.append(f'<param name="Рост" unit="см">{esc(o["height"])}</param>')
        out.append('<param name="Пол">Женский</param>')
        if o["color"]:
            out.append(f'<param name="Цвет">{esc(o["color"])}</param>')
        out.append("</offer>")

    out += ["</offers>", "</shop>", "</yml_catalog>"]
    return "\n".join(out)


def main():
    product_sections = {}     # url товара -> разделы, где он встречается
    for slug, cname in CATEGORIES:
        print(f"Раздел: {cname}")
        urls = crawl_section(slug)
        print(f"  товаров: {len(urls)}")
        for u in urls:
            product_sections.setdefault(u, []).append(cname)

    cat_ids = {cname: idx for idx, (_, cname) in enumerate(CATEGORIES, start=10)}
    yml_categories = [(idx, cname) for cname, idx in cat_ids.items()]
    product_category = {u: secs for u, secs in product_sections.items()}
    print(f"\nВсего уникальных товаров: {len(product_category)}")
    all_offers = []
    for n, (url, secs) in enumerate(product_category.items(), 1):
        page_html = get(url)
        if not page_html:
            continue
        offers = parse_product(page_html, url) or []
        for o in offers:
            if "Спорт" in secs:
                cname = "Спорт"
            else:
                cname = type_category(o["name"]) or secs[0]
            o["category_id"] = cat_ids[cname]
        all_offers.extend(offers)
        print(f"  [{n}/{len(product_category)}] {url} — размеров: {len(offers)}")

    if not all_offers:
        # не перезаписываем рабочий фид пустым (например, если сайт заблокировал запросы)
        sys.exit("ОШИБКА: не собрано ни одного товара — фид не обновлён")

    used = {o["category_id"] for o in all_offers}
    yml_categories = [c for c in yml_categories if c[0] in used]
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(build_yml(yml_categories, all_offers))
    print(f"\nГотово: {OUTPUT}, предложений: {len(all_offers)}")


if __name__ == "__main__":
    main()
