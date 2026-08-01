"""Max 3D and Max 3D+ official-source adapter."""

from vietlott.adapters.three_digit import ThreeDigitAdapter
from vietlott.config import get_game


class Max3DAdapter(ThreeDigitAdapter):
    def __init__(self) -> None:
        super().__init__(get_game("max3d"))
