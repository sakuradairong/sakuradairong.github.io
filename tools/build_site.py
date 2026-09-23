#!/usr/bin/env python3
"""Static site generator for https://sakuradairong.github.io

The original Hexo source repository (hexo-blog-fly) no longer exists - only the
generated HTML was left on the master branch.  This script rebuilds the site
from Markdown sources in content/_posts while reproducing the markup that the
Hexo 4.2.0 + landscape theme produced, so old pages and new pages stay
visually and structurally identical.

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
GENERATOR = "build_site.py (Hexo 4.2.0 landscape compatible)"
TZ = timezone(timedelta(hours=8))
RECENT_POSTS = 5
READ_MORE = "Read more"

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
# page shell (identical markup to the 2020 Hexo output)
# --------------------------------------------------------------------------
NAV = [("Home", "/"), ("Archives", "/archives")]


def _head(
    title: str,
    path: str,
    og_type: str,
    description: str = "",
    published: str = "",
) -> str:
    desc_meta = (
        f'<meta name="description" content="{html.escape(description, quote=True)}">\n'
        if description
        else ""
    )
    og_desc = (
        f'<meta property="og:description" content="{html.escape(description, quote=True)}">\n'
        if description
        else ""
    )
    time_meta = ""
    if published:
        time_meta = (
            f'<meta property="article:published_time" content="{published}">\n'
            f'<meta property="article:modified_time" content="{published}">\n'
        )
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  

  <title>{title}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  {desc_meta}<meta property="og:type" content="{og_type}">
<meta property="og:title" content="{html.escape(SITE_TITLE if title == SITE_TITLE else title.replace(' | ' + SITE_TITLE, ''), quote=True)}">
<meta property="og:url" content="{SITE_URL}{path}">
<meta property="og:site_name" content="{SITE_TITLE}">
{og_desc}{time_meta}<meta property="article:author" content="{SITE_AUTHOR}">
<meta name="twitter:card" content="summary">
  
    <link rel="alternate" href="/atom.xml" title="{SITE_TITLE}" type="application/atom+xml">
  
  
    <link rel="icon" href="/favicon.png">
  
  
    <link href="//fonts.googleapis.com/css?family=Source+Code+Pro" rel="stylesheet" type="text/css">
  
  
<link rel="stylesheet" href="/css/style.css">

<meta name="generator" content="{GENERATOR}"></head>
"""


def _header() -> str:
    nav_links = "".join(
        f'\n          <a class="main-nav-link" href="{href}">{label}</a>\n        '
        for label, href in NAV
    )
    mobile_links = "".join(
        f'\n    <a href="{href}" class="mobile-nav-link">{label}</a>\n  '
        for label, href in NAV
    )
    return f"""<body>
  <div id="container">
    <div id="wrap">
      <header id="header">
  <div id="banner"></div>
  <div id="header-outer" class="outer">
    <div id="header-title" class="inner">
      <h1 id="logo-wrap">
        <a href="/" id="logo">{SITE_TITLE}</a>
      </h1>
      
    </div>
    <div id="header-inner" class="inner">
      <nav id="main-nav">
        <a id="main-nav-toggle" class="nav-icon"></a>
        {nav_links}
      </nav>
      <nav id="sub-nav">
        
          <a id="nav-rss-link" class="nav-icon" href="/atom.xml" title="RSS Feed"></a>
        
        <a id="nav-search-btn" class="nav-icon" title="Search"></a>
      </nav>
      <div id="search-form-wrap">
        <form action="//google.com/search" method="get" accept-charset="UTF-8" class="search-form"><input type="search" name="q" class="search-form-input" placeholder="Search"><button type="submit" class="search-form-submit">&#xF002;</button><input type="hidden" name="sitesearch" value="{SITE_URL}"></form>
      </div>
    </div>
  </div>
</header>
"""


def _sidebar(posts: list[Post]) -> str:
    months: dict[tuple[int, int], int] = {}
    for post in posts:
        key = (post.dt.year, post.dt.month)
        months[key] = months.get(key, 0) + 1
    archive_items = "".join(
        f'<li class="archive-list-item"><a class="archive-list-link" href="/archives/{year}/{month:02d}/">{MONTH_NAMES[month]} {year}</a></li>'
        for (year, month) in sorted(months, reverse=True)
    )
    recent_items = "".join(
        f"""
          <li>
            <a href="{post.path}">{html.escape(post.title)}</a>
          </li>
        """
        for post in posts[:RECENT_POSTS]
    )
    return f"""<aside id="sidebar">
  
    

  
    

  
    
  
    
  <div class="widget-wrap">
    <h3 class="widget-title">Archives</h3>
    <div class="widget">
      <ul class="archive-list">{archive_items}</ul>
    </div>
  </div>


  
    
  <div class="widget-wrap">
    <h3 class="widget-title">Recent Posts</h3>
    <div class="widget">
      <ul>
        {recent_items}
      </ul>
    </div>
  </div>

  
</aside>
"""


def _footer(posts: list[Post]) -> str:
    mobile_links = "".join(
        f'\n    <a href="{href}" class="mobile-nav-link">{label}</a>\n  '
        for label, href in NAV
    )
    return f"""      <footer id="footer">
  
  <div class="outer">
    <div id="footer-info" class="inner">
      &copy; 2020–2026 {SITE_AUTHOR}<br>
      Powered by <a href="http://hexo.io/" target="_blank">Hexo</a>
    </div>
  </div>
</footer>
    </div>
    <nav id="mobile-nav">
  {mobile_links}
</nav>
    

<script src="//ajax.googleapis.com/ajax/libs/jquery/2.0.3/jquery.min.js"></script>


  
<link rel="stylesheet" href="/fancybox/jquery.fancybox.css">

  
<script src="/fancybox/jquery.fancybox.pack.js"></script>




<script src="/js/script.js"></script>




  </div>
</body>
</html>
"""


