from ..core import db, models


def handler():
    from sample.core.db import connect  # lazy import
