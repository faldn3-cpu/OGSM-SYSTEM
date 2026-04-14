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
    initial_sidebar_state="expanded"
)

# ==========================================
#  🛡️ 強力喚醒模式 (Hold the Door)
# ==========================================
if "wake_up" in st.query_params:
    print("⏰ Wake up signal received. Holding connection...")
    st.title("🤖 System is Waking Up...")
    st.write("Holding the door open for 30 seconds...")
    # 改用倒數計時迴圈，每秒更新畫面，避免 time.sleep(30) 讓連線被伺服器強制切斷
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
# 找到 st.markdown("""<style>...""") 區塊，在裡面加入手機專屬 CSS
st.markdown("""
<style>
/* 📱 手機版專屬優化 */
@media (max-width: 768px) {
    .block-container { padding: 1rem !important; }
    .stButton > button { width: 100% !important; height: 50px !important; margin-bottom: 8px !important; }
    input, select, textarea { font-size: 16px !important; }
}

/* 側邊欄按鈕化美化 */
div[role="radiogroup"] label {
    padding: 12px 15px !important;
    margin-bottom: 8px !important;
    border-radius: 10px !important;
    border: 1px solid rgba(128, 128, 128, 0.1) !important;
}
</style>
""", unsafe_allow_html=True)

# 找到頁面名稱清單，加上圖標
pages = [
    "📝 OGSM日報系統", 
    "💰 牌價表查詢系統", 
    "📊 OGSM日報總覽", 
    "📈 CRM 商機總覽", 
    "🔑 修改密碼", 
    "👋 登出系統"
]

# ==========================================
#  雲端資安設定 & 全域變數
# ==========================================
SMTP_EMAIL = ""
SMTP_PASSWORD = ""
PRICE_DB_NAME = '經銷牌價表_資料庫'
REPORT_DB_NAME = '業務日報表_資料庫'

VALID_ROLES = {"admin", "manager", "assistant", "sales"}  # 合法角色清單

try:
    if "email" in st.secrets:
        SMTP_EMAIL = st.secrets["email"]["smtp_email"]
        SMTP_PASSWORD = st.secrets["email"]["smtp_password"]
except Exception as e:
    logging.error(f"Failed to load SMTP credentials: {e}")

# === Session State 初始化 ===
if 'logged_in' not in st.session_state: st.session_state.logged_in = False
if 'user_email' not in st.session_state: st.session_state.user_email = ""
if 'real_user_email' not in st.session_state: st.session_state.real_user_email = "" # 【新增】真實身分變數
if 'real_name' not in st.session_state: st.session_state.real_name = ""
if 'login_attempts' not in st.session_state: st.session_state.login_attempts = 0
if 'page_radio' not in st.session_state: st.session_state.page_radio = "📝 OGSM日報系統"
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
def get_global_login_tracker():
    return {}

LOGIN_ATTEMPTS_TRACKER = get_global_login_tracker()

def check_is_locked(email):
    if not email: return False, ""
    record = LOGIN_ATTEMPTS_TRACKER.get(email)
    if not record: return False, ""
    if record['count'] >= 3:
        elapsed = time.time() - record['last_time']
        if elapsed < 300:
            remaining = int(300 - elapsed)
            return True, f"帳號已鎖定，請於 {remaining} 秒後再試"
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
    if email in LOGIN_ATTEMPTS_TRACKER:
        del LOGIN_ATTEMPTS_TRACKER[email]

def check_password_strength(password):
    if len(password) < 8:
        return False, "密碼長度不足 (至少 8 碼)"
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return False, "密碼需包含英文與數字"
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

# === 工具函式 ===
def get_tw_time():
    tw_tz = timezone(timedelta(hours=8))
    return datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M:%S")

@st.cache_resource
def get_system_boot_time():
    return get_tw_time()

def get_client():
    error_log = []

    # 1. 嘗試讀取本機的 service_account.json
    if os.path.exists('service_account.json'):
        try:
            # 使用 gspread 內建的本機檔案驗證方法
            return gspread.service_account(filename='service_account.json')
        except Exception as e:
            error_log.append(f"Local file error: {str(e)}")
    else:
        error_log.append("Local 'service_account.json' not found.")

    # 2. 嘗試讀取 Streamlit Secrets (雲端部署環境)
    try:
        if "gcp_service_account" in st.secrets:
            try:
                creds_dict = dict(st.secrets["gcp_service_account"])
                if "private_key" not in creds_dict:
                    error_log.append("Secrets found but 'private_key' is missing.")
                else:
                    # 使用 gspread 內建的字典格式驗證方法
                    return gspread.service_account_from_dict(creds_dict)
            except Exception as inner_e:
                error_log.append(f"Secrets parsing error: {str(inner_e)}")
        else:
            error_log.append("Secrets 'gcp_service_account' key not found.")
    except Exception as e:
        error_log.append(f"General Secrets error: {str(e)}\n{traceback.format_exc()}")

    st.session_state.connection_error_msg = " || ".join(error_log)
    return None

