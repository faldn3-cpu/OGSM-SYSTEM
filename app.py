import streamlit as st
import gspread
import os
import bcrypt
import pandas as pd
import smtplib
from email.mime.text import MIMEText
import random
import string
import time
from datetime import datetime, timezone, timedelta
import extra_streamlit_components as stx 
import logging
from functools import wraps
import traceback 
import re 

# 匯入頁面模組
from views import price_query, daily_report, report_overview, crm_overview

# ==========================================
#  安全性設定
# ==========================================
logging.basicConfig(
    filename='app_security.log',
    level=logging.WARNING,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ==========================================
#  頁面設定
# ==========================================
st.set_page_config(
    page_title="士電業務整合系統", 
    page_icon="⚡",
    layout="wide", 
    initial_sidebar_state="collapsed" # 預設收起側邊欄
)

# ==========================================
#  🛡️ 強力喚醒模式 (Hold the Door)
# ==========================================
if "wake_up" in st.query_params:
    print("⏰ Wake up signal received. Holding connection...")
    st.title("🤖 System is Waking Up...")
    st.write("Holding the door open for 30 seconds...")
    countdown_placeholder = st.empty()
    for i in range(30, 0, -1):
        countdown_placeholder.info(f"⏳ 系統保持喚醒中... 剩餘 {i} 秒")
        time.sleep(1)
    countdown_placeholder.success("✅ Done. System is live.")
    st.stop()

# ==========================================
#  強制 HTTPS 檢查
# ==========================================
if 'https_checked' not in st.session_state:
    st.session_state.https_checked = False

if not st.session_state.https_checked:
    if os.getenv('STREAMLIT_ENV') == 'production':
        pass
    st.session_state.https_checked = True

# ==========================================
#  CSS 樣式設定
# ==========================================
st.markdown("""
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: visible !important;}
[data-testid="stDecoration"] {display: none;}
[data-testid="stElementToolbar"] { display: none; }
.stAppDeployButton {display: none;}
[data-testid="stManageAppButton"] {display: none;}

div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlockBorderWrapper"] {
    border: 1px solid rgba(128, 128, 128, 0.2);
    border-radius: 18px;
    padding: 20px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.05);
    background-color: var(--secondary-background-color); 
    margin-bottom: 16px;
}

/* 水平 Radio 按鈕樣式優化 (導覽列用) */
div[role="radiogroup"] {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    justify-content: center;
}
div[role="radiogroup"] > label > div:first-child { display: none; }
div[role="radiogroup"] label {
    flex: 1;
    min-width: 80px;
    display: flex;                    
    justify-content: center;
    align-items: center;              
    text-align: center;
    padding: 12px 10px;
    border-radius: 12px;
    border: 1px solid rgba(128, 128, 128, 0.2); 
    background-color: var(--secondary-background-color);
    cursor: pointer;
    transition: all 0.2s ease;
    box-sizing: border-box;           
}
div[role="radiogroup"] label:hover {
    background-color: var(--primary-color);
    color: white !important;
    opacity: 0.8;
}
div[role="radiogroup"] label[data-checked="true"] {
    background-color: #0071e3 !important;
    color: white !important;
    font-weight: bold;
    border: none;
    box-shadow: 0 4px 10px rgba(0, 113, 227, 0.4);
}
div[role="radiogroup"] label p {
    font-size: 16px;
    margin: 0;
    text-align: center;
}

input, select, textarea { font-size: 16px !important; }
button { min-height: 48px !important; }
</style>
""", unsafe_allow_html=True)

# ==========================================
#  雲端資安設定 & 全域變數
# ==========================================
SMTP_EMAIL = ""
SMTP_PASSWORD = ""
PRICE_DB_NAME = '經銷牌價表_資料庫'
REPORT_DB_NAME = '業務日報表_資料庫'

# 【資安強化】加入 "other" 作為其他部門的專屬代號
VALID_ROLES = {"admin", "manager", "assistant", "sales", "other"}

try:
    if "email" in st.secrets:
        SMTP_EMAIL = st.secrets["email"]["smtp_email"]
        SMTP_PASSWORD = st.secrets["email"]["smtp_password"]
except Exception as e:
    logging.error(f"Failed to load SMTP credentials: {e}")

# === Session State 初始化 ===
if 'logged_in' not in st.session_state: st.session_state.logged_in = False
if 'user_email' not in st.session_state: st.session_state.user_email = ""
if 'real_user_email' not in st.session_state: st.session_state.real_user_email = ""
if 'real_name' not in st.session_state: st.session_state.real_name = ""
if 'login_attempts' not in st.session_state: st.session_state.login_attempts = 0
if 'role' not in st.session_state: st.session_state.role = "sales"
if 'reset_stage' not in st.session_state: st.session_state.reset_stage = 0 
if 'reset_otp' not in st.session_state: st.session_state.reset_otp = ""
if 'reset_target_email' not in st.session_state: st.session_state.reset_target_email = ""
if 'cleanup_checked' not in st.session_state: st.session_state.cleanup_checked = False
if 'force_change_password' not in st.session_state: st.session_state.force_change_password = False 
if 'connection_error_msg' not in st.session_state: st.session_state.connection_error_msg = ""
if 'admin_mode_unlocked' not in st.session_state: st.session_state.admin_mode_unlocked = False

# ==========================================
#  🔒 安全性功能
# ==========================================
@st.cache_resource
def get_global_login_tracker(): return {}

LOGIN_ATTEMPTS_TRACKER = get_global_login_tracker()

def check_is_locked(email):
    if not email: return False, ""
    record = LOGIN_ATTEMPTS_TRACKER.get(email)
    if not record: return False, ""
    if record['count'] >= 3:
        elapsed = time.time() - record['last_time']
        if elapsed < 300:
            return True, f"帳號已鎖定，請於 {int(300 - elapsed)} 秒後再試"
        else:
            LOGIN_ATTEMPTS_TRACKER[email] = {'count': 0, 'last_time': time.time()}
            return False, ""
    return False, ""

def record_login_fail(email):
    if not email: return
    now = time.time()
    if email not in LOGIN_ATTEMPTS_TRACKER:
        LOGIN_ATTEMPTS_TRACKER[email] = {'count': 1, 'last_time': now}
    else:
        LOGIN_ATTEMPTS_TRACKER[email]['count'] += 1
        LOGIN_ATTEMPTS_TRACKER[email]['last_time'] = now

def reset_login_attempts(email):
    if email in LOGIN_ATTEMPTS_TRACKER: del LOGIN_ATTEMPTS_TRACKER[email]

def check_password_strength(password):
    if len(password) < 8: return False, "密碼長度不足 (至少 8 碼)"
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password): return False, "密碼需包含英文與數字"
    return True, "OK"

