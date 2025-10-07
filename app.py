import csv
import re
import secrets
from io import BytesIO
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, session, url_for, Response, flash, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy import extract, func
from dotenv import load_dotenv
load_dotenv()
from config import config
import os

app = Flask(__name__)
app.config.from_object(config[os.getenv('FLASK_ENV', 'development')])

db = SQLAlchemy(app)
mail = Mail(app)

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
    is_recurring = db.Column(db.Boolean, default=False)
    recurrence_type = db.Column(db.String(20))  # monthly, weekly, yearly


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    type = db.Column(db.String(20), nullable=False)  # Income or Expense
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    color = db.Column(db.String(7), default='#6c757d')  # Hex color for charts


class Budget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    month = db.Column(db.Integer, nullable=False)  # 1-12
    year = db.Column(db.Integer, nullable=False)
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
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        # Validation
        if not email or not password:
            flash('Email and password are required.', 'danger')
            return render_template('register.html')

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('User already exists! Try logging in.', 'warning')
            return render_template('register.html')

        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'danger')
            return render_template('register.html')
        if not re.search(r"\d", password):
            flash('Password must contain at least one number.', 'danger')
            return render_template('register.html')
        if not re.search(r"[A-Za-z]", password):
            flash('Password must contain at least one letter.', 'danger')
            return render_template('register.html')

        try:
            hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
            new_user = User(email=email, password=hashed_password)
            db.session.add(new_user)
            db.session.commit()
            
            # Initialize default categories for new user
            default_categories = [
                ('Salary', 'Income', '#28a745'),
                ('Freelance', 'Income', '#20c997'),
                ('Food', 'Expense', '#dc3545'),
                ('Transport', 'Expense', '#fd7e14'),
                ('Shopping', 'Expense', '#e83e8c'),
                ('Bills', 'Expense', '#6610f2'),
                ('Entertainment', 'Expense', '#17a2b8'),
                ('Healthcare', 'Expense', '#ffc107'),
                ('Other', 'Expense', '#6c757d')
            ]
            
            for cat_name, cat_type, color in default_categories:
                category = Category(name=cat_name, type=cat_type, color=color, user_id=new_user.id)
                db.session.add(category)
            
            db.session.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            flash('An error occurred during registration. Please try again.', 'danger')
            return render_template('register.html')

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        if not email or not password:
            flash('Email and password are required.', 'danger')
            return render_template('login.html')

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            session.permanent = True
            session['user_id'] = user.id
            session['email'] = user.email
            flash(f'Welcome back, {email}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials! Please try again.', 'danger')
            return render_template('login.html')

    return render_template('login.html')


