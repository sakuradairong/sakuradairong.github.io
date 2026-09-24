#!/usr/bin/env python3
"""Static site generator for https://sakuradairong.github.io

The original Hexo source repository (hexo-blog-fly) no longer exists - only the
generated HTML was left on the master branch.  This script rebuilds the site
from Markdown sources in content/_posts, recreating the look of the original
Hexo Landscape theme (full-bleed css/images/banner.jpg hero with the site title,
article column plus sidebar) with this repository's own HTML, CSS and
JavaScript.  Existing article URLs and Markdown content are preserved.

Usage:
    python3 tools/build_site.py            # write the site into the repo root
    python3 tools/build_site.py --check    # post-generation link/tag validation
"""

from __future__ import annotations

import argparse
import html
import json
import math
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
SITE_TAGLINE = "把想法写成代码，把过程留在这里。"
SITE_DESCRIPTION = "雨季少年的开源项目与开发手记：AstrBot 插件、Rime 输入法、Tauri 桌面端、Go 服务与前端可视化。"
GITHUB_URL = "https://github.com/sakuradairong"
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
# small view helpers
# --------------------------------------------------------------------------
def plain_text(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value))


def reading_minutes(post: Post) -> int:
    return max(1, round(len(plain_text(post.html_body)) / 500))


def post_tags(post: Post) -> list[str]:
    return [tag.strip() for tag in post.tags.split(",") if tag.strip()]


def post_summary(post: Post, limit: int = 150) -> str:
    text = re.sub(r"\s+", " ", post.description or plain_text(post.html_excerpt or post.html_body)).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip("，,。.、 ") + "…"


