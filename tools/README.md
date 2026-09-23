# 静态博客构建

本站使用 Python + Markdown 生成可直接部署至 GitHub Pages 的静态站点。原 Hexo 源仓库已删除，目前 `content/_posts/` 是文章来源，`tools/build_site.py` 是唯一页面生成入口。

## 目录与设计

- `content/_posts/*.md`：文章正文及 front matter。
- `content/legacy/*.json`：可选历史文章，当前为空。
- `tools/build_site.py`：生成首页、文章页、年/月归档、Atom feed 和图标。
- `css/style.css`：响应式布局、色彩、卡片和阅读排版。
- `js/script.js`：主题筛选、本地搜索、复制文章链接。

界面采用暖白与墨绿配色，首页展示开发手记卡片，文章页提供桌面目录。无前端构建步骤，无外部字体或 JavaScript 依赖。禁用 JavaScript 时仍可阅读全部文章和归档，交互控件自动隐藏。

## 新增文章

在 `content/_posts/` 下创建 `YYYY-MM-DD-slug.md`：

```markdown
---
title: 文章标题
date: 2026-09-24 09:00
category: 后端工程
tags: Go, 工程实践
slug: post-url-slug
description: 文章摘要，用于首页卡片、搜索和页面元数据。
---

开头介绍……

<!-- more -->

## 正文标题

正文……
```

- `slug` 决定 `/YYYY/MM/DD/<slug>/` URL，已有 URL 保持兼容。
- `category` 默认是「工程手记」，首页自动生成分类按钮。
- `tags` 使用英文逗号分隔；搜索匹配标题、摘要和标签，以空格分隔的多个关键词取交集。
- `description` 用于首页摘要；未填写时，卡片从文章文本中截取。
- `<!-- more -->` 之前的正文用于 feed 摘要。
- 阅读时长按正文约 500 字符/分钟估算。
- 二、三级标题自动生成文章目录；围栏代码块包含行号。
- 外链自动添加 `target="_blank" rel="noopener"`。

## 构建、校验和预览

依赖 Python 3.10+ 和 `markdown` 包：

```bash
pip install markdown
python3 tools/build_site.py
python3 tools/build_site.py --check
python3 -m http.server 4173 --bind 127.0.0.1
```

浏览器打开 `http://127.0.0.1:4173`。支持 `/` 快捷键搜索，Esc 关闭搜索。复制链接在 HTTPS 或 localhost 可用，并提供失败反馈。

构建会清理失去来源的文章页与归档页。请修改文章源和生成器后重新构建，避免直接编辑生成的 HTML。

校验检查 HTML 标签闭合、站内链接与资源、首页与归档文章可达性、feed 最新 20 篇条目。桌面和移动端布局、搜索和筛选需在浏览器中检查。
