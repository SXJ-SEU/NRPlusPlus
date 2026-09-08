from __future__ import annotations

import bisect
import struct
import subprocess
import time
from dataclasses import dataclass

from adb_runtime import AdbError, AdbRuntime
from subprocess_utils import hidden_process_kwargs


@dataclass(frozen=True)
class MemoryRegion:
    start: int
    end: int
    perms: str
    offset: int
    path: str

    @property
    def size(self) -> int:
        return self.end - self.start

    def contains(self, address: int, size: int = 1) -> bool:
        return self.start <= address and address + size <= self.end


@dataclass(frozen=True)
class BattlePointers:
    holder: int
    ui_state: int
    model: int
    hand_array: int
    queue_array: int


@dataclass(frozen=True)
class BattleValues:
    own_elixir: int
    battle_clock: float
    hand_indices: tuple[int, int, int, int]
    next_deck_index: int
    deck_data_ids: tuple[int, int, int, int, int, int, int, int]


@dataclass(frozen=True)
class BattleEntity:
    pointer: int
    kind: int
    side: int
    x: int
    y: int
    x2: int
    y2: int
    card_id: int | None
    level: int
    hp: int
    max_hp: int
    role: str | None


@dataclass(frozen=True)
class RawBattleEntity:
    address: int
    kind: int
    side: int
    x: int
    y: int
    x2: int
    y2: int
    card_id: int | None
    level: int
    hp: int | None = None
    max_hp: int | None = None


def parse_proc_maps(text: str) -> list[MemoryRegion]:
    regions: list[MemoryRegion] = []
    for raw_line in text.splitlines():
        parts = raw_line.strip().split(maxsplit=5)
        if len(parts) < 5 or "-" not in parts[0]:
            continue
        start_text, end_text = parts[0].split("-", 1)
        try:
            start = int(start_text, 16)
            end = int(end_text, 16)
            offset = int(parts[2], 16)
        except ValueError:
            continue
        regions.append(
            MemoryRegion(
                start=start,
                end=end,
                perms=parts[1],
                offset=offset,
                path=parts[5] if len(parts) > 5 else "",
            )
        )
    return regions


class RootProcessMemory:
    """Read-only access to one Android process through root adbd."""

    def __init__(self, adb: AdbRuntime, pid: int) -> None:
        self.adb = adb
        self.pid = pid

    def maps(self) -> list[MemoryRegion]:
        text = self.adb.root_shell(f"cat /proc/{self.pid}/maps", timeout=30)
        return parse_proc_maps(text)

    def _dd_command(self, address: int, size: int) -> str:
        return (
            f"dd if=/proc/{self.pid}/mem iflag=skip_bytes,count_bytes "
            f"skip={address} count={size} status=none"
        )

    def read_many(self, requests: list[tuple[int, int]], *, timeout: float = 120) -> list[bytes]:
        if not requests:
            return []
        command = ";".join(self._dd_command(address, size) for address, size in requests)
        host_command = [
            str(self.adb.config.adb_path),
            "-s",
            self.adb.config.device_serial,
            "exec-out",
        ]
        # Passing the complete command as one argument preserves separators and
        # redirections through adb's remote shell. adbd was verified as root by
        # the launcher, so an additional su layer is neither needed nor portable.
        host_command.append(command)
        try:
            result = subprocess.run(
                host_command,
                capture_output=True,
                timeout=timeout,
                cwd=str(self.adb.config.adb_path.parent),
                **hidden_process_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"读取 /proc/{self.pid}/mem 超时") from exc
        expected = sum(size for _, size in requests)
        if result.returncode != 0 or len(result.stdout) != expected:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise AdbError(
                f"读取 /proc/{self.pid}/mem 失败：期望 {expected} 字节，"
                f"得到 {len(result.stdout)} 字节；{detail or '目标地址已失效'}"
            )
        chunks: list[bytes] = []
        cursor = 0
        for _, size in requests:
            chunks.append(result.stdout[cursor : cursor + size])
            cursor += size
        return chunks

    def read(self, address: int, size: int, *, timeout: float = 120) -> bytes:
        return self.read_many([(address, size)], timeout=timeout)[0]


@dataclass(frozen=True)
class _SnapshotRegion:
    mapping: MemoryRegion
    data: bytes


