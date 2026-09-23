#!/usr/bin/env python3
"""Static site generator for https://sakuradairong.github.io

The original Hexo source repository (hexo-blog-fly) no longer exists - only the
generated HTML was left on the master branch.  This script rebuilds the site
from Markdown sources in content/_posts with a responsive, self-contained
editorial design. Existing article URLs and Markdown content are preserved.

Usage:
    python3 tools/build_site.py            # write the site into the repo root
    python3 tools/build_site.py --check    # post-generation link/tag validation
"""

from __future__ import annotations

import argparse
import html
import json
import re
import struct
import sys
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path

import markdown

SITE_TITLE = "雨季少年的博客"
SITE_AUTHOR = "雨季少年"
SITE_URL = "https://sakuradairong.github.io"
SITE_LANG = "zh-CN"
GENERATOR = "Rain Notes / build_site.py"
TZ = timezone(timedelta(hours=8))

ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = ROOT / "content" / "_posts"
LEGACY_DIR = ROOT / "content" / "legacy"

MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
MONTH_ABBR = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


@dataclass
class Post:
    title: str
    slug: str
    dt: datetime
    description: str = ""
    html_body: str = ""
    html_excerpt: str = ""
    source: str = ""
    category: str = "工程手记"
    tags: str = ""

    @property
    def path(self) -> str:
        return f"/{self.dt.year}/{self.dt.month:02d}/{self.dt.day:02d}/{self.slug}/"

    @property
    def date_text(self) -> str:
        return self.dt.strftime("%Y-%m-%d")

    @property
    def iso(self) -> str:
        return (
            self.dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            + "Z"
        )

    @property
    def archive_date_text(self) -> str:
        return f"{MONTH_ABBR[self.dt.month]} {self.dt.day}"


# --------------------------------------------------------------------------
# markdown -> landscape flavoured HTML
# --------------------------------------------------------------------------
_SLUG_KEEP = re.compile(r"[^\w\u4e00-\u9fff\u3040-\u30ff\- ]+", re.UNICODE)


def heading_id(text: str) -> str:
    """Hexo toc style anchor id: keep CJK, spaces become dashes."""
    txt = re.sub(r"<[^>]+>", "", text)
    txt = _SLUG_KEEP.sub("", txt).strip().lower()
    return re.sub(r"\s+", "-", txt)


def _highlight_block(code: str, lang: str) -> str:
    """Reproduce Hexo's <figure class="highlight"> wrapper with line gutters."""
    lines = code.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    gutter = "".join(
        f'<span class="line">{i + 1}</span><br>' for i in range(len(lines))
    )
    body = "".join(
        f'<span class="line">{html.escape(line) if line else ""}</span><br>'
        for line in lines
    )
    return (
        f'<figure class="highlight {html.escape(lang or "plain")}">'
        f'<table><tr><td class="gutter"><pre>{gutter}</pre></td>'
        f'<td class="code"><pre>{body}</pre></td></tr></table></figure>'
    )


_CODE_BLOCK_RE = re.compile(
    r'<pre><code class="language-(?P<lang>[^"]*)">(?P<code>.*?)</code></pre>',
    re.DOTALL,
)
_CODE_BLOCK_PLAIN_RE = re.compile(r"<pre><code>(?P<code>.*?)</code></pre>", re.DOTALL)
_HEADING_RE = re.compile(r"<h(?P<lvl>[1-6])>(?P<body>.*?)</h(?P=lvl)>", re.DOTALL)
_LINK_RE = re.compile(r'<a href="(?P<href>[^"]+)"')


