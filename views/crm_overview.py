import streamlit as st
import pandas as pd
import plotly.express as px
import gspread
from datetime import date, datetime, timedelta
import logging
from gspread.exceptions import SpreadsheetNotFound

CRM_DB_NAME = "客戶關係表單 (回覆)"
CRM_SHEET_NAME = "表單回應 1"
DIRECT_SALES_NAMES = ["曾仁君", "溫達仁", "楊家豪", "莊富丞", "謝瑞騏", "何宛茹", "張書偉"]
DISTRIBUTOR_SALES_NAMES = ["張何達", "周柏翰", "葉仁豪"]
OPT_ALL, OPT_DIRECT, OPT_DIST = "(1) 🟢 全員選取", "(2) 🔵 直賣全員", "(3) 🟠 經銷全員"
SPECIAL_OPTS = [OPT_ALL, OPT_DIRECT, OPT_DIST]

def clean_currency(val):
    if not val: return 0.0
    if isinstance(val, (int, float)): return float(val)
    try: return float(str(val).replace(",", "").strip())
    except ValueError: return 0.0

@st.cache_data(ttl=600, show_spinner="正在下載 CRM 資料...")
def load_crm_data_cached(_client, db_name, sheet_name):
    try:
        sh = _client.open(db_name)
        try: ws = sh.worksheet(sheet_name)
        except: ws = sh.sheet1
        rows = ws.get_all_values()
        if not rows or len(rows) < 2: return pd.DataFrame()
        headers, data = rows[0], rows[1:]
        n_cols = len(headers)
        data_normalized = [row[:n_cols] + [""] * (n_cols - len(row)) for row in data]
        
        seen = {}
        unique_headers = []
        for h in headers:
            if h in seen:
                seen[h] += 1
                unique_headers.append(f"{h}_{seen[h]}")
            else:
                seen[h] = 0; unique_headers.append(h)

        df = pd.DataFrame(data_normalized, columns=unique_headers)
        rename_map = {}
        for col in df.columns:
            str_col = str(col)
            for kw, tgt in {"客戶名稱":"客戶名稱", "推廣產品":"推廣產品", "總金額":"總金額", "客戶所屬":"客戶所屬", "案件狀況說明":"實際行程", "拜訪目的":"工作內容", "產出日期":"產出日期", "依賴事項":"依賴事項"}.items():
                if kw in str_col: rename_map[col] = tgt; break 
        if rename_map: df.rename(columns=rename_map, inplace=True)
        df["拜訪日期_dt"] = pd.to_datetime(df.get("拜訪日期"), errors='coerce').dt.date
        df["總金額_數值"] = df.get("總金額", pd.Series([0]*len(df))).apply(clean_currency)
        non_dt_cols = [c for c in df.columns if c != "拜訪日期_dt"]
        df[non_dt_cols] = df[non_dt_cols].fillna("")
        return df
    except Exception as e: return pd.DataFrame()

