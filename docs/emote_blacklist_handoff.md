# 创建表情黑名单：功能交接

## 目标与预期行为

为 NRPlusPlus 提供一个 Windows 外部管理界面，让用户：

- 浏览表情目录；
- 按名称、ID、角色系列或类型搜索；
- 从下拉框选择动态表情或文字信息；
- 将表情添加到黑名单；
- 查看、批量移除已选表情；
- 将配置持久化到 JSON。

当前阶段明确只实现“黑名单配置 UI”，不实际拦截游戏表情。未来真正屏蔽时，只应拦截对手发送且命中黑名单的表情，其他表情正常显示。

## 要求与约束

- 运行环境是 Windows。
- Frida 方案已废弃，未来不得重新依赖 Frida。
- 当前版本不需要实际屏蔽，也不需要底层捕获表情事件。
- 黑名单同时支持动态表情 ID（如 `1-0`）和文字信息 ID（如 `Taunt1`）。
- UI 必须支持下拉选择和搜索。
- 修改完成后需要同步到 GitHub。
- 不得影响现有状态读取和可视化功能。

## 当前仓库状态

- 分支：`main`
- 功能提交：`8d17351 Add emote blacklist manager UI`
- 表情目录提交：`0932fec Add generated emote resource catalog`

## 设计决策

### 配置格式

配置位于 `resources/emote_blacklist.json`：

```json
{
  "schema_version": 1,
  "mode": "configuration_only",
  "animated_emote_ids": [],
  "text_emote_ids": []
}
```

`configuration_only` 明确表示当前配置不会实际用于游戏内屏蔽。保存时使用临时文件加 `os.replace()`，避免写入中断导致配置损坏。

### 表情目录

目录位于 `resources/emotes.json`：

- 动态表情：636 个；
- 元数据完整：580 个；
- 元数据不完整：56 个；
- 文字信息：6 条；
- 动态表情 ID 格式：`IndexHi-IndexLo`。

文字信息优先显示简体中文和英文，例如 `祝你好运！ / Good luck! [Taunt1]`。

### UI 技术

使用 Python 标准库 Tkinter/ttk，不增加新依赖。

- 窗口标题：`NRPlusPlus · 表情黑名单`
- 默认尺寸：920×620
- 最小尺寸：760×520
- 左侧：搜索、下拉选择、信息预览和添加按钮
- 右侧：黑名单表格，支持多选
- 底部：状态信息、保存和移除按钮

状态窗口通过独立子进程打开管理器，并防止同一状态窗口重复启动多个管理器。

## 相关文件、函数与类

### `windows/deploy/emote_blacklist_ui.py`

- `EmoteEntry`：统一表示动态表情和文字信息。
- `load_catalog()`：读取并整理表情目录。
- `filter_entries()`：多关键词、不区分大小写搜索。
- `load_blacklist()`：加载现有配置。
- `save_blacklist()`：原子保存配置。
- `EmoteBlacklistWindow`：管理整个 Tkinter 窗口。
- `_build()`：创建界面控件。
- `_refresh_candidates()`：根据搜索词刷新下拉框。
- `_update_preview()`：显示候选表情详情。
- `add_selected()`：添加选中表情。
- `remove_selected()`：批量移除。
- `_refresh_blacklist()`：刷新右侧表格。
- `save()`：保存配置并更新状态。
- `close()`：未保存时询问是否关闭。

### 其他文件

- `windows/Manage-EmoteBlacklist.ps1`：独立启动入口，优先使用 `pythonw.exe`。
- `windows/deploy/minimal_visualizer.py`：状态窗口中的 **Manage emote blacklist** 按钮和重复启动保护。
- `windows/tests/test_emote_blacklist_ui.py`：目录、搜索和配置持久化测试。
- `windows/tests/test_minimal_visualizer.py`：管理器重复启动保护测试。
- `README.md`：用户使用说明。

独立启动命令：

```powershell
powershell -ExecutionPolicy Bypass -File windows\Manage-EmoteBlacklist.ps1
```

## 当前实现状态

黑名单配置 UI 已实现并推送。真实黑名单当前为空。没有实现真实表情捕获或屏蔽。

## 已执行测试

聚焦测试：

```powershell
python -m unittest windows.tests.test_emote_blacklist_ui windows.tests.test_minimal_visualizer -v
```

结果：6 项全部通过。

实现完成时执行过完整测试：

```powershell
python -m unittest discover -s windows\tests -v
```

结果：23 项全部通过。

界面级冒烟测试：

- 窗口成功初始化为 920×620；
- 成功加载 642 个可选项目；
- 使用临时配置完成“搜索 Good luck → 定位 Taunt1 → 添加 → 保存 → 重新读取”；
- 测试未修改真实黑名单文件。

## 已知问题与不确定性

- 尚未实现实际表情识别或屏蔽。
- 56 个动态表情元数据不完整，可能只能显示内部名称或 ID。
- 搜索依赖目录中的名称、ID、family 和类型，不支持目录中不存在的民间别名。例如完整短语 `Minion Giant Burp` 当前不一定能找到对应条目。
- 尚无表情图片或动画预览。
- 状态窗口只能阻止由自身重复启动的管理器；用户仍可多次运行 PowerShell 启动器。多个实例同时保存时会发生“最后保存者覆盖”。
- `load_blacklist()` 会检查列表类型，但尚未严格验证 `schema_version` 和 `mode`。
- 目录或配置 JSON 损坏时，启动异常目前不会显示友好的错误窗口。
- Windows 自动截图工具对 Tk 窗口连续返回 `SetIsBorderRequired failed: 不支持此接口 (0x80004002)`。因此没有完成自动截图式视觉检查，但窗口初始化和交互冒烟测试已通过。

## 已放弃或撤销的方案

- Frida：Windows 模拟器无法使用，已明确废弃。
- 底层表情事件反向扫描：曾尝试从 native 内存结构定位表情事件，但捕获不稳定。
- “只记录不屏蔽”观察管线：底层没有可靠输出真实 `emote_events`。用户将范围改为 UI 后，该管线及调试代码已全部撤销。
- 当前仓库不存在 `emote_observer.py`，native helper 也没有遗留 `emote_debug` 输出。

## 未完成任务与建议顺序

1. 由用户实际检查窗口布局、按钮文案和使用流程。
2. 根据反馈考虑类型筛选、角色系列筛选、缩略图、清空黑名单、搜索结果列表和自动保存。
3. 加强 schema/mode 校验、JSON 损坏提示和旧配置备份。
4. 使用单实例锁或配置变更检测解决多实例并发保存。
5. 改善 56 个元数据不完整条目的名称和搜索别名。
6. 只有用户重新要求实际屏蔽后，再设计非 Frida 的底层方案：
   - 捕获双方真实表情事件；
   - 获取发送方及稳定表情 ID；
   - 只对 opponent 事件匹配黑名单；
   - 未命中的表情正常显示；
   - 动态表情和文字信息分别匹配；
   - 先实现只记录和命中日志，再启用拦截。
7. 后续每次修改完成后运行测试、提交并推送 GitHub。
