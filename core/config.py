"""应用配置：保存 / 读取上次打开的根目录"""

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
