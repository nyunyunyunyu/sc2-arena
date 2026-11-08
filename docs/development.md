# 开发与目录迁移

所有命令从仓库根目录运行。Python 模块仍为 `sc2.run`、`sc2.lan`、`sc2.eval`；
当前规则、地图源码、内置策略和默认 `sc2/runs/` 位置保持不变。

## 离线检查

在已安装依赖的 Python 3.10 环境中运行：

```powershell
& .deps/sc2-win/Scripts/python.exe -B -m unittest discover -s tests -t . -v
& .deps/sc2-win/Scripts/python.exe -B -m sc2.run --help
& .deps/sc2-win/Scripts/python.exe -B -m sc2.lan --help
& .deps/sc2-win/Scripts/python.exe -B -m sc2.eval --help
```

这些检查不会启动 SC2。Linux 使用相同的 unittest 命令，Windows PowerShell 专用测试会跳过。
地图打包需要 `smpq`，用 `python sc2/maps/build.py` 构建；真实引擎诊断另见
[地图说明](../sc2/maps/README.md)。

## 路径迁移

| 原路径 | 新路径 |
|---|---|
| `sc2/test_*.py`、`sc2/maps/test_build.py` | `tests/` |
| `sc2/start-windows.ps1`、`sc2/start.ps1` | `scripts/` |
| `sc2/setup/*.ps1`、`sc2/setup/bootstrap.py` | `scripts/setup/` |
| `sc2/requirements.in`、`sc2/requirements.txt` | 仓库根目录 |
| `sc2/LAN.md`、`sc2/setup/README.md` | `docs/lan.md`、`docs/setup.md` |
| 原 `sc2/README.md` 中的历史验收 | `docs/history/acceptance.md` |
| 旧 `.SC2Map` 与 `sc2/rules-*.json` | `sc2/legacy/` |

Windows 继续使用根目录 `start.cmd`。直接引用旧脚本或旧测试模块的外部命令需要按上表更新。
历史文档保留当时的命令、版本与哈希，不作为当前操作指南。

`.deps/`、`.cache/`、`.retro/`、`.env`、`sc2/runs/` 都是本地状态，保持 Git 忽略。
本次 worktree 复制当前源文件，不搬迁或删除原 checkout 的环境、录像、证据和配置。
在新 worktree 首次启动会配置自己的环境；也可用已有 Python 的绝对路径运行离线测试。

## 兼容资产

旧地图与规则保留原始字节，回放仍验证 manifest 中的哈希。默认回放会在当前目录缺图时
查找 `sc2/legacy/`；显式 `--map` 仍严格使用指定文件。旧规则的使用方式见
[legacy 说明](../sc2/legacy/README.md)。
A/B 冻结策略及后续 A2、A3、A4 均保留；启动器可选择全部已注册策略。Linux/PyGame 和引擎诊断仍有用途，继续保留。
