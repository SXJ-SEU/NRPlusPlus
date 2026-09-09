# NRPlusPlus 基础功能交接

> 本文只涵盖项目基础/核心功能。表情黑名单是独立功能，已在另一份交接中维护，本文不描述其需求、实现、测试或后续工作。

## 1. 总体目标

NRPlusPlus 的基础功能是在 Windows 上连接运行 Null's Royale 的 MuMu 安卓模拟器，通过只读方式读取游戏进程内存，并实时显示：

- 对局是否处于活动状态；
- 双方单位和建筑的位置、阵营、名称、等级与生命值；
- 己方与对手的圣水；
- 内部对局计时；
- 己方当前四张手牌和下一张牌；
- 对手已经使用过的卡牌，未使用位置显示 “?”；
- 对手已使用卡牌的平均圣水费用，保留一位小数；
- 单次 native 内存采样耗时。

界面视角必须始终是己方蓝色、位于下方；对手红色、位于上方。跨对局运行时不得沿用上一局的身份、圣水、手牌或已展示卡牌状态。

## 2. 已实现功能

### 非注入式内存读取

- Windows Python 进程通过 ADB 连接模拟器。
- 模拟器内运行静态 x86_64 Linux ELF helper。
- helper 只读打开 /proc/PID/mem 并读取 /proc/PID/maps。
- 不向游戏进程注入代码。
- native helper 默认每 20ms 输出一次 JSON 快照。
- Python 辅助读取器处理更复杂但频率较低的身份、手牌和牌组解析。

### 战场绘制

- Pygame 窗口尺寸为 760×720。
- 左侧绘制 18,000×32,000 逻辑坐标的战场、河流、桥梁、单位和塔。
- 己方使用蓝色，对手使用红色。
- 若本地玩家在游戏内部是 side 0，_arena_point() 会同时翻转 X/Y，使己方仍显示在下方。
- 单位按 card ID 映射为名称，生命值显示为绿色血条。
- 曾显示的 Entities 数量已按用户要求移除，但实体仍继续用于战场绘制和对手出牌兜底识别。

### 双方圣水

- native helper 从 battle HP state 中读取两个 player resource。
- 游戏内部以万分之一圣水储存，读取结果除以 10,000。
- 尚未提交完成的待部署卡牌费用会从 raw elixir 中扣除，使结果更接近游戏圣水条。
- Python 使用账户 ID 匹配本地玩家在 player list 中的真实索引，再映射 player_elixir[index]。
- 修复了双方值被错误当成同一个值、local 值滞后一秒以及第二局 local 永远为 0 的问题。
- 每次 battle active 状态变化都会销毁旧 reader，并为下一局创建新 reader。

### 己方手牌和下一张牌

- 扫描 MuMu/Scudo 内存 size class，定位 battle model、四张手牌数组和 next-card queue。
- 手牌内保存的是 0–7 的牌组索引。
- 卡牌 wrapper 解析出实际 card data ID。
- deploy/cards.json 提供卡牌名称和圣水费用。

### 对手已使用卡牌

- 内部可以解析对手的八张牌，但界面不会提前公开整副牌。
- 对手当前手牌通过账户 ID 与对应 hand model 绑定。
- 第一次读取只作为 baseline，不算出牌。
- 某个 deck index 从四张手牌中消失时，认为该牌已使用。
- 对手第一张使用的牌显示在第一行左侧，第二张在第一行右侧，之后按首次使用时间依次填充。
- 每个 deck index 只展示一次。
- native 实体流作为补充证据：新出现的对手实体也可揭示卡牌。
- 没有 HP component 的短时 spell/deployment entity 也会被 native helper 输出，HP 记为 -1。
- Spirit Empress 的 battle form ID 26000104、26000105 会映射到 deck card 28000025。
- 若 battle form 没有显式 alias，会尝试按卡牌名称在对手牌组中唯一匹配。
- The Log 等同费用法术不再依靠“圣水下降量 + 同费用单位”猜测，而通过手牌转移识别。

