"""Seed defaults (Phase 1).

Seed data only — no real personal data. Demo accounts are for local
demonstration and development; the Owner must change their passwords
before any real use (see README).
"""
from __future__ import annotations

# Default system settings seeded into system_settings (key -> value).
DEFAULT_SETTINGS: dict[str, str] = {
    "venue.name": "Benha Wedding Hall",
    "venue.currency": "EGP",
    "venue.timezone": "Africa/Cairo",
    "venue.default_language": "ar",
    "booking.hold_hours": "48",
    "booking.grace_hours": "24",
    "booking.buffer_minutes": "60",
    "booking.deposit_percent": "30",
    "booking.min_duration_minutes": "120",
    "auth.max_failed_logins": "5",
    "auth.lockout_minutes": "15",
    "auth.session_expiry_hours": "8",
    "attachments.max_size_mb": "10",
    "pricing.max_discount_percent": "20",
    # -- Phase 5: financial policy (SRS §8.4, §8.8, §8.10). The SRS
    # defines the policy SHAPE (deposit %, installments, cancellation
    # by timing, no-show fees) but no numbers, so these are documented
    # seed defaults (decision A-43/A-44), Owner-configurable, and frozen
    # into financial_policy_snapshots at confirmation — later changes
    # never rewrite a confirmed booking's policy.
    "finance.installments_count": "3",
    "finance.installment_interval_days": "30",
    "finance.final_deadline_days_before_event": "7",
    "finance.late_fee_percent": "0",
    # Cancellation fee tiers: fee is a % of the total contract value,
    # chosen by days between cancellation and the event; the fee is
    # capped at the amount paid, refundable = paid - fee.
    "finance.cancellation_policy": (
        '[{"min_days_before_event": 30, "fee_percent_of_total": 10}, '
        '{"min_days_before_event": 14, "fee_percent_of_total": 25}, '
        '{"min_days_before_event": 0, "fee_percent_of_total": 50}]'
    ),
    # No-show: the deposit is forfeited, nothing is refundable, and the
    # unpaid balance stays outstanding.
    "finance.no_show_policy": (
        '{"fee_basis": "deposit", "refundable_percent": 0}'
    ),
}

# Roles seeded by data/seed.py (SRS §2.1).
SEED_ROLES: list[dict[str, str]] = [
    {"name": "Owner", "description": "Full access; system configuration, users, roles, financial reports, backup/restore."},
    {"name": "Manager", "description": "Bookings, halls, staff, operations, tasks, maintenance, operational reports."},
    {"name": "Receptionist", "description": "Customer records, inquiries, quotations, booking creation and permitted updates."},
    {"name": "Accountant", "description": "Payments, expenses, refunds, financial reports."},
    {"name": "Staff", "description": "View assigned tasks and update their own assigned task statuses."},
]

