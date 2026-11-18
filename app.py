import os
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import wraps
from hmac import compare_digest
from secrets import token_urlsafe

import click
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
database_url = os.environ["DATABASE_URL"]
if database_url.startswith(("postgres://", "postgresql://")):
    database_url = "postgresql+psycopg://" + database_url.split("://", 1)[1]

app.config.update(
    SECRET_KEY=os.environ["SECRET_KEY"],
    SQLALCHEMY_DATABASE_URI=database_url,
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    SESSION_COOKIE_SECURE=os.environ.get("LOCAL_DEV") != "1",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=3600,
    MAX_CONTENT_LENGTH=16384,
)
db = SQLAlchemy(app)

guardians = db.Table(
    "guardian_student",
    db.Column("guardian_id", db.ForeignKey("account.id"), primary_key=True),
    db.Column("student_id", db.ForeignKey("student.id"), primary_key=True),
)


class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    admin = db.Column(db.Boolean, default=False, nullable=False)
    students = db.relationship("Student", secondary=guardians)


class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(40), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)


class Entry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.ForeignKey("student.id"), index=True, nullable=False)
    kind = db.Column(db.String(10), nullable=False)
    description = db.Column(db.String(140), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    day = db.Column(db.Date, nullable=False)
    submission = db.Column(db.String(80), unique=True, nullable=False)
    __table_args__ = (
        db.CheckConstraint("amount > 0"),
        db.CheckConstraint("kind in ('charge', 'payment')"),
    )


DUMMY_HASH = generate_password_hash(token_urlsafe(32))


@app.before_request
def validate_csrf():
    if request.method == "POST":
        expected = session.get("csrf")
        if not expected or not compare_digest(expected.encode(), request.form.get("csrf", "").encode()):
            abort(400)


@app.context_processor
def template_helpers():
    if "csrf" not in session:
        session["csrf"] = token_urlsafe(32)
    return {"csrf": session["csrf"]}


@app.after_request
def response_headers(response):
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    return response


def signed_in(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        account_id = session.get("account")
        account = db.session.get(Account, account_id) if account_id else None
        if account is None:
            return redirect(url_for("login"))
        return view(account, *args, **kwargs)

    return wrapped


@app.template_filter("eur")
def euros(amount):
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",") + " €"


@app.get("/healthz")
def health():
    db.session.execute(select(1))
    return "OK"


@app.route("/entrar", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        account = db.session.scalar(select(Account).where(Account.email == email))
        password_hash = account.password_hash if account else DUMMY_HASH
        if len(password) <= 256 and check_password_hash(password_hash, password) and account:
            session.clear()
            session.permanent = True
            session["account"] = account.id
            return redirect(url_for("portal"))
        flash("Verifique o email e a palavra-passe.")
    return render_template("login.html")


@app.post("/sair")
def logout():
    session.clear()
    return redirect(url_for("login"), code=303)


@app.get("/")
@app.get("/aluno/<int:student_id>")
@signed_in
def portal(account, student_id=None):
    available_guardians = []
    if account.admin:
        students = db.session.scalars(select(Student).order_by(Student.name, Student.id)).all()
        available_guardians = db.session.scalars(
            select(Account).where(Account.admin.is_(False)).order_by(Account.email)
        ).all()
    else:
        students = sorted(account.students, key=lambda student: (student.name, student.id))
    student = next((item for item in students if item.id == student_id), None)
    if student_id is not None and student is None:
        abort(404)
    if student is None and students:
        student = students[0]

    entries = []
    if student:
        entries = db.session.scalars(
            select(Entry).where(Entry.student_id == student.id).order_by(Entry.day.desc(), Entry.id.desc())
        ).all()
    charged = sum((item.amount for item in entries if item.kind == "charge"), Decimal("0.00"))
    paid = sum((item.amount for item in entries if item.kind == "payment"), Decimal("0.00"))
    return render_template(
        "portal.html", account=account, students=students, student=student, entries=entries,
        charged=charged, paid=paid, balance=charged - paid, today=date.today().isoformat(),
        submission=token_urlsafe(32), available_guardians=available_guardians,
    )


@app.post("/encarregados")
@signed_in
def create_guardian(account):
    if not account.admin:
        abort(403)

    form_url = url_for("portal", _anchor="novo-encarregado")
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    local, separator, domain = email.partition("@")
    if (
        not separator or not local or not domain or "@" in domain
        or len(email) > 254 or any(char.isspace() for char in email)
    ):
        flash("Indique um endereço de email válido.")
        return redirect(form_url, code=303)
    if not 12 <= len(password) <= 256:
        flash("A palavra-passe deve ter entre 12 e 256 caracteres.")
        return redirect(form_url, code=303)
    if password != request.form.get("confirm_password"):
        flash("As palavras-passe não coincidem.")
        return redirect(form_url, code=303)

    student = None
    if request.form.get("student_id"):
        student_id = request.form.get("student_id", type=int)
        if student_id is not None and 0 < student_id <= 2147483647:
            student = db.session.get(Student, student_id)
        if student is None:
            flash("Selecione um aluno válido.")
            return redirect(form_url, code=303)

    guardian = Account(email=email, password_hash=generate_password_hash(password), admin=False)
    if student is not None:
        guardian.students.append(student)
    db.session.add(guardian)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if db.session.scalar(select(Account.id).where(Account.email == email)) is None:
            raise
        flash("Já existe uma conta com este email.")
        return redirect(form_url, code=303)

    flash("Conta de encarregado criada com sucesso.")
    return redirect(url_for("portal", student_id=student.id if student else None, _anchor="novo-encarregado"), code=303)


@app.post("/alunos")
@signed_in
def create_student(account):
    if not account.admin:
        abort(403)

    reference = request.form.get("reference", "").strip()
    name = request.form.get("name", "").strip()
    guardian_id = request.form.get("guardian_id", "").strip()
    if not 1 <= len(reference) <= 40 or not 1 <= len(name) <= 120:
        flash("Indique o nome e a referência do aluno.")
        return redirect(url_for("portal", _anchor="novo-aluno"), code=303)

    guardian = None
    if guardian_id:
        if guardian_id.isascii() and guardian_id.isdecimal() and len(guardian_id) <= 10:
            valid_id = int(guardian_id)
            if 0 < valid_id <= 2147483647:
                guardian = db.session.get(Account, valid_id)
        if guardian is None or guardian.admin:
            flash("Selecione um encarregado de educação válido.")
            return redirect(url_for("portal", _anchor="novo-aluno"), code=303)

    student = Student(reference=reference, name=name)
    if guardian is not None:
        guardian.students.append(student)
    db.session.add(student)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if db.session.scalar(select(Student.id).where(Student.reference == reference)) is None:
            raise
        flash("Já existe um aluno com esta referência.")
        return redirect(url_for("portal", _anchor="novo-aluno"), code=303)

    flash("Aluno criado com sucesso.")
    return redirect(url_for("portal", student_id=student.id), code=303)


@app.post("/aluno/<int:student_id>/movimentos")
@signed_in
def add_entry(account, student_id):
    if not account.admin:
        abort(403)
    if db.session.get(Student, student_id) is None:
        abort(404)
    kind = request.form.get("kind")
    description = request.form.get("description", "").strip()
    submission = request.form.get("submission", "")
    try:
        amount = Decimal(request.form.get("amount", "").replace(",", "."))
        day = date.fromisoformat(request.form.get("day", ""))
        valid_amount = amount.is_finite() and 0 < amount <= Decimal("9999999999.99") and amount == amount.quantize(Decimal(".01"))
    except (InvalidOperation, ValueError):
        valid_amount = False
    if not valid_amount or not 1 <= len(description) <= 140 or kind not in {"charge", "payment"} or len(submission) != 43:
        flash("Verifique o tipo, a descrição, a data e o valor do movimento.")
    else:
        try:
            db.session.add(Entry(student_id=student_id, kind=kind, description=description,
                                 amount=amount, day=day, submission=submission))
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            existing = db.session.scalar(select(Entry).where(Entry.submission == submission, Entry.student_id == student_id))
            if existing is None:
                raise
        flash("Movimento registado.")
    return redirect(url_for("portal", student_id=student_id), code=303)


@app.cli.command("init-db")
def init_db():
    """Cria as tabelas iniciais."""
    db.create_all()


@app.cli.command("create-account")
@click.argument("email")
@click.option("--admin", is_flag=True)
@click.password_option()
def create_account(email, admin, password):
    """Cria uma conta da secretaria ou de um encarregado."""
    email = email.strip().lower()
    if "@" not in email or len(email) > 254 or not 12 <= len(password) <= 256:
        raise click.ClickException("Indique um email válido e uma palavra-passe de 12 a 256 caracteres.")
    if db.session.scalar(select(Account).where(Account.email == email)):
        raise click.ClickException("A conta já existe.")
    db.session.add(Account(email=email, password_hash=generate_password_hash(password), admin=admin))
    db.session.commit()


@app.cli.command("add-student")
@click.argument("reference")
@click.argument("name")
@click.argument("guardian_email")
def add_student(reference, name, guardian_email):
    """Cria um aluno ou associa um aluno existente a um encarregado."""
    reference, name = reference.strip(), name.strip()
    if not 1 <= len(reference) <= 40 or not 1 <= len(name) <= 120:
        raise click.ClickException("Verifique a referência e o nome.")
    guardian = db.session.scalar(select(Account).where(Account.email == guardian_email.strip().lower()))
    if guardian is None or guardian.admin:
        raise click.ClickException("Crie primeiro a conta do encarregado.")
    student = db.session.scalar(select(Student).where(Student.reference == reference))
    if student is None:
        student = Student(reference=reference, name=name)
        db.session.add(student)
    elif student.name != name:
        raise click.ClickException("A referência pertence a um aluno com outro nome.")
    if student not in guardian.students:
        guardian.students.append(student)
    db.session.commit()

def ensure_test_admin():
    password = os.environ.get("SOURCEPT_TEST_ADMIN_PASSWORD")
    if password is None:
        return
    if not 12 <= len(password) <= 256:
        raise RuntimeError("SOURCEPT_TEST_ADMIN_PASSWORD deve ter 12 a 256 caracteres.")

    email = "secretaria@example.org"
    accounts = Account.__table__
    with app.app_context(), db.engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            # Serialize bootstrap across workers and overlapping deploys.
            connection.execute(db.text("SELECT pg_advisory_xact_lock(53170, 1)"))
        db.metadata.create_all(connection)
        existing = connection.execute(
            select(accounts.c.admin).where(accounts.c.email == email)
        ).scalar_one_or_none()
        if existing is False:
            raise RuntimeError("O email da conta de teste já pertence a uma conta sem permissões de administração.")
        if existing is None:
            connection.execute(
                accounts.insert().values(
                    email=email, password_hash=generate_password_hash(password), admin=True
                )
            )


ensure_test_admin()