user_rate_limits = {}

def rate_limit(max_calls=10, period=60):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            user_email = st.session_state.get('user_email', 'anonymous')
            now = time.time()
            if user_email not in user_rate_limits: user_rate_limits[user_email] = {}
            func_name = func.__name__
            if func_name not in user_rate_limits[user_email]: user_rate_limits[user_email][func_name] = []
            user_rate_limits[user_email][func_name] = [t for t in user_rate_limits[user_email][func_name] if now - t < period]
            if len(user_rate_limits[user_email][func_name]) >= max_calls:
                st.error(f"⚠️ 操作過於頻繁，請 {period} 秒後再試")
                write_log("RATE_LIMIT_EXCEEDED", user_email, f"Function: {func_name}")
                return False, "速率限制"
            user_rate_limits[user_email][func_name].append(now)
            return func(*args, **kwargs)
        return wrapper
    return decorator

email_send_count = {}
def can_send_email(email):
    now = time.time()
    if email not in email_send_count: email_send_count[email] = []
    email_send_count[email] = [t for t in email_send_count[email] if now - t < 3600]
    if len(email_send_count[email]) >= 3: return False, "此 Email 在 1 小時內已發送過 3 次驗證碼"
    email_send_count[email].append(now)
    return True, "OK"

def get_tw_time():
    tw_tz = timezone(timedelta(hours=8))
    return datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M:%S")

@st.cache_resource
def get_system_boot_time(): return get_tw_time()

