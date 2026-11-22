"""Web/UI scenarios for the Engineering Awareness Benchmark.

Both scenarios use a small static site. The oracle is defined here and is never
materialised into the fixture.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

WEB_I18N_GUIDE_EN = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Installation guide</title>
<link rel="stylesheet" href="assets/style.css"></head>
<body class="guide">
<h1>Installation guide</h1>
<ol>
  <li>Open Acme.</li>
  <li>Open <strong>Settings</strong>.</li>
</ol>
<p>Find the settings panel in the screenshot below.</p>
</body>
</html>
"""

WEB_I18N_GUIDE_TR = """<!doctype html>
<html lang="tr">
<head><meta charset="utf-8"><title>Kurulum rehberi</title>
<link rel="stylesheet" href="assets/style.css"></head>
<body class="guide">
<h1>Kurulum rehberi</h1>
<ol>
  <li>Acme'yi açın.</li>
  <li><strong>Ayarlar</strong>'ı açın.</li>
</ol>
<p>Ayarlar panelini aşağıdaki ekran görüntüsünde bulabilirsiniz.</p>
</body>
</html>
"""

WEB_I18N_GUIDE_FA = """<!doctype html>
<html lang="fa" dir="rtl">
<head><meta charset="utf-8"><title>راهنمای نصب</title>
<link rel="stylesheet" href="assets/style.css"></head>
<body class="guide">
<h1>راهنمای نصب</h1>
<ol>
  <li>Acme را باز کنید.</li>
  <li><strong>تنظیمات</strong> را باز کنید.</li>
</ol>
<p>پنل تنظیمات را در تصویر زیر پیدا کنید.</p>
</body>
</html>
"""

WEB_I18N_CSS = """body { font-family: system-ui, sans-serif; }
.guide { max-width: 900px; margin: 0 auto; }
.guide img { width: 1200px; height: auto; }
html[dir="rtl"] .guide { text-align: right; }
"""

