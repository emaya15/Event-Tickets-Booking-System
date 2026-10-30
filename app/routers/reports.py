from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth.dependencies import require_admin, require_roles
from app.database import get_db
from app.models import User
from app.services import report_service
from app.utils.constants import Role

router = APIRouter(prefix="/reports", tags=["Reports & Dashboard"])
admin_or_organizer = require_roles(Role.ADMIN, Role.ORGANIZER)


@router.get("/admin-dashboard")
def admin_dashboard(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return report_service.admin_dashboard(db)


@router.get("/event-sales")
def event_sales(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    event_id: Optional[int] = Query(None, description="Limit to one event"),
    user: User = Depends(admin_or_organizer),
    db: Session = Depends(get_db),
):
    """Tickets sold, revenue and refunds per event and show (filtered by SHOW date)."""
    return report_service.event_sales(db, user, from_date, to_date, event_id)


@router.get("/event-sales/export")
def event_sales_export(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    event_id: Optional[int] = None,
    user: User = Depends(admin_or_organizer),
    db: Session = Depends(get_db),
):
    """Excel (.xlsx) download of the event sales report."""
    report = report_service.event_sales(db, user, from_date, to_date, event_id)
    content = report_service.event_sales_excel(report)
    filename = f"event_sales_{report['from_date']}_{report['to_date']}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/occupancy")
def occupancy(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    event_id: Optional[int] = None,
    user: User = Depends(admin_or_organizer),
    db: Session = Depends(get_db),
):
    return report_service.occupancy(db, user, from_date, to_date, event_id)


@router.get("/top-events")
def top_events(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return report_service.top_events(db, from_date, to_date)


@router.get("/refunds")
def refunds(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return report_service.refunds_report(db, from_date, to_date)
