import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from artifact_io import save_text

class AtomicArtifacts(unittest.TestCase):
    def test_competing_commits_keep_one_complete_unicode_version(self):
        with tempfile.TemporaryDirectory()as tmp:
            dest=Path(tmp)/'中文.json';texts=['甲'*20000,'乙'*20000]
            def commit(t):
                try:save_text(dest,t);return True
                except FileExistsError:return False
            with ThreadPoolExecutor(2)as pool:results=list(pool.map(commit,texts))
            self.assertEqual(sum(results),1);self.assertIn(dest.read_text(encoding='utf8'),texts)
            self.assertEqual(list(Path(tmp).iterdir()),[dest])
    def test_publication_failure_leaves_no_partial_checkpoint(self):
        with tempfile.TemporaryDirectory()as tmp:
            dest=Path(tmp)/'checkpoint.json'
            with patch('artifact_io.os.link',side_effect=OSError('unsupported')):
                with self.assertRaises(OSError):save_text(dest,'完整内容')
            self.assertFalse(dest.exists());self.assertFalse(list(Path(tmp).iterdir()))
    def test_existing_file_never_overwritten(self):
        with tempfile.TemporaryDirectory()as tmp:
            dest=Path(tmp)/'checkpoint.json';dest.write_bytes(b'previous')
            with self.assertRaises(FileExistsError):save_text(dest,'replacement')
            self.assertEqual(dest.read_bytes(),b'previous')

if __name__=='__main__':unittest.main()
