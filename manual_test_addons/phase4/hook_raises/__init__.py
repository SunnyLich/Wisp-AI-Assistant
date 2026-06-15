def before_query(prompt, context):
    raise RuntimeError("intentional phase4 hook failure")


def after_response(text):
    raise RuntimeError("intentional phase4 response failure")

