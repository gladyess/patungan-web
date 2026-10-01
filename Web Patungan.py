import hmac
import os
import secrets
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for


BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = (Path("/tmp").resolve() if os.environ.get("VERCEL") == "1" else BASE_DIR / "instance")
INSTANCE_DIR.mkdir(exist_ok=True)

app = Flask(__name__, instance_path=str(INSTANCE_DIR))
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-this-secret-key"),
    DATABASE=os.environ.get("DATABASE_PATH", str(INSTANCE_DIR / "patungan.sqlite3")),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(
        os.environ.get("FLASK_ENV") == "production"
        or bool(os.environ.get("RENDER_EXTERNAL_URL"))
    ),
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    expense_date TEXT NOT NULL,
    payer_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS expense_participants (
    expense_id INTEGER NOT NULL REFERENCES expenses(id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE RESTRICT,
    PRIMARY KEY (expense_id, participant_id)
);

CREATE TABLE IF NOT EXISTS settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    debtor_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE RESTRICT,
    creditor_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE RESTRICT,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    paid_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (debtor_id != creditor_id)
);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 5000")
    return g.db


def init_db():
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    connection.close()


@app.teardown_appcontext
def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


@app.before_request
def protect_forms():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "")
        if not hmac.compare_digest(submitted_token, session["csrf_token"]):
            abort(400, description="Token formulir tidak valid. Muat ulang halaman.")


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": session.get("csrf_token", "")}


def amount_to_cents(raw_amount):
    try:
        amount = Decimal(raw_amount.strip().replace(",", "."))
        if not amount.is_finite() or amount <= 0:
            raise ValueError
        cents = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if cents <= 0 or cents > 999_999_999_999:
            raise ValueError
        return cents
    except (InvalidOperation, AttributeError, ValueError):
        raise ValueError("Jumlah harus berupa angka lebih dari nol (maksimal Rp9.999.999.999,99).")


def format_idr(cents):
    whole, fraction = divmod(int(cents), 100)
    formatted = f"{whole:,}".replace(",", ".")
    if fraction:
        return f"Rp {formatted},{fraction:02d}"
    return f"Rp {formatted}"


app.jinja_env.filters["idr"] = format_idr


def get_participants():
    return get_db().execute("SELECT id, name FROM participants ORDER BY name COLLATE NOCASE").fetchall()


def parse_expense_form(participants):
    description = request.form.get("description", "").strip()
    expense_date = request.form.get("expense_date", "").strip()
    payer_raw = request.form.get("payer_id", "").strip()
    if not description or len(description) > 120:
        raise ValueError("Keterangan wajib diisi dan maksimal 120 karakter.")
    try:
        date.fromisoformat(expense_date)
        payer_id = int(payer_raw)
    except (ValueError, TypeError):
        raise ValueError("Tanggal atau pembayar tidak valid.")
    participant_ids = {int(participant["id"]) for participant in participants}
    if payer_id not in participant_ids:
        raise ValueError("Pilih pembayar yang terdaftar.")
    if request.form.get("split_all"):
        included_ids = participant_ids
    else:
        try:
            included_ids = {int(value) for value in request.form.getlist("participant_ids")}
        except ValueError:
            raise ValueError("Daftar anggota tidak valid.")
        if not included_ids or not included_ids.issubset(participant_ids):
            raise ValueError("Pilih minimal satu anggota yang ikut menanggung.")
    return description, amount_to_cents(request.form.get("amount", "")), expense_date, payer_id, included_ids


