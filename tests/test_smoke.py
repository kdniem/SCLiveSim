from schema import SCHEMA_VERSION


def test_schema_version_is_semver() -> None:
    major, minor, patch = SCHEMA_VERSION.split(".")
    assert all(part.isdigit() for part in (major, minor, patch))
