from __future__ import annotations

import json
import os
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_PATH = PROJECT_ROOT / "resources" / "emotes.json"
DEFAULT_BLACKLIST_PATH = PROJECT_ROOT / "resources" / "emote_blacklist.json"


@dataclass(frozen=True)
class EmoteEntry:
    identifier: str
    kind: str
    name: str
    family: str = ""

    @property
    def type_label(self) -> str:
        return "动态表情" if self.kind == "animated" else "文字信息"

    @property
    def display_label(self) -> str:
        details = f" · {self.family}" if self.family else ""
        return f"{self.name}  [{self.identifier}]{details}"

    @property
    def search_text(self) -> str:
        return " ".join(
            (self.identifier, self.kind, self.name, self.family, self.type_label)
        ).casefold()


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> list[EmoteEntry]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries: list[EmoteEntry] = []
    for item in payload.get("emotes", []):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        name = item.get("human_readable_name") or item.get("name") or item["id"]
        entries.append(
            EmoteEntry(
                identifier=item["id"],
                kind="animated",
                name=str(name),
                family=str(item.get("family") or ""),
            )
        )
    for item in payload.get("taunts", []):
        if not isinstance(item, dict) or not item.get("is_text_emote"):
            continue
        identifier = item.get("name") or item.get("tid")
        if not isinstance(identifier, str):
            continue
        translations = item.get("translations")
        translations = translations if isinstance(translations, dict) else {}
        chinese = translations.get("CN") or translations.get("CNT")
        english = translations.get("EN")
        if chinese and english:
            name = f"{chinese} / {english}"
        else:
            name = chinese or english or item.get("tid") or identifier
        entries.append(EmoteEntry(identifier, "text", str(name), "Text"))
    return sorted(entries, key=lambda entry: (entry.kind, entry.name.casefold(), entry.identifier))


def filter_entries(entries: list[EmoteEntry], query: str) -> list[EmoteEntry]:
    words = query.casefold().split()
    if not words:
        return list(entries)
    return [entry for entry in entries if all(word in entry.search_text for word in words)]


def load_blacklist(path: Path = DEFAULT_BLACKLIST_PATH) -> tuple[set[str], set[str]]:
    if not path.exists():
        return set(), set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    animated = payload.get("animated_emote_ids", [])
    text = payload.get("text_emote_ids", [])
    if not isinstance(animated, list) or not isinstance(text, list):
        raise ValueError("黑名单文件中的 ID 列表格式无效")
    return (
        {value for value in animated if isinstance(value, str)},
        {value for value in text if isinstance(value, str)},
    )


