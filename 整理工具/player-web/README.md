# 独立播放器前端

`player-web` 是按 zyronon/douyin 的 Vue 3 + Vite 组织方式拆出的本地播放器前端：

- `App.vue`：虚拟窗口式单视频播放，只维护当前项和前后预加载项。
- `Pinia`：保存 feed、索引、模式和事件上报状态。
- `touch`：垂直滑动、单击暂停、双击喜欢、桌面滚轮切换。
- `preload`：下一个视频使用 `preload=auto`，切换时复用已预热的 video 节点。
- `/player/`：由现有 Python 服务提供构建后的静态文件。

构建：

```powershell
cd 整理工具/player-web
pnpm install
pnpm build
```

构建后访问 `http://127.0.0.1:8765/player/`。
