# Git 自动同步（post-commit hook）

> 安装位置：`.git/hooks/post-commit`（worktree 共享，全局生效）
>
> 安装时间：2026-04-20

## 它做什么

每次 `git commit` 之后自动：
1. `git push origin HEAD:main` → 推到 GitHub
2. `cd /Users/dp/Documents/M/stock && git pull --ff-only` → 同步主目录

不论你在哪个 worktree commit，主目录都会跟着更新。
8000 端口的 start.command 浏览器刷新就能看到新前端文件
（后端 Python 进程仍需要重启或加 `--reload`，参见 README）。

## 失败处理

- push 失败：commit 仍然成功，stderr 提示
- 主目录 pull 失败：push 仍然成功，stderr 提示

不会让 commit 看起来失败。

## 排错

操作日志：`/tmp/cm-git-sync.log`

```bash
tail -30 /tmp/cm-git-sync.log
```

## 临时禁用

```bash
mv .git/hooks/post-commit .git/hooks/post-commit.disabled
```

## 注意

主目录自身的 commit 也会触发，但因为在主目录里就不再 pull 自己（避免自环），只 push。
