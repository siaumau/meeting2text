import re
import sys
from pathlib import Path

import markdown as md_lib

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --pink: #ff8fab;
    --pink-light: #ffe3ea;
    --pink-dark: #d6537a;
    --ink: #2b2b33;
    --muted: #8a8fa3;
    --bg: #f7f4f8;
    --card: #ffffff;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 40px 16px;
    background: var(--bg);
    font-family: "Microsoft JhengHei", "PingFang TC", "Noto Sans TC", "Segoe UI", sans-serif;
    color: var(--ink);
    line-height: 1.8;
  }}
  .doc {{
    max-width: 820px;
    margin: 0 auto;
    background: var(--card);
    border-radius: 20px;
    padding: 40px 48px 32px;
    box-shadow: 0 12px 32px rgba(43, 20, 30, 0.08);
  }}
  .badge {{
    display: inline-block;
    font-size: 13px;
    color: var(--pink-dark);
    background: var(--pink-light);
    border-radius: 999px;
    padding: 4px 14px;
    margin-bottom: 12px;
  }}
  h1 {{
    font-size: 26px;
    margin: 0 0 8px;
    padding-bottom: 14px;
    border-bottom: 3px solid var(--pink);
  }}
  h2 {{
    font-size: 17px;
    color: var(--pink-dark);
    margin-top: 32px;
    margin-bottom: 12px;
    padding: 8px 16px;
    background: var(--pink-light);
    border-radius: 10px;
    display: inline-block;
  }}
  h3 {{
    font-size: 15px;
    color: var(--ink);
    margin-top: 20px;
  }}
  p {{ margin: 10px 0; }}
  ul, ol {{ padding-left: 24px; }}
  li {{ margin: 6px 0; }}
  li::marker {{ color: var(--pink-dark); }}
  strong {{ color: var(--pink-dark); }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 12px 0 20px;
    font-size: 14px;
    overflow: hidden;
    border-radius: 10px;
  }}
  th, td {{
    border: 1px solid #f1dfe6;
    padding: 10px 14px;
    text-align: left;
  }}
  th {{
    background: var(--pink-light);
    color: var(--pink-dark);
    font-weight: 600;
  }}
  tr:nth-child(even) td {{ background: #fffafb; }}
  .footer {{
    margin-top: 40px;
    padding-top: 16px;
    border-top: 1px dashed #eadde2;
    text-align: right;
    font-size: 12px;
    color: var(--muted);
  }}
  @media print {{
    body {{ background: white; padding: 0; }}
    .doc {{ box-shadow: none; border-radius: 0; max-width: none; }}
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #1c1b22; color: #eee6ea; }}
    .doc {{ background: #26232b; box-shadow: none; }}
    h2 {{ background: #3a2732; }}
    th {{ background: #3a2732; }}
    tr:nth-child(even) td {{ background: #2b2830; }}
    td, th {{ border-color: #3d3540; }}
  }}
</style>
</head>
<body>
  <div class="doc">
    <span class="badge">📓 會議錄音轉文字工具</span>
    {body}
    <div class="footer">由 AI 自動整理產生，內容可能有誤，請以原始錄音／逐字稿為準</div>
  </div>
</body>
</html>
"""


def md_to_html_body(md_text: str) -> str:
    return md_lib.markdown(md_text, extensions=["tables", "nl2br"])


def export_html(md_path: Path, html_path: Path | None = None) -> Path:
    md_text = md_path.read_text(encoding="utf-8")
    target = html_path or md_path.with_suffix(".html")

    title_match = re.search(r"^#\s+(.+)$", md_text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else target.stem

    body = md_to_html_body(md_text)
    html = HTML_TEMPLATE.format(title=title, body=body)
    target.write_text(html, encoding="utf-8")
    return target


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("用法：python export_html.py <會議紀錄.md> [輸出.html]")
    out = export_html(
        Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None
    )
    print(f"已匯出：{out}")