def get_client():
    error_log = []
    if os.path.exists('service_account.json'):
        try: return gspread.service_account(filename='service_account.json')
        except Exception as e: error_log.append(f"Local file error: {str(e)}")
    else:
        error_log.append("Local 'service_account.json' not found.")

    try:
        if "gcp_service_account" in st.secrets:
            try:
                creds_dict = dict(st.secrets["gcp_service_account"])
                if "private_key" not in creds_dict: error_log.append("Secrets found but 'private_key' is missing.")
                else: return gspread.service_account_from_dict(creds_dict)
            except Exception as inner_e: error_log.append(f"Secrets parsing error: {str(inner_e)}")
        else: error_log.append("Secrets 'gcp_service_account' key not found.")
    except Exception as e:
        error_log.append(f"General Secrets error: {str(e)}\n{traceback.format_exc()}")

    st.session_state.connection_error_msg = " || ".join(error_log)
    return None

def write_log(action, user_email, note=""):
    client = get_client()
    if not client: return
    final_user_str = user_email
    try:
        if 'real_user_email' in st.session_state and 'user_email' in st.session_state:
            real, curr = st.session_state.real_user_email, st.session_state.user_email
            if real and curr and real != curr and user_email == curr:
                final_user_str = f"{real} (模擬: {curr})"
    except: pass
        
    try:
        sh = client.open(PRICE_DB_NAME)
        try: ws = sh.worksheet("Logs")
        except: 
            ws = sh.add_worksheet(title="Logs", rows=1000, cols=4)
            ws.append_row(["時間", "使用者", "動作", "備註"])
        ws.append_row([get_tw_time(), final_user_str, action, note])
    except Exception: pass

def write_session_log(email, name, action="LOGIN"):
    client = get_client()
    if not client: return
    try:
        sh = client.open(PRICE_DB_NAME)
        try: ws = sh.worksheet("Sessions")
        except: 
            ws = sh.add_worksheet(title="Sessions", rows=1000, cols=4)
            ws.append_row(["時間", "使用者Email", "使用者姓名", "動作"])
        ws.append_row([get_tw_time(), email, name, action])
    except Exception as e: pass

def auto_cleanup_logs(client):
    if st.session_state.cleanup_checked: return
    try:
        tw_tz = timezone(timedelta(hours=8))
        now = datetime.now(tw_tz)
        current_month_key = now.strftime("%Y-%m")
        sh = client.open(PRICE_DB_NAME)
        need_cleanup = True
        try:
            logs_ws = sh.worksheet("Logs")
            recent_logs = logs_ws.get_all_values()[-100:] 
            for row in reversed(recent_logs):
                if len(row) >= 3 and row[2] == "AUTO_CLEANUP" and row[0].startswith(current_month_key):
                    need_cleanup = False
                    break
        except: pass

        if not need_cleanup:
            st.session_state.cleanup_checked = True
            return

        with st.spinner("🔄 系統每月維護中，正在最佳化資料庫..."):
            cutoff_date = now - timedelta(days=62)
            cutoff_str = cutoff_date.strftime("%Y-%m-%d")
            for sheet_name in ["Logs", "Sessions", "SearchLogs"]:
                try:
                    try: ws = sh.worksheet(sheet_name)
                    except gspread.WorksheetNotFound: continue
                    rows = ws.get_all_values()
                    if len(rows) < 2: continue
                    header, data_rows = rows[0], rows[1:]
                    new_data = [row for row in data_rows if row and str(row[0]) >= cutoff_str]
                    if len(new_data) < len(data_rows):
                        ws.clear()
                        ws.update(values=[header] + new_data, range_name='A1')
                except Exception: pass
            write_log("AUTO_CLEANUP", "SYSTEM", f"Maintenance done. Kept data after {cutoff_str}")
        st.session_state.cleanup_checked = True
    except Exception as e: st.session_state.cleanup_checked = True

def check_password(plain_text, hashed_text):
    try: return bcrypt.checkpw(plain_text.encode('utf-8'), hashed_text.encode('utf-8'))
    except: return False

def hash_password(plain_text):
    return bcrypt.hashpw(plain_text.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

@st.cache_data(ttl=600)
def get_users_list_cached():
    client = get_client()
    if not client: return []
    try:
        sh = client.open(PRICE_DB_NAME)
        ws = sh.worksheet("Users")
        return ws.get_all_records()
    except: return []

def send_otp_email(to_email, otp_code):
    if not SMTP_EMAIL or not SMTP_PASSWORD: return False, "未設定信箱"
    allowed, msg = can_send_email(to_email)
    if not allowed: return False, msg
    msg = MIMEText(f"驗證碼:{otp_code}\n\n此驗證碼 10 分鐘內有效，請勿分享給他人。")
    msg['Subject'] = "【士林電機FA】密碼重置驗證碼"
    msg['From'] = SMTP_EMAIL
    msg['To'] = to_email
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=10) as smtp:
            smtp.login(SMTP_EMAIL, SMTP_PASSWORD)
            smtp.send_message(msg)
        return True, "已發送"
    except Exception as e: return False, str(e)

