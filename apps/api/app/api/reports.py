from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import CashSession
from app.schemas.daily_reports import CurrentReportOut, DailyReportListItem, DailyReportOut, MonthlyReportOut
from app.schemas.reports import DashboardReport
from app.services.daily_reports import (current_report, generate_daily_report, get_daily_report,
                                        get_latest_daily_report, list_daily_reports,
                                        monthly_report)
from app.services.reporting import dashboard_report

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/dashboard", response_model=DashboardReport)
def dashboard(preset: str = Query(default="today", pattern="^(today|yesterday|last7|month|custom)$"),
              date_from: date | None = None, date_to: date | None = None,
              db: Session = Depends(get_db)):
    return dashboard_report(db, preset, date_from, date_to)


def _list_item(report) -> DailyReportListItem:
    return DailyReportListItem(
        id=report.id, cash_session_id=report.cash_session_id, generated_at=report.generated_at,
        opened_at=report.session.opened_at, closed_at=report.session.closed_at,
        sales_total=report.sales_total, cash_total=report.cash_total, electronic_total=report.electronic_total,
        tickets=report.tickets, average_ticket=report.average_ticket, gross_profit=report.gross_profit,
        gross_margin=report.gross_margin, closing_expected=report.closing_expected,
        closing_actual=report.closing_actual, closing_difference=report.closing_difference)


def _detail(report) -> DailyReportOut:
    return DailyReportOut(**_list_item(report).model_dump(), gross_cost=report.gross_cost,
                          report_data=report.report_data)


@router.get("/current", response_model=CurrentReportOut)
def current(db: Session = Depends(get_db)):
    return current_report(db)


@router.get("/daily/latest", response_model=DailyReportOut | None)
def daily_latest(db: Session = Depends(get_db)):
    report = get_latest_daily_report(db)
    return _detail(report) if report else None


@router.get("/daily", response_model=list[DailyReportListItem])
def daily_list(year: int | None = None, month: int | None = None,
              limit: int = Query(default=31, ge=1, le=366), offset: int = Query(default=0, ge=0),
              db: Session = Depends(get_db)):
    return [_list_item(report) for report in list_daily_reports(db, year, month, limit, offset)]


@router.get("/daily/{report_id}", response_model=DailyReportOut)
def daily_detail(report_id: UUID, db: Session = Depends(get_db)):
    report = get_daily_report(db, report_id)
    if not report:
        raise HTTPException(404, "Informe no encontrado")
    return _detail(report)


@router.get("/monthly", response_model=MonthlyReportOut)
def monthly(year: int = Query(ge=2000, le=2100), month: int = Query(ge=1, le=12),
           db: Session = Depends(get_db)):
    return monthly_report(db, year, month)


@router.post("/sessions/{cash_session_id}/generate", response_model=DailyReportOut)
def generate(cash_session_id: UUID, db: Session = Depends(get_db)):
    session = db.get(CashSession, cash_session_id)
    if not session:
        raise HTTPException(404, "Sesión de caja no encontrada")
    if session.closed_at is None:
        raise HTTPException(409, "Solo se puede generar el informe de una jornada cerrada")
    return _detail(generate_daily_report(db, session))
