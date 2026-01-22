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

# 匯入頁面模組
from views import price_query, daily_report, report_overview

# ==========================================
#  1. 頁面設定
# ==========================================
st.set_page_config(
    page_title="士電業務整合系統", 
    page_icon="⚡",
    layout="wide", 
    initial_sidebar_state="expanded"
)

# ==========================================
#  2. 賈伯斯風格 CSS (深色模式修復版)
# ==========================================
st.markdown("""
<style>
/* 隱藏預設雜訊 */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: visible !important;}
[data-testid="stDecoration"] {display: none;}
[data-testid="stElementToolbar"] { display: none; }
.stAppDeployButton {display: none;}
[data-testid="stManageAppButton"] {display: none;}

/* 卡片設計 - 適應深色/淺色模式 */
div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlockBorderWrapper"] {
    border: 1px solid rgba(128, 128, 128, 0.2);
    border-radius: 18px;
    padding: 20px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.05);
    background-color: var(--secondary-background-color); 
    margin-bottom: 16px;
}

/* 側邊欄優化 */
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
    background-color: #0071e3 !important; /* Apple Blue */
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

/* 輸入框優化 - 加大觸控區 */
input, select, textarea {
    font-size: 16px !important; /* 防止 iOS 自動縮放 */
}
button {
    min-height: 48px !important; /* 手指好按的高度 */
}
</style>
""", unsafe_allow_html=True)

# ==========================================
#  🔐 雲端資安設定 & 全域變數
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
except: pass

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

# === 工具函式 ===
@st.cache_resource
def get_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    if os.path.exists('service_account.json'):
        creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
        return gspread.authorize(creds)
    try:
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            return gspread.authorize(creds)
    except: pass
    return None

def get_tw_time():
    tw_tz = timezone(timedelta(hours=8))
    return datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M:%S")

def write_log(action, user_email, note=""):
    client = get_client()
    if not client: return
    try:
        sh = client.open(PRICE_DB_NAME)
        try: ws = sh.worksheet("Logs")
        except: return 
        ws.append_row([get_tw_time(), user_email, action, note])
    except: pass

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

# === Token Session 管理 ===
def create_session_token(email, days_valid=30):
    client = get_client()
    if not client: return None, None
    try:
        sh = client.open(PRICE_DB_NAME)
        try: ws = sh.worksheet("Sessions")
        except: 
            ws = sh.add_worksheet(title="Sessions", rows=1000, cols=5)
            ws.append_row(["Token", "Email", "Expires_At", "Created_At"])
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone(timedelta(hours=8)))
        expires_at = now + timedelta(days=days_valid)
        ws.append_row([token, email, expires_at.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S")])
        return token, expires_at
    except: return None, None

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
                    expires_at = datetime.strptime(row.get("Expires_At"), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=8)))
                    if now < expires_at: return row.get("Email")
                except: pass
        return None
    except: return None

def delete_session_token(token):
    if not token: return
    client = get_client()
    if not client: return
    try:
        sh = client.open(PRICE_DB_NAME)
        ws = sh.worksheet("Sessions")
        cell = ws.find(token)
        if cell: ws.delete_rows(cell.row)
    except: pass

# === 郵件功能 ===
def send_otp_email(to_email, otp_code):
    if not SMTP_EMAIL or not SMTP_PASSWORD: return False, "未設定信箱"
    msg = MIMEText(f"驗證碼：{otp_code}")
    msg['Subject'] = "【士林電機FA】密碼重置驗證碼"
    msg['From'] = SMTP_EMAIL
    msg['To'] = to_email
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(SMTP_EMAIL, SMTP_PASSWORD)
            smtp.send_message(msg)
        return True, "已發送"
    except Exception as e: return False, str(e)

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
        return False, "帳號或密碼錯誤"
    except Exception as e: return False, str(e)

