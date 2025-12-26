import sqlite3
import datetime
import pandas as pd
from typing import List, Dict, Optional, Tuple

import config

def get_db_connection():
    """ایجاد یک کانکشن به دیتابیس"""
    conn = sqlite3.connect(config.DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def initialize_database():
    """ایجاد جداول مورد نیاز در دیتابیس در صورتی که وجود نداشته باشند"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # جدول شرکت‌کنندگان
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS participants (
        national_id TEXT PRIMARY KEY,
        full_name TEXT NOT NULL,
        father_name TEXT,
        payment_status TEXT DEFAULT 'unpaid',
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    # جدول پذیرش حضوری
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS checkins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        national_id TEXT UNIQUE,
        checked_in_by TEXT,
        checked_in_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT, -- confirmed / rejected / emergency
        FOREIGN KEY (national_id) REFERENCES participants (national_id)
    )""")

    # جدول قفل نرم
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS soft_locks (
        national_id TEXT PRIMARY KEY,
        locked_by TEXT,
        locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP
    )""")

    # جدول لاگ حسابرسی
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT NOT NULL,
        national_id TEXT,
        user_id TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        details TEXT
    )""")

    conn.commit()
    conn.close()
    print("Database initialized successfully.")

# --- Functions for Handlers ---

def get_participant_info(national_id: str) -> Optional[sqlite3.Row]:
    """دریافت اطلاعات یک شرکت‌کننده بر اساس کد ملی"""
    conn = get_db_connection()
    participant = conn.execute("SELECT * FROM participants WHERE national_id = ?", (national_id,)).fetchone()
    conn.close()
    return participant

def get_checkin_status(national_id: str) -> Optional[sqlite3.Row]:
    """بررسی وضعیت پذیرش یک کد ملی"""
    conn = get_db_connection()
    checkin = conn.execute("SELECT * FROM checkins WHERE national_id = ?", (national_id,)).fetchone()
    conn.close()
    return checkin

def log_action(action: str, user_id: int, national_id: Optional[str] = None, details: str = ""):
    """ثبت یک رویداد در جدول لاگ"""
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO audit_logs (action, user_id, national_id, details) VALUES (?, ?, ?, ?)",
        (action, str(user_id), national_id, details)
    )
    conn.commit()
    conn.close()

def create_soft_lock(national_id: str, user_id: int) -> bool:
    """ایجاد قفل نرم روی یک کد ملی"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # ابتدا قفل‌های منقضی شده را پاک کن
    cursor.execute("DELETE FROM soft_locks WHERE expires_at < ?", (datetime.datetime.now(),))
    
    try:
        expires_at = datetime.datetime.now() + datetime.timedelta(seconds=config.LOCK_DURATION_SECONDS)
        cursor.execute(
            "INSERT INTO soft_locks (national_id, locked_by, expires_at) VALUES (?, ?, ?)",
            (national_id, str(user_id), expires_at)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError: # قفل از قبل وجود دارد
        return False
    finally:
        conn.close()

def release_soft_lock(national_id: str):
    """آزاد کردن قفل نرم"""
    conn = get_db_connection()
    conn.execute("DELETE FROM soft_locks WHERE national_id = ?", (national_id,))
    conn.commit()
    conn.close()

def perform_checkin(national_id: str, user_id: int, status: str):
    """انجام عملیات پذیرش (تایید، رد، اضطراری)"""
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO checkins (national_id, checked_in_by, status) VALUES (?, ?, ?)",
        (national_id, str(user_id), status)
    )
    conn.commit()
    conn.close()
    log_action(f"checkin_{status}", user_id, national_id)

def import_participants_from_dataframe(df: pd.DataFrame):
    """وارد کردن داده‌ها از دیتافریم پانداز به دیتابیس"""
    conn = get_db_connection()
    # داده‌ها را به صورت موقت در یک جدول دیگر میریزیم و سپس به جدول اصلی منتقل میکنیم
    # با ON CONFLICT DO NOTHING از ورود کدهای تکراری جلوگیری میشود
    df.to_sql('temp_participants', conn, if_exists='replace', index=False)
    
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO participants (national_id, full_name, father_name, payment_status)
        SELECT national_id, full_name, father_name, payment_status
        FROM temp_participants
        ON CONFLICT(national_id) DO NOTHING;
    """)
    
    conn.commit()
    conn.close()

def get_live_stats() -> Dict[str, int]:
    """محاسبه آمار لحظه‌ای از دیتابیس"""
    conn = get_db_connection()
    total_participants = conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
    confirmed = conn.execute("SELECT COUNT(*) FROM checkins WHERE status = 'confirmed'").fetchone()[0]
    emergency = conn.execute("SELECT COUNT(*) FROM checkins WHERE status = 'emergency'").fetchone()[0]
    rejected = conn.execute("SELECT COUNT(*) FROM checkins WHERE status = 'rejected'").fetchone()[0]
    unpaid_total = conn.execute("SELECT COUNT(*) FROM participants WHERE payment_status = 'unpaid'").fetchone()[0]
    
    conn.close()

    return {
        "total": total_participants,
        "checked_in_total": confirmed + emergency,
        "confirmed": confirmed,
        "emergency": emergency,
        "remaining": total_participants - (confirmed + emergency),
        "unpaid_count": unpaid_total,
    }

def get_checked_in_data_for_excel() -> pd.DataFrame:
    """دریافت داده‌های پذیرش‌شده برای خروجی اکسل"""
    conn = get_db_connection()
    query = """
        SELECT
            p.full_name,
            p.national_id,
            p.payment_status,
            c.checked_in_by,
            c.checked_in_at
        FROM participants p
        JOIN checkins c ON p.national_id = c.national_id
        WHERE c.status = 'confirmed' OR c.status = 'emergency'
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

def get_not_checked_in_data_for_excel() -> pd.DataFrame:
    """دریافت داده‌های پذیرش‌نشده برای خروجی اکسل"""
    conn = get_db_connection()
    query = """
        SELECT
            p.full_name,
            p.national_id,
            p.father_name,
            p.payment_status
        FROM participants p
        LEFT JOIN checkins c ON p.national_id = c.national_id
        WHERE c.id IS NULL
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df
