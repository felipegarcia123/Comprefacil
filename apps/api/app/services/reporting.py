from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.services.effective_payments import effective_channel_id_column
from app.domain.enums import SaleStatus
from app.models import (Expense, InternalConsumption, OperationalCategory,
                        Payment, PaymentChannel, Sale, SaleItem)
from app.schemas.reports import (DashboardReport, ReportCategory, ReportChannel,
                                 ReportPaymentGroup, ReportPeriod, ReportSummary,
                                 TimePoint, TopProduct)
from app.services.operations import ZERO, _allocate
from app.services.reporting_categories import reporting_category, reporting_category_name

ZONE = ZoneInfo("America/Bogota")
CENT = Decimal("0.01")


def _utc(local: datetime) -> datetime:
    return local.replace(tzinfo=ZONE).astimezone(timezone.utc)


def resolve_period(preset: str, date_from: date | None = None, date_to: date | None = None,
                   now: datetime | None = None) -> tuple[datetime, datetime, datetime, datetime, str]:
    local_now = (now or datetime.now(timezone.utc)).astimezone(ZONE)
    today = local_now.date()
    if preset == "today": start_local, end_local = datetime.combine(today,time.min), local_now.replace(tzinfo=None)
    elif preset == "yesterday":
        day=today-timedelta(days=1); start_local=datetime.combine(day,time.min); end_local=datetime.combine(day,time.max)
    elif preset == "last7": start_local=datetime.combine(today-timedelta(days=6),time.min); end_local=local_now.replace(tzinfo=None)
    elif preset == "month": start_local=datetime.combine(today.replace(day=1),time.min); end_local=local_now.replace(tzinfo=None)
    elif preset == "custom":
        if not date_from or not date_to or date_from > date_to: raise HTTPException(422,"El rango de fechas no es válido")
        if (date_to-date_from).days > 366: raise HTTPException(422,"El rango máximo es de 366 días")
        start_local=datetime.combine(date_from,time.min); end_local=datetime.combine(date_to,time.max)
    else: raise HTTPException(422,"Período no válido")
    duration=end_local-start_local
    if preset in {"today","yesterday"}:
        previous_start=start_local-timedelta(days=1); previous_end=end_local-timedelta(days=1)
    elif preset=="last7":
        previous_start=start_local-timedelta(days=7); previous_end=end_local-timedelta(days=7)
    elif preset=="month":
        prior_month_end=start_local.date()-timedelta(days=1)
        previous_start=datetime.combine(prior_month_end.replace(day=1),time.min); previous_end=previous_start+duration
    else:
        previous_end=start_local-timedelta(microseconds=1); previous_start=previous_end-duration
    granularity="hour" if start_local.date()==end_local.date() else "day"
    return _utc(start_local),_utc(end_local),_utc(previous_start),_utc(previous_end),granularity


def _metrics(db:Session,start:datetime,end:datetime):
    total,tickets=db.execute(select(func.coalesce(func.sum(Sale.total),0),func.count(Sale.id)).where(
        Sale.status==SaleStatus.COMPLETED,Sale.completed_at>=start,Sale.completed_at<=end)).one()
    units=db.scalar(select(func.coalesce(func.sum(SaleItem.quantity),0)).join(Sale,Sale.id==SaleItem.sale_id).where(
        Sale.status==SaleStatus.COMPLETED,Sale.completed_at>=start,Sale.completed_at<=end,SaleItem.product_id.is_not(None)))
    total=Decimal(total); tickets=int(tickets); return total,tickets,(total/tickets).quantize(CENT) if tickets else ZERO,Decimal(units)


def _change(current,previous):
    previous=Decimal(previous); return None if previous==0 else ((Decimal(current)-previous)/previous*100).quantize(Decimal("0.1"),rounding=ROUND_HALF_UP)


