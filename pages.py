"""
The HTML pages (talk page, dashboard, login, patient phone) with this clinic's
name. The files say "Pearl Dental"; another deployment sets CLINIC_SHORT_NAME
in .env and the pages follow without editing them.
"""

from pathlib import Path

from fastapi.responses import HTMLResponse

import config

TEMPLATE_NAME = "Pearl Dental"


def html(path: Path) -> HTMLResponse:
    text = Path(path).read_text(encoding="utf-8")
    if config.CLINIC_SHORT_NAME != TEMPLATE_NAME:
        text = text.replace(TEMPLATE_NAME, config.CLINIC_SHORT_NAME)
    return HTMLResponse(text)
