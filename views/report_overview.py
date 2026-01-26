import streamlit as st
import pandas as pd
from datetime import date, datetime, timedelta
import gspread
import time
from gspread.exceptions import APIError, SpreadsheetNotFound
import logging

# === 設定:系統分頁黑名單 ===
SYSTEM_SHEETS = [
    "DATA", "經銷價(總)", "整套搭配", "參數設定", "總表", 
    "溫控器", "雷射", "SENSOR", "減速機", "變頻器", "伺服", 
    "PLC", "人機", "軟體", "Robot", "配件", "端子臺",
    "Users", "Logs", "Sessions"
]

# === 設定:直賣全員名單 ===
DIRECT_SALES_NAMES = [
    "曾仁君", "溫達仁", "楊家豪", "莊富丞", "謝瑞騏", "何宛茹", "張書偉"
]

# === 設定:經銷全員名單 ===
DISTRIBUTOR_SALES_NAMES = [
    "張何達", "周柏翰", "葉仁豪"
]

# === 設定:特殊群組選項名稱 ===
OPT_ALL = "(1) 🟢 全員選取"
OPT_DIRECT = "(2) 🔵 直賣全員"
OPT_DIST = "(3) 🟠 經銷全員"
SPECIAL_OPTS = [OPT_ALL, OPT_DIRECT, OPT_DIST]

# === 資料庫連線快取 ===
@st.cache_resource(ttl=600)
def get_spreadsheet_with_retry(client, db_name, max_retries=3):
    for attempt in range(max_retries):
        try:
            sh = client.open(db_name)
            return sh
        except SpreadsheetNotFound:
            logging.error(f"Spreadsheet not found: {db_name}")
            raise
        except APIError as e:
            if "429" in str(e) or "Quota exceeded" in str(e):
                if attempt < max_retries - 1:
                    time.sleep((attempt + 1) * 2)
                    continue
                else:
                    raise
            else:
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                else:
                    raise
        except Exception:
            if attempt < max_retries - 1:
                time.sleep((attempt + 1) * 1.5)
                continue
            else:
                raise
    return None

@st.cache_data(ttl=300)
def get_worksheets_cached(_spreadsheet):
    try:
        worksheets = _spreadsheet.worksheets()
        return {ws.title: ws for ws in worksheets}
    except Exception as e:
        logging.error(f"Failed to get worksheets: {e}")
        return {}

def sanitize_csv_field(value):
    if not isinstance(value, str):
        return value
    dangerous_chars = ['=', '+', '-', '@', '\t', '\r']
    if value and value[0] in dangerous_chars:
        return "'" + value
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
        if len(self.request_times) > 50:
            self.current_delay = min(self.current_delay * 1.5, self.max_delay)
        else:
            self.current_delay = max(self.current_delay * 0.9, self.base_delay)
        time.sleep(self.current_delay)
        self.request_times.append(now)
    
    def handle_error(self, attempt):
        return min(2 ** attempt * 2, 30)

rate_limiter = APIRateLimiter()

def load_data_from_sheet(ws, start_date, end_date):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                time.sleep(rate_limiter.handle_error(attempt))
            
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
            filtered_df = df.loc[mask].copy()
            filtered_df = filtered_df.sort_values(by=["日期"], ascending=False)
            return filtered_df[ui_columns]
        except APIError:
            if attempt < max_retries - 1: continue
            else: raise
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()

def get_all_sales_names(ws_map):
    sales_names = []
    for title in ws_map.keys():
        if title not in SYSTEM_SHEETS and not title.startswith("整套_") and "經銷" not in title:
            sales_names.append(title)
    return sales_names

def get_smart_date_range(option):
    """
    【修正邏輯】
    根據選項計算日期區間
    """
    today = date.today()
    
    # 結束日期:今天+1,跳過週末
    end_date = today + timedelta(days=1)
    if end_date.weekday() == 5: # Sat
        end_date += timedelta(days=2)
    elif end_date.weekday() == 6: # Sun
        end_date += timedelta(days=1)
    
    # 起始日期
    if option == "1週":
        start_date = today - timedelta(weeks=1)
    elif option == "2週":
        start_date = today - timedelta(weeks=2)
    elif option == "1個月":
        start_date = today - timedelta(days=30)
    else:
        start_date = today - timedelta(weeks=1)
        
    return start_date, end_date

