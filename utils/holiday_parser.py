import pandas as pd
import logging
from datetime import datetime

def parse_holiday_excel(file_obj):
    """
    解析後台上傳的 Excel 行事曆 (V6 Spec 4.C)
    回傳: List of date strings ["2026-01-01", ...]
    """
    try:
        df = pd.read_excel(file_obj, header=None)
        holidays = []
        
        # 遍歷所有欄位
        # 邏輯: 偶數欄(0, 2, 4...) 為日期, 奇數欄(1, 3, 5...) 為備註
        num_cols = len(df.columns)
        
        for i in range(0, num_cols, 2):
            if i + 1 >= num_cols: break
            
            date_col = df.iloc[:, i]
            note_col = df.iloc[:, i+1]
            
            for d, n in zip(date_col, note_col):
                # 檢查備註是否有內容 (非 NaN 且非標題)
                n_str = str(n).strip()
                if pd.isna(n) or n_str == "" or n_str in ["備註", "nan"]:
                    continue
                
                # 嘗試解析日期
                try:
                    if isinstance(d, datetime):
                        holidays.append(d.strftime("%Y-%m-%d"))
                    else:
                        # 嘗試轉字串後解析
                        d_dt = pd.to_datetime(d, errors='coerce')
                        if not pd.isna(d_dt):
                            holidays.append(d_dt.strftime("%Y-%m-%d"))
                except:
                    continue
                    
        return sorted(list(set(holidays)))
        
    except Exception as e:
        logging.error(f"Excel parse error: {e}")
        return []