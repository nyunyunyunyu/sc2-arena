# 三兵缩圈 / SC2

当前默认是双方属性完全相同的 **轻骑＋长枪＋弓手**。借用跳虫、狂热者、陆战队员的造型，使用用户授权的自定义数值，零升级、零护甲、无护盾/回血/额外技能。地图负责缩圈、伤害和终局，Python 规则只控制本方单位。人机优先使用 **Windows 原生 SC2 界面**，Linux SC2 4.10 的 RGB/Pygame 入口也保留。

新图为 `maps/ThreeSoldiersCounterRing.SC2Map`。旧原生近战图 `maps/ThreeSoldiersMeleeRing.SC2Map` 和旧三远程图 `maps/ThreeSoldiersRing.SC2Map` 的原始字节保留，旧录像仍可回放。旧组合分别用 `--rules sc2/rules-melee-native.json` 和 `--rules sc2/rules-terran.json` 启动。旧局不能计作新组合的战绩。

数值的时间单位与缩圈 HUD 相同，都是场景秒（16 loops）；Faster 下每墙秒约推进 1.4 场景秒。单一配置源为 `rules.json` 的 `combat`，构建器同时生成数据 XML 和原生界面的属性说明。

| 角色（造型） | 生命 | 移速 | 每轮攻击 | 间隔 | 射程 |
|---|---:|---:|---:|---:|---:|
| 轻骑（跳虫） | 100 | 4.0 | 10 | 1 秒 | 0.1 |
| 长枪（狂热者） | 100 | 1.75 | 4×2；对轻骑 15×2 | 1.2 秒 | 0.1 |
| 弓手（陆战队员） | 60 | 2.75 | 6 | 0.85 秒 | 5 |

轻骑是六兵中唯一的 Armored 类型；长枪每段对 Armored 加 11，因此只特攻轻骑。这个类型标签不提供护甲。双方生命、攻速、速度和特攻完全相同。保留普通移动、攻击、停止/坚守/巡逻；禁用冲锋、兴奋剂、潜地、变形等其余能力。近战 AI 保持贴身攻击，远程保留冷却时走位。小地图、正常视野、60/120/180/240 秒缩圈、300 秒按存活数结算不变。

克制验证用 `python -m sc2.maps.matchup_probe --output <新目录>`：快近战对站定/走打弓手、长枪对轻骑、长枪对站定弓手、弓手风筝长枪，各交换双方一次。每局诊断移除四个无关单位后，使用真实引擎伤害和移动，必须在首次缩圈之前取得预期结果。诊断不计入真人或训练 AI 胜率。

当前图验证汇总见 `runs/counter-validation.json`：39 项离线测试通过，`counter-matchups-002` 十个换边案例全部通过。轻骑胜弓手后剩 52 HP、胜走打弓手后剩 58/64 HP；长枪胜轻骑后剩 60 HP、胜直接对 A 的弓手后剩 22 HP；走打弓手胜长枪后剩 44/60 HP。另有 `counter-clock-002`、`counter-hazard-001`、`counter-mirror-002` 通过时钟/圈伤、同时全灭平局和完整镜像对战。13 局全为同一最终地图哈希、零动作错误、已保存录像。最终地图 SHA256：`2c1d875608214e234fcb3a32cd7144832e2472a38ba73c92bd5f3ca789eef4cc`。新阵容尚未真人实测，原生属性面板可读性尚未目视确认。

旧原生近战版本的 37 项离线检查、`melee-mirror-001`、`melee-clock-001`、`melee-simultaneous_hazard-001` 及 `sc2/runs/melee-validation.json` 仅证明旧图；其 SHA256 是 `77c482399367e36d898f8b4c13b9b83f6b5d6f46b4c1b478dffafc95bede01c1`。

## Windows 原生界面

本机隔离环境已安装。在项目根目录的 PowerShell 运行：

```powershell
.\sc2\start-windows.ps1
```

