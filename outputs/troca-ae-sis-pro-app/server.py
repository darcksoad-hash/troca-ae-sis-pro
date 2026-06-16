from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import base64
import gzip
import hashlib
import hmac
import json
import os
import secrets
import smtplib
import sqlite3
import subprocess
import threading
import time
import traceback
import uuid
import zipfile
from datetime import date, datetime
from decimal import Decimal
from email.message import EmailMessage

ROOT = Path(__file__).resolve().parent
STORAGE_ROOT = Path(os.environ.get("APP_STORAGE_ROOT", ROOT)).resolve()
PUBLIC = ROOT / "public"
UPLOADS = STORAGE_ROOT / "uploads"
DATA = STORAGE_ROOT / "data"
BACKUPS = STORAGE_ROOT / "backups"
DB_PATH = DATA / "troca_ae.db"
PENDING_RESTORE = DATA / "restore-pending.json"
SCHEMA_PATH = ROOT / "schema.sql"
POSTGRES_SCHEMA_PATH = ROOT / "schema.postgresql.sql"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
APP_URL = os.environ.get("APP_URL", "").strip().rstrip("/")
DB_ENGINE = "postgres" if DATABASE_URL.startswith(("postgres://", "postgresql://")) else "sqlite"
SESSIONS = {}
SESSION_SECONDS = 8 * 60 * 60
LOCK_ATTEMPTS = 5
LOCK_SECONDS = 15 * 60
PG_CONN = None
PG_LOCK = threading.RLock()
DASHBOARD_CACHE = {"expires_at": 0, "data": None}
DASHBOARD_CACHE_SECONDS = 20


def session_secret():
    return os.environ.get("SESSION_SECRET") or os.environ.get("SECRET_KEY") or DATABASE_URL or "troca-ae-local-session-secret"


def b64url_encode(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_session_token(user_id, expires_at):
    payload = json.dumps({"user_id": user_id, "exp": int(expires_at)}, separators=(",", ":")).encode("utf-8")
    payload_b64 = b64url_encode(payload)
    signature = hmac.new(session_secret().encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"v1.{payload_b64}.{b64url_encode(signature)}"


def verify_session_token(token):
    try:
        if not token.startswith("v1."):
            return None
        _, payload_b64, signature_b64 = token.split(".", 2)
        expected = hmac.new(session_secret().encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(b64url_decode(signature_b64), expected):
            return None
        data = json.loads(b64url_decode(payload_b64).decode("utf-8"))
        if int(data.get("exp") or 0) < int(time.time()):
            return None
        return data
    except Exception:
        return None

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None


def uid(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def seconds_from_now(seconds):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + seconds))


def today():
    return time.strftime("%Y-%m-%d")


def add_months(value, months):
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return value
    month = parsed.month - 1 + months
    year = parsed.year + month // 12
    month = month % 12 + 1
    days = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    day = min(parsed.day, days[month - 1])
    return f"{year:04d}-{month:02d}-{day:02d}"


def slug(text):
    value = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(text))
    while "--" in value:
        value = value.replace("--", "-")
    return value.strip("-")


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return f"{salt}${digest}"


def check_password(password, stored):
    salt, digest = stored.split("$", 1)
    return hmac.compare_digest(password_hash(password, salt).split("$", 1)[1], digest)


def password_strength_error(password):
    value = password or ""
    if len(value) < 8:
        return "A senha precisa ter pelo menos 8 caracteres."
    if not any(ch.isalpha() for ch in value) or not any(ch.isdigit() for ch in value):
        return "A senha precisa ter letras e numeros."
    return ""


def token_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def app_url_from_request(handler):
    if APP_URL:
        return APP_URL
    proto = handler.headers.get("X-Forwarded-Proto", "http")
    host = handler.headers.get("Host", "127.0.0.1:5050")
    return f"{proto}://{host}"


def smtp_configured():
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def send_email(to_email, subject, body):
    try:
        if not smtp_configured():
            print(f"[email nao configurado] Para: {to_email} | {subject}\n{body}", flush=True)
            return False
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER")
        msg["To"] = to_email
        msg.set_content(body)
        host = os.environ["SMTP_HOST"]
        port = int(os.environ.get("SMTP_PORT", "587"))
        use_ssl = os.environ.get("SMTP_SSL", "").lower() in ("1", "true", "yes")
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=20) as server:
                server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as server:
                server.starttls()
                server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
                server.send_message(msg)
        return True
    except Exception as error:
        print(f"[falha ao enviar email] {to_email}: {error}", flush=True)
        return False


def clear_runtime_caches():
    DASHBOARD_CACHE["expires_at"] = 0
    DASHBOARD_CACHE["data"] = None


def is_integrity_error(error):
    return isinstance(error, sqlite3.IntegrityError) or bool(psycopg and isinstance(error, psycopg.IntegrityError))


class EmptyCursor:
    def fetchall(self):
        return []

    def fetchone(self):
        return None


def pg_sql(sql):
    value = sql.replace("?", "%s")
    value = value.replace("MAX(stock - %s, 0)", "GREATEST(stock - %s, 0)")
    value = value.replace("updated_at=CURRENT_TIMESTAMP", "updated_at=(CURRENT_TIMESTAMP::TEXT)")
    stripped = value.strip()
    if stripped.upper().startswith("INSERT OR IGNORE INTO "):
        value = value.replace("INSERT OR IGNORE INTO ", "INSERT INTO ", 1)
        value = f"{value} ON CONFLICT DO NOTHING"
    return value


def split_sql_script(script):
    statements = []
    current = []
    in_string = False
    quote = ""
    for ch in script:
        current.append(ch)
        if ch in ("'", '"'):
            if not in_string:
                in_string = True
                quote = ch
            elif quote == ch:
                in_string = False
        if ch == ";" and not in_string:
            statement = "".join(current).strip().rstrip(";")
            if statement:
                statements.append(statement)
            current = []
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


class PostgresConnection:
    def __init__(self):
        if psycopg is None:
            raise RuntimeError("Instale psycopg para usar PostgreSQL: pip install psycopg[binary]")
        self.conn = None

    def __enter__(self):
        global PG_CONN
        PG_LOCK.acquire()
        if PG_CONN is None or PG_CONN.closed:
            PG_CONN = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        self.conn = PG_CONN
        return self

    def __exit__(self, exc_type, exc, tb):
        global PG_CONN
        try:
            if exc_type:
                self.conn.rollback()
            else:
                self.conn.commit()
        except Exception:
            try:
                self.conn.close()
            except Exception:
                pass
            PG_CONN = None
        finally:
            PG_LOCK.release()

    def execute(self, sql, params=()):
        if sql.strip().upper().startswith("PRAGMA"):
            return EmptyCursor()
        return self.conn.execute(pg_sql(sql), params)

    def executescript(self, script):
        for statement in split_sql_script(script):
            self.execute(statement)

    def commit(self):
        self.conn.commit()


def db():
    if DB_ENGINE == "postgres":
        return PostgresConnection()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def row_dict(row):
    return dict(row) if row else None


def rows(sql, params=()):
    with db() as conn:
        return [row_dict(row) for row in conn.execute(sql, params).fetchall()]


def row(sql, params=()):
    with db() as conn:
        return row_dict(conn.execute(sql, params).fetchone())


def execute(sql, params=()):
    with db() as conn:
        conn.execute(sql, params)
        conn.commit()


def json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def table_columns(conn, table):
    if DB_ENGINE == "sqlite":
        return [item["name"] for item in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return [item["column_name"] for item in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name=%s",
        (table,),
    ).fetchall()]


def backup_name(kind="manual"):
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    return f"troca-ae-{kind}-{stamp}.zip"


def postgres_tool(name):
    env_name = f"{name.upper()}_PATH"
    return os.environ.get(env_name, name)


def create_backup(kind="manual"):
    BACKUPS.mkdir(parents=True, exist_ok=True)
    name = backup_name(kind)
    path = BACKUPS / name
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if DB_ENGINE == "postgres":
            dump_path = BACKUPS / f"{path.stem}.dump"
            try:
                subprocess.run(
                    [postgres_tool("pg_dump"), "--format=custom", "--file", str(dump_path), DATABASE_URL],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                archive.write(dump_path, "database/postgres.dump")
            finally:
                if dump_path.exists():
                    dump_path.unlink()
        elif DB_PATH.exists():
            archive.write(DB_PATH, "data/troca_ae.db")
        if UPLOADS.exists():
            for file in UPLOADS.rglob("*"):
                if file.is_file():
                    archive.write(file, f"uploads/{file.relative_to(UPLOADS).as_posix()}")
        archive.writestr("backup-info.json", json.dumps({"created_at": now(), "kind": kind, "database": DB_ENGINE}, ensure_ascii=False, indent=2))
    return path


def safe_backup_path(name):
    backup = (BACKUPS / name).resolve()
    if not str(backup).startswith(str(BACKUPS.resolve())) or not backup.exists() or backup.suffix != ".zip":
        raise FileNotFoundError("Backup nao encontrado.")
    return backup


def restore_uploads(archive):
    members = set(archive.namelist())
    for member in members:
        if not member.startswith("uploads/") or member.endswith("/"):
            continue
        target = (UPLOADS / member.replace("uploads/", "", 1)).resolve()
        if str(target).startswith(str(UPLOADS.resolve())):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))


def stage_restore_backup(name):
    backup = safe_backup_path(name)
    with zipfile.ZipFile(backup) as archive:
        if "data/troca_ae.db" not in set(archive.namelist()):
            raise RuntimeError("Este backup nao contem banco SQLite.")
    create_backup("pre-restore")
    PENDING_RESTORE.write_text(json.dumps({"name": backup.name, "created_at": now()}, ensure_ascii=False, indent=2), encoding="utf-8")
    return backup


def apply_pending_restore():
    if DB_ENGINE != "sqlite" or not PENDING_RESTORE.exists():
        return
    DATA.mkdir(exist_ok=True)
    UPLOADS.mkdir(exist_ok=True)
    data = json.loads(PENDING_RESTORE.read_text(encoding="utf-8"))
    backup = safe_backup_path(data.get("name", ""))
    with zipfile.ZipFile(backup) as archive:
        if "data/troca_ae.db" not in set(archive.namelist()):
            raise RuntimeError("Backup pendente nao contem banco SQLite.")
        tmp_db = DATA / f"restore-startup-{uuid.uuid4().hex}.db"
        tmp_db.write_bytes(archive.read("data/troca_ae.db"))
        tmp_db.replace(DB_PATH)
        restore_uploads(archive)
    PENDING_RESTORE.unlink()


