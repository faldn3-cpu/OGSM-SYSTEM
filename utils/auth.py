import streamlit as st
import bcrypt
import time
import smtplib
import random
import string
import logging
from email.mime.text import MIMEText
from datetime import datetime
from .db import get_db_connection, get_tw_time

# 預設密碼
DEFAULT_PASSWORD = "SEEC5240"
# 郵件發送速率限制暫存
email_send_count = {}

# ==========================================
#  新增：Session Log 寫入函式
# ==========================================
def write_session_log(email, name, action):
    """
    寫入登入/登出紀錄
    位置: [業務日報表_資料庫] (Report_DB) -> 'Session Logs' 頁面
    """
    # 連線至 Report_DB
    sh, msg = get_db_connection("report")
    if not sh:
        logging.error(f"Failed to write session log: {msg}")
        return

    try:
        try:
            ws = sh.worksheet("Session Logs")
        except:
            # 若不存在則建立
            ws = sh.add_worksheet(title="Session Logs", rows=1000, cols=4)
            ws.append_row(["時間", "Email", "姓名", "動作"])
        
        # 寫入紀錄
        ws.append_row([
            get_tw_time().strftime("%Y-%m-%d %H:%M:%S"),
            email,
            name,
            action
        ])
    except Exception as e:
        logging.error(f"Error writing session log: {e}")

# ==========================================
#  原有 Auth 函式
# ==========================================
def hash_password(plain_text):
    return bcrypt.hashpw(plain_text.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(plain_text, hashed_text):
    try: return bcrypt.checkpw(plain_text.encode('utf-8'), hashed_text.encode('utf-8'))
    except: return False

def is_password_weak(password):
    if len(password) < 8: return True
    import re
    if not re.search(r"[A-Za-z]", password) or not re.search(r"[0-9]", password): return True
    return False

def can_send_email(email):
    now = time.time()
    if email not in email_send_count: email_send_count[email] = []
    email_send_count[email] = [t for t in email_send_count[email] if now - t < 3600]
    if len(email_send_count[email]) >= 3: return False, "1小時內已發送過 3 次"
    email_send_count[email].append(now)
    return True, "OK"

def send_otp_email(to_email):
    try:
        if "email" not in st.secrets: return False, None, "未設定 SMTP"
        smtp_email = st.secrets["email"]["smtp_email"]
        smtp_password = st.secrets["email"]["smtp_password"]
    except: return False, None, "SMTP 設定讀取失敗"

    allowed, msg = can_send_email(to_email)
    if not allowed: return False, None, msg

    otp_code = "".join(random.choices(string.digits, k=6))
    msg = MIMEText(f"驗證碼：{otp_code}\n10分鐘內有效。")
    msg['Subject'] = "【士林電機FA】密碼重置"
    msg['From'] = smtp_email
    msg['To'] = to_email
    
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=10) as smtp:
            smtp.login(smtp_email, smtp_password)
            smtp.send_message(msg)
        return True, otp_code, "已發送"
    except Exception as e: return False, None, str(e)

def update_password_in_db(email, new_password):
    sh, msg = get_db_connection("price")
    if not sh: return False, "DB連線失敗"
    try:
        ws = sh.worksheet("Users")
        cell = ws.find(email.strip())
        if cell:
            ws.update_cell(cell.row, 3, hash_password(new_password))
            try:
                ws.update_cell(cell.row, 6, "active")
                ws.update_cell(cell.row, 7, 0)
            except: pass
            return True, "重置成功"
        return False, "無此帳號"
    except Exception as e: return False, str(e)

def login_user(email, password):
    sh, msg = get_db_connection("price")
    if not sh: return False, None, "DB連線失敗"

    try:
        ws = sh.worksheet("Users")
        records = ws.get_all_records()
        target, row_idx = None, -1
        
        for idx, r in enumerate(records):
            if str(r.get("Email")).strip().lower() == email.strip().lower():
                target, row_idx = r, idx + 2
                break
        
        if not target: return False, None, "帳號或密碼錯誤"

        status = str(target.get("Status", "active")).lower()
        if status == "locked":
            last = str(target.get("LastFailTime", ""))
            if last:
                lt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
                if (get_tw_time().replace(tzinfo=None) - lt).total_seconds() < 300:
                    return False, None, "帳號鎖定中 (5分鐘)"
                else:
                    ws.update_cell(row_idx, 6, "active")
                    ws.update_cell(row_idx, 7, 0)

        if check_password(password, str(target.get("Password", ""))):
            if int(target.get("FailAttempts", 0)) > 0:
                ws.update_cell(row_idx, 7, 0)
                ws.update_cell(row_idx, 6, "active")
            
            force = (password == DEFAULT_PASSWORD or is_password_weak(password))
            user_data = {
                "Email": target["Email"],
                "Name": target["Name"],
                "Role": target["Role"],
                "Dept": target["Dept"],
                "ForceChange": force
            }
            
            # 【修改】登入成功，寫入 Session Log
            write_session_log(user_data["Email"], user_data["Name"], "LOGIN")
            
            return True, user_data, "登入成功"
        else:
            fails = int(target.get("FailAttempts", 0)) + 1
            ws.update_cell(row_idx, 7, fails)
            ws.update_cell(row_idx, 8, get_tw_time().strftime("%Y-%m-%d %H:%M:%S"))
            if fails >= 3: ws.update_cell(row_idx, 6, "locked")
            return False, None, "密碼錯誤"

    except Exception as e:
        logging.error(f"Login error: {e}")
        return False, None, "系統錯誤"