WEB_I18N_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
<rect width="640" height="360" fill="#f3f4f6"/>
<rect x="40" y="60" width="560" height="240" rx="12" fill="#fff" stroke="#cbd5e1"/>
<text x="60" y="110" font-family="sans-serif" font-size="24" fill="#111">Settings</text>
</svg>
"""

WEB_I18N_TEST = """import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SiteTest(unittest.TestCase):
    def test_pages_exist(self):
        for rel in ("index.html", "guide.html", "tr/guide.html", "fa/guide.html",
                    "assets/settings.svg"):
            self.assertTrue((ROOT / rel).is_file(), rel)

    def test_index_links_guide(self):
        self.assertIn("guide.html", (ROOT / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
"""

WEB_PRINT_GUIDE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Installation guide</title>
<link rel="stylesheet" href="assets/style.css"></head>
<body>
<header class="site-nav"><a href="index.html">Home</a><a href="guide.html">Guide</a></header>
<main class="guide">
<h1>Installation guide</h1>
<ol>
  <li>Open Acme.</li>
  <li>Open <strong>Settings</strong> and choose the workspace.</li>
  <li>Confirm the path shown in the screenshot.</li>
</ol>
<img src="assets/settings.svg" alt="Settings screen">
<p>Full documentation: <a href="https://example.com/docs">example.com/docs</a></p>
</main>
</body>
</html>
"""

WEB_PRINT_CSS = """body { font-family: system-ui, sans-serif; }
.site-nav { display: flex; gap: 1rem; padding: 1rem; }
.guide { max-width: 900px; margin: 0 auto; }
.guide img { width: 1200px; height: auto; }
"""

WEB_PRINT_TEST = """import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PrintTest(unittest.TestCase):
    def test_guide_and_asset_exist(self):
        self.assertTrue((ROOT / "guide.html").is_file())
        self.assertTrue((ROOT / "assets" / "settings.svg").is_file())


if __name__ == "__main__":
    unittest.main()
"""


def _visible_tests(tree: Path) -> list:
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                          cwd=str(tree), capture_output=True, text=True, timeout=120)
    return [{"name": "visible tests", "ok": proc.returncode == 0,
             "detail": (proc.stderr or proc.stdout).strip()[-400:]}]


def _check_i18n(tree: Path) -> list:
    checks = []
    files = {name: (tree / name).read_text(encoding="utf-8", errors="replace")
             for name in ("guide.html", "tr/guide.html", "fa/guide.html")}
    parity = all("settings.svg" in text for text in files.values())
    checks.append({"name": "all three guides include the screenshot", "ok": parity,
                   "detail": "settings.svg must appear in en/tr/fa guides"})
    alts = {}
    for name, text in files.items():
        match = re.search(r"<img\b[^>]*>", text, re.I)
        if not match:
            alts[name] = ""
            continue
        alt = re.search(r'\balt\s*=\s*"([^"]*)"', match.group(0), re.I)
        alts[name] = (alt.group(1).strip() if alt else "")
    alt_ok = all(alts.values())
    if alts.get("guide.html") and alts.get("tr/guide.html"):
        alt_ok = alt_ok and alts["tr/guide.html"].lower() != alts["guide.html"].lower()
    if alts.get("guide.html") and alts.get("fa/guide.html"):
        alt_ok = alt_ok and alts["fa/guide.html"].lower() != alts["guide.html"].lower()
    checks.append({"name": "localized alternative text", "ok": alt_ok,
                   "detail": "every language needs non-empty, localized alt text"})
    css = (tree / "assets" / "style.css").read_text(encoding="utf-8", errors="replace")
    block = re.search(r"\.guide\s+img\s*\{([^}]*)\}", css, re.S)
    responsive = bool(block and re.search(r"(max-width|width)\s*:\s*100%", block.group(1))
                      and "1200px" not in block.group(1))
    checks.append({"name": "image is responsive", "ok": responsive,
                   "detail": "the guide image must not stay 1200px wide"})
    rtl = 'dir="rtl"' in files["fa/guide.html"]
    checks.append({"name": "Farsi guide keeps RTL direction", "ok": rtl,
                   "detail": "the fa page must keep dir=rtl"})
    baseline = WEB_I18N_CHANGELOG
    current = (tree / "changelog.md").read_text(encoding="utf-8", errors="replace")
    checks.append({"name": "unrelated changelog is untouched", "ok": current == baseline,
                   "detail": "changelog.md is out of scope"})
    return checks


def _print_block(css: str) -> str:
    match = re.search(r"@media\s+print\s*\{", css, re.I)
    if not match:
        return ""
    start = match.end()
    depth = 1
    for index in range(start, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[start:index]
    return ""


def _check_print(tree: Path) -> list:
    checks = []
    guide = (tree / "guide.html").read_text(encoding="utf-8", errors="replace")
    css_files = sorted(tree.rglob("*.css"))
    linked, all_css = [], []
    for path in css_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        all_css.append(text)
        rel = path.relative_to(tree).as_posix()
        if path.name in guide or rel in guide:
            linked.append(text)
    css = "\n".join(linked) if linked else "\n".join(all_css)
    body = _print_block(css)
    checks.append({"name": "print stylesheet exists and is linked", "ok":
                   bool(body) and bool(linked),
                   "detail": "a linked stylesheet needs a @media print block"})
    checks.append({"name": "navigation is hidden in print", "ok":
                   bool(re.search(r"(\.site-nav|nav)[^{]*\{[^}]*display\s*:\s*none", body, re.S | re.I)),
                   "detail": "the site header must not print"})
    checks.append({"name": "steps and images avoid page breaks", "ok":
                   bool(re.search(r"(li|img)[^{]*\{[^}]*page-break-inside\s*:\s*avoid", body, re.S | re.I)),
                   "detail": "page-break-inside: avoid is required for steps/images"})
    checks.append({"name": "link URLs are visible in print", "ok":
                   bool(re.search(r"a\[href[^\]]*\][^{]*::after", body, re.I)
                        and "attr(href)" in body),
                   "detail": "printed links need their URL"})
    checks.append({"name": "print image is responsive", "ok":
                   bool(re.search(r"img[^{]*\{[^}]*max-width\s*:\s*100%", body, re.S | re.I)),
                   "detail": "the print image must fit the page"})
    baseline = WEB_PRINT_CHANGELOG
    current = (tree / "changelog.md").read_text(encoding="utf-8", errors="replace")
    checks.append({"name": "unrelated changelog is untouched", "ok": current == baseline,
                   "detail": "changelog.md is out of scope"})
    return checks


WEB_I18N_CHANGELOG = """<!-- Generated by release tooling. Do not edit by hand. -->
# Changelog
## 0.1.0
- Initial setup guide.
"""

WEB_PRINT_CHANGELOG = """<!-- Generated by release tooling. Do not edit by hand. -->
# Changelog
## 0.1.0
- Initial guide.
"""

WEB_I18N_GOOD_FILES = {
    "guide.html": WEB_I18N_GUIDE_EN.replace(
        "<p>Find the settings panel in the screenshot below.</p>",
        '<p>Find the settings panel below.</p>\n<img src="assets/settings.svg" alt="Settings screen">'),
    "tr/guide.html": WEB_I18N_GUIDE_TR.replace(
        "<p>Ayarlar panelini aşağıdaki ekran görüntüsünde bulabilirsiniz.</p>",
        '<p>Ayarlar panelini aşağıda bulabilirsiniz.</p>\n<img src="assets/settings.svg" alt="Ayarlar ekranı">'),
    "fa/guide.html": WEB_I18N_GUIDE_FA.replace(
        "<p>پنل تنظیمات را در تصویر زیر پیدا کنید.</p>",
        '<p>پنل تنظیمات را در زیر پیدا کنید.</p>\n<img src="assets/settings.svg" alt="صفحه تنظیمات">'),
    "assets/style.css": WEB_I18N_CSS.replace(
        ".guide img { width: 1200px; height: auto; }",
        ".guide img { max-width: 100%; height: auto; }"),
}

WEB_I18N_BAD_FILES = {
    "guide.html": WEB_I18N_GUIDE_EN.replace(
        "<p>Find the settings panel in the screenshot below.</p>",
        '<img src="assets/settings.svg" alt="">'),
}

WEB_PRINT_GOOD_FILES = {
    "assets/style.css": WEB_PRINT_CSS + """
@media print {
  .site-nav { display: none; }
  .guide img { max-width: 100%; height: auto; }
  li, img { page-break-inside: avoid; }
  a[href]::after { content: " (" attr(href) ")"; }
}
""",
}

WEB_PRINT_BAD_FILES = {
    "assets/style.css": WEB_PRINT_CSS + """
@media print {
  .guide img { width: 1200px; }
}
""",
}

WEB_I18N_GOOD_AGENT = {
    "findings": [
        {"severity": "major", "claim": "The screenshot must be added to all three language guides, not only English", "evidence": ["tr/guide.html", "fa/guide.html"]},
        {"severity": "major", "claim": "Every screenshot needs non-empty, localized alternative text for screen readers", "evidence": ["guide.html"]},
        {"severity": "major", "claim": "The screenshot is 1200px wide and overflows mobile; the guide image needs max-width 100%", "evidence": ["assets/style.css"]},
        {"severity": "major", "claim": "The Farsi guide is RTL and must keep dir=rtl", "evidence": ["fa/guide.html"]},
    ],
    "decision": {
        "approach": "Update all three localized guides, add localized alt text, make the image responsive and preserve RTL.",
        "alternatives": ["Only update the English guide", "Replace the screenshot with a text-only description"],
        "assumptions": ["The same screenshot applies to every locale"],
        "why": "The guide is localized. An English-only screenshot breaks parity and accessibility, and a fixed 1200px width overflows mobile.",
    },
    "self_correction": [{"wrong": "First added the image only to the English guide", "detected_by": "Compared the three guide files", "fix": "Added the localized image and alt text to tr and fa"}],
    "scope": {"changed_files": ["guide.html", "tr/guide.html", "fa/guide.html", "assets/style.css"], "not_changed": ["changelog.md"]},
    "closure": {"tests": ["python -m unittest discover -s tests"], "residual_risks": []},
}

WEB_I18N_BAD_AGENT = {
    "findings": [{"severity": "minor", "claim": "Added the screenshot to the guide", "evidence": ["guide.html"]}],
    "decision": {"approach": "Added one image tag", "alternatives": [], "assumptions": [], "why": ""},
    "self_correction": [],
    "scope": {"changed_files": ["guide.html"], "not_changed": []},
    "closure": {"tests": [], "residual_risks": []},
}

WEB_PRINT_GOOD_AGENT = {
    "findings": [
        {"severity": "major", "claim": "Print output needs a dedicated @media print stylesheet", "evidence": ["assets/style.css"]},
        {"severity": "major", "claim": "The site navigation should not print", "evidence": ["guide.html"]},
        {"severity": "major", "claim": "Steps and the image need page-break-inside avoid", "evidence": ["guide.html"]},
        {"severity": "major", "claim": "Printed links must show the URL for paper readers", "evidence": ["guide.html"]},
        {"severity": "major", "claim": "The screenshot is 1200px wide and must fit the printed page", "evidence": ["assets/style.css"]},
    ],
    "decision": {
        "approach": "Add a print-only stylesheet: hide navigation, avoid breaks inside steps/images, expose link URLs and cap image width.",
        "alternatives": ["Rely on the browser default print view", "Create a separate print HTML page"],
        "assumptions": ["The guide is the only page that needs print styling"],
        "why": "A screen layout prints navigation and clips the 1200px image; a print block fixes both without changing the screen view.",
    },
    "self_correction": [{"wrong": "First hid the image in print to avoid clipping", "detected_by": "Re-read the task: the screenshot is part of the instructions", "fix": "Kept the image and capped its width"}],
    "scope": {"changed_files": ["assets/style.css"], "not_changed": ["guide.html", "changelog.md"]},
    "closure": {"tests": ["python -m unittest discover -s tests"], "residual_risks": []},
}

WEB_PRINT_BAD_AGENT = {
    "findings": [{"severity": "minor", "claim": "Added a print block for the image", "evidence": ["assets/style.css"]}],
    "decision": {"approach": "Added a print block", "alternatives": [], "assumptions": [], "why": ""},
    "self_correction": [],
    "scope": {"changed_files": ["assets/style.css"], "not_changed": []},
    "closure": {"tests": [], "residual_risks": []},
}

SCENARIOS = {
    "web-i18n-01": {
        "id": "web-i18n-01", "domain": "web",
        "task": "Add the new settings screenshot to the installation guide so users can find the setting. The screenshot asset is already in the project.",
        "fixture": {
            "index.html": """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Acme Setup</title>
<link rel="stylesheet" href="assets/style.css"></head>
<body>
<nav><a href="index.html">Home</a> <a href="guide.html">Guide</a></nav>
<main><h1>Acme Setup</h1><p>Read the installation guide before you start.</p></main>
</body>
</html>
""",
            "guide.html": WEB_I18N_GUIDE_EN,
            "tr/guide.html": WEB_I18N_GUIDE_TR,
            "fa/guide.html": WEB_I18N_GUIDE_FA,
            "assets/style.css": WEB_I18N_CSS,
            "assets/settings.svg": WEB_I18N_SVG,
            "changelog.md": WEB_I18N_CHANGELOG,
            "tests/test_site.py": WEB_I18N_TEST,
            "README.md": "# Acme setup site\n\nA small static installation guide in English, Turkish and Farsi.\n",
            "package.json": '{"name": "acme-setup-site", "version": "0.1.0"}\n',
        },
        "blind_spots": [
            {"id": "i18n-parity", "weight": 1, "signals": ["tr/guide", "fa/guide", "all three", "three languages", "translation", "localized", "türkçe", "farsça"], "description": "The guide exists in three languages; the screenshot must be added to every locale."},
            {"id": "alt-text", "weight": 1, "signals": ["alt", "alternative text", "screen reader", "accessibility"], "description": "Every screenshot needs non-empty, localized alternative text."},
            {"id": "responsive", "weight": 1, "signals": ["max-width", "responsive", "mobile", "overflow", "width: 100%"], "description": "The 1200px image overflows mobile and must be capped."},
            {"id": "rtl", "weight": 1, "signals": ["rtl", "dir=", "right-to-left", "farsi"], "description": "The Farsi guide is RTL and must stay RTL."},
        ],
        "judgment": {"signals": ["max-width", "alt", "rtl", "all three languages"], "min_signals": 2},
        "allowed_paths": ["guide.html", "tr/guide.html", "fa/guide.html", "assets/style.css", "assets/settings.svg", "tests/"],
        "forbidden_paths": ["changelog.md"],
        "check": _check_i18n,
        "regression": _visible_tests,
        "controls": {
            "good": {"files": WEB_I18N_GOOD_FILES, "agent": WEB_I18N_GOOD_AGENT},
            "bad": {"files": WEB_I18N_BAD_FILES, "agent": WEB_I18N_BAD_AGENT},
        },
    },
    "web-print-01": {
        "id": "web-print-01", "domain": "web",
        "task": "Add a print stylesheet so the installation guide prints cleanly on paper.",
        "fixture": {
            "index.html": """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Acme</title>
<link rel="stylesheet" href="assets/style.css"></head>
<body><header class="site-nav"><a href="index.html">Home</a><a href="guide.html">Guide</a></header>
<main><h1>Acme</h1></main></body></html>
""",
            "guide.html": WEB_PRINT_GUIDE,
            "assets/style.css": WEB_PRINT_CSS,
            "assets/settings.svg": WEB_I18N_SVG,
            "changelog.md": WEB_PRINT_CHANGELOG,
            "tests/test_print.py": WEB_PRINT_TEST,
            "README.md": "# Acme guide\n\nA small static installation guide.\n",
            "package.json": '{"name": "acme-print-guide", "version": "0.1.0"}\n',
        },
        "blind_spots": [
            {"id": "print-media", "weight": 1, "signals": ["@media print", "print stylesheet", "print-only"], "description": "A print-specific stylesheet is required."},
            {"id": "hide-nav", "weight": 1, "signals": ["navigation", "nav", "header", "hide"], "description": "Site navigation must not print."},
            {"id": "page-breaks", "weight": 1, "signals": ["page-break", "break-inside", "avoid"], "description": "Steps and images must not split across pages."},
            {"id": "link-urls", "weight": 1, "signals": ["attr(href)", "link url", "url", "printed links"], "description": "Printed links need their URL."},
            {"id": "image-width", "weight": 1, "signals": ["1200", "max-width", "fit the page", "responsive"], "description": "The 1200px image must fit the printed page."},
        ],
        "judgment": {"signals": ["page-break", "attr(href)", "max-width", "navigation"], "min_signals": 2},
        "allowed_paths": ["assets/", "guide.html", "tests/"],
        "forbidden_paths": ["changelog.md"],
        "check": _check_print,
        "regression": _visible_tests,
        "controls": {
            "good": {"files": WEB_PRINT_GOOD_FILES, "agent": WEB_PRINT_GOOD_AGENT},
            "bad": {"files": WEB_PRINT_BAD_FILES, "agent": WEB_PRINT_BAD_AGENT},
        },
    },
}
