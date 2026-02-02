import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
import time
import logging
import traceback
from datetime import datetime, timedelta, timezone

# 設定資料庫名稱對應
DB_NAMES = {
    "price": "經銷牌價表_資料庫",
    "report": "業務日報表_資料庫",
    "crm": "客戶關係表單 (回覆)"
}

BACKUP_FOLDER_ID = "1EcIIcZpOaPmh1nMCx-urfuD3d_jIxyt4"  # V6 指定封存資料夾

def get_tw_time():
    """取得台灣時間"""
    tw_tz = timezone(timedelta(hours=8))
    return datetime.now(tw_tz)

def get_client():
    """建立 Google Sheets 連線 Client"""
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    
    # 優先嘗試讀取 Streamlit Secrets
    try:
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            return gspread.authorize(creds)
    except Exception as e:
        logging.error(f"Secrets connection failed: {e}")

    # 其次嘗試本地檔案
    if os.path.exists('service_account.json'):
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
            return gspread.authorize(creds)
        except Exception as e:
            logging.error(f"Local file connection failed: {e}")
            
    return None

def get_db_connection(db_type="price"):
    """
    Triple DB 連線策略實作
    db_type: 'price', 'report', 'crm'
    """
    client = get_client()
    if not client:
        return None, "Client Init Failed"

    target_db_name = DB_NAMES.get(db_type)
    if not target_db_name:
        return None, f"Unknown DB Type: {db_type}"

    try:
        sh = client.open(target_db_name)
        return sh, None
    except Exception as e:
        logging.error(f"Failed to open DB {target_db_name}: {e}")
        return None, str(e)

def retry_request(func, retries=3, delay=1.5):
    """指數退避重試機制"""
    for i in range(retries):
        try:
            return func()
        except Exception as e:
            if i == retries - 1:
                raise e
            time.sleep(delay * (i + 1))

# ==========================================
#  【新增】系統日誌寫入 (Logs)
# ==========================================
def write_log(action, user_email, note=""):
    """
    寫入系統日誌 (Logs 工作表)
    用於記錄: LOGIN_FAILED, RATE_LIMIT, AUTO_CLEANUP 等
    """
    sh, msg = get_db_connection("price") # Log 存於 Price_DB
    if not sh: return

    try:
        try: 
            ws = sh.worksheet("Logs")
        except: 
            ws = sh.add_worksheet(title="Logs", rows=1000, cols=4)
            ws.append_row(["時間", "使用者", "動作", "備註"])
        
        ws.append_row([
            get_tw_time().strftime("%Y-%m-%d %H:%M:%S"), 
            user_email, 
            action, 
            note
        ])
    except Exception as e:
        logging.error(f"Failed to write log: {e}")

# ==========================================
#  備份與清理模組 (增強版)
# ==========================================
@st.cache_resource
def check_and_run_backup():
    """
    自動備份與清理模組
    觸發條件：雙數月 (2,4,6...) 的 1 號
    增強：執行前檢查 DB 中的 Logs，防止重啟重複執行
    """
    now = get_tw_time()
    
    # 1. 檢查日期條件
    if now.month % 2 != 0 or now.day != 1:
        return

    # 2. 檢查記憶體快取 (Session State)
    cache_key = f"backup_done_{now.year}_{now.month}_{now.day}"
    if st.session_state.get(cache_key):
        return

    client = get_client()
    if not client: return

    try:
        # 3. 【新增】檢查資料庫 Logs (防止伺服器重啟後的重複執行)
        sh_price = client.open(DB_NAMES["price"])
        try:
            ws_logs = sh_price.worksheet("Logs")
            # 讀取最近 50 筆 Log
            recent_logs = ws_logs.get_all_values()[-50:]
            current_month_key = now.strftime("%Y-%m")
            
            for row in reversed(recent_logs):
                # 檢查是否有本月的 AUTO_CLEANUP 紀錄
                # Log 格式: [時間, 使用者, 動作, 備註]
                if len(row) >= 3 and row[2] == "AUTO_CLEANUP":
                    if row[0].startswith(current_month_key):
                        logging.info("Backup already performed this month (Checked DB).")
                        st.session_state[cache_key] = True
                        return
        except:
            # 若 Logs 表不存在或讀取失敗，則繼續嘗試執行備份 (安全起見)
            pass

        logging.info("Starting Auto Backup Sequence...")
        
        # Phase 1: Backup
        targets = ["report", "crm"]
        backup_success = True
        
        for t_type in targets:
            db_name = DB_NAMES[t_type]
            try:
                sh = client.open(db_name)
                backup_name = f"{db_name}_{now.strftime('%Y%m')}_Backup"
                client.copy(sh.id, title=backup_name, folder_id=BACKUP_FOLDER_ID)
                logging.info(f"Backup created: {backup_name}")
            except Exception as e:
                logging.error(f"Backup failed for {db_name}: {e}")
                backup_success = False

        # Phase 2: Prune (僅當備份成功且針對 Report DB)
        if backup_success:
            try:
                sh_report = client.open(DB_NAMES["report"])
                cutoff_date = (now - timedelta(days=62)).strftime("%Y-%m-%d")
                
                worksheets_to_prune = ["Daily_Report", "System_Logs"]
                
                try: all_ws = {ws.title: ws for ws in sh_report.worksheets()}
                except: all_ws = {}

                for ws_name in worksheets_to_prune:
                    if ws_name in all_ws:
                        ws = all_ws[ws_name]
                        rows = ws.get_all_values()
                        if len(rows) > 1:
                            header = rows[0]
                            data = rows[1:]
                            # 簡單判斷：若第一欄包含日期且 >= cutoff
                            new_data = []
                            for row in data:
                                # 嘗試組合前兩欄尋找日期
                                check_str = str(row[0]) + " " + (str(row[1]) if len(row)>1 else "")
                                if check_str >= cutoff_date: 
                                    new_data.append(row)
                            
                            if len(new_data) < len(data):
                                ws.clear()
                                ws.update(values=[header] + new_data, range_name='A1')
                                logging.info(f"Pruned {ws_name}: Removed {len(data) - len(new_data)} rows")

                # 【新增】寫入完成紀錄到 Logs
                write_log("AUTO_CLEANUP", "SYSTEM", f"Backup & Prune completed. Cutoff: {cutoff_date}")
                
            except Exception as e:
                logging.error(f"Prune process failed: {e}")

        # 標記本次執行完成
        st.session_state[cache_key] = True

    except Exception as e:
        logging.error(f"Critical Backup Error: {e}")