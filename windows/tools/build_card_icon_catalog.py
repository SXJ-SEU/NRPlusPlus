from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError


SCHEMA_VERSION = 1
DEFAULT_WORKERS = 8
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_TIMEOUT_SECONDS = 30
VARIANT_DIRECTORY = {
    "normal": "normal",
    "evolution": "evolution",
    "hero": "hero",
}


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError(f"{path} does not contain an items array")
    return payload


def _frame_class(rarity: object) -> str:
    if rarity == "legendary":
        return "legendary"
    if rarity == "champion":
        return "champion"
    return "standard"


def _source_spec(
    canonical_id: int,
    variant: str,
    url: str,
    output_root: Path,
) -> dict[str, Any]:
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".png"
    relative_path = Path("source") / VARIANT_DIRECTORY[variant] / f"{canonical_id}{suffix}"
    return {
        "variant": variant,
        "url": url,
        "path": relative_path.as_posix(),
        "absolutePath": str((output_root / relative_path).resolve()),
    }


def build_catalog(cards_path: Path, output_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = _read_json(cards_path)
    raw_items = [item for item in payload["items"] if isinstance(item, dict)]
    issues: list[dict[str, Any]] = []

    by_identity: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    seen_ids: set[int] = set()
    for index, item in enumerate(raw_items):
        card_id = item.get("id")
        name = item.get("name")
        medium = item.get("iconUrls", {}).get("medium") if isinstance(item.get("iconUrls"), dict) else None
        if not isinstance(card_id, int) or not isinstance(name, str) or not isinstance(medium, str):
            issues.append(
                {
                    "severity": "error",
                    "code": "invalid_card_record",
                    "recordIndex": index,
                    "cardId": card_id,
                    "name": name,
                    "message": "Card record requires integer id, string name, and iconUrls.medium.",
                }
            )
            continue
        if card_id in seen_ids:
            issues.append(
                {
                    "severity": "error",
                    "code": "duplicate_card_id",
                    "cardId": card_id,
                    "name": name,
                    "message": "The same card id appears more than once.",
                }
            )
        seen_ids.add(card_id)
        by_identity[(name, medium)].append(item)

    cards: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    id_aliases: dict[str, int] = {}
    expected_outputs: list[dict[str, Any]] = []

    for group in by_identity.values():
        # Preserve cards.json ordering: the first record is the canonical display record.
        canonical = group[0]
        canonical_id = int(canonical["id"])
        card_ids = [int(item["id"]) for item in group]
        for card_id in card_ids:
            id_aliases[str(card_id)] = canonical_id

        names = {str(item.get("name")) for item in group}
        rarities = {str(item.get("rarity")) for item in group}
        costs = {item.get("elixirCost") for item in group if item.get("elixirCost") is not None}
        if len(names) != 1 or len(rarities) != 1 or len(costs) > 1:
            issues.append(
                {
                    "severity": "error",
                    "code": "alias_metadata_conflict",
                    "cardIds": card_ids,
                    "message": "Records sharing a display identity disagree on name, rarity, or elixir cost.",
                }
            )

        merged_urls: dict[str, set[str]] = {"normal": set(), "evolution": set(), "hero": set()}
        evolution_cycles: set[int] = set()
        for item in group:
            icon_urls = item.get("iconUrls") if isinstance(item.get("iconUrls"), dict) else {}
            for variant, field in (
                ("normal", "medium"),
                ("evolution", "evolutionMedium"),
                ("hero", "heroMedium"),
            ):
                value = icon_urls.get(field)
                if isinstance(value, str) and value:
                    merged_urls[variant].add(value)
            forms = item.get("forms") if isinstance(item.get("forms"), dict) else {}
            evolution = forms.get("evolution") if isinstance(forms.get("evolution"), dict) else {}
            cycles = evolution.get("cycles")
            if isinstance(cycles, int):
                evolution_cycles.add(cycles)

        for variant, urls in merged_urls.items():
            if len(urls) > 1:
                issues.append(
                    {
                        "severity": "error",
                        "code": "alias_source_conflict",
                        "cardIds": card_ids,
                        "variant": variant,
                        "urls": sorted(urls),
                        "message": "Aliased records provide different source URLs for the same variant.",
                    }
                )
        if len(evolution_cycles) > 1:
            issues.append(
                {
                    "severity": "error",
                    "code": "evolution_cycle_conflict",
                    "cardIds": card_ids,
                    "cycles": sorted(evolution_cycles),
                    "message": "Aliased records disagree on evolution cycle count.",
                }
            )

        rarity = str(canonical.get("rarity"))
        elixir_cost = next(iter(costs), None)
        card_sources: dict[str, dict[str, Any]] = {}
        for variant in ("normal", "evolution", "hero"):
            urls = merged_urls[variant]
            if not urls:
                continue
            spec = _source_spec(canonical_id, variant, next(iter(urls)), output_root)
            card_sources[variant] = {key: value for key, value in spec.items() if key != "absolutePath"}
            spec.update({"cardId": canonical_id, "name": canonical["name"]})
            sources.append(spec)

        cycles = next(iter(evolution_cycles), None)
        if "evolution" in card_sources and cycles not in {1, 2}:
            issues.append(
                {
                    "severity": "error",
                    "code": "invalid_evolution_cycles",
                    "cardId": canonical_id,
                    "name": canonical["name"],
                    "cycles": cycles,
                    "message": "Evolution source requires a supported cycle count of 1 or 2.",
                }
            )
        if cycles in {1, 2} and "evolution" not in card_sources:
            issues.append(
                {
                    "severity": "error",
                    "code": "missing_evolution_source",
                    "cardId": canonical_id,
                    "name": canonical["name"],
                    "message": "Evolution form metadata exists but iconUrls.evolutionMedium is missing.",
                }
            )

        states: list[dict[str, Any]] = [{"form": "normal", "key": "normal"}]
        expected_outputs.append({"cardId": canonical_id, "form": "normal", "state": "normal"})
        if "evolution" in card_sources and cycles in {1, 2}:
            evolution_states = []
            for charge in range(cycles + 1):
                key = f"evolution_{charge}_of_{cycles}"
                evolution_states.append({"charge": charge, "cycles": cycles, "key": key})
                expected_outputs.append(
                    {
                        "cardId": canonical_id,
                        "form": "evolution",
                        "state": key,
                        "charge": charge,
                        "cycles": cycles,
                    }
                )
            states.append({"form": "evolution", "cycles": cycles, "states": evolution_states})
        if "hero" in card_sources:
            states.append({"form": "hero", "key": "hero"})
            expected_outputs.append({"cardId": canonical_id, "form": "hero", "state": "hero"})

        cards.append(
            {
                "canonicalCardId": canonical_id,
                "cardIds": card_ids,
                "name": canonical["name"],
                "rarity": rarity,
                "frameClass": _frame_class(rarity),
                "elixirCost": elixir_cost,
                "dynamicElixirCost": elixir_cost is None,
                "sources": card_sources,
                "states": states,
            }
        )

    cards.sort(key=lambda item: (str(item["name"]).casefold(), int(item["canonicalCardId"])))
    sources.sort(key=lambda item: (str(item["variant"]), int(item["cardId"])))
    expected_outputs.sort(key=lambda item: (int(item["cardId"]), str(item["form"]), str(item["state"])))

    state_counts = Counter(item["form"] for item in expected_outputs)
    rarity_counts = Counter(item["rarity"] for item in cards)
    frame_counts = Counter(item["frameClass"] for item in cards)
    catalog = {
        "schemaVersion": SCHEMA_VERSION,
        "sourceCatalog": str(cards_path.resolve()),
        "sourceMetadata": payload.get("formMetadata"),
        "summary": {
            "sourceRecords": len(raw_items),
            "uniqueCards": len(cards),
            "cardIds": len(id_aliases),
            "normalSources": sum(1 for item in sources if item["variant"] == "normal"),
            "evolutionSources": sum(1 for item in sources if item["variant"] == "evolution"),
            "heroSources": sum(1 for item in sources if item["variant"] == "hero"),
            "expectedOutputs": len(expected_outputs),
            "expectedOutputsByForm": dict(sorted(state_counts.items())),
            "rarities": dict(sorted(rarity_counts.items())),
            "frameClasses": dict(sorted(frame_counts.items())),
        },
        "idAliases": dict(sorted(id_aliases.items(), key=lambda pair: int(pair[0]))),
        "cards": cards,
        "expectedOutputs": expected_outputs,
    }

    overlap = payload.get("formMetadata", {}).get("overlapSummary", {})
    if isinstance(overlap, dict):
        declared_evolution = overlap.get("evolutionCards")
        actual_evolution = catalog["summary"]["evolutionSources"]
        if isinstance(declared_evolution, int) and declared_evolution != actual_evolution:
            issues.append(
                {
                    "severity": "warning",
                    "code": "evolution_metadata_source_scope_mismatch",
                    "declaredForms": declared_evolution,
                    "availableSources": actual_evolution,
                    "message": "formMetadata Evolution coverage differs from iconUrls.evolutionMedium coverage.",
                }
            )
        declared_hero = overlap.get("heroCards")
        actual_hero = catalog["summary"]["heroSources"]
        if isinstance(declared_hero, int) and declared_hero != actual_hero:
            issues.append(
                {
                    "severity": "warning",
                    "code": "hero_metadata_source_scope_mismatch",
                    "declaredForms": declared_hero,
                    "availableSources": actual_hero,
                    "message": "formMetadata Hero rows are runtime form coverage; only iconUrls.heroMedium entries are renderable Hero sources.",
                }
            )
    return catalog, issues


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect_image(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        image.load()
        bands = image.getbands()
        return {
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
            "hasAlpha": "A" in bands or "transparency" in image.info,
        }


def _download_one(source: dict[str, Any], *, refresh: bool) -> dict[str, Any]:
    destination = Path(source["absolutePath"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = {key: value for key, value in source.items() if key != "absolutePath"}
    result["host"] = urllib.parse.urlparse(str(source["url"])).hostname
    result["status"] = "pending"

    try:
        if refresh or not destination.exists():
            temporary = destination.with_suffix(destination.suffix + ".part")
            last_error: Exception | None = None
            for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
                try:
                    request = urllib.request.Request(
                        str(source["url"]),
                        headers={"User-Agent": "NRPlusPlus-card-icon-audit/1.0"},
                    )
                    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                        with temporary.open("wb") as stream:
                            shutil.copyfileobj(response, stream)
                    os.replace(temporary, destination)
                    last_error = None
                    break
                except (OSError, urllib.error.URLError) as exc:
                    last_error = exc
                    temporary.unlink(missing_ok=True)
                    if attempt < DOWNLOAD_ATTEMPTS:
                        time.sleep(attempt)
            if last_error is not None:
                raise last_error

        result.update(_inspect_image(destination))
        result["bytes"] = destination.stat().st_size
        result["sha256"] = _sha256(destination)
        result["status"] = "ok"
        if result["format"] != "PNG":
            result["status"] = "warning"
            result["warning"] = f"Expected PNG, decoded as {result['format']}."
    except (OSError, UnidentifiedImageError, urllib.error.URLError) as exc:
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def audit_sources(
    sources: list[dict[str, Any]],
    *,
    download: bool,
    refresh: bool,
    workers: int,
) -> list[dict[str, Any]]:
    if not download:
        return [
            {
                **{key: value for key, value in source.items() if key != "absolutePath"},
                "host": urllib.parse.urlparse(str(source["url"])).hostname,
                "status": "not_downloaded",
            }
            for source in sources
        ]

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(_download_one, source, refresh=refresh): source
            for source in sources
        }
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: (str(item["variant"]), int(item["cardId"])))


def _find_content_issues(audit: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    by_card: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in audit:
        by_card[int(item["cardId"])].append(item)
        if item["status"] == "error":
            issues.append(
                {
                    "severity": "error",
                    "code": "source_unavailable",
                    "cardId": item["cardId"],
                    "name": item["name"],
                    "variant": item["variant"],
                    "message": item.get("error", "Source could not be downloaded or decoded."),
                }
            )
        elif item["status"] == "warning":
            issues.append(
                {
                    "severity": "warning",
                    "code": "source_format_warning",
                    "cardId": item["cardId"],
                    "name": item["name"],
                    "variant": item["variant"],
                    "message": item.get("warning"),
                }
            )

    for card_id, items in by_card.items():
        hashes: dict[str, list[str]] = defaultdict(list)
        for item in items:
            if item.get("sha256"):
                hashes[str(item["sha256"])].append(str(item["variant"]))
        for variants in hashes.values():
            if len(variants) > 1:
                issues.append(
                    {
                        "severity": "error",
                        "code": "identical_cross_form_source",
                        "cardId": card_id,
                        "name": items[0]["name"],
                        "variants": sorted(variants),
                        "message": "Different card forms resolve to byte-identical source images.",
                    }
                )
    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in audit:
        if item.get("sha256"):
            by_hash[str(item["sha256"])].append(item)
    for matching in by_hash.values():
        card_ids = sorted({int(item["cardId"]) for item in matching})
        if len(card_ids) > 1:
            issues.append(
                {
                    "severity": "warning",
                    "code": "identical_cross_card_source",
                    "cardIds": card_ids,
                    "sources": [
                        {
                            "cardId": item["cardId"],
                            "name": item["name"],
                            "variant": item["variant"],
                        }
                        for item in matching
                    ],
                    "message": "Different display cards resolve to byte-identical source images and require review.",
                }
            )
    return issues


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_report(
    path: Path,
    catalog: dict[str, Any],
    audit: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> None:
    summary = catalog["summary"]
    statuses = Counter(item["status"] for item in audit)
    hosts = Counter(item.get("host") or "unknown" for item in audit)
    severity = Counter(item["severity"] for item in issues)
    alias_cards = [item for item in catalog["cards"] if len(item["cardIds"]) > 1]
    dynamic_cards = [item for item in catalog["cards"] if item["dynamicElixirCost"]]

    lines = [
        "# Card icon source audit — phase 1",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Scope",
        "",
        f"- Source records: {summary['sourceRecords']}",
        f"- Unique display cards: {summary['uniqueCards']}",
        f"- Normal sources: {summary['normalSources']}",
        f"- Evolution sources: {summary['evolutionSources']}",
        f"- Hero sources: {summary['heroSources']}",
        f"- Expected rendered states: {summary['expectedOutputs']}",
        "",
        "## Source audit",
        "",
        f"- Statuses: {dict(sorted(statuses.items()))}",
        f"- Hosts: {dict(sorted(hosts.items()))}",
        f"- Issues by severity: {dict(sorted(severity.items()))}",
        "",
        "## Frame classes",
        "",
        f"- {summary['frameClasses']}",
        "",
        "Legendary and Champion are separate frame classes; Common, Rare, and Epic use Standard.",
        "",
        "## Aliases and dynamic cost",
        "",
    ]
    if alias_cards:
        for card in alias_cards:
            lines.append(
                f"- {card['name']}: canonical {card['canonicalCardId']}, ids {card['cardIds']}"
            )
    else:
        lines.append("- No aliased display cards.")
    for card in dynamic_cards:
        lines.append(f"- {card['name']} ({card['canonicalCardId']}): runtime elixir cost required.")

    lines.extend(["", "## Issues", ""])
    if not issues:
        lines.append("No machine-detectable catalog or source errors.")
    else:
        for issue in issues:
            identity = issue.get("name") or issue.get("cardId") or issue.get("recordIndex") or "catalog"
            lines.append(
                f"- [{str(issue['severity']).upper()}] {issue['code']} ({identity}): {issue['message']}"
            )

    lines.extend(
        [
            "",
            "## Phase boundary",
            "",
            "This phase verifies catalog structure, availability, decoding, file metadata, hashes, aliases, and exact cross-form duplicates. Semantic visual approval of every artwork and all UI template geometry belongs to the golden-sample phase.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_gallery(path: Path, audit: list[dict[str, Any]]) -> None:
    cards = []
    for item in audit:
        relative = Path("..") / Path(str(item["path"]))
        label = f"{item['cardId']} · {item['name']} · {item['variant']}"
        details = f"{item.get('width', '?')}×{item.get('height', '?')} · {item.get('mode', '?')}"
        cards.append(
            "<figure>"
            f'<img src="{html.escape(relative.as_posix(), quote=True)}" loading="lazy" '
            f'alt="{html.escape(label, quote=True)}">'
            f"<figcaption><strong>{html.escape(label)}</strong><br>{html.escape(details)}</figcaption>"
            "</figure>"
        )
    document = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NRPlusPlus card icon source gallery</title>
<style>
body { margin: 24px; color: #e8edf5; background: #111722; font: 14px/1.4 system-ui, sans-serif; }
h1 { margin: 0 0 18px; }
main { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 14px; }
figure { margin: 0; padding: 12px; border: 1px solid #354055; border-radius: 8px; background: #1b2433; }
img { display: block; width: 100%; height: 220px; object-fit: contain; background: #2a3445; }
figcaption { margin-top: 9px; overflow-wrap: anywhere; }
</style>
</head>
<body>
<h1>Card icon source gallery</h1>
<main>
""" + "\n".join(cards) + """
</main>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")


def run(
    cards_path: Path,
    output_root: Path,
    *,
    download: bool = True,
    refresh: bool = False,
    workers: int = DEFAULT_WORKERS,
) -> int:
    output_root.mkdir(parents=True, exist_ok=True)
    catalog, catalog_issues = build_catalog(cards_path, output_root)
    source_specs: list[dict[str, Any]] = []
    for card in catalog["cards"]:
        for variant, source in card["sources"].items():
            source_specs.append(
                {
                    **source,
                    "variant": variant,
                    "cardId": card["canonicalCardId"],
                    "name": card["name"],
                    "absolutePath": str((output_root / source["path"]).resolve()),
                }
            )

    audit = audit_sources(
        source_specs,
        download=download,
        refresh=refresh,
        workers=workers,
    )
    issues = catalog_issues + _find_content_issues(audit)
    generated_at = datetime.now(timezone.utc).isoformat()
    catalog["generatedAt"] = generated_at
    audit_payload = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated_at,
        "summary": {
            "sources": len(audit),
            "statuses": dict(sorted(Counter(item["status"] for item in audit).items())),
            "issues": dict(sorted(Counter(item["severity"] for item in issues).items())),
        },
        "sources": audit,
        "issues": issues,
    }

    catalog_dir = output_root / "catalog"
    _write_json(catalog_dir / "card_icon_manifest.json", catalog)
    _write_json(catalog_dir / "source_audit.json", audit_payload)
    _write_report(catalog_dir / "phase1_report.md", catalog, audit, issues)
    _write_gallery(catalog_dir / "source_gallery.html", audit)

    print(json.dumps({"catalog": catalog["summary"], "audit": audit_payload["summary"]}, indent=2))
    return 1 if any(issue["severity"] == "error" for issue in issues) else 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and audit the NRPlusPlus card icon source catalog.")
    parser.add_argument("--cards", type=Path, required=True, help="Path to deploy/cards.json")
    parser.add_argument("--output", type=Path, required=True, help="Standardized asset workspace")
    parser.add_argument("--no-download", action="store_true", help="Build metadata without downloading sources")
    parser.add_argument("--refresh", action="store_true", help="Download all sources again")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Concurrent download count")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    return run(
        args.cards,
        args.output,
        download=not args.no_download,
        refresh=args.refresh,
        workers=args.workers,
    )


if __name__ == "__main__":
    raise SystemExit(main())
