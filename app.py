import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
import bcrypt
import pandas as pd
import smtplib
from email.mime.text import MIMEText
import random
import string
import time
from datetime import datetime, timezone, timedelta
import secrets 
import extra_streamlit_components as stx 
import logging
from functools import wraps

# 匯入頁面模組
from views import price_query, daily_report, report_overview

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
#  強制 HTTPS 檢查
# ==========================================
if 'https_checked' not in st.session_state:
    st.session_state.https_checked = False

if not st.session_state.https_checked:
    if os.getenv('STREAMLIT_ENV') == 'production':
        pass
    st.session_state.https_checked = True

# ==========================================
#  賈伯斯風格 CSS
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

div[role="radiogroup"] > label > div:first-child { display: none; }
div[role="radiogroup"] label {
    width: 100% !important;           
    display: flex;                    
    justify-content: center;          
    align-items: center;              
    text-align: center;               
    padding: 12px 16px;
    margin-bottom: 8px;
    border-radius: 8px;
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
    box-shadow: 0 2px 8px rgba(0, 113, 227, 0.4);
}
div[role="radiogroup"] label p {
    font-size: 15px;
    margin: 0;
    width: 100%;                      
    text-align: center;               
}

input, select, textarea {
    font-size: 16px !important;
}
button {
    min-height: 48px !important;
}
</style>
""", unsafe_allow_html=True)

# ==========================================
#  雲端資安設定 & 全域變數
# ==========================================
SMTP_EMAIL = ""
SMTP_PASSWORD = ""
PRICE_DB_NAME = '經銷牌價表_資料庫'
REPORT_DB_NAME = '業務日報表_資料庫'

ASSISTANTS = ["serena.huang@seec.com.tw", "sarah.wang@seec.com.tw", "yingsin.ye@seec.com.tw"]
MANAGERS = ["welsong@seec.com.tw", "Dennis.chang@seec.com.tw", "steventseng@seec.com.tw"]

try:
    if "email" in st.secrets:
        SMTP_EMAIL = st.secrets["email"]["smtp_email"]
        SMTP_PASSWORD = st.secrets["email"]["smtp_password"]
except Exception as e:
    logging.error(f"Failed to load SMTP credentials: {e}")

# === Session State 初始化 ===
if 'logged_in' not in st.session_state: st.session_state.logged_in = False
if 'user_email' not in st.session_state: st.session_state.user_email = ""
if 'real_name' not in st.session_state: st.session_state.real_name = ""
if 'login_attempts' not in st.session_state: st.session_state.login_attempts = 0
if 'page_radio' not in st.session_state: st.session_state.page_radio = "📝 寫 OGSM 日報"
if 'role' not in st.session_state: st.session_state.role = "sales"
if 'reset_stage' not in st.session_state: st.session_state.reset_stage = 0 
if 'reset_otp' not in st.session_state: st.session_state.reset_otp = ""
if 'reset_target_email' not in st.session_state: st.session_state.reset_target_email = ""

# ==========================================
#  🔒 安全性功能：速率限制器
# ==========================================
user_rate_limits = {}

def rate_limit(max_calls=10, period=60):
    """裝飾器：限制函數呼叫頻率"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            user_email = st.session_state.get('user_email', 'anonymous')
            now = time.time()
            
            if user_email not in user_rate_limits:
                user_rate_limits[user_email] = {}
            
            func_name = func.__name__
            if func_name not in user_rate_limits[user_email]:
                user_rate_limits[user_email][func_name] = []
            
            user_rate_limits[user_email][func_name] = [
                t for t in user_rate_limits[user_email][func_name] if now - t < period
            ]
            
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
    """檢查是否允許發送 Email"""
    now = time.time()
    if email not in email_send_count:
        email_send_count[email] = []
    
    email_send_count[email] = [t for t in email_send_count[email] if now - t < 3600]
    
    if len(email_send_count[email]) >= 3:
        return False, "此 Email 在 1 小時內已發送過 3 次驗證碼"
    
    email_send_count[email].append(now)
    return True, "OK"