def restore_backup_file(name):
    backup = safe_backup_path(name)
    create_backup("pre-restore")
    with zipfile.ZipFile(backup) as archive:
        members = set(archive.namelist())
        if DB_ENGINE == "postgres":
            if "database/postgres.dump" not in members:
                raise RuntimeError("Este backup nao contem dump PostgreSQL.")
            dump_path = BACKUPS / f"restore-{uuid.uuid4().hex}.dump"
            try:
                dump_path.write_bytes(archive.read("database/postgres.dump"))
                subprocess.run(
                    [postgres_tool("pg_restore"), "--clean", "--if-exists", "--no-owner", "--dbname", DATABASE_URL, str(dump_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            finally:
                if dump_path.exists():
                    dump_path.unlink()
        else:
            if "data/troca_ae.db" not in members:
                raise RuntimeError("Este backup nao contem banco SQLite.")
            tmp_db = DATA / f"restore-{uuid.uuid4().hex}.db"
            try:
                tmp_db.write_bytes(archive.read("data/troca_ae.db"))
                with sqlite3.connect(tmp_db) as source, sqlite3.connect(DB_PATH) as target:
                    source.backup(target)
            finally:
                if tmp_db.exists():
                    try:
                        tmp_db.unlink()
                    except PermissionError:
                        pass
        restore_uploads(archive)
    return backup


def backup_rows():
    BACKUPS.mkdir(parents=True, exist_ok=True)
    return [
        {"name": file.name, "size": file.stat().st_size, "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(file.stat().st_mtime))}
        for file in sorted(BACKUPS.glob("*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
    ]


def ensure_daily_backup():
    BACKUPS.mkdir(parents=True, exist_ok=True)
    prefix = f"troca-ae-auto-{today()}"
    has_database = DB_ENGINE == "postgres" or DB_PATH.exists()
    if not any(file.name.startswith(prefix) for file in BACKUPS.glob("*.zip")) and has_database:
        try:
            create_backup("auto")
        except (subprocess.CalledProcessError, FileNotFoundError) as error:
            print(f"Backup automatico nao criado: {error}")


def order_total(conn, order_id):
    part_total = conn.execute(
        "SELECT COALESCE(SUM(unit_price * quantity),0) AS total FROM order_parts WHERE order_id=?",
        (order_id,),
    ).fetchone()["total"]
    service_total = conn.execute(
        "SELECT COALESCE(SUM(labor),0) AS total FROM order_services WHERE order_id=?",
        (order_id,),
    ).fetchone()["total"]
    order = conn.execute("SELECT discount, paid FROM service_orders WHERE id=?", (order_id,)).fetchone()
    total = float(part_total or 0) + float(service_total or 0) - float(order["discount"] or 0)
    paid = float(order["paid"] or 0)
    return {"total": total, "paid": paid, "balance": total - paid}


def order_payload(order_id=None, include_details=True):
    where = "WHERE service_orders.id=?" if order_id else ""
    params = (order_id,) if order_id else ()
    with db() as conn:
        orders = [dict(item) for item in conn.execute(
            f"""SELECT service_orders.*, clients.name AS client_name
            FROM service_orders
            JOIN clients ON clients.id = service_orders.client_id
            {where}
            ORDER BY number DESC""",
            params,
        ).fetchall()]
        if not orders:
            return None if order_id else []

        order_ids = [order["id"] for order in orders]
        placeholders = ",".join(["?"] * len(order_ids))

        def grouped(query, key="order_id"):
            result = {item: [] for item in order_ids}
            for item in conn.execute(query, tuple(order_ids)).fetchall():
                data = dict(item)
                result.setdefault(data[key], []).append(data)
            return result

        parts_by_order = {}
        services_by_order = {}
        photos_by_order = {}
        history_by_order = {}
        if include_details:
            parts_by_order = grouped(
                f"""SELECT order_parts.*, parts.name, parts.sku
                FROM order_parts JOIN parts ON parts.id = order_parts.part_id
                WHERE order_id IN ({placeholders}) ORDER BY parts.name"""
            )
            services_by_order = grouped(
                f"""SELECT order_services.*, services.name, services.duration
                FROM order_services JOIN services ON services.id = order_services.service_id
                WHERE order_id IN ({placeholders}) ORDER BY services.name"""
            )
            photos_by_order = grouped(
                f"SELECT * FROM order_photos WHERE order_id IN ({placeholders}) ORDER BY created_at DESC"
            )
            history_by_order = grouped(
                f"""SELECT order_status_history.*, users.name AS user_name
                FROM order_status_history
                LEFT JOIN users ON users.id = order_status_history.user_id
                WHERE order_id IN ({placeholders}) ORDER BY created_at DESC"""
            )
        part_totals = {
            item["order_id"]: item["total"]
            for item in conn.execute(
                f"SELECT order_id, COALESCE(SUM(unit_price*quantity),0) AS total FROM order_parts WHERE order_id IN ({placeholders}) GROUP BY order_id",
                tuple(order_ids),
            ).fetchall()
        }
        service_totals = {
            item["order_id"]: item["total"]
            for item in conn.execute(
                f"SELECT order_id, COALESCE(SUM(labor),0) AS total FROM order_services WHERE order_id IN ({placeholders}) GROUP BY order_id",
                tuple(order_ids),
            ).fetchall()
        }
        for order in orders:
            part_total = float(part_totals.get(order["id"], 0) or 0)
            service_total = float(service_totals.get(order["id"], 0) or 0)
            paid = float(order["paid"] or 0)
            total = part_total + service_total - float(order["discount"] or 0)
            order["detail_loaded"] = bool(include_details)
            order["parts"] = parts_by_order.get(order["id"], []) if include_details else []
            order["services"] = services_by_order.get(order["id"], []) if include_details else []
            order["photos"] = photos_by_order.get(order["id"], []) if include_details else []
            order["status_history"] = history_by_order.get(order["id"], []) if include_details else []
            order["calc"] = {"total": total, "paid": paid, "balance": total - paid}
        return orders[0] if order_id and orders else (None if order_id else orders)


def reports_summary_payload():
    with db() as conn:
        finance = row_dict(conn.execute(
            """SELECT
            COALESCE(SUM(CASE WHEN type='Entrada' THEN amount ELSE 0 END),0) AS income,
            COALESCE(SUM(CASE WHEN type='Saida' THEN amount ELSE 0 END),0) AS expense,
            COALESCE(SUM(card_fee),0) AS fees
            FROM finance_entries"""
        ).fetchone())
        statuses = [
            {"Status": item["status"], "Quantidade": item["total"]}
            for item in conn.execute("SELECT status, COUNT(*) AS total FROM service_orders GROUP BY status ORDER BY total DESC").fetchall()
        ]
        stock = row_dict(conn.execute(
            """SELECT
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN stock <= min_stock THEN 1 ELSE 0 END),0) AS low,
            COALESCE(SUM(CASE WHEN stock = 0 THEN 1 ELSE 0 END),0) AS empty
            FROM parts"""
        ).fetchone())
        services = [
            row_dict(item) for item in conn.execute(
                """SELECT services.name AS Servico, COUNT(order_services.id) AS Quantidade,
                COALESCE(SUM(order_services.labor),0) AS Receita,
                COALESCE(AVG(order_services.warranty_days),0) AS Garantia_media
                FROM order_services
                JOIN services ON services.id=order_services.service_id
                GROUP BY services.id, services.name
                ORDER BY Quantidade DESC, Receita DESC
                LIMIT 8"""
            ).fetchall()
        ]
        parts = [
            row_dict(item) for item in conn.execute(
                """SELECT parts.name AS Peca, parts.sku AS SKU, COALESCE(SUM(order_parts.quantity),0) AS Quantidade,
                COALESCE(SUM(order_parts.unit_price*order_parts.quantity),0) AS Receita,
                COALESCE(SUM(order_parts.unit_cost*order_parts.quantity),0) AS Custo,
                COALESCE(SUM((order_parts.unit_price-order_parts.unit_cost)*order_parts.quantity),0) AS Lucro
                FROM order_parts
                JOIN parts ON parts.id=order_parts.part_id
                GROUP BY parts.id, parts.name, parts.sku
                ORDER BY Quantidade DESC, Lucro DESC
                LIMIT 8"""
            ).fetchall()
        ]
        technicians = [
            row_dict(item) for item in conn.execute(
                """SELECT COALESCE(technician_name,'Sem tecnico') AS Tecnico, COUNT(*) AS OS,
                COALESCE(SUM(paid),0) AS Receita, 0 AS Custo_pecas
                FROM service_orders
                GROUP BY technician_name
                ORDER BY Receita DESC
                LIMIT 8"""
            ).fetchall()
        ]
        warranty = [
            row_dict(item) for item in conn.execute(
                """SELECT service_orders.number AS OS, clients.name AS Cliente, service_orders.status AS Status,
                service_orders.opened AS Abertura, service_orders.delivery_signed_at AS Entrega,
                COALESCE(service_orders.technician_name,'') AS Tecnico, COALESCE(service_orders.follow_up,'') AS Pos_venda,
                90 AS Garantia_dias
                FROM service_orders
                JOIN clients ON clients.id=service_orders.client_id
                WHERE LOWER(COALESCE(service_orders.follow_up,'') || ' ' || COALESCE(service_orders.warranty_term,'') || ' ' || COALESCE(service_orders.status,'')) LIKE ?
                OR LOWER(COALESCE(service_orders.follow_up,'') || ' ' || COALESCE(service_orders.warranty_term,'') || ' ' || COALESCE(service_orders.status,'')) LIKE ?
                ORDER BY service_orders.opened DESC
                LIMIT 8""",
                ("%garantia%", "%retorno%"),
            ).fetchall()
        ]
    for item in technicians:
        item["Mao_de_obra"] = max(0, float(item.get("Receita") or 0) - float(item.get("Custo_pecas") or 0))
        item["Lucro"] = float(item.get("Receita") or 0) - float(item.get("Custo_pecas") or 0)
        item["Margem"] = f"{((item['Lucro'] / float(item.get('Receita') or 1)) * 100):.1f}%" if float(item.get("Receita") or 0) else "0%"
    return {"finance": finance, "statuses": statuses, "stock": stock, "turnover": [], "technicians": technicians, "warranty_returns": warranty, "top_services": services, "top_parts": parts}


def parts_list_payload(query="", manufacturer_id="", stock_filter="", page=1, page_size=20):
    page = max(1, int(page or 1))
    page_size = min(5000, max(1, int(page_size or 20)))
    where = []
    params = []
    if query:
        like = f"%{query.lower()}%"
        where.append("""LOWER(
            COALESCE(parts.name,'') || ' ' ||
            COALESCE(parts.sku,'') || ' ' ||
            COALESCE(parts.category,'') || ' ' ||
            COALESCE(parts.compatible_models,'') || ' ' ||
            COALESCE(manufacturers.name,'')
        ) LIKE ?""")
        params.append(like)
    if manufacturer_id:
        where.append("parts.manufacturer_id=?")
        params.append(manufacturer_id)
    if stock_filter == "low":
        where.append("parts.stock <= parts.min_stock")
    if stock_filter == "zero":
        where.append("parts.stock = 0")
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    from_sql = "FROM parts LEFT JOIN manufacturers ON manufacturers.id=parts.manufacturer_id"
    select_sql = f"""SELECT parts.id,parts.sku,parts.name,parts.category,parts.manufacturer_id,parts.compatible_models,
        parts.cost,parts.price,parts.stock,parts.min_stock,parts.supplier_id,parts.warranty_days,parts.usage_type
        {from_sql} {where_sql}
        ORDER BY parts.name
        LIMIT ? OFFSET ?"""
    count_sql = f"SELECT COUNT(*) AS total {from_sql} {where_sql}"
    with db() as conn:
        total = int(conn.execute(count_sql, tuple(params)).fetchone()["total"] or 0)
        items = [row_dict(item) for item in conn.execute(select_sql, tuple(params + [page_size, (page - 1) * page_size])).fetchall()]
    return {"parts": items, "parts_meta": {"total": total, "page": page, "page_size": page_size, "query": query, "manufacturer_id": manufacturer_id, "stock_filter": stock_filter}}


def insert_status_history(conn, order_id, user_id, old_status, new_status, note):
    if old_status == new_status and note != "OS criada":
        return
    conn.execute(
        "INSERT INTO order_status_history(id,order_id,user_id,old_status,new_status,note) VALUES(?,?,?,?,?,?)",
        (uid("hist"), order_id, user_id, old_status or "", new_status or "", note or ""),
    )


def seed():
    DATA.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        schema_path = POSTGRES_SCHEMA_PATH if DB_ENGINE == "postgres" else SCHEMA_PATH
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        if DB_ENGINE == "sqlite":
            columns = table_columns(conn, "company_settings")
            if "dark_color" not in columns:
                conn.execute("ALTER TABLE company_settings ADD COLUMN dark_color TEXT NOT NULL DEFAULT '#18231f'")
            if "theme" not in columns:
                conn.execute("ALTER TABLE company_settings ADD COLUMN theme TEXT NOT NULL DEFAULT 'light'")
            user_columns = table_columns(conn, "users")
            if "failed_attempts" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0")
            if "locked_until" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN locked_until TEXT")
            if "last_login" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN last_login TEXT")
            if "password_changed_at" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN password_changed_at TEXT")
            for column, definition in {
                "email_verified": "INTEGER NOT NULL DEFAULT 1",
                "email_verification_token": "TEXT",
                "email_verification_expires": "TEXT",
                "password_reset_token": "TEXT",
                "password_reset_expires": "TEXT",
            }.items():
                if column not in user_columns:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")
            conn.execute("UPDATE users SET email_verified=1 WHERE email_verified IS NULL")
            part_columns = table_columns(conn, "parts")
            if "warranty_days" not in part_columns:
                conn.execute("ALTER TABLE parts ADD COLUMN warranty_days INTEGER NOT NULL DEFAULT 90")
            if "usage_type" not in part_columns:
                conn.execute("ALTER TABLE parts ADD COLUMN usage_type TEXT NOT NULL DEFAULT 'Ambos'")
            order_columns = table_columns(conn, "service_orders")
            for column in ("approval_signature", "approval_signed_at", "delivery_signature", "delivery_signed_at"):
                if column not in order_columns:
                    conn.execute(f"ALTER TABLE service_orders ADD COLUMN {column} TEXT")
            order_part_columns = table_columns(conn, "order_parts")
            if "warranty_days" not in order_part_columns:
                conn.execute("ALTER TABLE order_parts ADD COLUMN warranty_days INTEGER NOT NULL DEFAULT 90")
            order_service_columns = table_columns(conn, "order_services")
            if "warranty_days" not in order_service_columns:
                conn.execute("ALTER TABLE order_services ADD COLUMN warranty_days INTEGER NOT NULL DEFAULT 90")
            movement_columns = table_columns(conn, "stock_movements")
            for column, definition in {
                "supplier_id": "TEXT REFERENCES suppliers(id)",
                "lot": "TEXT",
                "purchase_id": "TEXT REFERENCES purchase_entries(id)",
                "sale_id": "TEXT",
            }.items():
                if column not in movement_columns:
                    conn.execute(f"ALTER TABLE stock_movements ADD COLUMN {column} {definition}")
            finance_columns = table_columns(conn, "finance_entries")
            for column, definition in {
                "cash_session_id": "TEXT",
                "recurrence_id": "TEXT",
                "card_fee": "REAL NOT NULL DEFAULT 0",
                "reconciled": "INTEGER NOT NULL DEFAULT 0",
                "reconciled_at": "TEXT",
                "recurrence_frequency": "TEXT",
                "recurrence_until": "TEXT",
                "installment": "INTEGER NOT NULL DEFAULT 1",
                "installments": "INTEGER NOT NULL DEFAULT 1",
            }.items():
                if column not in finance_columns:
                    conn.execute(f"ALTER TABLE finance_entries ADD COLUMN {column} {definition}")
        else:
            conn.execute("ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS dark_color TEXT NOT NULL DEFAULT '#18231f'")
            conn.execute("ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS theme TEXT NOT NULL DEFAULT 'light'")
            conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_attempts INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TEXT")
            conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login TEXT")
            conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TEXT")
            conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified INTEGER NOT NULL DEFAULT 1")
            conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verification_token TEXT")
            conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verification_expires TEXT")
            conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_token TEXT")
            conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_expires TEXT")
            conn.execute("UPDATE users SET email_verified=1 WHERE email_verified IS NULL")
            conn.execute("ALTER TABLE parts ADD COLUMN IF NOT EXISTS warranty_days INTEGER NOT NULL DEFAULT 90")
            conn.execute("ALTER TABLE parts ADD COLUMN IF NOT EXISTS usage_type TEXT NOT NULL DEFAULT 'Ambos'")
            conn.execute("ALTER TABLE service_orders ADD COLUMN IF NOT EXISTS approval_signature TEXT")
            conn.execute("ALTER TABLE service_orders ADD COLUMN IF NOT EXISTS approval_signed_at TEXT")
            conn.execute("ALTER TABLE service_orders ADD COLUMN IF NOT EXISTS delivery_signature TEXT")
            conn.execute("ALTER TABLE service_orders ADD COLUMN IF NOT EXISTS delivery_signed_at TEXT")
            conn.execute("ALTER TABLE order_parts ADD COLUMN IF NOT EXISTS warranty_days INTEGER NOT NULL DEFAULT 90")
            conn.execute("ALTER TABLE order_services ADD COLUMN IF NOT EXISTS warranty_days INTEGER NOT NULL DEFAULT 90")
            conn.execute("ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS supplier_id TEXT REFERENCES suppliers(id)")
            conn.execute("ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS lot TEXT")
            conn.execute("ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS purchase_id TEXT REFERENCES purchase_entries(id)")
            conn.execute("ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS sale_id TEXT")
            conn.execute("ALTER TABLE finance_entries ADD COLUMN IF NOT EXISTS cash_session_id TEXT")
            conn.execute("ALTER TABLE finance_entries ADD COLUMN IF NOT EXISTS recurrence_id TEXT")
            conn.execute("ALTER TABLE finance_entries ADD COLUMN IF NOT EXISTS card_fee DOUBLE PRECISION NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE finance_entries ADD COLUMN IF NOT EXISTS reconciled INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE finance_entries ADD COLUMN IF NOT EXISTS reconciled_at TEXT")
            conn.execute("ALTER TABLE finance_entries ADD COLUMN IF NOT EXISTS recurrence_frequency TEXT")
            conn.execute("ALTER TABLE finance_entries ADD COLUMN IF NOT EXISTS recurrence_until TEXT")
            conn.execute("ALTER TABLE finance_entries ADD COLUMN IF NOT EXISTS installment INTEGER NOT NULL DEFAULT 1")
            conn.execute("ALTER TABLE finance_entries ADD COLUMN IF NOT EXISTS installments INTEGER NOT NULL DEFAULT 1")
        if conn.execute("SELECT COUNT(*) AS total FROM roles").fetchone()["total"] == 0:
            permissions = {
                "clientes": ["ver", "criar", "editar", "excluir"],
                "os": ["ver", "criar", "editar", "aprovar", "finalizar", "imprimir"],
                "estoque": ["ver", "criar", "editar", "movimentar", "excluir"],
                "servicos": ["ver", "criar", "editar", "excluir"],
                "financeiro": ["ver", "criar", "editar", "pagar", "excluir"],
                "pdv": ["ver", "criar", "editar", "excluir"],
                "relatorios": ["ver", "exportar"],
                "fabricantes": ["ver", "criar", "editar", "excluir"],
                "configuracoes": ["ver", "editar"],
                "auditoria": ["ver"],
            }
            conn.execute(
                "INSERT INTO roles(id,name,level,permissions) VALUES(?,?,?,?)",
                ("role-adm", "Administrador", 100, json.dumps(permissions)),
            )
            conn.execute(
                "INSERT INTO users(id,name,email,password_hash,role_id,status) VALUES(?,?,?,?,?,?)",
                ("usr-admin", "Administrador", "admin@troca-ae.local", password_hash("admin123"), "role-adm", "Ativo"),
            )
            warranty = (
                "TERMO DE GARANTIA DO SERVICO PRESTADO\n\n"
                "A garantia legal do servico prestado e de 90 dias, contados a partir da data de entrega do aparelho ao cliente. "
                "A garantia cobre exclusivamente o servico executado nesta Ordem de Servico e as pecas substituidas descritas no documento.\n\n"
                "A garantia nao cobre queda, impacto, oxidacao, contato com liquido, mau uso, violacao, tentativa de reparo por terceiros, "
                "falhas de software, perda de dados ou defeitos nao relacionados ao reparo aprovado."
            )
            conn.execute(
                """INSERT INTO company_settings
                (id,system_name,trade_name,legal_name,logo_path,primary_color,dark_color,theme,warranty_term,print_footer)
                VALUES(1,?,?,?,?,?,?,?,?,?)""",
                ("Troca Ae SIS PRO", "Troca Ae SIS PRO", "", "/troca-ae-logo.jpg", "#f9732f", "#18231f", "light", warranty, "Obrigado pela preferencia."),
            )
            conn.execute(
                "INSERT INTO clients(id,name,phone,email,document,zip,street,number,neighborhood,city,state,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("cli-1", "Cliente exemplo", "(11) 99999-0000", "cliente@email.com", "000.000.000-00", "01310-100", "Avenida Paulista", "100", "Bela Vista", "Sao Paulo", "SP", "Cadastro inicial"),
            )
            conn.execute(
                "INSERT INTO manufacturers(id,name,support_phone,site,notes) VALUES(?,?,?,?,?)",
                ("man-apple", "Apple", "0800 761 0880", "apple.com/br", "Linha iPhone"),
            )
            conn.execute(
                "INSERT INTO product_models(id,manufacturer_id,name,category,year,notes) VALUES(?,?,?,?,?,?)",
                ("mod-iphone-13", "man-apple", "iPhone 13", "Smartphone", 2021, "Modelo inicial"),
            )
            conn.execute(
                "INSERT INTO suppliers(id,name,phone,email,document,notes) VALUES(?,?,?,?,?,?)",
                ("sup-1", "Fornecedor exemplo", "(11) 3000-0000", "fornecedor@email.com", "", "Fornecedor inicial"),
            )
            conn.execute(
                "INSERT INTO parts(id,sku,name,category,manufacturer_id,compatible_models,cost,price,stock,min_stock,supplier_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("part-1", "TEL-IP13", "Tela iPhone 13", "Tela", "man-apple", json.dumps(["mod-iphone-13"]), 460, 720, 3, 2, "sup-1"),
            )
            conn.execute(
                "INSERT INTO services(id,name,category,labor,warranty_days,duration) VALUES(?,?,?,?,?,?)",
                ("srv-1", "Troca de tela", "Manutencao", 180, 90, "2h"),
            )
            conn.execute(
                "INSERT INTO audit_logs(id,user_id,action,detail) VALUES(?,?,?,?)",
                (uid("aud"), "usr-admin", "Sistema iniciado", "Banco criado com usuario administrador."),
            )
        conn.commit()
    ensure_default_permissions()
    ensure_default_roles()
    ensure_performance_indexes()
    if DB_ENGINE != "postgres":
        ensure_catalog()
        ensure_daily_backup()


def run_startup_tasks():
    try:
        ensure_catalog()
        ensure_daily_backup()
    except Exception as error:
        print(f"Tarefas iniciais nao concluidas: {error}", flush=True)


def ensure_default_permissions():
    admin = row("SELECT id,permissions FROM roles WHERE id='role-adm'")
    if not admin:
        return
    permissions = json.loads(admin["permissions"] or "{}")
    defaults = {
        "clientes": ["ver", "criar", "editar", "excluir"],
        "os": ["ver", "criar", "editar", "aprovar", "finalizar", "imprimir"],
        "estoque": ["ver", "criar", "editar", "movimentar", "excluir"],
        "servicos": ["ver", "criar", "editar", "excluir"],
        "financeiro": ["ver", "criar", "editar", "pagar", "excluir"],
        "pdv": ["ver", "criar", "editar", "excluir"],
        "relatorios": ["ver", "exportar"],
        "fabricantes": ["ver", "criar", "editar", "excluir"],
        "configuracoes": ["ver", "editar"],
        "auditoria": ["ver"],
    }
    changed = False
    for module, actions in defaults.items():
        current = set(permissions.get(module, []))
        merged = sorted(current | set(actions))
        if merged != permissions.get(module, []):
            permissions[module] = merged
            changed = True
    if changed:
        execute("UPDATE roles SET permissions=? WHERE id='role-adm'", (json.dumps(permissions),))


def ensure_default_roles():
    defaults = [
        ("role-atd", "Atendente", 40, {"clientes": ["ver", "criar", "editar"], "os": ["ver", "criar"], "fabricantes": ["ver"], "estoque": ["ver"], "servicos": ["ver"]}),
        ("role-tec", "Tecnico", 60, {"os": ["ver", "editar", "finalizar"], "estoque": ["ver", "movimentar"], "fabricantes": ["ver"], "servicos": ["ver"]}),
        ("role-fin", "Financeiro", 70, {"financeiro": ["ver", "criar", "editar", "pagar"], "relatorios": ["ver", "exportar"], "os": ["ver"]}),
    ]
    with db() as conn:
        for role_id, name, level, permissions in defaults:
            exists = conn.execute("SELECT id FROM roles WHERE id=?", (role_id,)).fetchone()
            if not exists:
                conn.execute("INSERT INTO roles(id,name,level,permissions) VALUES(?,?,?,?)", (role_id, name, level, json.dumps(permissions)))
        conn.commit()


def ensure_performance_indexes():
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_clients_name ON clients(name)",
        "CREATE INDEX IF NOT EXISTS idx_clients_document ON clients(document)",
        "CREATE INDEX IF NOT EXISTS idx_manufacturers_name ON manufacturers(name)",
        "CREATE INDEX IF NOT EXISTS idx_models_manufacturer ON product_models(manufacturer_id)",
        "CREATE INDEX IF NOT EXISTS idx_models_name ON product_models(name)",
        "CREATE INDEX IF NOT EXISTS idx_parts_manufacturer ON parts(manufacturer_id)",
        "CREATE INDEX IF NOT EXISTS idx_parts_name ON parts(name)",
        "CREATE INDEX IF NOT EXISTS idx_parts_sku ON parts(sku)",
        "CREATE INDEX IF NOT EXISTS idx_parts_stock ON parts(stock, min_stock)",
        "CREATE INDEX IF NOT EXISTS idx_orders_client ON service_orders(client_id)",
        "CREATE INDEX IF NOT EXISTS idx_orders_status ON service_orders(status)",
        "CREATE INDEX IF NOT EXISTS idx_orders_due ON service_orders(due)",
        "CREATE INDEX IF NOT EXISTS idx_orders_number ON service_orders(number)",
        "CREATE INDEX IF NOT EXISTS idx_order_parts_order ON order_parts(order_id)",
        "CREATE INDEX IF NOT EXISTS idx_order_services_order ON order_services(order_id)",
        "CREATE INDEX IF NOT EXISTS idx_order_photos_order ON order_photos(order_id)",
        "CREATE INDEX IF NOT EXISTS idx_order_history_order ON order_status_history(order_id)",
        "CREATE INDEX IF NOT EXISTS idx_finance_date ON finance_entries(date)",
        "CREATE INDEX IF NOT EXISTS idx_finance_type_status ON finance_entries(type, status)",
        "CREATE INDEX IF NOT EXISTS idx_finance_order ON finance_entries(order_id)",
        "CREATE INDEX IF NOT EXISTS idx_stock_movements_created ON stock_movements(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_stock_movements_part ON stock_movements(part_id)",
        "CREATE INDEX IF NOT EXISTS idx_pos_sales_date ON pos_sales(date)",
    ]
    with db() as conn:
        for statement in indexes:
            conn.execute(statement)
        conn.commit()


def ensure_catalog():
    manufacturers = [
        ("man-apple", "Apple", "0800 761 0880", "apple.com/br", "Linha iPhone."),
        ("man-samsung", "Samsung", "4004-0000", "samsung.com/br", "Linhas Galaxy S, Note, Z, A e M."),
        ("man-motorola", "Motorola", "4002-1244", "motorola.com.br", "Linhas Moto G, Edge e Razr."),
        ("man-xiaomi", "Xiaomi", "0800 030 5065", "mi.com/br", "Linhas Redmi, POCO e Mi/Xiaomi."),
    ]
    raw_models = [
        ("man-apple", 2017, ["iPhone 8", "iPhone 8 Plus", "iPhone X"]),
        ("man-apple", 2018, ["iPhone XR", "iPhone XS", "iPhone XS Max"]),
        ("man-apple", 2019, ["iPhone 11", "iPhone 11 Pro", "iPhone 11 Pro Max"]),
        ("man-apple", 2020, ["iPhone SE 2", "iPhone 12 mini", "iPhone 12", "iPhone 12 Pro", "iPhone 12 Pro Max"]),
        ("man-apple", 2021, ["iPhone 13 mini", "iPhone 13", "iPhone 13 Pro", "iPhone 13 Pro Max"]),
        ("man-apple", 2022, ["iPhone SE 3", "iPhone 14", "iPhone 14 Plus", "iPhone 14 Pro", "iPhone 14 Pro Max"]),
        ("man-apple", 2023, ["iPhone 15", "iPhone 15 Plus", "iPhone 15 Pro", "iPhone 15 Pro Max"]),
        ("man-apple", 2024, ["iPhone 16", "iPhone 16 Plus", "iPhone 16 Pro", "iPhone 16 Pro Max"]),
        ("man-apple", 2025, ["iPhone 16e", "iPhone 17", "iPhone 17 Pro", "iPhone 17 Pro Max"]),
        ("man-apple", 2026, ["iPhone 17e"]),
        ("man-samsung", 2017, ["Galaxy S8", "Galaxy S8+", "Galaxy Note8", "Galaxy A5 2017", "Galaxy J7 Pro"]),
        ("man-samsung", 2018, ["Galaxy S9", "Galaxy S9+", "Galaxy Note9", "Galaxy A8 2018", "Galaxy J8"]),
        ("man-samsung", 2019, ["Galaxy S10e", "Galaxy S10", "Galaxy S10+", "Galaxy Note10", "Galaxy Note10+", "Galaxy A10", "Galaxy A20", "Galaxy A30", "Galaxy A50", "Galaxy A70"]),
        ("man-samsung", 2020, ["Galaxy S20", "Galaxy S20+", "Galaxy S20 Ultra", "Galaxy Note20", "Galaxy Note20 Ultra", "Galaxy A51", "Galaxy A71", "Galaxy M31"]),
        ("man-samsung", 2021, ["Galaxy S21", "Galaxy S21+", "Galaxy S21 Ultra", "Galaxy Z Flip3", "Galaxy Z Fold3", "Galaxy A52", "Galaxy A72"]),
        ("man-samsung", 2022, ["Galaxy S22", "Galaxy S22+", "Galaxy S22 Ultra", "Galaxy Z Flip4", "Galaxy Z Fold4", "Galaxy A53", "Galaxy A73"]),
        ("man-samsung", 2023, ["Galaxy S23", "Galaxy S23+", "Galaxy S23 Ultra", "Galaxy Z Flip5", "Galaxy Z Fold5", "Galaxy A34", "Galaxy A54"]),
        ("man-samsung", 2024, ["Galaxy S24", "Galaxy S24+", "Galaxy S24 Ultra", "Galaxy Z Flip6", "Galaxy Z Fold6", "Galaxy A35", "Galaxy A55"]),
        ("man-samsung", 2025, ["Galaxy S25", "Galaxy S25+", "Galaxy S25 Ultra", "Galaxy Z Flip7", "Galaxy Z Fold7", "Galaxy A36", "Galaxy A56"]),
        ("man-samsung", 2026, ["Galaxy S26", "Galaxy S26+", "Galaxy S26 Ultra"]),
        ("man-motorola", 2017, ["Moto G5", "Moto G5 Plus", "Moto Z2 Play"]),
        ("man-motorola", 2018, ["Moto G6", "Moto G6 Plus", "Moto Z3 Play"]),
        ("man-motorola", 2019, ["Moto G7", "Moto G7 Plus", "Motorola One Vision"]),
        ("man-motorola", 2020, ["Moto G8", "Moto G8 Plus", "Motorola Edge", "Motorola Edge Plus", "Motorola Razr 5G"]),
        ("man-motorola", 2021, ["Moto G30", "Moto G60", "Motorola Edge 20", "Motorola Edge 20 Pro"]),
        ("man-motorola", 2022, ["Moto G52", "Moto G82", "Motorola Edge 30", "Motorola Razr 2022"]),
        ("man-motorola", 2023, ["Moto G53", "Moto G73", "Motorola Edge 40", "Motorola Razr 40"]),
        ("man-motorola", 2024, ["Moto G54", "Moto G84", "Motorola Edge 50", "Motorola Razr 50"]),
        ("man-motorola", 2025, ["Moto G 2025", "Moto G Power 2025", "Motorola Edge 60", "Motorola Razr 2025"]),
        ("man-motorola", 2026, ["Moto G 2026", "Moto G Power 2026", "Moto G Stylus 2026", "Motorola Razr 2026", "Motorola Edge 70"]),
        ("man-xiaomi", 2017, ["Redmi Note 4", "Mi 6", "Mi A1", "Redmi 5 Plus"]),
        ("man-xiaomi", 2018, ["Redmi Note 5", "Mi 8", "Mi A2", "POCO F1"]),
        ("man-xiaomi", 2019, ["Redmi Note 7", "Redmi Note 8", "Mi 9", "Mi 9T", "POCO X2"]),
        ("man-xiaomi", 2020, ["Redmi Note 9", "Redmi Note 9 Pro", "Mi 10", "Mi 10T", "POCO X3 NFC"]),
        ("man-xiaomi", 2021, ["Redmi Note 10", "Redmi Note 10 Pro", "Mi 11", "Mi 11 Lite", "POCO F3", "POCO X3 Pro"]),
        ("man-xiaomi", 2022, ["Redmi Note 11", "Redmi Note 11 Pro", "Xiaomi 12", "Xiaomi 12 Lite", "POCO F4", "POCO X4 Pro"]),
        ("man-xiaomi", 2023, ["Redmi Note 12", "Redmi Note 12 Pro", "Xiaomi 13", "Xiaomi 13 Lite", "POCO F5", "POCO X5 Pro"]),
        ("man-xiaomi", 2024, ["Redmi Note 13", "Redmi Note 13 Pro", "Xiaomi 14", "Xiaomi 14 Ultra", "POCO F6", "POCO X6 Pro"]),
        ("man-xiaomi", 2025, ["Redmi Note 14", "Redmi Note 14 Pro", "Xiaomi 15", "Xiaomi 15 Ultra", "POCO F7", "POCO X7 Pro"]),
        ("man-xiaomi", 2026, ["Redmi Note 15", "Redmi Note 15 Pro", "Xiaomi 16", "Xiaomi 16 Ultra", "POCO F8"]),
    ]
    part_templates = [
        ("TELA", "Tela / display completo", "Tela", 320, 620),
        ("BAT", "Bateria", "Bateria", 95, 220),
        ("CONC", "Conector de carga", "Conector", 35, 120),
        ("CAMF", "Camera frontal", "Camera", 75, 190),
        ("CAMT", "Camera traseira", "Camera", 120, 320),
        ("TAMP", "Tampa traseira", "Carcaca", 80, 220),
        ("CARC", "Carcaca / chassi", "Carcaca", 140, 360),
        ("AUR", "Auricular", "Audio", 30, 95),
        ("ALTF", "Alto-falante viva-voz", "Audio", 38, 110),
        ("MIC", "Microfone", "Audio", 28, 90),
        ("VIB", "Vibracall", "Motor", 24, 80),
        ("FLEXP", "Flex power", "Flex", 32, 95),
        ("FLEXV", "Flex volume", "Flex", 30, 95),
        ("SUB", "Placa sub / carga", "Placa", 65, 180),
        ("SIM", "Bandeja SIM", "Acabamento", 12, 45),
        ("LENC", "Lente da camera", "Camera", 18, 75),
        ("SENS", "Sensor proximidade/luz", "Sensor", 35, 105),
        ("BIOM", "Sensor biometrico", "Sensor", 45, 140),
    ]
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO suppliers(id,name,phone,email,document,notes) VALUES(?,?,?,?,?,?)",
            ("sup-1", "Fornecedor exemplo", "(11) 3000-0000", "fornecedor@email.com", "", "Fornecedor inicial"),
        )
        for item in manufacturers:
            conn.execute("INSERT OR IGNORE INTO manufacturers(id,name,support_phone,site,notes) VALUES(?,?,?,?,?)", item)
        for manufacturer_id, year, names in raw_models:
            for name in names:
                model_id = f"mod-{slug(name)}"
                conn.execute(
                    "INSERT OR IGNORE INTO product_models(id,manufacturer_id,name,category,year,notes) VALUES(?,?,?,?,?,?)",
                    (model_id, manufacturer_id, name, "Smartphone", year, f"Catalogo base {year}"),
                )
                for prefix, label, category, cost, price in part_templates:
                    part_id = f"part-{prefix.lower()}-{slug(name)}"
                    sku = f"{prefix}-{manufacturer_id.replace('man-', '')[:3].upper()}-{slug(name).upper()}"
                    conn.execute(
                        "INSERT OR IGNORE INTO parts(id,sku,name,category,manufacturer_id,compatible_models,cost,price,stock,min_stock,supplier_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (part_id, sku, f"{label} {name}", category, manufacturer_id, json.dumps([model_id]), cost, price, 0, 1, "sup-1"),
                    )
        conn.commit()


class App(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False, default=json_default).encode("utf-8")
        use_gzip = len(payload) > 1024 and "gzip" in self.headers.get("Accept-Encoding", "").lower()
        if use_gzip:
            payload = gzip.compress(payload, compresslevel=6)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except BrokenPipeError:
            return

    def read_json(self):
        size = int(self.headers.get("Content-Length", "0"))
        if not size:
            return {}
        return json.loads(self.rfile.read(size).decode("utf-8"))

    def token_user(self):
        auth = self.headers.get("Authorization", "")
        token = auth.replace("Bearer ", "", 1) if auth.startswith("Bearer ") else ""
        signed_session = verify_session_token(token)
        if signed_session:
            user = row("SELECT users.*, roles.name AS role_name, roles.level, roles.permissions FROM users JOIN roles ON roles.id = users.role_id WHERE users.id=? AND users.status='Ativo'", (signed_session.get("user_id"),))
            if user:
                return user
        session = SESSIONS.get(token)
        if isinstance(session, str):
            session = {"user_id": session, "expires_at": time.time() + SESSION_SECONDS}
            SESSIONS[token] = session
        if not session:
            return None
        if session.get("expires_at", 0) < time.time():
            SESSIONS.pop(token, None)
            return None
        session["expires_at"] = time.time() + SESSION_SECONDS
        if session.get("user"):
            return session["user"]
        user_id = session.get("user_id")
        user = row("SELECT users.*, roles.name AS role_name, roles.level, roles.permissions FROM users JOIN roles ON roles.id = users.role_id WHERE users.id=?", (user_id,)) if user_id else None
        if user:
            session["user"] = user
        return user

    def require_user(self):
        user = self.token_user()
        if not user:
            self.send_json({"error": "Login necessario."}, 401)
            return None
        return user

    def has_permission(self, user, module, action):
        if int(user["level"] or 0) >= 100:
            return True
        permissions = json.loads(user["permissions"] or "{}")
        return action in permissions.get(module, [])

    def require_permission(self, user, module, action):
        if self.has_permission(user, module, action):
            return True
        self.send_json({"error": f"Sem permissao para {action} em {module}."}, 403)
        return False

    def send_verification_email(self, user):
        raw_token = secrets.token_urlsafe(32)
        expires = seconds_from_now(24 * 60 * 60)
        execute(
            "UPDATE users SET email_verification_token=?, email_verification_expires=? WHERE id=?",
            (token_hash(raw_token), expires, user["id"]),
        )
        link = f"{app_url_from_request(self)}/?verify_email={user['email']}&verify_token={raw_token}"
        body = (
            f"Ola {user['name']},\n\n"
            "Confirme seu acesso ao Troca Ae SIS PRO pelo link abaixo:\n"
            f"{link}\n\n"
            "Este link vale por 24 horas. Se voce nao solicitou este acesso, ignore este e-mail."
        )
        return send_email(user["email"], "Confirme seu acesso - Troca Ae SIS PRO", body)

    def send_password_reset_email(self, user):
        raw_token = secrets.token_urlsafe(32)
        expires = seconds_from_now(60 * 60)
        execute(
            "UPDATE users SET password_reset_token=?, password_reset_expires=? WHERE id=?",
            (token_hash(raw_token), expires, user["id"]),
        )
        link = f"{app_url_from_request(self)}/?reset_email={user['email']}&reset_token={raw_token}"
        body = (
            f"Ola {user['name']},\n\n"
            "Recebemos uma solicitacao para redefinir sua senha no Troca Ae SIS PRO.\n"
            f"Acesse o link abaixo para criar uma nova senha:\n{link}\n\n"
            "Este link vale por 1 hora. Se voce nao solicitou, ignore este e-mail."
        )
        return send_email(user["email"], "Redefinicao de senha - Troca Ae SIS PRO", body)

    def request_password_reset(self, data):
        email = (data.get("email") or "").strip().lower()
        user = row("SELECT id,name,email,status FROM users WHERE LOWER(email)=?", (email,))
        if user and user["status"] == "Ativo":
            self.send_password_reset_email(user)
        self.send_json({"ok": True, "message": "Se o e-mail estiver cadastrado, enviaremos um link de redefinicao."})

    def reset_password_by_token(self, data):
        email = (data.get("email") or "").strip().lower()
        raw_token = data.get("token") or ""
        new_password = data.get("password") or ""
        error = password_strength_error(new_password)
        if error:
            self.send_json({"error": error}, 400)
            return
        user = row("SELECT id,email,password_reset_token,password_reset_expires FROM users WHERE LOWER(email)=?", (email,))
        if not user or not user["password_reset_token"] or user["password_reset_token"] != token_hash(raw_token) or (user["password_reset_expires"] or "") < now():
            self.send_json({"error": "Link de redefinicao invalido ou expirado."}, 400)
            return
        execute(
            """UPDATE users SET password_hash=?, password_changed_at=?, failed_attempts=0, locked_until=NULL,
            password_reset_token=NULL, password_reset_expires=NULL, email_verified=1 WHERE id=?""",
            (password_hash(new_password), now(), user["id"]),
        )
        self.send_json({"ok": True, "message": "Senha redefinida com sucesso. Voce ja pode entrar."})

    def verify_email_by_token(self, data):
        email = (data.get("email") or "").strip().lower()
        raw_token = data.get("token") or ""
        user = row("SELECT id,email,email_verification_token,email_verification_expires FROM users WHERE LOWER(email)=?", (email,))
        if not user or not user["email_verification_token"] or user["email_verification_token"] != token_hash(raw_token) or (user["email_verification_expires"] or "") < now():
            self.send_json({"error": "Link de confirmacao invalido ou expirado."}, 400)
            return
        execute("UPDATE users SET email_verified=1, email_verification_token=NULL, email_verification_expires=NULL WHERE id=?", (user["id"],))
        self.send_json({"ok": True, "message": "E-mail confirmado com sucesso. Voce ja pode entrar."})

    def resend_verification(self, data):
        email = (data.get("email") or "").strip().lower()
        user = row("SELECT id,name,email,status,email_verified FROM users WHERE LOWER(email)=?", (email,))
        if user and user["status"] == "Ativo" and not int(user.get("email_verified") or 0):
            self.send_verification_email(user)
        self.send_json({"ok": True, "message": "Se houver uma conta pendente, enviaremos um novo link de confirmacao."})

    def user_payload(self, user):
        return {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role_name"], "level": user["level"], "permissions": json.loads(user["permissions"])}

    def base_payload(self, user, full=True):
        return {
            "company": row("SELECT * FROM company_settings WHERE id=1"),
            "database": {"engine": DB_ENGINE, "production": DB_ENGINE == "postgres"},
            "full": full,
            "me": self.user_payload(user),
        }

    def page_payload(self, user, page):
        payload = self.base_payload(user, True)
        part_select = "SELECT id,sku,name,category,manufacturer_id,compatible_models,price,stock,min_stock,warranty_days,usage_type FROM parts ORDER BY name"
        part_inventory_select = "SELECT id,sku,name,category,manufacturer_id,compatible_models,cost,price,stock,min_stock,supplier_id,warranty_days,usage_type FROM parts ORDER BY name"
        if page == "clients":
            if not self.has_permission(user, "clientes", "ver"):
                return payload | {"clients": []}
            return payload | {"clients": rows("SELECT * FROM clients ORDER BY name")}
        if page == "catalog":
            allowed = self.has_permission(user, "fabricantes", "ver")
            return payload | {
                "manufacturers": rows("SELECT * FROM manufacturers ORDER BY name") if allowed else [],
                "models": rows("SELECT * FROM product_models ORDER BY name") if allowed else [],
            }
        if page == "parts":
            stock_allowed = self.has_permission(user, "estoque", "ver")
            catalog_allowed = stock_allowed or self.has_permission(user, "fabricantes", "ver")
            parts_payload = parts_list_payload() if stock_allowed else {"parts": [], "parts_meta": {"total": 0, "page": 1, "page_size": 20}}
            return payload | {
                "manufacturers": rows("SELECT * FROM manufacturers ORDER BY name") if catalog_allowed else [],
                "parts": parts_payload["parts"],
                "parts_meta": parts_payload["parts_meta"],
                "suppliers": rows("SELECT * FROM suppliers ORDER BY name") if stock_allowed else [],
                "stock_movements": rows("SELECT stock_movements.*, parts.name AS part_name FROM stock_movements LEFT JOIN parts ON parts.id=stock_movements.part_id ORDER BY stock_movements.created_at DESC LIMIT 300") if stock_allowed else [],
                "purchases": rows("SELECT * FROM purchase_entries ORDER BY date DESC, created_at DESC LIMIT 100") if stock_allowed else [],
                "purchase_items": [],
            }
        if page == "services":
            return payload | {"services": rows("SELECT * FROM services ORDER BY name") if self.has_permission(user, "servicos", "ver") else []}
        if page == "orders":
            order_allowed = self.has_permission(user, "os", "ver") or self.has_permission(user, "os", "criar")
            return payload | {
                "clients": [],
                "manufacturers": [],
                "models": [],
                "parts": [],
                "services": [],
                "orders": order_payload(include_details=False) if self.has_permission(user, "os", "ver") else [],
            }
        if page == "order-form":
            order_allowed = self.has_permission(user, "os", "ver") or self.has_permission(user, "os", "criar")
            return payload | {
                "clients": rows("SELECT * FROM clients ORDER BY name") if order_allowed else [],
                "manufacturers": rows("SELECT * FROM manufacturers ORDER BY name") if order_allowed else [],
                "models": rows("SELECT * FROM product_models ORDER BY name") if order_allowed else [],
                "services": rows("SELECT * FROM services ORDER BY name") if order_allowed else [],
            }
        if page == "finance":
            allowed = self.has_permission(user, "financeiro", "ver")
            return payload | {
                "finance": rows("SELECT * FROM finance_entries ORDER BY date DESC, due_date DESC") if allowed else [],
                "cash_sessions": rows("SELECT * FROM cash_sessions ORDER BY date DESC") if allowed else [],
            }
        if page == "pos":
            allowed = self.has_permission(user, "pdv", "ver")
            return payload | {
                "clients": rows("SELECT * FROM clients ORDER BY name") if allowed else [],
                "parts": rows(part_select) if allowed else [],
                "pos_sales": rows("SELECT * FROM pos_sales ORDER BY date DESC, number DESC") if allowed else [],
                "pos_sale_items": rows("SELECT * FROM pos_sale_items ORDER BY id") if allowed else [],
            }
        if page == "settings":
            allowed = self.has_permission(user, "configuracoes", "ver")
            return payload | {
                "roles": rows("SELECT id,name,level,permissions,created_at FROM roles ORDER BY level DESC") if allowed else [],
                "users": rows("SELECT users.id,users.name,users.email,users.role_id,users.status,users.failed_attempts,users.locked_until,users.last_login,users.password_changed_at,users.email_verified,users.created_at,roles.name AS role_name FROM users JOIN roles ON roles.id=users.role_id ORDER BY users.name") if allowed else [],
            }
        if page == "reports":
            if not self.has_permission(user, "relatorios", "ver"):
                return payload | {"report_summary": {}}
            return payload | {"report_summary": reports_summary_payload()}
        if page == "reports-full":
            if not self.has_permission(user, "relatorios", "ver"):
                return payload | {"report_summary": {}}
            report_payload = self.page_payload(user, "orders") | self.page_payload(user, "parts") | self.page_payload(user, "finance") | self.page_payload(user, "services") | self.page_payload(user, "catalog")
            if self.has_permission(user, "os", "ver"):
                report_payload["orders"] = order_payload(include_details=True)
            if self.has_permission(user, "estoque", "ver"):
                report_payload["stock_movements"] = rows("SELECT * FROM stock_movements ORDER BY created_at DESC")
                report_payload["purchases"] = rows("SELECT * FROM purchase_entries ORDER BY date DESC, created_at DESC")
            report_payload["report_summary"] = reports_summary_payload()
            return report_payload
        return payload

    def serve_file(self, base, rel):
        path = (base / rel.lstrip("/")).resolve()
        if not str(path).startswith(str(base.resolve())) or not path.exists() or path.is_dir():
            self.send_error(404)
            return
        content_type = "text/html; charset=utf-8" if path.suffix == ".html" else "application/octet-stream"
        if path.suffix == ".jpg":
            content_type = "image/jpeg"
        if path.suffix == ".png":
            content_type = "image/png"
        if path.suffix == ".css":
            content_type = "text/css"
        if path.suffix == ".js":
            content_type = "text/javascript"
        if path.suffix == ".json":
            content_type = "application/json; charset=utf-8"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self.serve_file(PUBLIC, "index.html")
            return
        if path.startswith("/uploads/"):
            self.serve_file(UPLOADS, path.replace("/uploads/", "", 1))
            return
        if path.startswith("/api/"):
            try:
                self.api_get(path)
            except Exception as error:
                traceback.print_exc()
                self.send_json({"error": f"Erro interno: {error}"}, 500)
            return
        self.serve_file(PUBLIC, path)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/auth/request-password-reset":
            self.request_password_reset(self.read_json())
            return
        if path == "/api/auth/reset-password":
            self.reset_password_by_token(self.read_json())
            return
        if path == "/api/auth/verify-email":
            self.verify_email_by_token(self.read_json())
            return
        if path == "/api/auth/resend-verification":
            self.resend_verification(self.read_json())
            return
        if path == "/api/login":
            data = self.read_json()
            user = row("SELECT users.*, roles.name AS role_name, roles.level, roles.permissions FROM users JOIN roles ON roles.id = users.role_id WHERE email=?", (data.get("email", ""),))
            if user and user.get("locked_until") and user["locked_until"] > now():
                self.send_json({"error": f"Usuario bloqueado ate {user['locked_until']}."}, 403)
                return
            if not user or not check_password(data.get("password", ""), user["password_hash"]):
                if user:
                    attempts = int(user.get("failed_attempts") or 0) + 1
                    locked_until = seconds_from_now(LOCK_SECONDS) if attempts >= LOCK_ATTEMPTS else None
                    execute("UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?", (attempts, locked_until, user["id"]))
                    if locked_until:
                        self.audit(user["id"], "Usuario bloqueado", f"{user['email']} bloqueado por tentativas de login.")
                self.send_json({"error": "E-mail ou senha invalidos."}, 401)
                return
            if user["status"] != "Ativo":
                self.send_json({"error": "Usuario inativo ou bloqueado."}, 403)
                return
            if not int(user.get("email_verified") if user.get("email_verified") is not None else 1):
                self.send_json({"error": "Confirme seu e-mail antes de entrar. Use a opcao de reenviar confirmacao na tela de login."}, 403)
                return
            expires_at = time.time() + SESSION_SECONDS
            token = create_session_token(user["id"], expires_at)
            login_at = now()
            user["last_login"] = login_at
            user["failed_attempts"] = 0
            user["locked_until"] = None
            if os.environ.get("APP_ENV") != "production":
                SESSIONS[token] = {"user_id": user["id"], "user": user, "expires_at": expires_at}
            with db() as conn:
                conn.execute("UPDATE users SET failed_attempts=0, locked_until=NULL, last_login=? WHERE id=?", (login_at, user["id"]))
                conn.execute("INSERT INTO audit_logs(id,user_id,action,detail) VALUES(?,?,?,?)", (uid("aud"), user["id"], "Login realizado", user["email"]))
                conn.commit()
            self.send_json({"token": token, "expires_at": int(expires_at), "user": {"id": user["id"], "name": user["name"], "role": user["role_name"]}})
            return
        if path == "/api/logout":
            auth = self.headers.get("Authorization", "")
            token = auth.replace("Bearer ", "", 1) if auth.startswith("Bearer ") else ""
            SESSIONS.pop(token, None)
            self.send_json({"ok": True})
            return
        user = self.require_user()
        if not user:
            return
        try:
            self.api_post(path, user)
            clear_runtime_caches()
        except Exception as error:
            traceback.print_exc()
            self.send_json({"error": f"Erro interno: {error}"}, 500)

    def do_DELETE(self):
        path = urlparse(self.path).path
        user = self.require_user()
        if not user:
            return
        self.api_delete(path, user)
        clear_runtime_caches()

    def api_get(self, path):
        if path == "/api/health":
            database_ok = False
            try:
                database_ok = bool(row("SELECT 1 AS ok"))
            except Exception:
                database_ok = False
            self.send_json({
                "ok": database_ok,
                "app": "Troca Ae SIS PRO",
                "database": DB_ENGINE,
                "time": now(),
                "version": "pwa-v1"
            }, 200 if database_ok else 503)
            return
        user = self.require_user()
        if not user:
            return
        if path == "/api/me":
            self.send_json(self.user_payload(user))
        elif path == "/api/dashboard":
            if DASHBOARD_CACHE["data"] and DASHBOARD_CACHE["expires_at"] > time.time():
                self.send_json(DASHBOARD_CACHE["data"])
                return
            with db() as conn:
                summary = row_dict(conn.execute(
                    """SELECT
                    (SELECT COUNT(*) FROM service_orders WHERE status NOT IN ('Finalizada','Cancelada')) AS open_orders,
                    (SELECT COUNT(*) FROM clients) AS clients,
                    (SELECT COUNT(*) FROM parts WHERE stock <= min_stock) AS parts_low_stock,
                    (SELECT COUNT(*) FROM parts WHERE stock = 0) AS parts_no_stock,
                    (SELECT COUNT(*) FROM parts) AS parts_total,
                    (SELECT COALESCE(SUM(amount - card_fee),0) FROM finance_entries WHERE type='Entrada') AS income,
                    (SELECT COALESCE(SUM(amount),0) FROM finance_entries WHERE type='Saida') AS expense,
                    (SELECT COALESCE(SUM((SELECT COALESCE(SUM(unit_price*quantity),0) FROM order_parts WHERE order_id=service_orders.id) + (SELECT COALESCE(SUM(labor),0) FROM order_services WHERE order_id=service_orders.id) - discount - paid),0) FROM service_orders) AS receivable"""
                ).fetchone())
                order_statuses = {
                    item["status"]: item["total"]
                    for item in conn.execute("SELECT status, COUNT(*) AS total FROM service_orders GROUP BY status ORDER BY total DESC").fetchall()
                }
                overdue_orders = [
                    row_dict(item)
                    for item in conn.execute("SELECT service_orders.number, clients.name AS client_name FROM service_orders LEFT JOIN clients ON clients.id=service_orders.client_id WHERE service_orders.due < ? AND service_orders.status NOT IN ('Finalizada','Cancelada') ORDER BY service_orders.due LIMIT 3", (today(),)).fetchall()
                ]
                low_parts = [
                    row_dict(item)
                    for item in conn.execute("SELECT name, stock, min_stock FROM parts WHERE stock <= min_stock ORDER BY stock, name LIMIT 3").fetchall()
                ]
            summary.update({"order_statuses": order_statuses, "overdue_orders": overdue_orders, "low_parts": low_parts})
            DASHBOARD_CACHE["data"] = summary
            DASHBOARD_CACHE["expires_at"] = time.time() + DASHBOARD_CACHE_SECONDS
            self.send_json(summary)
        elif path.startswith("/api/page-data/"):
            page = path.split("/")[-1]
            self.send_json(self.page_payload(user, page))
        elif path == "/api/parts-list":
            if not self.has_permission(user, "estoque", "ver"):
                self.send_json({"error": "Sem permissao para ver pecas."}, 403)
                return
            query = parse_qs(urlparse(self.path).query)
            self.send_json(parts_list_payload(
                query=query.get("q", [""])[0],
                manufacturer_id=query.get("manufacturer_id", [""])[0],
                stock_filter=query.get("stock", [""])[0],
                page=query.get("page", ["1"])[0],
                page_size=query.get("page_size", ["20"])[0],
            ))
        elif path == "/api/order-parts":
            if not (self.has_permission(user, "os", "ver") or self.has_permission(user, "os", "criar") or self.has_permission(user, "estoque", "ver")):
                self.send_json({"error": "Sem permissao para ver pecas."}, 403)
                return
            query = parse_qs(urlparse(self.path).query)
            model_id = query.get("model_id", [""])[0]
            manufacturer_id = query.get("manufacturer_id", [""])[0]
            select_sql = "SELECT id,name,manufacturer_id,compatible_models,price,stock,warranty_days,usage_type FROM parts WHERE COALESCE(usage_type,'Ambos') <> 'Venda'"
            if model_id:
                self.send_json(rows(f"{select_sql} AND compatible_models LIKE ? ORDER BY name", (f"%{model_id}%",)))
                return
            if manufacturer_id:
                self.send_json(rows(f"{select_sql} AND manufacturer_id=? ORDER BY name", (manufacturer_id,)))
                return
            self.send_json([])
        elif path in ("/api/bootstrap", "/api/bootstrap-lite"):
            lite = path == "/api/bootstrap-lite"
            clients_allowed = self.has_permission(user, "clientes", "ver") or self.has_permission(user, "os", "ver") or self.has_permission(user, "os", "criar")
            catalog_allowed = self.has_permission(user, "fabricantes", "ver") or self.has_permission(user, "os", "ver") or self.has_permission(user, "os", "criar") or self.has_permission(user, "estoque", "ver")
            stock_allowed = self.has_permission(user, "estoque", "ver") or self.has_permission(user, "os", "ver") or self.has_permission(user, "os", "criar")
            services_allowed = self.has_permission(user, "servicos", "ver") or self.has_permission(user, "os", "ver") or self.has_permission(user, "os", "criar")
            if lite:
                self.send_json(self.base_payload(user, False) | {
                    "clients": [],
                    "manufacturers": [],
                    "models": [],
                    "parts": [],
                    "services": [],
                    "suppliers": [],
                    "stock_movements": [],
                    "purchases": [],
                    "purchase_items": [],
                    "roles": [],
                    "users": [],
                    "orders": [],
                    "finance": [],
                    "cash_sessions": [],
                    "pos_sales": [],
                    "pos_sale_items": [],
                })
                return
            self.send_json(self.base_payload(user, True) | {
                "clients": rows("SELECT * FROM clients ORDER BY name") if clients_allowed else [],
                "manufacturers": rows("SELECT * FROM manufacturers ORDER BY name") if catalog_allowed else [],
                "models": rows("SELECT * FROM product_models ORDER BY name") if catalog_allowed else [],
                "parts": [] if lite else (rows("SELECT * FROM parts ORDER BY name") if stock_allowed else []),
                "services": rows("SELECT * FROM services ORDER BY name") if services_allowed else [],
                "suppliers": rows("SELECT * FROM suppliers ORDER BY name") if stock_allowed else [],
                "stock_movements": [] if lite else (rows("SELECT * FROM stock_movements ORDER BY created_at DESC") if stock_allowed else []),
                "purchases": [] if lite else (rows("SELECT * FROM purchase_entries ORDER BY date DESC, created_at DESC") if stock_allowed else []),
                "purchase_items": [] if lite else (rows("SELECT * FROM purchase_items ORDER BY id") if stock_allowed else []),
                "roles": rows("SELECT id,name,level,permissions,created_at FROM roles ORDER BY level DESC") if self.has_permission(user, "configuracoes", "ver") else [],
                "users": rows("SELECT users.id,users.name,users.email,users.role_id,users.status,users.failed_attempts,users.locked_until,users.last_login,users.password_changed_at,users.email_verified,users.created_at,roles.name AS role_name FROM users JOIN roles ON roles.id=users.role_id ORDER BY users.name") if self.has_permission(user, "configuracoes", "ver") else [],
                "orders": [] if lite else (order_payload(include_details=False) if self.has_permission(user, "os", "ver") else []),
                "finance": [] if lite else (rows("SELECT * FROM finance_entries ORDER BY date DESC, due_date DESC") if self.has_permission(user, "financeiro", "ver") else []),
                "cash_sessions": [] if lite else (rows("SELECT * FROM cash_sessions ORDER BY date DESC") if self.has_permission(user, "financeiro", "ver") else []),
                "pos_sales": [] if lite else (rows("SELECT * FROM pos_sales ORDER BY date DESC, number DESC") if self.has_permission(user, "pdv", "ver") else []),
                "pos_sale_items": [] if lite else (rows("SELECT * FROM pos_sale_items ORDER BY id") if self.has_permission(user, "pdv", "ver") else []),
            })
        elif path == "/api/orders":
            if not self.require_permission(user, "os", "ver"):
                return
            self.send_json(order_payload())
        elif path.startswith("/api/orders/"):
            if not self.require_permission(user, "os", "ver"):
                return
            order_id = path.split("/")[3]
            order = order_payload(order_id)
            if not order:
                self.send_json({"error": "OS nao encontrada."}, 404)
                return
            self.send_json(order)
        elif path == "/api/finance":
            if not self.require_permission(user, "financeiro", "ver"):
                return
            self.send_json(rows("SELECT * FROM finance_entries ORDER BY date DESC, due_date DESC"))
        elif path == "/api/backups":
            if not self.require_permission(user, "configuracoes", "ver"):
                return
            self.send_json(backup_rows())
        elif path == "/api/backups/download":
            if not self.require_permission(user, "configuracoes", "ver"):
                return
            name = parse_qs(urlparse(self.path).query).get("name", [""])[0]
            backup = (BACKUPS / name).resolve()
            if not str(backup).startswith(str(BACKUPS.resolve())) or not backup.exists() or backup.suffix != ".zip":
                self.send_json({"error": "Backup nao encontrado."}, 404)
                return
            data = backup.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{backup.name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_json({"error": "Rota nao encontrada."}, 404)

    def api_post(self, path, user):
        data = self.read_json()
        if path == "/api/me/password":
            self.change_my_password(data, user)
        elif path.startswith("/api/clients"):
            if not self.require_permission(user, "clientes", "editar" if self.id_from_path(path) else "criar"):
                return
            self.save_client(path, data, user)
        elif path.startswith("/api/manufacturers"):
            if not self.require_permission(user, "fabricantes", "editar" if self.id_from_path(path) else "criar"):
                return
            self.save_manufacturer(path, data, user)
        elif path.startswith("/api/models"):
            if not self.require_permission(user, "fabricantes", "editar" if self.id_from_path(path) else "criar"):
                return
            self.save_model(path, data, user)
        elif path.startswith("/api/parts"):
            if not self.require_permission(user, "estoque", "editar" if self.id_from_path(path) else "criar"):
                return
            self.save_part(path, data, user)
        elif path == "/api/purchases":
            if not self.require_permission(user, "estoque", "movimentar"):
                return
            self.save_purchase(data, user)
        elif path == "/api/pos-sales":
            if not self.require_permission(user, "pdv", "criar"):
                return
            self.save_pos_sale(data, user)
        elif path.startswith("/api/services"):
            if not self.require_permission(user, "servicos", "editar" if self.id_from_path(path) else "criar"):
                return
            self.save_service(path, data, user)
        elif path.startswith("/api/users/") and path.endswith("/unlock"):
            if not self.require_permission(user, "configuracoes", "editar"):
                return
            self.unlock_user(path, user)
        elif path.startswith("/api/users"):
            if not self.require_permission(user, "configuracoes", "editar"):
                return
            self.save_user(path, data, user)
        elif path.startswith("/api/roles"):
            if not self.require_permission(user, "configuracoes", "editar"):
                return
            self.save_role(path, data, user)
        elif path == "/api/company":
            if not self.require_permission(user, "configuracoes", "editar"):
                return
            self.save_company(data, user)
        elif path == "/api/backups":
            if not self.require_permission(user, "configuracoes", "editar"):
                return
            try:
                backup = create_backup("manual")
            except (subprocess.CalledProcessError, FileNotFoundError) as error:
                self.send_json({"error": f"Falha ao criar backup: {error}"}, 500)
                return
            self.audit(user["id"], "Backup criado", backup.name)
            self.send_json({"ok": True, "backup": {"name": backup.name, "size": backup.stat().st_size, "created_at": now()}}, 201)
        elif path == "/api/backups/restore":
            if not self.require_permission(user, "configuracoes", "editar"):
                return
            name = data.get("name", "")
            try:
                if DB_ENGINE == "sqlite":
                    backup = stage_restore_backup(name)
                    self.audit(user["id"], "Restauracao de backup preparada", backup.name)
                    self.send_json({"ok": True, "restored": backup.name, "restart_required": True})
                    return
                backup = restore_backup_file(name)
            except (FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as error:
                self.send_json({"error": f"Falha ao restaurar backup: {error}"}, 500)
                return
            self.audit(user["id"], "Backup restaurado", backup.name)
            self.send_json({"ok": True, "restored": backup.name})
        elif path.startswith("/api/finance"):
            if not self.require_permission(user, "financeiro", "editar" if self.id_from_path(path) else "criar"):
                return
            self.save_finance(path, data, user)
        elif path.startswith("/api/cash-sessions"):
            if not self.require_permission(user, "financeiro", "pagar"):
                return
            self.save_cash_session(path, data, user)
        elif path == "/api/orders":
            if not self.require_permission(user, "os", "criar"):
                return
            number = row("SELECT COALESCE(MAX(number),1000)+1 AS next FROM service_orders")["next"]
            order_id = uid("os")
            with db() as conn:
                conn.execute(
                    """INSERT INTO service_orders
                    (id,number,client_id,device_manufacturer_id,device_model_id,device_brand,device_model,device_imei,device_color,device_password,device_condition,status,approval_status,priority,opened,due,technician_name,defect,diagnosis,solution,photos_notes,warranty_term,delivery_term,follow_up,discount,paid)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (order_id, number, data.get("client_id"), data.get("device_manufacturer_id", ""), data.get("device_model_id", ""), data.get("device_brand", ""), data.get("device_model", ""), data.get("device_imei", ""), data.get("device_color", ""), data.get("device_password", ""), data.get("device_condition", ""), data.get("status", "Aberta"), data.get("approval_status", "Pendente"), data.get("priority", "Normal"), data.get("opened", today()), data.get("due", today()), data.get("technician_name", ""), data.get("defect", ""), data.get("diagnosis", ""), data.get("solution", ""), data.get("photos_notes", ""), data.get("warranty_term", ""), data.get("delivery_term", ""), data.get("follow_up", ""), float(data.get("discount", 0) or 0), float(data.get("paid", 0) or 0)),
                )
                for part_id in data.get("parts", []):
                    part = conn.execute("SELECT id, price, cost, warranty_days FROM parts WHERE id=?", (part_id,)).fetchone()
                    if part:
                        conn.execute(
                            "INSERT INTO order_parts(id,order_id,part_id,quantity,unit_price,unit_cost,warranty_days) VALUES(?,?,?,?,?,?,?)",
                            (uid("op"), order_id, part["id"], 1, part["price"], part["cost"], part["warranty_days"]),
                        )
                for service_id in data.get("services", []):
                    service = conn.execute("SELECT id, labor, warranty_days FROM services WHERE id=?", (service_id,)).fetchone()
                    if service:
                        conn.execute(
                            "INSERT INTO order_services(id,order_id,service_id,labor,warranty_days) VALUES(?,?,?,?,?)",
                            (uid("osv"), order_id, service["id"], service["labor"], service["warranty_days"]),
                        )
                insert_status_history(conn, order_id, user["id"], "", data.get("status", "Aberta"), "OS criada")
                conn.commit()
            self.audit(user["id"], "OS criada", f"OS {number}")
            self.send_json(order_payload(order_id), 201)
        elif path.startswith("/api/orders/") and path.endswith("/approve"):
            if not self.require_permission(user, "os", "aprovar"):
                return
            self.sign_order(path, data, user, "approval")
        elif path.startswith("/api/orders/") and path.endswith("/deliver"):
            if not self.require_permission(user, "os", "finalizar"):
                return
            self.sign_order(path, data, user, "delivery")
        elif path.startswith("/api/orders/") and not path.endswith("/payment") and not path.endswith("/finish") and not path.endswith("/photos") and not path.endswith("/approve") and not path.endswith("/deliver"):
            if not self.require_permission(user, "os", "editar"):
                return
            self.update_order(path, data, user)
        elif path.startswith("/api/orders/") and path.endswith("/payment"):
            if not self.require_permission(user, "financeiro", "pagar"):
                return
            order_id = path.split("/")[3]
            order = row("SELECT id, number, paid FROM service_orders WHERE id=?", (order_id,))
            if not order:
                self.send_json({"error": "OS nao encontrada."}, 404)
                return
            amount = float(data.get("amount", 0) or 0)
            if amount <= 0:
                self.send_json({"error": "Valor invalido."}, 400)
                return
            payment_id = uid("fin")
            execute("UPDATE service_orders SET paid=paid+?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (amount, order_id))
            execute(
                "INSERT INTO finance_entries(id,order_id,type,date,due_date,category,description,amount,status,payment_method) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (payment_id, order_id, "Entrada", data.get("date", today()), data.get("date", today()), "Pagamento OS", f"Pagamento OS {order['number']}", amount, "Recebido", data.get("payment_method", "PIX")),
            )
            self.audit(user["id"], "Pagamento registrado", f"OS {order['number']}: {amount}")
            self.send_json(order_payload(order_id))
        elif path.startswith("/api/orders/") and path.endswith("/finish"):
            if not self.require_permission(user, "os", "finalizar"):
                return
            order_id = path.split("/")[3]
            order = row("SELECT id, number, status FROM service_orders WHERE id=?", (order_id,))
            if not order:
                self.send_json({"error": "OS nao encontrada."}, 404)
                return
            with db() as conn:
                conn.execute("UPDATE service_orders SET status='Finalizada', updated_at=CURRENT_TIMESTAMP WHERE id=?", (order_id,))
                insert_status_history(conn, order_id, user["id"], order["status"], "Finalizada", "OS finalizada")
                for item in conn.execute("SELECT part_id, quantity, unit_cost FROM order_parts WHERE order_id=?", (order_id,)).fetchall():
                    conn.execute("UPDATE parts SET stock = MAX(stock - ?, 0) WHERE id=?", (item["quantity"], item["part_id"]))
                    conn.execute(
                        "INSERT INTO stock_movements(id,part_id,order_id,type,quantity,unit_cost,reason) VALUES(?,?,?,?,?,?,?)",
                        (uid("mov"), item["part_id"], order_id, "Saida", item["quantity"], item["unit_cost"], f"Baixa por OS {order['number']}"),
                    )
                conn.commit()
            self.audit(user["id"], "OS finalizada", f"OS {order['number']}")
            self.send_json(order_payload(order_id))
        elif path.startswith("/api/orders/") and path.endswith("/photos"):
            if not self.require_permission(user, "os", "editar"):
                return
            order_id = path.split("/")[3]
            order = row("SELECT id, number FROM service_orders WHERE id=?", (order_id,))
            if not order:
                self.send_json({"error": "OS nao encontrada."}, 404)
                return
            data_url = data.get("data_url", "")
            if "," in data_url:
                header, encoded = data_url.split(",", 1)
            else:
                header, encoded = "data:image/jpeg;base64", data_url
            mime = data.get("mime_type") or header.replace("data:", "").replace(";base64", "") or "image/jpeg"
            ext = ".jpg" if "jpeg" in mime or "jpg" in mime else ".png"
            photo_id = uid("foto")
            file_name = data.get("file_name") or f"os-{order['number']}-{photo_id}{ext}"
            safe_name = f"{photo_id}-{Path(file_name).name}"
            file_path = UPLOADS / safe_name
            file_path.write_bytes(base64.b64decode(encoded))
            execute(
                "INSERT INTO order_photos(id,order_id,file_name,file_path,mime_type,size) VALUES(?,?,?,?,?,?)",
                (photo_id, order_id, file_name, f"/uploads/{safe_name}", mime, file_path.stat().st_size),
            )
            self.audit(user["id"], "Foto adicionada", f"OS {order['number']}: {file_name}")
            self.send_json(row("SELECT * FROM order_photos WHERE id=?", (photo_id,)), 201)
        else:
            self.send_json({"error": "Rota nao encontrada."}, 404)

    def sign_order(self, path, data, user, kind):
        order_id = path.split("/")[3]
        order = row("SELECT id, number, status FROM service_orders WHERE id=?", (order_id,))
        if not order:
            self.send_json({"error": "OS nao encontrada."}, 404)
            return
        signature = data.get("signature", "")
        if not signature.startswith("data:image/"):
            self.send_json({"error": "Assinatura invalida."}, 400)
            return
        if kind == "approval":
            new_status = "Aprovada"
            with db() as conn:
                conn.execute(
                    "UPDATE service_orders SET approval_status='Aprovada', status=?, approval_signature=?, approval_signed_at=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (new_status, signature, now(), order_id),
                )
                insert_status_history(conn, order_id, user["id"], order["status"], new_status, "Orcamento aprovado com assinatura do cliente")
                conn.commit()
            self.audit(user["id"], "Orcamento aprovado", f"OS {order['number']}")
        else:
            new_status = "Entregue"
            with db() as conn:
                conn.execute(
                    "UPDATE service_orders SET status=?, delivery_signature=?, delivery_signed_at=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (new_status, signature, now(), order_id),
                )
                insert_status_history(conn, order_id, user["id"], order["status"], new_status, "Termo de entrega assinado pelo cliente")
                conn.commit()
            self.audit(user["id"], "Termo de entrega assinado", f"OS {order['number']}")
        self.send_json(order_payload(order_id))

    def update_order(self, path, data, user):
        order_id = path.split("/")[3]
        order = row("SELECT id, number, status FROM service_orders WHERE id=?", (order_id,))
        if not order:
            self.send_json({"error": "OS nao encontrada."}, 404)
            return
        with db() as conn:
            conn.execute(
                """UPDATE service_orders SET
                client_id=?,device_manufacturer_id=?,device_model_id=?,device_brand=?,device_model=?,device_imei=?,device_color=?,device_password=?,device_condition=?,
                status=?,approval_status=?,priority=?,opened=?,due=?,technician_name=?,defect=?,diagnosis=?,solution=?,photos_notes=?,warranty_term=?,delivery_term=?,follow_up=?,discount=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
                (
                    data.get("client_id"),
                    data.get("device_manufacturer_id", ""),
                    data.get("device_model_id", ""),
                    data.get("device_brand", ""),
                    data.get("device_model", ""),
                    data.get("device_imei", ""),
                    data.get("device_color", ""),
                    data.get("device_password", ""),
                    data.get("device_condition", ""),
                    data.get("status", "Aberta"),
                    data.get("approval_status", "Pendente"),
                    data.get("priority", "Normal"),
                    data.get("opened", today()),
                    data.get("due", today()),
                    data.get("technician_name", ""),
                    data.get("defect", ""),
                    data.get("diagnosis", ""),
                    data.get("solution", ""),
                    data.get("photos_notes", ""),
                    data.get("warranty_term", ""),
                    data.get("delivery_term", ""),
                    data.get("follow_up", ""),
                    float(data.get("discount", 0) or 0),
                    order_id,
                ),
            )
            new_status = data.get("status", "Aberta")
            insert_status_history(conn, order_id, user["id"], order["status"], new_status, "Status editado na OS")
            conn.execute("DELETE FROM order_parts WHERE order_id=?", (order_id,))
            for part_id in data.get("parts", []):
                part = conn.execute("SELECT id, price, cost, warranty_days FROM parts WHERE id=?", (part_id,)).fetchone()
                if part:
                    conn.execute(
                        "INSERT INTO order_parts(id,order_id,part_id,quantity,unit_price,unit_cost,warranty_days) VALUES(?,?,?,?,?,?,?)",
                        (uid("op"), order_id, part["id"], 1, part["price"], part["cost"], part["warranty_days"]),
                    )
            conn.execute("DELETE FROM order_services WHERE order_id=?", (order_id,))
            for service_id in data.get("services", []):
                service = conn.execute("SELECT id, labor, warranty_days FROM services WHERE id=?", (service_id,)).fetchone()
                if service:
                    conn.execute(
                        "INSERT INTO order_services(id,order_id,service_id,labor,warranty_days) VALUES(?,?,?,?,?)",
                        (uid("osv"), order_id, service["id"], service["labor"], service["warranty_days"]),
                    )
            conn.commit()
        self.audit(user["id"], "OS editada", f"OS {order['number']}")
        self.send_json(order_payload(order_id))

    def save_client(self, path, data, user):
        item_id = self.id_from_path(path)
        values = (
            data.get("name", ""),
            data.get("phone", ""),
            data.get("email", ""),
            data.get("document", ""),
            data.get("zip", ""),
            data.get("street", ""),
            data.get("number", ""),
            data.get("neighborhood", ""),
            data.get("city", ""),
            data.get("state", ""),
            data.get("complement", ""),
            data.get("notes", ""),
        )
        if item_id:
            execute(
                """UPDATE clients SET name=?,phone=?,email=?,document=?,zip=?,street=?,number=?,neighborhood=?,city=?,state=?,complement=?,notes=? WHERE id=?""",
                values + (item_id,),
            )
            self.audit(user["id"], "Cliente editado", data.get("name", ""))
            self.send_json(row("SELECT * FROM clients WHERE id=?", (item_id,)))
            return
        client_id = uid("cli")
        execute(
            "INSERT INTO clients(id,name,phone,email,document,zip,street,number,neighborhood,city,state,complement,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (client_id,) + values,
        )
        self.audit(user["id"], "Cliente criado", data.get("name", ""))
        self.send_json(row("SELECT * FROM clients WHERE id=?", (client_id,)), 201)

    def id_from_path(self, path):
        parts = path.strip("/").split("/")
        return parts[2] if len(parts) >= 3 else ""

    def save_manufacturer(self, path, data, user):
        item_id = self.id_from_path(path) or uid("man")
        exists = row("SELECT id FROM manufacturers WHERE id=?", (item_id,))
        if exists:
            execute("UPDATE manufacturers SET name=?,support_phone=?,site=?,notes=? WHERE id=?", (data.get("name", ""), data.get("support_phone", ""), data.get("site", ""), data.get("notes", ""), item_id))
            action = "Fabricante editado"
        else:
            execute("INSERT INTO manufacturers(id,name,support_phone,site,notes) VALUES(?,?,?,?,?)", (item_id, data.get("name", ""), data.get("support_phone", ""), data.get("site", ""), data.get("notes", "")))
            action = "Fabricante criado"
        self.audit(user["id"], action, data.get("name", ""))
        self.send_json(row("SELECT * FROM manufacturers WHERE id=?", (item_id,)), 201 if not exists else 200)

    def save_model(self, path, data, user):
        item_id = self.id_from_path(path) or uid("mod")
        exists = row("SELECT id FROM product_models WHERE id=?", (item_id,))
        params = (data.get("manufacturer_id", ""), data.get("name", ""), data.get("category", "Smartphone"), int(data.get("year", 0) or 0), data.get("notes", ""))
        if exists:
            execute("UPDATE product_models SET manufacturer_id=?,name=?,category=?,year=?,notes=? WHERE id=?", (*params, item_id))
            action = "Modelo editado"
        else:
            execute("INSERT INTO product_models(id,manufacturer_id,name,category,year,notes) VALUES(?,?,?,?,?,?)", (item_id, *params))
            action = "Modelo criado"
        self.audit(user["id"], action, data.get("name", ""))
        self.send_json(row("SELECT * FROM product_models WHERE id=?", (item_id,)), 201 if not exists else 200)

    def save_purchase(self, data, user):
        items = data.get("items", [])
        if not items:
            self.send_json({"error": "Informe pelo menos uma peca na compra."}, 400)
            return
        purchase_id = uid("compra")
        supplier_id = data.get("supplier_id") or None
        total = 0
        with db() as conn:
            for item in items:
                quantity = int(item.get("quantity", 0) or 0)
                unit_cost = float(item.get("unit_cost", 0) or 0)
                if quantity <= 0:
                    continue
                total += quantity * unit_cost
            conn.execute(
                "INSERT INTO purchase_entries(id,supplier_id,date,document,status,notes,total) VALUES(?,?,?,?,?,?,?)",
                (purchase_id, supplier_id, data.get("date", today()), data.get("document", ""), data.get("status", "Recebido"), data.get("notes", ""), total),
            )
            for item in items:
                part_id = item.get("part_id")
                quantity = int(item.get("quantity", 0) or 0)
                unit_cost = float(item.get("unit_cost", 0) or 0)
                lot = item.get("lot", "")
                if not part_id or quantity <= 0:
                    continue
                part = conn.execute("SELECT id, stock, cost FROM parts WHERE id=?", (part_id,)).fetchone()
                if not part:
                    continue
                old_stock = int(part["stock"] or 0)
                old_cost = float(part["cost"] or 0)
                new_stock = old_stock + quantity
                average_cost = ((old_stock * old_cost) + (quantity * unit_cost)) / new_stock if new_stock else unit_cost
                conn.execute(
                    "INSERT INTO purchase_items(id,purchase_id,part_id,quantity,unit_cost,lot) VALUES(?,?,?,?,?,?)",
                    (uid("ci"), purchase_id, part_id, quantity, unit_cost, lot),
                )
                conn.execute("UPDATE parts SET stock=stock+?, cost=?, supplier_id=? WHERE id=?", (quantity, average_cost, supplier_id, part_id))
                conn.execute(
                    "INSERT INTO stock_movements(id,part_id,type,quantity,unit_cost,supplier_id,lot,purchase_id,reason) VALUES(?,?,?,?,?,?,?,?,?)",
                    (uid("mov"), part_id, "Entrada", quantity, unit_cost, supplier_id, lot, purchase_id, f"Compra {data.get('document', '')}".strip()),
                )
            if total > 0:
                conn.execute(
                    "INSERT INTO finance_entries(id,type,date,due_date,category,description,amount,status,payment_method) VALUES(?,?,?,?,?,?,?,?,?)",
                    (uid("fin"), "Saida", data.get("date", today()), data.get("due_date", data.get("date", today())), "Compra de estoque", f"Compra estoque {data.get('document', '')}".strip(), total, data.get("financial_status", "Pendente"), data.get("payment_method", "")),
                )
            conn.commit()
        self.audit(user["id"], "Entrada de compra registrada", f"Compra {data.get('document', '')} - {total}")
        self.send_json(row("SELECT * FROM purchase_entries WHERE id=?", (purchase_id,)), 201)

    def save_pos_sale(self, data, user):
        items = data.get("items", [])
        if not items:
            self.send_json({"error": "Informe pelo menos um item no PDV."}, 400)
            return
        sale_id = uid("pdv")
        number = row("SELECT COALESCE(MAX(number),0)+1 AS next FROM pos_sales")["next"]
        discount = float(data.get("discount", 0) or 0)
        total = 0
        with db() as conn:
            prepared = []
            for item in items:
                part_id = item.get("part_id")
                quantity = int(item.get("quantity", 0) or 0)
                if not part_id or quantity <= 0:
                    continue
                part = conn.execute("SELECT id, price, cost, stock FROM parts WHERE id=?", (part_id,)).fetchone()
                if not part:
                    continue
                unit_price = float(item.get("unit_price", part["price"]) or part["price"] or 0)
                total += quantity * unit_price
                prepared.append((part, quantity, unit_price))
            total = max(total - discount, 0)
            conn.execute(
                "INSERT INTO pos_sales(id,number,client_id,date,payment_method,discount,total,status,notes) VALUES(?,?,?,?,?,?,?,?,?)",
                (sale_id, number, data.get("client_id") or None, data.get("date", today()), data.get("payment_method", "PIX"), discount, total, data.get("status", "Recebido"), data.get("notes", "")),
            )
            for part, quantity, unit_price in prepared:
                conn.execute(
                    "INSERT INTO pos_sale_items(id,sale_id,part_id,quantity,unit_price,unit_cost) VALUES(?,?,?,?,?,?)",
                    (uid("pdi"), sale_id, part["id"], quantity, unit_price, part["cost"]),
                )
                conn.execute("UPDATE parts SET stock = MAX(stock - ?, 0) WHERE id=?", (quantity, part["id"]))
                conn.execute(
                    "INSERT INTO stock_movements(id,part_id,type,quantity,unit_cost,sale_id,reason) VALUES(?,?,?,?,?,?,?)",
                    (uid("mov"), part["id"], "Saida", quantity, part["cost"], sale_id, f"Venda PDV #{number}"),
                )
            conn.execute(
                "INSERT INTO finance_entries(id,type,date,due_date,category,description,amount,status,payment_method) VALUES(?,?,?,?,?,?,?,?,?)",
                (uid("fin"), "Entrada", data.get("date", today()), data.get("date", today()), "Venda PDV", f"Venda PDV #{number}", total, data.get("status", "Recebido"), data.get("payment_method", "PIX")),
            )
            conn.commit()
        self.audit(user["id"], "Venda PDV registrada", f"PDV #{number} - {total}")
        self.send_json(row("SELECT * FROM pos_sales WHERE id=?", (sale_id,)), 201)

    def save_part(self, path, data, user):
        item_id = self.id_from_path(path) or uid("part")
        exists = row("SELECT id FROM parts WHERE id=?", (item_id,))
        compatible = json.dumps(data.get("compatible_models", []), ensure_ascii=False)
        supplier_id = data.get("supplier_id") or None
        params = (data.get("sku", ""), data.get("name", ""), data.get("category", ""), data.get("manufacturer_id", ""), compatible, float(data.get("cost", 0) or 0), float(data.get("price", 0) or 0), int(data.get("stock", 0) or 0), int(data.get("min_stock", 0) or 0), supplier_id, int(data.get("warranty_days", 90) or 90), data.get("usage_type", "Ambos"))
        if exists:
            execute("UPDATE parts SET sku=?,name=?,category=?,manufacturer_id=?,compatible_models=?,cost=?,price=?,stock=?,min_stock=?,supplier_id=?,warranty_days=?,usage_type=? WHERE id=?", (*params, item_id))
            action = "Peca editada"
        else:
            execute("INSERT INTO parts(id,sku,name,category,manufacturer_id,compatible_models,cost,price,stock,min_stock,supplier_id,warranty_days,usage_type) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (item_id, *params))
            action = "Peca criada"
        self.audit(user["id"], action, data.get("name", ""))
        self.send_json(row("SELECT * FROM parts WHERE id=?", (item_id,)), 201 if not exists else 200)

    def save_service(self, path, data, user):
        item_id = self.id_from_path(path) or uid("srv")
        exists = row("SELECT id FROM services WHERE id=?", (item_id,))
        params = (data.get("name", ""), data.get("category", ""), float(data.get("labor", 0) or 0), int(data.get("warranty_days", 90) or 90), data.get("duration", ""))
        if exists:
            execute("UPDATE services SET name=?,category=?,labor=?,warranty_days=?,duration=? WHERE id=?", (*params, item_id))
            action = "Servico editado"
        else:
            execute("INSERT INTO services(id,name,category,labor,warranty_days,duration) VALUES(?,?,?,?,?,?)", (item_id, *params))
            action = "Servico criado"
        self.audit(user["id"], action, data.get("name", ""))
        self.send_json(row("SELECT * FROM services WHERE id=?", (item_id,)), 201 if not exists else 200)

    def save_user(self, path, data, user):
        item_id = self.id_from_path(path) or uid("usr")
        exists = row("SELECT id,email,password_hash,email_verified FROM users WHERE id=?", (item_id,))
        new_password = data.get("password", "")
        email = (data.get("email", "") or "").strip().lower()
        if not data.get("name", "").strip() or not email:
            self.send_json({"error": "Informe nome e e-mail do usuario."}, 400)
            return
        duplicate = row("SELECT id FROM users WHERE LOWER(email)=? AND id<>?", (email, item_id))
        if duplicate:
            self.send_json({"error": "Ja existe outro usuario com este e-mail."}, 400)
            return
        role_id = data.get("role_id", "")
        role = row("SELECT id FROM roles WHERE id=?", (role_id,))
        if not role:
            self.send_json({"error": "Selecione um perfil valido para o usuario."}, 400)
            return
        if new_password:
            error = password_strength_error(new_password)
            if error:
                self.send_json({"error": error}, 400)
                return
        status = data.get("status", "Ativo")
        if exists:
            email_changed = email and email != (exists.get("email") or "").lower()
            email_verified = 0 if email_changed else int(exists.get("email_verified") or 0)
            execute(
                "UPDATE users SET name=?,email=?,role_id=?,status=?,email_verified=? WHERE id=?",
                (data.get("name", ""), email, role_id, status, email_verified, item_id),
            )
            if status == "Ativo":
                execute("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?", (item_id,))
            if new_password:
                execute("UPDATE users SET password_hash=?, password_changed_at=? WHERE id=?", (password_hash(new_password), now(), item_id))
            if email_changed:
                target = row("SELECT id,name,email FROM users WHERE id=?", (item_id,))
                if target:
                    self.send_verification_email(target)
            action = "Usuario editado"
        else:
            password = new_password or "Senha123"
            error = password_strength_error(password)
            if error:
                self.send_json({"error": error}, 400)
                return
            execute(
                "INSERT INTO users(id,name,email,password_hash,role_id,status,password_changed_at,email_verified) VALUES(?,?,?,?,?,?,?,?)",
                (item_id, data.get("name", ""), email, password_hash(password), role_id, status, now(), 0),
            )
            target = row("SELECT id,name,email FROM users WHERE id=?", (item_id,))
            if target:
                self.send_verification_email(target)
            action = "Usuario criado"
        self.audit(user["id"], action, data.get("email", ""))
        self.send_json(row("SELECT users.id,users.name,users.email,users.role_id,users.status,users.failed_attempts,users.locked_until,users.last_login,users.password_changed_at,users.email_verified,roles.name AS role_name FROM users JOIN roles ON roles.id=users.role_id WHERE users.id=?", (item_id,)), 201 if not exists else 200)

    def change_my_password(self, data, user):
        current = data.get("current_password", "")
        new_password = data.get("new_password", "")
        confirm = data.get("confirm_password", "")
        full_user = row("SELECT id,email,password_hash FROM users WHERE id=?", (user["id"],))
        if not full_user or not check_password(current, full_user["password_hash"]):
            self.send_json({"error": "Senha atual invalida."}, 400)
            return
        if new_password != confirm:
            self.send_json({"error": "A confirmacao da senha nao confere."}, 400)
            return
        if check_password(new_password, full_user["password_hash"]):
            self.send_json({"error": "A nova senha precisa ser diferente da atual."}, 400)
            return
        error = password_strength_error(new_password)
        if error:
            self.send_json({"error": error}, 400)
            return
        execute("UPDATE users SET password_hash=?, password_changed_at=?, failed_attempts=0, locked_until=NULL WHERE id=?", (password_hash(new_password), now(), user["id"]))
        self.audit(user["id"], "Senha alterada", full_user["email"])
        self.send_json({"ok": True})

    def unlock_user(self, path, user):
        item_id = self.id_from_path(path)
        target = row("SELECT id,email FROM users WHERE id=?", (item_id,))
        if not target:
            self.send_json({"error": "Usuario nao encontrado."}, 404)
            return
        execute("UPDATE users SET failed_attempts=0, locked_until=NULL, status='Ativo' WHERE id=?", (item_id,))
        self.audit(user["id"], "Usuario desbloqueado", target["email"])
        self.send_json(row("SELECT users.id,users.name,users.email,users.role_id,users.status,users.failed_attempts,users.locked_until,users.last_login,users.password_changed_at,users.email_verified,roles.name AS role_name FROM users JOIN roles ON roles.id=users.role_id WHERE users.id=?", (item_id,)))

    def save_role(self, path, data, user):
        item_id = self.id_from_path(path) or uid("role")
        exists = row("SELECT id FROM roles WHERE id=?", (item_id,))
        permissions = json.dumps(data.get("permissions", {}), ensure_ascii=False)
        if exists:
            execute("UPDATE roles SET name=?,level=?,permissions=? WHERE id=?", (data.get("name", ""), int(data.get("level", 0) or 0), permissions, item_id))
            action = "Perfil editado"
        else:
            execute("INSERT INTO roles(id,name,level,permissions) VALUES(?,?,?,?)", (item_id, data.get("name", ""), int(data.get("level", 0) or 0), permissions))
            action = "Perfil criado"
        self.audit(user["id"], action, data.get("name", ""))
        self.send_json(row("SELECT id,name,level,permissions FROM roles WHERE id=?", (item_id,)), 201 if not exists else 200)

    def save_company(self, data, user):
        execute(
            """UPDATE company_settings SET
            system_name=?, trade_name=?, legal_name=?, document=?, phone=?, email=?, zip=?, street=?, number=?,
            neighborhood=?, city=?, state=?, logo_path=?, primary_color=?, dark_color=?, theme=?, warranty_term=?, print_footer=?
            WHERE id=1""",
            (
                data.get("system_name", ""),
                data.get("trade_name", ""),
                data.get("legal_name", ""),
                data.get("document", ""),
                data.get("phone", ""),
                data.get("email", ""),
                data.get("zip", ""),
                data.get("street", ""),
                data.get("number", ""),
                data.get("neighborhood", ""),
                data.get("city", ""),
                data.get("state", ""),
                data.get("logo_path", "/troca-ae-logo.jpg"),
                data.get("primary_color", "#f9732f"),
                data.get("dark_color", "#18231f"),
                data.get("theme", "light"),
                data.get("warranty_term", ""),
                data.get("print_footer", ""),
            ),
        )
        self.audit(user["id"], "Configuracoes da empresa atualizadas", data.get("trade_name", ""))
        self.send_json(row("SELECT * FROM company_settings WHERE id=1"))

    def save_finance(self, path, data, user):
        item_id = self.id_from_path(path)
        reconciled = 1 if str(data.get("reconciled", "")).lower() in ("1", "true", "on", "sim") else 0
        card_fee = float(data.get("card_fee", 0) or 0)
        installments = max(1, int(data.get("installments", 1) or 1))
        recurrence_frequency = data.get("recurrence_frequency", "")
        recurrence_id = data.get("recurrence_id") or (uid("rec") if installments > 1 or recurrence_frequency else "")
        values = (
            data.get("order_id") or None,
            data.get("cash_session_id") or None,
            recurrence_id or None,
            data.get("type", "Entrada"),
            data.get("date", today()),
            data.get("due_date", data.get("date", today())),
            data.get("category", ""),
            data.get("description", ""),
            float(data.get("amount", 0) or 0),
            data.get("status", "Pendente"),
            data.get("payment_method", ""),
            card_fee,
            reconciled,
            now() if reconciled else None,
            recurrence_frequency,
            data.get("recurrence_until", ""),
            int(data.get("installment", 1) or 1),
            installments,
        )
        if item_id:
            execute(
                """UPDATE finance_entries SET order_id=?,cash_session_id=?,recurrence_id=?,type=?,date=?,due_date=?,category=?,description=?,amount=?,status=?,payment_method=?,card_fee=?,reconciled=?,reconciled_at=?,recurrence_frequency=?,recurrence_until=?,installment=?,installments=? WHERE id=?""",
                values + (item_id,),
            )
            self.audit(user["id"], "Lancamento financeiro editado", data.get("description", ""))
            self.send_json(row("SELECT * FROM finance_entries WHERE id=?", (item_id,)))
            return
        finance_id = uid("fin")
        created_ids = []
        base_date = data.get("date", today())
        base_due = data.get("due_date", base_date)
        with db() as conn:
            for index in range(installments):
                entry_id = finance_id if index == 0 else uid("fin")
                entry_values = list(values)
                entry_values[4] = add_months(base_date, index)
                entry_values[5] = add_months(base_due, index)
                entry_values[7] = f"{data.get('description', '')} ({index + 1}/{installments})" if installments > 1 else data.get("description", "")
                entry_values[16] = index + 1
                conn.execute(
                    """INSERT INTO finance_entries
                    (id,order_id,cash_session_id,recurrence_id,type,date,due_date,category,description,amount,status,payment_method,card_fee,reconciled,reconciled_at,recurrence_frequency,recurrence_until,installment,installments)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (entry_id,) + tuple(entry_values),
                )
                created_ids.append(entry_id)
            conn.commit()
        self.audit(user["id"], "Lancamento financeiro criado", data.get("description", ""))
        self.send_json(row("SELECT * FROM finance_entries WHERE id=?", (finance_id,)) | {"created_count": len(created_ids)}, 201)

    def save_cash_session(self, path, data, user):
        item_id = self.id_from_path(path)
        session_date = data.get("date", today())
        opening = float(data.get("opening_amount", 0) or 0)
        closing = float(data.get("closing_amount", 0) or 0)
        expected = float(data.get("expected_amount", 0) or 0)
        status = data.get("status", "Aberto")
        difference = closing - expected if status == "Fechado" else 0
        existing = row("SELECT id FROM cash_sessions WHERE date=?", (session_date,))
        if item_id or existing:
            target_id = item_id or existing["id"]
            execute(
                """UPDATE cash_sessions SET date=?,opening_amount=?,closing_amount=?,expected_amount=?,difference=?,status=?,closed_by=?,closed_at=?,notes=? WHERE id=?""",
                (session_date, opening, closing, expected, difference, status, user["id"] if status == "Fechado" else None, now() if status == "Fechado" else None, data.get("notes", ""), target_id),
            )
            self.audit(user["id"], "Caixa diario atualizado", session_date)
            self.send_json(row("SELECT * FROM cash_sessions WHERE id=?", (target_id,)))
            return
        session_id = uid("caixa")
        execute(
            "INSERT INTO cash_sessions(id,date,opening_amount,closing_amount,expected_amount,difference,status,opened_by,notes) VALUES(?,?,?,?,?,?,?,?,?)",
            (session_id, session_date, opening, closing, expected, difference, status, user["id"], data.get("notes", "")),
        )
        self.audit(user["id"], "Caixa diario aberto", session_date)
        self.send_json(row("SELECT * FROM cash_sessions WHERE id=?", (session_id,)), 201)

    def api_delete(self, path, user):
        mapping = {
            "clients": ("clients", "Cliente excluido", "clientes"),
            "manufacturers": ("manufacturers", "Fabricante excluido", "fabricantes"),
            "models": ("product_models", "Modelo excluido", "fabricantes"),
            "parts": ("parts", "Peca excluida", "estoque"),
            "services": ("services", "Servico excluido", "servicos"),
            "finance": ("finance_entries", "Lancamento financeiro excluido", "financeiro"),
            "users": ("users", "Usuario excluido", "configuracoes"),
            "roles": ("roles", "Perfil excluido", "configuracoes"),
        }
        parts = path.strip("/").split("/")
        key = parts[1] if len(parts) >= 3 else ""
        item_id = parts[2] if len(parts) >= 3 else ""
        if key not in mapping or not item_id:
            self.send_json({"error": "Rota nao encontrada."}, 404)
            return
        table, action, module = mapping[key]
        if not self.require_permission(user, module, "excluir" if module != "configuracoes" else "editar"):
            return
        if key == "users" and item_id == user["id"]:
            self.send_json({"error": "O usuario logado nao pode excluir a propria conta."}, 400)
            return
        if key == "users":
            target = row("SELECT users.id,users.email,roles.level FROM users JOIN roles ON roles.id=users.role_id WHERE users.id=?", (item_id,))
            if not target:
                self.send_json({"error": "Usuario nao encontrado."}, 404)
                return
            if int(target["level"] or 0) >= 100:
                remaining_admins = row(
                    """SELECT COUNT(*) AS total
                    FROM users JOIN roles ON roles.id=users.role_id
                    WHERE users.id<>? AND users.status='Ativo' AND roles.level>=100""",
                    (item_id,),
                )
                if int(remaining_admins["total"] or 0) == 0:
                    self.send_json({"error": "Nao e possivel remover o ultimo administrador ativo."}, 400)
                    return
        try:
            execute(f"DELETE FROM {table} WHERE id=?", (item_id,))
            self.audit(user["id"], action, item_id)
            self.send_json({"ok": True})
        except Exception as error:
            if not is_integrity_error(error):
                raise
            if key == "users":
                execute("UPDATE users SET status='Inativo', failed_attempts=0, locked_until=NULL WHERE id=?", (item_id,))
                self.audit(user["id"], "Usuario inativado", item_id)
                self.send_json({"ok": True, "message": "Usuario possui historico no sistema e foi inativado em vez de excluido."})
                return
            self.send_json({"error": "Registro em uso. Remova os vinculos antes de excluir."}, 400)

    def audit(self, user_id, action, detail):
        execute("INSERT INTO audit_logs(id,user_id,action,detail) VALUES(?,?,?,?)", (uid("aud"), user_id, action, detail))

    def log_message(self, format, *args):
        return


def main():
    apply_pending_restore()
    seed()
    port = int(os.environ.get("PORT", "5050"))
    host = os.environ.get("HOST", "127.0.0.1")
    if DB_ENGINE == "postgres" and os.environ.get("RUN_STARTUP_TASKS", "") == "1":
        threading.Thread(target=run_startup_tasks, daemon=True).start()
    print(f"Troca Ae SIS PRO rodando em http://{host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), App).serve_forever()


if __name__ == "__main__":
    main()