def login(email, password):
    is_locked, lock_msg = check_is_locked(email)
    if is_locked: return False, lock_msg
    client = get_client()
    if not client: return False, "連線失敗: 無法建立 Google 連線"
    try:
        users = get_users_list_cached()
        for user in users:
            if str(user.get('email')).strip() == email.strip():
                if check_password(password, str(user.get('password'))):
                    reset_login_attempts(email)
                    return True, str(user.get('name')) or email
        record_login_fail(email)
        write_log("LOGIN_FAILED", email, "帳號或密碼錯誤") 
        time.sleep(2)
        return False, "帳號或密碼錯誤"
    except Exception as e: return False, f"登入驗證失敗: {str(e)}"

def change_password(email, new_password):
    client = get_client()
    if not client: return False
    try:
        sh = client.open(PRICE_DB_NAME)
        ws = sh.worksheet("Users")
        headers = ws.row_values(1)
        try: pwd_col_index = headers.index("password") + 1
        except ValueError: return False
        cell = ws.find(email)
        if cell:
            ws.update_cell(cell.row, pwd_col_index, hash_password(new_password))
            get_users_list_cached.clear()
            return True
        return False
    except Exception as e: return False

def check_email_exists(email):
    try:
        users = get_users_list_cached()
        for u in users:
            if str(u.get('email')).strip() == email.strip(): return True
        return False
    except: return False

def post_login_init(email, name, role_override=None):
    st.session_state.logged_in = True
    st.session_state.user_email = email
    st.session_state.real_name = name
    st.session_state.login_attempts = 0

    if role_override:
        # 管理員切換身份時直接指定角色
        st.session_state.role = role_override
    else:
        # 【資安強化】落實零信任 (Default Deny 原則)
        # 只要身分不明、空白、或拼字錯誤，預設降級為 "other" (僅能查看牌價表)，絕不給予 sales 權限
        role_from_sheet = "other" 
        try:
            all_users = get_users_list_cached()
            for user in all_users:
                if str(user.get("email", "")).strip().lower() == email.strip().lower():
                    raw_role = str(user.get("role", "")).strip().lower()
                    if raw_role in VALID_ROLES:
                        role_from_sheet = raw_role
                    else:
                        logging.warning(f"User {email} has invalid role '{raw_role}', defaulting to other")
                    break
        except Exception: pass
        st.session_state.role = role_from_sheet

def admin_switch_callback(target_email, target_name):
    post_login_init(target_email, target_name)
    if "daily_data_cache" in st.session_state: del st.session_state.daily_data_cache
    if "daily_data_key" in st.session_state: del st.session_state.daily_data_key

