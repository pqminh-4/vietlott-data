"""Domain-specific failures."""


class VietlottError(Exception):
    """Base exception for controlled collector failures."""


class FetchError(VietlottError):
    """The official source could not be fetched safely."""


class TemporaryFetchError(FetchError):
    """Nguồn chính thức tạm thời không truy cập được; có thể thử lại."""


class ParseError(VietlottError):
    """The official response did not match a supported structure."""


class ValidationError(VietlottError):
    """A normalized record violated the public schema."""


class FreshnessError(VietlottError):
    """A freshness check could not be configured safely."""
