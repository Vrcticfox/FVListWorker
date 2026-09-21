"""공개된 아틀라스가 이번에 생성한 파일과 같은지 확인한다."""

import argparse
import hashlib
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def verify_public(directory, base_url, attempts=3, interval=30):
    # JSON은 아직 공개 전이므로 이미지 파일만 비교한다.
    paths = sorted(Path(directory).glob("FV*.jpg"))
    if not paths:
        raise ValueError("검증할 아틀라스가 없습니다.")
    if urllib.parse.urlsplit(base_url).scheme != "https":
        raise ValueError("공개 URL은 https여야 합니다.")
    expected = {p.name: hashlib.sha256(p.read_bytes()).digest() for p in paths}
    for attempt in range(attempts):
        failed = []
        for name, digest in expected.items():
            request = urllib.request.Request(
                base_url.rstrip("/") + "/" + name,
                headers={"User-Agent": "ProjectFV-public-verification/1.0"},
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = response.read(32 * 1024 * 1024 + 1)
                if hashlib.sha256(data).digest() != digest:
                    failed.append(name)
            except (urllib.error.URLError, TimeoutError):
                failed.append(name)
        if not failed:
            print(f"공개 아틀라스 {len(paths)}개 확인 완료")
            return
        print("아직 일치하지 않는 파일: " + ", ".join(failed))
        if attempt + 1 < attempts:
            time.sleep(interval)
    raise RuntimeError("공개 이미지 검증 실패: JSON을 전환하지 않습니다.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    verify_public(args.directory, args.base_url)
