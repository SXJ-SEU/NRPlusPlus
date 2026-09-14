# NRPlusPlus

## 对手卡牌栏

非 headless 模式默认打开独立的 500×340 对手卡牌栏：

- 窗口无系统标题栏、默认置顶，可按住任意位置拖动；
- 对手尚未使用的卡牌显示为 “?”，首次使用后按顺序显示对应卡牌；Hero 与 Evolution 卡牌会根据当前形态和进化点数切换图标；
- 底部显示对手当前圣水和已揭示卡牌的平均费用；
- 按 `Esc`、`Alt+F4` 或关闭进程可退出。

旧的战场状态窗口仍保留用于调试，启动时传入 `--ui debug` 即可显示。

## 表情黑名单管理器

当前版本提供黑名单配置界面，但尚未在游戏中实际拦截表情。

- 在状态窗口底部点击 **Manage emote blacklist**；或运行
  `powershell -ExecutionPolicy Bypass -File windows\Manage-EmoteBlacklist.ps1`。
- 可按表情名称、ID、角色系列或类型搜索，并从下拉列表中添加表情。
- 右侧列表支持多选后批量移除。
- 点击“保存配置”后，结果写入 `resources\emote_blacklist.json`。

表情目录来自 `resources\emotes.json`。黑名单分别保存动态表情 ID（如
`1-0`）和文字信息 ID（如 `Taunt1`），供后续屏蔽功能直接使用。
