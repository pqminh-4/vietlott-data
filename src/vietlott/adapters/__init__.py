"""Official Vietlott product adapters."""

from collections.abc import Callable

from vietlott.adapters.base import BaseAdapter
from vietlott.adapters.lotto535 import Lotto535Adapter
from vietlott.adapters.max3d import Max3DAdapter
from vietlott.adapters.max3d_pro import Max3DProAdapter
from vietlott.adapters.mega645 import Mega645Adapter
from vietlott.adapters.power655 import Power655Adapter


def get_adapter(game: str) -> BaseAdapter:
    adapters: dict[str, Callable[[], BaseAdapter]] = {
        "mega645": Mega645Adapter,
        "power655": Power655Adapter,
        "lotto535": Lotto535Adapter,
        "max3d": Max3DAdapter,
        "max3d_pro": Max3DProAdapter,
    }
    try:
        return adapters[game]()
    except KeyError as exc:
        raise ValueError(f"Unknown game: {game}") from exc


__all__ = [
    "BaseAdapter",
    "Lotto535Adapter",
    "Max3DAdapter",
    "Max3DProAdapter",
    "Mega645Adapter",
    "Power655Adapter",
    "get_adapter",
]
