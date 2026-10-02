import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import review


class ReviewSmoke(unittest.TestCase):
    def test_versioned_review_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "review"
            video = root / "sample.mp4"
            video.write_bytes(b"synthetic fixture")
            review.initialize(project, "Example")
            self.assertEqual(review.add(project, video, "First cut"), 1)
            item = review.comment(project, 1, 1.25, 1.75, "Tighten this pause")
            self.assertEqual(item["status"], "open")
            review.set_status(project, item["id"], "addressed", "Trimmed")
            self.assertEqual(review.add(project, video, "Second cut"), 2)
            state = review.load(project)
            self.assertEqual([v["number"] for v in state["versions"]], [1, 2])
            self.assertEqual(state["comments"][0]["version"], 1)
            self.assertEqual(state["comments"][0]["note"], "Trimmed")
            self.assertEqual((project / "media" / "v1.mp4").read_bytes(), video.read_bytes())

    def test_invalid_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "review"
            review.initialize(project, "Example")
            with self.assertRaises(ValueError):
                review.comment(project, 1, 0, 1, "No version")
            with self.assertRaises(ValueError):
                review.set_status(project, "unknown", "addressed", "")
            with self.assertRaises(ValueError):
                review.initialize(project, "Again")

    def test_edl_uses_argument_list_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            source.write_bytes(b"untouched")
            edl = root / "edl.json"
            edl.write_text(json.dumps({"keep": [[0, 1.5], [2.5, 4]]}))
            output = root / "out.mp4"
            with patch.object(review.shutil, "which", return_value="/usr/bin/ffmpeg"):
                with patch.object(review.subprocess, "run") as run:
                    review.render(source, edl, output)
            args = run.call_args.args[0]
            self.assertEqual(args[0], "ffmpeg")
            self.assertIn("concat=n=2:v=1:a=1", " ".join(args))
            self.assertEqual(source.read_bytes(), b"untouched")


if __name__ == "__main__":
    unittest.main()