启动器自动连接人类和 AI 两方，不需要在战网大厅选择电脑玩家。**先在 PowerShell 按 Enter 确认准备好，再切到随后打开的「三兵缩圈 — 玩家窗口」；地图加载完成自动开局。** Windows 人机已改为实时运行，因此不再在地图第 0 帧等待终端输入或额外停 3 秒。游戏窗口中的 Enter 是聊天键。使用 SC2 原生鼠标、编队、快捷键和镜头操作；**Esc 是原生菜单键**，旧 PyGame 的 F4/F5 等按键说明不适用于此入口。

地图、策略、正常视野和录像采集保持一致。AI 的第二个客户端在启动和地图加载后按其进程 ID 隐藏，人类客户端以 1600×900 窗口启动。不要手动关闭 AI 客户端，这会中断连接。Windows 人机的 `realtime=true` 由引擎连续运行；Python 约每 4 loops 采样、最多每 4 loops 决策一次，迟到的观测按实际 loop 记录，不再按整除判断而漏掉 AI 决策。采集不会暂停引擎。Linux/PyGame、离线 duel 和原生 watch 仍保留原步进方式。

```powershell
# 原生窗口回放同版本录像；保留同目录的 manifest.json
.\sc2\start-windows.ps1 watch --replay 'sc2/runs/<本局目录>/game.SC2Replay'
# 后台双 AI；不作为真人战绩
.\sc2\start-windows.ps1 duel
```

本机 Windows 引擎为 5.0.17.98310 / Base98310，data `9C1F3D3D30CA252E33FC67CA05CBE1EA`。`windows-smoke-001` 已实际完成镜像对战、保存录像且退出无残留；地图、策略、规则哈希与 Linux 版一致。**不同引擎版本的对战记录分开统计**，回放也会校验版本。Windows 依赖安装见 [setup/README.md](setup/README.md)。

原生 UI 的 `windows-ui-check-001` 短诊断已实际显示五圈、倒计时和 SC2 操作面板；F2 全军选择及右键命令均可用，48 loops 内有三兵实际位移，零动作错误。该局按诊断上限停止，status=watchdog、无胜负，不能算真人比赛。`windows-watch-001` 使用同版原生客户端自然播完上面的镜像录像、exit0；进程扫描无 SC2 残留。

用户随后完成了 `20261107T050719.316252Z` 原生真人局，P1 Victory / P2 Defeat。期间反馈两个窗口和开始方式不清楚；已查明 PySC2 4.0 的 `_launch` 丢弃 `startupinfo`，旧隐藏请求未生效。现在直接隐藏本次启动的 AI 进程窗口，并验证其不可见；玩家窗口与终端显示中文开始提示。19 项离线检查覆盖只操作对应进程窗口、标题和隐藏失败检查。加载时窗口仍可能短暂出现，不承诺全程不抢焦点。

`windows-hide-001` 随后通过真实 Windows 镜像检查：两份客户端各在启动和加载地图后通过隐藏验证，共4次；自然结束、exit0、录像保存正常、无 SC2 残留。检查记录为 `sc2/runs/windows-hide-checks.json`。此验证只证明加载后的隐藏与引擎链路，修复后的真人窗口标题尚未另作实机交互测试。

用户反馈卡顿后，旧真人局日志测得 411 loops / 22.39 墙秒 = 18.36 loops/s，单步间隔中位数 62ms；这不是屏幕 FPS。现已将 Windows 人机切到引擎实时模式。`windows-realtime-probe-001` 在两个隐藏客户端、无人操作条件下推进 1200 loops / 52.515 秒 = 22.85 loops/s，零动作错误、录像保存正常；按预设上限终止，status=watchdog、source=real_engine_diagnostic，不能计作真人战绩或完成比赛。结果见 `sc2/runs/windows-realtime-probe-001/timing-summary.json`。19 项离线检查覆盖启动前确认、无手动 sleep、迟到/非4倍数观测仍触发决策。前台渲染 FPS 和真人手感尚未复测。

