# M8.9 launchd 自动化

每个工作日 17:30 自动跑智能更新 + 双轨 daily topK 推荐.

## 文件清单

| 文件 | 作用 |
|---|---|
| `run_daily.sh` | wrapper: 触发 /api/update/all + 跑 topK primary/shadow |
| `cn.local.chunky-monkey.daily.plist` | launchd 配置 (Mon-Fri 17:30) |
| `install_launchd.sh` | 安装/卸载/状态/手动触发 |

## 前置条件

1. **backend 必须常驻**: `cd backend && python3 -m uvicorn main:app --port 8000` 已运行 (建议另起 launchd 守护; 本目录暂不管)
2. **PYTHON 路径**: 默认用 `python3` (PATH 中). 如需改, 编辑 `run_daily.sh` 顶部 `PYTHON_BIN`
3. **akshare 维护**: 启动脚本只检查本地 akshare 版本，不会自动联网升级。需要维护升级时在项目根目录手动运行 `./scripts/upgrade_akshare.sh`。

## 安装

```bash
cd /Users/dp/Documents/M/stock/ops
./install_launchd.sh install
```

## 验证

```bash
./install_launchd.sh status        # 查看是否已加载
./install_launchd.sh kick          # 立即触发一次, 不等下次 17:30
tail -f ~/Library/Logs/chunky-monkey/daily-$(date +%Y-%m-%d).log  # 看实时日志
```

## 卸载

```bash
./install_launchd.sh uninstall
```

## 日志位置

- `~/Library/Logs/chunky-monkey/daily-YYYY-MM-DD.log` — wrapper 主日志
- `~/Library/Logs/chunky-monkey/launchd-stdout.log` — launchd 标准输出
- `~/Library/Logs/chunky-monkey/launchd-stderr.log` — launchd 标准错误

## 退出码语义

| code | 含义 |
|---|---|
| 0 | 成功 (即使 update 内有 step failed, 主链路完成且双轨 topK 跑了) |
| 1 | backend daemon 未起 (跳过本次, 不算严重错误) |
| 2 | daily topK 失败 |
| 3 | 智能更新触发失败或超时 |

## 调度细节

- **触发时间**: Mon-Fri 17:30 (`StartCalendarInterval` × 5 块)
- **周末/节假日**: wrapper 内 `DOW>=6` 跳过周末; 节假日仍可能跑空但不报错
- **错过触发不补跑**: `StartCalendarIntervalRunAtLoad=false`, 避免开机回灌一周积压
- **超时**: wrapper 60 分钟封顶 + plist `ExitTimeOut=5400` 即 90 分钟硬限
- **优先级**: `Nice=5` 让 launchd 任务低于交互进程

## 与监控页的关系

工作台 `/api/update/status` 已展示 step_status. M8.9 不引入新前端, 仅借现有监控. 后续可加 "最近 N 次自动跑批" 历史视图, 但不是 M8.9 范围.
