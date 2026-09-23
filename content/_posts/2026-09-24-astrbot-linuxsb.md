---
title: AstrBot linuxsb 插件：HTML 解析容错与自适应卡片渲染
date: 2026-09-24 16:00
slug: astrbot-plugin-linuxsb
description: 一个把 linux.sb 帖子链接变成预览卡片的 AstrBot 插件。真正的工作量在解析容错、卡片自适应布局，以及"宁可报错也不发空卡片"的失败策略上。
---

[AstrBot 的 linuxsb 插件](https://github.com/sakuradairong/astrbot-plugin-linuxsb)（v0.2.4，要求 AstrBot ≥ 4.5.7）功能一句话就能说完：检测聊天里的 `https://linux.sb/topic/<数字>` 链接，渲染成一张自适应卡片图。

但把一句话的功能做成可用，要处理的是三件脏活：**别人家的 HTML 会变**、**卡片高度必须跟着内容变**、**失败时不能假装成功**。

<!-- more -->

## 一、识别：正则 + 去重

`client.py`（93 行）只做三件事：

```python
TOPIC_URL_RE = re.compile(r"https?://(?:www\.)?linux\.sb/topic/(?P<id>\d+)(?!\w)", re.I)
TOPIC_ID_RE = re.compile(r"^\d+$")
```

- 从任意文本里抽取 topic id 并去重（一条消息里贴十个相同链接只预览一次）；
- `resolve_topic_id` 接受纯数字、完整 URL，甚至相对路径 `/topic/123`（方便在网页里复制到半个链接的情况）；
- 抓取用 `aiohttp`，`ClientTimeout(total=15.0)`，UA 是自带身份声明的 `Mozilla/5.0 (compatible; AstrBot-LinuxSB/0.1; …)`，Accept-Language 优先 `zh-CN`；
- 状态码分流：`404` 抛 `LookupError`（告诉用户"帖子不存在"），`>= 400` 抛 `RuntimeError`。

**关键的一段是抓取后的结构校验**：

```python
if "post-content-title" not in html and "topic-post-list" not in html:
    raise RuntimeError("页面结构异常，可能触发了防护校验，请稍后重试")
```

站点改版或被防护拦截时，页面会返回 200 但结构完全不同。如果不检查，就会解析出一张"标题为 Topic #12345、正文为空"的假卡片——**比报错更糟的是错误地成功**。

## 二、解析：stdlib `HTMLParser` 的状态机

`parser.py`（321 行）没有引入 BeautifulSoup / lxml，而是用标准库 `HTMLParser` 写了个状态机。理由是插件运行在机器人进程里，依赖越少越好。

它按站点结构抓这些内容：

| 目标 | 定位方式 |
| --- | --- |
| 标题 | `h1.post-content-title`；失败时回退到 `<title>` 按 `" - "` 切分取第一段 |
| 版块 | 面包屑里第一个 `href` 以 `/forum/` 开头的 `<a>` |
| 阅读/回复数 | `div.post-content-stats` 内的 `<span>` 按序号取值 |
| 楼层 | `li.post-item` 的 `data-floor`；**没有该属性视为楼主（OP）** |
| 楼层作者 | `a.post-author` |
| 楼层时间 | 带 `data-performance-time` 的 `span` |
| 正文 | `div.post-content`，并跳过 `script`/`style` 与嵌套的"编辑提示"层（用 `_skip_depth` / `note_stack` 记深度） |

清洗阶段只做两件事，但都是必要的：

- `最后编辑于 2024-01-02 13:45` 这类编辑注记会被正则剥掉（它会紧跟在正文后面，不处理就会出现在预览里）；
- 文本统一压空白（`\s+ → 空格`）并 `unescape`。

由于是严格的"白名单定位"，站点小幅改版时最坏情况是**取到空值**，而不是取到隔壁楼层的内容——取值为空有兜底（`Topic #id`），取错内容没有。

## 三、渲染：三个密度档位

`card.py`（420 行）用一个 HTML 模板 + CSS 变量渲染卡片，再由 AstrBot 的 t2i 服务截图：

- 尺寸相关的东西全走变量（`--card-width` 默认 640px、`--title-size` 22px、`--content-size` 14px、`--content-lh` 1.55、`--banner-pad` / `--body-pad` / `--reply-pad` 等），**改版式只改一套变量**；
- 布局按内容量分 **short / normal / long** 三档：短贴宽松（大标题、完整正文），长贴紧凑（缩小正文区、`reply_limit` 降到 2 条回复）；
- 正文用 `white-space: pre-wrap` 保留换行；截断统一走 `_clip(text, n)`——超出时保留 `n-1` 个字符加省略号（`"abcdef", 5 → "abcd…"`）；
- 所有用户可控字段（标题、作者、正文、回复）**全部 HTML 转义**：测试直接断言 `title` 里的 `<img` 变成 `&lt;img`、作者里的 `<b>` 变成 `&lt;b&gt;`。卡片是 HTML 渲染的，这里漏一处就是注入。

截图参数也是踩过坑的写法（`build_render_options`）：

```python
{
    "type": "png",
    "full_page": True,
    "omit_background": True,
    # t2i 默认视口 800x720，短卡片下方会留一大片空白；
    # 视口宽度对齐卡片宽度、高度压到 10px，让 full_page 只按内容收缩
    "viewport_width": width,
    "viewport_height": 10,
    "animations": "disabled",
}
```

## 四、行为与配置

**渲染模式**三种：`card`（默认，发图）、`text`（发文本兜底）、`both`。文本模式由 `format_preview` 生成，格式是标题 + 元信息行（版块 · 楼主 · 时间 · 阅读 · 回复）+ 正文 + `—— 近期回复 ——` + 链接——**图挂了也不会让用户看不到内容**。

配置项（`_conf_schema.json`）都带中文说明，几个关键默认值：

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | true | 关闭后指令与自动预览均不可用 |
| `auto_preview` | true | 检测到帖子链接自动预览 |
| `max_content_chars` | 500 | 楼主正文与每条回复的截断长度 |
| `preview_replies` | 3 | 附带回复条数，0 表示不展示 |
| `max_auto_links` | — | 单条消息最多自动预览几个链接 |
| `stop_on_preview` | — | 命中预览后是否停止后续处理 |
| `request_timeout` | — | 抓取超时 |
| `render_mode` / `send_link_text` / `dynamic_layout` | card / true / — | 渲染与布局开关 |

抓取时会"多取一点再动态裁剪"（`_preview_kwargs` 注释里写明布局会按需收缩），避免长贴只剩一句半。

## 五、结构

```
main.py        AstrBot 插件入口：指令 /linuxsb、link 监听、渲染调度
client.py      链接识别 + HTTP 抓取
parser.py      HTMLParser 状态机 + 文本清洗
card.py        HTML/CSS 模板、密度分档、渲染参数
models.py      TopicPreview / TopicReply 数据类
tests/         test_parser / test_card / test_client / test_live + fixture
```

`tests/fixtures/topic_11504.html` 是一个真实保存的页面快照，`test_parser` 直接断言：标题/作者/正文非空、**正文不含 `<`**、编辑注记已剥离、阅读与回复是纯数字、正好 3 条回复且第一条楼层为 1。`test_live.py` 是可选的真实网络测试，与离线测试分开。

## 六、限制

依赖站点当前的 DOM 结构（改版需更新选择器，但失败会显式报错）；`max_content_chars` 之下长贴会被截断；图片由 t2i 服务渲染，字体可用性取决于运行环境。

## 小结

插件类项目的正确姿势是"**薄但严**"：入口薄、依赖少，但在三个地方必须严格——**页面结构校验**（防止假成功）、**用户内容转义**（卡片是 HTML）、**失败可解释**（404 与结构异常给不同信息）。再加上一个文本兜底，图渲染失败也不会让功能消失。

仓库：[sakuradairong/astrbot-plugin-linuxsb](https://github.com/sakuradairong/astrbot-plugin-linuxsb)
