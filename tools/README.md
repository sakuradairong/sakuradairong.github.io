# tools/ — 站点构建

`sakuradairong.github.io` 目前的仓库里只有 Hexo 生成的产物（原 Hexo 源仓库 `hexo-blog-fly` 已删除），
因此这里补了一份可复现的构建脚本：**用 Markdown 写文章，生成与 Hexo 4.2.0 + landscape 主题一致的静态页面**。

## 目录

```
content/_posts/*.md    文章源（Markdown + front matter）
content/legacy/*.json  非 Markdown 来源的历史文章（可选，当前为空）
tools/build_site.py    生成器：文章页 / 首页 / 归档页 / atom.xml / favicon.png
```

> 2020 年的两篇占位文章（`article-title`、`hello-world`）已删除，对应的 `content/legacy/*.json`
> 与生成页面一并移除。若今后还需要保留某篇无法转成 Markdown 的旧文章，可以按原格式放回该目录，
> 构建时会原样输出。

## 写一篇新文章

在 `content/_posts/` 新建 `YYYY-MM-DD-slug.md`：

```markdown
---
title: 文章标题
date: 2026-09-24 09:00
slug: post-url-slug
description: 用于 <meta name="description"> 与 og:description 的摘要
---

开头这一两段会作为首页摘要（`<!-- more -->` 之前的内容）。

<!-- more -->

## 正文标题

正文……
```

- `slug` 决定 URL：`/YYYY/MM/DD/<slug>/`；
- 首页只展示 `<!-- more -->` 之前的内容，并附 "Read more"；
- 代码块用围栏语法并标注语言，会渲染成主题自带的 `figure.highlight`（带行号）；
- 外链自动加 `target="_blank" rel="noopener"`。

## 构建与校验

```bash
python3 tools/build_site.py          # 生成整站并自检
python3 tools/build_site.py --check  # 只做校验（标签闭合、内链/资源、feed 条目）
```

依赖：Python 3.10+ 与 `markdown` 包（`pip install markdown`）。

校验内容：HTML 标签闭合、所有站内链接与资源可解析、每篇文章在首页与归档中可达、`atom.xml` 覆盖最新 20 篇。

## 说明

- 归档页会自动按 `年 / 月` 目录生成（`/archives/2026/09/`）；
- 侧栏 "Archives" 与 "Recent Posts" 由脚本统一渲染，新增文章后无需手工改动；
- **删除文章只需要删掉 `content/_posts/` 下对应的 Markdown**：构建会自动清理失去来源的文章页与归档页，并回收空目录；
- 生成器同时修正了 2020 年产物里的两个问题：`og:url` 中的多余 `/github.io` 路径前缀、站点内搜索的 `sitesearch` 值；
- 缺少的 `/atom.xml` 与 `/favicon.png`（原来都是 404）由构建产物提供。