# Permissions as data (SRS §2.2). Format: code -> description.
# Codes are checked in the business layer via permissions.has_permission().
PERMISSIONS: dict[str, str] = {
    # Users / roles / settings
    "user.manage": "Create, enable/disable and manage employee accounts.",
    "role.manage": "Configure roles and their permissions.",
    "settings.manage": "Change system/venue settings.",
    "settings.view": "View system/venue settings.",
    # Halls / catalog
    "hall.manage": "Create, update and deactivate halls, schedules and maintenance.",
    "hall.delete": "Permanently delete a hall that has no bookings, quotations, reschedule history or hall-scoped expenses.",
    "hall.view": "View halls and their schedules.",
    "catalog.manage": "Manage event types, packages, services and pricing rules.",
    "catalog.view": "View event types, packages, services and pricing.",
    # Customers / quotations / bookings
    "customer.create": "Create customer records.",
    "customer.view": "View customer records.",
    "customer.view_sensitive": "View sensitive customer data (e.g. full National ID).",
    "quotation.create": "Create quotations.",
    "quotation.view": "View quotations.",
    "booking.create": "Create bookings.",
    "booking.view": "View bookings.",
    "booking.view_price": "View booking prices.",
    "booking.view_profit": "View venue profit on bookings.",
    "booking.update": "Update permitted booking details.",
    "booking.cancel": "Request/approve booking cancellation.",
    "booking.reschedule": "Reschedule bookings.",
    "booking.transfer": "Transfer a booking to another customer.",
    "booking.change_services": "Change services on a Confirmed booking (re-price, record delta).",
    # Contracts / operations
    "contract.manage": "Generate and manage contracts.",
    "contract.view": "View contracts.",
    "task.manage": "Create, edit, assign and reassign tasks.",
    "task.view": "View all tasks, event plans and task templates.",
    "task.view_assigned": "View tasks assigned to oneself.",
    "task.update_own": "Update the status of one's own assigned tasks.",
    # Finance
    "payment.record": "Record payments.",
    "payment.view": "View payment records.",
    "expense.record": "Record expenses.",
    "expense.approve": "Approve or reject expenses.",
    "refund.request": "Request a refund.",
    "refund.approve": "Approve refunds.",
    "finance.view": "View financial reports.",
    # Files / audit / backup
    "attachment.upload": "Upload attachments.",
    "attachment.download": "Download/preview attachments.",
    "attachment.delete": "Delete (soft-delete) attachments.",
    "audit.view": "View the audit log.",
    "backup.manage": "Create and restore database backups.",
    "report.view": "View operational reports.",
    "notification.view": "View in-app notifications.",
}

# Which permissions each seeded role gets. Owner gets everything.
# Least privilege: no role besides Owner is an administrator (SRS §2.2).
ROLE_PERMISSIONS: dict[str, list[str]] = {
    "Manager": [
        "settings.view", "hall.manage", "hall.delete", "hall.view", "catalog.manage", "catalog.view",
        "customer.create", "customer.view", "quotation.create", "quotation.view",
        "booking.create", "booking.view", "booking.view_price", "booking.update",
        "booking.cancel", "booking.reschedule", "booking.transfer",
        "booking.change_services",
        "contract.manage", "contract.view",
        "task.manage", "task.view", "task.view_assigned", "task.update_own",
        "user.manage",
        "payment.view", "expense.approve", "attachment.upload", "attachment.download",
        "attachment.delete", "audit.view", "report.view", "notification.view",
    ],
    "Receptionist": [
        "hall.view", "catalog.view", "customer.create", "customer.view",
        "quotation.create", "quotation.view", "booking.create", "booking.view",
        "booking.view_price", "booking.update", "contract.view",
        "task.view_assigned", "payment.view", "attachment.upload",
        "attachment.download", "report.view", "notification.view",
    ],
    "Accountant": [
        "hall.view", "catalog.view", "customer.view", "quotation.view",
        "booking.view", "booking.view_price", "contract.view",
        "payment.record", "payment.view", "expense.record", "expense.approve",
        "refund.request", "refund.approve", "finance.view",
        "attachment.upload", "attachment.download", "report.view",
        "notification.view",
    ],
    "Staff": [
        "task.view_assigned", "task.update_own", "notification.view",
    ],
}

# Demo accounts seeded by data/seed.py. Passwords are demo-only.
# Username -> (full name, role, password). Change before real use.
DEMO_USERS: dict[str, tuple[str, str, str]] = {
    "owner": ("Owner (Demo)", "Owner", "Owner123!"),
    "manager": ("Manager (Demo)", "Manager", "Manager123!"),
    "receptionist": ("Receptionist (Demo)", "Receptionist", "Reception123!"),
    "accountant": ("Accountant (Demo)", "Accountant", "Account123!"),
    "staff": ("Staff (Demo)", "Staff", "Staff123!"),
}