# === 工具函式 ===
@st.cache_resource
def get_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    if os.path.exists('service_account.json'):
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
            return gspread.authorize(creds)
        except FileNotFoundError as e:
            logging.error(f"Service account file not found: {e}")
            st.error("系統設定檔遺失，請聯繫管理員")
            return None
        except Exception as e:
            logging.critical(f"Unexpected error in get_client (local): {e}")
            return None
    
    try:
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            return gspread.authorize(creds)
    except Exception as e:
        logging.critical(f"Failed to load GCP credentials: {e}")
        st.error("無法連線資料庫，請稍後再試")
    
    return None

def get_tw_time():
    tw_tz = timezone(timedelta(hours=8))
    return datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M:%S")

def write_log(action, user_email, note=""):
    """寫入操作日誌"""
    client = get_client()
    if not client: return
    try:
        sh = client.open(PRICE_DB_NAME)
        try: 
            ws = sh.worksheet("Logs")
        except: 
            ws = sh.add_worksheet(title="Logs", rows=1000, cols=4)
            ws.append_row(["時間", "使用者", "動作", "備註"])
        
        ws.append_row([get_tw_time(), user_email, action, note])
    except Exception as e:
        logging.error(f"Failed to write log: {e}")

def get_greeting():
    tw_tz = timezone(timedelta(hours=8))
    current_hour = datetime.now(tw_tz).hour
    if current_hour >= 22 or current_hour < 5: return "夜深了，早點休息 🛌"
    elif 5 <= current_hour < 11: return "早安！祝你活力滿滿 ☀️"
    elif 11 <= current_hour < 14: return "午安！記得吃飯休息 🍱"
    elif 14 <= current_hour < 18: return "下午好！繼續加油 💪"
    else: return "晚上好！辛苦了 🌙"

def check_password(plain_text, hashed_text):
    try: 
        return bcrypt.checkpw(plain_text.encode('utf-8'), hashed_text.encode('utf-8'))
    except Exception as e:
        logging.error(f"Password check failed: {e}")
        return False

