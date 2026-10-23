"""Registry of replay scenarios (datasets) the engine can run."""

import os
from collections.abc import Callable
from dataclasses import dataclass

from engine.replay.plan import SourceLoader


@dataclass(frozen=True)
class Scenario:
    name: str
    version: str
    description: str
    make_loader: Callable[[], SourceLoader]


def _dataco_loader() -> SourceLoader:
    from adapters.dataco import DataCoLoader
    from adapters.dataco.fetch import ensure_dataset

    auto = os.environ.get("DATACO_AUTO_DOWNLOAD", "true").lower() not in ("0", "false", "no")
    return DataCoLoader(ensure_dataset(auto_download=auto))


def _registry() -> dict[str, Scenario]:
    from adapters.dataco.fetch import DATASET_VERSION

    return {
        "dataco": Scenario(
            name="dataco",
            version=f"mendeley-v{DATASET_VERSION}",
            description="DataCo Smart Supply Chain (outbound orders 2015-2018, CC BY 4.0)",
            make_loader=_dataco_loader,
        )
    }


SCENARIOS = _registry()
