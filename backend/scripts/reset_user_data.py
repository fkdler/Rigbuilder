"""Offline user-data reset; run with the backend stopped and a verified backup.

python -m scripts.reset_user_data --apply --admin-username <name>
Password is read from stdin when piped, otherwise prompted without echo.
Only public user/session/trace tables are touched; Truth and migrations are preserved.
"""
import argparse
import getpass
import sys
from sqlalchemy import text
from app.db.session import SessionLocal
from app.models import User
from app.core.security import hash_password
from app.services.auth import normalize_username

TABLES = ('tool_call', 'agent_message', 'query_event', 'agent_run',
          'conversation_context_snapshot', 'user_constraint', 'conversation_message',
          'query_job', 'conversation', 'auth_session', 'app_user')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--admin-username', required=True)
    args = parser.parse_args()
    username = normalize_username(args.admin_username)
    with SessionLocal() as session:
        counts = {name: session.scalar(text(f'SELECT count(*) FROM public.{name}')) for name in TABLES}
        print('Before:', counts)
        if not args.apply:
            return
        active = session.scalar(text("SELECT count(*) FROM public.query_job WHERE status IN ('queued','running','cancel_requested')"))
        if active:
            raise SystemExit('Stop the backend and resolve active jobs before resetting.')
        password = getpass.getpass('Admin password: ') if sys.stdin.isatty() else sys.stdin.readline().rstrip('\r\n')
        if not password or len(password) > 256:
            raise SystemExit('A non-empty password of at most 256 characters is required.')
        for name in TABLES:
            session.execute(text(f'DELETE FROM public.{name}'))
        session.add(User(username=username, password_hash=hash_password(password), role='admin', status='active'))
        session.commit()
        print('After:', {name: session.scalar(text(f'SELECT count(*) FROM public.{name}')) for name in TABLES})

if __name__ == '__main__':
    main()
