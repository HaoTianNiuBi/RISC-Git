#!/usr/bin/env python3
"""把 Markdown 方案书/技术文档转成可提交的 PDF（本机 Chrome/Edge 无头打印）。

用法：
    python RV-Doctor/scripts/md_to_pdf.py 方案书.md 成果包/方案书.pdf

依赖：
    pip install markdown
    本机需有 Chrome 或 Edge（脚本自动找常见路径）

说明：只做排版，不改内容；表格按 GitHub 风格渲染，代码块等宽字体。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

CSS = """
@page { size: A4; margin: 20mm 18mm 18mm 18mm; }
body { font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
       font-size: 10.5pt; line-height: 1.65; color: #1b1b1b; }
h1 { font-size: 20pt; margin: 0 0 6pt; border-bottom: 2px solid #333; padding-bottom: 4pt; }
h2 { font-size: 14pt; margin: 16pt 0 6pt; border-left: 4px solid #2f6f4f; padding-left: 8pt; }
h3 { font-size: 12pt; margin: 12pt 0 4pt; }
p, li { margin: 4pt 0; }
blockquote { margin: 8pt 0; padding: 6pt 10pt; background: #f6f8f6;
             border-left: 3px solid #9bb8a6; color: #333; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9.5pt; }
th, td { border: 1px solid #c8c8c8; padding: 4pt 6pt; vertical-align: top; }
th { background: #eef3ef; text-align: left; }
code { font-family: Consolas, "Cascadia Mono", monospace; font-size: 9pt;
       background: #f2f2f2; padding: 1pt 3pt; border-radius: 2pt; }
pre { background: #f6f6f6; border: 1px solid #e2e2e2; padding: 6pt 8pt; overflow-x: auto; }
pre code { background: none; padding: 0; }
a { color: #1a5fb4; text-decoration: none; }
hr { border: none; border-top: 1px solid #ddd; margin: 12pt 0; }
"""


def find_browser() -> str:
    for candidate in BROWSERS:
        if Path(candidate).exists():
            return candidate
    for name in ("msedge", "chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("找不到 Chrome/Edge，无法打印 PDF")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("output", type=Path)
    args = ap.parse_args()

    import markdown  # 延迟导入，缺包时给出可读提示

    text = args.source.read_text(encoding="utf-8")
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])
    html = (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>{args.source.stem}</title><style>{CSS}</style></head>"
        f"<body>{body}</body></html>"
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "doc.html"
        html_path.write_text(html, encoding="utf-8")
        browser = find_browser()
        cmd = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={args.output.resolve()}",
            html_path.as_uri(),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=180)
        if not args.output.exists():
            # 老版本浏览器不支持 --headless=new 时回退
            cmd[1] = "--headless"
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=180)
        if not args.output.exists():
            print(result.stdout[-800:])
            print(result.stderr[-800:])
            return 1

    size = args.output.stat().st_size
    print(f"pdf={args.output} bytes={size} browser={os.path.basename(browser)}")
    return 0 if size > 10_000 else 1


if __name__ == "__main__":
    sys.exit(main())