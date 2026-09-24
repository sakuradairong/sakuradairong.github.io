# 静态博客构建

本站使用 Python + Markdown 生成可直接部署至 GitHub Pages 的静态站点。原 Hexo 源仓库已删除，目前 `content/_posts/` 是文章来源，`tools/build_site.py` 是唯一页面生成入口。

## 目录与设计

- `content/_posts/*.md`：文章正文及 front matter。
- `content/legacy/*.json`：可选历史文章，当前为空。
- `tools/build_site.py`：生成首页、文章页、年/月归档、Atom feed、图标和文章封面。
- `css/style.css`：响应式布局、亮/暗配色、卡片和阅读排版。
- `css/images/banner.jpg`：首页与文章页的头图（原 Landscape 主题的 banner）。
- `css/images/avatar.svg`：侧栏头像，手绘 SVG，无外部依赖。
- `js/script.js`：深浅色切换、本地搜索、分类筛选、目录高亮、阅读进度。

## 界面与主题

整体复刻原 Hexo Landscape 主题的观感：顶部通栏导航，下方是整幅 `banner.jpg` 头图，网站标题居中压在头图上，正文区为「文章列 + 侧栏」两栏，窄屏自动堆叠为单栏。

- 头图高度随视口变化：首页桌面约 430px、手机约 296px，文章页与归档页更矮，保留原图的地平线。
- 亮/暗双主题共用一套 CSS 变量。`<head>` 内联一段极小脚本在首帧前写入 `data-theme`，避免深色模式白屏闪烁。
- 用户选择保存在 `localStorage` 的 `rain-theme`；未做过选择时跟随 `prefers-color-scheme`，并随系统变化实时同步。
- 首页文章卡片的封面是仓库内生成的 SVG 示意图（页面上明确标注「封面示意图」），不是项目截图，也不含真实数据；文章页头图仍是 `banner.jpg`。
- 无前端构建步骤，无外部字体、CDN 或第三方 JavaScript。禁用 JavaScript 时仍可阅读全部文章和归档，搜索、筛选、主题切换等交互控件自动隐藏。
- 支持 `prefers-reduced-motion`、打印样式，以及键盘 Tab 焦点样式和「跳到正文」跳过链接。

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
- `category` 默认是「工程手记」，首页自动生成分类按钮，侧栏同步统计分类与标签数量。
- `tags` 使用英文逗号分隔；搜索匹配标题、摘要和标签，以空格分隔的多个关键词取交集。
- `description` 用于首页摘要；未填写时，卡片从文章文本中截取。
- `<!-- more -->` 之前的正文用于 feed 摘要。
- 阅读时长按正文约 500 字符/分钟估算。
- 二、三级标题自动生成文章目录（桌面侧栏显示，窄屏提供折叠目录）；围栏代码块包含行号。
- 外链自动添加 `target="_blank" rel="noopener"`。
- 文章封面：`tools/build_site.py` 里的 `COVER_ART` 按 slug 映射到绘图函数，未登记的文章回退到通用封面。新增专属封面时，写一个返回内联 SVG 字符串的 `_cover_*` 函数并加入 `COVER_ART` 即可。

## 构建、校验和预览

依赖 Python 3.10+ 和 `markdown` 包：

```bash
pip install markdown
python3 tools/build_site.py
python3 tools/build_site.py --check
python3 -m http.server 4173 --bind 127.0.0.1
```

浏览器打开 `http://127.0.0.1:4173`。常用交互：

- `/` 打开搜索、`Esc` 关闭搜索弹窗；结果可点击跳转，关闭后焦点回到搜索按钮。
- 首页与归档页的分类按钮可直接筛选当前页面列表。
- 文章页支持复制链接（HTTPS 或 localhost 可用，失败时给出提示）、侧栏目录滚动高亮、顶部阅读进度条和「回到顶部」。

构建会清理失去来源的文章页与归档页。请修改文章源和生成器后重新构建，避免直接编辑生成的 HTML。

`--check` 在不写盘的前提下逐页校验：HTML 标签闭合、每页恰好一个 `<h1>`、`<head>` 元数据与防闪烁脚本存在、站内链接与静态资源可达、首页与归档页能到达全部文章、`atom.xml` 条目完整。布局细节（多端断点、搜索、筛选）仍需在浏览器中确认。
