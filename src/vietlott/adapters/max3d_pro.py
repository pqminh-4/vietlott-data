"""Max 3D Pro official-source adapter."""

from vietlott.adapters.three_digit import ThreeDigitAdapter
from vietlott.config import get_game


class Max3DProAdapter(ThreeDigitAdapter):
    def __init__(self) -> None:
        super().__init__(get_game("max3d_pro"))
