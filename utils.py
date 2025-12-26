import pandas as pd
import io
from typing import Optional

def validate_national_id(nid: str) -> bool:
    """بررسی میکند که آیا کد ملی ۱۰ رقمی و عددی است یا خیر"""
    return nid.isdigit() and len(nid) == 10

def process_excel_file(file_bytes: bytes) -> Optional[pd.DataFrame]:
    """خواندن فایل اکسل و تبدیل آن به دیتافریم پانداز"""
    try:
        # ستون‌های مورد انتظار در فایل اکسل
        expected_columns = ['national_id', 'full_name', 'father_name', 'payment_status']
        
        # خواندن بایت‌های فایل در حافظه
        file_like_object = io.BytesIO(file_bytes)
        
        df = pd.read_excel(file_like_object, dtype={'national_id': str})
        
        # بررسی وجود ستون‌های لازم
        if not all(col in df.columns for col in expected_columns):
            return None

        # اطمینان از اینکه کد ملی فرمت رشته دارد
        df['national_id'] = df['national_id'].astype(str)
        
        return df[expected_columns]
    except Exception as e:
        print(f"Error processing excel file: {e}")
        return None
