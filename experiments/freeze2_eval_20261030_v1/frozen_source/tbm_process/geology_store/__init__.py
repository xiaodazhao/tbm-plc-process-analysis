"""Read-only chainage/time queries over the existing geological evidence."""

VERSION = "tbm_geology_store_v1_20261027"

from .database import GeologyStore, import_catalog

__all__ = ["GeologyStore", "import_catalog", "VERSION"]
