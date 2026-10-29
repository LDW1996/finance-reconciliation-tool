"""Mail-only frozen dependency checks; does not connect to a mailbox."""
from .bundle_probe import check_msg_parser, check_outlook_components, write_report as _write

def check_server_configuration():
    import uvicorn
    config=uvicorn.Config('app.mail_main:app',log_config=None)
    config.load()
    config.setup_event_loop()

def write_report(path,checks=None):
    return _write(path,checks if checks is not None else {'server_configuration':check_server_configuration,'msg_parser':check_msg_parser,'outlook_components':check_outlook_components})