def show(client, user_email, real_name, is_manager):
    st.subheader("📊 CRM 商機總覽")
    current_year = date.today().year
    sheet_options = {f"🟢 [當前] {current_year} 年度": CRM_SHEET_NAME, "🗄️ [歷史] 2025 年度": "20251231"}
    
    selected_sheet_labels = st.multiselect("📂 選擇查詢庫", options=list(sheet_options.keys()), default=[list(sheet_options.keys())[0]])
    if not selected_sheet_labels: st.warning("請至少選擇一個資料庫"); return

    all_dfs = []
    with st.spinner("正在載入..."):
        for label in selected_sheet_labels:
            df_part = load_crm_data_cached(client, CRM_DB_NAME, sheet_options[label])
            if not df_part.empty: all_dfs.append(df_part)
                
    if not all_dfs: st.info("尚無資料"); return
    df_original = pd.concat(all_dfs, ignore_index=True)

    with st.container(border=True):
        sel_fuzzy_kw = st.text_input("🔍 全域關鍵字快搜", placeholder="輸入客戶名稱、目的、狀況...")
        st.markdown("---")
        col1, col2 = st.columns([1, 2])
        with col1: date_range = st.date_input("選擇區間", (date.today().replace(day=1), date.today()))
        with col2:
            target_users = []
            all_sales = set()
            for c in ["填寫人", "客戶所屬"]:
                if c in df_original.columns: all_sales.update([str(v) for v in df_original[c].dropna().unique()])
            all_sales = sorted([x for x in list(all_sales) if x.strip() != ""])

            if is_manager:
                if "crm_sales_select" not in st.session_state: st.session_state.crm_sales_select = []
                def on_sel_change():
                    cur = st.session_state.crm_sales_select
                    st.session_state.crm_sales_select = [cur[-1]] if cur and cur[-1] in SPECIAL_OPTS else [x for x in cur if x not in SPECIAL_OPTS]
                st.multiselect("選擇業務員", options=SPECIAL_OPTS + all_sales, key="crm_sales_select", on_change=on_sel_change)
                opts = st.session_state.crm_sales_select
                fs = set()
                if OPT_ALL in opts: fs.update(all_sales)
                else:
                    if OPT_DIRECT in opts: fs.update([x for x in DIRECT_SALES_NAMES if x in all_sales])
                    if OPT_DIST in opts: fs.update([x for x in DISTRIBUTOR_SALES_NAMES if x in all_sales])
                    fs.update([x for x in opts if x not in SPECIAL_OPTS])
                target_users = list(fs)
            else:
                st.text_input("查看對象", value=real_name, disabled=True)
                target_users = [real_name]

    df_filtered = df_original.copy()
    is_global_search = bool(sel_fuzzy_kw.strip())

    if is_global_search:
        valid_cols = [c for c in ["客戶名稱", "工作內容", "依賴事項", "實際行程"] if c in df_filtered.columns]
        if valid_cols:
            mask_fuzzy = pd.Series([False]*len(df_filtered), index=df_filtered.index)
            for col in valid_cols: mask_fuzzy |= df_filtered[col].astype(str).str.contains(sel_fuzzy_kw, case=False)
            df_filtered = df_filtered[mask_fuzzy]
        if not is_manager:
            mask_user = pd.Series([False]*len(df_filtered), index=df_filtered.index)
            if "填寫人" in df_filtered.columns: mask_user |= df_filtered["填寫人"].astype(str) == real_name
            if "客戶所屬" in df_filtered.columns: mask_user |= df_filtered["客戶所屬"].astype(str) == real_name
            df_filtered = df_filtered[mask_user]
    else:
        if not target_users or not (isinstance(date_range, tuple) and len(date_range) == 2): return
        sd, ed = date_range
        mask_date = df_filtered["拜訪日期_dt"].apply(lambda x: isinstance(x, date) and sd <= x <= ed)
        df_filtered = df_filtered.loc[mask_date]
        
        mask_user = pd.Series([False]*len(df_filtered), index=df_filtered.index)
        if "填寫人" in df_filtered.columns: mask_user |= df_filtered["填寫人"].astype(str).isin(target_users)
        if "客戶所屬" in df_filtered.columns: mask_user |= df_filtered["客戶所屬"].astype(str).isin(target_users)
        df_filtered = df_filtered[mask_user]

    if df_filtered.empty: st.info("無資料"); return

    total_amount, total_count, unique_clients = df_filtered["總金額_數值"].sum(), len(df_filtered), df_filtered["客戶名稱"].nunique()
    
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("💰 商機總額(萬)", f"{total_amount:,.1f}")
    k2.metric("📝 案件數量", total_count)
    k3.metric("🏢 客戶數", unique_clients)
    k4.metric("📈 平均金額(萬)", f"{total_amount/total_count if total_count else 0:,.1f}")

    st.dataframe(df_filtered.sort_values(by="拜訪日期", ascending=False)[["拜訪日期", "填寫人", "客戶名稱", "產業別", "總金額"]], use_container_width=True, hide_index=True)
    
    csv = df_filtered.to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 下載 CRM 報表", data=csv, file_name=f"CRM_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv")
