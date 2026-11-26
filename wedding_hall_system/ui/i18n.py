"""Bilingual UI strings (Phase 6, SRS §16.3).

Arabic is the venue default (``venue.default_language`` = 'ar') and
the default for Ahmed; English is the second language. Every string
the Phase 6 screens render comes from ``t(lang, key)`` — the toggle
in the main view switches the language state, persists the per-user
preference (logic/preferences.py), and re-renders all dynamic content
in the new language. Static labels of the Phase 6 tabs are built from
these tables at build time (Arabic by default) and updated through
``gr.update(label=...)`` by the toggle handler; tab titles themselves
are bilingual ("Dashboard • لوحة المتابعة") because Gradio cannot
rename a tab at runtime.

``dir_for`` returns the layout direction: Arabic is RTL, English is
LTR. Dynamic Markdown/HTML output is wrapped in
``<div dir="rtl">`` / ``<div dir="ltr">`` so the reading direction of
the rendered content actually switches with the language.
"""
from __future__ import annotations

LANGUAGES = ("ar", "en")

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        # -- chrome
        "app.title": "Wedding Hall Management System",
        "app.login_hint": "Please log in.",
        "login.username": "Username",
        "login.password": "Password",
        "login.button": "Log in",
        "login.logout": "Log out",
        "login.refresh": "Refresh status",
        "login.welcome": "Welcome",
        "login.roles": "Roles",
        "login.permissions": "Permissions",
        "login.change_password": "Change password",
        "login.current_password": "Current password",
        "login.new_password": "New password (8+ characters, letter + digit)",
        "login.language": "Language / اللغة",
        "common.show": "Show",
        "common.refresh": "Refresh",
        "common.search": "Search",
        "common.no_records": "No records yet.",
        "common.page": "Page",
        "common.total": "Total",
        # -- dashboard
        "dashboard.title": "Dashboard",
        "dashboard.pending": "Pending bookings",
        "dashboard.hold_remaining": "Hold remaining",
        "dashboard.missing_deposit": "Missing deposit",
        "dashboard.warnings": "Warnings",
        "dashboard.revenue": "Recognised revenue",
        "dashboard.profit": "Profit",
        "dashboard.bookings": "Bookings",
        "dashboard.expenses": "Expenses",
        "dashboard.upcoming_events": "Upcoming events",
        "dashboard.hall_status": "Hall status",
        "dashboard.staff_overview": "Staff overview",
        "dashboard.tasks_overview": "Tasks overview",
        "dashboard.conflicts": "Assignment conflicts",
        "dashboard.maintenance": "Maintenance",
        "dashboard.new_bookings": "New bookings",
        "dashboard.customers": "Customers",
        "dashboard.quotations": "Quotations",
        "dashboard.holds_expiring": "Holds expiring soon",
        "dashboard.payments": "Payments",
        "dashboard.installments_due": "Installments due / overdue",
        "dashboard.refunds": "Refunds",
        "dashboard.financial_summary": "Financial summary",
        "dashboard.my_tasks": "My tasks",
        "dashboard.upcoming_tasks": "Upcoming tasks",
        "dashboard.overdue_tasks": "Overdue tasks",
        # -- search
        "search.title": "Search",
        "search.global": "Global search",
        "search.global_hint": "One box across customers, bookings, contracts, payments, staff, files and your own tasks.",
        "search.term": "Search term",
        "search.customers": "Customers",
        "search.bookings": "Bookings",
        "search.contracts": "Contracts",
        "search.payments": "Payments",
        "search.staff": "Staff",
        "search.files": "Files",
        "search.tasks": "Tasks",
        "search.events": "Events",
        "search.status": "Status",
        "search.hall": "Hall",
        "search.date_from": "From date (YYYY-MM-DD)",
        "search.date_to": "To date (YYYY-MM-DD)",
        "search.method": "Method",
        # -- files
        "files.title": "Files",
        "files.upload": "Upload file",
        "files.entity_type": "Attach to",
        "files.entity_id": "Record ID",
        "files.category": "Category",
        "files.list": "List files",
        "files.download": "Download",
        "files.preview": "Preview",
        "files.delete": "Delete file",
        "files.attachment_id": "Attachment ID",
        # -- reports
        "reports.title": "Reports",
        "reports.bookings": "Booking report",
        "reports.hall_utilization": "Hall utilization",
        "reports.customers": "Customer report",
        "reports.packages": "Package performance",
        "reports.staff": "Staff performance",
        "reports.events": "Event report",
        "reports.cancellations": "Cancellation analysis",
        "reports.financial": "Financial report",
        "reports.run": "Run report",
        "reports.export_csv": "Export CSV",
        "reports.basis": "Basis",
        "reports.comparison": "Previous period",
        "audit.title": "Audit log",
        "audit.action": "Action",
        "audit.entity": "Entity",
        "audit.employee": "Employee ID",
        "audit.search": "Search audit log",
    },
    "ar": {
        # -- chrome
        "app.title": "نظام إدارة قاعات الأفراح",
        "app.login_hint": "يرجى تسجيل الدخول.",
        "login.username": "اسم المستخدم",
        "login.password": "كلمة المرور",
        "login.button": "تسجيل الدخول",
        "login.logout": "تسجيل الخروج",
        "login.refresh": "تحديث الحالة",
        "login.welcome": "أهلاً",
        "login.roles": "الأدوار",
        "login.permissions": "الصلاحيات",
        "login.change_password": "تغيير كلمة المرور",
        "login.current_password": "كلمة المرور الحالية",
        "login.new_password": "كلمة المرور الجديدة (٨ أحرف فأكثر، حرف + رقم)",
        "login.language": "اللغة / Language",
        "common.show": "عرض",
        "common.refresh": "تحديث",
        "common.search": "بحث",
        "common.no_records": "لا توجد سجلات بعد.",
        "common.page": "الصفحة",
        "common.total": "الإجمالي",
        # -- dashboard
        "dashboard.title": "لوحة المتابعة",
        "dashboard.pending": "الحجوزات المعلّقة",
        "dashboard.hold_remaining": "المتبقي من الحجز المؤقت",
        "dashboard.missing_deposit": "المقدم المتبقي",
        "dashboard.warnings": "تنبيهات",
        "dashboard.revenue": "الإيراد المعترف به",
        "dashboard.profit": "الربح",
        "dashboard.bookings": "الحجوزات",
        "dashboard.expenses": "المصروفات",
        "dashboard.upcoming_events": "الأحداث القادمة",
        "dashboard.hall_status": "حالة القاعات",
        "dashboard.staff_overview": "نظرة على الموظفين",
        "dashboard.tasks_overview": "نظرة على المهام",
        "dashboard.conflicts": "تعارضات التعيين",
        "dashboard.maintenance": "الصيانة",
        "dashboard.new_bookings": "حجوزات جديدة",
        "dashboard.customers": "العملاء",
        "dashboard.quotations": "عروض الأسعار",
        "dashboard.holds_expiring": "حجوزات مؤقتة تنتهي قريباً",
        "dashboard.payments": "المدفوعات",
        "dashboard.installments_due": "أقساط مستحقة / متأخرة",
        "dashboard.refunds": "المبالغ المستردة",
        "dashboard.financial_summary": "الملخص المالي",
        "dashboard.my_tasks": "مهامي",
        "dashboard.upcoming_tasks": "مهام قادمة",
        "dashboard.overdue_tasks": "مهام متأخرة",
        # -- search
        "search.title": "البحث",
        "search.global": "بحث عام",
        "search.global_hint": "بحث واحد في العملاء والحجوزات والعقود والمدفوعات والموظفين والملفات ومهامك.",
        "search.term": "كلمة البحث",
        "search.customers": "العملاء",
        "search.bookings": "الحجوزات",
        "search.contracts": "العقود",
        "search.payments": "المدفوعات",
        "search.staff": "الموظفون",
        "search.files": "الملفات",
        "search.tasks": "المهام",
        "search.events": "الأحداث",
        "search.status": "الحالة",
        "search.hall": "القاعة",
        "search.date_from": "من تاريخ (YYYY-MM-DD)",
        "search.date_to": "إلى تاريخ (YYYY-MM-DD)",
        "search.method": "طريقة الدفع",
        # -- files
        "files.title": "الملفات",
        "files.upload": "رفع ملف",
        "files.entity_type": "إرفاق بـ",
        "files.entity_id": "رقم السجل",
        "files.category": "الفئة",
        "files.list": "عرض الملفات",
        "files.download": "تنزيل",
        "files.preview": "معاينة",
        "files.delete": "حذف الملف",
        "files.attachment_id": "رقم المرفق",
        # -- reports
        "reports.title": "التقارير",
        "reports.bookings": "تقرير الحجوزات",
        "reports.hall_utilization": "استغلال القاعات",
        "reports.customers": "تقرير العملاء",
        "reports.packages": "أداء الباقات",
        "reports.staff": "أداء الموظفين",
        "reports.events": "تقرير الأحداث",
        "reports.cancellations": "تحليل الإلغاءات",
        "reports.financial": "التقرير المالي",
        "reports.run": "تشغيل التقرير",
        "reports.export_csv": "تصدير CSV",
        "reports.basis": "أساس الحساب",
        "reports.comparison": "الفترة السابقة",
        "audit.title": "سجل التدقيق",
        "audit.action": "الإجراء",
        "audit.entity": "الكيان",
        "audit.employee": "رقم الموظف",
        "audit.search": "بحث في سجل التدقيق",
    },
}