# =====================
# Forgot / Reset Password
# =====================
@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        
        if not email:
            flash("Email is required.", "danger")
            return render_template("forgot_password.html")
        
        user = User.query.filter_by(email=email).first()

        if user:
            token = secrets.token_urlsafe(32)
            user.reset_token = token
            user.token_expiry = datetime.utcnow() + timedelta(hours=1)
            db.session.commit()

            reset_link = url_for("reset_password", token=token, _external=True)
            
            # Try to send email, fallback to console
            try:
                if app.config['MAIL_USERNAME']:
                    msg = Message(
                        'QuickLedger - Password Reset Request',
                        recipients=[email]
                    )
                    msg.body = f'''Hello,

You requested a password reset for your QuickLedger account.

Click the link below to reset your password:
{reset_link}

This link will expire in 1 hour.

If you didn't request this, please ignore this email.

Best regards,
QuickLedger Team
'''
                    mail.send(msg)
                    flash("Password reset link has been sent to your email.", "success")
                else:
                    print("🔗 Password reset link:", reset_link)
                    flash("Password reset link has been generated (check console in dev mode).", "info")
            except Exception as e:
                print("🔗 Password reset link:", reset_link)
                print(f"Email error: {e}")
                flash("Password reset link has been generated (check console).", "info")
        else:
            # Don't reveal if email exists for security
            flash("If an account exists with that email, a reset link has been sent.", "info")

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
        try:
            amount = float(request.form.get('amount', 0))
            category = request.form.get('category', '').strip()
            note = request.form.get('note', '').strip()
            is_recurring = request.form.get('is_recurring') == 'on'
            recurrence_type = request.form.get('recurrence_type', None)

            if amount <= 0:
                flash('Amount must be greater than zero.', 'danger')
                return redirect(url_for('dashboard'))
            
            if not category:
                flash('Category is required.', 'danger')
                return redirect(url_for('dashboard'))

            new_txn = Transaction(
                amount=amount,
                category=category,
                note=note,
                user_id=session['user_id'],
                is_recurring=is_recurring,
                recurrence_type=recurrence_type if is_recurring else None
            )
            db.session.add(new_txn)
            db.session.commit()
            flash('Transaction added successfully!', 'success')
        except ValueError:
            flash('Invalid amount entered.', 'danger')
        except Exception as e:
            db.session.rollback()
            flash('Error adding transaction. Please try again.', 'danger')

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

    # Get all user categories to determine type
    user_categories = Category.query.filter_by(user_id=session['user_id']).all()
    category_types = {cat.name: cat.type for cat in user_categories}
    
    # Calculate income and expense based on category type
    income = sum(t.amount for t in transactions if category_types.get(t.category) == "Income")
    expense = sum(t.amount for t in transactions if category_types.get(t.category) == "Expense")
    balance = income - expense

    # Get top expenses (only expense categories)
    expense_categories = [cat.name for cat in user_categories if cat.type == "Expense"]
    top_expenses = (
        db.session.query(Transaction.category, db.func.sum(Transaction.amount).label("total"))
        .filter_by(user_id=session['user_id'])
        .filter(Transaction.category.in_(expense_categories))
        .group_by(Transaction.category)
        .order_by(db.func.sum(Transaction.amount).desc())
        .limit(5)
        .all()
    )

    categories = [row[0] for row in top_expenses]
    amounts = [row[1] for row in top_expenses]

    # Get income and expense category names
    income_categories = [cat.name for cat in user_categories if cat.type == "Income"]
    
    monthly_data = (
        db.session.query(
            db.extract('year', Transaction.timestamp).label("year"),
            db.extract('month', Transaction.timestamp).label("month"),
            db.func.sum(db.case((Transaction.category.in_(income_categories), Transaction.amount), else_=0)).label("income"),
            db.func.sum(db.case((Transaction.category.in_(expense_categories), Transaction.amount), else_=0)).label("expense"),
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

    # Get user categories
    user_categories = Category.query.filter_by(user_id=session['user_id']).all()
    
    # Calculate financial insights
    insights = {
        'savings_rate': ((income - expense) / income * 100) if income > 0 else 0,
        'avg_monthly_income': income / max(len(monthly_summary), 1),
        'avg_monthly_expense': expense / max(len(monthly_summary), 1),
        'highest_expense_category': categories[0] if categories else None,
        'highest_expense_amount': amounts[0] if amounts else 0,
        'total_months': len(monthly_summary),
        'is_overspending': expense > income,
        'monthly_surplus': (income - expense) / max(len(monthly_summary), 1) if len(monthly_summary) > 0 else 0
    }
    
    # Spending trends (last 3 months vs previous 3 months)
    if len(monthly_summary) >= 6:
        recent_3_months = monthly_summary[-3:]
        previous_3_months = monthly_summary[-6:-3]
        
        recent_avg_expense = sum(m['expense'] for m in recent_3_months) / 3
        previous_avg_expense = sum(m['expense'] for m in previous_3_months) / 3
        
        insights['spending_trend'] = 'increasing' if recent_avg_expense > previous_avg_expense else 'decreasing'
        insights['trend_percentage'] = abs((recent_avg_expense - previous_avg_expense) / previous_avg_expense * 100) if previous_avg_expense > 0 else 0
    else:
        insights['spending_trend'] = 'stable'
        insights['trend_percentage'] = 0
    
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
        monthly_data=monthly_summary,
        user_categories=user_categories,
        insights=insights
    )


# =====================
# Edit Transaction
# =====================
@app.route('/edit/<int:transaction_id>', methods=['GET', 'POST'])
def edit_transaction(transaction_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    transaction = Transaction.query.filter_by(id=transaction_id, user_id=session['user_id']).first()
    
    if not transaction:
        flash('Transaction not found.', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        try:
            transaction.amount = float(request.form.get('amount', 0))
            transaction.category = request.form.get('category', '').strip()
            transaction.note = request.form.get('note', '').strip()
            transaction.is_recurring = request.form.get('is_recurring') == 'on'
            transaction.recurrence_type = request.form.get('recurrence_type', None) if transaction.is_recurring else None
            
            db.session.commit()
            flash('Transaction updated successfully!', 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            db.session.rollback()
            flash('Error updating transaction.', 'danger')
    
    user_categories = Category.query.filter_by(user_id=session['user_id']).all()
    return render_template('edit_transaction.html', transaction=transaction, user_categories=user_categories)


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
        flash('Transaction deleted successfully!', 'success')
    else:
        flash('Transaction not found.', 'danger')

    return redirect('/dashboard')


# =====================
# API Endpoints for Charts
# =====================
@app.route('/api/expense-breakdown')
def api_expense_breakdown():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Get expense categories
    user_categories = Category.query.filter_by(user_id=session['user_id'], type='Expense').all()
    expense_category_names = [cat.name for cat in user_categories]
    
    # Get expense breakdown by category (only expense categories)
    expense_data = (
        db.session.query(Transaction.category, func.sum(Transaction.amount).label('total'))
        .filter_by(user_id=session['user_id'])
        .filter(Transaction.category.in_(expense_category_names))
        .group_by(Transaction.category)
        .all()
    )
    
    # Get category colors
    categories_dict = {cat.name: cat.color for cat in user_categories}
    
    labels = []
    data = []
    colors = []
    
    for category, total in expense_data:
        labels.append(category)
        data.append(float(total))
        colors.append(categories_dict.get(category, '#6c757d'))
    
    return jsonify({
        'labels': labels,
        'data': data,
        'colors': colors
    })


@app.route('/api/income-expense-trend')
def api_income_expense_trend():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Get income and expense categories
    user_categories = Category.query.filter_by(user_id=session['user_id']).all()
    income_categories = [cat.name for cat in user_categories if cat.type == 'Income']
    expense_categories = [cat.name for cat in user_categories if cat.type == 'Expense']
    
    # Get last 12 months data
    monthly_data = (
        db.session.query(
            extract('year', Transaction.timestamp).label('year'),
            extract('month', Transaction.timestamp).label('month'),
            func.sum(func.case((Transaction.category.in_(income_categories), Transaction.amount), else_=0)).label('income'),
            func.sum(func.case((Transaction.category.in_(expense_categories), Transaction.amount), else_=0)).label('expense')
        )
        .filter_by(user_id=session['user_id'])
        .group_by('year', 'month')
        .order_by('year', 'month')
        .limit(12)
        .all()
    )
    
    labels = []
    income_data = []
    expense_data = []
    
    for row in monthly_data:
        month_name = datetime(int(row.year), int(row.month), 1).strftime('%b %Y')
        labels.append(month_name)
        income_data.append(float(row.income))
        expense_data.append(float(row.expense))
    
    return jsonify({
        'labels': labels,
        'income': income_data,
        'expense': expense_data
    })


# =====================
# Budget Management
# =====================
@app.route('/budgets', methods=['GET', 'POST'])
def budgets():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        try:
            category = request.form.get('category', '').strip()
            amount = float(request.form.get('amount', 0))
            month = int(request.form.get('month', datetime.now().month))
            year = int(request.form.get('year', datetime.now().year))
            
            # Check if budget already exists
            existing_budget = Budget.query.filter_by(
                user_id=session['user_id'],
                category=category,
                month=month,
                year=year
            ).first()
            
            if existing_budget:
                existing_budget.amount = amount
                flash('Budget updated successfully!', 'success')
            else:
                new_budget = Budget(
                    category=category,
                    amount=amount,
                    month=month,
                    year=year,
                    user_id=session['user_id']
                )
                db.session.add(new_budget)
                flash('Budget created successfully!', 'success')
            
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash('Error managing budget.', 'danger')
    
    # Get current month budgets
    current_month = datetime.now().month
    current_year = datetime.now().year
    
    budgets_list = Budget.query.filter_by(
        user_id=session['user_id'],
        month=current_month,
        year=current_year
    ).all()
    
    # Calculate spending for each budget
    budget_progress = []
    for budget in budgets_list:
        spent = db.session.query(func.sum(Transaction.amount)).filter(
            Transaction.user_id == session['user_id'],
            Transaction.category == budget.category,
            extract('month', Transaction.timestamp) == current_month,
            extract('year', Transaction.timestamp) == current_year
        ).scalar() or 0
        
        percentage = (spent / budget.amount * 100) if budget.amount > 0 else 0
        budget_progress.append({
            'budget': budget,
            'spent': float(spent),
            'remaining': float(budget.amount - spent),
            'percentage': min(percentage, 100)
        })
    
    user_categories = Category.query.filter_by(user_id=session['user_id'], type='Expense').all()
    
    return render_template('budgets.html', 
                         budget_progress=budget_progress,
                         user_categories=user_categories,
                         current_month=current_month,
                         current_year=current_year)


# =====================
# Category Management
# =====================
@app.route('/categories', methods=['GET', 'POST'])
def categories():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            cat_type = request.form.get('type', 'Expense')
            color = request.form.get('color', '#6c757d')
            
            if not name:
                flash('Category name is required.', 'danger')
                return redirect(url_for('categories'))
            
            # Check if category already exists
            existing = Category.query.filter_by(
                user_id=session['user_id'],
                name=name
            ).first()
            
            if existing:
                flash('Category already exists!', 'warning')
            else:
                new_category = Category(
                    name=name,
                    type=cat_type,
                    color=color,
                    user_id=session['user_id']
                )
                db.session.add(new_category)
                db.session.commit()
                flash('Category created successfully!', 'success')
        except Exception as e:
            db.session.rollback()
            flash('Error creating category.', 'danger')
    
    user_categories = Category.query.filter_by(user_id=session['user_id']).order_by(Category.type, Category.name).all()
    return render_template('categories.html', categories=user_categories)


@app.route('/categories/delete/<int:category_id>', methods=['POST'])
def delete_category(category_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    category = Category.query.filter_by(id=category_id, user_id=session['user_id']).first()
    
    if category:
        # Check if category is used in transactions
        transaction_count = Transaction.query.filter_by(
            user_id=session['user_id'],
            category=category.name
        ).count()
        
        if transaction_count > 0:
            flash(f'Cannot delete category "{category.name}" as it is used in {transaction_count} transaction(s).', 'danger')
        else:
            db.session.delete(category)
            db.session.commit()
            flash('Category deleted successfully!', 'success')
    
    return redirect(url_for('categories'))


# =====================
# Export PDF
# =====================
@app.route('/export/pdf')
def export_pdf():
    if "user_id" not in session:
        return redirect(url_for('login'))

    transactions = Transaction.query.filter_by(user_id=session['user_id']).order_by(Transaction.timestamp.desc()).all()

    # Get category types
    user_categories = Category.query.filter_by(user_id=session['user_id']).all()
    category_types = {cat.name: cat.type for cat in user_categories}
    
    income = sum(t.amount for t in transactions if category_types.get(t.category) == "Income")
    expense = sum(t.amount for t in transactions if category_types.get(t.category) == "Expense")
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
