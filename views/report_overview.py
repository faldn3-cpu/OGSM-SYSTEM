import streamlit as st
import pandas as pd
from datetime import date, datetime, timedelta
import gspread
import time
from gspread.exceptions import APIError
import logging

# === 設定:系統分頁黑名單 ===
SYSTEM_SHEETS = [
    "DATA", "經銷價(總)", "整套搭配", "參數設定", "總表", 
    "溫控器", "雷射", "SENSOR", "減速機", "變頻器", "伺服", 
    "PLC", "人機", "軟體", "Robot", "配件", "端子臺",
    "Users", "Logs", "Sessions"  # 【修復】加入 Sessions
]

# === 設定:直賣全員名單 ===
DIRECT_SALES_NAMES = [
    "曾仁君",
    "溫達仁",
    "楊家豪",
    "莊富丞",
    "謝瑞騏",
    "何宛茹",
    "張書偉"
]

# === 設定:經銷全員名單 ===
DISTRIBUTOR_SALES_NAMES = [
    "張何達",
    "周柏翰",
    "葉仁豪"
]

# === 設定:特殊群組選項名稱 ===
OPT_ALL = "(1) 🟢 全員選取"
OPT_DIRECT = "(2) 🔵 直賣全員"
OPT_DIST = "(3) 🟠 經銷全員"
SPECIAL_OPTS = [OPT_ALL, OPT_DIRECT, OPT_DIST]

# === 【修復】CSV Injection 防護 ===
def sanitize_csv_field(value):
    """清理 CSV 欄位以防注入攻擊"""
    if not isinstance(value, str):
        return value
    
    # 如果開頭是危險字元，加上單引號前綴
    dangerous_chars = ['=', '+', '-', '@', '\t', '\r']
    if value and value[0] in dangerous_chars:
        return "'" + value  # Excel 會將其視為純文字
    
    return value

