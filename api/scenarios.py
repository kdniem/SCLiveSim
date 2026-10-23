"""Scenario endpoints: list, inspect, build and upload scenarios."""

from collections.abc import Iterator
from typing import Annotated, Any

import psycopg
import yaml
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field, ValidationError

from engine.scenarios import get_scenario, list_scenarios, save_scenario
from engine.simulation.builder import BuilderRequest, build_scenario, summarize
from engine.simulation.library import DISRUPTION_PROFILES, INDUSTRIES, REGIONS, SIZES
from engine.simulation.spec import ScenarioSpec

router = APIRouter(tags=["scenarios"])


def _conn(request: Request) -> Iterator[psycopg.Connection]:
    pool: ConnectionPool = request.app.state.pool
    with pool.connection() as conn:
        yield conn


Conn = Annotated[psycopg.Connection, Depends(_conn)]


class ScenarioOut(BaseModel):
    name: str
    version: str
    mode: str = Field(description="replay or simulation")
    origin: str = Field(description="built-in, builder, upload or calibration")
    description: str


class BuilderPreview(BaseModel):
    summary: dict[str, Any]
    spec: dict[str, Any]


@router.get("/scenarios")
def scenarios(conn: Conn) -> list[ScenarioOut]:
    """Scenarios that can be run: dataset replays, templates and your own."""
    return [
        ScenarioOut(name=s.name, version=s.version, mode=s.mode.value, origin=origin,
                    description=s.description)
        for s, origin in list_scenarios(conn)
    ]  # fmt: skip


@router.get("/scenarios/{name}", response_model=None)
def scenario(
    name: str,
    conn: Conn,
    format: Annotated[str, Query(pattern="^(json|yaml)$")] = "json",
) -> dict[str, Any] | PlainTextResponse:
    """The full specification of a simulation scenario (`?format=yaml` to download)."""
    found = get_scenario(conn, name)
    if found is None:
        raise HTTPException(404, f"scenario {name} not found")
    try:
        spec = found.spec()
    except LookupError as exc:
        raise HTTPException(409, f"{name} replays a dataset and has no specification") from exc
    data = spec.model_dump(mode="json", exclude_none=True)
    if format == "yaml":
        text = "# SC LiveSim scenario - licensed under CC BY 4.0\n" + yaml.safe_dump(
            data, sort_keys=False, allow_unicode=True
        )
        return PlainTextResponse(
            text,
            media_type="application/yaml",
            headers={"Content-Disposition": f'attachment; filename="{name}.yaml"'},
        )
    return data


@router.get("/builder/options")
def builder_options() -> dict[str, Any]:
    """Choices for the scenario builder."""
    return {
        "industries": [
            {"name": i.name, "label": i.label, "description": i.description,
             "produces": i.produces, "products": [p.name for p in i.products]}
            for i in INDUSTRIES.values()
        ],
        "regions": [
            {"name": r.name, "label": r.label, "hubs": [c.name for c in r.hubs]}
            for r in REGIONS.values()
        ],
        "sizes": SIZES,
        "disruption_profiles": DISRUPTION_PROFILES,
        "defaults": BuilderRequest.model_validate({}).model_dump(),
    }  # fmt: skip


@router.post("/builder/preview")
def builder_preview(body: BuilderRequest) -> BuilderPreview:
    """Generate a scenario without saving it."""
    spec = build_scenario(body)
    return BuilderPreview(summary=summarize(spec), spec=spec.model_dump(mode="json"))


@router.post("/scenarios", status_code=201)
def create_scenario(body: BuilderRequest, conn: Conn) -> ScenarioOut:
    """Generate a scenario with the builder and save it, ready for `POST /runs`."""
    spec = build_scenario(body)
    try:
        save_scenario(conn, spec, "builder", body.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return ScenarioOut(name=spec.name, version=spec.version, mode="simulation",
                       origin="builder", description=spec.description)  # fmt: skip


@router.post("/scenarios/upload", status_code=201)
def upload_scenario(
    conn: Conn,
    text: Annotated[str, Body(media_type="application/yaml")],
) -> ScenarioOut:
    """Upload your own scenario as YAML (same format as scenarios/*/scenario.yaml)."""
    try:
        data = yaml.safe_load(text)
        spec = ScenarioSpec.model_validate(data)
    except (yaml.YAMLError, ValidationError) as exc:
        raise HTTPException(422, f"invalid scenario: {exc}") from exc
    try:
        save_scenario(conn, spec, "upload")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return ScenarioOut(name=spec.name, version=spec.version, mode="simulation",
                       origin="upload", description=spec.description)  # fmt: skip