def _postprocess(rendered: str) -> str:
    """Give the output the same shape Hexo's renderer produced."""

    def code_sub(match: re.Match) -> str:
        raw = html.unescape(re.sub(r"<[^>]+>", "", match.group("code")))
        return _highlight_block(raw, match.group("lang"))

    rendered = _CODE_BLOCK_RE.sub(code_sub, rendered)
    rendered = _CODE_BLOCK_PLAIN_RE.sub(
        lambda m: _highlight_block(
            html.unescape(re.sub(r"<[^>]+>", "", m.group("code"))), "plain"
        ),
        rendered,
    )

    def heading_sub(match: re.Match) -> str:
        level, body = match.group("lvl"), match.group("body")
        hid = heading_id(body)
        title = html.escape(re.sub(r"<[^>]+>", "", body), quote=True)
        return (
            f'<h{level} id="{hid}">'
            f'<a href="#{hid}" class="headerlink" title="{title}"></a>{body}'
            f"</h{level}>"
        )

    rendered = _HEADING_RE.sub(heading_sub, rendered)

    def link_sub(match: re.Match) -> str:
        href = match.group("href")
        if href.startswith(("http://", "https://", "//")):
            return f'<a href="{href}" target="_blank" rel="noopener"'
        return f'<a href="{href}"'

    return _LINK_RE.sub(link_sub, rendered)


MD = markdown.Markdown(
    extensions=["fenced_code", "tables", "sane_lists", "attr_list"],
    output_format="html5",
)


def render_markdown(text: str) -> str:
    MD.reset()
    return _postprocess(MD.convert(text))


def split_excerpt(text: str) -> tuple[str, bool]:
    marker = "<!-- more -->"
    if marker in text:
        head, _, _tail = text.partition(marker)
        return head, True
    return text, False


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------
def _parse_front_matter(raw: str) -> tuple[dict, str]:
    meta: dict[str, str] = {}
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            block = raw[3:end].strip("\n")
            raw = raw[end + 4 :]
            for line in block.split("\n"):
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, raw.lstrip("\n")


def load_posts() -> list[Post]:
    posts: list[Post] = []

    for md_path in sorted(POSTS_DIR.glob("*.md")):
        meta, body = _parse_front_matter(md_path.read_text(encoding="utf-8"))
        dt = datetime.strptime(meta["date"], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        excerpt_md, has_more = split_excerpt(body)
        posts.append(
            Post(
                title=meta["title"],
                slug=meta.get("slug") or md_path.stem,
                dt=dt,
                description=meta.get("description", ""),
                html_body=render_markdown(
                    body.replace("<!-- more -->", "").strip("\n")
                ),
                html_excerpt=render_markdown(excerpt_md) if has_more else "",
                source=md_path.name,
                category=meta.get("category", "工程手记"),
                tags=meta.get("tags", ""),
            )
        )

    for json_path in sorted(LEGACY_DIR.glob("*.json")):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        dt = (
            datetime.fromisoformat(data["datetime"].replace("Z", "+00:00"))
            .astimezone(TZ)
        )
        posts.append(
            Post(
                title=data["title"],
                slug=data["slug"],
                dt=dt,
                description=data.get("description", ""),
                html_body=data["body"],
                html_excerpt="",
                source=json_path.name,
            )
        )

    posts.sort(key=lambda p: p.dt, reverse=True)
    return posts


# --------------------------------------------------------------------------
# page shell and views
# --------------------------------------------------------------------------
def plain_text(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value))


def reading_minutes(post: Post) -> int:
    return max(1, round(len(plain_text(post.html_body)) / 500))


