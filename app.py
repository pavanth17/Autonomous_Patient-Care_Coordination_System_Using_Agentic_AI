"""CareSync AI - application bootstrap, entry point and admin CLI.

This is the only module that wires the framework together.  It replaced the
previous ``app.py`` + ``main.py`` + ``database.py`` trio: the Flask app, the
Socket.IO server, the schema upgrade step and the command-line interface all
live here now.

Run the server::

    python app.py

Run an administrative command::

    python app.py init-db --doctors 25 --nurses 40 --patients 60
    python app.py rebalance
    python app.py check-data
    python app.py reset-password DOC0001 secret
"""

import argparse
import logging
import os
import sys
import threading
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, session
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

try:                                                   # optional LLM support
    from groq import Groq
except ImportError:                                    # pragma: no cover
    Groq = None

from flask_socketio import SocketIO, join_room
from sqlalchemy import inspect, text

# ``models`` owns the ``db`` object now, so there is no separate database module.
from models import SCHEMA_UPGRADES, Patient, StaffMember, db

log = logging.getLogger(__name__)
_socket_presence_lock = threading.Lock()
_socket_staff_by_sid = {}
_staff_sids = {}


def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


configure_logging()


# ===========================================================================
# Application factory
# ===========================================================================
def create_app():
    """Build and configure the Flask application."""
    app = Flask(__name__)
    app.secret_key = os.environ.get('SESSION_SECRET', 'dev-session-secret')
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    default_db = os.path.join(os.path.dirname(__file__), 'patient_care_dev.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = (
        os.environ.get('DATABASE_URL') or f'sqlite:///{default_db}')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_recycle': 300, 'pool_pre_ping': True}
    if app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite'):
        app.config['SQLALCHEMY_ENGINE_OPTIONS']['connect_args'] = {
            'timeout': 15, 'check_same_thread': False}

    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(
        minutes=int(os.environ.get('SESSION_LIFETIME_MINUTES', 15)))
    app.config['SESSION_REFRESH_EACH_REQUEST'] = True
    # Template auto-reload keeps edits visible without a restart in dev.
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.jinja_env.cache = None

    if os.environ.get('PRODUCTION') in ('1', 'true', 'True'):
        if not os.environ.get('SESSION_SECRET'):
            raise RuntimeError(
                'SESSION_SECRET environment variable must be set in production')
        app.config['SESSION_COOKIE_SECURE'] = True
        app.config['SESSION_COOKIE_HTTPONLY'] = True
        app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
        log.info('Running in PRODUCTION mode with secure settings')
    else:
        log.info('Running in development mode')

    _init_ai(app)

    db.init_app(app)
    with app.app_context():
        db.create_all()
        ensure_schema()
        log.info('Database ready')

    socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading',
                        logger=False, engineio_logger=False)
    app.socketio = socketio
    return app


def _init_ai(app):
    """Configure the optional Groq client; the app runs fine without it."""
    api_key = os.environ.get('GROQ_API_KEY')
    global groq_client
    groq_client = None

    if Groq is None:
        log.info('groq not installed; AI features disabled')
        return
    if not api_key:
        log.info('GROQ_API_KEY not set; AI features disabled')
        return

    try:
        groq_client = Groq(api_key=api_key)
        model_name = os.environ.get(
            'GROQ_MODEL', 'llama-3.3-70b-versatile')
        app.config['AI_MODEL_NAME'] = model_name
        log.info('Groq API initialised with model %s', model_name)
    except Exception as exc:
        groq_client = None
        log.error('Failed to initialise Groq API: %s', exc)


def generate_ai_response(messages):
    """Return a Groq chat completion, or None when AI is not configured."""
    if groq_client is None:
        return None

    completion = groq_client.chat.completions.create(
        model=app.config.get('AI_MODEL_NAME', 'llama-3.3-70b-versatile'),
        messages=messages,
    )
    if not completion.choices:
        return None
    return completion.choices[0].message.content


def ensure_schema():
    """Add columns introduced after the first release to existing databases.

    ``db.create_all()`` only creates missing *tables*; it never adds columns to
    one that already exists.  This walks ``models.SCHEMA_UPGRADES`` and issues
    an ``ALTER TABLE`` for anything missing, so a long-lived SQLite demo file
    keeps working after an upgrade.
    """
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())

    for table, columns in SCHEMA_UPGRADES.items():
        if table not in existing_tables:
            continue
        present = {c['name'] for c in inspector.get_columns(table)}
        for column, ddl in columns:
            if column in present:
                continue
            try:
                db.session.execute(text(
                    f'ALTER TABLE {table} ADD COLUMN {column} {ddl}'))
                log.info('Schema upgrade: added %s.%s', table, column)
            except Exception as exc:
                log.error('Schema upgrade failed for %s.%s: %s',
                          table, column, exc)
    db.session.commit()


app = create_app()
socketio = app.socketio


# ===========================================================================
# Socket.IO handlers
# ===========================================================================
@socketio.on('connect')
def handle_connect(auth=None):
    """Join the member's private room and flag their dashboard as open.

    Marking presence here is what allows the monitor to stay quiet while no
    dashboard is being watched.
    """
    from flask import request
    from routing import mark_dashboard_open

    staff_id = session.get('staff_id')
    if staff_id:
        join_room(f'staff_{staff_id}')
        with _socket_presence_lock:
            _socket_staff_by_sid[request.sid] = staff_id
            _staff_sids.setdefault(staff_id, set()).add(request.sid)
        mark_dashboard_open(staff_id)
        log.info('Socket connected: staff %s (sid=%s)', staff_id, request.sid)
    else:
        log.info('Socket connected without a staff session (sid=%s)', request.sid)


