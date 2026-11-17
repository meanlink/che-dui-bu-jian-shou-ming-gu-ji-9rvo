from fastapi import APIRouter, Depends
from src.config.database import get_db
from src.middlewares.auth import current_user, can_write, admin_only
from src.schemas.acceptance import AcceptanceInput, ReportInput
from src.services import acceptance

router = APIRouter(prefix='/acceptance')


@router.get('/cases')
def listing(view: str = 'all', db=Depends(get_db), u=Depends(current_user)):
    return acceptance.listing(db, view)


@router.post('/cases', status_code=201)
def create(data: AcceptanceInput, db=Depends(get_db), u=Depends(can_write)):
    case = acceptance.open_case(db, data, u)
    db.commit()
    return acceptance.detail(db, case.id)


@router.get('/cases/{id}')
def detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    return acceptance.detail(db, id)


@router.post('/cases/{id}/reports', status_code=201)
def report(id: int, data: ReportInput, db=Depends(get_db), u=Depends(can_write)):
    return acceptance.submit_report(db, id, data, u)


@router.post('/cases/{id}/approve')
def approve(id: int, db=Depends(get_db), u=Depends(admin_only)):
    acceptance.approve(db, id, u)
    return acceptance.detail(db, id)