# ------------------------------------------------------------------ Phase 2
# EXAMPLE catalog data (SRS §3.6, §4). Real hall names, capacities,
# prices and opening hours are in NEITHER source document (GAP A-05),
# so these are clearly-marked EXAMPLES for demo/testing only — every
# name carries "(example)" and the UI labels them as such. The Owner can
# edit or deactivate all of them from the Halls & Pricing screens, and
# must replace them with the real venue data before real use.
# Money here is in EGP; seed.py converts to integer piasters.
EXAMPLE_HALLS: list[dict] = [
    {
        "name": "Main Hall (example)",
        "description": "Example large hall — replace with the real hall.",
        "capacity": 400,
        "base_price_egp": 15000,
        "status": "active",
        "facilities": ["Air conditioning", "Stage", "Sound system", "Lighting", "Tables", "Chairs"],
        "notes": "EXAMPLE DATA — not a real hall.",
        "weekly_hours": [{"weekday": d, "open": "12:00", "close": "23:00"} for d in range(7)],
    },
    {
        "name": "Garden Hall (example)",
        "description": "Example smaller hall — replace with the real hall.",
        "capacity": 150,
        "base_price_egp": 8000,
        "status": "active",
        "facilities": ["Air conditioning", "Garden view", "Sound system"],
        "notes": "EXAMPLE DATA — not a real hall.",
        "weekly_hours": [{"weekday": d, "open": "12:00", "close": "23:00"} for d in range(7)],
    },
]

EXAMPLE_EVENT_TYPES: list[dict] = [
    {"name": "Wedding", "default_duration_min": 300},
    {"name": "Engagement", "default_duration_min": 180},
    {"name": "Birthday", "default_duration_min": 180},
    {"name": "Graduation", "default_duration_min": 240},
    {"name": "Private Events", "default_duration_min": 120},
]

EXAMPLE_SERVICES: list[dict] = [
    {"name": "Photographer", "description": "Professional event photography.",
     "pricing_method": "per_event", "unit_price_egp": 3000},
    {"name": "DJ", "description": "DJ and music for the event.",
     "pricing_method": "per_event", "unit_price_egp": 2500},
    {"name": "Catering", "description": "Food and drinks, priced per guest.",
     "pricing_method": "per_guest", "unit_price_egp": 150},
    {"name": "Wedding Cake", "description": "Wedding cake.",
     "pricing_method": "per_event", "unit_price_egp": 2000},
    {"name": "Flowers & Decoration", "description": "Flowers and hall decoration.",
     "pricing_method": "per_event", "unit_price_egp": 4000},
    {"name": "Lighting & Sound", "description": "Extra lighting and sound, per hour.",
     "pricing_method": "per_hour", "unit_price_egp": 1000},
    {"name": "Screen & Photography Display", "description": "Screens and photo display.",
     "pricing_method": "per_event", "unit_price_egp": 1500},
    {"name": "Wedding Organization", "description": "Full event organization.",
     "pricing_method": "per_event", "unit_price_egp": 5000},
]

EXAMPLE_PACKAGES: list[dict] = [
    {
        "name": "Classic Wedding (example)",
        "description": "Example wedding package — replace with the real package.",
        "base_price_egp": 25000, "default_duration_min": 300,
        "min_duration_min": 180, "included_guests": 200,
        "extra_guest_price_egp": 100,
        "halls": ["Main Hall (example)", "Garden Hall (example)"],
        "event_types": ["Wedding"],
        "services": ["Photographer", "Flowers & Decoration"],
    },
    {
        "name": "Silver Engagement (example)",
        "description": "Example engagement package — replace with the real package.",
        "base_price_egp": 12000, "default_duration_min": 180,
        "min_duration_min": 120, "included_guests": 100,
        "extra_guest_price_egp": 75,
        "halls": ["Main Hall (example)", "Garden Hall (example)"],
        "event_types": ["Engagement"],
        "services": ["DJ"],
    },
]