def _page(title: str, path: str, main: str, posts: list[Post],
          og_type: str = "website", description: str = "", published: str = "") -> str:
    esc = html.escape
    description = description or "记录代码里的思考，和把想法做成工具的过程。雨季少年的开源项目与开发手记。"
    nav = ''.join(f'<a href="{url}"{chr(32) + "aria-current=page" if active else ""}>{label}</a>'
                  for url, label, active in [("/", "手记", path == "/"),
                                            ("/archives/", "归档", path.startswith("/archives/"))])
    search_items = ''.join(
        f'<li data-search="{esc(p.title + " " + p.description + " " + p.tags, quote=True)}">'
        f'<a href="{p.path}"><span>{esc(p.category)} · {p.date_text}</span>{esc(p.title)}</a></li>'
        for p in posts)
    published_meta = f'<meta property="article:published_time" content="{published}">' if published else ''
    return f"""<!DOCTYPE html>
<html lang="{SITE_LANG}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description, quote=True)}">
<meta property="og:title" content="{esc(title, quote=True)}">
<meta property="og:type" content="{og_type}">
<meta property="og:url" content="{SITE_URL}{path}">
<meta property="og:description" content="{esc(description, quote=True)}">
<meta property="og:site_name" content="{SITE_TITLE}">
<meta name="twitter:card" content="summary">
<meta name="generator" content="{GENERATOR}">
{published_meta}
<link rel="canonical" href="{SITE_URL}{path}">
<link rel="icon" href="/favicon.png">
<link rel="alternate" href="/atom.xml" title="{SITE_TITLE}" type="application/atom+xml">
<link rel="stylesheet" href="/css/style.css">
<script src="/js/script.js" defer></script>
</head>
<body>
<a class="skip-link" href="#main">跳到正文</a>
<header class="site-header"><div class="header-inner">
<a class="brand" href="/" aria-label="雨季少年的博客首页"><span class="brand-mark" aria-hidden="true">雨</span><span>雨季少年<span class="brand-sub">代码与生活的切片</span></span></a>
<nav aria-label="主导航">{nav}<a href="https://github.com/sakuradairong" target="_blank" rel="noopener">GitHub ↗</a><button class="search-trigger" type="button" data-open-search hidden><span aria-hidden="true">⌕</span> 搜索 <kbd>/</kbd></button></nav>
</div></header>
<main id="main" class="site-main" tabindex="-1">{main}</main>
<footer class="site-footer"><div><a class="footer-brand" href="/">雨季少年<span> / </span>RAIN NOTES</a><p>在代码里探索，在文字里留痕。</p></div><div class="footer-right"><a href="/atom.xml">RSS 订阅 ↗</a><span>© 2020–{max((p.dt.year for p in posts), default=2026)} 雨季少年</span></div></footer>
<dialog id="search-dialog" aria-labelledby="search-title"><div class="search-heading"><h2 id="search-title">搜索手记</h2><button type="button" data-close-search aria-label="关闭搜索">✕</button></div><label class="sr-only" for="search-input">输入标题、技术或关键词</label><input id="search-input" type="search" placeholder="输入标题、技术或关键词…" autocomplete="off"><p id="search-status" role="status"></p><ul class="search-results">{search_items}</ul><p class="search-hint">按 Esc 关闭 · 搜索在本地完成</p></dialog>
</body></html>
"""


def _card(post: Post, index: int) -> str:
    tags = ''.join(f'<span>{html.escape(t.strip())}</span>' for t in post.tags.split(',') if t.strip())
    return f"""<article class="post-card" data-category="{html.escape(post.category, quote=True)}">
<div class="card-meta"><span>{html.escape(post.category)}</span><span class="card-number">{index:02d}</span></div>
<h3><a href="{post.path}">{html.escape(post.title)}</a></h3>
<p>{html.escape(post.description or plain_text(post.html_excerpt or post.html_body)[:150])}</p>
<div class="tags">{tags}</div><div class="card-bottom"><time datetime="{post.iso}">{post.date_text.replace('-', '.')}</time><span>约 {reading_minutes(post)} 分钟 <span class="card-arrow" aria-hidden="true">↗</span></span></div></article>"""


