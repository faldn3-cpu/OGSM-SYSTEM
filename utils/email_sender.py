import smtplib
from email.mime.text import MIMEText
import streamlit as st
import random
import string
import time

def send_otp_email(to_email):
    """發送 6 位數驗證碼"""
    if "email" not in st.secrets:
        return False, "未設定 SMTP 資訊 (secrets.toml)"
    
    smtp_email = st.secrets["email"]["smtp_email"]
    smtp_password = st.secrets["email"]["smtp_password"]
    
    otp = "".join(random.choices(string.digits, k=6))
    
    msg = MIMEText(f"【士電業務系統】密碼重置驗證碼: {otp}\n\n此驗證碼 10 分鐘內有效，請勿分享給他人。")
    msg['Subject'] = "【士電業務系統】密碼重置驗證碼"
    msg['From'] = smtp_email
    msg['To'] = to_email
    
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=10) as smtp:
            smtp.login(smtp_email, smtp_password)
            smtp.send_message(msg)
        return True, otp
    except Exception as e:
        return False, str(e)