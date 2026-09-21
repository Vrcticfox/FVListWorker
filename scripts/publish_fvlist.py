"""GitHub Pages 브랜치 배포: 이미지 선공개 후 JSON을 전환한다."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from verify_public import verify_public

TARGET = Path("vrchat/ProjectFV/ImageLoading")
STAGED = Path("build/staged")
WAIT_SECONDS = 900


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def api(path, method="GET"):
    return json.loads(run("gh", "api", "--method", method, path))


def publish(paths, message):
    # 입력 CSV에 새 커밋이 있어도 생성 당시의 후보 파일은 그대로 유지한다.
    run("git", "pull", "--rebase", "origin", "main")
    TARGET.mkdir(parents=True, exist_ok=True)
    for path in paths:
        shutil.copy2(path, TARGET / path.name)
    run("git", "add", "--", str(TARGET))
    if run("git", "diff", "--cached", "--name-only"):
        run("git", "commit", "-m", message)
        for attempt in range(3):
            try:
                run("git", "pull", "--rebase", "origin", "main")
                run("git", "push", "origin", "HEAD:main")
                break
            except subprocess.CalledProcessError:
                if attempt == 2:
                    raise
    commit = run("git", "rev-parse", "HEAD")
    repo = os.environ["GITHUB_REPOSITORY"]
    # GITHUB_TOKEN으로 push하면 Pages가 자동 실행되지 않아 명시적으로 요청한다.
    api(f"repos/{repo}/pages/builds", "POST")
    wait_for_pages(repo, commit)


def wait_for_pages(repo, commit, attempts=60):
    """동일 커밋에 빌드가 여러 개 생겨도 성공한 배포를 기다린다."""
    checked = {}
    last_error = ""
    for attempt in range(attempts):
        builds = api(f"repos/{repo}/pages/builds?per_page=30")
        for build in builds:
            revision = build.get("commit")
            if not revision:
                continue
            if revision not in checked:
                # CSV 커밋이 추가돼 Pages가 후속 커밋을 빌드해도 이번 변경이 포함되면 인정한다.
                run("git", "fetch", "origin", "main")
                checked[revision] = subprocess.run(
                    ["git", "merge-base", "--is-ancestor", commit, revision],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                ).returncode == 0
            if checked[revision]:
                if build["status"] == "built":
                    return
                if build["status"] == "errored":
                    # 실패한 기록 하나가 다른 진행 중 빌드까지 실패했다는 뜻은 아니다.
                    message = (build.get("error") or {}).get("message") or "상세 오류 없음"
                    detail = f"{build.get('url', revision)}: {message}"
                    if detail != last_error:
                        print(f"Pages 실패 기록 확인, 다른 빌드 완료 대기: {detail}", flush=True)
                    last_error = detail
        if attempt + 1 < attempts:
            time.sleep(10)
    raise RuntimeError("Pages 배포 완료 확인 시간 초과" + (f"; 최근 실패: {last_error}" if last_error else ""))


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    pages = api(f"repos/{repo}/pages")
    source = pages.get("source", {})
    if pages.get("build_type") == "workflow" or source.get("branch") != "main" or source.get("path") != "/":
        raise RuntimeError("Pages를 Deploy from a branch / main / (root)로 설정해야 합니다.")
    public_url = pages["html_url"].rstrip("/") + "/" + TARGET.as_posix()
    run("git", "config", "user.name", "github-actions[bot]")
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    # 이전 실행이 JSON 전환 직후 중단됐을 수도 있으므로 재사용 전에 항상 기다린다.
    print("이전 뱅크 보호 대기: 15분", flush=True)
    time.sleep(WAIT_SECONDS)
    candidate = json.loads((STAGED / "FVList.json").read_text(encoding="utf-8"))
    if candidate.get("worlds") == [] and candidate.get("worldCount") == 0 and candidate.get("previewPageCount") == 0 and candidate.get("detailPageCount") == 0:
        # 모든 월드가 내려간 경우도 정상 결과다. 이미지는 그대로 두고 빈 목록만 공개한다.
        print("표시 가능한 월드가 없어 빈 목록을 공개합니다.", flush=True)
    else:
        publish(sorted(STAGED.glob("FV*.jpg")), "Update FVList inactive atlas bank")
        print("이미지 배포 후 전파 대기: 15분", flush=True)
        time.sleep(WAIT_SECONDS)
        verify_public(STAGED, public_url)
    publish([STAGED / "FVList.json"], "Publish FVList catalog")
    print("FVList 공개 완료. 다음 실행 시작 시 이전 뱅크 보호 시간을 적용합니다.", flush=True)


if __name__ == "__main__":
    main()
