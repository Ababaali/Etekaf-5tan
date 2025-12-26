import mysql.connector
from mysql.connector import errorcode
import datetime
import pandas as pd
from typing import List, Dict, Optional

import config

def get_db_connection():
    """ایجاد یک کانکشن به دیتابیس MySQL هاست شده"""
    try:
        conn = mysql.connector.connect(
            host=config.DB_HOST,
            user=config.DB_USER,
            password=config.DB_PASSWORD,
            database=config.DB_NAME
        )
        return conn
    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
            print("خطا: نام کاربری یا رمز عبور دیتابیس اشتباه است.")
        elif err.errno == errorcode.ER_BAD_DB_ERROR:
            print(f"خطا: دیتابیس '{config.DB_NAME}' وجود ندارد.")
        else:
            print(err)
        return None

def initialize_database():
    """ایجاد جداول مورد نیاز در دیتابیس MySQL در صورتی که وجود نداشته باشند"""
    conn = get_db_connection()
    if not conn:
        return
        
    cursor = conn.cursor()

    # جدول شرکت‌کنندگان
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS participants (
        national_id VARCHAR(10) PRIMARY KEY,
        full_name VARCHAR(255) NOT NULL,
        father_name VARCHAR(255),
        payment_status VARCHAR(20) DEFAULT 'unpaid',
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB CHARACTER SET=utf8mb4;
    """)

    # جدول پذیرش حضوری
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS checkins (
        id INT AUTO_INCREMENT PRIMARY KEY,
        national_id VARCHAR(10) UNIQUE,
        checked_in_by VARCHAR(50),
        checked_in_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status VARCHAR(20), -- confirmed / rejected / emergency
        FOREIGN KEY (national_id) REFERENCES participants (national_id)
    ) ENGINE=InnoDB CHARACTER SET=utf8mb4;
    """)

    # جدول قفل نرم
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS soft_locks (
        national_id VARCHAR(10) PRIMARY KEY,
        locked_by VARCHAR(50),
        locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP
    ) ENGINE=InnoDB CHARACTER SET=utf8mb4;
    """)

    # جدول لاگ حسابرسی
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INT AUTO_INCREMENT PRIMARY KEY,
        action VARCHAR(50) NOT NULL,
        national_id VARCHAR(10),
        user_id VARCHAR(50),
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        details TEXT
    ) ENGINE=InnoDB CHARACTER SET=utf8mb4;
    """)

    conn.commit()
    cursor.close()
    conn.close()
    print("MySQL Database initialized successfully.")

# --- Functions for Handlers (Adapted for MySQL) ---

def get_participant_info(national_id: str) -> Optional[Dict]:
    """دریافت اطلاعات یک شرکت‌کننده بر اساس کد ملی از MySQL"""
    conn = get_db_connection()
    if not conn: return None
    cursor = conn.cursor(dictionary=True) # dictionary=True باعث میشود خروجی شبیه دیکشنری باشد
    cursor.execute("SELECT * FROM participants WHERE national_id = %s", (national_id,))
    participant = cursor.fetchone()
    cursor.close()
    conn.close()
    return participant

def get_checkin_status(national_id: str) -> Optional[Dict]:
    """بررسی وضعیت پذیرش یک کد ملی از MySQL"""
    conn = get_db_connection()
    if not conn: return None
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM checkins WHERE national_id = %s", (national_id,))
    checkin = cursor.fetchone()
    cursor.close()
    conn.close()
    return checkin

def log_action(action: str, user_id: int, national_id: Optional[str] = None, details: str = ""):
    """ثبت یک رویداد در جدول لاگ MySQL"""
    conn = get_db_connection()
    if not conn: return
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO audit_logs (action, user_id, national_id, details) VALUES (%s, %s, %s, %s)",
        (action, str(user_id), national_id, details)
    )
    conn.commit()
    cursor.close()
    conn.close()

