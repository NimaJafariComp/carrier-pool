"""Offline Texas Triangle ZIP normalization and centroid lookup."""

import csv
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from importlib.resources import files


class GeographyQualityFlag(StrEnum):
    """Explicit quality outcomes; lookup never guesses a location."""

    MISSING_ZIP = "MISSING_ZIP"
    INVALID_ZIP_FORMAT = "INVALID_ZIP_FORMAT"
    ZIP_NOT_IN_REFERENCE = "ZIP_NOT_IN_REFERENCE"
    INVALID_STATE = "INVALID_STATE"
    STATE_ZIP_MISMATCH = "STATE_ZIP_MISMATCH"
    MISSING_CITY = "MISSING_CITY"


@dataclass(frozen=True, slots=True)
class GeographyResult:
    """Normalized stop geography and any non-fatal reference-data condition."""

    postal_code: str | None
    city: str | None
    state: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    metro_group: str | None
    quality_flags: tuple[GeographyQualityFlag, ...]


@dataclass(frozen=True, slots=True)
class _ReferenceRow:
    postal_code: str
    city: str
    state: str
    latitude: Decimal
    longitude: Decimal
    metro_group: str


class GeographyLookup:
    """Read-only lookup over packaged data; no network client exists here."""

    def __init__(self, rows: dict[str, _ReferenceRow]) -> None:
        self._rows = rows

    @classmethod
    def default(cls) -> "GeographyLookup":
        return cls(_default_rows())

    def lookup(
        self, postal_code: str | None, city: str | None, state: str | None
    ) -> GeographyResult:
        normalized_zip, zip_flag = _normalize_zip(postal_code)
        normalized_city, city_flag = _normalize_city(city)
        normalized_state, state_flag = _normalize_state(state)
        flags = tuple(flag for flag in (zip_flag, city_flag, state_flag) if flag is not None)
        if normalized_zip is None:
            return GeographyResult(
                normalized_zip, normalized_city, normalized_state, None, None, None, flags
            )
        row = self._rows.get(normalized_zip)
        if row is None:
            return GeographyResult(
                normalized_zip,
                normalized_city,
                normalized_state,
                None,
                None,
                None,
                (*flags, GeographyQualityFlag.ZIP_NOT_IN_REFERENCE),
            )
        if normalized_state != row.state:
            return GeographyResult(
                normalized_zip,
                normalized_city,
                normalized_state,
                None,
                None,
                None,
                (*flags, GeographyQualityFlag.STATE_ZIP_MISMATCH),
            )
        return GeographyResult(
            normalized_zip,
            normalized_city,
            normalized_state,
            row.latitude,
            row.longitude,
            row.metro_group,
            flags,
        )


@lru_cache(maxsize=1)
def _default_rows() -> dict[str, _ReferenceRow]:
    path = files("carrier_pool.geography.data").joinpath("tx_triangle_zip_centroids.csv")
    with path.open("r", encoding="utf-8") as handle:
        return {
            row["zip"]: _ReferenceRow(
                postal_code=row["zip"],
                city=row["city"],
                state=row["state"],
                latitude=Decimal(row["latitude"]),
                longitude=Decimal(row["longitude"]),
                metro_group=row["metro_group"],
            )
            for row in csv.DictReader(handle)
        }


def _normalize_zip(value: str | None) -> tuple[str | None, GeographyQualityFlag | None]:
    if value is None or not value.strip():
        return None, GeographyQualityFlag.MISSING_ZIP
    normalized = value.strip()
    if len(normalized) == 10 and normalized[5] == "-":
        normalized = normalized[:5]
    if len(normalized) != 5 or not normalized.isascii() or not normalized.isdigit():
        return None, GeographyQualityFlag.INVALID_ZIP_FORMAT
    return normalized, None


def _normalize_city(value: str | None) -> tuple[str | None, GeographyQualityFlag | None]:
    if value is None or not value.strip():
        return None, GeographyQualityFlag.MISSING_CITY
    return " ".join(value.split()).upper(), None


def _normalize_state(value: str | None) -> tuple[str | None, GeographyQualityFlag | None]:
    if value is None or not value.strip():
        return None, GeographyQualityFlag.INVALID_STATE
    normalized = value.strip().upper()
    if len(normalized) != 2 or not normalized.isascii() or not normalized.isalpha():
        return None, GeographyQualityFlag.INVALID_STATE
    return normalized, None
