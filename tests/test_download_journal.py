"""Offline regression tests for the crash-safe download journal."""
import tempfile
import unittest
from pathlib import Path

from zotify.download_journal import DownloadJournal


class DownloadJournalTests(unittest.TestCase):
    def test_completed_paths_excludes_unfinished_and_pathless_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = DownloadJournal(temp_dir)
            for state in ('complete', 'complete_untagged', 'tags_pending',
                          'audio_verified', 'downloading', 'failed'):
                journal.set_state('track:' + state, state, final_path=Path(temp_dir) / state)
            journal.set_state('track:pathless', 'complete')
            self.assertEqual(set(journal.completed_paths()),
                             {'track:complete', 'track:complete_untagged'})

    def test_state_survives_reopening_journal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            journal = DownloadJournal(root)
            stage = root / ".track.stage.mp3"
            final = root / "track.mp3"
            journal.set_state("spotify:track:one", "audio_verified", stage, final)

            reopened = DownloadJournal(root)
            state = reopened.get("spotify:track:one")

            self.assertEqual(state["state"], "audio_verified")
            self.assertEqual(state["stage_path"], str(stage))
            self.assertEqual(state["final_path"], str(final))

    def test_tag_retry_count_is_bounded_and_can_be_reset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = DownloadJournal(temp_dir)
            uri = "spotify:track:two"
            journal.set_state(uri, "tags_pending")

            self.assertEqual(journal.increment_tag_attempts(uri), 1)
            self.assertEqual(journal.increment_tag_attempts(uri), 2)
            self.assertEqual(journal.get(uri)["tag_attempts"], 2)

            journal.reset_tag_attempts(uri)
            self.assertEqual(journal.get(uri)["tag_attempts"], 0)

    def test_state_update_clears_stale_error_and_stage_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = DownloadJournal(temp_dir)
            uri = "spotify:track:three"
            journal.set_state(uri, "failed", Path("partial.tmp"), error="connection lost")
            journal.set_state(uri, "downloading")

            state = journal.get(uri)
            self.assertEqual(state["state"], "downloading")
            self.assertIsNone(state["stage_path"])
            self.assertIsNone(state["error"])


if __name__ == "__main__":
    unittest.main()
