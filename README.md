该软件由Trae生成

# VideoView (视频收藏集管理器)

一个基于 PyQt5 的本地视频收藏集浏览与播放工具。将任意文件夹作为"根目录"，自动把其中的子文件夹识别为独立的"收藏集"，并生成缩略图网格，方便管理和观看本地视频。

## ✨ 功能特性

- **文件夹扫描**：选择根目录后，递归扫描所有子文件夹作为独立收藏集，同时根目录内的直接视频会作为"根目录"收藏集出现
- **缩略图自动生成**：每个视频首次展示时生成缩略图缓存到 `.videoview/` 目录，之后秒开
- **双列表模式**：左侧可在「📁 根目录收藏集」和「⭐ 最爱列表」间切换
  - **批量收藏**：可将整个子收藏夹一键加入最爱列表
  - **单个收藏**：对每个视频可单独"收藏"（加入默认收藏夹）
- **应用内视频播放器**：双击视频在应用内播放，支持：
  - 上一个 / 下一个视频（跨收藏集导航：播放完当前收藏集的最后一个，自动进入下一个收藏集）
  - 视频旋转（左旋/右旋 90°，角度持久化记录）
  - 自动记录「上次播放位置」：启动时自动定位到上次播放的收藏集，并高亮最后播放的视频
- **收藏集标记**：可将常用收藏集加标记（出现在根目录列表末尾）
- **缓存与配置**：所有元数据（收藏、旋转角度、标记、上次播放位置）存放在每个根目录下的 `.videoview/` 子目录中，JSON 格式，便于迁移

## 🛠 技术栈

- **Python 3**
- **PyQt5** — GUI 框架
- **python-vlc** — 视频播放主后端（流畅 + 有声音）
- **OpenCV (cv2)** — 视频缩略图生成 + VLC 不可用时的兜底播放后端
- **纯 JSON 持久化** — 无外部数据库依赖

## 📦 安装

```bash
# 1. 克隆或下载本项目
cd MyVideo

# 2. 创建虚拟环境（推荐）
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate # macOS / Linux

# 3. 安装依赖
pip install -r requirements.txt
```

## 🚀 使用

```bash
python main.py
```

1. 点击左上角 **「📂 打开文件夹」**，选择存放视频的根目录
2. 左侧会出现该目录下所有子文件夹作为收藏集
3. 双击右侧的视频缩略图 → 应用内播放
4. 播放视频时，左侧的"来源列表"（根目录/最爱）会被记住，下次启动时自动回到原处
5. 关闭后再次打开会自动加载上次的根目录与播放位置

## 📁 目录结构

```
MyVideo/
├── main.py                  # 程序入口
├── requirements.txt         # 依赖清单
│
├── core/
│   ├── cache_manager.py     # 缓存/收藏/标记/旋转/上次位置（JSON 持久化）
│   ├── thumbnail.py         # 视频缩略图生成
│   ├── utils.py             # ThumbnailManager 线程 + 扫描工具
│   └── config.py            # 应用全局配置（最近打开的根目录等）
│
└── ui/
    ├── main_window.py       # 主窗口（收藏集列表 + 视频网格 + 入口路由）
    ├── video_player_window.py # 应用内视频播放器（VLC 主后端 + OpenCV 兜底）
    └── widgets.py           # 流式布局、收藏集项、视频项等控件
```

## ⚙ 配置与元数据位置

每个被选为根目录的文件夹会自动创建 `.videoview/` 子目录：

```
某根目录/
└── .videoview/
    ├── cache/                # 缩略图缓存文件
    │   ├── thumbs/           # 实际缩略图（带旋转角度信息的文件名）
    │   └── manifest.json     # 缩略图索引（rel_path → 文件路径）
    ├── favorites.json        # 最爱列表（默认收藏夹 + 命名收藏夹）
    ├── marked_collections.json # 被标记的收藏集名
    └── config.json           # 根目录配置（上次播放位置）
```

## 🧩 播放流程与数据流

```
用户双击视频项
      │
      ▼
on_video_double_clicked()    ──► 读取 current_tab（0=根目录 / 1=最爱）
      │                              作为 source_mode
      ▼
VideoPlayerWindow            ◄─── 传入 collection_name + collection_source
      │
      ▼
播放视频时调用 _save_last_played()
      │
      ▼
cache_manager.set_last_played(rel_path, abs_path,
                               collection_name, source_mode)
      │
      ▼
写 .videoview/config.json →
      last_played.rel_path
      last_played.collection_name
      last_played.source_mode (0 或 1)
      │
      ▼
下次启动 main.py 时 _auto_open_last_played_or_first()
读取 source_mode → 精确切换到根目录 / 最爱列表 → 打开对应收藏集 → 高亮视频
```

## 📌 备注

- **VLC 依赖**：建议本机安装 [VLC Media Player](https://www.videolan.org/)，`python-vlc` 需要 `libvlc.dll`；如果未安装，播放会自动回退到 OpenCV（无声音）
- **Windows**：在 Windows 上使用默认的 `\` 路径分隔符和 POSIX `/` 风格的配置值混合使用，代码已做归一化处理，无需额外配置
- **首次扫描**：首次打开包含大量视频的根目录时，缩略图生成会占用一些时间；生成后的缩略图被缓存到 `.videoview/cache/thumbs/`，之后浏览即开即显