# Weekend surcharge examples: Friday=4, Saturday=5 (Monday=0). 10% each.
EXAMPLE_PRICING_RULES: list[dict] = [
    {"name": "Friday surcharge (example)", "weekday": 4,
     "adjustment_type": "percent", "adjustment_value": 10},
    {"name": "Saturday surcharge (example)", "weekday": 5,
     "adjustment_type": "percent", "adjustment_value": 10},
]

# ------------------------------------------------------------------ Phase 3
# EXAMPLE customers (SRS §5). Clearly-marked EXAMPLES for demo/testing
# only — every name carries "(example)". No real personal data. The
# National IDs below are invented placeholders, not real IDs. One
# example quotation is created for the first customer by seed.py (a
# Draft quotation for the Main Hall, next Friday) so the
# quotation → booking flow is demoable.
EXAMPLE_CUSTOMERS: list[dict] = [
    {
        "full_name": "Ahmed Hassan (example)",
        "phone": "01012345678",
        "email": "ahmed.hassan@example.com",
        "address": "Benha, Qalyubia",
        "national_id": "29001010123456",
        "notes": "EXAMPLE DATA — not a real customer.",
    },
    {
        "full_name": "Mona Ali (example)",
        "phone": "01123456789",
        "email": "mona.ali@example.com",
        "address": "Benha, Qalyubia",
        "national_id": "29502020234567",
        "notes": "EXAMPLE DATA — not a real customer.",
    },
    {
        "full_name": "Karim Mostafa (example)",
        "phone": "01234567890",
        "email": "",
        "address": "Toukh, Qalyubia",
        "national_id": "",
        "notes": "EXAMPLE DATA — not a real customer.",
    },
]

# ------------------------------------------------------------------ Phase 4
# Default notification configuration (SRS §10): notification type ->
# recipient roles + priority. The Owner can change it; stored as JSON in
# the ``notification.config`` system setting. Employee-targeted types
# (task_assigned, task_due_soon, task_overdue) have no role mapping —
# they go to the assigned employee directly.
DEFAULT_NOTIFICATION_CONFIG: dict[str, dict] = {
    "new_booking": {"roles": ["Manager"], "priority": "normal"},
    "hold_expiring": {"roles": ["Manager", "Receptionist"], "priority": "high"},
    "booking_cancelled": {"roles": ["Manager"], "priority": "normal"},
    "booking_rescheduled": {"roles": ["Manager"], "priority": "normal"},
    "booking_services_changed": {"roles": ["Manager"], "priority": "normal"},
    "payment_received": {"roles": ["Accountant", "Manager"], "priority": "normal"},
    "installment_due": {"roles": ["Accountant"], "priority": "normal"},
    "event_approaching": {"roles": ["Manager"], "priority": "normal"},
    "task_due_soon": {"roles": [], "priority": "normal"},
    "task_overdue": {"roles": [], "priority": "high"},
    "task_assigned": {"roles": [], "priority": "normal"},
    "assignment_conflict": {"roles": ["Manager"], "priority": "high"},
    "contract_awaiting_signature": {"roles": ["Manager", "Owner"], "priority": "normal"},
    "hall_maintenance": {"roles": ["Manager"], "priority": "normal"},
}

