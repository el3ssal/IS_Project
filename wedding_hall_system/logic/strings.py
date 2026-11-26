"""Language-aware user-facing strings produced by the logic tier
(Phase 8, NFR-005).

The logic tier never imports ``ui`` (layering), so the few messages
it renders for users — availability reasons — live here as small
template tables. Service *exception* messages stay English
(decision A-71): they are also asserted by tests and logged, and
translating every exception would need an error-code system.

Price-item labels are NOT here: they are frozen records parsed by
``BookingService._selections_from_frozen_items``, so they stay
canonical English internally and are translated at display time by
``ui/i18n.py::translate_price_label``.
"""
from __future__ import annotations

AVAILABILITY_REASONS: dict[str, dict[str, str]] = {
    "en": {
        "end_before_start": "End time must be after start time.",
        "hall_missing": "Hall #{hall_id} does not exist.",
        "hall_not_bookable": "Hall '{name}' is {state} and cannot be booked.",
        "closed_exceptional": "The hall is closed on that day (exceptional closure).",
        "closed_day": "The hall is closed on that day.",
        "multi_day": "Events must start and end on the same day.",
        "outside_hours": "Outside opening hours ({spans}). The event must fit inside one opening interval.",
        "maintenance": "Maintenance '{type}' blocks the hall ({start} – {end}).",
        "conflict": "Conflicts with {status} booking ({start} – {end}), including buffer time.",
    },
    "ar": {
        "end_before_start": "يجب أن يكون وقت النهاية بعد وقت البداية.",
        "hall_missing": "القاعة رقم {hall_id} غير موجودة.",
        "hall_not_bookable": "القاعة «{name}» {state} ولا يمكن حجزها.",
        "closed_exceptional": "القاعة مغلقة في ذلك اليوم (إغلاق استثنائي).",
        "closed_day": "القاعة مغلقة في ذلك اليوم.",
        "multi_day": "يجب أن يبدأ الحدث وينتهي في نفس اليوم.",
        "outside_hours": "خارج مواعيد العمل ({spans}). يجب أن يكون الحدث داخل فترة فتح واحدة.",
        "maintenance": "الصيانة «{type}» تحجب القاعة ({start} – {end}).",
        "conflict": "يتعارض مع حجز {status} ({start} – {end})، بما في ذلك وقت التحضير.",
    },
}


def arsn(lang: str, key: str, **params) -> str:
    """Render an availability reason template."""
    lang = lang if lang in AVAILABILITY_REASONS else "en"
    template = AVAILABILITY_REASONS[lang].get(key) or AVAILABILITY_REASONS["en"][key]
    return template.format(**params)
