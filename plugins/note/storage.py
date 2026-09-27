"""note 存储层

JSON 单文件存储，结构：

  {
    "next_id": 3,
    "notes": [
      {"id": 1, "text": "买菜", "created_at": "2026-09-14 17:30:00", "user_id": 853307164},
      {"id": 2, "text": "交电费", "created_at": "2026-09-14 18:02:11", "user_id": 853307164}
    ]
  }

序号用「稳定自增 id」而不是列表下标：删除中间的记录后，剩余记录的序号不会
前移。如果按下标算，用户看着 20 分钟前的旧列表发 `note rm 2`，很可能删错人。

写盘用「临时文件 + 原子替换」，避免进程被杀时留下半截 JSON。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import nonebot

# 同一个进程内串行化「读-改-写」，避免两次操作交叉后互相覆盖
_lock = threading.Lock()


def _empty() -> Dict:
    """空库结构：next_id 从 1 开始。"""
    return {"next_id": 1, "notes": []}


def _read(path: Path) -> Dict:
    """读库；文件不存在或内容坏掉都返回空库。

    内容坏掉时先把原文件改名备份，再从空库开始 —— 宁可让用户看到「暂无记录」，
    也不能悄悄清掉用户还能人工捞回来的数据。
    """
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        try:
            broken = path.with_suffix(f".broken-{datetime.now():%Y%m%d-%H%M%S}.json")
            path.replace(broken)
        except OSError as backup_exc:
            # 备份失败也不能把异常抛给调用方：按空库继续，至少增删还能用
            nonebot.logger.warning(
                f"[note] 存储文件损坏（{exc}）且备份失败（{backup_exc}），"
                f"暂按空库继续：{path}"
            )
        return _empty()

    if not isinstance(data, dict) or "notes" not in data:
        return _empty()
    # 老数据可能没有 next_id，用现有记录的最大 id 反推一个，保证序号只增不减
    data.setdefault("next_id", max((n.get("id", 0) for n in data["notes"]), default=0) + 1)
    return data


def _write(path: Path, data: Dict) -> None:
    """先写临时文件再原子替换，避免进程被杀时留下半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)  # 原子替换


def list_notes(path: Path) -> List[Dict]:
    """按写入顺序返回全部记录；只读不落盘。"""
    with _lock:
        return _read(path)["notes"]


def add_note(path: Path, text: str, user_id: Optional[int] = None) -> Dict:
    """追加一条记录，返回新记录（含分配到的 id）"""
    with _lock:
        data = _read(path)
        note = {
            "id": data["next_id"],
            "text": text,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": user_id,
        }
        data["notes"].append(note)
        data["next_id"] += 1
        _write(path, data)
        return note


def remove_note(path: Path, note_id: int) -> Optional[Dict]:
    """按 id 删除，返回被删的记录；不存在返回 None"""
    with _lock:
        data = _read(path)
        for i, n in enumerate(data["notes"]):
            if n.get("id") == note_id:
                removed = data["notes"].pop(i)
                _write(path, data)
                return removed
        return None
