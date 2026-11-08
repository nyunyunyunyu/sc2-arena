# 三兵缩圈 / SC2

双方各一名轻骑、长枪、弓手，使用相同的公开自定义属性，在小地图上限时缩圈对战。

双击仓库根目录的 **`start.cmd`** 即可自动配置环境并启动 Windows 原生客户端，也可在 PowerShell 运行：

```powershell
.\start.cmd
```

双击后可选对手：`1` / `default` 为原有 AI，`2` / `a` 为决赛冠军 A，`3` / `b` 为决赛 B，`4` / `a2` 为护弓修订版 A2，`5` / `a3` 为远程残局修订版 A3，`6` / `a4` 为最新撤退路线与轻骑救援版 A4；直接按 Enter 保持原有 AI。也可跳过选择菜单：

```powershell
.\start.cmd human --ai a4
.\start.cmd human --ai a3
.\start.cmd human --ai a2
.\start.cmd human --ai a
.\start.cmd human --ai b
.\start.cmd human --ai default
```

A/B 是决赛冻结版的原样副本，双方属性和正常视野规则不变。每局记录所选 `policy_name` 与源码哈希。A 的 3:0 决赛成绩来自 Linux 4.10，不能当作 Windows 人机胜率。命令行不传 `--ai` 时仍使用原有 AI；`duel --ai a` 会让两侧使用同一个 A 策略。联机各自选择本方 AI，例如 `start.cmd join --address 192.168.1.20 --control ai --ai b`；真人端不替远端选择 AI。联机的 `--yes` 会跳过选择菜单和准备提示，未传 `--ai` 时保持原有 AI。

首次运行自动准备 uv、Python 3.10 和锁定依赖。未配置 `SC2PATH` 时会询问游戏安装目录（包含 `SC2Data`、`Versions`），验证后保存到根目录 `.env`，以后直接复用。已有环境变量优先于 `.env`。游戏本体需自行安装，不包含在本仓库中。

联机默认空密码：`./start.cmd host --control human --rounds 5` 建立连续 5 局房间；另一台运行 `./start.cmd join --address <房主IPv4> --control human`。局数只由 host 设置，省略时为 1 局。每局准备倒计时约 5 秒、结束停留 5 秒，随后复用游戏客户端载入下一局并分别保存录像。若需要密码，双方额外传入相同的 `--key`（8～128 字符）。

真人镜头距离 30；AI 可见窗口距离 100，居中俯瞰全图。第一次缩圈后，当前边界与更外面的圈线显示红色，尚未启用的内圈保持绿色。

游戏默认独占全屏，使用 SC2 中设置的游戏分辨率；加 `--windowed` 使用 1600×900 窗口。AI 端加 `--no-window` 后在后台运行，例如：

```powershell
.\start.cmd --windowed
.\start.cmd host --control ai --no-window
.\start.cmd join --address 192.168.1.20 --control ai --no-window
.\start.cmd duel --no-window
```

`--no-window` 不用于真人控制。单机人机本来就自动隐藏 AI 端；`duel` 默认也隐藏两个 AI 端。Windows 使用隐藏图形客户端的方式，加载时可能短暂出现窗口，仍有图形开销。

两台电脑联机及双方独立选择真人/AI，见 [联机说明](docs/lan.md)。

详细玩法、兵种属性、测试与 Linux 入口见 [玩法与记录约定](docs/gameplay.md)，地图构建见 [sc2/maps/README.md](sc2/maps/README.md)。

Python 包保留在 `sc2/`，从仓库根目录运行 `python -m sc2.run`。本机依赖、对局记录和历史工作记录分别位于 `.deps/`、`sc2/runs/` 和 `.retro/`，均不纳入 Git。历史证据中的原始绝对路径保留，以免改变已记录的哈希。

## 目录与开发

```text
sc2/          运行代码、当前规则、地图源码和 AI 策略
  legacy/     旧地图与规则，仅供历史复现和回放
tests/        离线回归测试
scripts/      Windows / WSL 启动器与环境安装脚本
docs/         玩法、联机、安装、开发说明
  history/    历史验收记录
requirements.in / requirements.txt   依赖声明与锁定版本
start.cmd     Windows 双击入口
```

测试与旧路径迁移见 [开发说明](docs/development.md)，环境配置见 [安装说明](docs/setup.md)。