def hash_password(plain_text):
    return bcrypt.hashpw(plain_text.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

# === Token Session 管理 ===
@rate_limit(max_calls=5, period=300)
def create_session_token(email, days_valid=30):
    """建立 Session Token 並自動清理舊 Token"""
    client = get_client()
    if not client: return None, None
    
    try:
        sh = client.open(PRICE_DB_NAME)
        try: 
            ws = sh.worksheet("Sessions")
        except: 
            ws = sh.add_worksheet(title="Sessions", rows=1000, cols=5)
            ws.append_row(["Token", "Email", "Expires_At", "Created_At"])
        
        all_records = ws.get_all_records()
        rows_to_delete = []
        for idx, row in enumerate(all_records, start=2):
            if row.get("Email") == email:
                rows_to_delete.append(idx)
        
        for row_idx in sorted(rows_to_delete, reverse=True):
            try:
                ws.delete_rows(row_idx)
            except:
                pass
        
        now = datetime.now(timezone(timedelta(hours=8)))
        remaining_records = ws.get_all_records()
        expired_rows = []
        for idx, row in enumerate(remaining_records, start=2):
            try:
                exp = datetime.strptime(row.get("Expires_At"), "%Y-%m-%d %H:%M:%S")
                exp = exp.replace(tzinfo=timezone(timedelta(hours=8)))
                if now > exp:
                    expired_rows.append(idx)
            except: 
                pass
        
        for row_idx in sorted(expired_rows, reverse=True)[:50]:
            try:
                ws.delete_rows(row_idx)
            except:
                pass
        
        token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(days=days_valid)
        ws.append_row([
            token, 
            email, 
            expires_at.strftime("%Y-%m-%d %H:%M:%S"), 
            now.strftime("%Y-%m-%d %H:%M:%S")
        ])
        
        return token, expires_at
    except Exception as e:
        logging.error(f"Token creation failed: {e}")
        write_log("TOKEN_ERROR", email, str(e))
        return None, None

def validate_session_token(token):
    if not token: return None
    client = get_client()
    if not client: return None
    
    try:
        sh = client.open(PRICE_DB_NAME)
        ws = sh.worksheet("Sessions")
        records = ws.get_all_records()
        now = datetime.now(timezone(timedelta(hours=8)))
        
        for row in records:
            if str(row.get("Token")) == token:
                try:
                    expires_at = datetime.strptime(row.get("Expires_At"), "%Y-%m-%d %H:%M:%S")
                    expires_at = expires_at.replace(tzinfo=timezone(timedelta(hours=8)))
                    if now < expires_at: 
                        return row.get("Email")
                except: 
                    pass
        return None
    except Exception as e:
        logging.error(f"Token validation failed: {e}")
        return None

def delete_session_token(token):
    if not token: return
    client = get_client()
    if not client: return
    
    try:
        sh = client.open(PRICE_DB_NAME)
        ws = sh.worksheet("Sessions")
        cell = ws.find(token)
        if cell: 
            ws.delete_rows(cell.row)
            write_log("TOKEN_DELETED", "system", f"Token: {token[:10]}...")
    except Exception as e:
        logging.error(f"Token deletion failed: {e}")

# === 郵件功能 ===
def send_otp_email(to_email, otp_code):
    """發送 OTP 驗證碼"""
    if not SMTP_EMAIL or not SMTP_PASSWORD: 
        return False, "未設定信箱"
    
    allowed, msg = can_send_email(to_email)
    if not allowed:
        write_log("EMAIL_RATE_LIMIT", to_email, msg)
        return False, msg
    
    msg = MIMEText(f"驗證碼:{otp_code}\n\n此驗證碼 10 分鐘內有效，請勿分享給他人。")
    msg['Subject'] = "【士林電機FA】密碼重置驗證碼"
    msg['From'] = SMTP_EMAIL
    msg['To'] = to_email
    
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=10) as smtp:
            smtp.login(SMTP_EMAIL, SMTP_PASSWORD)
            smtp.send_message(msg)
        write_log("EMAIL_SENT", to_email, "OTP 驗證碼")
        return True, "已發送"
    except Exception as e:
        logging.error(f"Email sending failed: {e}")
        write_log("EMAIL_ERROR", to_email, str(e))
        return False, str(e)

def login(email, password):
    client = get_client()
    if not client: return False, "連線失敗"
    
    try:
        sh = client.open(PRICE_DB_NAME)
        ws = sh.worksheet("Users")
        users = ws.get_all_records()
        
        for user in users:
            if str(user.get('email')).strip() == email.strip():
                if check_password(password, str(user.get('password'))):
                    write_log("登入成功", email)
                    return True, str(user.get('name')) or email
        
        write_log("登入失敗", email, "帳號或密碼錯誤")
        return False, "帳號或密碼錯誤"
    except Exception as e:
        logging.error(f"Login failed: {e}")
        return False, str(e)

def change_password(email, new_password):
    client = get_client()
    if not client: return False
    
    try:
        sh = client.open(PRICE_DB_NAME)
        ws = sh.worksheet("Users")
        cell = ws.find(email)
        if cell:
            ws.update_cell(cell.row, 2, hash_password(new_password))
            write_log("密碼已修改", email)
            return True
        return False
    except Exception as e:
        logging.error(f"Password change failed: {e}")
        return False

def check_email_exists(email):
    client = get_client()
    if not client: return False
    
    try:
        sh = client.open(PRICE_DB_NAME)
        ws = sh.worksheet("Users")
        ws.find(email.strip())
        return True
    except: 
        return False

