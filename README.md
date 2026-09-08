# NRPlusPlus

## 表情黑名单管理器

当前版本提供黑名单配置界面，但尚未在游戏中实际拦截表情。

- 在状态窗口底部点击 **Manage emote blacklist**；或运行
  `powershell -ExecutionPolicy Bypass -File windows\Manage-EmoteBlacklist.ps1`。
- 可按表情名称、ID、角色系列或类型搜索，并从下拉列表中添加表情。
- 右侧列表支持多选后批量移除。
- 点击“保存配置”后，结果写入 `resources\emote_blacklist.json`。

表情目录来自 `resources\emotes.json`。黑名单分别保存动态表情 ID（如
`1-0`）和文字信息 ID（如 `Taunt1`），供后续屏蔽功能直接使用。
