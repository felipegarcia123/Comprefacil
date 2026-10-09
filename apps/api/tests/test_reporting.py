from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.domain.enums import PaymentGroup, PaymentMethod, SaleSource, SaleStatus
from app.main import app
from app.models import OperationalCategory, Payment, PaymentChannel, Product, Sale, SaleItem, User
from app.services.reporting import dashboard_report, resolve_period


@pytest.fixture()
def report_context():
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as db:
        user=User(name="Admin"); cigar=OperationalCategory(code="CIGARROS",name="Cigarros"); supercat=OperationalCategory(code="SUPER",name="Super")
        cash=PaymentChannel(name="Efectivo",payment_type=PaymentMethod.CASH,payment_group=PaymentGroup.CASH,affects_cash=True)
        nequi=PaymentChannel(name="Nequi",payment_type=PaymentMethod.TRANSFER,payment_group=PaymentGroup.ELECTRONIC)
        card=PaymentChannel(name="Datáfono",payment_type=PaymentMethod.CARD,payment_group=PaymentGroup.ELECTRONIC)
        p1=Product(barcode="P1",name="Cigarro",sale_price=100,unit_cost=50,operational_category_id=cigar.id)
        p2=Product(barcode="P2",name="Arroz",sale_price=100,unit_cost=60,operational_category_id=supercat.id)
        db.add_all([user,cigar,supercat,cash,nequi,card,p1,p2]);db.flush()
        def sale(number,hour,minute,total,status=SaleStatus.COMPLETED,items=None,payments=None,source=SaleSource.POS_PRODUCT,day=9):
            row=Sale(number=number,status=status,source=source,created_by=user.id,total=Decimal(total),completed_at=datetime(2026,9,day,hour+5,minute,tzinfo=timezone.utc) if status==SaleStatus.COMPLETED else None)
            db.add(row);db.flush()
            for product,code,name,qty,amount in items or []: db.add(SaleItem(sale_id=row.id,product_id=product.id if product else None,operational_category_id=cigar.id if code=="CIGARROS" else supercat.id,category_code=code,product_name=name,quantity=Decimal(qty),unit_price=Decimal(amount)/Decimal(qty),unit_cost=0,line_total=Decimal(amount)))
            for channel,amount in payments or []: db.add(Payment(sale_id=row.id,method=channel.payment_type,payment_channel_id=channel.id,amount=Decimal(amount)))
            return row
        sale("A",10,15,100,items=[(p1,"CIGARROS","Cigarro",1,40),(p2,"SUPER","Arroz",2,60)],payments=[(cash,50),(nequi,50)])
        sale("B",10,37,200,items=[(p1,"CIGARROS","Cigarro",2,200)],payments=[(cash,200)])
        sale("C",10,59,300,items=[(p2,"SUPER","Arroz",3,300)],payments=[(card,300)])
        sale("D",11,4,400,items=[(None,"SIN_CLASIFICAR","Venta manual",1,400)],payments=[(nequi,400)],source=SaleSource.MANUAL_SACK)
        sale("OPEN",10,1,999,status=SaleStatus.OPEN);sale("CANCEL",10,2,999,status=SaleStatus.CANCELLED)
        sale("PREV",10,1,500,items=[(p1,"CIGARROS","Cigarro",1,500)],payments=[(cash,500)],day=8)
        db.commit()
        def override():yield db
        app.dependency_overrides[get_db]=override
        try:yield db,TestClient(app),datetime(2026,9,9,17,0,tzinfo=timezone.utc)
        finally:app.dependency_overrides.clear()


def report(context):
    db,_,now=context;return dashboard_report(db,"today",now=now)


def test_completed_only_kpis_and_average(report_context):
    data=report(report_context)
    assert data.summary.sales_total==1000 and data.summary.tickets==4
    assert data.summary.average_ticket==250 and data.summary.units==8


def test_exact_ticket_hours_zero_fill_and_average(report_context):
    data=report(report_context);points={x.label:x for x in data.series}
    assert points["10:00"].tickets==3 and points["11:00"].tickets==1
    assert points["09:00"].tickets==0 and points["09:00"].sales==0
    assert points["10:00"].average_ticket==Decimal("200.00")


def test_peak_metrics(report_context):
    data=report(report_context)
    assert data.summary.peak_tickets_label=="10:00" and data.summary.peak_tickets==3
    assert data.summary.peak_sales_label=="10:00" and data.summary.peak_sales==600


def test_mixed_payment_category_allocation(report_context):
    data=report(report_context);cats={x.code:x for x in data.categories}
    assert cats["CIGARROS"].cash==220 and cats["CIGARROS"].electronic==20
    assert cats["SUPER"].cash==30 and cats["SUPER"].electronic==330
    assert cats["SIN_CLASIFICAR"].electronic==400
    assert sum(x.total for x in data.categories)==data.summary.sales_total


def test_payment_groups_channels_and_mixed_does_not_duplicate(report_context):
    data=report(report_context);groups={x.group:x.amount for x in data.payment_groups};channels={x.name:x for x in data.payment_channels}
    assert groups=={"CASH":Decimal("250"),"ELECTRONIC":Decimal("750")}
    assert channels["Nequi"].amount==450 and channels["Nequi"].payment_count==2
    assert sum(x.amount for x in data.payment_channels)==1000


def test_top_products_excludes_manual_units(report_context):
    data=report(report_context)
    assert [x.name for x in data.top_products]==["Arroz","Cigarro"]
    assert sum(x.units for x in data.top_products)==8


def test_comparison_previous_period(report_context):
    data=report(report_context)
    assert data.summary.sales_change==Decimal("100.0")
    assert data.summary.tickets_change==Decimal("300.0")


def test_empty_response_and_zero_division(report_context):
    db,_,_=report_context;data=dashboard_report(db,"custom",datetime(2025,1,1).date(),datetime(2025,1,1).date())
    assert data.summary.tickets==0 and data.summary.average_ticket==0
    assert data.summary.sales_change is None and len(data.series)==24


@pytest.mark.parametrize("preset,granularity",[("today","hour"),("yesterday","hour"),("last7","day"),("month","day")])
def test_period_presets(report_context,preset,granularity):
    db,_,now=report_context;data=dashboard_report(db,preset,now=now)
    assert data.period.granularity==granularity


@pytest.mark.parametrize("start,end",[("2026-09-10","2026-09-09"),(None,"2026-09-09"),("2025-01-01","2026-09-09")])
def test_invalid_custom_ranges(report_context,start,end):
    _,client,_=report_context;params={"preset":"custom"}
    if start:params["date_from"]=start
    if end:params["date_to"]=end
    assert client.get("/api/v1/reports/dashboard",params=params).status_code==422


def test_timezone_colombia_day_boundary(report_context):
    start,end,_,_,_=resolve_period("custom",datetime(2026,9,9).date(),datetime(2026,9,9).date())
    assert start.hour==5 and end.astimezone(timezone.utc).date()==datetime(2026,9,10).date()


def test_dashboard_endpoint_shape(report_context):
    _,client,_=report_context;response=client.get("/api/v1/reports/dashboard",params={"preset":"custom","date_from":"2026-09-09","date_to":"2026-09-09"})
    assert response.status_code==200
    assert {"summary","series","categories","payment_groups","payment_channels","top_products"}<set(response.json())


@pytest.mark.parametrize("hour", range(13))
def test_today_zero_fills_every_elapsed_hour(report_context, hour):
    points={point.label:point for point in report(report_context).series}
    assert f"{hour:02d}:00" in points
