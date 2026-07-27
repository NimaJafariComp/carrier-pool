"""Local Texas Triangle ZIP lookup contracts."""

from decimal import Decimal

from carrier_pool.geography.lookup import GeographyLookup, GeographyQualityFlag


def test_lookup_normalizes_dfw_zip_plus_four_and_suburb_city() -> None:
    result = GeographyLookup.default().lookup(" 75050-1234 ", " grand   prairie ", "tx")

    assert result.postal_code == "75050"
    assert result.city == "GRAND PRAIRIE"
    assert result.state == "TX"
    assert result.latitude == Decimal("32.745964")
    assert result.metro_group == "DFW"
    assert result.quality_flags == ()


def test_lookup_returns_houston_and_san_antonio_reference_rows() -> None:
    lookup = GeographyLookup.default()

    houston = lookup.lookup("77449", "Katy", "TX")
    san_antonio = lookup.lookup("78205", "San Antonio", "TX")

    assert (houston.metro_group, houston.postal_code) == ("HOUSTON", "77449")
    assert (san_antonio.metro_group, san_antonio.postal_code) == ("SAN_ANTONIO", "78205")
    assert houston.latitude is not None
    assert san_antonio.longitude is not None


def test_lookup_returns_quality_flags_for_missing_invalid_and_unknown_zips() -> None:
    lookup = GeographyLookup.default()

    missing = lookup.lookup(None, "Dallas", "TX")
    invalid = lookup.lookup("75A01", "Dallas", "TX")
    unknown = lookup.lookup("79901", "El Paso", "TX")

    assert missing.quality_flags == (GeographyQualityFlag.MISSING_ZIP,)
    assert invalid.quality_flags == (GeographyQualityFlag.INVALID_ZIP_FORMAT,)
    assert unknown.quality_flags == (GeographyQualityFlag.ZIP_NOT_IN_REFERENCE,)
    assert all(result.latitude is None for result in (missing, invalid, unknown))