def index_page(posts: list[Post]) -> str:
    categories = list(dict.fromkeys(p.category for p in posts))
    filters = '<button type="button" data-filter="all" aria-pressed="true">全部手记 <span>' + str(len(posts)) + '</span></button>'
    filters += ''.join(f'<button type="button" data-filter="{html.escape(c, quote=True)}" aria-pressed="false">{html.escape(c)}</button>' for c in categories)
    cards = ''.join(_card(p, i) for i, p in enumerate(posts, 1))
    latest = posts[0].date_text.replace('-', '.') if posts else '尚无文章'
    main = f"""<section class="hero" aria-labelledby="hero-title"><div class="hero-copy"><div class="eyebrow"><span class="status-dot"></span> A PERSONAL DEVELOPMENT JOURNAL</div><h1 id="hero-title">把想法写成代码，<br>把过程留在这里<span class="accent">。</span></h1><p>你好，我是雨季少年。<br>这里记录我的开源项目、工程实践，以及那些值得拆开聊聊的技术细节。</p><a class="primary-link" href="#notes">翻开开发手记 <span aria-hidden="true">↗</span></a></div><div class="hero-art" aria-hidden="true"><div class="art-grid"></div><span class="art-label">IDEAS → CODE → NOTES</span><div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><div class="art-center">雨<span>build. learn. write.</span></div><span class="art-cross cross-one">+</span><span class="art-cross cross-two">+</span><span class="art-caption">持续构建，保持好奇。<br><span>WORK IN PROGRESS / ALWAYS</span></span></div></section>
<div class="journal-meta"><span><span class="status-dot"></span> 从真实项目中来</span><span>{len(posts):02d} 篇手记 <i>/</i> 最近更新 {latest}</span></div>
<section id="notes" class="notes-section" aria-labelledby="notes-title"><div class="section-heading"><div><span class="eyebrow">THE NOTEBOOK</span><h2 id="notes-title">最近的探索<span>每一个细节，都有来由。</span></h2></div><a href="/archives/">全部归档 ↗</a></div><div class="filters" aria-label="按主题筛选" hidden>{filters}</div><p class="sr-only" id="filter-status" role="status"></p><div class="post-grid">{cards}</div></section>
<aside class="closing-note"><span class="closing-symbol" aria-hidden="true">*</span><div><h2>代码之外，也是记录。</h2><p>好的工具从一个小问题开始，好的记录让下一次探索更容易。</p></div><a href="https://github.com/sakuradairong" target="_blank" rel="noopener">去 GitHub 看看 ↗</a></aside>"""
    return _page(SITE_TITLE, '/', main, posts)


def post_page(post: Post, posts: list[Post]) -> str:
    idx = posts.index(post)
    toc = ''.join(f'<li class="toc-level-{level}"><a href="#{anchor}">{html.escape(plain_text(body))}</a></li>'
                  for level, anchor, body in re.findall(r'<h([23]) id="([^"]+)">(.*?)</h[23]>', post.html_body, re.DOTALL))
    neighbours = ''
    for label, neighbour in [('← 较新一篇', posts[idx - 1] if idx else None),
                              ('较早一篇 →', posts[idx + 1] if idx + 1 < len(posts) else None)]:
        if neighbour:
            neighbours += f'<a href="{neighbour.path}"><span>{label}</span>{html.escape(neighbour.title)}</a>'
    main = f"""<div class="breadcrumb"><a href="/">手记</a><span>/</span>{html.escape(post.category)}</div><div class="reading-layout"><article class="reading-article"><header class="post-header"><div class="eyebrow">{html.escape(post.category)} / DEVELOPMENT NOTES</div><h1>{html.escape(post.title)}</h1><div class="post-meta"><span>雨季少年</span><time datetime="{post.iso}">{post.date_text}</time><span>约 {reading_minutes(post)} 分钟</span></div><p class="post-description">{html.escape(post.description)}</p></header><div class="article-entry">{post.html_body}</div><footer class="article-footer"><a href="/">← 返回所有手记</a><button type="button" data-copy-url hidden>复制文章链接</button><span id="copy-status" role="status"></span></footer><nav class="article-nav" aria-label="相邻文章">{neighbours}</nav></article><aside class="reading-sidebar"><nav class="toc" aria-label="文章目录"><span class="eyebrow">ON THIS PAGE</span><h2>文章目录</h2><ol>{toc}</ol><a class="back-top" href="#main">回到顶部 ↑</a></nav></aside></div>"""
    return _page(f'{post.title} | {SITE_TITLE}', post.path, main, posts, 'article', post.description, post.iso)


