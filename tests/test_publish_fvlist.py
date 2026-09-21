"""배포 순서와 이미지 검증 실패 시 JSON 보호를 확인한다."""

import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import publish_fvlist


class PublishTests(unittest.TestCase):
    def test_failed_build_does_not_hide_later_success(self):
        failed = {"commit": "abc", "status": "errored", "error": {"message": "Page build failed."}}
        succeeded = {"commit": "abc", "status": "built"}
        for responses in ([[failed], [succeeded, failed]], [[failed, succeeded]]):
            with self.subTest(responses=responses), \
                 patch.object(publish_fvlist, "api", side_effect=responses), \
                 patch.object(publish_fvlist, "run"), \
                 patch.object(publish_fvlist.subprocess, "run", return_value=Mock(returncode=0)), \
                 patch.object(publish_fvlist.time, "sleep"):
                publish_fvlist.wait_for_pages("example/repo", "abc", attempts=2)

    def test_failed_builds_still_timeout(self):
        with patch.object(publish_fvlist, "api", return_value=[{"commit": "abc", "status": "errored", "error": {"message": "bad build"}}]), \
             patch.object(publish_fvlist, "run"), \
             patch.object(publish_fvlist.subprocess, "run", return_value=Mock(returncode=0)), \
             patch.object(publish_fvlist.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "bad build"):
                publish_fvlist.wait_for_pages("example/repo", "abc", attempts=2)

    def test_git_publish_preserves_catalog_and_unrelated_files(self):
        # 실제 원격 대신 임시 bare 저장소에서 파일 보존과 두 단계 커밋을 확인한다.
        def git(*args, cwd=None):
            return subprocess.check_output(["git", *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, work = root / "remote.git", root / "work"
            git("init", "--bare", str(remote))
            git("clone", str(remote), str(work))
            git("checkout", "-b", "main", cwd=work)
            git("config", "user.name", "Test", cwd=work)
            git("config", "user.email", "test@example.com", cwd=work)
            target = work / publish_fvlist.TARGET
            target.mkdir(parents=True)
            (target / "FVList.json").write_text("old")
            (work / "unrelated.txt").write_text("preserve me")
            git("add", ".", cwd=work)
            git("commit", "-m", "seed", cwd=work)
            git("push", "-u", "origin", "main", cwd=work)
            image = root / "FVPreviewB_00.jpg"
            image.write_bytes(b"image")
            catalog = root / "FVList.json"
            catalog.write_text("new")
            previous = Path.cwd()
            try:
                os.chdir(work)
                with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/test"}), \
                     patch.object(publish_fvlist, "api", side_effect=lambda path, method="GET":
                                  {} if method == "POST" else [{"commit": git("rev-parse", "HEAD"), "status": "built"}]):
                    publish_fvlist.publish([image], "images")
                    self.assertEqual((target / "FVList.json").read_text(), "old")
                    publish_fvlist.publish([catalog], "catalog")
                    self.assertEqual((target / "FVList.json").read_text(), "new")
                    self.assertEqual((work / "unrelated.txt").read_text(), "preserve me")
            finally:
                os.chdir(previous)

    def run_publish(self, is_valid, is_empty=False):
        events = []
        settings = {"build_type": "legacy", "source": {"branch": "main", "path": "/"},
                    "html_url": "https://vrcticfox.github.io/ExternalResources/"}

        def verify(*args):
            events.append("verify")
            if not is_valid:
                raise RuntimeError("stale image")

        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "Vrcticfox/ExternalResources"}), \
             patch.object(publish_fvlist, "api", return_value=settings), \
             patch.object(publish_fvlist, "run"), \
             patch.object(Path, "read_text", return_value='{"worlds":[],"worldCount":0,"previewPageCount":0,"detailPageCount":0}' if is_empty else '{"worldCount":1}'), \
             patch.object(publish_fvlist.time, "sleep", side_effect=lambda seconds: events.append("wait")), \
             patch.object(publish_fvlist, "verify_public", side_effect=verify), \
             patch.object(publish_fvlist, "publish", side_effect=lambda paths, message: events.append(message)):
            if is_valid:
                publish_fvlist.main()
            else:
                with self.assertRaises(RuntimeError):
                    publish_fvlist.main()
        return events

    def test_images_then_verification_then_catalog(self):
        self.assertEqual(self.run_publish(True), ["wait", "Update FVList inactive atlas bank", "wait", "verify", "Publish FVList catalog"])

    def test_stale_image_never_promotes_catalog(self):
        self.assertNotIn("Publish FVList catalog", self.run_publish(False))

    def test_all_missing_publishes_empty_catalog_without_images(self):
        self.assertEqual(self.run_publish(True, is_empty=True), ["wait", "Publish FVList catalog"])

    def test_wrong_pages_source_stops_before_writes(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/repo"}), \
             patch.object(publish_fvlist, "api", return_value={"build_type": "workflow"}), \
             patch.object(publish_fvlist, "run") as commands:
            with self.assertRaises(RuntimeError):
                publish_fvlist.main()
            commands.assert_not_called()


if __name__ == "__main__":
    unittest.main()
