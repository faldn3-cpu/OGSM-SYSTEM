import streamlit as st
import pandas as pd
from datetime import date, datetime, timedelta
import gspread
import time
from gspread.exceptions import APIError, SpreadsheetNotFound
import logging

SYSTEM_SHEETS = ["DATA", "經銷價(總)", "整套搭配", "參數設定", "總表", "溫控器", "雷射", "SENSOR", "減速機", "變頻器", "伺服", "PLC", "人機", "軟體", "Robot", "配件", "端子臺", "Users", "Logs", "Sessions"]
DIRECT_SALES_NAMES = ["曾仁君", "溫達仁", "楊家豪", "莊富丞", "謝瑞騏", "何宛茹", "張書偉"]
DISTRIBUTOR_SALES_NAMES = ["張何達", "邱文輝", "葉仁豪"]
OPT_ALL, OPT_DIRECT, OPT_DIST = "(1) 🟢 全員選取", "(2) 🔵 直賣全員", "(3) 🟠 經銷全員"
SPECIAL_OPTS = [OPT_ALL, OPT_DIRECT, OPT_DIST]

def get_spreadsheet_with_retry(client, db_name, max_retries=3):
    for attempt in range(max_retries):
        try: return client.open(db_name)
        except SpreadsheetNotFound: raise
        except APIError as e:
            if "429" in str(e) or "Quota exceeded" in str(e):
                if attempt < max_retries - 1: time.sleep((attempt + 1) * 2); continue
                else: raise
            else:
                if attempt < max_retries - 1: time.sleep(1); continue
                else: raise
        except Exception:
            if attempt < max_retries - 1: time.sleep((attempt + 1) * 1.5); continue
            else: raise
    return None

def get_worksheets_retry(spreadsheet):
    try: return {ws.title: ws for ws in spreadsheet.worksheets()}
    except Exception: return {}

def sanitize_csv_field(value):
    if not isinstance(value, str): return value
    if value and value[0] in ['=', '+', '-', '@', '\t', '\r']: return "'" + value
    return value

class APIRateLimiter:
    def __init__(self):
        self.request_times = []
        self.base_delay = 0.5
        self.max_delay = 10
        self.current_delay = self.base_delay
    def wait(self):
        now = time.time()
        self.request_times = [t for t in self.request_times if now - t < 60]
        self.current_delay = min(self.current_delay * 1.5, self.max_delay) if len(self.request_times) > 50 else max(self.current_delay * 0.9, self.base_delay)
        time.sleep(self.current_delay)
        self.request_times.append(now)
    def handle_error(self, attempt): return min(2 ** attempt * 2, 30)

rate_limiter = APIRateLimiter()

