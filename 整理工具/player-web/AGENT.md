# 播放器前端代理说明

## 技术栈和入口

这是 Vue 3 + Vite + Pinia 的移动优先播放器。主要文件：

- `src/App.vue`：状态、接口、播放窗口、上下滑、底部页面和设置。
- `src/style.css`：布局、沉浸式样式、平铺卡片和反馈按钮。
- `dist/`：构建产物，由上级 `web_app.py` 的 `/player/`路由提供。

## 播放逻辑

播放器只渲染当前视频、前一个视频和后一个视频。下一个视频通过隐藏 video 节点提前 `preload=auto`。切换索引前必须暂停所有已挂载的非当前 video，否则手机会出现两段声音。修改 `prepare`、`prepareSlots`或 `store.next`时必须保留这一点。

用户操作：单击暂停/继续，双击喜欢，上下滑切换，喜欢/收藏可以取消，点击博主昵称进入博主流，来源和作品信息通过弹层展示。

## 数据接口

不要在前端直接读 SQLite。使用后端接口：

- `/api/feed`
- `/api/collections?type=like|favorite`
- `/api/following?q=...`
- `/api/follow`：保存/取消播放器关注。
- `/api/creator-videos?creator_id=...&page=0&limit=36`：博主分页本地视频，按可用历史点赞量排序。
- `/api/event`
- `/api/history-status`
- `/api/history-import`

喜欢和收藏页面按作品 ID去重，后端优先返回主库文件。历史 Excel 的未匹配记录不应伪造成视频卡片。

## 构建

当前环境没有保证全局 `npm`命令可用，使用工作区 Node：

```powershell
$node='C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
& $node 'node_modules\vite\bin\vite.js' build
```

构建后重启统一工作台，打开：`http://127.0.0.1:8765/player/`。

## 修改注意事项

- 保持手机触摸事件和桌面滚轮都能切换。
- 全屏失败时要保留沉浸式 CSS fallback。
- 不要把管理平台旧版播放器重新接回导航；管理平台的“播放器”必须直接链接 `/player/`。
- 修改后检查构建生成的 `dist/index.html`是否引用最新 hash 资源。


