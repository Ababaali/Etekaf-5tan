# database.py
import mysql.connector
from mysql.connector import pooling, errorcode
import datetime
import pandas as pd
from typing import List, Dict, Optional
import config

# ایجاد استخر اتصال (Connection Pool) برای جلوگیری از کندی
db_pool = None

def initialize_database():
    global db_pool
    try:
        # ساخت دیتابیس و جداول (مشابه قبل اما با اطمینان بیشتر)
        tmp_conn = mysql.connector.connect(
            host=config.DB_HOST, user=config.DB_USER, password=config.DB_PASSWORD
        )
        cursor = tmp_conn.cursor()
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {config.DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci")
        tmp_conn.database = config.DB_NAME
        
        # جداول
        queries = [
            """CREATE TABLE IF NOT EXISTS participants (
                national_id VARCHAR(10) PRIMARY KEY,
                full_name VARCHAR(255) NOT NULL,
                father_name VARCHAR(255),
                payment_status VARCHAR(20) DEFAULT 'unpaid',
                imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB CHARACTER SET=utf8mb4;""",
            
            """CREATE TABLE IF NOT EXISTS checkins (
                id INT AUTO_INCREMENT PRIMARY KEY,
                national_id VARCHAR(10) UNIQUE,
                checked_in_by VARCHAR(50),
                checked_in_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status VARCHAR(20),
                FOREIGN KEY (national_id) REFERENCES participants (national_id)
            ) ENGINE=InnoDB CHARACTER SET=utf8mb4;""",
            
            """CREATE TABLE IF NOT EXISTS soft_locks (
                national_id VARCHAR(10) PRIMARY KEY,
                locked_by VARCHAR(50),
                locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            ) ENGINE=InnoDB CHARACTER SET=utf8mb4;""",
            
            """CREATE TABLE IF NOT EXISTS audit_logs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                action VARCHAR(50) NOT NULL,
                user_id VARCHAR(50),
                national_id VARCHAR(10),
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                details TEXT
            ) ENGINE=InnoDB CHARACTER SET=utf8mb4;"""
        ]
        
        for q in queries:
            cursor.execute(q)
            
        tmp_conn.close()
        
        # راه اندازی Pool
        db_pool = mysql.connector.pooling.MySQLConnectionPool(
            pool_name=config.POOL_NAME,
            pool_size=config.POOL_SIZE,
            host=config.DB_HOST,
            database=config.DB_NAME,
            user=config.DB_USER,
            password=config.DB_PASSWORD
        )
        print("✅ Database & Connection Pool Initialized Successfully.")
        
    except mysql.connector.Error as err:
        print(f"❌ DB Error: {err}")

def get_connection():
    """دریافت کانکشن از استخر"""
    global db_pool
    if not db_pool:
        initialize_database()
    return db_pool.get_connection()

# --- توابع اصلی ---

def search_participants(query: str) -> List[Dict]:
    """جستجوی شرکت‌کننده با نام یا بخشی از نام"""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    # جستجوی امن با پارامتر
    sql = "SELECT * FROM participants WHERE full_name LIKE %s OR father_name LIKE %s LIMIT 10"
    like_query = f"%{query}%"
    cursor.execute(sql, (like_query, like_query))
    results = cursor.fetchall()
    cursor.close()
    conn.close()
    return results

def get_recent_logs(limit=15) -> str:
    """دریافت آخرین لاگ‌ها برای ادمین"""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT %s", (limit,))
    logs = cursor.fetchall()
    cursor.close()
    conn.close()
    
    report = "📋 **آخرین رخدادهای سیستم:**\n\n"
    for log in logs:
        time_str = log['timestamp'].strftime("%H:%M:%S")
        report += f"🔹 `{time_str}` | {log['action']} | {log['user_id']}\n"
    return report

# (بقیه توابع باید دقیقاً مثل قبل باشند ولی به جای connect() از get_connection() استفاده کنند)
# برای سادگی کار شما، توابع مهم را اینجا بازنویسی می‌کنم که کپی کنید:

def get_participant_info(national_id: str) -> Optional[Dict]:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM participants WHERE national_id = %s", (national_id,))
    res = cursor.fetchone()
    cursor.close(); conn.close()
    return res

def get_checkin_status(national_id: str) -> Optional[Dict]:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM checkins WHERE national_id = %s", (national_id,))
    res = cursor.fetchone()
    cursor.close(); conn.close()
    return res

def log_action(action: str, user_id: int, national_id: Optional[str] = None, details: str = ""):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO audit_logs (action, user_id, national_id, details) VALUES (%s, %s, %s, %s)",
            (action, str(user_id), national_id, details)
        )
        conn.commit()
        cursor.close(); conn.close()
    except Exception as e:
        print(f"Log Error: {e}")

