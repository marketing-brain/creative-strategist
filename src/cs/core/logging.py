import logging

LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    """Configure process-wide logging for an entrypoint.

    httpx and httpcore log every request line, including the full URL, at INFO. Meta
    request URLs carry access tokens and app secrets, so those loggers stay at WARNING.
    """
    logging.basicConfig(level=level, format=LOG_FORMAT)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