# Default contract template (SRS §6). Stored as JSON in the
# ``contract.template.standard`` system setting; templates are settings
# rows, not a table (ARCHITECTURE_REVIEW §3.5). Placeholders must be
# members of models.CONTRACT_PLACEHOLDERS. The document is printable
# HTML (A-38: no PDF library is permitted yet).
DEFAULT_CONTRACT_TEMPLATE: dict[str, str] = {
    "name": "Standard Wedding Contract",
    "body": """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Wedding Hall Contract {{contract_number}}</title>
<style>body{font-family:Arial,sans-serif;margin:40px;color:#222}
h1{font-size:22px}h2{font-size:16px;margin-top:24px}
table{border-collapse:collapse;width:100%}
td,th{border:1px solid #ccc;padding:8px;text-align:left}
.sig{margin-top:48px;display:flex;justify-content:space-between}
.sig div{width:45%;border-top:1px solid #000;padding-top:6px}
</style></head><body>
<h1>Wedding Hall Booking Contract &mdash; {{contract_number}}</h1>
<p>Date: {{date}} &nbsp;|&nbsp; Venue: {{venue_name}}</p>
<h2>1. Parties</h2>
<p><strong>Venue:</strong> {{venue_name}}<br>
<strong>Customer:</strong> {{customer_name}}<br>
Phone: {{customer_phone}}<br>
Address: {{customer_address}}<br>
National ID: {{customer_national_id}}</p>
<h2>2. Event</h2>
<table>
<tr><th>Hall</th><td>{{hall_name}}</td></tr>
<tr><th>Event type</th><td>{{event_type}}</td></tr>
<tr><th>Package</th><td>{{package_name}}</td></tr>
<tr><th>Date</th><td>{{event_date}}</td></tr>
<tr><th>Time</th><td>{{event_start}} &ndash; {{event_end}}</td></tr>
<tr><th>Guests</th><td>{{guest_count}}</td></tr>
<tr><th>Booking</th><td>{{booking_number}}</td></tr>
</table>
<h2>3. Financial terms</h2>
<table>
<tr><th>Total amount</th><td>{{total_amount}}</td></tr>
<tr><th>Deposit required ({{deposit_percent}}%)</th><td>{{deposit_required}}</td></tr>
<tr><th>Deposit paid</th><td>{{deposit_paid}}</td></tr>
<tr><th>Outstanding</th><td>{{outstanding}}</td></tr>
</table>
<p>The booking is held for {{hold_hours}} hours; a deposit of
{{deposit_percent}}% confirms it. If the deposit is not recorded by the
end of the hold plus the {{grace_hours}}-hour grace period, the booking
expires and the time is released.</p>
<h2>4. Cancellation and refunds</h2>
<p>Cancellation follows the venue policy in force at confirmation.
Refunds, if any, are limited to amounts actually paid and follow the
refund policy; the original payment records are preserved.</p>
<h2>5. Signatures</h2>
<div class="sig"><div>Venue representative<br><br>Date: __________</div>
<div>Customer: {{customer_name}}<br><br>Date: __________</div></div>
</body></html>""",
    # Arabic mirror of the body above (Phase 8, NFR-005): same
    # placeholders, RTL. Used when the generating employee's language
    # is Arabic; the English body stays the default.
    "body_ar": """<!DOCTYPE html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<title>عقد حجز قاعة أفراح {{contract_number}}</title>
<style>body{font-family:Arial,sans-serif;margin:40px;color:#222}
h1{font-size:22px}h2{font-size:16px;margin-top:24px}
table{border-collapse:collapse;width:100%}
td,th{border:1px solid #ccc;padding:8px;text-align:right}
.sig{margin-top:48px;display:flex;justify-content:space-between}
.sig div{width:45%;border-top:1px solid #000;padding-top:6px}
</style></head><body>
<h1>عقد حجز قاعة أفراح &mdash; {{contract_number}}</h1>
<p>التاريخ: {{date}} &nbsp;|&nbsp; القاعة: {{venue_name}}</p>
<h2>1. الطرفان</h2>
<p><strong>القاعة:</strong> {{venue_name}}<br>
<strong>العميل:</strong> {{customer_name}}<br>
الهاتف: {{customer_phone}}<br>
العنوان: {{customer_address}}<br>
الرقم القومي: {{customer_national_id}}</p>
<h2>2. الحدث</h2>
<table>
<tr><th>القاعة</th><td>{{hall_name}}</td></tr>
<tr><th>نوع الحدث</th><td>{{event_type}}</td></tr>
<tr><th>الباقة</th><td>{{package_name}}</td></tr>
<tr><th>التاريخ</th><td>{{event_date}}</td></tr>
<tr><th>الوقت</th><td>{{event_start}} &ndash; {{event_end}}</td></tr>
<tr><th>الضيوف</th><td>{{guest_count}}</td></tr>
<tr><th>الحجز</th><td>{{booking_number}}</td></tr>
</table>
<h2>3. الشروط المالية</h2>
<table>
<tr><th>المبلغ الإجمالي</th><td>{{total_amount}}</td></tr>
<tr><th>المقدم المطلوب ({{deposit_percent}}%)</th><td>{{deposit_required}}</td></tr>
<tr><th>المقدم المدفوع</th><td>{{deposit_paid}}</td></tr>
<tr><th>المتبقي</th><td>{{outstanding}}</td></tr>
</table>
<p>يُحفظ الحجز لمدة {{hold_hours}} ساعة؛ ويسدد مقدم بنسبة
{{deposit_percent}}% لتأكيد الحجز. وإذا لم يُسجَّل المقدم بنهاية
فترة الحفظ مضافًا إليها فترة السماح البالغة {{grace_hours}} ساعة، ينتهي
الحجز ويُحرَّر الوقت.</p>
<h2>4. الإلغاء والاسترداد</h2>
<p>يخضع الإلغاء لسياسة القاعة السارية عند التأكيد.
وتقتصر الاستردادات، إن وُجدت، على المبالغ المدفوعة فعلًا وتتبع
سياسة الاسترداد؛ وتُحفظ سجلات الدفع الأصلية.</p>
<h2>5. التوقيعات</h2>
<div class="sig"><div>ممثل القاعة<br><br>التاريخ: __________</div>
<div>العميل: {{customer_name}}<br><br>التاريخ: __________</div></div>
</body></html>""",
}