### 对手平均费用

- 只统计当前已经展示的对手卡牌。
- 按 card ID 去重。
- 使用 deploy/cards.json 中的 elixirCost。
- 无已知卡牌时显示 “--”，否则保留一位小数。

### 状态与性能信息

- Battle clock 是游戏内存中的内部对局时钟，以秒显示并保留一位小数；它不是系统时间。
- Memory read 是 native helper 生成一次快照所花的微秒数，只代表 helper 的内存读取/编码时间，不等于从游戏画面到桌面绘制的端到端延迟。
- native 采样周期从 100ms 降为 20ms。
- Pygame 按 60 FPS 刷新。
- Python secondary reader 在 active battle 中约每 100ms poll 一次。
- 尚未做严谨的端到端延迟基准。

## 3. 架构和主要设计

    Null's Royale (MuMu x86_64 process)
            │ read-only /proc/PID/mem
            ▼
    cr-arm-entity-stream-x86_64
      - 20ms native JSON stream
      - battle active / both elixir / entities / basic hand data
            │ adb exec-out
            ▼
    capture_native_entity_stream.py
      - normalize_snapshot()
      - BattleStateCoordinator
      - secondary reader thread (~100ms)
            │
            ├── proc_memory.py
            │   - account identity
            │   - hand-model binding
            │   - deck/card resolution
            │
            ├── SnapshotStore
            ├── JSONL capture log
            ▼
    minimal_visualizer.py (Pygame 60 FPS)

采用 native + Python 混合架构：

- native helper 直接 pread，适合高频实体和双方圣水读取；
- Python 更适合 Scudo 扫描、指针验证、账户匹配和多来源状态合并；
- 慢速扫描不会阻塞 20ms native stream 或 Pygame 主线程。

## 4. 重要约束

- Windows 模拟器无法使用 Frida；Frida 方案已经废弃，不应恢复。
- 当前方案要求 MuMu 的 adbd 本身以 root 运行；启动器检查 adb shell id -u 必须返回 0。
- 当前目标设备为 x86_64，因此 helper 是 x86_64-linux-musl 静态 ELF。
- 所有内存偏移和对象布局都依赖当前 Null's Royale/libg.so 版本，游戏更新后可能失效。
- 必须保持只读，不修改游戏内存。
- 每次代码修改后需要运行测试并同步 GitHub。
- 不能为了识别法术而仅按圣水费用猜牌，同费用卡牌会造成歧义。
- 对手牌组内部解析结果只能用于将事件映射为卡牌，UI 只展示已使用卡牌。

## 5. 相关文件和组件

### windows/tools/native-stream/cr_arm_entity_stream.c

native 数据流实现。关键偏移：

- MANAGER_GLOBAL = 0x19bbdd8
- MANAGER_CONTEXT = 0x248
- CONTEXT_BATTLE = 0x90
- BATTLE_HP_STATE = 0xa8
- HP_STATE_REGISTRY = 0x08
- HP_STATE_PLAYER_COUNT = 0x60
- HP_STATE_PLAYER_RESOURCE = 0xe0
- PLAYER_RESOURCE_PENDING = 0x248
- PLAYER_RESOURCE_ELIXIR = 0x2f8
- MAX_OBJECTS = 2048
- ENTITY_SIZE = 0x124

关键函数：

- read_player_elixir()：读取玩家圣水并扣除 pending cost。
- read_battle_ui() / find_battle_ui()：读取 UI clock、手牌和 next card。
- sane_entity()：验证实体字段范围。
- find_libg_base()：根据 maps 和 mapping offset 处理 ASLR。
- 每行输出一个 entity_stream JSON 对象和 read_us。

### windows/runtime/cr-arm-entity-stream-x86_64

- 已构建并纳入 Git。
- 当前文件大小 53,544 字节。
- 修改 C 源码后必须重新构建并一并提交该二进制。

### windows/tools/build_native_helper.ps1

- 使用 Zig cc。
- target：x86_64-linux-musl。
- 参数：-O3 -std=c17 -static -s。
- 输出到 windows/runtime/cr-arm-entity-stream-x86_64。

