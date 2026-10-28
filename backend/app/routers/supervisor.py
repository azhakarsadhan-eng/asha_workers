import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session
from sqlalchemy import func
from ..database import get_db
from ..models.auth import User, UserRole
from ..models.beneficiary import Beneficiary, PregnancyRecord
from ..models.clinical import ANCVisit, PNCVisit, RiskFactor, Referral
from ..models.sync import SyncQueueItem
from ..models.audit import AuditLog
from ..services.auth_service import get_current_user, require_role, log_audit

router = APIRouter(prefix="/api/supervisor", tags=["Supervisor Dashboard & Reporting"])

@router.get("/dashboard-kpis")
def get_dashboard_kpis(
    village: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Computes live dashboard statistics directly from database records.
    Zero hardcoded numbers.
    """
    today = datetime.date.today()
    thirty_days_ago = today - datetime.timedelta(days=30)

    # Base query for beneficiaries
    b_query = db.query(Beneficiary).filter(Beneficiary.is_active == True)
    if current_user.role == UserRole.ASHA and current_user.village:
        b_query = b_query.filter(Beneficiary.village == current_user.village)
    elif village:
        b_query = b_query.filter(Beneficiary.village == village)

    ben_ids = [b.beneficiary_id for b in b_query.all()]
    total_beneficiaries = len(ben_ids)

    # Active pregnancies
    prg_query = db.query(PregnancyRecord).filter(PregnancyRecord.beneficiary_id.in_(ben_ids))
    total_active_pregnancies = prg_query.filter(PregnancyRecord.status == "ACTIVE").count()
    new_registrations_last_30d = prg_query.filter(PregnancyRecord.registration_date >= thirty_days_ago).count()
    high_risk_pregnancies = prg_query.filter(PregnancyRecord.status == "ACTIVE", PregnancyRecord.high_risk_flag == True).count()
    total_delivered = prg_query.filter(PregnancyRecord.status == "DELIVERED").count()

    prg_ids = [p.pregnancy_id for p in prg_query.all()]

    # ANC visits
    anc_query = db.query(ANCVisit).filter(ANCVisit.pregnancy_id.in_(prg_ids))
    anc_completed = anc_query.filter(ANCVisit.status == "COMPLETED").count()
    anc_due_soon = anc_query.filter(
        ANCVisit.status.in_(["DUE_SOON", "NOT_YET_DUE"]),
        ANCVisit.scheduled_date >= today,
        ANCVisit.scheduled_date <= today + datetime.timedelta(days=7)
    ).count()
    anc_overdue = anc_query.filter(
        ANCVisit.status != "COMPLETED",
        ANCVisit.status != "DEFERRED",
        ANCVisit.scheduled_date < today
    ).count()

    # PNC visits
    pnc_query = db.query(PNCVisit).filter(PNCVisit.pregnancy_id.in_(prg_ids))
    pnc_completed = pnc_query.filter(PNCVisit.status == "COMPLETED").count()
    pnc_due_soon = pnc_query.filter(
        PNCVisit.status.in_(["DUE_SOON", "NOT_YET_DUE"]),
        PNCVisit.scheduled_date >= today,
        PNCVisit.scheduled_date <= today + datetime.timedelta(days=3)
    ).count()

    # Referrals
    ref_query = db.query(Referral).filter(Referral.pregnancy_id.in_(prg_ids))
    total_referrals = ref_query.count()
    unresolved_referrals = ref_query.filter(Referral.is_resolved == False).count()

    # Sync queue
    pending_sync_count = db.query(SyncQueueItem).filter(SyncQueueItem.sync_state != "SYNCHRONIZED").count()

    # Village aggregate breakdown
    village_breakdown = []
    villages = db.query(Beneficiary.village).distinct().all()
    for (v_name,) in villages:
        if not v_name:
            continue
        v_bens = db.query(Beneficiary).filter(Beneficiary.village == v_name, Beneficiary.is_active == True).all()
        v_b_ids = [b.beneficiary_id for b in v_bens]
        v_active_prg = db.query(PregnancyRecord).filter(PregnancyRecord.beneficiary_id.in_(v_b_ids), PregnancyRecord.status == "ACTIVE").count()
        v_hrp = db.query(PregnancyRecord).filter(PregnancyRecord.beneficiary_id.in_(v_b_ids), PregnancyRecord.high_risk_flag == True).count()
        village_breakdown.append({
            "village": v_name,
            "beneficiary_count": len(v_bens),
            "active_pregnancies": v_active_prg,
            "high_risk_count": v_hrp
        })

    return {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "total_beneficiaries": total_beneficiaries,
        "total_active_pregnancies": total_active_pregnancies,
        "new_registrations_last_30d": new_registrations_last_30d,
        "high_risk_pregnancies": high_risk_pregnancies,
        "total_delivered": total_delivered,
        "anc_stats": {
            "completed": anc_completed,
            "due_soon": anc_due_soon,
            "overdue": anc_overdue
        },
        "pnc_stats": {
            "completed": pnc_completed,
            "due_soon": pnc_due_soon
        },
        "referrals": {
            "total": total_referrals,
            "unresolved": unresolved_referrals
        },
        "sync_queue": {
            "pending_count": pending_sync_count
        },
        "village_breakdown": village_breakdown
    }

@router.get("/audit-logs", dependencies=[Depends(require_role([UserRole.SUPERVISOR, UserRole.ADMIN, UserRole.MEDICAL_OFFICER]))])
def view_audit_logs(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    logs = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit).all()
    return [l.to_dict() for l in logs]

@router.get("/export-report", dependencies=[Depends(require_role([UserRole.SUPERVISOR, UserRole.ADMIN]))])
def export_aggregate_report(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Secure aggregate report export for health supervisors."""
    kpis = get_dashboard_kpis(village=None, db=db, current_user=current_user)
    log_audit(db, action="EXPORT_REPORT", user=current_user, details="Exported block maternal health aggregate KPI report")
    return {
        "report_title": "Block Maternal and Child Health Performance Report",
        "generated_by": current_user.full_name,
        "role": current_user.role,
        "data": kpis
    }