# 【修改】write_log 支援雙重身分紀錄
def write_log(action, user_email, note=""):
    client = get_client()
    if not client: return
    
    # === 雙重身分判斷邏輯 ===
    # 預設使用傳入的 user_email (這確保了 login_failed 等未登入狀態也能紀錄)
    final_user_str = user_email
    
    # 只有當 Session 內有資料，且是針對「當前登入者」的操作時，才進行複合字串處理
    try:
        if 'real_user_email' in st.session_state and 'user_email' in st.session_state:
            real = st.session_state.real_user_email
            curr = st.session_state.user_email
            
            # 如果「真實身分」與「當前身分」不同，且目前要紀錄的對象就是「當前身分」
            if real and curr and real != curr and user_email == curr:
                final_user_str = f"{real} (模擬: {curr})"
    except:
        pass # 若 Session 讀取有誤，則維持原樣
        
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
        try: 
            ws = sh.worksheet("Sessions")
        except: 
            ws = sh.add_worksheet(title="Sessions", rows=1000, cols=4)
            ws.append_row(["時間", "使用者Email", "使用者姓名", "動作"])
        
        ws.append_row([get_tw_time(), email, name, action])
    except Exception as e:
        logging.warning(f"Failed to write session log: {e}")

# ==========================================
#  🧹 智慧型每月自動清理功能
# ==========================================
def auto_cleanup_logs(client):
    if st.session_state.cleanup_checked:
        return

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
                if len(row) >= 3 and row[2] == "AUTO_CLEANUP":
                    if row[0].startswith(current_month_key):
                        need_cleanup = False
                        break
        except:
            pass

        if not need_cleanup:
            st.session_state.cleanup_checked = True
            return

        with st.spinner("🔄 系統每月維護中，正在最佳化資料庫..."):
            cutoff_date = now - timedelta(days=62)
            cutoff_str = cutoff_date.strftime("%Y-%m-%d")
            
            target_sheets = ["Logs", "Sessions", "SearchLogs"]
            cleaned_count = 0
            
            for sheet_name in target_sheets:
                try:
                    try: ws = sh.worksheet(sheet_name)
                    except gspread.WorksheetNotFound: continue
                        
                    rows = ws.get_all_values()
                    if not rows: continue
                    
                    header = rows[0]
                    data_rows = rows[1:]
                    if not data_rows: continue
                    
                    new_data = [row for row in data_rows if row and str(row[0]) >= cutoff_str]
                    
                    if len(new_data) < len(data_rows):
                        ws.clear()
                        ws.update(values=[header] + new_data, range_name='A1')
                        cleaned_count += 1
                        logging.info(f"Cleaned {sheet_name}: Removed {len(data_rows) - len(new_data)} rows")
                except Exception as e:
                    logging.error(f"Cleanup failed for {sheet_name}: {e}")

            if cleaned_count >= 0:
                write_log("AUTO_CLEANUP", "SYSTEM", f"Maintenance done. Kept data after {cutoff_str}")
        
        st.session_state.cleanup_checked = True

    except Exception as e:
        logging.error(f"Auto cleanup critical error: {e}")
        st.session_state.cleanup_checked = True

# ==========================================
#  其他輔助函式 (含快取優化)
# ==========================================
def get_greeting():
    tw_tz = timezone(timedelta(hours=8))
    current_hour = datetime.now(tw_tz).hour
    if current_hour >= 22 or current_hour < 5: return "夜深了，早點休息 🛌"
    elif 5 <= current_hour < 11: return "早安！祝你活力滿滿 ☀️"
    elif 11 <= current_hour < 14: return "午安！記得吃飯休息 🍱"
    elif 14 <= current_hour < 18: return "下午好！繼續加油 💪"
    else: return "晚上好！辛苦了 🌙"

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

# === 郵件 & 登入功能 ===
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
    if is_locked:
        return False, lock_msg

    client = get_client()
    if not client: return False, "連線失敗: 無法建立 Google 連線"
    
    try:
        sh = client.open(PRICE_DB_NAME)
        ws = sh.worksheet("Users")
        users = ws.get_all_records()
        
        email_found = False
        login_success = False
        user_name = ""

        for user in users:
            if str(user.get('email')).strip() == email.strip():
                email_found = True
                if check_password(password, str(user.get('password'))):
                    login_success = True
                    user_name = str(user.get('name')) or email
                    break
        
        if login_success:
            reset_login_attempts(email)
            return True, user_name
        else:
            record_login_fail(email)
            write_log("LOGIN_FAILED", email, "帳號或密碼錯誤") 
            time.sleep(2)
            return False, "帳號或密碼錯誤"

    except Exception as e:
        return False, f"登入驗證失敗: {str(e)}"