血条常显：下一局前在 SC2 的「选项 → 游戏性 → 显示单位状态条」选「总是」并保存；对局中可由 F10 进入。血条显示比例，选中单位后底部面板显示具体 HP。原三远程配置均无升级：Marine 45HP/0甲/射程5/伤害6，Marauder 125HP/1甲/射程6/伤害10（对重甲20），Hellion 90HP/0甲/射程5/伤害8（对轻甲14）。恶火本身也是轻甲，掠夺者攻击它没有重甲加成；这组没有完整的三角克制。武器伤害还要扣目标护甲。常显设置说明见[暴雪操作指南](https://news.blizzard.com/en-us/article/4552955/game-guide-special-control)。

## Linux / PyGame 入口与历史验收

`human-user-001`已由用户本人完成，真人P1败、AI P2胜；录像和输入记录已保存，窗口与引擎已正常退出。用户确认五条圈线、剩余时间和缩圈倒计时都能看清。

以后新开局，在`F:\project\sc2`的PowerShell运行：

```powershell
# 人机：默认 RGB、首帧暂停
.\sc2\start.ps1

# AI 镜像对战
.\sc2\start.ps1 duel

# 录像：将占位符替换为实际 .SC2Replay 文件路径
.\sc2\start.ps1 watch --replay '<实际录像路径>'
```

操作：**Esc**开始，**左点/框选**己兵，**右键空地**移动、**右键可见敌人**攻击；**Pause**暂停/继续，**F4**退出并保存。终局自动关闭并保存记录。

**原接受计划的功能验收已齐：最终地图、13项预检、固定双侧8局、完整watch回放及真人完整一局。** 真人局记录了1次全军选择、17次地面Smart和4次可见敌目标Smart；其中16次地面命令有实际Move订单和位移，4次敌目标命令有Attack订单，最后一次地面命令遇终局无后续可判。零动作错误，实际录像game_speed为4；这是SC2枚举，不称AoE“标准”速度。中途Pause/F4/F5及watch快捷键未逐项真人测试，保留为覆盖限制。

Linux 4.10.0.75689/Base75689/dataB89B5D6FA7CBF6452E721311BFBC6CB2 已实测可运行。环境路径、已验证的 bootstrap 命令和 OSMesa 配置见 [setup/README.md](setup/README.md)。

## 当前可确认的结果

地图实际采用官方Flat32模板、可读Galaxy/XML源码及smpq（StormLib）构建`.SC2Map`，替代原计划的Editor + `.SC2Components`路线。本次没有运行或验收Editor工程流程；构建来源和方法见 [maps/README.md](maps/README.md)。

- 最终地图SHA256为`8d05314c6864bf2b06f14fc21fe6bfac278e5cf1e4754a2d7f9876c9be647a40`，rules为`26e5cfbb5b8d2c0d983018952b9848456a3324e73b23a8055edb251b927bf9f6`，双方仍为原生Marine45/Marauder125/Hellion90 HP、零升级。
- 最终图`final-map-preflight-002`的13项实机检查通过：Move位移1.59375、Stop后连续三帧位置稳定且订单清空；敌军从Visible转为连续四帧Absent，无HP/位置泄漏；零action_errors，真实录像已保存。001在开引擎前因诊断脚本漏flags初始化失败并保留。002是明确的诊断，不是完整对战或八局成绩。
- `mirror-001` 在300loops/场景score18取得真实 P1 Defeat、P2 Victory，零 action_errors，录像与实际速度枚举4已保存。这是一局同策略镜像，不是对人类实力证明。
- 地图8项实机探针覆盖消灭、同tick全灭平局、300秒同数/异数终局及内/边/外边界；四档伤害实测为2/3/4/6 HP/场景秒，后续圈不叠加旧圈伤害。这些显式诊断含补血/移动/杀兵，不能算零修改比赛。
- 时钟实测 **16loops=1场景秒、4800loops=300场景秒**。最终rules已落实`clock_verified=true`、每loop0.0625场景秒、movement_speed_scale=1。

最终包使用280个无视野/碰撞、不可选择攻击的Neutral Beacon点显示五圈；实际是绿色，以当前圈较大点区分，不声称黄色。1/59/60/61场景秒的正常fog与六兵计数通过，60秒当前圈切换到半径11；原生三种战斗兵API数据不变。逆转视觉改动后能精确重建原已测基线hash，因此无变化的圈伤/终局规则证据可复用。默认RGB已升至1024×768并通过相关检查；地图Dialog不进API RGB，Renderer独立显示HUD，用户已确认实际圈线和倒计时可读。真人局在场景score23结束，未亲历首次60秒缩圈；全部阶段仍由引擎探针和最终包等价证明覆盖。

固定hold功能组使用最终版、seeds1/2/3/4双侧8行，每行仅一次；全部complete/exit0、零action_errors、实际4loop步长、8份唯一录像，运行后无SC2残留，三次完整生命周期要求由本组满足。结果为5胜3负、unknown0，不能解释为真人胜率或独立随机试验：

| 策略侧 | 对固定hold的结果 |
|---|---|
| slot0 / P1 | 4胜0负 |
| slot1 / P2 | 1胜3负；仅seed4胜 |

前两局只读排查未发现敌我/槽位接线错误：一侧在追击门槛外停于圆心，另一侧因挤位跨过门槛后先暴露Hellion，受到原生自动反击。这揭示规则对距离门槛敏感，不能把功能通过说成战术很强；评测后未调整冻结策略。完整记录见本轮 [八局报告](../.retro/goals/20261107-5afe5d35e47f/tasks/sc2_acceptance/final-eval-001/report.md) 和[只读核验](../.retro/goals/20261107-5afe5d35e47f/tasks/sc2_acceptance/final-eval-audit.json)。

watch首次因缺少地图数据失败，已保留日志并最小修复：只把通过同局录像/地图SHA校验的bytes传入`RequestStartReplay.map_data`。3项专测通过；`watch-final-identity-002`自然完整播放同一identity录像2576步，115.897秒、22.227fps、exit0，零运行者输入/信号且清理无残留。它证明回放引擎链路，**不证明观看画面、适屏或快捷键交互已由人验证**。

八局对应旧runner `21c552a9…`，快照已保留；当时验收runner `63d10c53…`仅改watch/helper。此前差分确认play/main、比赛依赖和策略不变，因此原验收复用八局证据。后续 Windows 入口改动不改写这些历史结果，也不把旧局当作 Windows 验收。

## 已运行的离线检查

下列命令均在项目根目录执行过，未启动游戏：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:TEMP='F:/project/sc2/.cache/sc2-tests'
$env:TMP=$env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
& .deps/sc2-win/Scripts/python.exe -B -m unittest sc2.test_agent sc2.test_runtime sc2.test_eval sc2.maps.test_build -q
& .deps/sc2-win/Scripts/python.exe -B -m sc2.eval --help
& .deps/sc2-win/Scripts/python.exe -B -m sc2.run duel --help
```

原 Linux 验收的评测账本8项自检和runtime 15项回归通过；夹具是合成数据，不是对战成绩。新增 Windows 分支后使用 `.deps/sc2-win/Scripts/python.exe -m unittest sc2.test_runtime -v` 检查平台、原生窗口配置、真人槽位及无 PyGame 的回放路径。实际引擎证据独立于这些离线检查。

Windows薄入口 [start.ps1](start.ps1) 已交付，默认参数为human/RGB/首帧暂停；10项mock和实际`.\sc2\start.ps1 --help`通过。真人已完成human局并确认画面，自动UI工具仍未取得成功截图，不能混淆两种证据来源。watch已创建可视窗口并完成引擎播放，未另取得用户逐项watch交互反馈。

## 记录与评测约定

运行器入口为 `sc2.run` 的 `human` / `duel` / `watch`。`view-slot` 是观看/人类控制的 Participant 索引；`policy-slot` 是 `duel --opponent hold` 时规则策略所在侧。最新源码中human记录真实AI槽，镜像记录`actual_policy_slots=[0,1]`、`policy_slot=null`，它们不是写死的SC2 player_id。`--rgb`请求图像，不能单独理解为已经打开窗口。

human使用同一SC2Env非实时同步步进：UI每1loop、policy每4loops，目标墙钟pacing为22.4loops/s。初始暂停曾长时间保持loop0，用户恢复后完成比赛；Pause/Esc开始、Pause停/续、F4退出、F5新局的pygame分支及runtime路由已有检查，但中途Pause/F4/F5没有逐项真人输入证据。duel的policy cadence固定4，不能任意改成8等值。

每局独立目录保存 `manifest.json`、`events.jsonl`、`observations.jsonl.gz` 和 `game.SC2Replay`。manifest记录引擎、地图/策略/规则/单位数据哈希、真实玩家编号、速度及结果。最新日志复制观测后裁掉RGB/feature像素并以`observation_omitted_fields`声明，raw/fog/score/actions保留；原policy输入不变，关键截图单独PNG保存，不能再称裁剪日志为完整原proto。未知/超时/退出不是败局；动作错误和实际观测loop间隔另行记录。

`sc2.eval` 提供 `plan` / `run` / `report`：从正常、完整结束的hold duel manifest锁定条件（如`final-identity-001`，`source=real_engine`、`mode=duel`、`status=complete`、`opponent=hold`）；diagnostic preflight不合格。预登记4个固定seed × 两侧8行，顺序调用同一duel入口。hold仍允许游戏原生自动攻击，评测用途是功能与行为检查。错误或未知保留在原行；恢复只执行未启动行，不自动重试或替换失败。地图、策略或比赛运行代码改变后，应保留旧组并建立新组。

报告的 `eligible` / `all_eight_eligible` 表示结果与固定身份/文件证据满足账本检查，不表示全部功能通过或策略很强。动作错误、实际 cadence、回放可读性及完整圈规则还需各自验收。报告不输出挑选成功局后的胜率。

## 真人结果与覆盖边界

`human-user-001`于本地11月6日20:31:14自然结束，P1末观察loop382、P2为383，公开score23，实际推进约18.06墙秒；初始暂停等待不计入比赛时长。真人Defeat、AI Victory，录像SHA为`590232d0a6ff36c71dd8b354c9b2680e1128a887268df2f59650bdd2a3c837c3`，exit0且完整进程扫描无残留。命令分类和后续实际订单见[真人输入报告](../.retro/goals/20261107-5afe5d35e47f/tasks/sc2_runtime/human-baseline-report.md)。

[首局交战分析](../.retro/goals/20261107-5afe5d35e47f/tasks/sc2_policy/human-001-analysis.md)保留逐时刻HP和开火证据；本局早于首次缩圈，未有圈伤，不根据单次结果猜测玩家意图或改策略。

中途Pause/恢复、F4主动退出、F5新局和watch快捷键没有逐项真人实测；离线pygame映射、runtime路由及真实正常保存/退出分别提供支持，不能改写成真人逐键通过。[独立完成审计](../.retro/goals/20261107-5afe5d35e47f/tasks/sc2_review/completion-audit.md)确认这些不是原计划另设的交付前置条件。

一次真人结果不足以估计人机胜率。双AI、hold对手、合成测试、agent模拟鼠标键盘都不能当真人战绩。完整状态表见本轮 [acceptance.json](../.retro/goals/20261107-5afe5d35e47f/tasks/sc2_acceptance/acceptance.json)，执行说明见 [RUNBOOK](../.retro/goals/20261107-5afe5d35e47f/RUNBOOK.md)。

HUD、圈伤和终局使用地图公开score的同一场景秒时钟。实测换算是16loops/场景秒；22.4loops/s仅是窗口目标墙钟pacing，实际墙钟耗时受性能影响。功能验收通过不等于真人战力已验证。
