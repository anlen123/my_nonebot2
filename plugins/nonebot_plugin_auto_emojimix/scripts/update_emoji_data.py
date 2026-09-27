"""
本地运行：检查上游 emoji 数据是否有更新，有则自动更新。

用法:
    python scripts/update_emoji_data.py
"""

import hashlib
import json
import os
import re
import sqlite3
import sys

import httpx

# ── 路径配置 ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
HASH_FILE = os.path.join(SCRIPT_DIR, "metadata_hash.txt")
OUTPUT_DB = os.path.join(
    PROJECT_ROOT,
    "src",
    "nonebot_plugin_auto_emojimix",
    "emojimix.db",
)

METADATA_URL = "https://raw.githubusercontent.com/xsalazar/emoji-kitchen-backend/main/app/metadata.json"
BASE_URL = "https://www.gstatic.com/android/keyboard/emojikitchen/"


def download_metadata() -> str:
    print("⏳ 正在下载上游 metadata.json ...")
    resp = httpx.get(METADATA_URL, timeout=30)
    resp.raise_for_status()
    print(f"✅ 下载完成 ({len(resp.text):,} 字节)")
    return resp.text


def check_hash(content: str) -> bool:
    """返回 True 表示有更新，False 表示无变化。"""
    new_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    if os.path.exists(HASH_FILE):
        old_hash = open(HASH_FILE).read().strip()
        if new_hash == old_hash:
            return False

    os.makedirs(os.path.dirname(HASH_FILE), exist_ok=True)
    with open(HASH_FILE, "w") as f:
        f.write(new_hash)
    return True


def extract_urls(content: str) -> list[str]:
    data = json.loads(content)
    urls = []

    def find_urls(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == "gStaticUrl" and isinstance(value, str):
                    urls.append(value)
                elif isinstance(value, (dict, list)):
                    find_urls(value)
        elif isinstance(obj, list):
            for item in obj:
                find_urls(item)

    find_urls(data)
    return urls


def process_urls_to_combos(urls: list[str]) -> list[tuple[str, str, str]]:
    """从 URL 列表提取去重后的 (code1, code2, date) 元组列表。"""
    # 先收集所有组合，按 emoji_name 去重保留最新日期
    emoji_dict: dict[str, dict[str, str]] = {}
    for url in urls:
        emoji_path = url.replace(BASE_URL, "")
        match = re.match(r"^(\d{8})/(.+)$", emoji_path)
        if match:
            date, rest = match.groups()
            # rest 例如: "u1f602/u1f602_u1f97a.png"
            emoji_name = rest.split("/")[-1]
            if emoji_name.endswith(".png"):
                emoji_name = emoji_name[:-4]
            if emoji_name not in emoji_dict:
                emoji_dict[emoji_name] = {"date": date}
            elif date > emoji_dict[emoji_name]["date"]:
                emoji_dict[emoji_name] = {"date": date}

    # 转换为 (code1, code2, date) 元组
    combos = []
    for emoji_name, info in emoji_dict.items():
        parts = emoji_name.split("_", 1)
        if len(parts) == 2:
            combos.append((parts[0], parts[1], info["date"]))

    return combos


def build_db(combos: list[tuple[str, str, str]]) -> None:
    """从组合数据构建 SQLite 数据库文件。"""
    # 删除旧文件（SQLite 不支持原子替换）
    if os.path.exists(OUTPUT_DB):
        os.remove(OUTPUT_DB)

    os.makedirs(os.path.dirname(OUTPUT_DB), exist_ok=True)
    db = sqlite3.connect(OUTPUT_DB)
    try:
        db.execute(
            "CREATE TABLE combos ("
            "  code1 TEXT NOT NULL,"
            "  code2 TEXT NOT NULL,"
            "  date  TEXT NOT NULL,"
            "  PRIMARY KEY (code1, code2)"
            ")"
        )
        db.executemany(
            "INSERT INTO combos (code1, code2, date) VALUES (?, ?, ?)", combos
        )
        db.execute("ANALYZE")  # 更新查询优化器的统计信息
        db.commit()
        db.execute("VACUUM")  # 压缩数据库文件
    finally:
        db.close()


def main():
    content = download_metadata()

    if not check_hash(content):
        print("ℹ️  无更新 — 上游数据与本地一致。")
        sys.exit(0)

    print("🔄 检测到更新，正在处理 ...")
    urls = extract_urls(content)
    print(f"   提取到 {len(urls):,} 个 URL")

    combos = process_urls_to_combos(urls)
    print(f"   {len(combos):,} 个组合 (去重后)")

    build_db(combos)

    size = os.path.getsize(OUTPUT_DB)
    print(f"✅ 已更新: {OUTPUT_DB}")
    print(f"   文件大小: {size:,} 字节 ({size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()
