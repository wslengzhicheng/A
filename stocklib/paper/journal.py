"""模拟盘流水持久化：cache/paper/<run_id>/ 下 JSON / JSONL。"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from .account import Account


def _project_root() -> str:
    # stocklib/paper/journal.py → 上三级为项目根
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def paper_root(base: Optional[str] = None) -> str:
    root = base or _project_root()
    return os.path.join(root, "cache", "paper")


def new_run_id() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{uuid.uuid4().hex[:6]}"


def _clean_for_json(obj):
    """轻量 JSON 清理（避免 inf/nan）。"""
    import math
    if isinstance(obj, dict):
        return {k: _clean_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_for_json(x) for x in obj]
    if isinstance(obj, float):
        if math.isinf(obj) or math.isnan(obj):
            return None
        return obj
    return obj


class PaperJournal:
    """一个 run 的读写门面。"""

    def __init__(self, run_id: str, base_dir: Optional[str] = None):
        self.run_id = str(run_id)
        self.root = os.path.join(paper_root(base_dir), self.run_id)

    @property
    def meta_path(self) -> str:
        return os.path.join(self.root, "meta.json")

    def ensure_dirs(self) -> None:
        os.makedirs(os.path.join(self.root, "accounts"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "equity"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "daily"), exist_ok=True)

    def create_run(self, meta: dict) -> dict:
        self.ensure_dirs()
        m = dict(meta or {})
        m.setdefault("run_id", self.run_id)
        m.setdefault("created_at", datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
        m.setdefault("status", "created")
        self.write_meta(m)
        for name in ("orders.jsonl", "fills.jsonl", "signals.jsonl"):
            path = os.path.join(self.root, name)
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    pass
        return m

    def write_meta(self, meta: dict) -> None:
        self.ensure_dirs()
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(_clean_for_json(meta), f, ensure_ascii=False, indent=2)

    def read_meta(self) -> dict:
        if not os.path.exists(self.meta_path):
            raise FileNotFoundError(f"run 不存在: {self.run_id}")
        with open(self.meta_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_account(self, account: Account) -> None:
        self.ensure_dirs()
        path = os.path.join(self.root, "accounts", f"{account.strategy_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_clean_for_json(account.to_dict()), f, ensure_ascii=False, indent=2)

    def load_account(self, strategy_id: str) -> Account:
        path = os.path.join(self.root, "accounts", f"{strategy_id}.json")
        with open(path, "r", encoding="utf-8") as f:
            return Account.from_dict(json.load(f))

    def list_accounts(self) -> List[str]:
        d = os.path.join(self.root, "accounts")
        if not os.path.isdir(d):
            return []
        out = []
        for name in sorted(os.listdir(d)):
            if name.endswith(".json"):
                out.append(name[:-5])
        return out

    def _append_jsonl(self, filename: str, row: dict) -> None:
        self.ensure_dirs()
        path = os.path.join(self.root, filename)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(_clean_for_json(row), ensure_ascii=False) + "\n")

    def append_order(self, order: dict) -> None:
        self._append_jsonl("orders.jsonl", order)

    def append_fill(self, fill: dict) -> None:
        self._append_jsonl("fills.jsonl", fill)

    def append_signal(self, signal: dict) -> None:
        self._append_jsonl("signals.jsonl", signal)

    def append_equity(self, strategy_id: str, row: dict) -> None:
        self.ensure_dirs()
        path = os.path.join(self.root, "equity", f"{strategy_id}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(_clean_for_json(row), ensure_ascii=False) + "\n")

    def read_jsonl(self, filename: str) -> List[dict]:
        path = os.path.join(self.root, filename)
        if not os.path.exists(path):
            return []
        rows = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
        return rows

    def read_fills(self, strategy_id: Optional[str] = None) -> List[dict]:
        rows = self.read_jsonl("fills.jsonl")
        if strategy_id:
            rows = [r for r in rows if r.get("strategy_id") == strategy_id]
        return rows

    def read_orders(self, strategy_id: Optional[str] = None) -> List[dict]:
        rows = self.read_jsonl("orders.jsonl")
        if strategy_id:
            rows = [r for r in rows if r.get("strategy_id") == strategy_id]
        return rows

    def read_equity(self, strategy_id: str) -> List[dict]:
        path = os.path.join(self.root, "equity", f"{strategy_id}.jsonl")
        if not os.path.exists(path):
            return []
        rows = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    @staticmethod
    def list_runs(base_dir: Optional[str] = None) -> List[str]:
        root = paper_root(base_dir)
        if not os.path.isdir(root):
            return []
        runs = []
        for name in sorted(os.listdir(root)):
            if os.path.isfile(os.path.join(root, name, "meta.json")):
                runs.append(name)
        return runs