def change_password(email, new_password):
    client = get_client()
    if not client: return False
    try:
        sh = client.open(PRICE_DB_NAME)
        ws = sh.worksheet("Users")

        # 找出標題列，確認 password 欄的實際位置，避免硬寫欄號
        headers = ws.row_values(1)
        try:
            pwd_col_index = headers.index("password") + 1  # gspread 欄號從 1 開始
        except ValueError:
            logging.error("change_password: 'password' column not found in Users sheet headers")
            return False

        cell = ws.find(email)
        if cell:
            ws.update_cell(cell.row, pwd_col_index, hash_password(new_password))
            return True
        return False
    except Exception as e:
        logging.error(f"change_password error: {e}")
        return False

def check_email_exists(email):
    client = get_client()
    if not client: return False
    try:
        sh = client.open(PRICE_DB_NAME)
        ws = sh.worksheet("Users")
        ws.find(email.strip())
        return True
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
        # 從 Google Sheet Users 表讀取 role 欄位，不再依賴硬寫名單
        # 若欄位不存在、空白、或填錯，預設為 sales (最低權限，安全考量)
        role_from_sheet = "sales"
        try:
            all_users = get_users_list_cached()
            for user in all_users:
                if str(user.get("email", "")).strip().lower() == email.strip().lower():
                    raw_role = str(user.get("role", "")).strip().lower()
                    if raw_role in VALID_ROLES:
                        role_from_sheet = raw_role
                    else:
                        logging.warning(f"User {email} has invalid role '{raw_role}', defaulting to sales")
                    break
        except Exception as e:
            logging.error(f"post_login_init: failed to fetch role for {email}: {e}")
        st.session_state.role = role_from_sheet

    st.session_state.page_radio = "💰 牌價表查詢系統" if st.session_state.role == "assistant" else "📝 OGSM日報系統"

# 【新增】管理員切換身分的回調函式 (Callback)
# 將切換邏輯移至此處，透過 on_click 觸發，確保在頁面重新渲染前更新狀態
def admin_switch_callback(target_email, target_name):
    # 1. 執行登入初始化
    post_login_init(target_email, target_name)
    
    # 2. 強制清除日報快取，確保資料刷新
    if "daily_data_cache" in st.session_state:
        del st.session_state.daily_data_cache
    if "daily_data_key" in st.session_state:
        del st.session_state.daily_data_key

