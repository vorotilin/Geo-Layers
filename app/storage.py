"""Постоянное хранилище файлов и массивов.

`LocalStorage` — каталог DATA_DIR (M1). `S3Storage` — S3 (MinIO локально) + локальное
зеркало в CACHE_DIR для mmap (ADR-0004). Ключи — относительные пути вида `files/<id>`,
`values/<id>.f32`. Запись атомарная.
"""

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

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


def get_storage(settings: Settings):
    """S3Storage, если задан S3_BUCKET, иначе LocalStorage (M1/локальная разработка)."""
    if settings.s3_bucket:
        return S3Storage(
            settings.s3_bucket,
            settings.s3_endpoint,
            settings.s3_access_key,
            settings.s3_secret_key,
            settings.s3_region,
            settings.cache_dir,
        )
    return LocalStorage(settings.data_dir)


def ensure_s3_bucket(settings: Settings) -> None:
    """Создаёт бакет, если его нет (для `docker compose up` с MinIO)."""
    if not settings.s3_bucket:
        return
    import boto3
    from botocore.exceptions import ClientError

    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        client.create_bucket(Bucket=settings.s3_bucket)


class S3Storage:
    """S3 + локальное зеркало (CACHE_DIR) для mmap. `path()` отдаёт локальный путь."""

    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None,
        access_key: str | None,
        secret_key: str | None,
        region: str | None,
        cache_root: str | Path,
    ) -> None:
        import boto3

        self.bucket = bucket
        self.cache_root = Path(cache_root)
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    def _local(self, key: str) -> Path:
        path = (self.cache_root / key).resolve()
        if self.cache_root.resolve() not in path.parents:
            raise ValueError(f"ключ вне хранилища: {key}")
        return path

    def _download(self, key: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = local.with_name(local.name + ".tmp-" + uuid4().hex)
        self._client.download_file(self.bucket, key, str(tmp))
        os.replace(tmp, local)

    def path(self, key: str) -> Path:
        local = self._local(key)
        if not local.exists():
            self._download(key, local)
        return local

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        if self._local(key).exists():
            return True
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    @contextmanager
    def writer(self, key: str) -> Iterator[BinaryIO]:
        local = self._local(key)
        local.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=local.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                yield fh
            self._client.upload_file(str(tmp), self.bucket, key)
            os.replace(tmp, local)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def write_bytes(self, key: str, data: bytes) -> None:
        with self.writer(key) as fh:
            fh.write(data)

    def delete(self, key: str) -> None:
        from contextlib import suppress

        from botocore.exceptions import ClientError

        self._local(key).unlink(missing_ok=True)
        with suppress(ClientError):
            self._client.delete_object(Bucket=self.bucket, Key=key)