def save_blacklist(
    animated: set[str],
    text: set[str],
    path: Path = DEFAULT_BLACKLIST_PATH,
) -> None:
    payload = {
        "schema_version": 1,
        "mode": "configuration_only",
        "animated_emote_ids": sorted(animated),
        "text_emote_ids": sorted(text),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


class EmoteBlacklistWindow:
    def __init__(
        self,
        root: tk.Tk,
        catalog_path: Path = DEFAULT_CATALOG_PATH,
        blacklist_path: Path = DEFAULT_BLACKLIST_PATH,
    ) -> None:
        self.root = root
        self.blacklist_path = blacklist_path
        self.entries = load_catalog(catalog_path)
        self.by_label = {entry.display_label: entry for entry in self.entries}
        self.by_key = {(entry.kind, entry.identifier): entry for entry in self.entries}
        self.animated, self.text = load_blacklist(blacklist_path)
        self.dirty = False

        root.title("NRPlusPlus · 表情黑名单")
        root.geometry("920x620")
        root.minsize(760, 520)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self._refresh_candidates()
        self._refresh_blacklist()

    def _build(self) -> None:
        container = ttk.Frame(self.root, padding=18)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="表情黑名单", font=("Microsoft YaHei UI", 18, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            container,
            text="选择不喜欢的表情并保存。当前版本只维护配置，不会实际拦截游戏中的表情。",
            foreground="#666666",
        ).pack(anchor="w", pady=(4, 16))

        body = ttk.Panedwindow(container, orient="horizontal")
        body.pack(fill="both", expand=True)
        available = ttk.LabelFrame(body, text="表情目录", padding=12)
        blocked = ttk.LabelFrame(body, text="已加入黑名单", padding=12)
        body.add(available, weight=3)
        body.add(blocked, weight=2)

        ttk.Label(available, text="搜索名称、ID 或角色系列").pack(anchor="w")
        self.query = tk.StringVar()
        search = ttk.Entry(available, textvariable=self.query)
        search.pack(fill="x", pady=(5, 10))
        self.query.trace_add("write", lambda *_: self._refresh_candidates())

        ttk.Label(available, text="从下拉列表选择").pack(anchor="w")
        self.selection = tk.StringVar()
        self.combo = ttk.Combobox(
            available,
            textvariable=self.selection,
            state="readonly",
            height=18,
        )
        self.combo.pack(fill="x", pady=(5, 10))
        self.combo.bind("<<ComboboxSelected>>", lambda _event: self._update_preview())

        self.preview = tk.StringVar(value="请选择一个表情")
        ttk.Label(
            available,
            textvariable=self.preview,
            foreground="#555555",
            wraplength=450,
        ).pack(anchor="w", fill="x", pady=(3, 12))
        ttk.Button(available, text="添加到黑名单  →", command=self.add_selected).pack(
            anchor="e"
        )
        self.result_count = tk.StringVar()
        ttk.Label(available, textvariable=self.result_count, foreground="#777777").pack(
            anchor="w", side="bottom"
        )

        columns = ("type", "id", "name")
        self.tree = ttk.Treeview(blocked, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("type", text="类型")
        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="名称")
        self.tree.column("type", width=72, stretch=False)
        self.tree.column("id", width=76, stretch=False)
        self.tree.column("name", width=220)
        scroll = ttk.Scrollbar(blocked, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        actions = ttk.Frame(container)
        actions.pack(fill="x", pady=(14, 0))
        self.status = tk.StringVar(value="配置尚未修改")
        ttk.Label(actions, textvariable=self.status, foreground="#666666").pack(side="left")
        ttk.Button(actions, text="移除所选", command=self.remove_selected).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(actions, text="保存配置", command=self.save).pack(side="right")

    def _refresh_candidates(self) -> None:
        visible = filter_entries(self.entries, self.query.get())
        labels = [entry.display_label for entry in visible]
        self.combo["values"] = labels
        self.result_count.set(f"找到 {len(labels)} 个结果，共 {len(self.entries)} 个表情")
        if self.selection.get() not in labels:
            self.selection.set(labels[0] if labels else "")
        self._update_preview()

    def _update_preview(self) -> None:
        entry = self.by_label.get(self.selection.get())
        if entry is None:
            self.preview.set("没有符合搜索条件的表情")
            return
        self.preview.set(
            f"类型：{entry.type_label}    ID：{entry.identifier}"
            + (f"    系列：{entry.family}" if entry.family else "")
        )

    def add_selected(self) -> None:
        entry = self.by_label.get(self.selection.get())
        if entry is None:
            return
        target = self.animated if entry.kind == "animated" else self.text
        if entry.identifier in target:
            self.status.set("该表情已在黑名单中")
            return
        target.add(entry.identifier)
        self.dirty = True
        self.status.set(f"已添加：{entry.name}（点击“保存配置”生效）")
        self._refresh_blacklist()

    def remove_selected(self) -> None:
        selected = self.tree.selection()
        if not selected:
            self.status.set("请先在右侧选择要移除的表情")
            return
        for item_id in selected:
            kind, identifier = self.tree.item(item_id, "tags")
            (self.animated if kind == "animated" else self.text).discard(identifier)
        self.dirty = True
        self.status.set(f"已移除 {len(selected)} 项（点击“保存配置”生效）")
        self._refresh_blacklist()

    def _refresh_blacklist(self) -> None:
        self.tree.delete(*self.tree.get_children())
        keys = [
            *(("animated", identifier) for identifier in self.animated),
            *(("text", identifier) for identifier in self.text),
        ]
        for kind, identifier in sorted(keys):
            entry = self.by_key.get((kind, identifier))
            type_label = entry.type_label if entry else ("动态表情" if kind == "animated" else "文字信息")
            name = entry.name if entry else "目录中未找到"
            self.tree.insert("", "end", values=(type_label, identifier, name), tags=(kind, identifier))

    def save(self) -> None:
        try:
            save_blacklist(self.animated, self.text, self.blacklist_path)
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            return
        self.dirty = False
        self.status.set(
            f"已保存：{len(self.animated)} 个动态表情，{len(self.text)} 条文字信息"
        )

    def close(self) -> None:
        if self.dirty and not messagebox.askyesno(
            "尚未保存",
            "有未保存的修改，确定关闭吗？",
            parent=self.root,
        ):
            return
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    EmoteBlacklistWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