# === 主程式 ===
def main():
    try:
        cookie_manager = stx.CookieManager()
        
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
                    last_email = cookie_manager.get("last_email") or ""
                    with st.form("login"):
                        email = st.text_input("Email", value=last_email, max_chars=100, placeholder="請輸入您的 Email")
                        pwd = st.text_input("密碼", type="password", max_chars=50, placeholder="請輸入密碼")
                        remember_email = st.checkbox("記住帳號", value=True)
                        if st.form_submit_button("登入", use_container_width=True):
                            if not email or not pwd: st.error("請輸入完整資訊")
                            else:
                                success, result = login(email, pwd)
                                if success:
                                    write_session_log(email, result, action="LOGIN")

                                    if remember_email:
                                        try:
                                            expires = datetime.now(timezone(timedelta(hours=8))) + timedelta(days=365)
                                            cookie_manager.set("last_email", email, expires_at=expires, key="set_last_email_cookie")
                                        except: pass
                                    else:
                                        try: cookie_manager.delete("last_email", key="del_last_email_cookie")
                                        except: pass
                                    
                                    time.sleep(1.5)

                                    # 【新增】記錄真實登入身分 (用於切換身分時的稽核)
                                    st.session_state.real_user_email = email

                                    post_login_init(email, result)
                                    
                                    is_strong, str_msg = check_password_strength(pwd)
                                    if not is_strong:
                                        st.session_state.force_change_password = True
                                    else:
                                        st.session_state.force_change_password = False
                                    
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
                            if not is_strong:
                                st.error(f"密碼強度不足：{str_msg}")
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
            c_time = get_tw_time()
            b_time = get_system_boot_time()
            st.caption(f"🕒 系統目前時間: {c_time} | 🚀 系統啟動時間: {b_time}")
            
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
                        if not is_strong:
                            st.error(f"❌ {str_msg}")
                        elif p1 != p2:
                            st.error("❌ 兩次密碼輸入不一致")
                        else:
                            if change_password(st.session_state.user_email, p1):
                                st.success("✅ 密碼更新成功！正在進入系統...")
                                st.session_state.force_change_password = False
                                time.sleep(1.5)
                                st.rerun()
                            else:
                                st.error("修改失敗，請聯繫管理員")
            return

        with st.sidebar:
            greeting = get_greeting()
            st.write(f"👤 **{st.session_state.real_name}**")
            st.caption(f"{greeting}")
            
            st.markdown("---")
            
            pages = ["📝 OGSM日報系統", "💰 牌價表查詢系統", "📊 OGSM日報總覽", "📊 CRM 商機總覽", "🔑 修改密碼", "👋 登出系統"]
            sel = st.radio("功能", pages, key="page_radio", label_visibility="collapsed")
            
            st.markdown("---")
            try:
                file_timestamp = os.path.getmtime(__file__)
                tw_time = datetime.fromtimestamp(file_timestamp, timezone(timedelta(hours=8)))
                last_updated_str = tw_time.strftime('%Y-%m-%d %H:%M')
                st.caption(f"檔案版本: {last_updated_str}")
            except:
                st.caption("Ver: Latest")
                
            boot_time = get_system_boot_time()
            st.caption(f"系統啟動: {boot_time}")

            # 【新增】管理員切換身份 (隱藏功能：需先解鎖)
            if st.session_state.get("admin_mode_unlocked", False):
                st.markdown("---")
                with st.expander("👑 管理員切換身份 (Unlocked)", expanded=True):
                    all_records = get_users_list_cached()
                    if all_records:
                        user_map = {f"{u.get('name')} ({u.get('email')})": u for u in all_records}
                        target = st.selectbox("選擇模擬對象", list(user_map.keys()))
                        t_user = user_map[target]
                        st.button("確認切換", type="primary", on_click=admin_switch_callback, args=(t_user.get('email'), t_user.get('name')))

        if sel == "👋 登出系統":
            write_log("登出系統", st.session_state.user_email)
            write_session_log(st.session_state.user_email, st.session_state.real_name, action="LOGOUT")
            st.session_state.logged_in = False
            # 登出時重置真實身分與解鎖狀態
            st.session_state.real_user_email = "" 
            st.session_state.admin_mode_unlocked = False 
            st.rerun()

        if not client:
            st.error("無法連線資料庫，請稍後再試")
            return

        if sel == "📝 OGSM日報系統": 
            daily_report.show(client, REPORT_DB_NAME, st.session_state.user_email, st.session_state.real_name)
        elif sel == "💰 牌價表查詢系統": 
            # 【新增】組合稽核身分字串，傳入 price_query
            audit_identity = st.session_state.user_email
            if st.session_state.get("real_user_email") and st.session_state.real_user_email != st.session_state.user_email:
                audit_identity = f"{st.session_state.real_user_email} (模擬: {st.session_state.user_email})"
            
            # 將 audit_identity 傳入，確保 SearchLogs 紀錄雙重身分
            price_query.show(client, PRICE_DB_NAME, audit_identity, st.session_state.real_name, st.session_state.role in ("manager", "admin"), st.session_state.role=="admin")
        elif sel == "📊 OGSM日報總覽": 
            report_overview.show(client, REPORT_DB_NAME, st.session_state.user_email, st.session_state.real_name, st.session_state.role in ("manager", "admin"))
        elif sel == "📊 CRM 商機總覽":
            crm_overview.show(client, st.session_state.user_email, st.session_state.real_name, st.session_state.role in ("manager", "admin"))
        elif sel == "🔑 修改密碼":
            st.subheader("修改密碼")
            p1 = st.text_input("新密碼 (至少 8 位，含英數)", type="password", max_chars=50)
            p2 = st.text_input("確認新密碼", type="password", max_chars=50)
            if st.button("確認", use_container_width=True):
                admin_key = st.secrets.get("ADMIN_KEY", None)
                
                if admin_key and p1 == admin_key and not p2:
                    st.session_state.admin_mode_unlocked = True
                    st.success("🔓 管理員切換模式已解鎖！(請查看側邊欄底部)")
                    time.sleep(1)
                    st.rerun()
                    return

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
    
    except Exception as e:
        error_msg = traceback.format_exc()
        logging.error(f"SYSTEM CRITICAL ERROR: {error_msg}")
        
        st.error("🚧 系統暫時忙碌中，請稍後再試。")
        with st.expander("查看錯誤代碼 (僅供管理員參考)"):
            st.caption(str(e))

if __name__ == "__main__":
    main()