def create_soft_lock(national_id: str, user_id: int) -> bool:
    """ایجاد قفل نرم روی یک کد ملی در MySQL"""
    conn = get_db_connection()
    if not conn: return False
    cursor = conn.cursor()
    
    # ابتدا قفل‌های منقضی شده را پاک کن
    cursor.execute("DELETE FROM soft_locks WHERE expires_at < NOW()")
    conn.commit()
    
    try:
        expires_at = datetime.datetime.now() + datetime.timedelta(seconds=config.LOCK_DURATION_SECONDS)
        cursor.execute(
            "INSERT INTO soft_locks (national_id, locked_by, expires_at) VALUES (%s, %s, %s)",
            (national_id, str(user_id), expires_at)
        )
        conn.commit()
        return True
    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_DUP_ENTRY:
            return False # قفل از قبل وجود دارد
        else:
            print(err)
            return False
    finally:
        cursor.close()
        conn.close()

def release_soft_lock(national_id: str):
    """آزاد کردن قفل نرم در MySQL"""
    conn = get_db_connection()
    if not conn: return
    cursor = conn.cursor()
    cursor.execute("DELETE FROM soft_locks WHERE national_id = %s", (national_id,))
    conn.commit()
    cursor.close()
    conn.close()

def perform_checkin(national_id: str, user_id: int, status: str):
    """انجام عملیات پذیرش (تایید، رد، اضطراری) در MySQL"""
    conn = get_db_connection()
    if not conn: return
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO checkins (national_id, checked_in_by, status) VALUES (%s, %s, %s)",
        (national_id, str(user_id), status)
    )
    conn.commit()
    cursor.close()
    conn.close()
    log_action(f"checkin_{status}", user_id, national_id)

def import_participants_from_dataframe(df: pd.DataFrame):
    """وارد کردن داده‌ها از دیتافریم پانداز به دیتابیس MySQL"""
    conn = get_db_connection()
    if not conn: return
    cursor = conn.cursor()
    
    # استفاده از INSERT ... ON DUPLICATE KEY UPDATE برای به‌روزرسانی رکوردهای موجود
    # و درج رکوردهای جدید
    insert_query = """
        INSERT INTO participants (national_id, full_name, father_name, payment_status)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            full_name = VALUES(full_name),
            father_name = VALUES(father_name),
            payment_status = VALUES(payment_status)
    """
    
    # تبدیل دیتافریم به لیستی از تاپل‌ها برای ورود به دیتابیس
    data_tuples = [tuple(row) for row in df.to_numpy()]
    
    cursor.executemany(insert_query, data_tuples)
    
    conn.commit()
    cursor.close()
    conn.close()

# ... (بقیه توابع مانند get_live_stats و get_checked_in_data_for_excel با همین الگو و با تغییر placeholder به %s قابل پیاده‌سازی هستند) ...

def get_live_stats() -> Dict[str, int]:
    """محاسبه آمار لحظه‌ای از دیتابیس MySQL"""
    conn = get_db_connection()
    if not conn: return {}
    cursor = conn.cursor()
    
    stats = {}
    cursor.execute("SELECT COUNT(*) FROM participants")
    stats['total'] = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM checkins WHERE status = 'confirmed'")
    stats['confirmed'] = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM checkins WHERE status = 'emergency'")
    stats['emergency'] = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM participants WHERE payment_status = 'unpaid'")
    stats['unpaid_count'] = cursor.fetchone()[0]
    
    stats['checked_in_total'] = stats['confirmed'] + stats['emergency']
    stats['remaining'] = stats['total'] - stats['checked_in_total']
    
    cursor.close()
    conn.close()
    return stats

def get_checked_in_data_for_excel() -> pd.DataFrame:
    """دریافت داده‌های پذیرش‌شده برای خروجی اکسل از MySQL"""
    conn = get_db_connection()
    if not conn: return pd.DataFrame()
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
    """دریافت داده‌های پذیرش‌نشده برای خروجی اکسل از MySQL"""
    conn = get_db_connection()
    if not conn: return pd.DataFrame()
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