def calculate_transfers():
    connection = get_db()
    participants = connection.execute("SELECT id, name FROM participants ORDER BY id").fetchall()
    balances = {participant["id"]: 0 for participant in participants}

    expenses = connection.execute(
        "SELECT id, amount_cents, payer_id FROM expenses ORDER BY id"
    ).fetchall()
    for expense in expenses:
        members = connection.execute(
            "SELECT participant_id FROM expense_participants WHERE expense_id = ? ORDER BY participant_id",
            (expense["id"],),
        ).fetchall()
        if not members:
            continue
        amount = expense["amount_cents"]
        share, remainder = divmod(amount, len(members))
        balances[expense["payer_id"]] += amount
        for index, member in enumerate(members):
            balances[member["participant_id"]] -= share + (1 if index < remainder else 0)

    payments = connection.execute(
        "SELECT debtor_id, creditor_id, amount_cents FROM settlements ORDER BY id"
    ).fetchall()
    for payment in payments:
        balances[payment["debtor_id"]] += payment["amount_cents"]
        balances[payment["creditor_id"]] -= payment["amount_cents"]

    names = {participant["id"]: participant["name"] for participant in participants}
    debtors = [[participant_id, -balance] for participant_id, balance in balances.items() if balance < 0]
    creditors = [[participant_id, balance] for participant_id, balance in balances.items() if balance > 0]
    transfers = []
    debtor_index = creditor_index = 0
    while debtor_index < len(debtors) and creditor_index < len(creditors):
        debtor_id, debt = debtors[debtor_index]
        creditor_id, credit = creditors[creditor_index]
        amount = min(debt, credit)
        if amount:
            transfers.append({
                "debtor_id": debtor_id,
                "debtor_name": names[debtor_id],
                "creditor_id": creditor_id,
                "creditor_name": names[creditor_id],
                "amount_cents": amount,
            })
        debtors[debtor_index][1] -= amount
        creditors[creditor_index][1] -= amount
        if debtors[debtor_index][1] == 0:
            debtor_index += 1
        if creditors[creditor_index][1] == 0:
            creditor_index += 1
    return transfers


def dashboard_data():
    connection = get_db()
    participants = get_participants()
    expenses = connection.execute(
        """SELECT e.id, e.description, e.amount_cents, e.expense_date, e.payer_id,
                  p.name AS payer_name,
                  GROUP_CONCAT(ep.participant_id) AS member_ids,
                  GROUP_CONCAT(member.name, ', ') AS member_names
           FROM expenses e
           JOIN participants p ON p.id = e.payer_id
           JOIN expense_participants ep ON ep.expense_id = e.id
           JOIN participants member ON member.id = ep.participant_id
           GROUP BY e.id
           ORDER BY e.expense_date DESC, e.id DESC"""
    ).fetchall()
    payments = connection.execute(
        """SELECT s.id, s.amount_cents, s.paid_at,
                  debtor.name AS debtor_name, creditor.name AS creditor_name
           FROM settlements s
           JOIN participants debtor ON debtor.id = s.debtor_id
           JOIN participants creditor ON creditor.id = s.creditor_id
           ORDER BY s.id DESC"""
    ).fetchall()
    total_cents = connection.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM expenses").fetchone()[0]
    return participants, expenses, payments, total_cents


@app.route("/")
def index():
    participants, expenses, payments, total_cents = dashboard_data()
    return render_template(
        "index.html",
        participants=participants,
        expenses=expenses,
        payments=payments,
        transfers=calculate_transfers(),
        total_cents=total_cents,
        today=date.today().isoformat(),
    )


@app.post("/participants")
def add_participant():
    name = request.form.get("name", "").strip()
    if not name or len(name) > 60:
        flash("Nama peserta wajib diisi dan maksimal 60 karakter.", "danger")
    else:
        try:
            get_db().execute("INSERT INTO participants (name) VALUES (?)", (name,))
            get_db().commit()
            flash(f"Peserta {name} berhasil ditambahkan.", "success")
        except sqlite3.IntegrityError:
            flash("Nama peserta tersebut sudah digunakan.", "warning")
    return redirect(url_for("index") + "#peserta")


@app.post("/participants/<int:participant_id>/edit")
def edit_participant(participant_id):
    name = request.form.get("name", "").strip()
    if not name or len(name) > 60:
        flash("Nama peserta wajib diisi dan maksimal 60 karakter.", "danger")
    else:
        try:
            cursor = get_db().execute("UPDATE participants SET name = ? WHERE id = ?", (name, participant_id))
            get_db().commit()
            flash("Nama peserta diperbarui." if cursor.rowcount else "Peserta tidak ditemukan.", "success")
        except sqlite3.IntegrityError:
            flash("Nama peserta tersebut sudah digunakan.", "warning")
    return redirect(url_for("index") + "#peserta")


