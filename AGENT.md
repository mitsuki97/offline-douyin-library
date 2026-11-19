# 项目代理说明

## 开始工作前

先阅读：

1. `开发日记_从0到1.md`
2. `AI交接说明.md`
3. `整理工具/使用说明.md`

然后执行只读检查：

```powershell
git status --short
git log -5 --oneline
Get-NetTCPConnection -LocalPort 8765,8766 -State Listen -ErrorAction SilentlyContinue
```

不要根据旧对话猜测目录状态。以当前文件、SQLite 和最新报告为准。

## 项目边界

- `整理工具/downloader_app.py`：独立下载器，主要维护 JSON、采集进度和下载任务。
- `整理工具/web_app.py`：管理平台、JSON API、媒体服务和 `/player/` 静态入口。
- `整理工具/player-web`：Vue 3 + Vite 播放器前端。
- `本地视频库/关注归档`：主库。
- `本地视频库/索引/library.sqlite3`：管理器和播放器使用的权威索引。

下载器 JSON 不要被管理器直接覆盖。视频、数据库、备份和浏览器 profile 不要加入 Git。

## 数据安全

涉及 SQLite、文件移动或删除时先备份并记录批次。完全重复和相似候选不能混为一谈；相似候选不能自动删除。未知热度数据保持为空，不写成 0。主库没有的补充视频只能标记“疑似绝版，待核对”，不能直接断言绝版。

## 运行

```powershell
& 'E:\抖音企划\整理工具\启动统一工作台.ps1'
```

管理平台：`http://127.0.0.1:8765/`

播放器：`http://127.0.0.1:8765/player/`

下载器：`http://127.0.0.1:8766/`

手机使用电脑局域网 IP替换 `127.0.0.1`。

## 修改完成后

播放器修改后重新构建 Vite `dist`，重启统一工作台并检查 `/player/`。管理器修改后运行 Python 语法检查，再检查 `/api/summary`、`/api/feed`和`/api/lan-info`。不要擅自推送远程仓库。


