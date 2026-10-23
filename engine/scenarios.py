"""Registry of scenarios the engine can run: replayed datasets and simulations.

Simulation scenarios are discovered from `scenarios/*/scenario.yaml`
(override the folder with LIVESIM_SCENARIOS_DIR).
"""

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from engine.replay.plan import SourceLoader
from schema.enums import RunMode

if TYPE_CHECKING:
    from engine.simulation.spec import ScenarioSpec

log = logging.getLogger("sc_livesim.engine")


@dataclass(frozen=True)
class Scenario:
    name: str
    version: str
    mode: RunMode
    description: str
    make_loader: Callable[[], SourceLoader]
    spec: Callable[[], "ScenarioSpec"] = lambda: _no_spec()

    @property
    def supports_disruptions(self) -> bool:
        return self.mode is RunMode.SIMULATION


def _no_spec() -> "ScenarioSpec":
    raise LookupError("this scenario has no simulation spec")


def scenarios_dir() -> Path:
    default = Path(__file__).resolve().parents[1] / "scenarios"
    return Path(os.environ.get("LIVESIM_SCENARIOS_DIR", default))


def _dataco_loader() -> SourceLoader:
    from adapters.dataco import DataCoLoader
    from adapters.dataco.fetch import ensure_dataset

    auto = os.environ.get("DATACO_AUTO_DOWNLOAD", "true").lower() not in ("0", "false", "no")
    return DataCoLoader(ensure_dataset(auto_download=auto))


def _simulation_scenarios(root: Path) -> dict[str, Scenario]:
    from engine.simulation.model import SimulationLoader
    from engine.simulation.spec import ScenarioSpec, load_scenario

    found: dict[str, Scenario] = {}
    for path in sorted(root.glob("*/scenario.yaml")):
        try:
            spec = load_scenario(path)
        except Exception:
            log.exception("invalid scenario %s, skipped", path)
            continue

        def make(spec: ScenarioSpec = spec) -> SourceLoader:
            return SimulationLoader(spec)

        def get_spec(spec: ScenarioSpec = spec) -> ScenarioSpec:
            return spec

        found[spec.name] = Scenario(
            spec.name, spec.version, RunMode.SIMULATION, spec.description.strip(), make, get_spec
        )
    return found


def _registry() -> dict[str, Scenario]:
    from adapters.dataco.fetch import DATASET_VERSION

    registry = {
        "dataco": Scenario(
            name="dataco",
            version=f"mendeley-v{DATASET_VERSION}",
            mode=RunMode.REPLAY,
            description="DataCo Smart Supply Chain (outbound orders 2015-2018, CC BY 4.0)",
            make_loader=_dataco_loader,
        )
    }
    registry.update(_simulation_scenarios(scenarios_dir()))
    return registry


SCENARIOS = _registry()