@app.post("/participants/<int:participant_id>/delete")
def delete_participant(participant_id):
    connection = get_db()
    in_use = connection.execute(
        """SELECT 1 FROM expenses WHERE payer_id = ?
           UNION SELECT 1 FROM expense_participants WHERE participant_id = ?
           UNION SELECT 1 FROM settlements WHERE debtor_id = ? OR creditor_id = ? LIMIT 1""",
        (participant_id, participant_id, participant_id, participant_id),
    ).fetchone()
    if in_use:
        flash("Peserta masih tercatat dalam pengeluaran atau pembayaran, sehingga tidak bisa dihapus.", "warning")
    else:
        cursor = connection.execute("DELETE FROM participants WHERE id = ?", (participant_id,))
        connection.commit()
        flash("Peserta berhasil dihapus." if cursor.rowcount else "Peserta tidak ditemukan.", "success")
    return redirect(url_for("index") + "#peserta")


def save_expense(expense_id=None):
    participants = get_participants()
    if not participants:
        flash("Tambahkan peserta terlebih dahulu sebelum mencatat pengeluaran.", "warning")
        return redirect(url_for("index") + "#pengeluaran")
    try:
        description, amount, expense_date, payer_id, included_ids = parse_expense_form(participants)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("index") + "#pengeluaran")

    connection = get_db()
    try:
        if expense_id is None:
            cursor = connection.execute(
                "INSERT INTO expenses (description, amount_cents, expense_date, payer_id) VALUES (?, ?, ?, ?)",
                (description, amount, expense_date, payer_id),
            )
            expense_id = cursor.lastrowid
            message = "Pengeluaran berhasil ditambahkan."
        else:
            cursor = connection.execute(
                "UPDATE expenses SET description = ?, amount_cents = ?, expense_date = ?, payer_id = ? WHERE id = ?",
                (description, amount, expense_date, payer_id, expense_id),
            )
            if not cursor.rowcount:
                connection.rollback()
                flash("Pengeluaran tidak ditemukan.", "warning")
                return redirect(url_for("index") + "#pengeluaran")
            connection.execute("DELETE FROM expense_participants WHERE expense_id = ?", (expense_id,))
            message = "Pengeluaran berhasil diperbarui."
        connection.executemany(
            "INSERT INTO expense_participants (expense_id, participant_id) VALUES (?, ?)",
            [(expense_id, participant_id) for participant_id in sorted(included_ids)],
        )
        connection.commit()
        flash(message, "success")
    except sqlite3.Error:
        connection.rollback()
        flash("Pengeluaran gagal disimpan. Silakan periksa kembali datanya.", "danger")
    return redirect(url_for("index") + "#pengeluaran")


@app.post("/expenses")
def add_expense():
    return save_expense()


@app.post("/expenses/<int:expense_id>/edit")
def edit_expense(expense_id):
    return save_expense(expense_id)


@app.post("/expenses/<int:expense_id>/delete")
def delete_expense(expense_id):
    connection = get_db()
    cursor = connection.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    connection.commit()
    flash("Pengeluaran berhasil dihapus." if cursor.rowcount else "Pengeluaran tidak ditemukan.", "success")
    return redirect(url_for("index") + "#pengeluaran")


@app.post("/settlements")
def add_settlement():
    try:
        debtor_id = int(request.form.get("debtor_id", ""))
        creditor_id = int(request.form.get("creditor_id", ""))
        amount = int(request.form.get("amount_cents", ""))
    except ValueError:
        abort(400, description="Data pembayaran tidak valid.")
    current_suggestions = calculate_transfers()
    is_suggestion = any(
        item["debtor_id"] == debtor_id
        and item["creditor_id"] == creditor_id
        and item["amount_cents"] == amount
        for item in current_suggestions
    )
    if not is_suggestion:
        abort(400, description="Saran pembayaran sudah berubah. Muat ulang halaman.")
    get_db().execute(
        "INSERT INTO settlements (debtor_id, creditor_id, amount_cents) VALUES (?, ?, ?)",
        (debtor_id, creditor_id, amount),
    )
    get_db().commit()
    flash("Pembayaran ditandai selesai.", "success")
    return redirect(url_for("index") + "#pelunasan")


@app.post("/settlements/<int:settlement_id>/undo")
def undo_settlement(settlement_id):
    connection = get_db()
    cursor = connection.execute("DELETE FROM settlements WHERE id = ?", (settlement_id,))
    connection.commit()
    flash("Tanda pembayaran dibatalkan." if cursor.rowcount else "Pembayaran tidak ditemukan.", "success")
    return redirect(url_for("index") + "#pelunasan")


@app.get("/health")
def health():
    get_db().execute("SELECT 1")
    return {"status": "ok"}


init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)