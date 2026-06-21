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


def path_hash(path):
    """计算路径的确定性哈希值（使用 MD5）"""
    return hashlib.md5(path.encode('utf-8')).hexdigest()[:16]


class CacheManager:
    def __init__(self, cache_dir, root_folder):
        self.cache_dir = cache_dir
        self.root_folder = root_folder.replace('\\', '/')
        self.manifest_path = os.path.join(cache_dir, 'cache_manifest.json')
        # favorites.json 放在 .videoview 根目录（与 rotations.json、marked_collections.json 同级）
        self.favorites_path = os.path.join(os.path.dirname(cache_dir), 'favorites.json')
        self.manifest = self._load_manifest()
        self.favorites = self._load_favorites()
        self.marked_collections = self._load_marked_collections()
        self.rotations = self._load_rotations()

    # ------------------------------------------------------------
    # 缓存清单
    # ------------------------------------------------------------

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
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(self.manifest_path, 'w', encoding='utf-8') as f:
                json.dump(self.manifest, f, ensure_ascii=False, indent=2)
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
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(self.favorites_path, 'w', encoding='utf-8') as f:
                json.dump(self.favorites, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[CacheManager] 保存收藏列表失败: {e}")

    def get_cache_path(self, video_relative_path):
        normalized = video_relative_path.replace('\\', '/')
        cache_key = path_hash(normalized)
        return os.path.join(self.cache_dir, f"{cache_key}.jpg")

    def cache_exists(self, video_relative_path):
        cache_path = self.get_cache_path(video_relative_path)
        return os.path.exists(cache_path), cache_path

    def add_cache(self, video_relative_path, cache_path):
        cache_key = path_hash(video_relative_path)
        self.manifest[video_relative_path] = cache_key
        self._save_manifest()

    def is_favorite(self, video_relative_path):
        normalized = video_relative_path.replace('\\', '/')
        fav_list = self.favorites.get('collections', {}).get('默认收藏夹', [])
        return normalized in fav_list

    def toggle_favorite(self, video_relative_path):
        normalized = video_relative_path.replace('\\', '/')
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
            abs_path = os.path.join(self.root_folder, rel_path)
            if os.path.exists(abs_path):
                result.append(abs_path)
        return result

    def get_favorite_count(self):
        fav_list = self.favorites.get('collections', {}).get('默认收藏夹', [])
        return len([f for f in fav_list if os.path.exists(os.path.join(self.root_folder, f))])

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
            os.makedirs(os.path.dirname(self.marked_path), exist_ok=True)
            with open(self.marked_path, 'w', encoding='utf-8') as f:
                json.dump({'marked': sorted(list(self.marked_collections))}, f, ensure_ascii=False, indent=2)
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
            os.makedirs(os.path.dirname(self.rotation_path), exist_ok=True)
            with open(self.rotation_path, 'w', encoding='utf-8') as f:
                json.dump({'rotations': self.rotations}, f, ensure_ascii=False, indent=2)
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