### windows/deploy/adb_runtime.py

- AdbConfig：ADB 路径和设备串号。
- AdbRuntime.root_shell()：通过已经 root 的 adbd 执行只读 shell 命令。
- 类名/字段仍保留早期 su 兼容痕迹，但实际路径不再添加 su。

### windows/deploy/proc_memory.py

- MemoryRegion / parse_proc_maps()：解析 maps。
- RootProcessMemory：使用 ADB exec-out + dd 批量读取进程内存。
- BattlePointers：battle holder/UI/model/hand/queue 指针。
- PlayerHandPointers：特定玩家的 model/hand/queue。
- BattleStateLocator.locate()：扫描 Scudo size classes，寻找有效 battle model。
- poll_elixir()、poll_hand_indices()、poll_next_deck_index()：低成本二次读取。
- poll_local_player_index()：以账户 ID 确定本地玩家索引。
- locate_player_hand_pointers()：把 hand model 绑定到指定 battle player。
- poll_card_object_deck()：读取己方牌组 wrapper。
- poll_opponent_card_deck()：读取对手牌组 wrapper。
- _poll_provider_card_deck()：provider/container/wrapper 到 data ID 的公共解析。
- _resolve_battle_hp_state() / _resolve_battle_entity_collection()：libg 全局链。
- _poll_battle_entity_collection()：直接从 battle registry 获取实体。
- 文件中仍保留旧的 vtable 扫描路径 poll_entities()；主运行路径使用 native helper/registry 路径。

重要设计：不能将 model + 0x288 当作 index-aligned deck。实测该字段更像逐步向客户端揭示的卡牌集合，因此当前通过 live card wrappers 解析真实牌组索引。

### windows/tools/capture_native_entity_stream.py

- DEFAULT_STREAM_INTERVAL_MS = 20。
- CARD_ID_ALIASES：当前包含 Spirit Empress 两个 battle form。
- SnapshotStore：线程安全保存最新快照。
- BattleStateCoordinator：
  - 管理每局独立 reader；
  - battle start/end 时清空状态；
  - 合并 secondary state；
  - 根据账户索引修正 local_side；
  - 合并手牌转移和实体首次出现事件；
  - 将对手牌按首次使用顺序映射到 8 个 UI slot；
  - 映射双方圣水。
- normalize_snapshot()：验证 native JSON 并将 -1 转为 None。
- played_hand_indices()：比较前后四张手牌，返回消失的 deck index。
- make_battle_reader()：构造每局 secondary reader 和局内状态。
- main()：校验 root、查找 PID、push/chmod helper，并启动数据流、secondary thread、日志和 UI。

默认日志为 windows/captures/native_entity_stream.jsonl。该目录被 Git 忽略，启动时以写模式覆盖旧日志。

### windows/deploy/minimal_visualizer.py

只描述基础功能部分：

- _load_card_catalog()：加载名称和费用。
- _average_card_cost()：计算已展示对手卡牌平均费用。
- _entity_label()：实体标签。
- _arena_point()：坐标归一化和本地视角翻转。
- _draw_arena()：战场、阵营颜色、实体和 HP。
- run()：60 FPS Pygame 事件循环及状态面板。

该文件还包含一个独立功能入口；它不属于本文范围，继续基础功能时不要误改或删除那部分代码。

### 其他文件

- deploy/cards.json：卡牌 ID、英文名称和费用。
- windows/deploy/subprocess_utils.py：隐藏 Windows 子进程控制台，并抑制子进程崩溃弹窗。
- windows/Setup.ps1：安装 Pygame 并构建 helper。
- windows/tests/test_native_snapshot.py：基础数据与状态协调测试。
- windows/tests/test_minimal_visualizer.py：其中两个 arena perspective 测试属于基础功能。
- .gitignore：忽略 captures、Zig cache、本地 portable Zig 和本地命令笔记。

## 6. 关键实现细节

### 本地身份与视角