# ------------------------------------------------------------------ Phase 6
# Dashboard widgets per role (SRS §13.2). The Owner controls which
# widgets each role sees (stored as JSON in the ``dashboard.widgets``
# system setting); there is no per-individual customization. Widget
# keys are stable identifiers consumed by logic/report_service.py.
DEFAULT_DASHBOARD_WIDGETS: dict[str, list[str]] = {
    "Owner": ["revenue", "profit", "bookings", "expenses", "pending_bookings",
              "upcoming_events", "refunds"],
    "Manager": ["upcoming_events", "hall_status", "staff_overview",
                "tasks_overview", "conflicts", "maintenance",
                "pending_bookings"],
    "Receptionist": ["new_bookings", "customers", "quotations",
                     "holds_expiring", "pending_bookings"],
    "Accountant": ["payments", "installments_due", "expenses",
                   "refunds", "financial_summary"],
    "Staff": ["my_tasks", "upcoming_tasks", "overdue_tasks"],
}

# Example task template (SRS §7.2). Clearly-marked EXAMPLE for demo only.
# Applies to Wedding events with the Classic Wedding package. Due offsets
# are minutes BEFORE the event start.
EXAMPLE_TASK_TEMPLATES: list[dict] = [
    {
        "name": "Wedding Day Checklist (example)",
        "event_type": "Wedding",
        "package": "Classic Wedding (example)",
        "items": [
            {"name": "Confirm hall setup and seating",
             "description": "Check tables, chairs and stage layout against the booking.",
             "due_offset_min": 180, "priority": "high"},
            {"name": "Sound and lighting check",
             "description": "Test microphones, speakers and hall lighting.",
             "due_offset_min": 120, "priority": "high"},
            {"name": "Photographer arrival",
             "description": "Confirm the photographer is on site before guests arrive.",
             "due_offset_min": 60, "priority": "medium"},
            {"name": "Welcome guests",
             "description": "Greet guests at the entrance and guide them to their seats.",
             "due_offset_min": 0, "priority": "medium"},
        ],
    },
]