def _time_expr(db:Session,granularity:str):
    if db.bind.dialect.name=="postgresql":
        local=func.timezone("America/Bogota",Sale.completed_at)
        return func.to_char(local,"YYYY-MM-DD HH24") if granularity=="hour" else func.to_char(local,"YYYY-MM-DD")
    local=func.datetime(Sale.completed_at,"-5 hours")
    return func.strftime("%Y-%m-%d %H",local) if granularity=="hour" else func.strftime("%Y-%m-%d",local)


def _series(db,start,end,granularity,now=None):
    key=_time_expr(db,granularity)
    rows={str(k):(Decimal(s),int(t)) for k,s,t in db.execute(select(key,func.sum(Sale.total),func.count(Sale.id)).where(
        Sale.status==SaleStatus.COMPLETED,Sale.completed_at>=start,Sale.completed_at<=end).group_by(key)).all()}
    local_start=start.astimezone(ZONE); local_end=end.astimezone(ZONE); points=[]
    if granularity=="hour":
        cursor=local_start.replace(minute=0,second=0,microsecond=0); finish=local_end.replace(minute=0,second=0,microsecond=0)
        while cursor<=finish:
            k=cursor.strftime("%Y-%m-%d %H"); sales,tickets=rows.get(k,(ZERO,0)); points.append(TimePoint(key=k,label=f"{cursor:%H}:00",sales=sales,tickets=tickets,average_ticket=(sales/tickets).quantize(CENT) if tickets else ZERO)); cursor+=timedelta(hours=1)
    else:
        cursor=local_start.date(); finish=local_end.date()
        while cursor<=finish:
            k=cursor.isoformat(); sales,tickets=rows.get(k,(ZERO,0)); points.append(TimePoint(key=k,label=cursor.strftime("%d/%m"),sales=sales,tickets=tickets,average_ticket=(sales/tickets).quantize(CENT) if tickets else ZERO)); cursor+=timedelta(days=1)
    return points


def _categories(db,start,end,total_sales):
    item_rows=db.execute(select(SaleItem.sale_id,Sale.total,SaleItem.category_code,SaleItem.manual_kind,Sale.source,func.sum(SaleItem.line_total)).join(Sale,Sale.id==SaleItem.sale_id).where(
        Sale.status==SaleStatus.COMPLETED,Sale.completed_at>=start,Sale.completed_at<=end).group_by(SaleItem.sale_id,Sale.total,SaleItem.category_code,SaleItem.manual_kind,Sale.source)).all()
    cats=defaultdict(dict); totals={}
    for sid,total,code,manual_kind,source,amount in item_rows:
        code=reporting_category(code,manual_kind=manual_kind,sale_source=source)
        cats[sid][code]=cats[sid].get(code,ZERO)+Decimal(amount);totals[sid]=Decimal(total)
    aggregate=defaultdict(lambda:defaultdict(Decimal))
    payments=db.execute(select(Payment.sale_id,Payment.amount,PaymentChannel.payment_group).join(PaymentChannel,PaymentChannel.id==effective_channel_id_column()).join(Sale,Sale.id==Payment.sale_id).where(
        Sale.status==SaleStatus.COMPLETED,Sale.completed_at>=start,Sale.completed_at<=end)).all()
    for sid,amount,group in payments:
        for code,value in _allocate(Decimal(amount),cats.get(sid,{}),totals.get(sid,ZERO)).items(): aggregate[code][group.value]+=value
    names={x.code:x.name for x in db.scalars(select(OperationalCategory))}; result=[]
    for code,data in aggregate.items():
        total=sum(data.values(),ZERO);result.append(ReportCategory(code=code,name=reporting_category_name(code,names),cash=data["CASH"],electronic=data["ELECTRONIC"],other=data["OTHER"],total=total,percentage=(total/total_sales*100).quantize(Decimal("0.1")) if total_sales else ZERO))
    return sorted(result,key=lambda x:x.total,reverse=True)


