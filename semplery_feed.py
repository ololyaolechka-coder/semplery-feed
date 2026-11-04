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
DELAY = (1.0, 2.0)          # пауза между запросами, сек (чтобы не забанили)
SKIP_UNAVAILABLE = False    # True — не выгружать размеры не в наличии

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


# ---------- 2. Карточка товара ----------

def clean_description(node):
    if node is None:
        return ""
    for t in node.find_all("table"):   # таблица обмеров в фиде не нужна
        t.decompose()
    text = node.get_text("\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    text = re.sub(r"^Арт\.\s*-?\s*", "", text)
    text = text.split("Обмеры изделия")[0].strip()
    return text[:2900]                 # лимит Яндекса на description — 3000


def parse_product(page_html, url):
    soup = BeautifulSoup(page_html, "lxml")
    data = soup.select_one("#catalogDetailData")
    if not data:
        return None

    product_id = re.search(r"/(\d+)\.html", url).group(1)
    h1 = soup.select_one(".b-catalog-detail h1, h1")
    name = h1.get_text(strip=True) if h1 else ""

    color_a = soup.select_one(".b-catalog-detail__color_ref a.i-active")
    color = color_a.get("title", "").strip() if color_a else ""

    old_price_node = soup.select_one(".b-catalog-detail__old-price s")
    old_price = to_int(old_price_node.get_text()) if old_price_node else None

    desc_node = soup.select_one('[itemtype="http://schema.org/Product"] [itemprop="description"]')
    description = clean_description(desc_node)

    offers = []
    for d in data.find_all("div", attrs={"data-id": True}, recursive=False):
        photos = d.get("data-big-photo") or d.get("data-photo") or ""
        pictures = [urljoin(BASE, p.strip()) for p in photos.split(";") if p.strip()]
        size = (d.get("data-list-sizes_clothes") or "").strip()
        offers.append({
            "id": d["data-id"],
            "group_id": product_id,
            "url": f"{BASE}/catalog/woman/{product_id}.html?product_id={d['data-id']}",
            "name": d.get("data-title") or name,
            "price": to_int(d.get("data-price")),
            "old_price": to_int(d.get("data-old-price")) or old_price,
            "available": d.get("data-available") == "Y",
            "vendor_code": (d.get("data-artnumber") or "").strip(),
            "size": size,
            "color": color,
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
        for p in o["pictures"]:
            out.append(f"<picture>{esc(p)}</picture>")
        if o["description"]:
            out.append(f'<description><![CDATA[{o["description"]}]]></description>')
        if o["size"]:
            out.append(f'<param name="Размер">{esc(o["size"])}</param>')
        if o["color"]:
            out.append(f'<param name="Цвет">{esc(o["color"])}</param>')
        out.append("</offer>")

    out += ["</offers>", "</shop>", "</yml_catalog>"]
    return "\n".join(out)


def main():
    product_category = {}     # url товара -> id категории (первая по приоритету)
    yml_categories = []
    for idx, (slug, cname) in enumerate(CATEGORIES, start=10):
        print(f"Раздел: {cname}")
        urls = crawl_section(slug)
        print(f"  товаров: {len(urls)}")
        if urls:
            yml_categories.append((idx, cname))
        for u in urls:
            product_category.setdefault(u, idx)

    print(f"\nВсего уникальных товаров: {len(product_category)}")
    all_offers = []
    for n, (url, cat_id) in enumerate(product_category.items(), 1):
        page_html = get(url)
        if not page_html:
            continue
        offers = parse_product(page_html, url) or []
        for o in offers:
            o["category_id"] = cat_id
        all_offers.extend(offers)
        print(f"  [{n}/{len(product_category)}] {url} — размеров: {len(offers)}")

    used = {o["category_id"] for o in all_offers}
    yml_categories = [c for c in yml_categories if c[0] in used]
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(build_yml(yml_categories, all_offers))
    print(f"\nГотово: {OUTPUT}, предложений: {len(all_offers)}")


if __name__ == "__main__":
    main()
