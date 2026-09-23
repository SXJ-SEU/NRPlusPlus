# NRPlusPlus

NRPlusPlus 是面向 Windows 的《皇室战争》对局辅助工具。它从 Android 模拟器读取原生实体流，并提供对手卡牌、玩家信息、交流与战斗日志界面。

## 快速开始

环境要求：Windows、Python 3，以及 Zig 0.15.2（用于构建原生读取辅助程序）。

```powershell
powershell -ExecutionPolicy Bypass -File windows\Setup.ps1
python windows\tools\capture_native_entity_stream.py --serial localhost:5557 --package nullsroyale.rel.free
```

默认会打开独立的 500×340 对手卡牌栏。使用 `--headless` 可仅记录数据，使用 `--ui debug` 可打开旧版战场状态调试窗口。完整参数可通过 `--help` 查看。

## 主要功能

- 对手尚未使用的卡牌显示为“?”，首次使用后按顺序展示；Hero 与 Evolution 卡牌会根据当前形态和进化点数切换图标。
- 底部展示对手当前圣水和已揭示卡牌的平均费用。
- 侧栏可切换玩家信息、交流、战斗日志和设置页面。
- 无系统标题栏的窗口默认置顶，可拖动；按 `Esc`、`Alt+F4` 或关闭进程可退出。

玩家信息页当前使用固定的开发期模拟玩家 `JTR_CR #8JCRL98YC`，资料与卡组图标来自 RoyaleTools；常用卡组展示胜率、平均费用和仅按 1v1 计算的场均皇冠。

## 表情黑名单管理器

当前版本提供黑名单配置界面，但尚未在游戏中实际拦截表情。

```powershell
powershell -ExecutionPolicy Bypass -File windows\Manage-EmoteBlacklist.ps1
```

界面支持按名称、ID、角色系列或类型搜索，批量添加或移除表情。保存结果位于 `resources\emote_blacklist.json`；表情目录来自 `resources\emotes.json`。黑名单分别保存动态表情 ID（如 `1-0`）和文字信息 ID（如 `Taunt1`）。

## 仓库结构

| 路径 | 职责 |
| --- | --- |
| `windows/deploy/` | 正式运行模块；界面、内存读取和对局状态逻辑均位于此处 |
| `windows/tools/` | 可复用的构建、采集和资源生成工具 |
| `windows/tests/` | 与运行模块和构建工具对应的自动化测试 |
| `windows/runtime/` | 原生读取辅助程序；本地 Zig 工具链和缓存不会入库 |
| `deploy/` | 卡牌等部署期数据 |
| `resources/icons/` | 正式卡牌图标 |
| `resources/ui/` | 正式界面资源 |

预览图、审阅批次、下载源图和临时构建结果应写入 `build/` 或 `.work/`。这两个目录不属于产品源码，不提交到仓库。

## 开发与验证

运行全部测试：

```powershell
python -m pytest windows/tests -q
```

资源构建工具依赖 Pillow，测试依赖 pytest。正式卡牌图标生成通过 `windows/tools/build_card_icon_catalog.py` 和 `windows/tools/generate_standardized_card_icons.py` 暴露的命令行接口完成；各工具的参数以 `--help` 输出为准。
