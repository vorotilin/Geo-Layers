"""Манифест импорта (design §4). Неизвестные поля игнорируются (common-requirements §2).

M1: только `kind=grid`, `geometry.type=geojson`, прямоугольные ячейки.
"""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SLUG = r"^[a-z0-9][a-z0-9_]{0,62}$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class DatasetRef(_Model):
    provider: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)


class Geometry(_Model):
    # M2: wkt, center_size, h3, quadkey, latlng.
    type: Literal["geojson"]


class Metric(_Model):
    column: str = Field(min_length=1, max_length=200)
    slug: str = Field(pattern=SLUG)
    title: str = Field(min_length=1, max_length=200)
    unit: str | None = Field(default=None, max_length=50)
    value_kind: Literal["intensive", "extensive"]


class Options(_Model):
    max_error_rate: float = Field(default=0.01, ge=0, le=1)


class Manifest(_Model):
    kind: Literal["grid"]
    dataset: DatasetRef
    period: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    geometry: Geometry
    # Если в файле нет своей CRS. GeoJSON без `crs` — долгота/широта WGS84.
    crs: str | None = Field(default=None, max_length=100)
    key: str = Field(min_length=1, max_length=200)
    metrics: list[Metric] = Field(min_length=1, max_length=100)
    options: Options = Options()

    @field_validator("metrics")
    @classmethod
    def _unique_slugs(cls, metrics: list[Metric]) -> list[Metric]:
        slugs = [m.slug for m in metrics]
        if len(set(slugs)) != len(slugs):
            raise ValueError("slug метрик повторяются")
        return metrics

    def normalized_hash(self) -> bytes:
        """Хэш нормализованного манифеста для дедупликации импорта (ADR-0009)."""
        body = json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(body.encode("utf-8")).digest()
