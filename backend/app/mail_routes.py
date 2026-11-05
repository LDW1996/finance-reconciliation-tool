"""Compose mail routes in their compatibility order."""
from fastapi import APIRouter, Depends, Response
from . import mail_routes_storage as storage
from . import mail_routes_messages as messages
from . import mail_routes_ledger as ledger
from . import mail_routes_customers as customers
from . import mail_routes_matching as matching
from . import mail_routes_diagnostics as diagnostics


def no_cache(response: Response):
    response.headers["Cache-Control"] = "no-store"


def create_router(require_local):
    router = APIRouter(prefix="/api/mail", dependencies=[Depends(require_local), Depends(no_cache)])
    messages.register(router)
    storage.register(router)
    diagnostics.register(router)
    ledger.register(router)
    customers.register(router)
    matching.register(router)
    order = [
        'recognition_mode',
        'status',
        'storage',
        'configure_storage',
        'maintenance_status',
        'maintenance_history',
        'maintenance_preview',
        'maintenance_optimize',
        'cleanup_backups',
        'cleanup_preview',
        'cleanup_execute',
        'cleanup_restore',
        'attachment_preview',
        'folders',
        'scan',
        'poll',
        'messages',
        'diagnostics_status',
        'diagnostics_export',
        'message_page',
        'open_original',
        'message',
        'mail_receipts',
        'clear',
        'analyze',
        'import_mail',
        'ledger',
        'online_config',
        'configure_online',
        'refresh_online',
        'writeback_online',
        'online_history',
        'customer_online_config',
        'configure_customer_online',
        'refresh_customer_online',
        'customer_mapping_preview',
        'customer_mapping',
        'customer_mapping_setting',
        'import_customer_mapping',
        'import_ledger',
        'matching',
        'matching_state',
        'retry_matching',
        'matching_snapshot',
        'ledger_preview',
        'review',
        'cashier_package',
        'export',
    ]
    router.routes.sort(key=lambda route: order.index(route.name))
    return router
