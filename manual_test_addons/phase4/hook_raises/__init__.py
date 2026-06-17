"""Package marker and exports for manual test addons phase4 hook raises."""

def before_query(prompt, context):
    """Support package marker and exports for manual test addons phase4 hook raises for before query."""
    raise RuntimeError("intentional phase4 hook failure")


def after_response(text):
    """Support package marker and exports for manual test addons phase4 hook raises for after response."""
    raise RuntimeError("intentional phase4 response failure")

