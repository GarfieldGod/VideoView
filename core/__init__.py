"""core 包 - 核心业务逻辑"""

from .cache_manager import CacheManager
from .thumbnail import (
    generate_video_thumbnail_file,
    generate_video_thumbnail,
    THUMBNAIL_SIZE_VIDEO,
    THUMBNAIL_SIZE_COLLECTION,
)
from .utils import (
    ThumbnailManager,
    VIDEO_EXTENSIONS,
    is_video_file,
    scan_folder_for_videos,
    path_hash,
    pixmap_to_bytes,
    bytes_to_pixmap,
    has_vlc,
    get_vlc_module,
    get_vlc_instance,
    get_vlc_dir,
)
from .config import (
    APP_CONFIG_PATH,
    get_app_config,
    save_app_config,
    get_last_root_folder,
    set_last_root_folder,
    get_volume,
    save_volume,
    get_window_state,
    save_window_state,
)

__all__ = [
    "CacheManager",
    "ThumbnailManager",
    "generate_video_thumbnail_file",
    "generate_video_thumbnail",
    "THUMBNAIL_SIZE_VIDEO",
    "THUMBNAIL_SIZE_COLLECTION",
    "VIDEO_EXTENSIONS",
    "is_video_file",
    "scan_folder_for_videos",
    "path_hash",
    "pixmap_to_bytes",
    "bytes_to_pixmap",
    "has_vlc",
    "get_vlc_module",
    "get_vlc_instance",
    "get_vlc_dir",
    "APP_CONFIG_PATH",
    "get_app_config",
    "save_app_config",
    "get_last_root_folder",
    "set_last_root_folder",
    "get_volume",
    "save_volume",
    "get_window_state",
    "save_window_state",
]
