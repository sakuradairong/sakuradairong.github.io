---
title: TruckDeck：用 Express + WebSocket 把 ETS2/ATS 遥测送进手机浏览器
date: 2026-09-24 09:00
slug: truckdeck-telemetry-control
description: TruckDeck 是一套跑在局域网 PC 上的 Node 服务，把 Euro Truck Simulator 2 / American Truck Simulator 的遥测推给手机浏览器，并把手机上的点按变成游戏按键。本文拆解它的模块划分、冻结的 WebSocket 契约、遥测与按键注入链路，以及"绝不在 mock 模式下注入"的边界设计。
---

把游戏遥测搬到第二块屏幕上，通常意味着再写一个桌面客户端。而 [TruckDeck](https://github.com/sakuradairong/TruckDeck) 换了个思路：**PC 上只跑一个 Node 服务，手机浏览器打开就是横屏中控**。所有状态以"后续遥测回显"为准，而不是以"命令已发送"为准——这是整个项目最核心的一条约束。

<!-- more -->

## 一、它解决什么问题

ETS2/ATS 的第三方遥测生态里，[RenCloud/scs-sdk-plugin](https://github.com/RenCloud/scs-sdk-plugin) 会把游戏状态写进 Windows 共享内存 `Local\SCSTelemetry`，Node 侧用 `trucksim-telemetry` 的 `getData()` 就能读到速度、转速、油量、灯光等字段。TruckDeck 把这条链路接上，再用 WebSocket 广播给手机；反向则把手机上的按钮点按变成游戏按键（Windows `SendInput`）。

目标很窄：**只服务局域网、不提供公网隧道、不做账号系统**。默认端口 `4000`，WebSocket 固定路径 `/ws`。

## 二、模块划分

```
server/
  index.js            # 入口：装配 config / telemetry / input / app / wsHub，注册退出清理
  src/
    app.js            # Express：/health、静态资源、状态页兜底
    config.js         # 常量、钳制、键位合并（maxPayload / helloTimeout / 队列上限）
    lan.js            # 私网 IP、Host、Origin 校验
    wsHub.js          # /ws 升级、握手、限流、广播、命令派发
    keybindsGame.js   # 解析游戏 controls.sii，读真实键位
    telemetry/        # index(模式仲裁) + mock(模拟状态) + scs(共享内存映射)
    input/            # index + windows(SendInput worker) + scs + mock + keyMap
    commands/         # handler(动作语义) + queue(有界串行队列) + lightCycle + scsPlan
    phase2/vjoy.js    # 二期占位：所有方法返回 UNSUPPORTED
config/
  server.default.json / keybinds.default.json
web/                  # 前端源（原生 HTML/CSS/JS），build 时复制到 server/public/
test/                 # 9 个 node:test 文件
```

设计上有一条硬性约定：**`server/index.js` 负责装配与销毁顺序**（`hub.dispose()` → `input.dispose()` → `server.close()`，2 秒兜底 `process.exit(0)`），任何模块都不自己持有生命周期资源。退出时必须释放已按下的键，否则游戏里会出现"按着不放"的卡键现象。

## 三、一份 12 个 action 的冻结契约

`docs/API_CONTRACT.md` 把 v1 契约冻死了：消息类型只有 `hello` / `telemetry` / `command` / `command_ack` / `command_nack` / `error`，动作只有 12 个。

| 类别 | action | 值语义 |
| --- | --- | --- |
| 灯语（6） | `lights.parking`、`lights.beamLow`、`lights.beamHigh`、`lights.blinkerLeft`、`lights.blinkerRight`、`lights.hazard` | 必须带 boolean 目标状态 |
| 雨刮（1） | `wipers.set` | 只接受 `off` / `auto` / `1` / `2` / `3` |
| 车辆（5） | `handbrake.toggle`、`diffLock.toggle`、`liftAxle.toggle`、`cruise.toggle`、`engine.toggle` | `value` 省略或为 null |

握手必须发生在任何命令之前，否则回 `HELLO_REQUIRED`：

```json
{"type":"hello","role":"web","version":"1"}
{"type":"hello","ok":true,"role":"server","version":"1","mock":true,"telemetryHz":10,"ts":1710000000000}
```

错误码也是枚举的一部分，不允许自由发挥：`HELLO_REQUIRED`、`INVALID_JSON`、`UNKNOWN_TYPE`、`UNKNOWN_ACTION`、`INVALID_VALUE`、`NOT_MAPPED`、`UNSUPPORTED`、`INJECT_FAILED`、`FORBIDDEN`、`UNSUPPORTED_VERSION`。

**为什么"能回读"比"能写入"重要**：雨刮在 SDK 里只有 boolean（开/关）可读，所以 live 模式下 `2`/`3`/`auto` 一律不伪造——mock 里五档齐全，是为了演示面板，而不是为了让 UI 看起来更丰富。

## 四、遥测链路：从共享内存到 10 Hz 广播

- 速率：契约下限 ≥ 5 Hz，默认 10 Hz；广播间隔取 `max(16, round(1000 / hz))`，`telemetryHz` 被钳制在 5–30。
- 单位换算是照着 SDK 一手文档做的，不靠猜：`speed`(m/s) × 3.6 → `speedKmh`；`fuel / fuelCapacity`（升）→ 百分比；`airPressure` 单位是 **psi**；挡位优先 `gearDashboard`，缺失才退回 `gear`。
- 模式仲裁在 `telemetry/index.js`：插件不可用、共享内存没有 `sdkActive`、非 Windows、或 `TRUCKDECK_MOCK=1` 一律进 mock。**"Node 包装得上"不算 live**——游戏没开也算 mock，并把模式变化通过 hello ack 的 `mock` 字段回推给已连接的手机。
## 五、按键注入：为什么不用 Node 直接调 Win32

Node 里没有原生 `SendInput`，`server/src/input/windows.js` 的做法是**常驻一个 PowerShell worker 进程**，Node 只负责发指令、收结果：

- 只有白名单里的键码能被注入，白名单来自 `config.js` 的 `KEY_WHITELIST`；
- 启动握手 `READY_TIMEOUT_MS = 15000`，单条指令 `DEFAULT_TIMEOUT_MS = 5000`，退出等待 `DISPOSE_WAIT_MS = 3000`；
- `mock` 与 Linux 环境下**不调用任何 OS 注入 API**，这是代码路径级别的隔离，不是"应该不会触发"。

灯语这里有个真实存在的坑：示廓灯和近光默认共用 `L` 键，键盘上没法直接"设置"到某个档，只能按循环步数推进。`commands/lightCycle.js` 把三态建模成 `off → parking → low`，步数用取模算：

```js
// taps = (to - from + 3) % 3 —— 三态循环，负值自然回正
const taps = (to - from + 3) % 3;
```

当共享内存路径可用时，走的是 SCS 的语义接口（`commands/scsPlan.js` + `scsControlOffsets.json` 里 276 个控件、`_totalBytes = 342` 的裸布局，`float=4 / bool=1`，无 header、无版本号），此时可以按"目标状态"精确写入，把按键脉冲的保压时间钳制在 `[20, 2000] ms`。

## 六、命令队列与"代数"失效

手机点按和游戏状态回读之间天然异步，TruckDeck 用三件事把它收敛：

1. **有界串行队列**：`maxDepth = 32`、`ttlMs = 1500`。溢出与超时都以 `command_nack` 回给前端，而不是静默丢弃。
2. **状态以后续遥测为准**：`commands/handler.js` 的时序常量是 `TELEMETRY_WAIT_MS = 600` → 轮询比对（40 ms 一次）→ `TAP_SETTLE_MS = 80` / `SCS_SETTLE_MS = 120` → `SCS_CONFIRM_MS = 700`。也就是说，命令返回的 `ack` 只代表"注入动作完成"，真正的高亮状态等遥测回来再点亮。
3. **模式代次**：mock ↔ live 切换会推进一个 generation，旧模式下积压的命令直接作废。否则一次模式切换就可能让"演示时的点击"在真实游戏里落地成按键。

限流同样在协议层：单连接 `msgRateLimit = 30` 条/秒，超出返回错误；单帧上限 `maxPayload = 8 KiB`；握手超时 `helloTimeoutMs = 10000`。

## 七、安全：只服务局域网的三道闸

`server/src/lan.js` 做的是"这个请求到底是不是来自本机/私网"的判断：

- **来源必须查得通**：请求 IP 必须是私网地址，Host 头必须与本机可达地址一致；
- **WebSocket Origin 校验**：`Origin` 与 `Host` 不匹配（含 `null`、端口不符、外域）一律拒绝升级——这条是防"任意网页通过局域网 IP 跨站触发键盘注入"的关键；
- **路径白名单**：只有 `/ws` 能升级，其他路径直接拒。

这类校验很容易写成"看起来有、实际能绕"，所以验收里专门有一组拒绝路径测试：外域 Origin、`null` Origin、端口不符、非本机 Host、非 `/ws` 路径、未握手先发命令、非法 JSON、非法 value、超过 8 KiB 的单帧。

## 八、验证：26 项单测 + 18 项端到端

- `npm test` 跑 `node --test test/*.test.js`，**26/26**，覆盖协议、队列、模式切换、灯光循环、键位解析、输入 worker、前端兜底与状态页，且**不依赖构建产物**（全新克隆 `npm ci && npm test` 即可），测试端口固定 4011，不占 4000。
- 端到端复验脚本 `docs/acceptance/verify-ws.cjs`，**18/18**：默认端口 4000 与非默认端口各跑一轮，12 类 action 共 22 次操作全部 ack 且与后续遥测一致，实测遥测 9.33 / 9.98 Hz。
- 一期是"编排放 + 两个实现代理"完成的：契约由 Cursor Agent 实现并冻结，手机面板由 Cline Agent 实现，`docs/acceptance/` 里保留了截图、原始输出、派单回传和两个代理的运行日志（已扫描确认无凭据）。

## 九、已知限制与二期

- **Windows 实机 / 游戏尚未验收**（`SendInput` 与共享内存需要真机），Linux 上可以跑全功能 mock 做演示；
- SDK 雨刮只有开关语义，live 不伪造多档与 AUTO；
- 无 Service Worker，离线不可用；局域网 HTTP 下"添加到主屏幕/方向锁定"依浏览器而定；
- 二期 vJoy 陀螺仪方向盘只留了接口与 `UNSUPPORTED` 占位，一期刻意不实现。

## 小结

TruckDeck 值得记下来的不是"手机能控游戏"，而是三处克制：**契约先冻结再实现**、**不能回读的状态就不假装能写**、**mock 与 live 在代码路径上彻底分开**。前两条让前端不必猜后端的语义，第三条让"演示"永远不会变成"误注入"。

仓库：[sakuradairong/TruckDeck](https://github.com/sakuradairong/TruckDeck)（MIT）
