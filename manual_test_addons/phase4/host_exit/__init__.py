"""Package marker and exports for manual test addons phase4 host exit."""

import os


def before_query(prompt, context):
    """Support package marker and exports for manual test addons phase4 host exit for before query."""
    os._exit(42)