class _ArenaSnapshot:
    def __init__(self, regions: list[_SnapshotRegion]) -> None:
        self.regions = sorted(regions, key=lambda item: item.mapping.start)
        self.starts = [item.mapping.start for item in self.regions]

    def read(self, address: int, size: int) -> bytes | None:
        index = bisect.bisect_right(self.starts, address) - 1
        if index < 0:
            return None
        region = self.regions[index]
        if not region.mapping.contains(address, size):
            return None
        offset = address - region.mapping.start
        return region.data[offset : offset + size]

    def u64(self, address: int) -> int | None:
        raw = self.read(address, 8)
        return struct.unpack("<Q", raw)[0] if raw is not None else None

    def i32(self, address: int) -> int | None:
        raw = self.read(address, 4)
        return struct.unpack("<i", raw)[0] if raw is not None else None

    def f32(self, address: int) -> float | None:
        raw = self.read(address, 4)
        return struct.unpack("<f", raw)[0] if raw is not None else None


class BattleStateLocator:
    """Find and poll x86_64 battle state without injecting code."""

    SCUDO_STRIDE = 0x1000_0000
    # Relative size classes observed for hand arrays, UI state and the battle
    # holder/model.  Class locations are stable even when ASLR changes.
    REQUIRED_CLASSES = {1, 2, 15, 16}
    ARRAY_CLASSES = {1, 2}
    UI_CLASS = 15
    MODEL_CLASS = 16
    ENTITY_CLASSES = {14, 16}
    UNIT_VTABLE_OFFSET = 0x19692B8
    KING_VTABLE_OFFSET = 0x1969EC0
    ARM_MANAGER_GLOBAL = 0x19BBDD8
    MANAGER_CONTEXT = 0x248
    CONTEXT_BATTLE = 0x90
    BATTLE_HP_STATE = 0xA8
    HP_STATE_REGISTRY = 0x08
    REGISTRY_ENTITY_COLLECTION = 0x40
    ENTITY_CATEGORY_MIN = 5_000_000
    ENTITY_CATEGORY_MAX = 6_000_000

    def __init__(self, memory: RootProcessMemory) -> None:
        self.memory = memory
        self._entity_addresses: tuple[int, ...] = ()
        self._raw_entity_addresses: tuple[int, ...] = ()
        self._libg_base: int | None = None
        self._battle_entity_collection: int | None = None
        self.last_timing: dict[str, float | int] = {}

    @staticmethod
    def _class_key(address: int) -> int:
        return address // BattleStateLocator.SCUDO_STRIDE

    def _load_snapshot(self) -> tuple[_ArenaSnapshot, dict[int, list[_SnapshotRegion]]] | None:
        primary = [
            region
            for region in self.memory.maps()
            if region.perms.startswith("rw") and region.path == "[anon:scudo:primary]"
        ]
        if not primary:
            raise AdbError("游戏进程没有可读的 scudo primary 区域")
        anchor = min(self._class_key(region.start) for region in primary)
        grouped_mappings: dict[int, list[MemoryRegion]] = {}
        for region in primary:
            relative = self._class_key(region.start) - anchor
            if relative in self.REQUIRED_CLASSES:
                grouped_mappings.setdefault(relative, []).append(region)
        missing = self.REQUIRED_CLASSES.difference(grouped_mappings)
        if missing:
            # The larger native size classes do not exist on the home screen;
            # they are allocated when a battle is constructed.
            return None

        requests: list[tuple[int, int]] = []
        order: list[tuple[int, MemoryRegion]] = []
        for relative in sorted(grouped_mappings):
            for mapping in grouped_mappings[relative]:
                requests.append((mapping.start, mapping.size))
                order.append((relative, mapping))
        blobs = self.memory.read_many(requests, timeout=180)
        grouped: dict[int, list[_SnapshotRegion]] = {}
        all_regions: list[_SnapshotRegion] = []
        for (relative, mapping), data in zip(order, blobs, strict=True):
            item = _SnapshotRegion(mapping, data)
            grouped.setdefault(relative, []).append(item)
            all_regions.append(item)
        return _ArenaSnapshot(all_regions), grouped

    @staticmethod
    def _in_regions(address: int, regions: list[_SnapshotRegion], size: int = 1) -> bool:
        return any(item.mapping.contains(address, size) for item in regions)

    def locate(self) -> BattlePointers | None:
        started = time.perf_counter()
        loaded = self._load_snapshot()
        snapshot_done = time.perf_counter()
        if loaded is None:
            self.last_timing = {
                "snapshot_ms": (snapshot_done - started) * 1000,
                "scan_ms": 0.0,
                "total_ms": (snapshot_done - started) * 1000,
                "snapshot_bytes": 0,
            }
            return None
        snapshot, grouped = loaded
        snapshot_bytes = sum(len(item.data) for item in snapshot.regions)
        arrays = grouped[1] + grouped[2]
        model_regions = grouped[self.MODEL_CLASS]
        ui_regions = grouped[self.UI_CLASS]
        candidates: list[tuple[float, BattlePointers]] = []

        for model_region in model_regions:
            data = model_region.data
            usable = len(data) - (len(data) % 8)
            for word_index, hand_array in enumerate(memoryview(data)[:usable].cast("Q")):
                if not self._in_regions(hand_array, arrays, 16):
                    continue
                pointer_address = model_region.mapping.start + word_index * 8
                model = pointer_address - 0x220
                queue_array = snapshot.u64(model + 0x230)
                queue_count = snapshot.i32(model + 0x23C)
                if queue_array is None or queue_count is None:
                    continue
                if not self._in_regions(queue_array, arrays, 4) or not (1 <= queue_count <= 8):
                    continue
                hand_raw = snapshot.read(hand_array, 16)
                next_index = snapshot.i32(queue_array)
                if hand_raw is None or next_index is None:
                    continue
                hand = struct.unpack("<4i", hand_raw)
                if not (all(0 <= value <= 7 for value in hand) and len(set(hand)) == 4):
                    continue
                if not (0 <= next_index <= 7):
                    continue

                needle = struct.pack("<Q", model)
                for holder_region in model_regions:
                    cursor = 0
                    while True:
                        found = holder_region.data.find(needle, cursor)
                        if found < 0:
                            break
                        cursor = found + 1
                        holder = holder_region.mapping.start + found - 0x290
                        ui_state = snapshot.u64(holder + 0xA8)
                        if ui_state is None or not self._in_regions(ui_state, ui_regions, 0x224):
                            continue
                        elixir = snapshot.i32(ui_state + 0x1E0)
                        clock = snapshot.f32(ui_state + 0x220)
                        if elixir is None or clock is None:
                            continue
                        if not (0 <= elixir <= 10 and 0.01 <= clock <= 600.0):
                            continue
                        pointers = BattlePointers(holder, ui_state, model, hand_array, queue_array)
                        candidates.append((clock, pointers))

        if not candidates:
            finished = time.perf_counter()
            self.last_timing = {
                "snapshot_ms": (snapshot_done - started) * 1000,
                "scan_ms": (finished - snapshot_done) * 1000,
                "total_ms": (finished - started) * 1000,
                "snapshot_bytes": snapshot_bytes,
            }
            return None
        finished = time.perf_counter()
        self.last_timing = {
            "snapshot_ms": (snapshot_done - started) * 1000,
            "scan_ms": (finished - snapshot_done) * 1000,
            "total_ms": (finished - started) * 1000,
            "snapshot_bytes": snapshot_bytes,
            "candidate_count": len(candidates),
        }
        return max(candidates, key=lambda item: item[0])[1]

    def poll(self, pointers: BattlePointers) -> BattleValues:
        ui_raw, model_raw, hand_raw, queue_raw = self.memory.read_many(
            [
                (pointers.ui_state + 0x1E0, 0x44),
                (pointers.model + 0x210, 0x98),
                (pointers.hand_array, 16),
                (pointers.queue_array, 4),
            ],
            timeout=20,
        )
        elixir = struct.unpack_from("<i", ui_raw, 0)[0]
        clock = struct.unpack_from("<f", ui_raw, 0x40)[0]
        current_hand_array = struct.unpack_from("<Q", model_raw, 0x10)[0]
        current_queue_array = struct.unpack_from("<Q", model_raw, 0x20)[0]
        queue_count = struct.unpack_from("<i", model_raw, 0x2C)[0]
        deck_data_ids = struct.unpack_from("<8i", model_raw, 0x78)
        hand = struct.unpack("<4i", hand_raw)
        next_index = struct.unpack("<i", queue_raw)[0]
        if current_hand_array != pointers.hand_array or current_queue_array != pointers.queue_array:
            raise AdbError("对战手牌指针已经更换")
        if not (
            0 <= elixir <= 10
            and 0.0 <= clock <= 600.0
            and all(0 <= value <= 7 for value in hand)
            and 1 <= queue_count <= 8
            and 0 <= next_index <= 7
            and sum(23_000_000 <= value < 29_000_000 for value in deck_data_ids) >= 4
            and all(value == -1 or 23_000_000 <= value < 29_000_000 for value in deck_data_ids)
        ):
            raise AdbError("对战状态地址已经失效")
        return BattleValues(elixir, clock, hand, next_index, deck_data_ids)

    def poll_elixir(self, pointers: BattlePointers) -> tuple[int, float]:
        """Read only the UI values required by the initial capture milestone."""
        raw = self.memory.read(pointers.ui_state + 0x1E0, 0x44, timeout=20)
        elixir = struct.unpack_from("<i", raw)[0]
        clock = struct.unpack_from("<f", raw, 0x40)[0]
        if not (0 <= elixir <= 10 and 0.0 <= clock <= 600.0):
            raise AdbError("battle UI pointer is no longer valid")
        return elixir, clock

    def poll_hand_indices(self, pointers: BattlePointers) -> tuple[int, int, int, int]:
        raw = self.memory.read(pointers.hand_array, 16, timeout=20)
        indices = struct.unpack("<4i", raw)
        if not (all(0 <= value <= 7 for value in indices) and len(set(indices)) == 4):
            raise AdbError("battle hand pointer is no longer valid")
        return indices

    def poll_next_deck_index(self, pointers: BattlePointers) -> int:
        raw = self.memory.read(pointers.queue_array, 4, timeout=20)
        index = struct.unpack("<i", raw)[0]
        if not 0 <= index <= 7:
            raise AdbError("battle next-card pointer is no longer valid")
        return index

    def poll_local_player_index(self, pointers: BattlePointers) -> int:
        """Match the local deck model's account ID to the battle player list."""
        player_raw = self.memory.read(pointers.model + 0x10, 0x70, timeout=20)
        context = struct.unpack_from("<Q", player_raw)[0]
        selector = struct.unpack_from("<i", player_raw, 0x68)[0]
        if context == 0:
            raise AdbError("local player context is null")

        provider = struct.unpack(
            "<Q", self.memory.read(context + 0x98, 8, timeout=20)
        )[0]
        if provider == 0:
            raise AdbError("local player provider is null")
        provider_raw = self.memory.read(provider + 0x30, 0x38, timeout=20)
        provider_count = struct.unpack_from("<i", provider_raw, 0x30)[0]
        if selector == 100:
            selected_entry = provider + 0x28
        elif 0 <= selector < provider_count <= 6:
            selected_entry = struct.unpack_from("<Q", provider_raw, selector * 8)[0]
        else:
            raise AdbError("local player selector is invalid")
        if selected_entry == 0:
            raise AdbError("local player entry is null")
        local_account_id = self.memory.read(selected_entry, 8, timeout=20)

        hp_state = self._resolve_battle_hp_state()
        hp_raw = self.memory.read(hp_state + 0x30, 0x38, timeout=20)
        player_count = struct.unpack_from("<i", hp_raw, 0x30)[0]
        if not 1 <= player_count <= 6:
            raise AdbError("battle player count is invalid")
        entries = struct.unpack_from(f"<{player_count}Q", hp_raw)
        nonzero_entries = [(index, entry) for index, entry in enumerate(entries) if entry]
        account_ids = self.memory.read_many(
            [(entry, 8) for _, entry in nonzero_entries], timeout=20
        )
        for (index, _), account_id in zip(nonzero_entries, account_ids, strict=True):
            if account_id == local_account_id:
                return index
        raise AdbError("local account is absent from battle player list")

    @staticmethod
    def _valid_card_data_id(value: int) -> bool:
        return 23_000_000 <= value < 29_000_000

    def poll_card_object_deck(
        self, pointers: BattlePointers
    ) -> tuple[int | None, int | None, int | None, int | None, int | None, int | None, int | None, int | None] | None:
        """Resolve deck-index card IDs through the player's live card wrappers.

        ``model + 0x288`` is intentionally not read here: on Null's Royale it
        records cards progressively revealed to this client, rather than an
        index-aligned deck.  This reproduces the non-mutating part of the
        native ``getCardByHandSlot`` path.
        """
        try:
            player_raw = self.memory.read(pointers.model + 0x10, 0x70, timeout=20)
            context = struct.unpack_from("<Q", player_raw, 0)[0]
            selector = struct.unpack_from("<i", player_raw, 0x68)[0]
            if context == 0:
                return None

            provider = struct.unpack("<Q", self.memory.read(context + 0x98, 8, timeout=20))[0]
            if provider == 0:
                return None
            # Covers entry pointers at +0x30, count at +0x60, and the six
            # corresponding container pointers at +0x88.
            provider_raw = self.memory.read(provider + 0x30, 0x88, timeout=20)
            provider_count = struct.unpack_from("<i", provider_raw, 0x30)[0]
            if not 1 <= provider_count <= 6:
                return None

            if selector == 100:
                selected_entry = provider + 0x28
            elif 0 <= selector < provider_count:
                selected_entry = struct.unpack_from("<Q", provider_raw, selector * 8)[0]
            else:
                return None
            if selected_entry == 0:
                return None
            selected_key = self.memory.read(selected_entry, 8, timeout=20)
            return self._poll_provider_card_deck(
                provider_raw, provider_count, selected_key
            )
        except AdbError:
            return None

    def poll_opponent_card_deck(
        self, pointers: BattlePointers, local_player_index: int
    ) -> tuple[int | None, ...] | None:
        """Read the other player's eight card wrappers from the battle provider."""
        try:
            if local_player_index not in (0, 1):
                return None
            player_context = struct.unpack(
                "<Q", self.memory.read(pointers.model + 0x10, 8, timeout=20)
            )[0]
            if player_context == 0:
                return None
            provider = struct.unpack(
                "<Q", self.memory.read(player_context + 0x98, 8, timeout=20)
            )[0]
            if provider == 0:
                return None
            provider_raw = self.memory.read(provider + 0x30, 0x88, timeout=20)
            provider_count = struct.unpack_from("<i", provider_raw, 0x30)[0]
            if not 1 <= provider_count <= 6:
                return None

            hp_state = self._resolve_battle_hp_state()
            hp_raw = self.memory.read(hp_state + 0x30, 0x38, timeout=20)
            player_count = struct.unpack_from("<i", hp_raw, 0x30)[0]
            opponent_index = 1 - local_player_index
            if not 2 <= player_count <= 6 or opponent_index >= player_count:
                return None
            opponent_entry = struct.unpack_from(
                "<Q", hp_raw, opponent_index * 8
            )[0]
            if opponent_entry == 0:
                return None
            opponent_account_id = self.memory.read(opponent_entry, 8, timeout=20)
            return self._poll_provider_card_deck(
                provider_raw, provider_count, opponent_account_id
            )
        except AdbError:
            return None

    def _poll_provider_card_deck(
        self, provider_raw: bytes, provider_count: int, selected_key: bytes
    ) -> tuple[int | None, ...] | None:
        entries = struct.unpack_from(f"<{provider_count}Q", provider_raw)
        nonzero_entries = [
            (index, entry) for index, entry in enumerate(entries) if entry
        ]
        entry_keys = self.memory.read_many(
            [(entry, 8) for _, entry in nonzero_entries], timeout=20
        )
        provider_slot = next(
            (
                index
                for (index, _), key in zip(nonzero_entries, entry_keys, strict=True)
                if key == selected_key
            ),
            None,
        )
        if provider_slot is None:
            return None

        container = struct.unpack_from("<Q", provider_raw, 0x58 + provider_slot * 8)[0]
        if container == 0:
            return None
        container_raw = self.memory.read(container + 0x20, 0x10, timeout=20)
        wrappers_data = struct.unpack_from("<Q", container_raw)[0]
        wrapper_count, wrapper_capacity = struct.unpack_from("<ii", container_raw, 8)
        if wrappers_data == 0 or wrapper_count != 8 or not 8 <= wrapper_capacity <= 32:
            return None

        wrappers = struct.unpack("<8Q", self.memory.read(wrappers_data, 64, timeout=20))
        nonzero_wrappers = [
            (index, wrapper) for index, wrapper in enumerate(wrappers) if wrapper
        ]
        card_data_raw = self.memory.read_many(
            [(wrapper + 0x10, 8) for _, wrapper in nonzero_wrappers], timeout=20
        )
        data_objects = [0] * 8
        for (index, _), raw in zip(nonzero_wrappers, card_data_raw, strict=True):
            data_objects[index] = struct.unpack("<Q", raw)[0]
        values_raw = self.memory.read_many(
            [(data + 0x40, 4) for data in data_objects if data], timeout=20
        )
        values_by_data = iter(values_raw)
        resolved = tuple(
            struct.unpack("<i", next(values_by_data))[0] if data else None
            for data in data_objects
        )

        if sum(value is not None and self._valid_card_data_id(value) for value in resolved) < 4:
            return None
        return tuple(
            value if value is not None and self._valid_card_data_id(value) else None
            for value in resolved
        )  # type: ignore[return-value]

    def _raw_entity_regions(self) -> list[MemoryRegion]:
        mappings = self.memory.maps()
        primary = [
            mapping
            for mapping in mappings
            if mapping.perms.startswith("rw") and mapping.path == "[anon:scudo:primary]"
        ]
        if not primary:
            return []
        anchor = min(self._class_key(mapping.start) for mapping in primary)
        return [
            mapping
            for mapping in primary
            if self._class_key(mapping.start) - anchor in self.ENTITY_CLASSES
        ]

    @staticmethod
    def _parse_raw_entity(address: int, raw: bytes) -> RawBattleEntity | None:
        state = struct.unpack_from("<i", raw, 0x24)[0]
        kind = struct.unpack_from("<i", raw, 0x30)[0]
        side = struct.unpack_from("<i", raw, 0x78)[0]
        x, y, x2, y2 = struct.unpack_from("<4i", raw, 0x7C)
        card_id = struct.unpack_from("<i", raw, 0xAC)[0]
        level_index = struct.unpack_from("<i", raw, 0x120)[0]
        if not (
            state >= 3
            and 10 <= kind <= 20
            and side in (0, 1)
            and 0 <= x <= 18_000
            and 0 <= y <= 32_000
            and (x != 0 or y != 0)
            and abs(x - x2) <= 200
            and abs(y - y2) <= 200
            and 0 <= level_index <= 16
            and (card_id == -1 or 23_000_000 <= card_id < 29_000_000)
        ):
            return None
        return RawBattleEntity(
            address=address,
            kind=kind,
            side=side,
            x=x,
            y=y,
            x2=x2,
            y2=y2,
            card_id=None if card_id == -1 else card_id,
            level=level_index + 1,
        )

    def _resolve_battle_hp_state(self) -> int:
        if self._libg_base is None:
            mappings = [
                mapping for mapping in self.memory.maps() if mapping.path.endswith("/libg.so")
            ]
            if not mappings:
                raise AdbError("libg.so is not mapped")
            self._libg_base = min(mapping.start - mapping.offset for mapping in mappings)

        def pointer(address: int) -> int:
            return struct.unpack("<Q", self.memory.read(address, 8, timeout=20))[0]

        manager = pointer(self._libg_base + self.ARM_MANAGER_GLOBAL)
        context = pointer(manager + self.MANAGER_CONTEXT)
        battle = pointer(context + self.CONTEXT_BATTLE)
        hp_state = pointer(battle + self.BATTLE_HP_STATE)
        if not hp_state:
            raise AdbError("battle player state is null")
        return hp_state

    def _resolve_battle_entity_collection(self) -> int:
        hp_state = self._resolve_battle_hp_state()

        def pointer(address: int) -> int:
            return struct.unpack("<Q", self.memory.read(address, 8, timeout=20))[0]

        registry = pointer(hp_state + self.HP_STATE_REGISTRY)
        collection = pointer(registry + self.REGISTRY_ENTITY_COLLECTION)
        if not collection:
            raise AdbError("battle entity collection is null")
        self._battle_entity_collection = collection
        return collection

    def _poll_battle_entity_collection(self) -> tuple[RawBattleEntity, ...]:
        collection = self._battle_entity_collection or self._resolve_battle_entity_collection()
        try:
            header = self.memory.read(collection, 0x18, timeout=20)
            data = struct.unpack_from("<Q", header, 0x08)[0]
            count = struct.unpack_from("<i", header, 0x14)[0]
            if not data or not 0 <= count <= 2048:
                raise AdbError("battle entity collection header is invalid")
            pointers_raw = self.memory.read(data, count * 8, timeout=20)
            addresses = [
                address for address in struct.unpack(f"<{count}Q", pointers_raw) if address
            ]
            blobs = self.memory.read_many(
                [(address, 0x124) for address in addresses], timeout=30
            )
        except AdbError:
            self._battle_entity_collection = None
            raise

        entities: list[RawBattleEntity] = []
        for address, raw in zip(addresses, blobs, strict=True):
            category = struct.unpack_from("<i", raw, 0x08)[0]
            if not self.ENTITY_CATEGORY_MIN <= category < self.ENTITY_CATEGORY_MAX:
                continue
            entity = self._parse_raw_entity(address, raw)
            if entity is not None:
                entities.append(entity)
        self._raw_entity_addresses = tuple(entity.address for entity in entities)
        return tuple(entities)

    def discover_raw_entity_addresses(self) -> tuple[RawBattleEntity, ...]:
        return self._poll_battle_entity_collection()

    def poll_raw_entities(self) -> tuple[RawBattleEntity, ...]:
        return self._poll_battle_entity_collection()

    def poll_raw_entities_with_health(self) -> tuple[RawBattleEntity, ...]:
        """Add the passive HP component fields to validated raw entities.

        Unlike ``poll_entities()``, this deliberately does not use the stale
        upstream x86_64 vtable offsets.  It starts from the MuMu-proven raw
        entity candidates, then follows ``entity+0x18 -> owner+0x10``.
        """
        entities = self.poll_raw_entities()
        if not entities:
            return ()
        primary = [
            mapping
            for mapping in self.memory.maps()
            if mapping.perms.startswith("rw") and mapping.path == "[anon:scudo:primary]"
        ]
        owners_raw = self.memory.read_many(
            [(entity.address + 0x18, 8) for entity in entities], timeout=30
        )
        owners = [struct.unpack("<Q", raw)[0] for raw in owners_raw]
        valid_owners = [
            (index, owner)
            for index, owner in enumerate(owners)
            if self._mapped(owner, primary, 0x18)
        ]
        if not valid_owners:
            return ()
        components_raw = self.memory.read_many(
            [(owner + 0x10, 8) for _, owner in valid_owners], timeout=30
        )
        valid_components = [
            (index, struct.unpack("<Q", raw)[0])
            for (index, _), raw in zip(valid_owners, components_raw, strict=True)
            if self._mapped(struct.unpack("<Q", raw)[0], primary, 0x18)
        ]
        if not valid_components:
            return ()
        health_raw = self.memory.read_many(
            [(component + 0x10, 8) for _, component in valid_components], timeout=30
        )
        health_by_index = {
            index: struct.unpack("<2i", raw)
            for (index, _), raw in zip(valid_components, health_raw, strict=True)
        }
        output: list[RawBattleEntity] = []
        for index, entity in enumerate(entities):
            values = health_by_index.get(index)
            if values is None:
                continue
            hp, max_hp = values
            if not (0 < hp <= max_hp <= 50_000):
                continue
            output.append(
                RawBattleEntity(
                    address=entity.address,
                    kind=entity.kind,
                    side=entity.side,
                    x=entity.x,
                    y=entity.y,
                    x2=entity.x2,
                    y2=entity.y2,
                    card_id=entity.card_id,
                    level=entity.level,
                    hp=hp,
                    max_hp=max_hp,
                )
            )
        return tuple(output)

    @staticmethod
    def _mapped(address: int, mappings: list[MemoryRegion], size: int = 1) -> bool:
        return any(mapping.contains(address, size) for mapping in mappings)

    def _discover_entity_addresses(
        self,
        mappings: list[MemoryRegion],
        libg_base: int,
    ) -> tuple[int, ...]:
        primary = sorted(
            (
                mapping
                for mapping in mappings
                if mapping.perms.startswith("rw")
                and mapping.path == "[anon:scudo:primary]"
            ),
            key=lambda item: item.start,
        )
        if not primary:
            return ()
        anchor = min(self._class_key(mapping.start) for mapping in primary)
        entity_regions = [
            mapping
            for mapping in primary
            if self._class_key(mapping.start) - anchor in self.ENTITY_CLASSES
        ]
        if not entity_regions:
            return ()

        blobs = self.memory.read_many(
            [(mapping.start, mapping.size) for mapping in entity_regions],
            timeout=120,
        )
        needles = (
            struct.pack("<Q", libg_base + self.UNIT_VTABLE_OFFSET),
            struct.pack("<Q", libg_base + self.KING_VTABLE_OFFSET),
        )
        addresses: set[int] = set()
        for mapping, data in zip(entity_regions, blobs, strict=True):
            for needle in needles:
                cursor = 0
                while True:
                    found = data.find(needle, cursor)
                    if found < 0:
                        break
                    cursor = found + 1
                    if found % 8 == 0 and found + 0x124 <= len(data):
                        addresses.add(mapping.start + found)
        return tuple(sorted(addresses))

    @staticmethod
    def _entity_role(kind: int, side: int, x: int, card_id: int) -> str | None:
        owner = "own" if side == 0 else "opponent"
        if card_id == -1 and 8000 <= x <= 10000:
            return f"{owner}_king"
        if card_id == -1 and (x < 8000 or x > 10000):
            lane = 0 if x < 9000 else 1
            return f"{owner}_princess_{lane}"
        return None

    def poll_entities(self, *, rescan: bool = False) -> tuple[BattleEntity, ...]:
        mappings = self.memory.maps()
        libg_mappings = [mapping for mapping in mappings if mapping.path.endswith("/libg.so")]
        if not libg_mappings:
            return ()
        libg_base = min(mapping.start - mapping.offset for mapping in libg_mappings)
        self._libg_base = libg_base
        primary = [
            mapping
            for mapping in mappings
            if mapping.perms.startswith("rw")
            and mapping.path == "[anon:scudo:primary]"
        ]
        if rescan or not self._entity_addresses:
            self._entity_addresses = self._discover_entity_addresses(mappings, libg_base)
        if not self._entity_addresses:
            return ()

        addresses = [
            address
            for address in self._entity_addresses
            if self._mapped(address, primary, 0x124)
        ]
        if not addresses:
            return ()
        raw_entities = self.memory.read_many(
            [(address, 0x124) for address in addresses],
            timeout=30,
        )
        candidates: list[tuple[int, int, int, int, int, int, int, int, int, int]] = []
        for address, raw in zip(addresses, raw_entities, strict=True):
            vtable = struct.unpack_from("<Q", raw, 0)[0]
            if vtable not in {
                libg_base + self.UNIT_VTABLE_OFFSET,
                libg_base + self.KING_VTABLE_OFFSET,
            }:
                continue
            hp_owner = struct.unpack_from("<Q", raw, 0x18)[0]
            state = struct.unpack_from("<i", raw, 0x24)[0]
            kind = struct.unpack_from("<i", raw, 0x30)[0]
            side = struct.unpack_from("<i", raw, 0x78)[0]
            x, y, x2, y2 = struct.unpack_from("<4i", raw, 0x7C)
            card_id = struct.unpack_from("<i", raw, 0xAC)[0]
            level = struct.unpack_from("<i", raw, 0x120)[0]
            if not (
                self._mapped(hp_owner, primary, 0x18)
                and state >= 3
                and 10 <= kind <= 20
                and side in (0, 1)
                and 0 <= x <= 18000
                and 0 <= y <= 32000
                and abs(x - x2) <= 200
                and abs(y - y2) <= 200
                and 0 <= level <= 16
                and (card_id == -1 or 20_000_000 <= card_id < 1_000_000_000)
            ):
                continue
            candidates.append((address, kind, side, x, y, x2, y2, card_id, level, hp_owner))
        if not candidates:
            return ()

        component_raw = self.memory.read_many(
            [(candidate[-1] + 0x10, 8) for candidate in candidates],
            timeout=30,
        )
        with_components = [
            (candidate, struct.unpack("<Q", raw)[0])
            for candidate, raw in zip(candidates, component_raw, strict=True)
            if self._mapped(struct.unpack("<Q", raw)[0], primary, 0x18)
        ]
        if not with_components:
            return ()
        hp_raw = self.memory.read_many(
            [(component + 0x10, 8) for _, component in with_components],
            timeout=30,
        )

        output: list[BattleEntity] = []
        for (candidate, _), raw in zip(with_components, hp_raw, strict=True):
            address, kind, side, x, y, x2, y2, raw_card_id, level, _ = candidate
            hp, max_hp = struct.unpack("<2i", raw)
            role = self._entity_role(kind, side, x, raw_card_id)
            if not (0 <= hp <= max_hp <= 50_000):
                continue
            if role is None and hp == 0:
                continue
            output.append(
                BattleEntity(
                    pointer=address,
                    kind=kind,
                    side=side,
                    x=x,
                    y=y,
                    x2=x2,
                    y2=y2,
                    card_id=None if raw_card_id == -1 else raw_card_id,
                    level=level + 1,
                    hp=hp,
                    max_hp=max_hp,
                    role=role,
                )
            )
        return tuple(output)

    def runtime_seed(self, pointers: BattlePointers) -> dict[str, object]:
        if self._libg_base is None:
            mappings = self.memory.maps()
            libg_mappings = [mapping for mapping in mappings if mapping.path.endswith("/libg.so")]
            if not libg_mappings:
                raise AdbError("游戏进程没有 libg.so 映射")
            self._libg_base = min(mapping.start - mapping.offset for mapping in libg_mappings)
        return {
            "holder": f"0x{pointers.holder:x}",
            "ui_state": f"0x{pointers.ui_state:x}",
            "model": f"0x{pointers.model:x}",
            "hand_array": f"0x{pointers.hand_array:x}",
            "queue_array": f"0x{pointers.queue_array:x}",
            "entity_addresses": [f"0x{address:x}" for address in self._entity_addresses],
            "libg_base": f"0x{self._libg_base:x}",
        }
