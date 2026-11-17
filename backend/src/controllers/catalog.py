from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from src.config.database import get_db
from src.middlewares.auth import current_user
from src.models import Dataset, Job, Report, Audit, AcceptanceCase
from src.services.business import metadata
from src.constants.acceptance import MAX_TOTAL_ALPHA, MAX_PHASES, MAX_PHASE_SIZE
router = APIRouter()


@router.get('/meta')
def meta(): return metadata()


@router.get('/acceptance/config')
def acceptance_config():
    return {'max_total_alpha': MAX_TOTAL_ALPHA, 'max_phases': MAX_PHASES, 'max_phase_size': MAX_PHASE_SIZE}


@router.get('/overview')
def overview(db=Depends(get_db), user=Depends(current_user)):
    cases = db.scalar(select(func.count()).select_from(AcceptanceCase))
    passed = db.scalar(select(func.count()).select_from(AcceptanceCase).where(AcceptanceCase.result == 'passed'))
    open_cases = db.scalar(select(func.count()).select_from(AcceptanceCase).where(AcceptanceCase.status == 'open'))
    return {
        'datasets': db.scalar(select(func.count()).select_from(Dataset)),
        'records': sum(len(d.records) for d in db.scalars(select(Dataset))),
        'jobs': db.scalar(select(func.count()).select_from(Job)),
        'approved': db.scalar(select(func.count()).select_from(Job).where(Job.approved == True)),
        'reports': db.scalar(select(func.count()).select_from(Report)),
        'acceptance_cases': cases, 'acceptance_passed': passed, 'acceptance_open': open_cases,
    }


@router.get('/audits')
def audits(db=Depends(get_db), user=Depends(current_user)):
    return [{'id': a.id, 'actor': a.actor, 'action': a.action, 'detail': a.detail, 'at': a.at} for a in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(50))]
