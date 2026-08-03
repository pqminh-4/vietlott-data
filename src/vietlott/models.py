"""Stable v1 data model and validation rules."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from typing import Any, Literal
from urllib.parse import urlparse

from vietlott.config import GAMES, OFFICIAL_HOSTS, get_game
from vietlott.errors import ValidationError

SCHEMA_VERSION = "1.0"
DRAW_ID_RE = re.compile(r"^\d{5,10}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
THREE_DIGIT_RE = re.compile(r"^\d{3}$")


@dataclass(slots=True)
class Prize:
    code: str
    name: str
    amount_vnd: int | None = None
    winner_count: int | None = None
    jackpot_vnd: int | None = None

    def validate(self) -> None:
        if not self.code or not self.name:
            raise ValidationError("Prize code and name are required")
        for field_name in ("amount_vnd", "winner_count", "jackpot_vnd"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValidationError(f"Prize {field_name} cannot be negative")


@dataclass(slots=True)
class NumberSetResult:
    kind: Literal["number_set"] = "number_set"
    main_numbers: list[int] = field(default_factory=list)
    bonus_numbers: list[int] = field(default_factory=list)


@dataclass(slots=True)
class ThreeDigitTier:
    code: str
    name: str
    numbers: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ThreeDigitResult:
    kind: Literal["three_digit_tiers"] = "three_digit_tiers"
    tiers: list[ThreeDigitTier] = field(default_factory=list)


Result = NumberSetResult | ThreeDigitResult


@dataclass(slots=True)
class DrawRecord:
    game: str
    draw_id: str
    draw_date: str
    result: Result
    source_url: str
    source_sha256: str
    retrieved_at: str
    draw_time: str | None = None
    draw_slot: str | None = None
    prizes: list[Prize] = field(default_factory=list)
    source_pdf_url: str | None = None
    source_pdf_sha256: str | None = None
    schema_version: str = SCHEMA_VERSION

    @property
    def key(self) -> tuple[str, str]:
        return self.game, self.draw_id

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError(f"Unsupported schema version: {self.schema_version}")
        if self.game not in GAMES:
            raise ValidationError(f"Unknown game: {self.game}")
        if not DRAW_ID_RE.fullmatch(self.draw_id):
            raise ValidationError(f"Invalid draw_id: {self.draw_id!r}")
        try:
            date.fromisoformat(self.draw_date)
        except ValueError as exc:
            raise ValidationError(f"Invalid draw_date: {self.draw_date!r}") from exc
        if (self.draw_time is None) != (self.draw_slot is None):
            raise ValidationError("draw_time and draw_slot must be populated together")
        if self.draw_time is not None:
            try:
                parsed_time = time.fromisoformat(self.draw_time)
            except ValueError as exc:
                raise ValidationError(f"Invalid draw_time: {self.draw_time!r}") from exc
            spec = get_game(self.game)
            if (
                parsed_time.hour not in spec.draw_hours
                or parsed_time.minute
                or parsed_time.second
                or parsed_time.microsecond
            ):
                raise ValidationError(f"Off-schedule draw_time for {self.game}: {self.draw_time}")
            expected_slot = {
                13: "afternoon",
                18: "evening",
                21: "night",
            }.get(parsed_time.hour, f"hour-{parsed_time.hour:02d}")
            if self.draw_slot != expected_slot:
                raise ValidationError("draw_time and draw_slot do not agree")
        try:
            retrieved_at = datetime.fromisoformat(self.retrieved_at)
        except ValueError as exc:
            raise ValidationError(f"Invalid retrieved_at: {self.retrieved_at!r}") from exc
        if retrieved_at.tzinfo is None:
            raise ValidationError("retrieved_at must include a UTC offset")
        self._validate_official_url(self.source_url)
        if not SHA256_RE.fullmatch(self.source_sha256):
            raise ValidationError("source_sha256 must be a lowercase SHA-256 digest")
        if self.source_pdf_url is not None:
            self._validate_official_url(self.source_pdf_url)
        if self.source_pdf_sha256 is not None and not SHA256_RE.fullmatch(self.source_pdf_sha256):
            raise ValidationError("source_pdf_sha256 must be a lowercase SHA-256 digest")
        if self.source_pdf_sha256 is not None and self.source_pdf_url is None:
            raise ValidationError("source_pdf_sha256 requires source_pdf_url")
        prize_codes = [prize.code for prize in self.prizes]
        if len(prize_codes) != len(set(prize_codes)):
            raise ValidationError("Prize codes must be unique within a draw")
        for prize in self.prizes:
            prize.validate()
        spec = get_game(self.game)
        if spec.kind == "number_set":
            self._validate_number_set()
        else:
            self._validate_three_digit()

    @staticmethod
    def _validate_official_url(value: str) -> None:
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname not in OFFICIAL_HOSTS:
            raise ValidationError(f"Source is not an approved Vietlott HTTPS URL: {value}")

    def _validate_number_set(self) -> None:
        if not isinstance(self.result, NumberSetResult):
            raise ValidationError(f"{self.game} requires a number_set result")
        spec = get_game(self.game)
        main = self.result.main_numbers
        bonus = self.result.bonus_numbers
        if len(main) != spec.main_count or len(bonus) != spec.bonus_count:
            raise ValidationError(
                f"{self.game} expects {spec.main_count}+{spec.bonus_count} numbers, "
                f"got {len(main)}+{len(bonus)}"
            )
        if len(set(main)) != len(main):
            raise ValidationError(f"{self.game} main numbers must be unique")
        if not all(spec.main_min <= value <= spec.main_max for value in main):  # type: ignore[operator]
            raise ValidationError(f"{self.game} main number is outside the configured range")
        if not all(spec.bonus_min <= value <= spec.bonus_max for value in bonus):  # type: ignore[operator]
            raise ValidationError(f"{self.game} bonus number is outside the configured range")
        if self.game == "power655" and set(main).intersection(bonus):
            raise ValidationError("Power 6/55 bonus number cannot repeat a main number")

    def _validate_three_digit(self) -> None:
        if not isinstance(self.result, ThreeDigitResult):
            raise ValidationError(f"{self.game} requires a three_digit_tiers result")
        if not self.result.tiers:
            raise ValidationError(f"{self.game} requires at least one result tier")
        seen_codes: set[str] = set()
        for tier in self.result.tiers:
            if not tier.code or tier.code in seen_codes:
                raise ValidationError("Three-digit result tier codes must be unique")
            seen_codes.add(tier.code)
            if not tier.numbers or not all(THREE_DIGIT_RE.fullmatch(x) for x in tier.numbers):
                raise ValidationError("Max 3D results must be three-character digit strings")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DrawRecord:
        result_payload = dict(payload["result"])
        kind = result_payload.get("kind")
        if kind == "number_set":
            result: Result = NumberSetResult(
                main_numbers=[int(x) for x in result_payload.get("main_numbers", [])],
                bonus_numbers=[int(x) for x in result_payload.get("bonus_numbers", [])],
            )
        elif kind == "three_digit_tiers":
            result = ThreeDigitResult(
                tiers=[ThreeDigitTier(**tier) for tier in result_payload.get("tiers", [])]
            )
        else:
            raise ValidationError(f"Unknown result kind: {kind!r}")
        record = cls(
            schema_version=str(payload.get("schema_version", SCHEMA_VERSION)),
            game=str(payload["game"]),
            draw_id=str(payload["draw_id"]),
            draw_date=str(payload["draw_date"]),
            draw_time=payload.get("draw_time"),
            draw_slot=payload.get("draw_slot"),
            result=result,
            prizes=[Prize(**prize) for prize in payload.get("prizes", [])],
            source_url=str(payload["source_url"]),
            source_sha256=str(payload["source_sha256"]),
            source_pdf_url=payload.get("source_pdf_url"),
            source_pdf_sha256=payload.get("source_pdf_sha256"),
            retrieved_at=str(payload["retrieved_at"]),
        )
        record.validate()
        return record
