"""Mega 6/45 official-source adapter."""

from vietlott.adapters.number_set import NumberSetAdapter
from vietlott.config import get_game


class Mega645Adapter(NumberSetAdapter):
    def __init__(self) -> None:
        super().__init__(get_game("mega645"))
