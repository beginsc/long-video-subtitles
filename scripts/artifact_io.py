"""Commit a complete UTF-8 artifact exclusively; never overwrite a checkpoint."""
import os
import tempfile
from pathlib import Path


def save_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Same-directory staging keeps link publication on one filesystem. A crash
    # before publication leaves only a .pending file, not a partial checkpoint.
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.pending', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        # Unlike replace(), hard-link creation fails if the destination exists,
        # including a concurrent writer. Unsupported filesystems fail closed.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