def archive_page(title: str, sections: list[tuple[int, int | None]], posts: list[Post]) -> str:
    path = '/archives/'
    if title.startswith('Archives:') and sections:
        year, month = sections[0]
        path += f'{year}/' + (f'{month:02d}/' if month else '')
    blocks = ''
    count = 0
    for year, month in sections:
        selected = [p for p in posts if p.dt.year == year and (month is None or p.dt.month == month)]
        count += len(selected)
        rows = ''.join(f'<a class="archive-row" href="{p.path}"><time datetime="{p.iso}">{p.dt.strftime("%m.%d")}</time><h3>{html.escape(p.title)}</h3><span>{html.escape(p.category)}</span><span aria-hidden="true">↗</span></a>' for p in selected)
        blocks += f'<section class="archive-group"><h2><a href="/archives/{year}/">{year}</a><span>{len(selected)} 篇手记</span></h2>{rows}</section>'
    scope = '全部归档' if path == '/archives/' else f'{year} 年' + (f' {month} 月' if month else '')
    main = f'<section class="archive-intro"><span class="eyebrow">THE ARCHIVE</span><h1>时间里的脚印<span class="accent">。</span></h1><p>{scope} · 共 {count} 篇手记，每一次探索都有迹可循。</p><a href="/archives/">浏览全部归档 ↗</a></section>{blocks}'
    return _page(f'{scope} | {SITE_TITLE}', path, main, posts)


# --------------------------------------------------------------------------
# atom feed
# --------------------------------------------------------------------------
def atom_xml(posts: list[Post]) -> str:
    updated = posts[0].iso if posts else "2020-04-04T21:37:26.685Z"
    entries = []
    for post in posts[:20]:
        url = f"{SITE_URL}{post.path}"
        summary = post.description or post.title
        body = (post.html_excerpt or post.html_body).replace("]]>", "]]&gt;")
        entries.append(
            f"""  <entry>
    <title>{html.escape(post.title)}</title>
    <link href="{url}" rel="alternate"/>
    <id>{url}</id>
    <published>{post.iso}</published>
    <updated>{post.iso}</updated>
    <summary>{html.escape(summary)}</summary>
    <content type="html"><![CDATA[{body}]]></content>
  </entry>
"""
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        f"  <title>{SITE_TITLE}</title>\n"
        f'  <link href="{SITE_URL}/atom.xml" rel="self"/>\n'
        f'  <link href="{SITE_URL}/"/>\n'
        f"  <updated>{updated}</updated>\n"
        f"  <id>{SITE_URL}/</id>\n"
        f"  <author><name>{SITE_AUTHOR}</name></author>\n"
        f'  <generator>{GENERATOR}</generator>\n'
        + "".join(entries)
        + "</feed>\n"
    )


# --------------------------------------------------------------------------
# favicon (生成了一个简单的雨滴图标，替代原来 404 的 /favicon.png)
# --------------------------------------------------------------------------
def _favicon_png(size: int = 32) -> bytes:
    bg = (15, 118, 110)
    fg = (250, 250, 250)
    cx, cy, radius = size / 2, size * 0.62, size * 0.27
    apex_y, base_y = size * 0.14, cy
    half_base = radius

    def inside(x: float, y: float) -> bool:
        if (x - cx) ** 2 + (y - cy) ** 2 <= radius**2:
            return True
        if apex_y <= y <= base_y:
            t = (y - apex_y) / (base_y - apex_y)
            return abs(x - cx) <= half_base * t
        return False

    rows = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            hits = 0
            samples = 4
            for sy in range(samples):
                for sx in range(samples):
                    x = px + (sx + 0.5) / samples
                    y = py + (sy + 0.5) / samples
                    if inside(x, y):
                        hits += 1
            ratio = hits / (samples * samples)
            r = round(bg[0] * (1 - ratio) + fg[0] * ratio)
            g = round(bg[1] * (1 - ratio) + fg[1] * ratio)
            b = round(bg[2] * (1 - ratio) + fg[2] * ratio)
            row.extend((r, g, b, 255))
        rows.append(bytes(row))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + row for row in rows)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------
def build() -> tuple[list[Path], list[Path]]:
    posts = load_posts()
    written: list[Path] = []

    def write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)

    write(ROOT / "index.html", index_page(posts))

    for post in posts:
        write(
            ROOT
            / f"{post.dt.year}"
            / f"{post.dt.month:02d}"
            / f"{post.dt.day:02d}"
            / post.slug
            / "index.html",
            post_page(post, posts),
        )

    years = sorted({p.dt.year for p in posts}, reverse=True)
    write(
        ROOT / "archives" / "index.html",
        archive_page(
            f"Archives | {SITE_TITLE}",
            [(y, None) for y in years],
            posts,
        ),
    )
    for year in years:
        write(
            ROOT / "archives" / str(year) / "index.html",
            archive_page(
                f"Archives: {year} | {SITE_TITLE}", [(year, None)], posts
            ),
        )
        months = sorted(
            {p.dt.month for p in posts if p.dt.year == year}, reverse=True
        )
        for month in months:
            write(
                ROOT / "archives" / str(year) / f"{month:02d}" / "index.html",
                archive_page(
                    f"Archives: {year}/{month} | {SITE_TITLE}",
                    [(year, month)],
                    posts,
                ),
            )

    write(ROOT / "atom.xml", atom_xml(posts))
    (ROOT / "favicon.png").write_bytes(_favicon_png(32))
    written.append(ROOT / "favicon.png")

    removed = _cleanup({p for p in written if p.name == "index.html"})
    return written, removed


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


