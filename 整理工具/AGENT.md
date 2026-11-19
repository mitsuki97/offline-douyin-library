# 整理工具代理说明

## 文件职责

- `web_app.py`：管理平台和播放器后端。管理器页面使用内嵌 HTML/JavaScript；新版播放器由 `/player/` 提供 `player-web/dist`。
- `downloader_app.py`：8766 下载器，不要把管理器逻辑写进下载器。
- `library.py`：索引、哈希、媒体检查、归档和报告。
- `主库优先整理.py`、`应用主库整理报告.py`：主库优先扫描和执行。
- `扫描主库索引.py`：将 `关注归档` 中的 MP4 及匹配的 `主页作品.json` 元数据登记到管理器 SQLite；会先备份并写入进度文件，支持停止后重扫续接，不移动或删除视频。
- `来源合并工具.py`：补充来源扫描和导入。
- `导入历史反馈.py`：读取历史 Excel，按作品 ID写入喜欢/收藏反馈。
- `启动统一工作台.ps1`、`停止统一工作台.ps1`：统一启动和停止入口。

## 数据库规则

管理器数据库：`本地视频库/索引/library.sqlite3`。常用表：`files`、`works`、`creators`、`media`、`metrics`、`feedback`、`similar`、`review_log`、`moves`、`history_imports`、`history_records`。

播放器反馈写入 `feedback`：

- `like` / `unlike`
- `favorite` / `unfavorite`
- `dislike`、`block_video`、`block_creator`
- 播放和观看事件

反馈状态按最新事件计算，不能只看是否存在一条旧的 `like`。

## 接口约定

- `/api/feed`：推荐流和博主流。
- `/api/collections?type=like|favorite`：喜欢/收藏平铺数据。
- `/api/following?q=...`：搜索本地博主；`following=1` 列出播放器关注。
- `/api/follow`：新增或取消播放器关注关系。
- `/api/creator-videos?creator_id=...&page=0&limit=36`：返回该博主分页本地视频，按可用历史点赞量降序。
- `/api/primary-index-start`、`/api/primary-index-stop`、`/api/primary-index-job`、`/api/primary-index-latest`：启动、停止、查询主库索引扫描；中断后通过 start 重新启动，扫描器按 SQLite 已有路径续接。
- `/api/event`：记录播放器行为。
- `/api/history-status`：历史 Excel 导入统计。
- `/api/history-import`：重新导入历史 Excel。
- `/api/lan-info`：返回本机局域网访问地址。
- `/api/downloader-status`：只读检查本机 8766 下载器端口是否在线；不启动或控制下载任务。
- `/media?path=...`：只允许读取本地视频库范围内的媒体文件，并支持 Range。

新增接口时优先保持 JSON 返回，避免让播放器依赖管理器页面中的 DOM。

## 修改和验证

修改数据库结构前先备份 SQLite。修改文件整理逻辑前先使用扫描/报告模式，不要直接执行移动。修改 `web_app.py`后运行：

```powershell
$py='C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $py -m py_compile 'E:\抖音企划\整理工具\web_app.py'
```

然后重启工作台并检查 `/api/summary`、`/api/feed?limit=1`、`/api/lan-info`。

主库审计页面中的“可用索引 MP4”只统计当前磁盘仍存在的记录；“索引记录总数”还包括路径已不存在的历史记录，两者口径不同。


