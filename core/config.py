"""应用配置：保存 / 读取上次打开的根目录、音量等设置"""

import os
import json


APP_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'app_config.json'
)


def get_app_config():
    if os.path.exists(APP_CONFIG_PATH):
        try:
            with open(APP_CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"读取应用配置失败: {e}")
    return {}


def save_app_config(config):
    try:
        with open(APP_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存应用配置失败: {e}")


def get_last_root_folder():
    return get_app_config().get('last_root_folder', '')


def set_last_root_folder(folder):
    config = get_app_config()
    config['last_root_folder'] = folder
    save_app_config(config)


def get_volume():
    return max(0, min(100, int(get_app_config().get('volume', 80))))


def save_volume(volume):
    config = get_app_config()
    config['volume'] = max(0, min(100, int(volume)))
    save_app_config(config)


def get_window_state(key='main'):
    """读取指定窗口的上次状态：最大化/普通 + 尺寸与位置。

    返回 dict: {
        'maximized': bool,
        'minimized': bool,
        'width': int,
        'height': int,
        'x': int,
        'y': int,
    } 或 None。
    """
    cfg = get_app_config().get('window_state', {})
    state = cfg.get(key)
    if not state:
        return None
    try:
        return {
            'maximized': bool(state.get('maximized', False)),
            'minimized': bool(state.get('minimized', False)),
            'width': int(state.get('width', 1400)),
            'height': int(state.get('height', 900)),
            'x': int(state.get('x', 0)),
            'y': int(state.get('y', 0)),
        }
    except Exception:
        return None


def save_window_state(window, key='main'):
    """把当前窗口的状态写入 app_config.json。

    对 Qt 窗口，最大化/最小化需要单独记录，因为 isMaximized 在
    closeEvent 阶段仍可准确获取。
    """
    try:
        from PyQt5.QtCore import Qt
        is_max = window.isMaximized()
        is_min = window.isMinimized()
        # 如果处于最大化或最小化，记录 normal 的尺寸/位置用于下次恢复
        if is_max or is_min:
            geom = window.normalGeometry()
        else:
            geom = window.geometry()
        state = {
            'maximized': bool(is_max),
            'minimized': bool(is_min) and not is_max,
            'width': int(geom.width()),
            'height': int(geom.height()),
            'x': int(geom.x()),
            'y': int(geom.y()),
        }
        config = get_app_config()
        ws = config.get('window_state', {})
        ws[key] = state
        config['window_state'] = ws
        save_app_config(config)
    except Exception as e:
        print(f"保存窗口状态失败: {e}")


# ------------------------------------------------------------
# .videoview 目录隐藏属性工具（仅在 Windows 上生效）
#
# 设计：只把目录标记为隐藏，不对单独的文件标记隐藏属性。
# 原因：在某些 Windows 配置 / 第三方安全软件下，对已标记 HIDDEN 的文件
# 执行 `open(path, 'w')` 会报 Permission denied (Errno 13)。
# `.videoview` 目录本身隐藏后，其中的文件在资源管理器中已不可见，
# 再对文件单独设置 HIDDEN 并不能获得额外收益。
# ------------------------------------------------------------

_FILE_ATTRIBUTE_HIDDEN = 0x02
_INVALID_FILE_ATTRIBUTES = -1


def _get_windows_attrs(path):
    """返回 Windows 文件/目录的属性值，失败或非 Windows 平台返回 None。"""
    try:
        if os.name != 'nt':
            return None
        import ctypes
        attrs = ctypes.windll.kernel32.GetFileAttributesW(os.path.abspath(path))
        if attrs == _INVALID_FILE_ATTRIBUTES:
            return None
        return attrs
    except Exception:
        return None


def _set_windows_attrs(path, attrs):
    """设置 Windows 文件/目录属性，成功返回 True。"""
    try:
        if os.name != 'nt':
            return False
        import ctypes
        return bool(ctypes.windll.kernel32.SetFileAttributesW(os.path.abspath(path), int(attrs)))
    except Exception:
        return False


def _set_windows_hidden(path):
    """对单个路径设置 Windows 隐藏属性（仅对目录生效，文件不处理）。

    非 Windows 平台 / 路径不存在 / 是文件时不做任何事。
    失败时只打印警告，不抛异常。
    """
    try:
        if os.name != 'nt':
            return
        if not os.path.exists(path):
            return
        # 只隐藏目录，不隐藏文件
        if not os.path.isdir(path):
            return
        current = _get_windows_attrs(path)
        if current is None:
            return
        new_attrs = current | _FILE_ATTRIBUTE_HIDDEN
        if new_attrs != current:
            _set_windows_attrs(path, new_attrs)
    except Exception as e:
        try:
            print(f"[config] 设置隐藏属性失败 ({os.path.basename(path)}): {e}")
        except Exception:
            pass


def _unset_windows_hidden_on_files(path):
    """若 path 是文件且设置了隐藏属性，去除该属性。

    用于修复旧版本遗留的"缓存文件被设为隐藏后无法写入"问题。
    """
    try:
        if os.name != 'nt':
            return
        if not os.path.isfile(path):
            return
        current = _get_windows_attrs(path)
        if current is None:
            return
        if current & _FILE_ATTRIBUTE_HIDDEN:
            _set_windows_attrs(path, current & ~_FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        pass


def _unset_windows_hidden_inside_dir(dirpath):
    """递归去除目录下所有文件的隐藏属性（保留目录隐藏）。

    对 `.videoview` 目录调用一次，以确保旧版本遗留下的隐藏文件都被恢复，
    避免写入时 Permission denied。
    """
    try:
        if os.name != 'nt':
            return
        if not os.path.isdir(dirpath):
            return
        for dirpath_inner, dirnames, filenames in os.walk(dirpath):
            for fname in filenames:
                try:
                    _unset_windows_hidden_on_files(os.path.join(dirpath_inner, fname))
                except Exception:
                    continue
            # 目录本身保持隐藏，不做处理
    except Exception:
        pass


def ensure_hidden(path, recursive=False):
    """把**目录**标记为隐藏（文件参数会被忽略以避免写入权限问题）。

    Args:
        path: 文件或目录路径 — 仅目录会被隐藏。
        recursive: 若是目录且为 True，同时递归隐藏所有子目录。
    """
    try:
        if not os.path.exists(path):
            return
        # 只处理目录
        if not os.path.isdir(path):
            # 若是文件，反而帮它去除可能残留的隐藏属性，避免后续写入失败
            _unset_windows_hidden_on_files(path)
            return
        # 先隐藏自身
        _set_windows_hidden(path)
        if not recursive:
            return
        # 递归隐藏子目录（不隐藏文件）
        for dirpath, dirnames, filenames in os.walk(path):
            for dname in dirnames:
                _set_windows_hidden(os.path.join(dirpath, dname))
            # 顺便去除文件上的隐藏属性（修复旧版本遗留状态）
            for fname in filenames:
                _unset_windows_hidden_on_files(os.path.join(dirpath, fname))
    except Exception as e:
        try:
            print(f"[config] ensure_hidden 异常 ({os.path.basename(path)}): {e}")
        except Exception:
            pass


def open_for_write(path, mode='w', encoding=None):
    """与内置 open() 类似，但写入前会去除目标文件可能存在的隐藏属性。

    用作上下文管理器：
        with open_for_write(path, 'w', encoding='utf-8') as f:
            f.write(...)
    """
    _unset_windows_hidden_on_files(path)
    # 确保父目录存在，同时确保父目录中的 .videoview 被标记为隐藏（仅目录）
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
    except Exception:
        pass
    # 确保 .videoview 祖先目录被隐藏（但不隐藏文件）
    try:
        cur = os.path.abspath(path)
        while cur and os.path.dirname(cur) != cur:
            if os.path.basename(cur) == '.videoview':
                ensure_hidden(cur, recursive=False)
                break
            cur = os.path.dirname(cur)
    except Exception:
        pass
    if encoding is not None:
        return open(path, mode, encoding=encoding)
    return open(path, mode)


def makedirs_hidden(path):
    """等价于 os.makedirs(path, exist_ok=True)，并确保各级 `.videoview`
    祖先目录都具备隐藏属性（Windows 下）。**只隐藏目录，不隐藏文件**。

    同时会清理目录内所有残留的"文件被隐藏"状态，避免写入失败。
    """
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    if not os.path.exists(path):
        return
    # 向上追溯直到遇到 .videoview 或到达文件系统根，隐藏相关目录
    try:
        cur = os.path.abspath(path)
        found_videoview = False
        while cur and os.path.dirname(cur) != cur:
            bname = os.path.basename(cur)
            if bname == '.videoview':
                ensure_hidden(cur, recursive=True)
                found_videoview = True
                break
            cur = os.path.dirname(cur)
        if not found_videoview:
            # 没有找到 .videoview 祖先，仅隐藏当前目录（非文件场景）
            if os.path.isdir(path):
                ensure_hidden(path, recursive=True)
    except Exception:
        pass