def _page(
    title: str,
    path: str,
    main: str,
    posts: list[Post],
    og_type: str = "website",
    description: str = "",
    published: str = "",
) -> str:
    return (
        _head(title, path, og_type, description, published)
        + _header()
        + '      <div class="outer">\n        '
        + main
        + "\n      </div>\n"
        + _footer(posts)
    )


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------
def _post_id(post: Post) -> str:
    import hashlib

    return hashlib.sha1(post.path.encode("utf-8")).hexdigest()[:24]


def _article(post: Post, *, homepage: bool = False, newer=None, older=None) -> str:
    if homepage:
        entry = post.html_excerpt or post.html_body
        more = (
            '\n        <div class="article-more-link">\n'
            f'          <a href="{post.path}">{READ_MORE}</a>\n'
            "        </div>\n      "
            if post.html_excerpt
            else ""
        )
        title_html = (
            '  \n    <h1 itemprop="name">\n'
            f'      <a class="article-title" href="{post.path}">{html.escape(post.title)}</a>\n'
            "    </h1>\n  \n"
        )
    else:
        entry = post.html_body
        more = ""
        title_html = (
            "  \n"
            '    <h1 class="article-title" itemprop="name">\n'
            f"      {html.escape(post.title)}\n"
            "    </h1>\n"
            "  \n"
        )

    nav = ""
    if not homepage and (newer or older):
        newer_block = ""
        if newer is not None:
            newer_block = f"""  
    <a href="{newer.path}" id="article-nav-newer" class="article-nav-link-wrap">
      <strong class="article-nav-caption">Newer</strong>
      <div class="article-nav-title">
        
          {html.escape(newer.title)}
        
      </div>
    </a>
  """
        older_block = ""
        if older is not None:
            older_block = f"""  
    <a href="{older.path}" id="article-nav-older" class="article-nav-link-wrap">
      <strong class="article-nav-caption">Older</strong>
      <div class="article-nav-title">{html.escape(older.title)}</div>
    </a>
  """
        nav = f"""    
<nav id="article-nav">
{newer_block}
{older_block}
</nav>

  """

    meta_date = (
        f'<time datetime="{post.iso}" itemprop="datePublished">{post.date_text}</time>'
    )
    return f"""<article id="post-{post.slug}" class="article article-type-post" itemscope itemprop="blogPost">
  <div class="article-meta">
    <a href="{post.path}" class="article-date">
  {meta_date}
</a>
    
  </div>
  <div class="article-inner">
    
    
      <header class="article-header">
        {title_html}
      </header>
    
    <div class="article-entry" itemprop="articleBody">
      {entry}{more}
    </div>
    <footer class="article-footer">
      <a data-url="{SITE_URL}{post.path}" data-id="{_post_id(post)}" class="article-share-link">Share</a>
      
      
    </footer>
  </div>
  {nav}
</article>
"""


def post_page(post: Post, posts: list[Post]) -> str:
    idx = posts.index(post)
    newer = posts[idx - 1] if idx > 0 else None
    older = posts[idx + 1] if idx + 1 < len(posts) else None
    main = f"""<section id="main">{_article(post, newer=newer, older=older)}

</section>
        
          {_sidebar(posts)}"""
    return _page(
        f"{post.title} | {SITE_TITLE}",
        f"{post.path}index.html",
        main,
        posts,
        og_type="article",
        description=post.description,
        published=post.iso,
    )


def index_page(posts: list[Post]) -> str:
    articles = "".join(
        f"\n  \n    {_article(post, homepage=True)}\n\n" for post in posts
    )
    main = f"""<section id="main">{articles}
  \n

</section>
        
          {_sidebar(posts)}"""
    return _page(SITE_TITLE, "/index.html", main, posts)


def _archive_article(post: Post) -> str:
    return f"""    <article class="archive-article archive-type-post">
  <div class="archive-article-inner">
    <header class="archive-article-header">
      <a href="{post.path}" class="archive-article-date">
  <time datetime="{post.iso}" itemprop="datePublished">{post.archive_date_text}</time>
</a>
      
  
    <h1 itemprop="name">
      <a class="archive-article-title" href="{post.path}">{html.escape(post.title)}</a>
    </h1>
  

    </header>
  </div>
</article>
"""


def archive_section(year: int, posts: list[Post]) -> str:
    items = "".join(f"    \n{_archive_article(p)}  \n" for p in posts)
    return f"""      <section class="archives-wrap">
        <div class="archive-year-wrap">
          <a href="/archives/{year}" class="archive-year">{year}</a>
        </div>
        <div class="archives">
    {items}
    </div></section>
"""


def archive_page(title: str, sections: list[tuple[int, int | None]], posts: list[Post]) -> str:
    blocks = "".join(
        "  \n  \n    \n    \n      \n      \n"
        + archive_section(
            year,
            [
                p
                for p in posts
                if p.dt.year == year and (month is None or p.dt.month == month)
            ],
        )
        + "  \n"
        for year, month in sections
    )
    main = f"""<section id="main">
  {blocks}

</section>
        
          {_sidebar(posts)}"""
    return _page(title, "", main, posts)


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
        '  <generator uri="https://hexo.io/">Hexo</generator>\n'
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
def build() -> list[Path]:
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
    return written


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

    # every post must be reachable from index + archives + atom
    for post in posts:
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
        written = build()
        print(f"built {len(written)} files")
        for path in written:
            print("   ", path.relative_to(ROOT))
    return check()


if __name__ == "__main__":
    sys.exit(main())

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