# Widget keys -> i18n keys for dashboard widget titles.
WIDGET_TITLES: dict[str, str] = {
    "revenue": "dashboard.revenue",
    "profit": "dashboard.profit",
    "bookings": "dashboard.bookings",
    "expenses": "dashboard.expenses",
    "pending_bookings": "dashboard.pending",
    "upcoming_events": "dashboard.upcoming_events",
    "refunds": "dashboard.refunds",
    "hall_status": "dashboard.hall_status",
    "staff_overview": "dashboard.staff_overview",
    "tasks_overview": "dashboard.tasks_overview",
    "conflicts": "dashboard.conflicts",
    "maintenance": "dashboard.maintenance",
    "new_bookings": "dashboard.new_bookings",
    "customers": "dashboard.customers",
    "quotations": "dashboard.quotations",
    "holds_expiring": "dashboard.holds_expiring",
    "payments": "dashboard.payments",
    "installments_due": "dashboard.installments_due",
    "financial_summary": "dashboard.financial_summary",
    "my_tasks": "dashboard.my_tasks",
    "upcoming_tasks": "dashboard.upcoming_tasks",
    "overdue_tasks": "dashboard.overdue_tasks",
}

REPORT_TITLES: dict[str, str] = {
    "bookings": "reports.bookings",
    "hall_utilization": "reports.hall_utilization",
    "customers": "reports.customers",
    "packages": "reports.packages",
    "staff": "reports.staff",
    "events": "reports.events",
    "cancellations": "reports.cancellations",
    "financial": "reports.financial",
}


def t(lang: str, key: str) -> str:
    """Translate ``key``; falls back to English, then to the key itself
    (a missing translation is visible, never silent)."""
    lang = lang if lang in STRINGS else "en"
    return STRINGS[lang].get(key) or STRINGS["en"].get(key) or key


def dir_for(lang: str) -> str:
    """Layout direction: Arabic is RTL, English is LTR (SRS §16.3)."""
    return "rtl" if lang == "ar" else "ltr"


def dir_wrap(lang: str, markdown: str) -> str:
    """Wrap dynamically rendered Markdown in a directional container so
    the reading direction of the content switches with the language."""
    return f'<div dir="{dir_for(lang)}">\n\n{markdown}\n\n</div>'