@socketio.on('disconnect')
def handle_disconnect(*args):
    """Clear dashboard presence when the last client for a member leaves."""
    from flask import request
    from routing import mark_dashboard_closed

    sid = getattr(request, 'sid', None)
    with _socket_presence_lock:
        staff_id = _socket_staff_by_sid.pop(sid, None)
        if staff_id is None:
            staff_id = session.get('staff_id')
        staff_sids = _staff_sids.get(staff_id, set())
        if sid:
            staff_sids.discard(sid)
        has_open_socket = bool(staff_sids)
        if not has_open_socket:
            _staff_sids.pop(staff_id, None)

    if staff_id:
        if has_open_socket:
            mark_dashboard_open(staff_id)
        else:
            mark_dashboard_closed(staff_id)
    log.info('Socket disconnected (sid=%s)', sid or '?')


@socketio.on('dashboard_heartbeat')
def handle_dashboard_heartbeat(data=None):
    """Refresh presence so a long-lived dashboard never times out."""
    from routing import mark_dashboard_open

    staff_id = session.get('staff_id')
    if staff_id:
        mark_dashboard_open(staff_id)


# ===========================================================================
# Command line interface
# ===========================================================================
def bootstrap_ward():
    """Ensure the ward is fully routed before the server starts serving."""
    from routing import router

    with app.app_context():
        routed = router.route_unassigned_patients()
        moved = router.rebalance_workload()
        if routed or moved:
            log.info('Bootstrap routing: %d patients assigned, %d rebalanced',
                     routed, moved)
        return routed + moved


def cmd_init_db(args):
    from services import initialize_synthetic_data

    with app.app_context():
        if args.delete_db:
            db.drop_all()
            log.info('Dropped all tables')
        db.create_all()
        ensure_schema()
        initialize_synthetic_data(
            num_doctors=args.doctors, num_nurses=args.nurses,
            num_patients=args.patients, num_discharged=args.discharged)
        log.info('Database initialised')


def cmd_rebalance(args):
    from routing import router, workload_report

    with app.app_context():
        router.route_unassigned_patients()
        moved = router.rebalance_workload()
        report = workload_report()
        print(f'Moved {moved} patient(s). Minimum is '
              f'{report["minimum"]} patients per doctor.')
        for row in report['short_fall']:
            print(f'  BELOW MINIMUM: {row["staff"].full_name} '
                  f'({row["staff"].staff_id}) has {row["patients"]}')
        if not report['short_fall']:
            print('  All doctors meet the minimum.')


def cmd_check_data(args):
    from routing import workload_report, monitoring_status

    with app.app_context():
        report = workload_report()
        status = monitoring_status()
        print('CareSync status')
        print('-' * 46)
        print(f"  Doctors        : {len(report['doctors'])}")
        print(f"  Nurses         : {len(report['nurses'])}")
        print(f"  Patients       : {Patient.query.count()}")
        print(f"  Min per doctor : {report['minimum']}")
        print(f"  Below minimum  : {len(report['short_fall'])}")
        print(f"  Miss threshold : {status['max_consecutive_misses']} consecutive")
        print(f"  Ack window     : {status['ack_window_seconds']}s")
        print(f"  Dashboards open: {status['dashboards_open'] or 'none'}")


def cmd_run_analysis(args):
    import json
    from services import analyze_all_patients

    with app.app_context():
        results = analyze_all_patients(create_alerts=False)
        print(json.dumps(results, indent=2, default=str))


def cmd_reset_password(args):
    with app.app_context():
        staff = StaffMember.query.filter_by(staff_id=args.staff_id).first()
        if not staff:
            print(f'Staff not found: {args.staff_id}')
            sys.exit(1)
        staff.set_password(args.new_password)
        db.session.commit()
        print(f'Password updated for {staff.staff_id}')


def build_parser():
    parser = argparse.ArgumentParser(
        description='CareSync AI management CLI',
        epilog='With no arguments the web server is started.')
    sub = parser.add_subparsers(dest='command')

    init = sub.add_parser('init-db', help='Initialise the database with demo data')
    init.add_argument('--doctors', type=int, default=25)
    init.add_argument('--nurses', type=int, default=40)
    init.add_argument('--patients', type=int, default=60)
    init.add_argument('--discharged', type=int, default=15)
    init.add_argument('--delete-db', action='store_true')
    init.set_defaults(func=cmd_init_db)

    rebalance = sub.add_parser(
        'rebalance', help='Give every doctor at least the minimum patient count')
    rebalance.set_defaults(func=cmd_rebalance)

    check = sub.add_parser('check-data', help='Summarise ward state')
    check.set_defaults(func=cmd_check_data)

    analysis = sub.add_parser('run-analysis', help='Print risk analysis as JSON')
    analysis.set_defaults(func=cmd_run_analysis)

    password = sub.add_parser('reset-password', help='Set a staff password')
    password.add_argument('staff_id')
    password.add_argument('new_password')
    password.set_defaults(func=cmd_reset_password)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command:
        args.func(args)
        return

    # Default: run the web server.
    import services
    services.train_demo_model()
    bootstrap_ward()

    from routing import start_monitor
    start_monitor()

    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 5000))
    print(f'CareSync AI listening on http://{host}:{port}')
    socketio.run(app, host=host, port=port, debug=False,
                 allow_unsafe_werkzeug=True)


if __name__ == '__main__':
    main()




# Import the HTTP layer last: routes.py needs ``app`` and ``socketio`` above.
import routes  # noqa: E402,F401  (registers every route on the app)
