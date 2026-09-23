---
title: SmartStrm clean-room：用 Go 重写 STRM 服务的工程纪律
date: 2026-09-24 13:00
slug: smartstrm-cleanroom-go
description: smartstrm-cleanroom 是 Go 从零实现的 STRM 生成、任务自动化与安全播放服务。比起功能清单，它更值得看的是纪律：严格配置校验、HMAC 签名播放、竞态测试、功能矩阵与"未经验证不得标完成"的完成规则。
---

[smartstrm-cleanroom](https://github.com/sakuradairong/smartstrm-cleanroom) 是一个用 Go 1.23 从零实现的 STRM 生成 / 任务自动化 / 安全播放服务，AGPL-3.0 发布。

它最有意思的地方不是"又做了一个 STRM 工具"，而是它把**"什么算完成"这件事写成了规则**：功能矩阵里 `🟡` 部分实现与 `⬜` 未实现都不许计入完成，每个 `✅` 必须附上文件、测试、真实协议或容器运行证据。

<!-- more -->

## 一、clean-room 的边界

README 开头就把话说死了：本仓库依据公开功能说明和公开协议从零编写，**不包含原闭源项目的私有源码，不绕过许可证，也不调用未经授权的私有 API**；项目名称只用于说明兼容目标。落到实现上，还额外加了两条自我约束：

- 不具备可信 File ID API 的驱动**不伪造稳定 File ID**，改用路径模式；
- 不逆向闭源程序、不猜测私有协议字段。

这类"能力边界声明"看起来像免责条款，实际是设计约束：它决定了当某个云盘没有公开 API 时，正确的做法是**通过 OpenList 聚合接入**并在矩阵里标成 `🟡`，而不是靠未公开的移动端接口"先跑通再说"。

## 二、代码结构与体量

```
cmd/smartstrm/        入口
internal/app/         HTTP API、管理 UI、集成入口（app.go ~1581 行）
internal/config/      严格配置、迁移、敏感字段处理
internal/storage/     local / webdav / openlist / ani_open 驱动
internal/task/        调度、监听、生成器、插件、工具箱
internal/tmdb/        TMDB 客户端与受限图片代理
internal/mediaproxy/  媒体服务器代理与 PlaybackInfo 改写
internal/signature/   HMAC 播放签名
internal/pathpolicy/  本地路径与符号链接边界
internal/urlpolicy/   远端 URL/endpoint 校验
internal/cronexpr/    五段 crontab 解析
internal/history/     0600 有界 JSONL 运行历史
```

约 9,400 行 Go，`internal/*` 下 **142 个 `Test` 函数**（`app_test.go` 28 个、`generator_test.go` 22 个）。外部依赖只有 `testify` —— 存储、签名、Cron、代理全部手写，没有为了省事引入大型框架。

## 三、生成器：并发之外的正确性

`internal/task/generator.go`（798 行）是整个项目最容易出错的地方，它用几条规则把风险框住：

- **增量**：`Incremental` 打开时，目标文件已存在就跳过，但仍记为"预期存在"；`DirTimeCheck` 把远端目录时间与本地生成目录比对，成功后 `os.Chtimes` 回写；
- **单条失败隔离**：改名、复制、子目录列举、STRM 内容、写入任一失败都只记录 `failures` 并 `continue`，只有 context 取消或非列举型致命错误才中断整轮——**一个大目录里有一个坏文件名，不该让整轮任务失败**；
- **同步删除的安全闸**：`removeStale` 只在 `SyncDelete && overridePath == ""` 时执行，而且**必须整轮零失败**；删除范围限制在 `.strm` 与 `CopyExt` 内的文件，并尊重 `KeepLocal` glob；
- **目标冲突预检**：`preflightDirectoryTargets` 在写入前就拒绝两个源映射到同一目标的情况，而不是等覆盖发生后再补救。

另外任务队列是**每个任务一条有界队列 + 串行 worker**，工具箱类操作（内容替换、全量覆写、清除）用 `sync.Mutex` 做互斥；多任务入队用单独的 `enqueueMu` 保证原子性。

## 四、签名播放与 URL 策略

播放地址是 HMAC 签名的，`internal/signature` 之外还配了 `internal/urlpolicy`：

- 生成的 STRM URL 用 `QueryEscape` 编码，**字面 `%`、`%23`、`%2F` 这类文件名必须保证只解码一次**；少编码一层时 HMAC 直接拒绝——这条专门防"编码层级不一致导致的签名绕过"；
- 远端直链（OpenList `raw_url`、ANi feed link、统一 `/stream` 重定向出口）三层校验：必须是绝对 HTTP(S)、禁止内嵌凭据与 Fragment、不回显恶意 URL，同时**保留 CDN 签名所需的 Query**；
- 存储 endpoint 在保存与启动前统一校验（HTTP(S)、无内嵌凭据/Fragment；OpenList/WebDAV 基地址还禁止 Query 但保留合法子路径）；
- WebDAV 播放响应只代理媒体 200/206 与无正文 304/416，其余状态映射成脱敏 502；416 只保留安全 Range 元数据；HEAD 上游用 HEAD 且不复制正文；响应头走允许列表并拒绝 `Set-Cookie`。

远端错误一律**脱敏**：不回报 WebDAV/OpenList 的 reason phrase 或响应正文，OpenList 业务错误不回显 `message`，只保留协议与数字状态码——避免把别人回显的凭据、token 或换行内容带进日志。

## 五、存储驱动：把"能连上"做成"能证明"

- **Local**：root 必须是绝对路径且不能是文件系统根；解析符号链接后**再校验一次**；隐藏或拒读的逃逸链接会被拒；Delete/Rename/Move 操作链接本身而不跟随目标；
- **WebDAV**：PROPFIND 结果规范化后按 href 跳过当前目录，不依赖响应顺序，兼容绝对 URL / 路径 / 相对 href / percent-encoded href / HTTP-date / RFC3339 UTC / 空修改时间；
- **OpenList**：显式分页（200 条/页），测试里完整读取 450 条目录，支持 `total` 收敛、只在第一页强刷，并拒绝重复页、提前出现的空页和超过 100,000 条的异常响应；
- **响应上限**：ANi Open XML、OpenList JSON、WebDAV PROPFIND XML 统一 **16 MiB 硬上限**，并拒绝超限、第二个根对象或尾随字符；而媒体直链与 Range 代理保持流式。

## 六、配置与部署

- 配置是**严格模式**：未知字段、类型错误、非法 URL、危险的本地根、无效 Cron 或不支持的能力都会阻止保存；保存会把整份候选配置严格校验后**原子写回同一个 `0600` 文件**，不会"只启动一半后台任务"；
- schema 版本化：v1 起步，旧配置自动迁移，未来版本直接拒绝；
- 容器以 UID/GID 10001 运行，`docker compose` 起服务，健康检查 `GET /health`；管理页面在 `8024`，媒体代理 `8097` 是可选的、只有配置后才监听；
- `/robots.txt` 全站 `Disallow: /`，且无需认证挑战。

## 七、Cron 也会咬人

`internal/cronexpr` 支持标准五段表达式（通配符、范围、列表、步长），日/周是 OR 语义。真正值得注意的是两处：

- 配置保存前拒绝非法表达式，**Manager 在启动任何 worker/watcher 之前完成全量预检**；
- `2 月 31 日` 这类"永远不会到达"的日期通过跨闰年有界匹配处理，并有容器启动 + Chrome 无白屏验证——这类输入不会报错，只会让你的任务永远不跑，属于最难排查的一类 bug。

## 八、验证体系

```bash
gofmt -l / go vet ./... / go test -race ./...
python3 -m json.tool config.example.json >/dev/null
docker compose config -q
```

CI 在 push/PR 上跑格式检查、`go mod tidy` diff、vet、**`-race` 测试**、JSON 校验、markdown 链接检查、compose 校验与镜像构建；此外 CodeQL 每周一 cron 扫描 Go。功能矩阵里还记录了基于真实 Chrome 的交互验证（任务/存储/日志/配置页，含截图与零 console 错误）以及"到 OpenList 302"的容器 E2E。

## 九、已知边界

原生夸克/115/天翼/123/迅雷账号驱动未实现（经 OpenList 使用）；Plex、飞牛影视与特定 Emby/Jellyfin 客户端的深度兼容仍待真实验证；TMDB 的实网限流/代理/地区响应需要真实 Key；File ID 模式的真实驱动与账号验证待完成。

## 小结

这个项目示范了一种可复制的工程态度：**把外部依赖的每个不变量写进策略包**（pathpolicy / urlpolicy / signature / cronexpr），**把"部分完成"和"未完成"从"完成"里剔除**，并且让"危险操作"（同步删除）必须通过一整轮的零失败才有资格执行。

仓库：[sakuradairong/smartstrm-cleanroom](https://github.com/sakuradairong/smartstrm-cleanroom)（AGPL-3.0）
