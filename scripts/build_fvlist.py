#!/usr/bin/env python3
"""CSV에서 FVList.json과 정사각형 아틀라스 후보를 생성한다. 공개는 별도 단계다."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from PIL import Image, ImageOps


WORLD_ID_RE = re.compile(r"^wrld_[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
API_ROOT = "https://api.vrchat.cloud/api/1/worlds/"
PREVIEW_SIZE = 256
DETAIL_SIZE = 512
PREVIEW_INNER = 252
DETAIL_INNER = 504
PREVIEW_COLUMNS = PREVIEW_ROWS = 8
DETAIL_COLUMNS = DETAIL_ROWS = 4
DEFAULT_MAX_DETAIL_PAGES = 32


@dataclass(frozen=True)
class BaseWorld:
    world_id: str
    custom_tags: list[str]
    custom_description: str


def read_base_csv(path: Path) -> list[BaseWorld]:
    """헤더 유무에 관계없이 CSV를 읽고 UUID와 중복을 검사한다."""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    if rows and [cell.strip().lower() for cell in rows[0]] in (["worldid", "tag", "description"], ["worldid", "customtags", "customdescription"]):
        header_offset = 2
        rows = rows[1:]
    else:
        header_offset = 1

    result: list[BaseWorld] = []
    seen: set[str] = set()
    for row_number, row in enumerate(rows, start=header_offset):
        if not row or not any(cell.strip() for cell in row):
            continue
        if len(row) != 3:
            raise ValueError(f"CSV row {row_number} must contain exactly 3 columns")
        world_id, tags, description = (cell.strip() for cell in row)
        world_id = world_id.lower()
        if not WORLD_ID_RE.fullmatch(world_id):
            raise ValueError(f"CSV row {row_number} has an invalid worldId: {world_id}")
        if world_id in seen:
            raise ValueError(f"duplicate worldId at CSV row {row_number}: {world_id}")
        seen.add(world_id)
        result.append(BaseWorld(world_id, [tag.strip() for tag in tags.split("|") if tag.strip()], description))
    if not result:
        raise ValueError("FVBase.csv contains no worlds")
    return result


def read_current_catalog(path: Path | None) -> tuple[str, int]:
    """공개 뱅크와 버전을 읽는다. 잘못된 상태는 추측하지 않고 중단한다."""
    if path is None or not path.exists():
        return "A", 0
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        bank = data["atlasBank"]
        if "releaseId" in data:
            release = data["releaseId"]
        else:
            release = data["contentVersion"]
            if type(bank) is int and bank in (0, 1):
                bank = ("A", "B")[bank]
        if bank not in ("A", "B") or type(release) is not int or release < 0:
            raise ValueError
        return bank, release
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError(
            f"current catalog is malformed: {path}; "
            'expected releaseId + atlasBank A/B, or legacy contentVersion + atlasBank 0/1'
        ) from exc


def _request_bytes(url: str, user_agent: str, timeout: float = 30.0, retries: int = 3) -> bytes:
    """일시 오류만 재시도한다. 인증 쿠키는 전달하지 않는다."""
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last_error = exc
            isRetryable = exc.code == 429 or 500 <= exc.code <= 599
            if not isRetryable or attempt == retries:
                raise
            delay = exc.headers.get("Retry-After") if exc.code == 429 else None
            try:
                wait = float(delay) if delay else 2.0 ** attempt
            except ValueError:
                wait = (parsedate_to_datetime(delay) - datetime.now(timezone.utc)).total_seconds()
            if wait > 120:
                raise RuntimeError("API 대기 요구가 120초를 초과하여 이번 빌드를 중단합니다.") from exc
            time.sleep(max(0, wait))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == retries:
                raise
            time.sleep(2.0 ** attempt)
    raise RuntimeError("request failed") from last_error


# 월드 메타데이터를 조회한다.
def fetch_world(world_id: str, user_agent: str) -> dict[str, Any]:
    time.sleep(1)  # API 요청을 짧은 시간에 몰아 보내지 않는다.
    return json.loads(_request_bytes(API_ROOT + urllib.parse.quote(world_id, safe=""), user_agent).decode("utf-8"))


def fetch_image(url: str, user_agent: str) -> Image.Image:
    # URL은 API에서 받은 이미지 주소만 사용하며 인증 쿠키를 전달하지 않는다.
    return Image.open(io.BytesIO(_request_bytes(url, user_agent))).convert("RGB")


# 비율을 유지하고 중앙 정사각형만 남긴다.
def square_cover(image: Image.Image, size: int) -> Image.Image:
    return ImageOps.fit(image.convert("RGB"), (size, size), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))


# 셀 테두리를 가장자리 색으로 채워 이웃 사진이 섞이는 것을 줄인다.
def _edge_extended(image: Image.Image, inner_size: int, cell_size: int) -> Image.Image:
    inner = square_cover(image, inner_size)
    cell = Image.new("RGB", (cell_size, cell_size))
    left = (cell_size - inner_size) // 2
    cell.paste(inner, (left, left))
    # 가장자리 픽셀을 여백에 복사해 atlas filtering bleed를 줄인다.
    if left:
        cell.paste(inner.crop((0, 0, 1, inner_size)).resize((left, inner_size)), (0, left))
        cell.paste(inner.crop((inner_size - 1, 0, inner_size, inner_size)).resize((left, inner_size)), (left + inner_size, left))
        cell.paste(inner.crop((0, 0, inner_size, 1)).resize((inner_size, left)), (left, 0))
        cell.paste(inner.crop((0, inner_size - 1, inner_size, inner_size)).resize((inner_size, left)), (left, left + inner_size))
        for x, y, color in ((0, 0, inner.getpixel((0, 0))), (1, 0, inner.getpixel((inner_size - 1, 0))), (0, 1, inner.getpixel((0, inner_size - 1))), (1, 1, inner.getpixel((inner_size - 1, inner_size - 1)))):
            cell.paste(Image.new("RGB", (left, left), color), (0 if x == 0 else left + inner_size, 0 if y == 0 else left + inner_size))
    return cell


# CSV 순서대로 왼쪽 위부터 한 페이지를 채운다.
def make_atlas(images: list[Image.Image], start: int, count: int, columns: int, rows: int, inner_size: int, cell_size: int) -> Image.Image:
    atlas = Image.new("RGB", (columns * cell_size, rows * cell_size), (0, 0, 0))
    for local_index in range(count):
        image = _edge_extended(images[start + local_index], inner_size, cell_size)
        x = (local_index % columns) * cell_size
        y = (local_index // columns) * cell_size
        atlas.paste(image, (x, y))
    return atlas


# 원본과 생성 파일의 내용 식별값을 계산한다.
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# 직접 입력한 설명과 API 설명을 구분해 저장한다.
def _api_record(base: BaseWorld, api: dict[str, Any], world_index: int) -> dict[str, Any]:
    if not api.get("name") or type(api.get("capacity")) is not int:
        raise ValueError(f"월드 필수 정보가 누락되었습니다: {base.world_id}")
    image_url = api.get("imageUrl") or api.get("thumbnailImageUrl")
    if not image_url:
        raise ValueError(f"world has no image URL: {base.world_id}")
    return {
        "worldId": base.world_id,
        "worldName": api.get("name", ""),
        "authorName": api.get("authorName", ""),
        "capacity": api.get("capacity"),
        "recommendedCapacity": api.get("recommendedCapacity"),
        "worldDescription": api.get("description", ""),
        "customTags": base.custom_tags,
        "customDescription": base.custom_description,
        "_imageUrl": image_url,
    }


def build_release(source: Path, current: Path | None, output: Path, user_agent: str | None = None,
                  max_detail_pages: int = DEFAULT_MAX_DETAIL_PAGES,
                  world_fetcher: Callable[[str, str], dict[str, Any]] = fetch_world,
                  image_fetcher: Callable[[str, str], Image.Image] = fetch_image) -> dict[str, Any]:
    """완성된 후보 릴리스를 생성한다. 공개 파일은 이 함수에서 수정하지 않는다."""
    if not user_agent:
        raise ValueError("user-agent is required (set FV_USER_AGENT for network builds)")
    worlds = read_base_csv(source)
    active_bank, previous_release = read_current_catalog(current)
    target_bank = "A" if current is None or not current.exists() else ("B" if active_bank == "A" else "A")
    detail_pages = (len(worlds) + 15) // 16
    if detail_pages > max_detail_pages:
        raise ValueError(f"world list needs {detail_pages} detail pages; limit is {max_detail_pages}")

    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError(f"output directory must be empty: {output}")
    records: list[dict[str, Any]] = []
    images: list[Image.Image] = []
    for index, base in enumerate(worlds):
        print(f"월드 정보 수집 중: {index + 1}/{len(worlds)} {base.world_id}", flush=True)
        api = world_fetcher(base.world_id, user_agent)
        records.append(_api_record(base, api, index))
        images.append(square_cover(image_fetcher(records[-1].pop("_imageUrl"), user_agent), DETAIL_INNER))

    preview_pages = (len(images) + 63) // 64
    detail_pages = (len(images) + 15) // 16
    for page in range(preview_pages):
        start = page * 64
        atlas = make_atlas(images, start, min(64, len(images) - start), PREVIEW_COLUMNS, PREVIEW_ROWS, PREVIEW_INNER, PREVIEW_SIZE)
        atlas.save(output / f"FVPreview{target_bank}_{page:02d}.jpg", "JPEG", quality=90, optimize=True)
    for page in range(detail_pages):
        start = page * 16
        atlas = make_atlas(images, start, min(16, len(images) - start), DETAIL_COLUMNS, DETAIL_ROWS, DETAIL_INNER, DETAIL_SIZE)
        atlas.save(output / f"FVDetail{target_bank}_{page:02d}.jpg", "JPEG", quality=90, optimize=True)

    release = {
        "schemaVersion": 1,
        "releaseId": previous_release + 1,
        "sourceRevision": _sha256(source),
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "atlasBank": target_bank,
        "worldCount": len(records),
        "previewPageCount": preview_pages,
        "detailPageCount": detail_pages,
        "worlds": records,
    }
    (output / "FVList.json").write_text(json.dumps(release, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {path.name: _sha256(path) for path in sorted(output.iterdir()) if path.is_file() and path.name != "build-manifest.json"}
    (output / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return release


def main(argv: Iterable[str] | None = None) -> int:
    # Actions에서는 build를 생략할 수 있고, 로컬에서는 validate로 CSV만 검사한다.
    normalized_args = list(sys.argv[1:] if argv is None else argv)
    if normalized_args and normalized_args[0] not in {"build", "validate", "-h", "--help"}:
        normalized_args.insert(0, "build")
    parser = argparse.ArgumentParser(description="Build staged FVList data")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--source", type=Path, required=True)
    build.add_argument("--current", type=Path)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--user-agent", default=os.environ.get("FV_USER_AGENT"))
    build.add_argument("--max-detail-pages", type=int, default=DEFAULT_MAX_DETAIL_PAGES)
    validate = sub.add_parser("validate")
    validate.add_argument("--source", type=Path, required=True)
    args = parser.parse_args(normalized_args)
    try:
        if args.command == "validate":
            worlds = read_base_csv(args.source)
            if len(worlds) > DEFAULT_MAX_DETAIL_PAGES * 16:
                raise ValueError("world list exceeds the default detail-page limit")
            print(f"valid: {len(worlds)} worlds")
        else:
            build_release(args.source, args.current, args.output, args.user_agent, args.max_detail_pages)
    except Exception as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