def dashboard_report(db:Session,preset:str,date_from=None,date_to=None,now=None):
    start,end,previous_start,previous_end,granularity=resolve_period(preset,date_from,date_to,now)
    sales,tickets,average,units=_metrics(db,start,end); ps,pt,pa,_=_metrics(db,previous_start,previous_end)
    series=_series(db,start,end,granularity,now); categories=_categories(db,start,end,sales)
    channel_rows=db.execute(select(PaymentChannel.id,PaymentChannel.name,PaymentChannel.payment_group,func.sum(Payment.amount),func.count(Payment.id)).join(Payment,effective_channel_id_column()==PaymentChannel.id).join(Sale,Sale.id==Payment.sale_id).where(
        Sale.status==SaleStatus.COMPLETED,Sale.completed_at>=start,Sale.completed_at<=end).group_by(PaymentChannel.id,PaymentChannel.name,PaymentChannel.payment_group)).all()
    from app.services.post_sale import completed_refunds_by_channel
    refunded_by_channel = completed_refunds_by_channel(db, start, end)
    refunded_total = sum(refunded_by_channel.values(), ZERO)
    net_sales = sales - refunded_total
    channels=[ReportChannel(channel_id=i,name=n,group=g.value,amount=Decimal(a)-refunded_by_channel.get(i, ZERO),payment_count=int(c),percentage=((Decimal(a)-refunded_by_channel.get(i, ZERO))/net_sales*100).quantize(Decimal("0.1")) if net_sales else ZERO) for i,n,g,a,c in channel_rows]
    channels.sort(key=lambda x:x.amount,reverse=True); groups=defaultdict(Decimal)
    for row in channels: groups[row.group]+=row.amount
    payment_groups=[ReportPaymentGroup(group=k,amount=v,percentage=(v/net_sales*100).quantize(Decimal("0.1")) if net_sales else ZERO) for k,v in groups.items()]
    top=[TopProduct(product_id=i,name=n,units=Decimal(u),sales=Decimal(s)) for i,n,u,s in db.execute(select(SaleItem.product_id,SaleItem.product_name,func.sum(SaleItem.quantity),func.sum(SaleItem.line_total)).join(Sale,Sale.id==SaleItem.sale_id).where(
        Sale.status==SaleStatus.COMPLETED,Sale.completed_at>=start,Sale.completed_at<=end,SaleItem.product_id.is_not(None)).group_by(SaleItem.product_id,SaleItem.product_name).order_by(func.sum(SaleItem.quantity).desc()).limit(10)).all()]
    expenses=Decimal(db.scalar(select(func.coalesce(func.sum(Expense.amount),0)).where(Expense.created_at>=start,Expense.created_at<=end)))
    consumption=Decimal(db.scalar(select(func.coalesce(func.sum(InternalConsumption.cost_total),0)).where(InternalConsumption.created_at>=start,InternalConsumption.created_at<=end)))
    from app.services.post_sale import exchanges_summary, returns_summary
    returns_total=returns_summary(db,start,end)["refunded_total"]
    exchanged=exchanges_summary(db,start,end)
    peak_t=max(series,key=lambda x:x.tickets) if tickets else None;peak_s=max(series,key=lambda x:x.sales) if sales else None
    period=ReportPeriod(preset=preset,date_from=start,date_to=end,previous_from=previous_start,previous_to=previous_end,granularity=granularity)
    summary=ReportSummary(sales_total=net_sales,tickets=tickets,average_ticket=net_sales/tickets if tickets else ZERO,units=units,expenses=expenses,internal_consumption=consumption,sales_change=_change(net_sales,ps),tickets_change=_change(tickets,pt),average_ticket_change=_change(net_sales/tickets if tickets else ZERO,pa),peak_tickets_label=peak_t.label if peak_t else None,peak_tickets=peak_t.tickets if peak_t else 0,peak_sales_label=peak_s.label if peak_s else None,peak_sales=peak_s.sales if peak_s else ZERO,leading_category=categories[0].name if categories else None,leading_channel=channels[0].name if channels else None,returns_total=returns_total,net_sales=net_sales,exchange_count=exchanged['exchange_count'],exchange_difference=exchanged['difference_collected'],exchange_difference_refunded=exchanged['difference_refunded'])
    return DashboardReport(period=period,summary=summary,series=series,categories=categories,payment_groups=payment_groups,payment_channels=channels,top_products=top)
