#!/usr/bin/env python3
"""Bundle the built UI into a single self-contained HTML file.

Run `npm run build` in app/ first, then this. The result (assets/ui-preview.html) has the
JS and CSS inlined and no server requirement at all, so the interface can be opened and
reviewed — including in the Freebuff Preview panel, or by double-clicking it — without
starting the Python service. Add `?demo=1` to get the placeholder dataset.

    .runtime/bin/python scripts/make-preview.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "app" / "dist"
OUT = ROOT / "assets" / "ui-preview.html"


def inline(html: str) -> str:
    # Stylesheet
    def css_replacement(match: re.Match[str]) -> str:
        href = match.group(1).lstrip("/")
        path = DIST / href
        if not path.is_file():
            return match.group(0)
        return f"<style>\n{path.read_text()}\n</style>"

    html = re.sub(r'<link[^>]+rel="stylesheet"[^>]*href="([^"]+)"[^>]*>', css_replacement, html)

    # Module script
    def js_replacement(match: re.Match[str]) -> str:
        src = match.group(1).lstrip("/")
        path = DIST / src
        if not path.is_file():
            return match.group(0)
        code = path.read_text()
        # A closing script tag inside the bundle would end the inline block early.
        code = code.replace("</script>", "<\\/script>")
        return f'<script type="module">\n{code}\n</script>'

    html = re.sub(r'<script[^>]+src="([^"]+)"[^>]*></script>', js_replacement, html)

    # The app's CSP forbids inline scripts, which is exactly what this file is made of.
    html = re.sub(r"<meta[^>]+Content-Security-Policy[^>]*>", "", html, flags=re.IGNORECASE)

    # This artifact is a design preview with no service behind it, so it always runs on
    # the built-in demo dataset.
    html = html.replace(
        '<div id="root"></div>',
        '<div id="root"></div>\n    <script>window.__REFRACT_DEMO__ = true;</script>',
    )
    return html


def main() -> int:
    index = DIST / "index.html"
    if not index.is_file():
        print(f"{index} not found — run `npm run build` in app/ first.", file=sys.stderr)
        return 1

    html = inline(index.read_text())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