def change_password(email, new_password):
    client = get_client()
    if not client: return False
    try:
        sh = client.open(PRICE_DB_NAME)
        ws = sh.worksheet("Users")
        cell = ws.find(email)
        if cell:
            ws.update_cell(cell.row, 2, hash_password(new_password))
            return True
        return False
    except: return False

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
    if role_override: st.session_state.role = role_override
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
                    except: pass
                    post_login_init(email, name)
                    st.rerun()
                else:
                    cookie_manager.delete("auth_token")

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
                    email = st.text_input("Email")
                    pwd = st.text_input("密碼", type="password")
                    remember = st.checkbox("記住我 (30天)")
                    if st.form_submit_button("登入", use_container_width=True):
                        success, result = login(email, pwd)
                        if success:
                            if remember:
                                token, expires = create_session_token(email)
                                if token: cookie_manager.set("auth_token", token, expires_at=expires)
                            post_login_init(email, result)
                            st.rerun()
                        else:
                            st.session_state.login_attempts += 1
                            st.error(result)
            
            with tab2:
                if st.session_state.reset_stage == 0:
                    r_email = st.text_input("註冊 Email")
                    if st.button("發送驗證碼", use_container_width=True):
                        if check_email_exists(r_email):
                            otp = "".join(random.choices(string.digits, k=6))
                            st.session_state.reset_otp = otp
                            st.session_state.reset_target_email = r_email
                            sent, msg = send_otp_email(r_email, otp)
                            if sent:
                                st.session_state.reset_stage = 1
                                st.rerun()
                            else: st.error(msg)
                        else: st.error("Email 不存在")
                elif st.session_state.reset_stage == 1:
                    otp_in = st.text_input("輸入驗證碼")
                    new_pw = st.text_input("新密碼", type="password")
                    if st.button("確認重置", use_container_width=True):
                        if otp_in == st.session_state.reset_otp:
                            if change_password(st.session_state.reset_target_email, new_pw):
                                st.success("密碼已重置")
                                st.session_state.reset_stage = 0
                                st.rerun()
                            else: st.error("重置失敗")
                        else: st.error("驗證碼錯誤")
        
        # 【修正】已完全移除 hidden mode 程式碼
        return

    # 側邊欄
    with st.sidebar:
        greeting = get_greeting()
        st.write(f"👤 **{st.session_state.real_name}**")
        st.caption(f"{greeting}")
        
        # [功能升級] 正規管理員切換身分 (僅限 曾維崧 welsong@seec.com.tw)
        # 必須通過正常登入流程後，系統確認是該 Email 才會顯示此區塊
        current_email = st.session_state.user_email.strip().lower()
        if current_email == "welsong@seec.com.tw" or st.session_state.real_name == "曾維崧":
            st.markdown("---")
            with st.expander("👑 管理員切換身分"):
                try:
                    client = get_client() 
                    if client:
                        sh = client.open(PRICE_DB_NAME)
                        ws_users = sh.worksheet("Users")
                        all_records = ws_users.get_all_records()
                        
                        # 製作選項: "姓名 (Email)"
                        user_map = {f"{u.get('name')} ({u.get('email')})": u for u in all_records}
                        
                        target_selection = st.selectbox("選擇模擬對象", list(user_map.keys()))
                        
                        if st.button("確認切換", type="primary", use_container_width=True):
                            target_user = user_map[target_selection]
                            # 執行切換
                            post_login_init(target_user.get('email'), target_user.get('name'))
                            st.success(f"已切換為：{target_user.get('name')}")
                            time.sleep(1)
                            st.rerun()
                except Exception as e:
                    st.error(f"讀取使用者列表失敗")

        st.markdown("---")
        
        pages = ["📝 寫 OGSM 日報", "💰 經銷牌價查詢", "🔑 修改密碼", "📊 日報總覽", "👋 登出系統"]
        sel = st.radio("功能", pages, key="page_radio", label_visibility="collapsed")

    if sel == "👋 登出系統":
        token = cookie_manager.get("auth_token")
        if token: delete_session_token(token)
        cookie_manager.delete("auth_token")
        st.session_state.logged_in = False
        st.rerun()

    client = get_client()
    if not client:
        st.error("無法連線資料庫")
        return

    if sel == "📝 寫 OGSM 日報": daily_report.show(client, REPORT_DB_NAME, st.session_state.user_email, st.session_state.real_name)
    elif sel == "💰 經銷牌價查詢": price_query.show(client, PRICE_DB_NAME, st.session_state.user_email, st.session_state.real_name, st.session_state.role=="manager")
    elif sel == "📊 日報總覽": report_overview.show(client, REPORT_DB_NAME, st.session_state.user_email, st.session_state.real_name, st.session_state.role=="manager")
    elif sel == "🔑 修改密碼":
        st.subheader("修改密碼")
        p1 = st.text_input("新密碼", type="password")
        p2 = st.text_input("確認新密碼", type="password")
        if st.button("確認"):
            if p1 and p1==p2:
                if change_password(st.session_state.user_email, p1):
                    st.success("密碼已修改，請重新登入")
                    time.sleep(1)
                    st.session_state.logged_in = False
                    st.rerun()
            else: st.error("密碼不一致或為空")

if __name__ == "__main__":
    main()