"""基于 JSON 的缓存管理器

负责：
- 缩略图缓存清单 (.videoview/cache_manifest.json)
- 收藏夹列表 (.videoview/favorites.json)
- 标记的收藏夹 (.videoview/marked_collections.json)
- 视频旋转角度 (.videoview/rotations.json)
（音量持久化见 app_config.json）
"""

import os
import json
import hashlib

from core.config import makedirs_hidden, ensure_hidden, open_for_write


def path_hash(path):
    """计算路径的确定性哈希值（使用 MD5）"""
    return hashlib.md5(path.encode('utf-8')).hexdigest()[:16]


class CacheManager:
    def __init__(self, cache_dir, root_folder, base_path=None):
        """创建缓存管理器。

        Args:
            cache_dir: 缓存目录（如 "D:/Videos/.videoview/cache"）
            root_folder: 项目根目录（始终为最顶层根目录）
            base_path: 实际目录（用于 favorites/rotations 路径解析），默认为 root_folder

        路径系统说明：
          - 调用端传入的都是「根目录相对路径」：如 "A/B/video1.mp4"
          - 子目录 CacheManager 需要把它转成「子目录内相对路径」：如 "video1.mp4"
          - 子目录相对前缀由 rel_prefix = "A/B"
        """
        self.cache_dir = cache_dir
        self.root_folder = root_folder.replace('\\', '/')
        self.base_path = (base_path or root_folder).replace('\\', '/')

        # 计算子目录 CacheManager 的相对路径前缀（root_folder 级 CacheManager rel_prefix = None）
        normalized_root = os.path.normpath(self.root_folder)
        normalized_base = os.path.normpath(self.base_path)
        if os.path.normpath(normalized_base) == os.path.normpath(normalized_root):
            self._rel_prefix = None
        else:
            rel = os.path.relpath(normalized_base, normalized_root).replace('\\', '/')
            if rel == '.' or rel == '' or rel.rstrip('/.'):
                self._rel_prefix = None
            else:
                self._rel_prefix = rel

        self.manifest_path = os.path.join(cache_dir, 'cache_manifest.json')
        # favorites.json 放在 .videoview 根目录
        self.favorites_path = os.path.join(os.path.dirname(cache_dir), 'favorites.json')
        self.manifest = self._load_manifest()
        self.favorites = self._load_favorites()
        self.marked_collections = self._load_marked_collections()
        self.rotations = self._load_rotations()

    # ------------------------------------------------------------
    # 路径归一化（核心逻辑）
    # ------------------------------------------------------------

    def _strip_prefix(self, rel_path):
        """将「根目录相对路径」转换为「子目录内相对路径」（只对子目录 CacheManager 生效）。

        例如：rel_path = "A/B/video1.mp4", _rel_prefix = "A/B" → 返回 "video1.mp4"
              rel_path = "video1.mp4", _rel_prefix = None → 返回 "video1.mp4"
        """
        normalized = rel_path.replace('\\', '/')
        if self._rel_prefix is None:
            return normalized
        prefix = self._rel_prefix.replace('\\', '/') + '/'
        if normalized.startswith(prefix):
            return normalized[len(prefix):]
        return normalized

    def _load_manifest(self):
        if os.path.exists(self.manifest_path):
            try:
                with open(self.manifest_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"[CacheManager] 加载缓存清单失败: {e}")
        return {}

    def _save_manifest(self):
        try:
            makedirs_hidden(self.cache_dir)
            with open_for_write(self.manifest_path, 'w', encoding='utf-8') as f:
                json.dump(self.manifest, f, ensure_ascii=False, indent=2)
            # 不再 ensure_hidden(manifest_path)：只隐藏目录，不隐藏文件，
            # 避免某些 Windows 环境下写入隐藏文件报 Permission denied。
        except Exception as e:
            print(f"[CacheManager] 保存缓存清单失败: {e}")

    def _load_favorites(self):
        if os.path.exists(self.favorites_path):
            try:
                with open(self.favorites_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    collections = data.get('collections', {})
                    if '默认收藏夹' not in collections:
                        collections['默认收藏夹'] = []
                    data['collections'] = collections
                    return data
            except Exception as e:
                print(f"[CacheManager] 加载收藏列表失败: {e}")
        return {'collections': {'默认收藏夹': []}}

    def save_favorites(self):
        try:
            makedirs_hidden(self.cache_dir)
            with open_for_write(self.favorites_path, 'w', encoding='utf-8') as f:
                json.dump(self.favorites, f, ensure_ascii=False, indent=2)
            # 不再 ensure_hidden(favorites_path)：同上，仅隐藏目录
        except Exception as e:
            print(f"[CacheManager] 保存收藏列表失败: {e}")

    def get_cache_path(self, video_relative_path, rotation_deg=None):
        """返回视频的缓存文件路径 — 始终使用 hash.jpg，不区分旋转角度。

        路径先通过 _strip_prefix() 归一化，保证子目录和根目录 CacheManager
        用同一语义计算缓存 key。
        """
        normalized = self._strip_prefix(video_relative_path)
        cache_key = path_hash(normalized)
        return os.path.join(self.cache_dir, f"{cache_key}.jpg")

    def cache_exists(self, video_relative_path, rotation_deg=None):
        """检查视频是否有缓存（兼容新旧格式）。

        1. 检查新的 hash.jpg 是否存在且可读
        2. 若不存在但有旧的 hash_rotXXX.jpg，迁移到新的 hash.jpg
        3. 验证文件可读
        """
        normalized = self._strip_prefix(video_relative_path)
        cache_key = path_hash(normalized)
        new_path = os.path.join(self.cache_dir, f"{cache_key}.jpg")

        # 优先检查新格式
        if os.path.exists(new_path):
            try:
                with open(new_path, 'rb') as f:
                    f.read(1)
                return True, new_path
            except Exception:
                try:
                    os.remove(new_path)
                except Exception:
                    pass
        else:
            # 新格式不存在，尝试旧格式 hash_rotXXX.jpg
            import glob as glob_module
            old_pattern = os.path.join(self.cache_dir, f"{cache_key}_rot*.jpg")
            old_files = glob_module.glob(old_pattern)
            if old_files:
                old_path = old_files[0]
                try:
                    with open(old_path, 'rb') as f:
                        f.read(1)
                    os.replace(old_path, new_path)
                    return True, new_path
                except Exception:
                    try:
                        os.remove(old_path)
                    except Exception:
                        pass

        return False, new_path

    def add_cache(self, video_relative_path, cache_path, rotation_deg=None):
        """将视频记录到缓存清单 — 始终覆盖"""
        normalized = self._strip_prefix(video_relative_path)
        cache_key = path_hash(normalized)
        self.manifest[normalized] = cache_key
        self._save_manifest()

    def clear_cache_for(self, video_relative_path):
        """删除指定视频的缓存文件（用于旋转后重新生成），同时清理新旧格式"""
        normalized = self._strip_prefix(video_relative_path)
        cache_key = path_hash(normalized)

        # 删除新格式
        new_path = os.path.join(self.cache_dir, f"{cache_key}.jpg")
        try:
            if os.path.exists(new_path):
                os.remove(new_path)
        except Exception:
            pass

        # 删除可能残留的旧格式文件
        import glob as glob_module
        old_pattern = os.path.join(self.cache_dir, f"{cache_key}_rot*.jpg")
        for old_path in glob_module.glob(old_pattern):
            try:
                os.remove(old_path)
            except Exception:
                pass

    def is_favorite(self, video_relative_path):
        normalized = self._strip_prefix(video_relative_path)
        fav_list = self.favorites.get('collections', {}).get('默认收藏夹', [])
        return normalized in fav_list

    def toggle_favorite(self, video_relative_path):
        normalized = self._strip_prefix(video_relative_path)
        fav_list = self.favorites.get('collections', {}).setdefault('默认收藏夹', [])
        if normalized in fav_list:
            fav_list.remove(normalized)
            self.save_favorites()
            return False
        else:
            fav_list.append(normalized)
            self.save_favorites()
            return True

    def get_favorite_videos(self):
        fav_list = self.favorites.get('collections', {}).get('默认收藏夹', [])
        result = []
        for rel_path in fav_list:
            abs_path = os.path.join(self.base_path, rel_path)
            if os.path.exists(abs_path):
                result.append(abs_path)
        return result

    def get_favorite_count(self):
        fav_list = self.favorites.get('collections', {}).get('默认收藏夹', [])
        return len([f for f in fav_list if os.path.exists(os.path.join(self.base_path, f))])

    # ------------------------------------------------------------
    # 命名收藏夹（.videoview/favorites.json → collections 字典支持多个命名收藏夹）
    # ------------------------------------------------------------

    def get_collection_names(self):
        """返回所有非默认收藏夹的名称（除"默认收藏夹"外的其他命名收藏夹）"""
        all_names = list(self.favorites.get('collections', {}).keys())
        # 默认收藏夹永远在首位
        result = []
        if '默认收藏夹' in all_names:
            result.append('默认收藏夹')
            all_names.remove('默认收藏夹')
        result.extend(sorted(all_names))
        return result

    def create_named_collection(self, name, video_relative_paths):
        """创建一个命名收藏夹（若已存在则覆盖）"""
        collections = self.favorites.setdefault('collections', {})
        normalized = []
        for v in video_relative_paths:
            nv = self._strip_prefix(v)
            if nv and nv not in normalized:
                normalized.append(nv)
        collections[name] = normalized
        self.save_favorites()

    def get_named_collection_videos(self, name):
        """获取命名收藏夹中的所有视频（返回绝对路径）"""
        collections = self.favorites.get('collections', {})
        if name not in collections:
            return []
        result = []
        for rel_path in collections[name]:
            abs_path = os.path.join(self.base_path, rel_path)
            if os.path.exists(abs_path):
                result.append(abs_path)
        return result

    def get_named_collection_count(self, name):
        """获取命名收藏夹中的视频数量"""
        return len(self.get_named_collection_videos(name))

    def remove_named_collection(self, name):
        """删除一个命名收藏夹（不能删除默认收藏夹）"""
        if name == '默认收藏夹':
            return
        collections = self.favorites.get('collections', {})
        if name in collections:
            del collections[name]
            self.save_favorites()

    # ===== 收藏夹标记功能 =====
    def _load_marked_collections(self):
        marked_path = os.path.join(os.path.dirname(self.cache_dir), 'marked_collections.json')
        self.marked_path = marked_path
        if os.path.exists(marked_path):
            try:
                with open(marked_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return set(data.get('marked', []))
            except Exception as e:
                print(f"[CacheManager] 加载标记列表失败: {e}")
        return set()

    def save_marked_collections(self):
        try:
            makedirs_hidden(os.path.dirname(self.marked_path))
            with open_for_write(self.marked_path, 'w', encoding='utf-8') as f:
                json.dump({'marked': sorted(list(self.marked_collections))}, f, ensure_ascii=False, indent=2)
            # 不再 ensure_hidden(self.marked_path)：同上，仅隐藏目录
        except Exception as e:
            print(f"[CacheManager] 保存标记列表失败: {e}")

    def is_marked(self, collection_name):
        return collection_name in self.marked_collections

    def toggle_mark(self, collection_name):
        if collection_name in self.marked_collections:
            self.marked_collections.discard(collection_name)
            self.save_marked_collections()
            return False
        else:
            self.marked_collections.add(collection_name)
            self.save_marked_collections()
            return True

    def set_mark(self, collection_name, is_marked):
        if is_marked:
            self.marked_collections.add(collection_name)
        else:
            self.marked_collections.discard(collection_name)
        self.save_marked_collections()

    # ===== 视频旋转功能 =====
    def _load_rotations(self):
        rotation_path = os.path.join(os.path.dirname(self.cache_dir), 'rotations.json')
        self.rotation_path = rotation_path
        if os.path.exists(rotation_path):
            try:
                with open(rotation_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    rotations = data.get('rotations', {})
                    if isinstance(rotations, dict):
                        return {str(k).replace('\\', '/'): int(v) % 360
                                for k, v in rotations.items() if int(v) % 360 in (0, 90, 180, 270)}
                    return {}
            except Exception as e:
                print(f"[CacheManager] 加载旋转列表失败: {e}")
        return {}

    def save_rotations(self):
        try:
            makedirs_hidden(os.path.dirname(self.rotation_path))
            with open_for_write(self.rotation_path, 'w', encoding='utf-8') as f:
                json.dump({'rotations': self.rotations}, f, ensure_ascii=False, indent=2)
            # 不再 ensure_hidden(self.rotation_path)：同上，仅隐藏目录
        except Exception as e:
            print(f"[CacheManager] 保存旋转列表失败: {e}")

    def get_rotation(self, video_relative_path):
        normalized = video_relative_path.replace('\\', '/')
        return int(self.rotations.get(normalized, 0))

    def set_rotation(self, video_relative_path, rotation_deg):
        normalized = video_relative_path.replace('\\', '/')
        deg = int(rotation_deg) % 360
        if deg not in (0, 90, 180, 270):
            deg = (deg // 90) * 90 % 360
        if deg == 0:
            if normalized in self.rotations:
                del self.rotations[normalized]
                self.save_rotations()
        else:
            self.rotations[normalized] = deg
            self.save_rotations()

    def rotate_left(self, video_relative_path):
        current = self.get_rotation(video_relative_path)
        new_deg = (current - 90) % 360
        self.set_rotation(video_relative_path, new_deg)
        return new_deg

    def rotate_right(self, video_relative_path):
        current = self.get_rotation(video_relative_path)
        new_deg = (current + 90) % 360
        self.set_rotation(video_relative_path, new_deg)
        return new_deg