def show(client, db_name, user_email, real_name, is_manager):
    st.title("📊 日報總覽與匯出")

    try:
        with st.spinner("正在連線資料庫..."):
            sh = get_spreadsheet_with_retry(client, db_name)
            if not sh:
                st.error(f"❌ 無法開啟資料庫: {db_name}")
                return
    except SpreadsheetNotFound:
        st.error(f"❌ 找不到資料庫: {db_name}")
        return
    except Exception:
        st.error(f"❌ 資料庫連線失敗")
        return

    # === 【修正重點】完全移除 date_input,改用 radio ===
    st.markdown("### 📅 選擇查詢區間")
    
    range_option = st.radio(
        "選擇區間 (限制範圍以避免超載)", 
        ["1週", "2週", "1個月"],
        horizontal=True,
        index=0,
        key="overview_range_radio"
    )
    
    start_date, end_date = get_smart_date_range(range_option)
    st.caption(f"目前顯示範圍: {start_date} ~ {end_date}")

    st.markdown("---")

    # === 2. 人員選擇 ===
    user_role = "manager" if is_manager else "sales"
    current_user_name = real_name
    target_users = []

    try:
        with st.spinner("正在讀取工作表列表..."):
            ws_map = get_worksheets_cached(sh)
            if not ws_map:
                st.error("❌ 無法讀取工作表列表")
                return
    except Exception:
        st.error(f"❌ 讀取資料庫結構失敗")
        return

    if user_role == "manager":
        if "overview_sales_select" not in st.session_state:
            st.session_state.overview_sales_select = []
        if "overview_sales_prev" not in st.session_state:
            st.session_state.overview_sales_prev = st.session_state.overview_sales_select

        all_sales = get_all_sales_names(ws_map)
        valid_direct_names = [name for name in DIRECT_SALES_NAMES if name in all_sales]
        valid_dist_names = [name for name in DISTRIBUTOR_SALES_NAMES if name in all_sales]
        menu_options = SPECIAL_OPTS + sorted(all_sales)

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
        st.text_input("👤 查看對象", value=current_user_name, disabled=True)
        target_users = [current_user_name]

    if not target_users:
        if user_role == "manager":
            st.info("請選擇人員或群組。")
        else:
            st.error("找不到您的資料表,請聯繫管理員。")
        return

    st.markdown("---")
    
    # === 3. 讀取與顯示 ===
    all_data = []
    MAX_USERS = 30
    if len(target_users) > MAX_USERS:
        st.error(f"⚠️ 一次最多查詢 {MAX_USERS} 位業務員,請縮小範圍")
        return
    
    estimated_time = len(target_users) * rate_limiter.current_delay
    st.info(f"⏱️ 正在讀取 {len(target_users)} 位業務員資料 (預計需時 {estimated_time:.1f} 秒)")
    
    query_key = f"{start_date}_{end_date}_{'_'.join(sorted(target_users))}"
    
    if "last_query_key" not in st.session_state: st.session_state.last_query_key = ""
    if "last_query_data" not in st.session_state: st.session_state.last_query_data = None
    
    if st.session_state.last_query_key == query_key and st.session_state.last_query_data is not None:
        st.success("✅ 使用快取資料 (無需重新查詢)")
        final_df = st.session_state.last_query_data
    else:
        with st.spinner(f"彙整中... (使用智慧速率限制以避免超載)"):
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
                    except Exception:
                        failed_users.append(user_name)
                progress_bar.progress((idx + 1) / len(target_users))
            
            progress_bar.empty()
            if failed_users:
                st.error(f"❌ 讀取失敗: {', '.join(failed_users)}")

        if not all_data:
            st.info("🔍 所選區間內無資料。")
            return

        final_df = pd.concat(all_data, ignore_index=True)
        st.session_state.last_query_key = query_key
        st.session_state.last_query_data = final_df
    
    # 顯示結果
    st.subheader(f"📈 統計摘要 ({start_date} ~ {end_date})")
    m1, m2, m3 = st.columns(3)
    m1.metric("總填寫筆數", len(final_df))
    m2.metric("參與業務人數", len(final_df["業務員"].unique()))
    m3.metric("拜訪客戶數", len(final_df["客戶名稱"].unique()))

    st.subheader("📝 詳細列表")
    MAX_DISPLAY_ROWS = 1000
    if len(final_df) > MAX_DISPLAY_ROWS:
        st.warning(f"⚠️ 資料過多,僅顯示前 {MAX_DISPLAY_ROWS} 筆")
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

    fname = f"業務日報彙整_{start_date}_{end_date}.csv"
    export_df = final_df.copy()
    export_df = export_df.applymap(sanitize_csv_field)
    csv = export_df.to_csv(index=False).encode('utf-8-sig')
    
    st.download_button("📥 下載 CSV 報表", data=csv, file_name=fname, mime="text/csv", type="primary")
    
    st.markdown("---")
    if st.button("🔄 強制重新查詢 (清除快取)"):
        st.session_state.last_query_key = ""
        st.session_state.last_query_data = None
        st.cache_data.clear()
        st.cache_resource.clear()
        st.success("✅ 快取已清除")
        time.sleep(1)
        st.rerun()