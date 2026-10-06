from unittest.mock import patch

import pytest

from vietlott.cli import main
from vietlott.errors import FetchError, ParseError, TemporaryFetchError, ValidationError


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (TemporaryFetchError("Transient HTTP 429"), 3),
        (FetchError("HTTP 403"), 1),
        (ParseError("Invalid source"), 1),
        (ValidationError("Invalid result"), 1),
    ],
)
def test_collection_distinguishes_temporary_and_permanent_errors(
    error: Exception, expected_code: int
) -> None:
    with patch("vietlott.cli.Collector.run_scheduled", side_effect=error):
        assert main(["scheduled"]) == expected_code