native helper 输出的 local_side: 1 只是占位值。真正身份由 Python：

1. 从本地 hand model 获取账户 ID；
2. 从 battle HP player list 获取各玩家账户 ID；
3. 找到匹配索引；
4. 用该索引覆盖 snapshot 的 local_side；
5. 用同一索引映射双方圣水；
6. 可视化层根据最终 local_side 决定颜色和是否旋转坐标。

不要重新依赖固定 side 或 player-list 顺序。

### 跨对局重置

BattleStateCoordinator.set_active() 只在 active 状态变化时操作：

- 清空 secondary state；
- 清空双方观察到的 card IDs；
- 清空 active entity keys；
- battle start 时调用 factory 创建全新 reader；
- battle end 时将 reader 设为 None。

这用于修复第一局结束后旧值残留、第二局 local 圣水为 0 等问题。

### 对手出牌顺序

make_battle_reader() 保存 previous_opponent_hand、opponent_play_events 和 played_opponent_indices。

第一次 hand snapshot 只建立 baseline。后续消失的 deck index 按当前时间写入事件。Coordinator 同时维护 native entity 的首次出现时间，并按 (observed_ms, source_priority, card_id) 排序、去重后填入 UI。相同卡牌不会重复占用位置。

### 圣水精度

输出为整数 0–10，不包含小数圣水。pending deployment cost 会提前扣除，但仍可能受到游戏内部提交时序和 20ms 采样边界影响。

## 7. 当前仓库状态

检查日期：2026-09-09。

- 当前分支：main。
- 写本文前 HEAD：c499e0c。
- main 与 origin/main 一致。
- 写本文前 git status 只输出 “## main...origin/main”。
- 无 staged、unstaged 或非 ignored untracked 变更。
- git diff --check 通过。
- 当前根 README 没有基础功能安装/运行文档，这是待补技术债。

相关基础功能提交：

- eefca8b：Windows 部署、测试、20ms native stream、双方 resource。
- 997502a：修复玩家/圣水映射并简化 UI。
- 2240ee9：展示对手牌组信息。
- 7d13947：只展示对手使用过的卡牌。
- df85f35：使用已解析 battle side 修复对手卡牌判断。
- 7aec247：按首次使用顺序排列。
- ef55566：无实体法术和 card form alias。
- f13baae：通过对手手牌转移精确识别出牌。

## 8. 已执行测试与实验

### 当前聚焦测试

2026-09-09 执行：

    python -m unittest windows.tests.test_native_snapshot windows.tests.test_minimal_visualizer.ArenaPerspectiveTests.test_side_one_is_local_blue_and_drawn_at_bottom windows.tests.test_minimal_visualizer.ArenaPerspectiveTests.test_side_zero_perspective_rotates_the_arena -v

结果：19 项全部通过。

覆盖：

- 默认 native interval 不高于 20ms；
- 双方 resource 根据 local player index 正确映射；
- local side 修正实体阵营；
- 只展示对手已使用牌；
- 首次使用顺序；
- 平均费用去重；
- Spirit Empress battle form alias；
- 同费用 The Log/Ice Golem 通过 hand transition 精确区分；
- 第一份 opponent hand 只作 baseline；
- 跨对局清空旧状态并创建新 reader；
- 无效圣水值归一化；
- 账户 ID 解析本地玩家；
- hand model 与 battle player 账户绑定；
- local side 0/1 时都保证己方蓝色在下方。

同时执行：

- Python py_compile 检查核心模块：通过；
- git diff --check：通过。

### 历史实机观察

用户和开发过程中的多局测试曾复现并推动修复：

- 战场上下/红蓝视角颠倒；
- 第一局正常、第二局 local 圣水恒为 0；
- opponent elixir 实际显示己方圣水；
- 对手牌长期显示 “?”；
- The Log 等无持久实体法术无法识别；
- Spirit Empress 使用 battle form ID 导致牌组 ID 不匹配；
- 对手卡牌展示顺序不是实际首次出牌顺序。

