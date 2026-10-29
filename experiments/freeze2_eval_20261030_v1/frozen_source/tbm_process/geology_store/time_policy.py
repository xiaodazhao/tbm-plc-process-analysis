from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Asia/Shanghai")


def cutoff(value):
    if isinstance(value, datetime):
        stamp = value
    else:
        stamp = datetime.fromisoformat(str(value))
    return stamp.replace(tzinfo=ZONE) if stamp.tzinfo is None else stamp.astimezone(ZONE)


def time_decision(document, as_of=None):
    if as_of is None:
        return {"eligible": True, "status": "all_history", "basis": "no_historical_availability_claim"}
    m = document["metadata"]
    end = cutoff(as_of)
    if m.get("date_conflicts") or document.get("date_audit", {}).get("status") != "PASS":
        return {"eligible": False, "status": "REVIEW", "reason": "missing_or_conflicting_date_evidence"}
    explicit = m.get("available_at")
    if explicit:
        try:
            instant = datetime.fromisoformat(explicit)
        except ValueError:
            return {"eligible": False, "status": "REVIEW", "reason": "invalid_availability_timestamp"}
        if instant.tzinfo is None or not m.get("actual_distribution_time_verified"):
            return {"eligible": False, "status": "REVIEW", "reason": "unverified_availability_timestamp"}
        return {"eligible": instant < end, "status": "eligible" if instant < end else "excluded",
                "basis": "verified_available_at", "available_at": explicit, "cutoff": end.isoformat()}
    if m.get("available_date_user_confirmed"):
        basis, field = m.get("available_date"), "user_confirmed_available_date"
    else:
        # A cover or test date alone does not establish that a forecast was submitted.
        basis, field = m.get("submission_date"), "submission_date"
        proof = m.get("submission_date_source")
        if not proof or document.get("date_audit", {}).get("basis_field") != "submission_date":
            basis = None
    try:
        day = date.fromisoformat(str(basis))
    except ValueError:
        return {"eligible": False, "status": "REVIEW", "reason": "submission_or_confirmed_availability_missing"}
    eligible = day < end.date()
    return {"eligible": eligible, "status": "eligible" if eligible else "excluded",
            "reason": "prior_day_date_policy" if eligible else "same_day_time_unknown" if day == end.date() else "future_document",
            "basis": field, "available_date": basis, "time_precision": "day", "available_at": None,
            "cutoff": end.isoformat(), "not_verified_receipt_timestamp": True}
