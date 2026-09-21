"""CDN에서 다른 버전이 내려오면 전환 검증이 실패하는지 확인한다."""

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_public import verify_public


class PublicVerificationTests(unittest.TestCase):
    def test_old_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "FVDetailA_00.jpg").write_bytes(b"new-image")
            with patch("verify_public.urllib.request.urlopen", return_value=io.BytesIO(b"old-image")):
                with self.assertRaises(RuntimeError):
                    verify_public(directory, "https://example.github.io/data", attempts=1)

    def test_matching_image_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "FVPreviewA_00.jpg").write_bytes(b"same-image")
            with patch("verify_public.urllib.request.urlopen", return_value=io.BytesIO(b"same-image")):
                verify_public(directory, "https://example.github.io/data", attempts=1)


if __name__ == "__main__":
    unittest.main()