当前这些场景都有实现和单元测试覆盖，但游戏版本升级后仍需重新做多局实机回归。

### 当前环境

- Python：3.13.5
- Pygame：2.6.1
- Zig：0.15.2
- ADB：1.0.41 / 35.0.1-11580240
- 设备 ABI：x86_64
- MuMu 设备型号：2201123C
- 游戏包名：nullsroyale.rel.free
- 检查时游戏 PID：2698（不可硬编码）
- portable Zig：windows/runtime/zig/zig.exe，被 Git 忽略。
- 已知 ADB：D:\Program Files\platform-tools\adb.exe。

## 9. 已知问题、风险和技术债

### P0：默认 ADB 串号在当前环境不可用

程序和 Setup 文案默认使用 localhost:5557，但当前 adb devices -l 显示 127.0.0.1:5557 和 emulator-5556。

实际验证：

- adb -s localhost:5557 get-state：失败，device not found；
- adb -s 127.0.0.1:5557 get-state：成功。

当前启动必须显式传 --serial 127.0.0.1:5557。下一步应修正默认值或实现可靠的设备自动选择，且避免两个设备同时存在时误选。

### 其他问题

- shutil.which("adb") 要求 ADB 在 PATH；Setup 不安装 ADB，也不支持 --adb-path。
- PID 只在启动时解析一次；游戏重启后插件不会自动重连。
- libg 偏移、Scudo class 和结构字段均为版本相关硬编码。
- secondary locator 首次扫描可能读取较大的 Scudo 区域，启动/每局重新定位可能较慢。
- 尚无端到端延迟测量；20ms 只是采样周期。
- opponent hand 约 100ms 采样；极端快速连续变化可能漏掉中间过渡。
- hand model 绑定失败时退回 entity 检测，纯法术仍可能漏识别。
- alias 表很小，其他变身/进化/召唤形态可能需要新增映射。
- 对手索引逻辑针对标准 1v1，1 - local_player_index 不适用于更多玩家模式。
- poll_provider_card_deck() 只要求至少四个合法 ID，因此可能短暂得到部分牌组。
- 己方 deck index 若观察到冲突，会在本局被永久标为 unresolved。
- native entity 无有效 HP component 时仍输出，HP/max_hp 为 -1；这是识别短时实体所需。
- status 文案可能为 Battle detected，但颜色以是否有 entities 决定；空场时可能为灰色。
- 本地身份尚未解析的短暂阶段，opponent elixir 可能显示 “--”。
- 捕获日志每次启动用 w 打开，会覆盖上一份日志。
- AdbRuntime 中 _root_ready / _root_via_su 当前没有实际控制作用。
- proc_memory.py 保留旧 vtable-based entity 代码，与当前 registry/native 路径并存。
- 没有真正的自动化模拟器集成测试。

## 10. 尝试后放弃的方案

### Frida

Windows 模拟器无法使用。相关 hook/raw capture 文件已删除；当前 tracked files 中没有 Frida、hook_raw_capture.js、run_raw_capture.py 或 queue_overlay。

### 固定 side / 固定 player-list 顺序

曾导致己方显示为红色上方、对手显示为蓝色下方，以及双方圣水错位。已改为账户 ID 匹配真实本地玩家索引。

### 复用第一局 reader

曾导致对局结束后旧值残留、第二局 local 圣水恒为 0。已改为 battle transition 时完全重建 reader。

### 直接公开对手整副牌

不符合用户需求。当前内部牌组只用于事件映射，UI 未使用位置显示 “?”。

### 仅依赖实体检测对手出牌

会漏掉 The Log 等无持久实体的法术，并受到 battle form ID 影响。当前以 opponent hand transition 为主、entity 为兜底。

### 根据圣水下降量和费用猜法术

同费用牌无法可靠区分，因此未采用。

### 使用 model + 0x288 作为完整 index-aligned deck

实测该字段是逐步揭示集合而非稳定索引牌组，已改为 provider/card wrapper 路径。

### 旧 x86_64 vtable 偏移作为主实体来源