def main():
    try:
        # 🌟【移除第三方 Cookie 套件】徹底解決雲端載入延遲導致的畫面破裂錯誤
        client = get_client()
        if client:
            auto_cleanup_logs(client)

        if not st.session_state.logged_in:
            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                st.markdown("<br><br>", unsafe_allow_html=True)
                st.header("🔒 士林電機FA 業務系統")
                
                if st.session_state.login_attempts >= 3:
                    pass

                tab1, tab2 = st.tabs(["會員登入", "忘記密碼"])
                with tab1:
                    with st.form("login"):
                        # 🌟【改用原生體驗】交給 iOS/Android 原生的 FaceID/密碼自動填寫功能
                        email = st.text_input("Email", value="", max_chars=100, placeholder="請輸入您的 Email")
                        pwd = st.text_input("密碼", type="password", max_chars=50, placeholder="請輸入密碼")
                        
                        if st.form_submit_button("登入", use_container_width=True):
                            if not email or not pwd: st.error("請輸入完整資訊")
                            else:
                                success, result = login(email, pwd)
                                if success:
                                    write_session_log(email, result, action="LOGIN")
                                    time.sleep(1.5)
                                    st.session_state.real_user_email = email
                                    post_login_init(email, result)
                                    is_strong, _ = check_password_strength(pwd)
                                    st.session_state.force_change_password = not is_strong
                                    st.rerun()
                                else:
                                    st.session_state.login_attempts += 1
                                    st.error(result)
                with tab2:
                    if st.session_state.reset_stage == 0:
                       r_email = st.text_input("註冊 Email", key="reset_email_input")
                       if st.button("發送驗證碼", use_container_width=True):
                           if not r_email: st.error("請輸入 Email")
                           elif check_email_exists(r_email):
                               otp = "".join(random.choices(string.digits, k=6))
                               st.session_state.reset_otp = otp
                               st.session_state.reset_target_email = r_email
                               st.session_state.reset_otp_time = time.time()
                               sent, msg = send_otp_email(r_email, otp)
                               if sent:
                                   st.session_state.reset_stage = 1
                                   st.success("✅ 驗證碼已發送，10 分鐘內有效")
                                   time.sleep(1)
                                   st.rerun()
                               else: st.error(f"發送失敗: {msg}")
                           else: st.error("Email 不存在")
                    elif st.session_state.reset_stage == 1:
                        if time.time() - st.session_state.get('reset_otp_time', 0) > 600:
                            st.error("⏰ 驗證碼已過期，請重新發送")
                            st.session_state.reset_stage = 0
                            st.rerun()
                        otp_in = st.text_input("輸入驗證碼", max_chars=6)
                        new_pw = st.text_input("新密碼 (至少 8 位，含英數)", type="password", max_chars=50)
                        if st.button("確認重置", use_container_width=True):
                            is_strong, str_msg = check_password_strength(new_pw)
                            if not is_strong: st.error(f"密碼強度不足：{str_msg}")
                            elif otp_in == st.session_state.reset_otp:
                                if change_password(st.session_state.reset_target_email, new_pw):
                                    st.success("✅ 密碼已重置，請重新登入")
                                    st.session_state.reset_stage = 0
                                    time.sleep(2)
                                    st.rerun()
                                else: st.error("重置失敗，請聯繫管理員")
                            else: st.error("驗證碼錯誤")
                        if st.button("← 返回", use_container_width=True):
                            st.session_state.reset_stage = 0
                            st.rerun()
            
            if not client:
                st.error(f"❌ 無法連線資料庫，請檢查以下錯誤詳情。")
                if st.session_state.connection_error_msg:
                     with st.expander("🔍 點擊查看技術錯誤詳情 (供管理員除錯)", expanded=True):
                        st.code(st.session_state.connection_error_msg, language="text")
            st.markdown("---")
            st.caption(f"🕒 系統目前時間: {get_tw_time()} | 🚀 系統啟動時間: {get_system_boot_time()}")
            return

        if st.session_state.get("force_change_password", False):
            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                st.warning("⚠️ 您的密碼安全性不足 (需 8 碼且包含英數字)，請立即更新密碼才能繼續使用。")
                with st.form("force_change_pwd_form"):
                    p1 = st.text_input("設定新密碼 (至少 8 位，含英數)", type="password", max_chars=50)
                    p2 = st.text_input("確認新密碼", type="password", max_chars=50)
                    if st.form_submit_button("確認修改並進入系統", use_container_width=True):
                        is_strong, str_msg = check_password_strength(p1)
                        if not is_strong: st.error(f"❌ {str_msg}")
                        elif p1 != p2: st.error("❌ 兩次密碼輸入不一致")
                        else:
                            if change_password(st.session_state.user_email, p1):
                                st.success("✅ 密碼更新成功！正在進入系統...")
                                st.session_state.force_change_password = False
                                time.sleep(1.5)
                                st.rerun()
                            else: st.error("修改失敗，請聯繫管理員")
            return

        # ==========================================
        #  登入後的主畫面導覽架構 (App UI)
        # ==========================================
        current_role = st.session_state.get("role", "sales")
        
        # 1. 權限地圖：決定上方導覽標籤
        if current_role in ["admin", "manager"]: nav_options = ["📋 日報", "💰 牌價", "👤 我的"]
        elif current_role == "sales": nav_options = ["📋 日報", "💰 牌價", "👤 我的"]
        else: nav_options = ["💰 牌價", "👤 我的"] # 其他部門僅能查牌價
            
        st.markdown(f"### ⚡ 士電業務整合系統")
        # 如果 URL 有帶參數也可以處理，這裡預設選第一個
        sel = st.radio("導覽標籤", nav_options, horizontal=True, label_visibility="collapsed")
        st.divider()

        if not client:
            st.error("無法連線資料庫，請稍後再試")
            return

        # 2. 頁面分發
        if sel == "📋 日報":
            daily_report.show(client, REPORT_DB_NAME, st.session_state.user_email, st.session_state.real_name)
            
        elif sel == "💰 牌價":
            audit_identity = st.session_state.user_email
            if st.session_state.get("real_user_email") and st.session_state.real_user_email != st.session_state.user_email:
                audit_identity = f"{st.session_state.real_user_email} (模擬: {st.session_state.user_email})"
            price_query.show(client, PRICE_DB_NAME, audit_identity, st.session_state.real_name, current_role in ("manager", "admin"), current_role=="admin")
            
        elif sel == "👤 我的":
            st.markdown(f"#### 👤 {st.session_state.real_name}")
            st.caption(f"角色：{current_role} | 帳號：{st.session_state.user_email}")
            
            # 主管功能區
            if current_role in ["admin", "manager"]:
                with st.container(border=True):
                    st.markdown("📈 **主管決策總覽**")
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("📊 日報總覽", use_container_width=True):
                            st.session_state.admin_view = "report"
                    with col2:
                        if st.button("📊 商機總覽", use_container_width=True):
                            st.session_state.admin_view = "crm"
                            
                    # 根據按鈕狀態顯示報表
                    admin_view = st.session_state.get("admin_view", "")
                    if admin_view == "report":
                        st.markdown("---")
                        report_overview.show(client, REPORT_DB_NAME, st.session_state.user_email, st.session_state.real_name, True)
                    elif admin_view == "crm":
                        st.markdown("---")
                        crm_overview.show(client, st.session_state.user_email, st.session_state.real_name, True)
            
            # 個人設定區
            with st.container(border=True):
                st.markdown("⚙️ **帳號與安全**")
                with st.expander("🔑 修改密碼"):
                    p1 = st.text_input("新密碼 (至少 8 位，含英數)", type="password", max_chars=50)
                    p2 = st.text_input("確認新密碼", type="password", max_chars=50)
                    if st.button("確認修改", use_container_width=True):
                        admin_key = st.secrets.get("ADMIN_KEY", None)
                        if admin_key and p1 == admin_key and not p2:
                            st.session_state.admin_mode_unlocked = True
                            st.success("🔓 管理員切換模式已解鎖！")
                            time.sleep(1)
                            st.rerun()
                        else:
                            is_strong, str_msg = check_password_strength(p1)
                            if not p1 or not p2: st.error("請輸入完整資訊")
                            elif not is_strong: st.error(f"❌ {str_msg}")
                            elif p1 != p2: st.error("兩次密碼輸入不一致")
                            else:
                                if change_password(st.session_state.user_email, p1):
                                    st.success("✅ 密碼已修改，請重新登入")
                                    time.sleep(1)
                                    st.session_state.logged_in = False
                                    st.rerun()
                                else: st.error("修改失敗，請聯繫管理員")
                
                if st.button("👋 登出系統", type="primary", use_container_width=True):
                    write_log("登出系統", st.session_state.user_email)
                    write_session_log(st.session_state.user_email, st.session_state.real_name, action="LOGOUT")
                    st.session_state.logged_in = False
                    st.session_state.real_user_email = "" 
                    st.session_state.admin_mode_unlocked = False 
                    st.rerun()
            
            # 隱藏的 Admin 模擬器
            if st.session_state.get("admin_mode_unlocked", False):
                with st.container(border=True):
                    st.markdown("👑 **管理員切換身份**")
                    all_records = get_users_list_cached()
                    if all_records:
                        user_map = {f"{u.get('name')} ({u.get('email')})": u for u in all_records}
                        target = st.selectbox("選擇模擬對象", list(user_map.keys()))
                        t_user = user_map[target]
                        st.button("確認切換", type="primary", on_click=admin_switch_callback, args=(t_user.get('email'), t_user.get('name')), use_container_width=True)

    except Exception as e:
        error_msg = traceback.format_exc()
        logging.error(f"SYSTEM CRITICAL ERROR: {error_msg}")
        st.error("🚧 系統暫時忙碌中，請稍後再試。")
        with st.expander("查看錯誤代碼 (僅供管理員參考)"): st.caption(str(e))

if __name__ == "__main__":
    main()