def load_data_from_sheet(ws, start_date, end_date):
    for attempt in range(3):
        try:
            if attempt > 0: time.sleep(rate_limiter.handle_error(attempt))
            data = ws.get_all_records()
            ui_columns = ["日期", "星期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]
            if not data: return pd.DataFrame(columns=ui_columns)
            df = pd.DataFrame(data)
            if "項次" in df.columns: df = df.drop(columns=["項次"])
            for col in ui_columns:
                if col not in df.columns: df[col] = ""
            df["日期"] = pd.to_datetime(df["日期"], errors='coerce').dt.date
            df = df.dropna(subset=["日期"]) 
            mask = (df["日期"] >= start_date) & (df["日期"] <= end_date)
            return df.loc[mask].copy().sort_values(by=["日期"], ascending=False)[ui_columns]
        except Exception as e:
            if attempt == 2: raise
    return pd.DataFrame()

def get_all_sales_names(ws_map):
    return [t for t in ws_map.keys() if t not in SYSTEM_SHEETS and not t.startswith("整套_") and "經銷" not in t]

def show(client, db_name, user_email, real_name, is_manager):
    st.subheader("📊 日報總覽與匯出")
    current_year = date.today().year
    db_options = {
        f"🟢 [當前] {current_year} 年度 (主庫)": db_name,
        f"🗄️ [歷史] {current_year - 1} 年度": f"{db_name}_歷史庫_{current_year - 1}",
        f"🗄️ [歷史] {current_year - 2} 年度": f"{db_name}_歷史庫_{current_year - 2}",
        f"🗄️ [歷史] {current_year - 3} 年度": f"{db_name}_歷史庫_{current_year - 3}"
    }
    actual_db_name = db_options[st.selectbox("📂 選擇查詢庫 (年度)", options=list(db_options.keys()))]

    try:
        with st.spinner(f"正在連線資料庫 ({actual_db_name})..."):
            sh = get_spreadsheet_with_retry(client, actual_db_name)
            if not sh: st.error("❌ 無法開啟資料庫"); return
    except Exception as e: st.error("❌ 連線失敗"); return

    col1, col2 = st.columns([2, 1])
    with col1:
        today = date.today()
        date_range = st.date_input("📅 選擇查詢區間", (today - timedelta(days=today.weekday()), today), key="overview_range_picker")

    try:
        with st.spinner("正在讀取工作表列表..."): ws_map = get_worksheets_retry(sh)
    except Exception: st.error("❌ 讀取資料庫結構失敗"); return

    target_users = []
    if is_manager:
        if "overview_sales_select" not in st.session_state: st.session_state.overview_sales_select = []
        if "overview_sales_prev" not in st.session_state: st.session_state.overview_sales_prev = st.session_state.overview_sales_select
        all_sales = get_all_sales_names(ws_map)
        def on_selection_change():
            current, previous = st.session_state.overview_sales_select, st.session_state.overview_sales_prev
            added = [item for item in current if item not in previous]
            new_selection = current
            if added:
                new_item = added[-1]
                new_selection = [new_item] if new_item in SPECIAL_OPTS else [item for item in current if item not in SPECIAL_OPTS]
            st.session_state.overview_sales_select = st.session_state.overview_sales_prev = new_selection
        with col2: st.multiselect("👥 選擇查看對象", options=SPECIAL_OPTS + sorted(all_sales), key="overview_sales_select", on_change=on_selection_change)
        
        selected_options = st.session_state.overview_sales_select
        final_target_set = set()
        if OPT_ALL in selected_options: final_target_set.update(all_sales)
        else:
            if OPT_DIRECT in selected_options: final_target_set.update([n for n in DIRECT_SALES_NAMES if n in all_sales])
            if OPT_DIST in selected_options: final_target_set.update([n for n in DISTRIBUTOR_SALES_NAMES if n in all_sales])
            for opt in selected_options:
                if opt not in SPECIAL_OPTS: final_target_set.add(opt)
        target_users = sorted(list(final_target_set))
    else:
        with col2: st.text_input("👤 查看對象", value=real_name, disabled=True)
        target_users = [real_name]

    if not target_users or not (isinstance(date_range, tuple) and len(date_range) == 2): return

    st.markdown("---")
    start_date, end_date = date_range
    all_data = []
    
    if len(target_users) > 30: st.error("⚠️ 一次最多查詢 30 位業務員"); return
    
    query_key = f"{actual_db_name}_{start_date}_{end_date}_{'_'.join(sorted(target_users))}"
    if st.session_state.get("last_query_key") == query_key and st.session_state.get("last_query_data") is not None:
        final_df = st.session_state.last_query_data
    else:
        with st.spinner(f"彙整中..."):
            progress_bar = st.progress(0)
            failed_users = []
            for idx, user_name in enumerate(target_users):
                ws = ws_map.get(user_name)
                if ws:
                    try:
                        rate_limiter.wait()
                        df = load_data_from_sheet(ws, start_date, end_date)
                        if not df.empty:
                            df.insert(0, "業務員", user_name)
                            all_data.append(df)
                    except Exception: failed_users.append(user_name)
                progress_bar.progress((idx + 1) / len(target_users))
            progress_bar.empty()
            if failed_users: st.error(f"❌ 以下讀取失敗: {', '.join(failed_users)}")

        if not all_data: st.info("🔍 所選區間內無資料。"); return
        final_df = pd.concat(all_data, ignore_index=True)
        st.session_state.last_query_key, st.session_state.last_query_data = query_key, final_df
    
    m1, m2, m3 = st.columns(3)
    m1.metric("總填寫筆數", len(final_df))
    m2.metric("參與人數", len(final_df["業務員"].unique()))
    m3.metric("客戶數", len([c for c in final_df["客戶名稱"].unique() if str(c).strip() not in ["", "-"]]))

    st.dataframe(final_df.head(1000), use_container_width=True, hide_index=True, column_config={"日期": st.column_config.DateColumn("日期", format="YYYY-MM-DD")})

    export_df = final_df.copy()
    if hasattr(export_df, 'map') and callable(getattr(pd.DataFrame, 'map', None)): export_df = export_df.map(sanitize_csv_field)
    else: export_df = export_df.applymap(sanitize_csv_field)
    st.download_button("📥 下載 CSV", data=export_df.to_csv(index=False).encode('utf-8-sig'), file_name=f"日報彙整_{start_date}_{end_date}.csv", mime="text/csv", type="primary")
