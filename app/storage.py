"""Постоянное хранилище файлов и массивов. M1 — каталог DATA_DIR, с M2 — S3.

Ключи — относительные пути вида `files/<id>`, `values/<id>.f32`. Запись атомарная:
во временный файл рядом и `os.replace`, поэтому читатель видит старое или новое целиком.
"""

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from app.config import Settings


class LocalStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError(f"ключ вне хранилища: {key}")
        return path

    def exists(self, key: str) -> bool:
        return self.path(key).exists()

    @contextmanager
    def writer(self, key: str) -> Iterator[BinaryIO]:
        """Атомарная запись: файл появляется под ключом только после успешного выхода."""
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                yield fh
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def write_bytes(self, key: str, data: bytes) -> None:
        with self.writer(key) as fh:
            fh.write(data)

    def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)


def get_storage(settings: Settings) -> LocalStorage:
    return LocalStorage(settings.data_dir)
