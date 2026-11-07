# 三兵缩圈 / SC2

双方各一名轻骑、长枪、弓手，使用相同的公开自定义属性，在小地图上限时缩圈对战。

在 PowerShell 启动 Windows 原生客户端：

```powershell
& 'F:\project\sc2\sc2\start-windows.ps1'
```

首次配置环境：`./sc2/setup/windows.ps1`。游戏本体使用本机安装的 StarCraft II；不包含在本仓库中。

详细玩法、兵种属性、测试与 Linux 入口见 [sc2/README.md](sc2/README.md)，地图构建见 [sc2/maps/README.md](sc2/maps/README.md)。

Python 包保留在 `sc2/`，从仓库根目录运行 `python -m sc2.run`。本机依赖、对局记录和历史工作记录分别位于 `.deps/`、`sc2/runs/` 和 `.retro/`，均不纳入 Git。历史证据中的原始绝对路径保留，以免改变已记录的哈希。
