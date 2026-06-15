import os


def before_query(prompt, context):
    os._exit(42)