def create_soft_lock(national_id: str, user_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM soft_locks WHERE expires_at < NOW()") # پاکسازی قدیمی‌ها
    conn.commit()
    try:
        expires_at = datetime.datetime.now() + datetime.timedelta(seconds=config.LOCK_DURATION_SECONDS)
        cursor.execute(
            "INSERT INTO soft_locks (national_id, locked_by, expires_at) VALUES (%s, %s, %s)",
            (national_id, str(user_id), expires_at)
        )
        conn.commit()
        return True
    except mysql.connector.Error:
        return False
    finally:
        cursor.close(); conn.close()

def release_soft_lock(national_id: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM soft_locks WHERE national_id = %s", (national_id,))
    conn.commit()
    cursor.close(); conn.close()

def perform_checkin(national_id: str, user_id: int, status: str):
    conn = get_connection()
    cursor = conn.cursor()
    # اگر قبلا هست پاک کن (برای تغییر وضعیت احتمالی)
    cursor.execute("DELETE FROM checkins WHERE national_id = %s", (national_id,))
    cursor.execute(
        "INSERT INTO checkins (national_id, checked_in_by, status) VALUES (%s, %s, %s)",
        (national_id, str(user_id), status)
    )
    conn.commit()
    cursor.close(); conn.close()
    log_action(f"checkin_{status}", user_id, national_id)

def get_live_stats() -> Dict[str, int]:
    conn = get_connection()
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
    cursor.close(); conn.close()
    return stats

# توابع اکسل (import_participants_from_dataframe, get_checked_in_data_for_excel, ...) 
# نیازی به تغییر الگوریتم ندارند، فقط کانکشن را از get_connection بگیرند.
# فرض بر این است که برنامه نویس شما میتواند این جایگزینی ساده را انجام دهد.
# اما برای تابع import:
def import_participants_from_dataframe(df: pd.DataFrame):
    """وارد کردن داده‌ها با مدیریت خطای سلول‌های خالی"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # 1. تبدیل نام ستون‌ها به حروف کوچک و حذف فاصله (برای اطمینان)
        df.columns = [c.lower().strip() for c in df.columns]
        
        # 2. پر کردن سلول‌های خالی (NaN) با مقدار خالی رشته‌ای
        # این خط حیاتی است: MySQL مقدار NaN را قبول نمی‌کند
        df = df.fillna("")
        
        # 3. اطمینان از اینکه همه چیز رشته است
        df['national_id'] = df['national_id'].astype(str)
        df['full_name'] = df['full_name'].astype(str)
        df['father_name'] = df['father_name'].astype(str)
        df['payment_status'] = df['payment_status'].astype(str)

        insert_query = """
            INSERT INTO participants (national_id, full_name, father_name, payment_status)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                full_name = VALUES(full_name),
                father_name = VALUES(father_name),
                payment_status = VALUES(payment_status)
        """
        
        # تبدیل به لیست تاپل
        data_tuples = []
        for _, row in df.iterrows():
            data_tuples.append((
                row['national_id'], 
                row['full_name'], 
                row['father_name'], 
                row['payment_status']
            ))
        
        cursor.executemany(insert_query, data_tuples)
        conn.commit()
        print(f"✅ Successfully imported {len(data_tuples)} rows.")
        
    except Exception as e:
        print(f"❌ Import Error: {e}")
        raise e # خطا را برگردان تا هندلر بفهمد
    finally:
        cursor.close()
        conn.close()


# در انتهای فایل database.py این دو تابع را جایگزین کنید:

def get_checked_in_data_for_excel() -> pd.DataFrame:
    """دریافت داده‌های پذیرش‌شده (روش اصلاح شده دستی)"""
    conn = get_connection()
    cursor = conn.cursor()
    
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
    
    try:
        cursor.execute(query)
        data = cursor.fetchall()
        # دریافت نام ستون‌ها
        columns = [col[0] for col in cursor.description]
        
        # ساخت دیتافریم دستی
        df = pd.DataFrame(data, columns=columns)
        return df
    except Exception as e:
        print(f"Export Error: {e}")
        return pd.DataFrame()
    finally:
        cursor.close()
        conn.close()

def get_not_checked_in_data_for_excel() -> pd.DataFrame:
    """دریافت داده‌های پذیرش‌نشده (روش اصلاح شده دستی)"""
    conn = get_connection()
    cursor = conn.cursor()
    
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
    
    try:
        cursor.execute(query)
        data = cursor.fetchall()
        columns = [col[0] for col in cursor.description]
        
        df = pd.DataFrame(data, columns=columns)
        return df
    except Exception as e:
        print(f"Export Error: {e}")
        return pd.DataFrame()
    finally:
        cursor.close()
        conn.close()

