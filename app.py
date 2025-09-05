import csv
import re
import secrets
from io import BytesIO
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, session, url_for, Response, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy import extract

app = Flask(__name__)
app.secret_key = "quickledger123"

# 🛢 MySQL connection (adjust username, password, db name)
app.config['SQLALCHEMY_DATABASE_URI'] = "mysql://root:Chandu%409392@localhost/quickledger"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# =====================
# Database Models
# =====================
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    reset_token = db.Column(db.String(200), nullable=True)
    token_expiry = db.Column(db.DateTime, nullable=True)
    transactions = db.relationship('Transaction', backref='user', lazy=True)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    note = db.Column(db.String(200))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)


# =====================
# Routes
# =====================
@app.route('/')
def home():
    if "user_id" in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            return "User already exists! Try logging in."

        if len(password) < 6:
            return "Password must be at least 6 characters long."
        if not re.search(r"\d", password):
            return "Password must contain at least one number."
        if not re.search(r"[A-Za-z]", password):
            return "Password must contain at least one letter."

        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(email=email, password=hashed_password)

        db.session.add(new_user)
        db.session.commit()

        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['email'] = user.email
            return redirect(url_for('dashboard'))
        else:
            return "Invalid credentials! Try again."

    return render_template('login.html')


# =====================
# Forgot / Reset Password
# =====================
@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form["email"]
        user = User.query.filter_by(email=email).first()

        if user:
            token = secrets.token_urlsafe(32)
            user.reset_token = token
            user.token_expiry = datetime.utcnow() + timedelta(hours=1)
            db.session.commit()

            reset_link = url_for("reset_password", token=token, _external=True)
            print("🔗 Password reset link:", reset_link)  # In dev mode, shown in console

            flash("Password reset link has been sent to your email (check console in dev mode).", "info")
        else:
            flash("No account found with that email.", "danger")

    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = User.query.filter_by(reset_token=token).first()

    if not user or user.token_expiry < datetime.utcnow():
        flash("Invalid or expired reset link.", "danger")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        new_password = generate_password_hash(request.form["password"], method='pbkdf2:sha256')
        user.password = new_password
        user.reset_token = None
        user.token_expiry = None
        db.session.commit()

        flash("Password reset successful! Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html")


# =====================
# Dashboard
# =====================
@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if "user_id" not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        amount = float(request.form['amount'])
        category = request.form['category']
        note = request.form['note']

        new_txn = Transaction(
            amount=amount,
            category=category,
            note=note,
            user_id=session['user_id']
        )
        db.session.add(new_txn)
        db.session.commit()

    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    search_note = request.args.get('note')

    query = Transaction.query.filter_by(user_id=session['user_id'])

    if start_date:
        query = query.filter(Transaction.timestamp >= start_date)
    if end_date:
        query = query.filter(Transaction.timestamp <= end_date)
    if search_note:
        query = query.filter(Transaction.note.ilike(f"%{search_note}%"))

    transactions = query.order_by(Transaction.timestamp.desc()).all()

    income = sum(t.amount for t in transactions if t.category == "Income")
    expense = sum(t.amount for t in transactions if t.category == "Expense")
    balance = income - expense

    top_expenses = (
        db.session.query(Transaction.category, db.func.sum(Transaction.amount).label("total"))
        .filter_by(user_id=session['user_id'])
        .filter(Transaction.category != "Income")
        .group_by(Transaction.category)
        .order_by(db.func.sum(Transaction.amount).desc())
        .limit(5)
        .all()
    )

    categories = [row[0] for row in top_expenses]
    amounts = [row[1] for row in top_expenses]

    monthly_data = (
        db.session.query(
            db.extract('year', Transaction.timestamp).label("year"),
            db.extract('month', Transaction.timestamp).label("month"),
            db.func.sum(db.case((Transaction.category == "Income", Transaction.amount), else_=0)).label("income"),
            db.func.sum(db.case((Transaction.category == "Expense", Transaction.amount), else_=0)).label("expense"),
        )
        .filter_by(user_id=session['user_id'])
        .group_by("year", "month")
        .order_by("year", "month")
        .all()
    )

    monthly_summary = []
    for row in monthly_data:
        monthly_summary.append({
            "year": int(row.year),
            "month": int(row.month),
            "income": float(row.income),
            "expense": float(row.expense),
            "balance": float(row.income - row.expense)
        })

    return render_template(
        'dashboard.html',
        email=session['email'],
        transactions=transactions,
        income=income,
        expense=expense,
        balance=balance,
        start_date=start_date,
        end_date=end_date,
        search_note=search_note,
        categories=categories,
        amounts=amounts,
        monthly_data=monthly_summary
    )


# =====================
# Delete Transaction
# =====================
@app.route('/delete/<int:transaction_id>', methods=['POST'])
def delete_transaction(transaction_id):
    if 'user_id' not in session:
        return redirect('/login')

    transaction = Transaction.query.filter_by(id=transaction_id, user_id=session['user_id']).first()

    if transaction:
        db.session.delete(transaction)
        db.session.commit()

    return redirect('/dashboard')


# =====================
# Export PDF
# =====================
@app.route('/export/pdf')
def export_pdf():
    if "user_id" not in session:
        return redirect(url_for('login'))

    transactions = Transaction.query.filter_by(user_id=session['user_id']).order_by(Transaction.timestamp.desc()).all()

    income = sum(t.amount for t in transactions if t.category == "Income")
    expense = sum(t.amount for t in transactions if t.category == "Expense")
    balance = income - expense

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(200, height - 50, "QuickLedger Report")

    pdf.setFont("Helvetica", 12)
    pdf.drawString(50, height - 100, f"User: {session['email']}")
    pdf.drawString(50, height - 120, f"Total Income: ₹ {income}")
    pdf.drawString(50, height - 140, f"Total Expense: ₹ {expense}")
    pdf.drawString(50, height - 160, f"Balance: ₹ {balance}")

    y = height - 200
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(50, y, "Amount")
    pdf.drawString(150, y, "Category")
    pdf.drawString(250, y, "Note")
    pdf.drawString(400, y, "Date")
    y -= 20

    pdf.setFont("Helvetica", 10)
    for txn in transactions:
        pdf.drawString(50, y, f"₹ {txn.amount}")
        pdf.drawString(150, y, txn.category)
        pdf.drawString(250, y, txn.note or "-")
        pdf.drawString(400, y, txn.timestamp.strftime("%Y-%m-%d"))
        y -= 20
        if y < 50:
            pdf.showPage()
            y = height - 50

    pdf.save()
    buffer.seek(0)

    return send_file(buffer, as_attachment=True, download_name="QuickLedger_Report.pdf", mimetype='application/pdf')


# =====================
# Export CSV
# =====================
@app.route('/export/csv')
def export_csv():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    transactions = Transaction.query.filter_by(user_id=session['user_id']).all()

    def generate():
        data = [["ID", "Amount", "Category", "Note", "Date"]]
        for t in transactions:
            data.append([t.id, t.amount, t.category, t.note, t.timestamp.strftime("%Y-%m-%d %H:%M")])

        for row in data:
            yield ",".join(map(str, row)) + "\n"

    return Response(
        generate(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"}
    )


# =====================
# Logout
# =====================
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# =====================
# Run the App
# =====================
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
