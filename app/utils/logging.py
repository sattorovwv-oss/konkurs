import logging


class RedactOAuthQuery(logging.Filter):
    """Uvicorn access logs must not retain single-use authorization codes."""

    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            args = list(record.args)
            if isinstance(args[2], str) and args[2].startswith("/auth/"):
                args[2] = args[2].split("?", 1)[0]
                record.args = tuple(args)
        return True