class _TagBalance(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        if not self.stack:
            self.errors.append(f"unexpected </{tag}>")
        elif self.stack[-1] != tag:
            self.errors.append(f"mismatched </{tag}> (open: {self.stack[-1]})")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
        else:
            self.stack.pop()


def _html_files() -> list[Path]:
    files = [ROOT / "index.html"]
    files += sorted(ROOT.glob("20*/*/*/*/index.html"))
    files += sorted(ROOT.glob("archives/**/index.html"))
    return [f for f in files if f.exists()]


def _cleanup(expected: set[Path]) -> list[Path]:
    """Remove generated pages whose sources are gone (deleted posts/archives)."""
    removed: list[Path] = []
    for path in _html_files():
        if path in expected:
            continue
        path.unlink()
        removed.append(path)
        parent = path.parent
        while parent != ROOT and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    return removed


def check() -> int:
    posts = load_posts()
    problems: list[str] = []
    html_files = _html_files()

    for path in html_files:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT)

        parser = _TagBalance()
        parser.feed(text)
        parser.close()
        if parser.stack:
            problems.append(f"{rel}: unclosed tags {parser.stack}")
        for err in parser.errors:
            problems.append(f"{rel}: {err}")

        if "<title>" not in text or "og:url" not in text:
            problems.append(f"{rel}: missing head metadata")

        for href in re.findall(r'href="(/(?!/)(?!#)[^":]*)"', text):
            target = ROOT / href.lstrip("/")
            if href.endswith("/"):
                target = target / "index.html"
            if not target.exists():
                problems.append(f"{rel}: broken internal link {href}")

        for src in re.findall(r'src="(/(?!/)[^"]+)"', text):
            if not (ROOT / src.lstrip("/")).exists():
                problems.append(f"{rel}: broken asset {src}")

    # Verify discovery in the visible content, excluding the shared search dialog.
    discovery_pages = [ROOT / "index.html", ROOT / "archives" / "index.html"]
    discovery_html = {p: p.read_text(encoding="utf-8").split("</main>")[0]
                      for p in discovery_pages if p.exists()}
    # every post must be reachable from index + archives + atom
    for post in posts:
        for discovery_page in discovery_pages:
            if f'href="{post.path}"' not in discovery_html.get(discovery_page, ""):
                problems.append(f"{discovery_page.relative_to(ROOT)}: missing article link {post.path}")
        page = ROOT / post.path.lstrip("/") / "index.html"
        if not page.exists():
            problems.append(f"missing post page {post.path}")

    atom = (ROOT / "atom.xml").read_text(encoding="utf-8")
    for post in posts[:20]:
        if f"{SITE_URL}{post.path}" not in atom:
            problems.append(f"atom.xml missing entry for {post.path}")

    print(f"validated {len(html_files)} html files, {len(posts)} posts")
    if problems:
        print(f"FAIL: {len(problems)} problem(s)")
        for item in problems:
            print("  -", item)
        return 1
    print("OK: markup balanced, all internal links and assets resolve")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="only validate")
    args = ap.parse_args(argv)

    if not args.check:
        written, removed = build()
        print(f"built {len(written)} files")
        for path in written:
            print("   ", path.relative_to(ROOT))
        if removed:
            print(f"removed {len(removed)} stale file(s)")
            for path in removed:
                print("   -", path.relative_to(ROOT))
    return check()


if __name__ == "__main__":
    sys.exit(main())
