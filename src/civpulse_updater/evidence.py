import os
import tempfile
from pathlib import Path

from .models import digest


class EvidenceStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def path(self, sha: str) -> Path:
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("Invalid evidence digest")
        return self.root / sha[:2] / sha

    def put(self, content: bytes) -> str:
        sha = digest(content)
        target = self.path(sha)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self.read(sha)
            return sha
        fd, temporary = tempfile.mkstemp(dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return sha

    def read(self, sha: str) -> bytes:
        content = self.path(sha).read_bytes()
        if digest(content) != sha:
            raise ValueError("Evidence checksum mismatch")
        return content
