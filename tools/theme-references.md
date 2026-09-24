# 主题改造参考

核实日期：2026-09-24。以下只使用主题作者维护的 GitHub 仓库、配置和演示。许可证名称是仓库声明，不扩展为法律意见。

本站目前由 [Python 生成器](build_site.py) 读取 `content/_posts/`，直接生成 HTML；原 Hexo 源仓库已删除，详见[构建说明](README.md)。因此，安装 Hexo 主题不能直接改变本站；完整换用主题需要恢复 Hexo 工程、迁移 front matter 与生成流程，并验证历史 URL、资源路径和 feed。

## 候选比较

| 主题及一手来源 | 视觉结构与现成功能 | 仓库许可证 | 对本站的启示与迁移区别 |
| --- | --- | --- | --- |
| [Landscape](https://github.com/hexojs/hexo-theme-landscape) · [官方预览](https://hexojs.github.io/hexo-theme-landscape/) · [配置](https://github.com/hexojs/hexo-theme-landscape/blob/master/_config.yml) | 顶部 banner、文章主栏；默认右侧栏包含分类、标签、归档和最近文章。 | [MIT](https://github.com/hexojs/hexo-theme-landscape/blob/master/LICENSE) | 最适合保留旧站的布局记忆，在现有生成器中深化外观；完整安装版本仍依赖 Hexo。 |
| [Fluid](https://github.com/fluid-dev/hexo-theme-fluid) · [配置](https://github.com/fluid-dev/hexo-theme-fluid/blob/master/_config.yml) | Material Design；可配置首页及文章页头图高度、蒙版、文章封面、暗色模式和本地搜索。 | [GPL-3.0](https://github.com/fluid-dev/hexo-theme-fluid/blob/master/LICENSE) | 借鉴头图与正文面板的层级、清楚的标题和元信息。完整换用需要 Hexo 配置及页面布局迁移。 |
| [Butterfly](https://github.com/jerryc127/hexo-theme-butterfly) · [官方站](https://butterfly.js.org/) | 卡片式双栏，响应式布局；阅读模式、桌面与移动目录、暗色模式、本地搜索。 | [Apache-2.0](https://github.com/jerryc127/hexo-theme-butterfly/blob/master/LICENSE) | 最接近“丰富但仍是个人博客”的方向，可参考侧栏与阅读工具的组织；安装还需 Pug、Stylus 渲染器。 |
| [Matery](https://github.com/blinkfox/hexo-theme-matery) · [作者演示](https://blinkfox.github.io/) | Material Design；banner、首页轮播、瀑布流文章、时间线归档及包含项目的关于页。 | [Apache-2.0](https://github.com/blinkfox/hexo-theme-matery/blob/develop/LICENSE) | 参考图文卡片和归档时间线；完整迁移还要配置分类、标签、关于等专用页面。 |

## 本次建议

以下是基于上述资料和本站构建方式作出的设计判断：以原 Landscape 的全幅 banner、文章主栏和右侧栏为骨架，保留旧站的辨识度；参考 Butterfly 的卡片与侧栏组织、Fluid 的头图层级，使用本站自己的 HTML、CSS 和 JavaScript 实现。

1. 首页保留有辨识度的全幅头图，控制高度，让首屏同时露出文章；移动端进一步缩短开场。
2. 主栏通过重点文章、图文比例和清晰的元信息建立主次。封面优先使用文章已有图片，避免无关随机图。
3. 右侧栏承担个人介绍、分类、归档等导航功能，文章页以阅读目录为主；窄屏转为正文之后或可折叠控件。
4. 完善暗色切换、搜索、返回顶部及目录定位。交互需有键盘操作、焦点状态和减少动画支持。
5. 保持现有 Python 构建入口及历史 URL，本次不引入 Hexo 构建栈；不复制候选主题源码或演示图片。若后续直接采用主题源码，应单独记录具体版本、许可证和归属说明。

这条路线可在当前项目中直接落地。若以后希望使用主题插件生态和配置体系，再做独立的 Hexo 迁移。
