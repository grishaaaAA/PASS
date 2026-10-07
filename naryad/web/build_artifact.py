"""
Сборка страницы генератора для ссылки в claude.ai: один HTML без сервера.

Справочные данные (парки, справка парка №7, вводные кейса, допущения, имена,
поля формата) берутся из Python-кода, логика - из static/generator.js.
Страница та же, что у локального сервера (index.html, app.js, app.css),
только данные считает генератор внутри страницы.

Запуск:
    python -m naryad.web.build_artifact            # -> dist/autodisp_generator.html
"""

from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

from ..data import names
from ..data.check import SCHEMA
from ..data.generate import FORMAT_VERSION
from ..data.presets import (ASSUMPTIONS, CASE, CASE_ASSUMPTIONS, CITY_PARKS, CLASS_LABELS, CLASSES,
                            DYNAMICS, PARK7)
from .server import VERSION

STATIC = Path(__file__).parent / "static"
OUT = Path(__file__).resolve().parents[2] / "dist" / "autodisp_generator.html"
FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Ubuntu:wght@400;500;700&display=swap">')


def data() -> dict:
    return {
        "page_version": VERSION,
        "format_version": FORMAT_VERSION,
        "classes": list(CLASSES),
        "class_labels": CLASS_LABELS,
        "city_parks": [list(p) for p in CITY_PARKS],
        "park7": PARK7,
        "case": {k: v for k, v in CASE.items() if k != "parks_setup"},
        "dynamics": DYNAMICS,
        "assumptions": ASSUMPTIONS,
        "case_assumptions": CASE_ASSUMPTIONS,
        "schema": {entity: [list(f) for f in fields] for entity, fields in SCHEMA.items()},
        "names": {"surnames": names.SURNAMES, "male": names.MALE_NAMES, "female": names.FEMALE_NAMES,
                  "patronymics": [list(p) for p in names.PATRONYMICS], "female_share": names.FEMALE_SHARE},
    }


def read(path: str) -> str:
    return (STATIC / path).read_text(encoding="utf-8")


def script(text: str) -> str:
    return "<script>\n" + text.replace("</script", "<\\/script") + "\n</script>"


def build(out: Path = OUT) -> Path:
    tokens = read("ds/tokens.css")
    tokens = tokens[:tokens.index("@font-face")] if "@font-face" in tokens else tokens
    logo = base64.b64encode((STATIC / "ds/logo.svg").read_bytes()).decode()
    page = read("index.html")
    body = page[page.index("<body>") + len("<body>"):page.index("</body>")]
    body = re.sub(r'<script src="[^"]+"></script>\s*', "", body)
    body = body.replace('src="ds/logo.svg"', f'src="data:image/svg+xml;base64,{logo}"')
    payload = json.dumps(data(), ensure_ascii=False)
    html = "\n".join([
        "<title>Генератор AUTODISP</title>",
        FONTS,
        "<style>\n" + tokens + read("ds/bundle.css") + read("app.css") + "\n</style>",
        body.strip(),
        script(read("ds/bundle.js")),
        script("window.AUTODISP_DATA = " + payload + ";"),
        script(read("generator.js")),
        script(read("artifact-api.js")),
        script(read("app.js")),
    ])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    path = build(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
    print(f"{path} ({path.stat().st_size // 1024} КБ)")
