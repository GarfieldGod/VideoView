"""视频收藏集管理器 - 入口文件

程序功能：
- 打开一个文件夹作为根目录，自动递归扫描子文件夹作为"收藏夹"
- 每个视频自动生成缩略图（缓存到 .videoview/）
- 双击视频 → 应用内播放（上一个/下一个/收藏/旋转）
- 右键视频 → "以本地播放器打开"（流畅 + 有声音）

模块结构：
  main.py              - 程序入口
  core/
    cache_manager.py   - 管理缓存/收藏/标记/旋转（JSON 持久化）
    thumbnail.py       - 视频缩略图生成（cv2）
    utils.py           - ThumbnailManager 线程 + 视频扫描/图片工具
    config.py          - 应用配置文件（最近打开的根目录）
  ui/
    widgets.py         - FlowLayout / CollectionItem / VideoItem / 列表 / 网格
    video_player_window.py - 应用内播放器（cv2 逐帧）
    main_window.py     - 主窗口
"""

import sys
import os

from PyQt5.QtWidgets import QApplication

# 把项目根目录加入 sys.path，避免子模块里的 "from core.xxx" 无法解析
_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("视频收藏集管理器")

    from ui.main_window import MainWindow
    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
