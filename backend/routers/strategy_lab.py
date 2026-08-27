"""Tier3 strategy-lab observation API. Read-only compact projection.

Does not run experiments, does not consume holdout, does not emit
StrategyRelease or claimable=true. Frontend contract:

  GET /api/v3/lab/status
  GET /api/v3/lab/packages
  GET /api/v3/lab/experiments
  GET /api/v3/lab/experiments/{family}/{block}
  GET /api/v3/lab/snapshots
  GET /api/v3/lab/release
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services import strategy_lab_serve as lab

router = APIRouter()


@router.get("/overview")
def overview():
    return lab.overview_payload()


@router.get("/status")
def status():
    body = lab.status_payload()
    return lab._envelope(framework=body)


@router.get("/packages")
def packages():
    return lab._envelope(**lab.list_packages())


@router.get("/experiments")
def experiments():
    rows = lab.list_experiments()
    return lab._envelope(experiments=rows, n=len(rows))


@router.get("/experiments/{family}/{block}")
def experiment(family: str, block: str):
    row = lab.get_experiment(family, block)
    if row is None:
        raise HTTPException(status_code=404, detail=f"experiment not found: {family}:{block}")
    return lab._envelope(experiment=row)


@router.get("/snapshots")
def snapshots():
    return lab._envelope(**lab.snapshot_cards())


@router.get("/release")
def release():
    status = lab.status_payload()
    rows = lab.list_experiments()
    snaps = lab.snapshot_cards()
    return lab._envelope(**lab.release_projection(status, rows, snaps))
