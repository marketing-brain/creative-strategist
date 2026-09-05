import logging

from cs.core.logging import configure_logging


def test_configure_logging_silences_http_request_urls():
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    logging.getLogger("httpcore").setLevel(logging.NOTSET)
    configure_logging()
    # httpx logs every request line, URL included, at INFO; Meta URLs carry credentials.
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
    assert not logging.getLogger("httpcore").isEnabledFor(logging.INFO)
    assert logging.getLogger("httpx").isEnabledFor(logging.WARNING)