def post_login_init(email, name, role_override=None):
    st.session_state.logged_in = True
    st.session_state.user_email = email
    st.session_state.real_name = name
    st.session_state.login_attempts = 0
    
    if role_override: 
        st.session_state.role = role_override
    else:
        is_mgr = email.strip().lower() in [m.lower() for m in MANAGERS]
        is_asst = email.strip().lower() in [a.lower() for a in ASSISTANTS]
        st.session_state.role = "manager" if is_mgr else "assistant" if is_asst else "sales"
    
    st.session_state.page_radio = "💰 經銷牌價查詢" if st.session_state.role == "assistant" else "📝 寫 OGSM 日報"

# === 主程式 ===
def main():
    cookie_manager = stx.CookieManager()

    # 自動登入
    if not st.session_state.logged_in:
        token = cookie_manager.get(cookie="auth_token")
        if token:
            with st.spinner("自動登入中..."):
                email = validate_session_token(token)
                if email:
                    client = get_client()
                    name = email
                    try:
                        sh = client.open(PRICE_DB_NAME)
                        ws = sh.worksheet("Users")
                        for r in ws.get_all_records():
                            if r.get("email") == email:
                                name = r.get("name")
                                break
                    except: 
                        pass
                    post_login_init(email, name)
                    st.rerun()
                else:
                    # 【修復】安全刪除 Cookie
                    try:
                        cookie_manager.delete("auth_token")
                    except:
                        pass

    if not st.session_state.logged_in:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown("<br><br>", unsafe_allow_html=True)
            st.header("🔒 士林電機FA 業務系統")
            
            if st.session_state.login_attempts >= 3:
                st.error("⚠️ 嘗試次數過多，請重整頁面")
                return

            tab1, tab2 = st.tabs(["會員登入", "忘記密碼"])
            
            with tab1:
                with st.form("login"):
                    email = st.text_input("Email", max_chars=100)
                    pwd = st.text_input("密碼", type="password", max_chars=50)
                    remember = st.checkbox("記住我 (30天)")
                    
                    if st.form_submit_button("登入", use_container_width=True):
                        if not email or not pwd:
                            st.error("請輸入完整資訊")
                        else:
                            success, result = login(email, pwd)
                            if success:
                                if remember:
                                    token_result = create_session_token(email)
                                    if token_result != (False, "速率限制"):
                                        token, expires = token_result
                                        if token: 
                                            cookie_manager.set(
                                                "auth_token", 
                                                token, 
                                                expires_at=expires
                                            )
                                post_login_init(email, result)
                                st.rerun()
                            else:
                                st.session_state.login_attempts += 1
                                st.error(result)
            
            with tab2:
                if st.session_state.reset_stage == 0:
                    r_email = st.text_input("註冊 Email", max_chars=100)
                    
                    if st.button("發送驗證碼", use_container_width=True):
                        if not r_email:
                            st.error("請輸入 Email")
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
                            else: 
                                st.error(f"發送失敗: {msg}")
                        else: 
                            st.error("Email 不存在")
                
                elif st.session_state.reset_stage == 1:
                    if time.time() - st.session_state.get('reset_otp_time', 0) > 600:
                        st.error("⏰ 驗證碼已過期，請重新發送")
                        st.session_state.reset_stage = 0
                        st.rerun()
                    
                    otp_in = st.text_input("輸入驗證碼", max_chars=6)
                    new_pw = st.text_input("新密碼 (至少 6 位)", type="password", max_chars=50)
                    
                    if st.button("確認重置", use_container_width=True):
                        if len(new_pw) < 6:
                            st.error("密碼至少需要 6 個字元")
                        elif otp_in == st.session_state.reset_otp:
                            if change_password(st.session_state.reset_target_email, new_pw):
                                st.success("✅ 密碼已重置，請重新登入")
                                st.session_state.reset_stage = 0
                                time.sleep(2)
                                st.rerun()
                            else: 
                                st.error("重置失敗，請聯繫管理員")
                        else: 
                            st.error("驗證碼錯誤")
                    
                    if st.button("← 返回", use_container_width=True):
                        st.session_state.reset_stage = 0
                        st.rerun()
        
        return

    # 側邊欄
    with st.sidebar:
        greeting = get_greeting()
        st.write(f"👤 **{st.session_state.real_name}**")
        st.caption(f"{greeting}")
        
        current_email = st.session_state.user_email.strip().lower()
        if current_email == "welsong@seec.com.tw":
            st.markdown("---")
            with st.expander("👑 管理員切換身份"):
                try:
                    client = get_client() 
                    if client:
                        sh = client.open(PRICE_DB_NAME)
                        ws_users = sh.worksheet("Users")
                        all_records = ws_users.get_all_records()
                        
                        user_map = {f"{u.get('name')} ({u.get('email')})": u for u in all_records}
                        target_selection = st.selectbox("選擇模擬對象", list(user_map.keys()))
                        
                        if st.button("確認切換", type="primary", use_container_width=True):
                            target_user = user_map[target_selection]
                            
                            write_log(
                                "ADMIN_IMPERSONATE", 
                                current_email,
                                f"Switch to: {target_user.get('email')} ({target_user.get('name')})"
                            )
                            
                            post_login_init(target_user.get('email'), target_user.get('name'))
                            st.success(f"已切換為:{target_user.get('name')}")
                            st.warning("⚠️ 您正在以其他身份操作，所有動作將被記錄")
                            time.sleep(1)
                            st.rerun()
                except Exception as e:
                    st.error("讀取使用者列表失敗")
                    logging.error(f"Impersonate failed: {e}")

        st.markdown("---")
        
        pages = ["📝 寫 OGSM 日報", "💰 經銷牌價查詢", "🔑 修改密碼", "📊 日報總覽", "👋 登出系統"]
        sel = st.radio("功能", pages, key="page_radio", label_visibility="collapsed")

    if sel == "👋 登出系統":
        token = cookie_manager.get("auth_token")
        if token: 
            delete_session_token(token)
        
        # 【修復】安全刪除 Cookie (處理 KeyError)
        try:
            cookie_manager.delete("auth_token")
        except KeyError:
            # Cookie 不存在時忽略錯誤
            pass
        except Exception as e:
            logging.error(f"Cookie deletion error: {e}")
        
        write_log("登出系統", st.session_state.user_email)
        st.session_state.logged_in = False
        st.rerun()

    client = get_client()
    if not client:
        st.error("無法連線資料庫，請稍後再試")
        return

    if sel == "📝 寫 OGSM 日報": 
        daily_report.show(client, REPORT_DB_NAME, st.session_state.user_email, st.session_state.real_name)
    elif sel == "💰 經銷牌價查詢": 
        price_query.show(client, PRICE_DB_NAME, st.session_state.user_email, st.session_state.real_name, st.session_state.role=="manager")
    elif sel == "📊 日報總覽": 
        report_overview.show(client, REPORT_DB_NAME, st.session_state.user_email, st.session_state.real_name, st.session_state.role=="manager")
    elif sel == "🔑 修改密碼":
        st.subheader("修改密碼")
        p1 = st.text_input("新密碼 (至少 6 位)", type="password", max_chars=50)
        p2 = st.text_input("確認新密碼", type="password", max_chars=50)
        
        if st.button("確認", use_container_width=True):
            if not p1 or not p2:
                st.error("請輸入完整資訊")
            elif len(p1) < 6:
                st.error("密碼至少需要 6 個字元")
            elif p1 != p2:
                st.error("兩次密碼輸入不一致")
            else:
                if change_password(st.session_state.user_email, p1):
                    st.success("✅ 密碼已修改，請重新登入")
                    time.sleep(1)
                    st.session_state.logged_in = False
                    st.rerun()
                else:
                    st.error("修改失敗，請聯繫管理員")

if __name__ == "__main__":
    main()