def category_counts(posts: list[Post]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for post in posts:
        counts[post.category] = counts.get(post.category, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def tag_counts(posts: list[Post]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for post in posts:
        for tag in post_tags(post):
            counts[tag] = counts.get(tag, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def archive_tree(posts: list[Post]) -> list[tuple[int, int, list[tuple[int, int]]]]:
    """[(year, total, [(month, count), ...]), ...] newest first."""
    months_by_year: dict[int, dict[int, int]] = {}
    for post in posts:
        months = months_by_year.setdefault(post.dt.year, {})
        months[post.dt.month] = months.get(post.dt.month, 0) + 1
    return [
        (year, sum(months.values()), sorted(months.items(), reverse=True))
        for year, months in sorted(months_by_year.items(), reverse=True)
    ]


def site_stats(posts: list[Post]) -> dict[str, int]:
    return {
        "posts": len(posts),
        "categories": len(category_counts(posts)),
        "tags": len(tag_counts(posts)),
    }


def _polar(cx: float, cy: float, radius: float, degrees: float) -> str:
    """SVG 坐标（y 轴向下），返回 "x y" 坐标串。"""
    angle = math.radians(degrees)
    return f"{cx + radius * math.cos(angle):.1f} {cy + radius * math.sin(angle):.1f}"


ICON_SEARCH = (
    '<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false">'
    '<circle cx="8.7" cy="8.7" r="5.1" fill="none" stroke="currentColor" stroke-width="1.6"/>'
    '<path d="M12.6 12.6 17 17" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>'
    "</svg>"
)
ICON_SUN = (
    '<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false" class="icon-sun">'
    '<circle cx="10" cy="10" r="3.9" fill="currentColor"/>'
    '<path d="M10 1.5V4M10 16v2.5M1.5 10H4M16 10h2.5M4 4l1.8 1.8M14.2 14.2 16 16M16 4l-1.8 1.8M5.8 14.2 4 16" '
    'fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>'
    "</svg>"
)
ICON_MOON = (
    '<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false" class="icon-moon">'
    '<path d="M15.8 13.2A6.6 6.6 0 0 1 7 4.3a6.8 6.8 0 1 0 8.8 8.9Z" fill="currentColor"/>'
    "</svg>"
)
ICON_TOP = (
    '<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false">'
    '<path d="M10 16.5V4M4.6 9.4 10 4l5.4 5.4" fill="none" stroke="currentColor" '
    'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>'
    "</svg>"
)
ICON_ARROW = (
    '<svg viewBox="0 0 20 20" width="14" height="14" aria-hidden="true" focusable="false">'
    '<path d="M4 10h11M10.4 5.2 15.6 10l-5.2 4.8" fill="none" stroke="currentColor" '
    'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>'
    "</svg>"
)
# 首屏主题选择脚本：内联执行，避免深色模式闪烁；读写失败时静默回退。
THEME_BOOT = (
    "(function(){try{var s=localStorage.getItem('rain-theme');"
    "var m=window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches;"
    "var t=(s==='dark'||s==='light')?s:(m?'dark':'light');"
    "var r=document.documentElement;r.setAttribute('data-theme',t);"
    "if(s==='dark'||s==='light'){r.setAttribute('data-theme-choice','user');}"
    "}catch(e){}})();"
)


def _topbar(path: str) -> str:
    home_current = ' aria-current="page"' if path == "/" else ""
    archive_current = ' aria-current="page"' if path.startswith("/archives/") else ""
    return f"""<nav class="topbar" aria-label="主导航">
<div class="topbar-inner wrap">
<a class="topbar-brand" href="/"><span class="brand-mark" aria-hidden="true">雨</span><span class="brand-text">雨季少年</span></a>
<div class="topbar-actions">
<a class="nav-link" href="/"{home_current}>首页</a>
<a class="nav-link" href="/archives/"{archive_current}>归档</a>
<a class="nav-link nav-ext" href="{GITHUB_URL}" target="_blank" rel="noopener">GitHub<span class="nav-ext-mark" aria-hidden="true">↗</span></a>
<button class="icon-btn" type="button" data-open-search aria-label="搜索文章" hidden>{ICON_SEARCH}</button>
<button class="icon-btn theme-toggle" type="button" data-theme-toggle aria-label="切换到深色模式" aria-pressed="false" hidden>{ICON_SUN}{ICON_MOON}</button>
</div>
</div>
<div class="topbar-progress" aria-hidden="true"><span data-progress></span></div>
</nav>"""


def _hero_home(posts: list[Post]) -> str:
    stats = site_stats(posts)
    latest = posts[0].date_text.replace("-", ".") if posts else "尚无文章"
    return f"""<header class="hero hero--home">
<div class="hero-media" aria-hidden="true"></div>
<div class="hero-inner wrap">
<p class="hero-kicker">RAIN NOTES · 代码与生活的切片</p>
<h1 class="hero-title"><a href="/">雨季少年的博客</a></h1>
<p class="hero-sub">{SITE_TAGLINE}</p>
<ul class="hero-facts">
<li><strong>{stats["posts"]}</strong> 篇文章</li>
<li><strong>{stats["categories"]}</strong> 个分类</li>
<li><strong>{stats["tags"]}</strong> 个标签</li>
<li>最近更新 {latest}</li>
</ul>
</div>
</header>"""


def _hero_page(breadcrumb: str, title: str, meta: str, kind: str = "page") -> str:
    return f"""<header class="hero hero--page hero--{kind}">
<div class="hero-media" aria-hidden="true"></div>
<div class="hero-inner wrap">
<nav class="breadcrumb" aria-label="面包屑">{breadcrumb}</nav>
<h1 class="hero-title">{title}</h1>
<p class="hero-sub">{meta}</p>
</div>
</header>"""


def _footer(posts: list[Post]) -> str:
    year = max((post.dt.year for post in posts), default=datetime.now(TZ).year)
    return f"""<footer class="site-footer">
<div class="wrap footer-inner">
<div class="footer-brand">
<a href="/">{SITE_TITLE}</a>
<p>{SITE_TAGLINE}</p>
</div>
<nav class="footer-nav" aria-label="页脚导航">
<a href="/">首页</a>
<a href="/archives/">归档</a>
<a href="/atom.xml">RSS 订阅</a>
<a href="{GITHUB_URL}" target="_blank" rel="noopener">GitHub<span class="nav-ext-mark" aria-hidden="true">↗</span></a>
</nav>
<p class="footer-note">© 2020–{year} {SITE_AUTHOR} · 个人博客，记录代码、生活与未完成的想法</p>
</div>
</footer>"""


def _search_dialog(posts: list[Post]) -> str:
    esc = html.escape
    items = "".join(
        f'<li class="search-item" data-search="{esc(post.title + " " + post.description + " " + post.category + " " + post.tags, quote=True)}">'
        f'<a href="{post.path}"><span class="search-meta">{esc(post.category)} · {post.date_text} · 约 {reading_minutes(post)} 分钟</span>'
        f'<span class="search-title">{esc(post.title)}</span></a></li>'
        for post in posts
    )
    return f"""<dialog id="search-dialog" aria-labelledby="search-title">
<div class="search-head">
<h2 id="search-title">搜索文章</h2>
<button class="icon-btn" type="button" data-close-search aria-label="关闭搜索"><span aria-hidden="true">✕</span></button>
</div>
<div class="search-field">
<span class="search-field-icon" aria-hidden="true">{ICON_SEARCH}</span>
<label class="sr-only" for="search-input">输入标题、分类、标签或关键词</label>
<input id="search-input" type="search" placeholder="输入标题、标签或关键词…" autocomplete="off" spellcheck="false">
</div>
<p id="search-status" class="search-status" role="status"></p>
<ul class="search-results">{items}</ul>
<p class="search-hint">按 <kbd>/</kbd> 打开搜索，<kbd>Esc</kbd> 关闭；多个关键词用空格分隔，搜索在本地完成。</p>
</dialog>"""


def _page(title: str, path: str, page: str, hero: str, main: str, posts: list[Post],
          og_type: str = "website", description: str = "", published: str = "") -> str:
    esc = html.escape
    description = description or SITE_DESCRIPTION
    published_meta = (
        f'<meta property="article:published_time" content="{published}">' if published else ""
    )
    return f"""<!DOCTYPE html>
<html lang="{SITE_LANG}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
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
<script>{THEME_BOOT}</script>
<script src="/js/script.js" defer></script>
</head>
<body class="page-{page}">
<a class="skip-link" href="#main">跳到正文</a>
{_topbar(path)}
{hero}
<main id="main" class="site-main wrap" tabindex="-1">{main}</main>
{_footer(posts)}
<button class="to-top" type="button" data-to-top aria-label="回到页面顶部" hidden>{ICON_TOP}</button>
{_search_dialog(posts)}
</body></html>
"""


# --------------------------------------------------------------------------
# 内联 SVG 封面示意图（全部由本仓库代码绘制，不是项目截图，也不包含真实数据）
# --------------------------------------------------------------------------
_STARS = (
    '<g fill="#cfe4f7" opacity=".5">'
    '<circle cx="24" cy="36" r=".9"/><circle cx="58" cy="16" r="1.3"/><circle cx="96" cy="52" r="1"/>'
    '<circle cx="132" cy="22" r="1.1"/><circle cx="168" cy="60" r=".9"/><circle cx="206" cy="20" r="1.2"/>'
    '<circle cx="244" cy="50" r="1"/><circle cx="286" cy="28" r="1.3"/><circle cx="322" cy="58" r=".9"/>'
    '<circle cx="358" cy="18" r="1.1"/><circle cx="392" cy="46" r="1"/><circle cx="430" cy="72" r="1.2"/>'
    '<circle cx="462" cy="30" r=".9"/><circle cx="14" cy="120" r=".9"/><circle cx="30" cy="170" r="1.1"/>'
    '<circle cx="58" cy="110" r=".9"/><circle cx="466" cy="120" r="1"/><circle cx="452" cy="168" r=".9"/>'
    '<circle cx="36" cy="224" r="1"/><circle cx="78" cy="264" r="1.2"/><circle cx="120" cy="290" r="1"/>'
    '<circle cx="172" cy="258" r=".9"/><circle cx="216" cy="284" r="1.1"/><circle cx="264" cy="262" r="1"/>'
    '<circle cx="310" cy="286" r="1.2"/><circle cx="354" cy="256" r=".9"/><circle cx="400" cy="282" r="1"/>'
    '<circle cx="444" cy="252" r="1.1"/><circle cx="470" cy="208" r=".9"/>'
    "</g>"
)

_GLASS = 'fill="rgba(255,255,255,.06)" stroke="rgba(255,255,255,.22)" stroke-width="1.5"'


def _cover_astrbot(uid: str) -> str:
    """链接 → 解析 → 卡片。"""
    return f"""<g stroke="url(#ac-{uid})" stroke-width="3" fill="none" stroke-linecap="round">
<path d="M92 116 L74 150 L92 184"/>
<path d="M140 116 L158 150 L140 184" stroke-dasharray="7 7" opacity=".7"/>
</g>
<g transform="translate(78,150) rotate(-45)" stroke="url(#ac-{uid})" stroke-width="4" fill="none">
<rect x="-13" y="-40" width="26" height="40" rx="13"/>
<rect x="-13" y="0" width="26" height="40" rx="13"/>
</g>
<rect x="196" y="82" width="228" height="140" rx="16" {_GLASS}/>
<rect x="212" y="98" width="58" height="58" rx="10" fill="rgba(127,216,240,.22)" stroke="rgba(127,216,240,.45)" stroke-width="1.5"/>
<path d="M226 156 L238 132 L250 146 L260 122 L268 156Z" fill="rgba(127,216,240,.35)"/>
<rect x="284" y="102" width="122" height="10" rx="5" fill="rgba(255,255,255,.42)"/>
<rect x="284" y="122" width="94" height="9" rx="4.5" fill="rgba(255,255,255,.26)"/>
<rect x="284" y="140" width="108" height="9" rx="4.5" fill="rgba(255,255,255,.2)"/>
<path d="M212 174 H408" stroke="rgba(255,255,255,.16)" stroke-width="1.5"/>
<rect x="212" y="188" width="70" height="9" rx="4.5" fill="rgba(127,216,240,.5)"/>
<rect x="294" y="188" width="46" height="9" rx="4.5" fill="rgba(255,255,255,.2)"/>
<circle cx="418" cy="90" r="7" fill="url(#ac-{uid})"/>"""


def _cover_rime(uid: str) -> str:
    """词级连接：前一个词影响后一个词的排序。"""
    upper = "".join(
        f'<rect x="{x}" y="62" width="100" height="42" rx="12" {_GLASS}/>' for x in (58, 190, 322)
    )
    lower = "".join(
        f'<rect x="{x}" y="196" width="100" height="42" rx="12" '
        f'fill="{"url(#ac-" + uid + ")" if x == 322 else "rgba(255,255,255,.06)"}" '
        f'stroke="rgba(255,255,255,.22)" stroke-width="1.5"/>'
        for x in (58, 190, 322)
    )
    return f"""<rect x="190" y="62" width="100" height="42" rx="12" fill="rgba(127,216,240,.16)" stroke="url(#ac-{uid})" stroke-width="2"/>
{upper}{lower}
<path d="M240 104 C240 140 108 158 108 196" fill="none" stroke="url(#ac-{uid})" stroke-width="2.5" opacity=".55"/>
<path d="M240 104 C240 142 240 158 240 196" fill="none" stroke="url(#ac-{uid})" stroke-width="2.5" opacity=".35" stroke-dasharray="6 6"/>
<path d="M240 104 C240 140 372 158 372 196" fill="none" stroke="url(#ac-{uid})" stroke-width="3.5"/>
<rect x="104" y="148" width="24" height="6" rx="3" fill="rgba(127,216,240,.5)"/>
<rect x="350" y="148" width="44" height="6" rx="3" fill="url(#ac-{uid})"/>
<g fill="rgba(255,255,255,.34)">
<rect x="74" y="78" width="68" height="10" rx="5"/><rect x="206" y="78" width="68" height="10" rx="5"/>
<rect x="338" y="78" width="68" height="10" rx="5"/><rect x="74" y="212" width="68" height="10" rx="5"/>
<rect x="206" y="212" width="68" height="10" rx="5"/><rect x="338" y="212" width="68" height="10" rx="5"/>
</g>"""


def _cover_keystats(uid: str) -> str:
    """键盘热力图：色阶示意，不代表真实统计。"""
    ramp = ("#1b3a52", "#255f7e", "#3996b1", "#7fd8f0", "#f0a95c")
    keys = []
    for row in range(4):
        for col in range(10):
            distance = abs(col - 4.4) / 4.6 * 2.6 + abs(row - 1.4) / 2.6 * 1.9
            jitter = ((col * 7 + row * 5) % 5) / 4.6
            heat = max(0, min(4, 4 - int(round(distance + jitter))))
            keys.append(
                f'<rect x="{52 + col * 37}" y="{80 + row * 39}" width="31" height="31" rx="7" '
                f'fill="{ramp[heat]}"/>'
            )
    legend = "".join(
        f'<rect x="{244 + index * 24}" y="246" width="15" height="10" rx="3" fill="{color}"/>'
        for index, color in enumerate(ramp)
    )
    key_svg = "".join(keys)
    return f"""<ellipse cx="240" cy="152" rx="206" ry="116" fill="url(#gl-{uid})"/>
{key_svg}
<rect x="52" y="80" width="31" height="31" rx="7" fill="none" stroke="url(#ac-{uid})" stroke-width="2.5"/>
{legend}"""


def _cover_smartstrm(uid: str) -> str:
    """服务拓扑：节点、连线与签名播放。"""
    boxes = (
        ('rect x="62" y="66" width="104" height="58" rx="14"'),
        ('rect x="314" y="66" width="104" height="58" rx="14"'),
        ('rect x="62" y="182" width="104" height="58" rx="14"'),
        ('rect x="314" y="182" width="104" height="58" rx="14"'),
    )
    nodes = "".join(f"<{box} {_GLASS}/>" for box in boxes)
    return f"""<ellipse cx="240" cy="152" rx="150" ry="106" fill="url(#gl-{uid})"/>
<path d="M166 96 L206 130" stroke="rgba(255,255,255,.3)" stroke-width="2" fill="none"/>
<path d="M314 96 L274 130" stroke="rgba(255,255,255,.3)" stroke-width="2" fill="none"/>
<path d="M166 210 L206 172" stroke="rgba(127,216,240,.6)" stroke-width="2" fill="none" stroke-dasharray="6 6"/>
<path d="M314 210 L274 172" stroke="rgba(255,255,255,.3)" stroke-width="2" fill="none"/>
{nodes}
<rect x="186" y="118" width="108" height="64" rx="18" fill="url(#ac-{uid})"/>
<path d="M214 150 h52 M240 134 v32" stroke="#082332" stroke-width="3" stroke-linecap="round" fill="none"/>
<path d="M198 138 l9 -4 -2 10Z" fill="rgba(255,255,255,.5)"/>
<path d="M282 138 l-9 -4 2 10Z" fill="rgba(255,255,255,.5)"/>
<path d="M198 162 l7 6 -10 1Z" fill="rgba(127,216,240,.85)"/>
<path d="M282 162 l-7 6 10 1Z" fill="rgba(255,255,255,.5)"/>
<g fill="rgba(255,255,255,.34)">
<rect x="96" y="86" width="36" height="10" rx="5"/><rect x="348" y="86" width="36" height="10" rx="5"/>
<rect x="96" y="202" width="36" height="10" rx="5"/><rect x="348" y="202" width="36" height="10" rx="5"/>
</g>"""


def _cover_bilidesk(uid: str) -> str:
    """播放符号与字幕轨道。"""
    return f"""<rect x="86" y="70" width="308" height="168" rx="18" {_GLASS}/>
<g fill="rgba(127,216,240,.45)">
<rect x="104" y="88" width="72" height="7" rx="3.5"/><rect x="188" y="88" width="52" height="7" rx="3.5"/>
<rect x="252" y="88" width="86" height="7" rx="3.5" opacity=".55"/>
</g>
<circle cx="240" cy="150" r="40" fill="url(#ac-{uid})"/>
<path d="M231 134 L258 150 L231 166Z" fill="#082332"/>
<g fill="rgba(255,255,255,.42)">
<rect x="120" y="128" width="62" height="8" rx="4"/><rect x="120" y="148" width="44" height="8" rx="4"/>
<rect x="300" y="128" width="62" height="8" rx="4"/><rect x="300" y="148" width="52" height="8" rx="4"/>
</g>
<rect x="110" y="208" width="260" height="7" rx="3.5" fill="rgba(255,255,255,.2)"/>
<rect x="110" y="208" width="132" height="7" rx="3.5" fill="url(#ac-{uid})"/>
<circle cx="242" cy="211.5" r="9" fill="#eaf7fd"/>"""


def _cover_tiez(uid: str) -> str:
    """剪贴板堆叠与加密标记。"""
    return f"""<rect x="118" y="70" width="168" height="132" rx="16" fill="rgba(255,255,255,.05)" stroke="rgba(255,255,255,.16)" stroke-width="1.5"/>
<rect x="138" y="90" width="168" height="132" rx="16" fill="rgba(255,255,255,.09)" stroke="rgba(255,255,255,.2)" stroke-width="1.5"/>
<rect x="158" y="110" width="168" height="132" rx="16" fill="rgba(17,38,58,.92)" stroke="rgba(255,255,255,.3)" stroke-width="1.5"/>
<rect x="204" y="102" width="76" height="16" rx="8" fill="url(#ac-{uid})"/>
<g fill="rgba(255,255,255,.34)">
<rect x="180" y="142" width="112" height="10" rx="5"/>
<rect x="180" y="164" width="86" height="10" rx="5"/>
<rect x="180" y="186" width="124" height="10" rx="5"/>
<rect x="180" y="208" width="64" height="10" rx="5"/>
</g>
<circle cx="364" cy="214" r="30" fill="url(#ac-{uid})"/>
<path d="M354 216 v-9 a10 10 0 0 1 20 0 v9" fill="none" stroke="#082332" stroke-width="4" stroke-linecap="round"/>
<rect x="349" y="214" width="30" height="24" rx="7" fill="#082332"/>
<circle cx="364" cy="224" r="3.4" fill="url(#ac-{uid})"/>"""


def _cover_truckdeck(uid: str) -> str:
    """遥测仪表与手机控制端。"""
    ticks = "".join(
        f'<path d="M{_polar(150, 150, 60, angle)} L{_polar(150, 150, 69, angle)}" '
        f'stroke="rgba(255,255,255,.34)" stroke-width="2.5" stroke-linecap="round"/>'
        for angle in (200, 217.5, 235, 252.5, 270, 287.5, 305, 322.5, 340)
    )
    return f"""<path d="M80.5 124.7 A74 74 0 0 1 219.5 124.7" fill="none" stroke="rgba(255,255,255,.2)" stroke-width="13" stroke-linecap="round"/>
<path d="M80.5 124.7 A74 74 0 0 1 137.2 77.1" fill="none" stroke="url(#ac-{uid})" stroke-width="13" stroke-linecap="round"/>
{ticks}
<path d="M150 150 L176 84" stroke="#eaf7fd" stroke-width="4" stroke-linecap="round"/>
<circle cx="150" cy="150" r="9" fill="#eaf7fd"/>
<rect x="300" y="66" width="106" height="172" rx="20" {_GLASS}/>
<rect x="312" y="86" width="82" height="104" rx="10" fill="rgba(17,38,58,.9)" stroke="rgba(255,255,255,.16)" stroke-width="1.5"/>
<g fill="rgba(127,216,240,.65)">
<rect x="324" y="98" width="34" height="8" rx="4"/><rect x="324" y="114" width="58" height="8" rx="4"/>
</g>
<rect x="324" y="150" width="58" height="6" rx="3" fill="rgba(255,255,255,.2)"/>
<rect x="324" y="150" width="38" height="6" rx="3" fill="url(#ac-{uid})"/>
<g fill="rgba(255,255,255,.24)">
<rect x="316" y="204" width="34" height="24" rx="7"/><rect x="358" y="204" width="34" height="24" rx="7"/>
</g>
<g stroke="url(#ac-{uid})" stroke-width="3" fill="none" stroke-linecap="round">
<path d="M236 96 a34 34 0 0 1 24 10"/><path d="M244 112 a22 22 0 0 1 14 7"/>
</g>
<circle cx="262" cy="126" r="3.6" fill="url(#ac-{uid})"/>"""


COVER_ART = {
    "astrbot-plugin-linuxsb": _cover_astrbot,
    "rime-context-filter-bigram": _cover_rime,
    "keystats-3d-heatmap": _cover_keystats,
    "smartstrm-cleanroom-go": _cover_smartstrm,
    "bilidesk-tauri-libmpv": _cover_bilidesk,
    "tiez-clipboard-tauri": _cover_tiez,
    "truckdeck-telemetry-control": _cover_truckdeck,
}


def _cover(post: Post) -> str:
    """首页卡片封面：本站代码绘制的主题示意图，不是项目截图。"""
    uid = re.sub(r"[^a-z0-9]+", "-", post.slug.lower()).strip("-") or "post"
    art = COVER_ART.get(post.slug, _cover_smartstrm)
    return f"""<div class="cover" aria-hidden="true">
<svg viewBox="0 0 480 300" preserveAspectRatio="xMidYMid slice" focusable="false">
<defs>
<linearGradient id="bg-{uid}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0a1725"/><stop offset="1" stop-color="#152c42"/></linearGradient>
<linearGradient id="ac-{uid}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#7fd8f0"/><stop offset="1" stop-color="#2ec4b6"/></linearGradient>
<radialGradient id="gl-{uid}" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="#6fd3ef" stop-opacity=".3"/><stop offset="1" stop-color="#6fd3ef" stop-opacity="0"/></radialGradient>
</defs>
<rect width="480" height="300" fill="url(#bg-{uid})"/>
{_STARS}
<path d="M0 246 Q240 208 480 246 L480 300 L0 300Z" fill="rgba(8,24,38,.72)"/>
{art(uid)}
</svg>
<span class="cover-tag">封面示意图</span>
</div>"""


def _post_item(post: Post, featured: bool = False) -> str:
    esc = html.escape
    tags = "".join(f"<li>{esc(tag)}</li>" for tag in post_tags(post))
    classes = "post-item post-item--feature" if featured else "post-item"
    mark = '<span class="post-mark">最新</span>' if featured else ""
    return f"""<article class="{classes}" data-item data-category="{esc(post.category, quote=True)}">
{_cover(post)}
<div class="post-body">
<p class="post-flags"><span class="post-cat">{esc(post.category)}</span>{mark}</p>
<h3 class="post-title"><a class="post-link" href="{post.path}">{esc(post.title)}</a></h3>
<p class="post-summary">{esc(post_summary(post))}</p>
<ul class="post-tags">{tags}</ul>
<p class="post-foot"><time datetime="{post.iso}">{post.date_text}</time><span class="post-sep" aria-hidden="true">·</span><span>约 {reading_minutes(post)} 分钟</span><span class="post-more">阅读全文{ICON_ARROW}</span></p>
</div>
</article>"""


def _sidebar(posts: list[Post], context: str, toc_items: str = "") -> str:
    """右侧栏：作者、真实计数、分类、归档、近期文章、（文章页）目录。"""
    esc = html.escape
    stats = site_stats(posts)
    widgets: list[str] = []

    if toc_items:
        widgets.append(f"""<section class="widget widget-toc">
<h2 class="widget-title">目录</h2>
<nav aria-label="文章目录"><ol class="toc-list">{toc_items}</ol></nav>
<a class="widget-more" href="#main">回到文章顶部 ↑</a>
</section>""")

    author_actions = (
        f'<a class="btn-ghost" href="{GITHUB_URL}" target="_blank" rel="noopener">GitHub<span aria-hidden="true">↗</span></a>'
        '<a class="btn-ghost" href="/atom.xml">RSS 订阅</a>'
    )
    widgets.append(f"""<section class="widget widget-author">
<div class="author-top">
<img class="author-avatar" src="/css/images/avatar.svg" alt="" width="60" height="60" loading="lazy" decoding="async">
<div class="author-id">
<h2 class="widget-title">雨季少年</h2>
<p class="author-handle">@sakuradairong</p>
</div>
</div>
<p class="author-bio">把想法写成代码，把过程留在这里。记录开源项目、工程实践，以及那些值得拆开聊的技术细节。</p>
<ul class="author-stats">
<li><strong>{stats["posts"]}</strong><span>文章</span></li>
<li><strong>{stats["categories"]}</strong><span>分类</span></li>
<li><strong>{stats["tags"]}</strong><span>标签</span></li>
</ul>
{author_actions}
</section>""")

    anchor = "#timeline" if context == "archive" else "#notes"
    if context in ("home", "archive"):
        cat_rows = "".join(
            f'<li><a href="{anchor}" data-filter-value="{esc(name, quote=True)}">'
            f'<span class="widget-label">{esc(name)}</span>'
            f'<span class="widget-count">{count}</span></a></li>'
            for name, count in category_counts(posts)
        )
        # 无 JS 时隐藏筛选提示：此时分类锚点仍可跳到对应列表，只是不筛选。
        hint = ('<p class="widget-hint" data-filter-hint hidden>'
                '点击分类可筛选当前页面的文章列表。</p>')
    else:
        cat_rows = "".join(
            f'<li><span class="widget-label">{esc(name)}</span>'
            f'<span class="widget-count">{count}</span></li>'
            for name, count in category_counts(posts)
        )
        hint = ""
    widgets.append(f"""<section class="widget widget-cats">
<h2 class="widget-title">分类</h2>
<ul class="widget-list">{cat_rows}</ul>
{hint}
</section>""")

    archive_rows = "".join(
        f'<li><a href="/archives/{year}/"><span class="widget-label">{year} 年</span>'
        f'<span class="widget-count">{total}</span></a>'
        + (
            '<ul class="archive-months">'
            + "".join(
                f'<li><a href="/archives/{year}/{month:02d}/"><span class="widget-label">{month:02d} 月</span>'
                f'<span class="widget-count">{count}</span></a></li>'
                for month, count in months
            )
            + "</ul>"
        )
        + "</li>"
        for year, total, months in archive_tree(posts)
    )
    widgets.append(f"""<section class="widget widget-archive">
<h2 class="widget-title">归档</h2>
<ul class="widget-list widget-list--tree">{archive_rows}</ul>
<a class="widget-more" href="/archives/">全部归档<span aria-hidden="true">↗</span></a>
</section>""")

    recent_rows = "".join(
        f'<li><a href="{item.path}"><time datetime="{item.iso}">{item.date_text}</time>'
        f'<span class="recent-title">{esc(item.title)}</span></a></li>'
        for item in posts[:5]
    )
    widgets.append(f"""<section class="widget widget-recent">
<h2 class="widget-title">最近文章</h2>
<ol class="recent-list">{recent_rows}</ol>
</section>""")

    return f'<aside class="sidebar sidebar--{context}">' + "".join(widgets) + "</aside>"


def _filter_chips(posts: list[Post]) -> str:
    esc = html.escape
    chips = (
        f'<button class="chip" type="button" data-filter="all" aria-pressed="true">'
        f'全部<span class="chip-count">{len(posts)}</span></button>'
    )
    chips += "".join(
        f'<button class="chip" type="button" data-filter="{esc(name, quote=True)}" aria-pressed="false">'
        f"{esc(name)}<span class=\"chip-count\">{count}</span></button>"
        for name, count in category_counts(posts)
    )
    return chips


def index_page(posts: list[Post]) -> str:
    latest = posts[0].date_text.replace("-", ".") if posts else "—"
    items = "".join(_post_item(post, index == 0) for index, post in enumerate(posts))
    main = f"""<div class="page-grid" data-filter-scope>
<div class="page-main">
<section class="toolbar" id="notes" aria-labelledby="notes-title">
<div class="toolbar-head">
<h2 id="notes-title">最新文章<span class="head-note">按时间倒序</span></h2>
<p class="toolbar-meta" id="filter-status" role="status">共 {len(posts)} 篇手记 · 更新于 {latest}</p>
</div>
<div class="chips" data-filters role="group" aria-label="按分类筛选文章" hidden>{_filter_chips(posts)}</div>
</section>
<div class="post-list" data-filter-items>{items}</div>
<p class="list-end"><span aria-hidden="true">✳</span> 已经到底了，<a href="/archives/">去归档翻翻全部 {len(posts)} 篇</a></p>
</div>
{_sidebar(posts, "home")}
</div>"""
    return _page(SITE_TITLE, "/", "home", _hero_home(posts), main, posts)


def post_page(post: Post, posts: list[Post]) -> str:
    esc = html.escape
    idx = posts.index(post)
    toc_items = "".join(
        f'<li class="toc-item toc-level-{level}"><a href="#{anchor}">{esc(plain_text(body))}</a></li>'
        for level, anchor, body in re.findall(
            r'<h([23]) id="([^"]+)">(.*?)</h[23]>', post.html_body, re.DOTALL
        )
    )
    neighbours = ""
    for label, neighbour in (
        ("较新一篇", posts[idx - 1] if idx else None),
        ("较早一篇", posts[idx + 1] if idx + 1 < len(posts) else None),
    ):
        if neighbour:
            neighbours += (
                f'<a class="neighbour" href="{neighbour.path}">'
                f'<span class="neighbour-label">{label}</span>'
                f'<span class="neighbour-title">{esc(neighbour.title)}</span></a>'
            )
    tags = "".join(f"<li>{esc(tag)}</li>" for tag in post_tags(post))
    breadcrumb = (
        '<a href="/">首页</a><span class="crumb-sep" aria-hidden="true">/</span>'
        '<a href="/archives/">归档</a><span class="crumb-sep" aria-hidden="true">/</span>'
        f'<span class="crumb-current">{esc(post.category)}</span>'
    )
    meta = (
        f'<time datetime="{post.iso}">{post.date_text}</time>'
        '<span class="meta-sep" aria-hidden="true"></span>'
        f"<span>约 {reading_minutes(post)} 分钟阅读</span>"
        '<span class="meta-sep" aria-hidden="true"></span>'
        f"<span>{esc(post.category)}</span>"
    )
    lede = f'<p class="post-lede">{esc(post.description)}</p>' if post.description else ""
    mobile_toc = (
        f'<details class="toc-mobile"><summary>文章目录<span class="toc-mobile-count">{len(re.findall(r"<h[23] ", post.html_body))} 节</span></summary>'
        f'<nav aria-label="文章目录"><ol class="toc-list">{toc_items}</ol></nav></details>'
        if toc_items
        else ""
    )
    main = f"""<div class="page-grid page-grid--post" data-filter-scope>
<div class="page-main">
<article class="post">
{mobile_toc}
{lede}
<div class="article-entry">{post.html_body}</div>
<footer class="article-footer">
<ul class="post-tags post-tags--footer">{tags}</ul>
<div class="article-actions">
<a class="btn-ghost" href="/">← 全部手记</a>
<button class="btn-ghost" type="button" data-copy-url hidden>复制文章链接</button>
<span id="copy-status" class="copy-status" role="status"></span>
</div>
</footer>
<nav class="article-nav" aria-label="相邻文章">{neighbours}</nav>
</article>
</div>
{_sidebar(posts, "post", toc_items)}
</div>"""
    return _page(
        f"{post.title} | {SITE_TITLE}",
        post.path,
        "post",
        _hero_page(breadcrumb, esc(post.title), meta, "post"),
        main,
        posts,
        "article",
        post.description,
        post.iso,
    )


def archive_page(path: str, sections: list[tuple[int, int | None]], posts: list[Post]) -> str:
    esc = html.escape
    match = re.match(r"^/archives/(?:(\d{4})/(?:(\d{2})/)?)?$", path)
    year = int(match.group(1)) if match and match.group(1) else None
    month = int(match.group(2)) if match and match.group(2) else None

    separator = '<span class="crumb-sep" aria-hidden="true">/</span>'
    crumbs = ['<a href="/">首页</a>', separator]
    if year is None:
        crumbs.append('<span class="crumb-current">归档</span>')
        scope = "全部年份"
    elif month is None:
        crumbs.append('<a href="/archives/">归档</a>')
        crumbs.append(separator)
        crumbs.append(f'<span class="crumb-current">{year} 年</span>')
        scope = f"{year} 年"
    else:
        crumbs.append('<a href="/archives/">归档</a>')
        crumbs.append(separator)
        crumbs.append(f'<a href="/archives/{year}/">{year} 年</a>')
        crumbs.append(separator)
        crumbs.append(f'<span class="crumb-current">{month:02d} 月</span>')
        scope = f"{year} 年 {month:02d} 月"

    scoped = [
        post
        for post in posts
        if (year is None or post.dt.year == year) and (month is None or post.dt.month == month)
    ]

    groups = ""
    for group_year, group_month in sections:
        rows = [
            post
            for post in scoped
            if post.dt.year == group_year and (group_month is None or post.dt.month == group_month)
        ]
        items = "".join(
            f'<li class="tl-item" data-item data-category="{esc(post.category, quote=True)}">'
            f'<time datetime="{post.iso}"><span class="tl-month">{post.dt.month:02d} 月</span>'
            f'<span class="tl-day">{post.dt.day:02d}</span></time>'
            f'<div class="tl-body"><h3><a href="{post.path}">{esc(post.title)}</a></h3>'
            f'<p class="tl-meta">{esc(post.category)}<span aria-hidden="true">·</span>约 {reading_minutes(post)} 分钟</p></div>'
            f'<span class="tl-arrow" aria-hidden="true">{ICON_ARROW}</span></li>'
            for post in rows
        )
        heading = f'<a href="/archives/{group_year}/">{group_year} 年</a>' if path != "/archives/" else f"{group_year} 年"
        groups += (
            f'<section class="tl-group" data-group>'
            f'<h2 class="tl-head">{heading}<span class="tl-count" data-count>{len(rows)} 篇</span></h2>'
            f'<ol class="tl-list">{items}</ol></section>'
        )

    total = len(scoped)
    meta = f"{esc(scope)} · 共 {total} 篇手记，按时间倒序排列。"
    main = f"""<div class="page-grid" data-filter-scope>
<div class="page-main">
<section class="toolbar" id="timeline" aria-labelledby="timeline-title">
<div class="toolbar-head">
<h2 id="timeline-title">时间线<span class="head-note">{esc(scope)}</span></h2>
<p class="toolbar-meta" id="filter-status" role="status">共 {total} 篇手记 · 按时间倒序</p>
</div>
<div class="chips" data-filters role="group" aria-label="按分类筛选文章" hidden>{_filter_chips(scoped)}</div>
</section>
<div class="timeline" data-filter-items>{groups}</div>
</div>
{_sidebar(posts, "archive")}
</div>"""
    return _page(
        f"归档 · {scope} | {SITE_TITLE}",
        path,
        "archive",
        _hero_page("".join(crumbs), "归档", meta, "archive"),
        main,
        posts,
    )


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
# favicon（雨滴图标，配色与头图夜空一致）
# --------------------------------------------------------------------------
def _favicon_png(size: int = 32) -> bytes:
    bg = (10, 23, 37)
    fg = (127, 216, 240)
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
        archive_page("/archives/", [(y, None) for y in years], posts),
    )
    for year in years:
        write(
            ROOT / "archives" / str(year) / "index.html",
            archive_page(f"/archives/{year}/", [(year, None)], posts),
        )
        months = sorted({p.dt.month for p in posts if p.dt.year == year}, reverse=True)
        for month in months:
            write(
                ROOT / "archives" / str(year) / f"{month:02d}" / "index.html",
                archive_page(f"/archives/{year}/{month:02d}/", [(year, month)], posts),
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

    for asset in ("css/style.css", "js/script.js", "css/images/banner.jpg", "css/images/avatar.svg"):
        if not (ROOT / asset).exists():
            problems.append(f"missing asset {asset}")

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
        if text.count("<h1") != 1:
            problems.append(f"{rel}: expected exactly one <h1>, found {text.count('<h1')}")
        if "data-theme" not in text:
            problems.append(f"{rel}: missing inline theme boot script (dark mode flash)")

        for href in re.findall(r'href="(/(?!/)(?!#)[^\":]*)"', text):
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
    print("OK: markup balanced, one h1 per page, all internal links and assets resolve")
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
