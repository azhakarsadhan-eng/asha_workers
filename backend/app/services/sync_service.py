import json
import uuid
import datetime
from typing import Dict, Any, List
from sqlalchemy.orm import Session
from ..models.sync import SyncQueueItem
from ..models.auth import User
from ..models.beneficiary import Beneficiary, PregnancyRecord
from ..models.clinical import ANCVisit, RiskFactor, Referral, HomeVisit, PNCVisit
from ..services.clinical_rules import evaluate_clinical_rules, calculate_edd, generate_standard_anc_schedule
from ..services.auth_service import log_audit

def process_sync_push(db: Session, user: User, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    acknowledged_ids = []
    conflicts = []
    errors = []

    for item in items:
        tx_id = item.get("client_tx_id") or str(uuid.uuid4())
        entity_type = item.get("entity_type")
        operation = item.get("operation", "CREATE").upper()
        client_version = item.get("client_version", 1)
        payload = item.get("payload", {})

        # Check idempotency: If already acknowledged, return success without duplicate insert
        existing_tx = db.query(SyncQueueItem).filter(SyncQueueItem.client_tx_id == tx_id).first()
        if existing_tx and existing_tx.sync_state == "SYNCHRONIZED":
            acknowledged_ids.append(tx_id)
            continue

        try:
            if entity_type == "beneficiary":
                b_id = payload.get("beneficiary_id")
                ben = db.query(Beneficiary).filter(Beneficiary.beneficiary_id == b_id).first() if b_id else None
                
                if operation == "CREATE":
                    if ben:
                        # Idempotent match
                        acknowledged_ids.append(tx_id)
                        continue
                    
                    dob_val = datetime.date.fromisoformat(payload["dob"]) if payload.get("dob") else None
                    ben = Beneficiary(
                        beneficiary_id=b_id or f"BEN-{datetime.date.today().year}-{uuid.uuid4().hex[:6].upper()}",
                        full_name=payload.get("full_name", "Unknown"),
                        age=int(payload.get("age", 25)),
                        dob=dob_val,
                        contact_phone=payload.get("contact_phone"),
                        village=payload.get("village", user.village or "Default Village"),
                        sub_centre=payload.get("sub_centre", user.sub_centre or "Default SC"),
                        primary_health_centre=payload.get("primary_health_centre", user.primary_health_centre or "Default PHC"),
                        assigned_asha_id=user.id,
                        preferred_language=payload.get("preferred_language", user.preferred_language or "ta"),
                        consent_obtained=payload.get("consent_obtained", True),
                        is_draft=payload.get("is_draft", False),
                        version_id=1
                    )
                    db.add(ben)
                    db.flush()

                    # Automatically create initial pregnancy record if pregnancy fields exist
                    lmp_raw = payload.get("lmp_date")
                    lmp_val = datetime.date.fromisoformat(lmp_raw) if lmp_raw else None
                    edd_val = calculate_edd(lmp_val)
                    
                    prg = PregnancyRecord(
                        pregnancy_id=f"PRG-{datetime.date.today().year}-{uuid.uuid4().hex[:6].upper()}",
                        beneficiary_id=ben.beneficiary_id,
                        gravida=int(payload.get("gravida", 1)),
                        parity=int(payload.get("parity", 0)),
                        lmp_date=lmp_val,
                        edd_date=edd_val,
                        registration_date=datetime.date.today(),
                        past_obstetric_history=payload.get("past_obstetric_history"),
                        known_medical_conditions=payload.get("known_medical_conditions"),
                        version_id=1
                    )
                    db.add(prg)
                    db.flush()

                    if lmp_val:
                        # Auto schedule 4 standard ANC visits
                        anc_sched = generate_standard_anc_schedule(lmp_val)
                        for visit in anc_sched:
                            anc = ANCVisit(
                                visit_uuid=f"ANC-{uuid.uuid4().hex[:8].upper()}",
                                pregnancy_id=prg.pregnancy_id,
                                beneficiary_id=ben.beneficiary_id,
                                anc_period=visit["anc_period"],
                                scheduled_date=visit["scheduled_date"],
                                status="NOT_YET_DUE",
                                recorded_by_user_id=user.id,
                                sync_status="SYNCHRONIZED"
                            )
                            db.add(anc)

                elif operation == "UPDATE":
                    if not ben:
                        errors.append({"client_tx_id": tx_id, "detail": f"Beneficiary {b_id} not found on server"})
                        continue
                    if client_version < ben.version_id:
                        conflicts.append({
                            "client_tx_id": tx_id,
                            "entity_type": entity_type,
                            "server_version": ben.version_id,
                            "client_version": client_version,
                            "detail": "Server record is newer than client version"
                        })
                        continue
                    ben.full_name = payload.get("full_name", ben.full_name)
                    ben.age = int(payload.get("age", ben.age))
                    ben.contact_phone = payload.get("contact_phone", ben.contact_phone)
                    ben.version_id += 1

            elif entity_type == "anc_visit":
                v_uuid = payload.get("visit_uuid")
                anc = db.query(ANCVisit).filter(ANCVisit.visit_uuid == v_uuid).first() if v_uuid else None
                
                act_date = datetime.date.fromisoformat(payload["actual_visit_date"]) if payload.get("actual_visit_date") else datetime.date.today()
                sched_date = datetime.date.fromisoformat(payload["scheduled_date"]) if payload.get("scheduled_date") else datetime.date.today()

                if not anc:
                    anc = ANCVisit(
                        visit_uuid=v_uuid or f"ANC-{uuid.uuid4().hex[:8].upper()}",
                        pregnancy_id=payload.get("pregnancy_id"),
                        beneficiary_id=payload.get("beneficiary_id"),
                        anc_period=payload.get("anc_period", "ANC-SPECIAL"),
                        scheduled_date=sched_date,
                        actual_visit_date=act_date,
                        status=payload.get("status", "COMPLETED"),
                        gestational_age_weeks=payload.get("gestational_age_weeks"),
                        recorded_by_user_id=user.id,
                        weight_kg=payload.get("weight_kg"),
                        bp_systolic=payload.get("bp_systolic"),
                        bp_diastolic=payload.get("bp_diastolic"),
                        hemoglobin_g_dl=payload.get("hemoglobin_g_dl"),
                        urine_albumin=payload.get("urine_albumin", "NOT_DONE"),
                        urine_sugar=payload.get("urine_sugar", "NOT_DONE"),
                        ultrasound_status=payload.get("ultrasound_status"),
                        ifa_tablets_issued=payload.get("ifa_tablets_issued", 0),
                        calcium_tablets_issued=payload.get("calcium_tablets_issued", 0),
                        tt_td_dose=payload.get("tt_td_dose", "NONE"),
                        counselling_topics=payload.get("counselling_topics"),
                        deferral_reason=payload.get("deferral_reason"),
                        clinical_notes=payload.get("clinical_notes"),
                        sync_status="SYNCHRONIZED",
                        version_id=1
                    )
                    db.add(anc)
                else:
                    anc.actual_visit_date = act_date
                    anc.status = payload.get("status", "COMPLETED")
                    anc.weight_kg = payload.get("weight_kg", anc.weight_kg)
                    anc.bp_systolic = payload.get("bp_systolic", anc.bp_systolic)
                    anc.bp_diastolic = payload.get("bp_diastolic", anc.bp_diastolic)
                    anc.hemoglobin_g_dl = payload.get("hemoglobin_g_dl", anc.hemoglobin_g_dl)
                    anc.urine_albumin = payload.get("urine_albumin", anc.urine_albumin)
                    anc.urine_sugar = payload.get("urine_sugar", anc.urine_sugar)
                    anc.counselling_topics = payload.get("counselling_topics", anc.counselling_topics)
                    anc.clinical_notes = payload.get("clinical_notes", anc.clinical_notes)
                    anc.sync_status = "SYNCHRONIZED"
                    anc.version_id += 1

                # Evaluate clinical rules on measurements
                flags = evaluate_clinical_rules(
                    bp_systolic=payload.get("bp_systolic"),
                    bp_diastolic=payload.get("bp_diastolic"),
                    hemoglobin_g_dl=payload.get("hemoglobin_g_dl"),
                    urine_albumin=payload.get("urine_albumin")
                )
                if flags and anc.pregnancy_id:
                    prg = db.query(PregnancyRecord).filter(PregnancyRecord.pregnancy_id == anc.pregnancy_id).first()
                    if prg:
                        prg.high_risk_flag = True
                    for flg in flags:
                        rf = RiskFactor(
                            risk_uuid=f"RISK-{uuid.uuid4().hex[:8].upper()}",
                            pregnancy_id=anc.pregnancy_id,
                            rule_code=flg.get("rule_code"),
                            risk_title=flg.get("risk_title"),
                            risk_detail=flg.get("risk_detail"),
                            severity=flg.get("severity", "HIGH"),
                            status=flg.get("status", "RULE_FLAGGED"),
                            requires_professional_review=True,
                            recorded_by_user_id=user.id
                        )
                        db.add(rf)

            elif entity_type == "home_visit":
                v_uuid = payload.get("visit_uuid")
                hv = db.query(HomeVisit).filter(HomeVisit.visit_uuid == v_uuid).first() if v_uuid else None
                v_date = datetime.date.fromisoformat(payload["visit_date"]) if payload.get("visit_date") else datetime.date.today()
                
                if not hv:
                    hv = HomeVisit(
                        visit_uuid=v_uuid or f"HV-{uuid.uuid4().hex[:8].upper()}",
                        pregnancy_id=payload.get("pregnancy_id"),
                        beneficiary_id=payload.get("beneficiary_id"),
                        visit_date=v_date,
                        assigned_worker_id=user.id,
                        visit_purpose=payload.get("visit_purpose", "ROUTINE_PREGNANCY_SURVEILLANCE"),
                        is_completed=payload.get("is_completed", True),
                        observations=payload.get("observations"),
                        counselling_topics=payload.get("counselling_topics"),
                        follow_up_tasks=payload.get("follow_up_tasks"),
                        notes=payload.get("notes"),
                        is_draft=payload.get("is_draft", False),
                        sync_status="SYNCHRONIZED"
                    )
                    db.add(hv)
                else:
                    hv.is_completed = payload.get("is_completed", hv.is_completed)
                    hv.observations = payload.get("observations", hv.observations)
                    hv.counselling_topics = payload.get("counselling_topics", hv.counselling_topics)
                    hv.notes = payload.get("notes", hv.notes)
                    hv.sync_status = "SYNCHRONIZED"

            elif entity_type == "referral":
                r_uuid = payload.get("referral_uuid")
                ref = db.query(Referral).filter(Referral.referral_uuid == r_uuid).first() if r_uuid else None
                if not ref:
                    ref = Referral(
                        referral_uuid=r_uuid or f"REF-{uuid.uuid4().hex[:8].upper()}",
                        pregnancy_id=payload.get("pregnancy_id"),
                        beneficiary_id=payload.get("beneficiary_id"),
                        danger_sign_code=payload.get("danger_sign_code"),
                        reason=payload.get("reason", "Urgent danger sign referral"),
                        urgency=payload.get("urgency", "CRITICAL_108"),
                        referring_user_id=user.id,
                        destination_facility_id=int(payload.get("destination_facility_id", 1)),
                        transport_mode=payload.get("transport_mode", "EMERGENCY_AMBULANCE_108"),
                        referral_initiated_at=datetime.datetime.utcnow()
                    )
                    db.add(ref)

            # Record queue item record in database for audit and transaction tracking
            if not existing_tx:
                queue_entry = SyncQueueItem(
                    client_tx_id=tx_id,
                    user_id=user.id,
                    entity_type=entity_type,
                    operation=operation,
                    client_version=client_version,
                    payload_json=json.dumps(payload),
                    sync_state="SYNCHRONIZED",
                    server_acknowledged_at=datetime.datetime.utcnow()
                )
                db.add(queue_entry)
            else:
                existing_tx.sync_state = "SYNCHRONIZED"
                existing_tx.server_acknowledged_at = datetime.datetime.utcnow()

            db.commit()
            acknowledged_ids.append(tx_id)

        except Exception as e:
            db.rollback()
            errors.append({"client_tx_id": tx_id, "detail": str(e)})

    log_audit(
        db,
        action="SYNC_PUSH",
        user=user,
        target_entity="batch_sync",
        details=f"Processed {len(items)} items. Acknowledged: {len(acknowledged_ids)}, Conflicts: {len(conflicts)}, Errors: {len(errors)}"
    )

    return {
        "acknowledged_ids": acknowledged_ids,
        "conflicts": conflicts,
        "errors": errors
    }