偏移容易过期。当前 native helper 直接从 battle registry collection 读取实体；旧 Python 路径只作为遗留代码保留。

### 100ms native 采样

用户感到明显延迟，已降至 20ms。helper 参数允许的最低值为 10ms。

## 11. 尚未完成的工作

1. 修复默认设备串号/设备发现。
2. 补充基础功能 README：安装、ADB root、启动、参数、日志、构建。
3. 在当前游戏版本做至少 3 局连续实机回归：
   - 两种 local player index；
   - 局间退出和重新开始；
   - 双方圣水；
   - The Log；
   - Spirit Empress；
   - 对手首次出牌顺序；
   - 平均费用。
4. 测量真实端到端延迟，而不只看 read_us。
5. 增加游戏进程重启后的自动重连。
6. 将 ADB 路径变为参数或实现可执行文件发现。
7. 为游戏版本/偏移失效提供明确诊断。
8. 扩展 card form alias 数据，最好移到数据文件。
9. 改善 secondary reader 的事件观测，降低轮询漏过渡风险。
10. 清理未使用的旧 entity/vtable 扫描代码，前提是先做回归验证。
11. 增加模拟器集成测试或可回放的脱敏内存快照测试。

## 12. 推荐后续顺序

### 第一优先级

1. 修复 ADB serial 默认值：
   - 最小修复：默认改为 127.0.0.1:5557；
   - 更可靠：列举 devices，按明确规则选择，并在多设备时要求显式指定。
2. 更新 Setup 输出和基础 README。
3. 运行全部单元测试。

### 第二优先级

4. 启动 MuMu，确认 root、包名和 ABI。
5. 连续运行多局实机回归并保存 JSONL。
6. 对照游戏录屏/时间戳测量端到端 P50/P95 延迟。

### 第三优先级

7. 自动处理 PID 变化和 ADB 断连。
8. 将硬编码 offsets/aliases 版本化。
9. 清理 legacy reader 路径并补集成测试。

## 13. 构建、部署和调试

### 环境准备

    cd E:\NRPlusPlus
    powershell -ExecutionPolicy Bypass -File windows\Setup.ps1

Setup 会查找 Python、安装 Pygame、查找 Zig 或使用 portable Zig，并构建 native helper。

### 单独构建 helper

    powershell -ExecutionPolicy Bypass -File windows\tools\build_native_helper.ps1 -Zig E:\NRPlusPlus\windows\runtime\zig\zig.exe

### 当前环境启动

必须确保 ADB 在 PATH；当前可执行文件位于 D:\Program Files\platform-tools\adb.exe。

    python windows\tools\capture_native_entity_stream.py --serial 127.0.0.1:5557 --package nullsroyale.rel.free

可选参数：--interval-ms 20、--duration SECONDS、--headless、--log PATH。

运行器会把 helper push 到 /data/local/tmp/cr-arm-entity-stream，然后 chmod 755，并通过 adb exec-out 持续读取 JSON。

### 快速环境检查

    adb devices -l
    adb -s 127.0.0.1:5557 shell id -u
    adb -s 127.0.0.1:5557 shell getprop ro.product.cpu.abi
    adb -s 127.0.0.1:5557 shell pidof nullsroyale.rel.free

预期 id -u 为 0，ABI 为 x86_64，pidof 返回当前 PID。

### 测试

    python -m unittest windows.tests.test_native_snapshot -v
    python -m unittest windows.tests.test_minimal_visualizer.ArenaPerspectiveTests.test_side_one_is_local_blue_and_drawn_at_bottom windows.tests.test_minimal_visualizer.ArenaPerspectiveTests.test_side_zero_perspective_rotates_the_arena -v

修改 C 后：

1. 重新构建 helper；
2. 检查 tracked binary 是否更新；
3. 在 MuMu 上 push 并运行；
4. 验证 JSON 每行可解析；
5. 验证 battle_active、player_elixir、entities、read_us；
6. 提交 C 源码和二进制；
7. 推送 GitHub。