def load_data_from_sheet(ws, start_date, end_date):
    """讀取資料並清洗"""
    try:
        data = ws.get_all_records()
        ui_columns = ["日期", "星期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]
        
        if not data:
            return pd.DataFrame(columns=ui_columns)
        
        df = pd.DataFrame(data)

        if "項次" in df.columns:
            df = df.drop(columns=["項次"])
        
        for col in ui_columns:
            if col not in df.columns:
                df[col] = ""

        df["日期"] = pd.to_datetime(df["日期"], errors='coerce').dt.date
        df = df.dropna(subset=["日期"]) 

        mask = (df["日期"] >= start_date) & (df["日期"] <= end_date)
        filtered_df = df.loc[mask].copy()
        
        filtered_df = filtered_df.sort_values(by=["日期"], ascending=False)
        return filtered_df[ui_columns]
    except Exception as e:
        logging.error(f"Failed to load data from sheet: {e}")
        return pd.DataFrame()

def get_all_sales_names(all_ws_objects):
    """直接從已抓取的 Worksheet 物件列表中篩選名稱"""
    sales_names = []
    for ws in all_ws_objects:
        title = ws.title
        # 排除系統分頁
        if title not in SYSTEM_SHEETS and not title.startswith("整套_") and "經銷" not in title:
            sales_names.append(title)
    return sales_names

def show(client, db_name, user_email, real_name, is_manager):
    st.title("📊 日報總覽與匯出")

    try:
        sh = client.open(db_name)
    except Exception as e:
        st.error(f"找不到資料庫:{db_name}")
        logging.error(f"Failed to open database: {e}")
        return

    # === 1. 日期選擇器 ===
    col1, col2 = st.columns([2, 1])
    with col1:
        today = date.today()
        start_default = today - timedelta(days=today.weekday())
        end_default = today
        
        date_range = st.date_input(
            "📅 選擇查詢區間", 
            (start_default, end_default),
            key="overview_range_picker"
        )

    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        st.warning("請選擇完整的起始與結束日期")
        return

    # === 2. 人員選擇 ===
    user_role = "manager" if is_manager else "sales"
    current_user_name = real_name
    target_users = []

    try:
        all_ws_objects = sh.worksheets()
        ws_map = {ws.title: ws for ws in all_ws_objects}
    except Exception as e:
        st.error(f"讀取資料庫結構失敗: {e}")
        logging.error(f"Failed to load worksheets: {e}")
        return

    if user_role == "manager":
        if "overview_sales_select" not in st.session_state:
            st.session_state.overview_sales_select = []
        if "overview_sales_prev" not in st.session_state:
            st.session_state.overview_sales_prev = st.session_state.overview_sales_select

        all_sales = get_all_sales_names(all_ws_objects)
        
        # 使用中文名單比對
        valid_direct_names = [name for name in DIRECT_SALES_NAMES if name in all_sales]
        valid_dist_names = [name for name in DISTRIBUTOR_SALES_NAMES if name in all_sales]
        
        menu_options = SPECIAL_OPTS + sorted(all_sales)

        # 互斥選取 Callback
        def on_selection_change():
            current = st.session_state.overview_sales_select
            previous = st.session_state.overview_sales_prev
            
            added = [item for item in current if item not in previous]
            new_selection = current
            
            if added:
                new_item = added[-1]
                if new_item in SPECIAL_OPTS:
                    new_selection = [new_item]
                else:
                    new_selection = [item for item in current if item not in SPECIAL_OPTS]
            
            st.session_state.overview_sales_select = new_selection
            st.session_state.overview_sales_prev = new_selection

        with col2:
            st.multiselect(
                "👥 選擇查看對象",
                options=menu_options,
                key="overview_sales_select", 
                on_change=on_selection_change 
            )

        selected_options = st.session_state.overview_sales_select
        
        final_target_set = set()
        if OPT_ALL in selected_options:
            final_target_set.update(all_sales)
        else:
            if OPT_DIRECT in selected_options:
                final_target_set.update(valid_direct_names)
            if OPT_DIST in selected_options:
                final_target_set.update(valid_dist_names)
            
            for opt in selected_options:
                if opt not in SPECIAL_OPTS:
                    final_target_set.add(opt)
        
        target_users = sorted(list(final_target_set))
            
    else:
        with col2:
            st.text_input("👤 查看對象", value=current_user_name, disabled=True)
        target_users = [current_user_name]

    if not target_users:
        if user_role == "manager":
            st.info("請選擇人員或群組 (預設不顯示，請手動選擇)。")
        else:
            st.error("找不到您的資料表，請聯繫管理員。")
        return

    st.markdown("---")
    
    # === 3. 【修復】讀取與顯示 (速度優化版 + 錯誤處理) ===
    all_data = []
    
    # 【修復】限制最大查詢人數 (防止過度消耗 API)
    MAX_USERS = 50
    if len(target_users) > MAX_USERS:
        st.error(f"⚠️ 一次最多查詢 {MAX_USERS} 位業務員，請縮小範圍")
        return
    
    with st.spinner(f"正在彙整 {len(target_users)} 位業務員的資料... (加速讀取中)"):
        progress_bar = st.progress(0)
        
        for idx, user_name in enumerate(target_users):
            ws = ws_map.get(user_name)
            
            if ws:
                # 重試機制
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        df = load_data_from_sheet(ws, start_date, end_date)
                        if not df.empty:
                            df.insert(0, "業務員", user_name)
                            all_data.append(df)
                        break 
                    
                    except APIError as e:
                        # 只有在遇到 429 時才進入慢速等待
                        if "429" in str(e) or "Quota exceeded" in str(e):
                            if attempt < max_retries - 1:
                                wait_time = (attempt + 1) * 3
                                st.toast(f"⚠️ 流量滿載，暫停 {wait_time} 秒後重試...", icon="⏳")
                                time.sleep(wait_time)
                            else:
                                st.error(f"無法讀取 {user_name} (流量超限)。")
                                logging.error(f"API quota exceeded for {user_name}")
                        else:
                            logging.error(f"API error for {user_name}: {e}")
                            break
                    except Exception as e:
                        logging.error(f"Unexpected error loading {user_name}: {e}")
                        break
            
            # 正常情況下只等待 0.1 秒
            time.sleep(0.1) 
            progress_bar.progress((idx + 1) / len(target_users))
        
        time.sleep(0.1)
        progress_bar.empty()

    if not all_data:
        st.info("🔍 所選區間內無資料。")
        return

    final_df = pd.concat(all_data, ignore_index=True)
    
    # 統計摘要
    st.subheader(f"📈 統計摘要 ({start_date} ~ {end_date})")
    m1, m2, m3 = st.columns(3)
    m1.metric("總填寫筆數", len(final_df))
    m2.metric("參與業務人數", len(final_df["業務員"].unique()))
    m3.metric("拜訪客戶數", len(final_df["客戶名稱"].unique()))

    # 詳細表格
    st.subheader("📝 詳細列表")
    
    # 【修復】限制顯示筆數 (防止頁面過載)
    MAX_DISPLAY_ROWS = 1000
    if len(final_df) > MAX_DISPLAY_ROWS:
        st.warning(f"⚠️ 資料過多，僅顯示前 {MAX_DISPLAY_ROWS} 筆 (下載 CSV 可取得完整資料)")
        display_df = final_df.head(MAX_DISPLAY_ROWS)
    else:
        display_df = final_df
    
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "日期": st.column_config.DateColumn("日期", format="YYYY-MM-DD"),
            "最後更新時間": st.column_config.TextColumn("更新時間", width="small")
        }
    )

    # 【修復】匯出 CSV (加入防護)
    fname = f"業務日報彙整_{start_date}_{end_date}.csv"
    
    # 清理所有欄位
    export_df = final_df.copy()
    export_df = export_df.applymap(sanitize_csv_field)
    
    csv = export_df.to_csv(index=False).encode('utf-8-sig')
    
    st.download_button(
        label="📥 下載 CSV 報表",
        data=csv,
        file_name=fname,
        mime="text/csv",
        type="primary"
    )
    st.caption("⚠️ 下載後請在受信任的環境中開啟檔案")