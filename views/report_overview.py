import streamlit as st
import pandas as pd
from datetime import date, datetime, timedelta
import time
import logging
from utils import db

# ==========================================
#  設定
# ==========================================
# 系統工作表黑名單 (不視為業務員)
SYSTEM_SHEETS = [
    "SearchLogs", "System_Logs", "Logs", "Users", "Sessions",
    "DATA", "經銷價(總)", "整套搭配", "參數設定", "總表"
]

# 群組定義
OPT_ALL = "(1) 🟢 全員選取"
OPT_DIRECT = "(2) 🔵 直賣全員"
OPT_DIST = "(3) 🟠 經銷全員"
SPECIAL_OPTS = [OPT_ALL, OPT_DIRECT, OPT_DIST]

# ==========================================
#  工具函式
# ==========================================
def get_all_sales_sheets(sh):
    """取得所有業務員工作表名稱"""
    try:
        all_ws = sh.worksheets()
        sales_sheets = []
        for ws in all_ws:
            title = ws.title
            # 排除系統表與備份表
            if title not in SYSTEM_SHEETS and not title.startswith("整套_") and "Backup" not in title:
                sales_sheets.append(title)
        return sorted(sales_sheets)
    except Exception as e:
        logging.error(f"Failed to get worksheets: {e}")
        return []

def load_data_from_sheet(ws, start_date, end_date):
    """讀取單一工作表並過濾日期"""
    try:
        data = ws.get_all_records()
        if not data: return pd.DataFrame()
        
        df = pd.DataFrame(data)
        
        # 欄位標準化
        if "項次" in df.columns: df = df.drop(columns=["項次"])
        ui_cols = ["日期", "星期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]
        for c in ui_cols:
            if c not in df.columns: df[c] = ""
            
        # 日期過濾
        df["日期"] = pd.to_datetime(df["日期"], errors='coerce').dt.date
        df = df.dropna(subset=["日期"])
        
        mask = (df["日期"] >= start_date) & (df["日期"] <= end_date)
        return df.loc[mask].copy()
    except Exception as e:
        logging.warning(f"Error loading sheet {ws.title}: {e}")
        return pd.DataFrame()

def sanitize_csv(val):
    """CSV Injection 防護"""
    if isinstance(val, str) and val.startswith(('=', '+', '-', '@')):
        return f"'{val}"
    return val

# ==========================================
#  主顯示函式
# ==========================================
def show(user_info):
    st.title("📊 日報總覽與匯出")
    
    user_role = user_info.get("Role", "sales")
    user_name = user_info.get("Name", "")
    
    # 權限判斷
    is_manager = user_role in ["admin", "manager"]

    # 連線 Report_DB
    sh, msg = db.get_db_connection("report")
    if not sh:
        st.error(f"資料庫連線失敗: {msg}")
        return

    # 1. 取得人員清單
    all_sales = get_all_sales_sheets(sh)
    
    # 2. 篩選器介面
    with st.container(border=True):
        c1, c2 = st.columns([1, 2])
        with c1:
            today = date.today()
            # 預設本週
            start = today - timedelta(days=today.weekday())
            date_range = st.date_input("📅 查詢區間", (start, today))
        
        with c2:
            target_users = []
            if is_manager:
                # 管理者模式：顯示多選選單
                options = SPECIAL_OPTS + all_sales
                sel = st.multiselect("👥 選擇業務員", options, placeholder="請選擇...")
                
                # 解析選項
                if OPT_ALL in sel:
                    target_users = all_sales
                else:
                    # 處理群組邏輯 (簡化：若選群組，需搭配 System_Config 或名稱規則，此處先採動態全選)
                    # 若使用者需要精確的群組，建議後續在 Config 中設定群組名單
                    temp_users = set()
                    for s in sel:
                        if s in all_sales: temp_users.add(s)
                    target_users = sorted(list(temp_users))
            else:
                # 業務/助理模式：鎖定自己
                st.text_input("👤 查看對象", value=user_name, disabled=True)
                if user_name in all_sales:
                    target_users = [user_name]
                else:
                    st.error("找不到您的日報表，請確認名稱是否一致。")
                    return

    # 3. 執行查詢
    if isinstance(date_range, tuple) and len(date_range) == 2:
        s_date, e_date = date_range
        
        if not target_users:
            if is_manager: st.info("請選擇至少一位業務員。")
            return

        if st.button("🔍 開始查詢", type="primary"):
            all_data = []
            progress = st.progress(0)
            status = st.empty()
            
            for i, u_name in enumerate(target_users):
                status.text(f"正在讀取: {u_name}...")
                try:
                    ws = sh.worksheet(u_name)
                    df = load_data_from_sheet(ws, s_date, e_date)
                    if not df.empty:
                        df.insert(0, "業務員", u_name)
                        all_data.append(df)
                except Exception as e:
                    pass # 若找不到該業務的表，略過
                progress.progress((i + 1) / len(target_users))
            
            status.empty()
            progress.empty()

            if not all_data:
                st.warning("查無資料")
                return

            final_df = pd.concat(all_data, ignore_index=True)
            
            # 4. 統計與顯示
            st.markdown("---")
            m1, m2, m3 = st.columns(3)
            m1.metric("總筆數", len(final_df))
            m2.metric("參與人數", final_df["業務員"].nunique())
            m3.metric("拜訪客戶數", final_df["客戶名稱"].nunique())
            
            st.dataframe(
                final_df, 
                use_container_width=True, 
                hide_index=True,
                column_config={"日期": st.column_config.DateColumn(format="YYYY-MM-DD")}
            )
            
            # 5. 匯出
            csv = final_df.applymap(sanitize_csv).to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 下載 CSV", csv, f"日報彙整_{s_date}_{e_date}.csv", "text/csv")