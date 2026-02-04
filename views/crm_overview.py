import streamlit as st
import pandas as pd
import plotly.express as px
import gspread
from datetime import date, datetime, timedelta
import time
import logging

# === 設定 ===
CRM_DB_NAME = "客戶關係表單 (回覆)"
CRM_SHEET_NAME = "表單回應 1"

# === 設定: 人員群組 ===
DIRECT_SALES_NAMES = [
    "曾仁君", "溫達仁", "楊家豪", "莊富丞", "謝瑞騏", "何宛茹", "張書偉"
]
DISTRIBUTOR_SALES_NAMES = [
    "張何達", "周柏翰", "葉仁豪"
]
OPT_ALL = "(1) 🟢 全員選取"
OPT_DIRECT = "(2) 🔵 直賣全員"
OPT_DIST = "(3) 🟠 經銷全員"
SPECIAL_OPTS = [OPT_ALL, OPT_DIRECT, OPT_DIST]

# === 資料處理函式 ===

def clean_currency(val):
    """將金額字串轉換為 float"""
    if not val: return 0.0
    if isinstance(val, (int, float)): return float(val)
    val_str = str(val).replace(",", "").strip()
    try:
        return float(val_str)
    except ValueError:
        return 0.0

def parse_crm_date(date_val):
    """解析 CRM 日期格式 (預期為 YYYY/M/D)"""
    if not date_val: return None
    try:
        return pd.to_datetime(date_val).date()
    except:
        return None

@st.cache_data(ttl=600, show_spinner="正在下載 CRM 資料...")
def load_crm_data_cached(_client, db_name, sheet_name):
    """
    讀取整張 CRM 表單並轉為 DataFrame
    """
    try:
        sh = _client.open(db_name)
        try:
            ws = sh.worksheet(sheet_name)
        except:
            ws = sh.sheet1
        
        # 改用 get_all_values 以避免 header 重複錯誤
        rows = ws.get_all_values()
        if not rows or len(rows) < 2:
            return pd.DataFrame()
            
        headers = rows[0]
        data = rows[1:]
        
        df = pd.DataFrame(data, columns=headers)
        
        # 智慧欄位對應
        # 【修改 1】新增 "依賴事項" 到對應表，確保它被正確讀取
        column_keywords = {
            "客戶名稱": "客戶名稱",
            "推廣產品": "推廣產品",
            "總金額": "總金額",
            "客戶所屬": "客戶所屬",
            "案件狀況說明": "實際行程",
            "拜訪目的": "工作內容",
            "產出日期": "產出日期",
            "依賴事項": "依賴事項"  # 新增
        }
        
        rename_map = {}
        for col in df.columns:
            str_col = str(col)
            for kw, target in column_keywords.items():
                if kw in str_col:
                    rename_map[col] = target
                    break 
        
        if rename_map:
            df.rename(columns=rename_map, inplace=True)
        
        if "拜訪日期" in df.columns:
            df["拜訪日期_dt"] = pd.to_datetime(df["拜訪日期"], errors='coerce').dt.date
        else:
            df["拜訪日期_dt"] = None
            
        if "總金額" in df.columns:
            df["總金額_數值"] = df["總金額"].apply(clean_currency)
        else:
            df["總金額_數值"] = 0.0

        df.fillna("", inplace=True)
        return df

    except Exception as e:
        logging.error(f"CRM data load error: {e}")
        st.error(f"無法讀取 CRM 資料: {e}")
        return pd.DataFrame()

# === 主顯示函式 ===
def show(client, user_email, real_name, is_manager):
    st.title("📊 CRM 商機總覽")

    # 1. 讀取資料
    df_original = load_crm_data_cached(client, CRM_DB_NAME, CRM_SHEET_NAME)
    
    if df_original.empty:
        st.info("尚無 CRM 資料或無法讀取 (可能是空的)。")
        if st.button("🔄 重試"):
            st.cache_data.clear()
            st.rerun()
        return

    required_cols = ["填寫人", "客戶所屬", "產業別", "通路商", "推廣產品", "客戶名稱", "總金額"]
    missing_cols = [c for c in required_cols if c not in df_original.columns]
    
    if missing_cols:
        st.error(f"❌ 資料表處理後仍缺少關鍵欄位: {', '.join(missing_cols)}")
        return

    # 2. 側邊/上方篩選器
    with st.container(border=True):
        col1, col2 = st.columns([1, 2])
        
        # --- 日期篩選 ---
        with col1:
            today = date.today()
            start_default = today.replace(day=1)
            end_default = today
            
            date_range = st.date_input(
                "📅 選擇拜訪日期區間", 
                (start_default, end_default),
                key="crm_date_range"
            )

        # --- 人員篩選 (權限控管 + 互斥邏輯) ---
        with col2:
            target_users = []
            
            cols_to_check = [c for c in ["填寫人", "客戶所屬"] if c in df_original.columns]
            all_sales_in_data = set()
            for c in cols_to_check:
                unique_vals = df_original[c].dropna().unique()
                for v in unique_vals:
                    all_sales_in_data.add(str(v))
            all_sales_in_data = sorted([x for x in list(all_sales_in_data) if str(x).strip() != ""])

            if is_manager:
                if "crm_sales_select" not in st.session_state:
                    st.session_state.crm_sales_select = []
                if "crm_sales_prev" not in st.session_state:
                    st.session_state.crm_sales_prev = st.session_state.crm_sales_select

                menu_options = SPECIAL_OPTS + all_sales_in_data

                def on_selection_change():
                    current = st.session_state.crm_sales_select
                    previous = st.session_state.crm_sales_prev
                    
                    added = [item for item in current if item not in previous]
                    new_selection = current
                    
                    if added:
                        new_item = added[-1]
                        if new_item in SPECIAL_OPTS:
                            new_selection = [new_item]
                        else:
                            new_selection = [item for item in current if item not in SPECIAL_OPTS]
                    
                    st.session_state.crm_sales_select = new_selection
                    st.session_state.crm_sales_prev = new_selection

                st.multiselect(
                    "👥 選擇業務員 (篩選 填寫人 或 客戶所屬)",
                    options=menu_options,
                    key="crm_sales_select",
                    on_change=on_selection_change,
                    placeholder="請選擇人員或群組..."
                )
                
                selected_opts = st.session_state.crm_sales_select
                
                final_target_set = set()
                if OPT_ALL in selected_opts:
                    final_target_set.update(all_sales_in_data)
                else:
                    if OPT_DIRECT in selected_opts:
                        final_target_set.update([x for x in DIRECT_SALES_NAMES if x in all_sales_in_data])
                    if OPT_DIST in selected_opts:
                        final_target_set.update([x for x in DISTRIBUTOR_SALES_NAMES if x in all_sales_in_data])
                    
                    for opt in selected_opts:
                        if opt not in SPECIAL_OPTS:
                            final_target_set.add(opt)
                
                target_users = list(final_target_set)
                
            else:
                st.text_input("👤 查看對象", value=f"{real_name} (權限鎖定)", disabled=True)
                target_users = [real_name]

    if not target_users:
        st.info("👆 請在上方選擇「查看對象」以開始查詢 (支援複選或群組)。")
        return

    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        st.warning("請選擇完整的日期區間")
        return

    # 【資安強化】權限二確 (Permission Double-Check)
    if not is_manager:
        # 非管理員，查詢對象必須只有自己
        invalid_targets = [u for u in target_users if u != real_name]
        if invalid_targets:
            st.error("⛔ 安全警告：權限異常，您無法查看其他人的資料。")
            logging.warning(f"SECURITY ALERT (CRM): User {real_name} tried to access {invalid_targets}")
            return

    # 3. 資料過濾邏輯
    # 步驟 A: 日期過濾
    mask_date = (df_original["拜訪日期_dt"] >= start_date) & (df_original["拜訪日期_dt"] <= end_date)
    df_filtered = df_original.loc[mask_date].copy()

    # 步驟 B: 人員過濾
    mask_user = pd.Series([False] * len(df_filtered), index=df_filtered.index)
    if "填寫人" in df_filtered.columns:
        mask_user |= df_filtered["填寫人"].astype(str).isin(target_users)
    if "客戶所屬" in df_filtered.columns:
        mask_user |= df_filtered["客戶所屬"].astype(str).isin(target_users)
        
    df_filtered = df_filtered[mask_user]
    
    # 步驟 C: 進階屬性過濾 (修正版：加入客戶名稱與模糊搜尋)
    if not df_filtered.empty:
        with st.expander("🔍 進階篩選 (客戶、產業、關鍵字)", expanded=False):
            # 【修改 2】第一列：產業與通路 (類別型)
            r1_c1, r1_c2 = st.columns(2)
            with r1_c1:
                all_industries = sorted(list(set([x for x in df_filtered["產業別"].unique() if x])))
                sel_industry = st.multiselect("產業別", options=all_industries)
            with r1_c2:
                all_channels = sorted(list(set([x for x in df_filtered["通路商"].unique() if x])))
                sel_channel = st.multiselect("通路商", options=all_channels)

            # 【修改 3】第二列：客戶名稱、產品、模糊搜尋 (文字/搜尋型)
            # 使用 vertical_alignment="bottom" 確保輸入框對齊
            r2_c1, r2_c2, r2_c3 = st.columns(3, vertical_alignment="bottom")
            with r2_c1:
                # 動態取得當前範圍內的客戶名稱
                all_clients = sorted(list(set([x for x in df_filtered["客戶名稱"].unique() if x])))
                sel_client_name = st.multiselect("客戶名稱", options=all_clients, placeholder="選擇特定客戶...")
            with r2_c2:
                sel_product_kw = st.text_input("產品關鍵字", placeholder="例如: 士林", help="篩選「推廣產品」欄位")
            with r2_c3:
                sel_fuzzy_kw = st.text_input("模糊關鍵字搜尋", placeholder="搜尋客戶/目的/狀況/依賴...", help="同時搜尋：客戶名稱、工作內容、依賴事項、實際行程")

            # 執行篩選
            if sel_industry:
                df_filtered = df_filtered[df_filtered["產業別"].isin(sel_industry)]
            if sel_channel:
                df_filtered = df_filtered[df_filtered["通路商"].isin(sel_channel)]
            if sel_client_name:
                df_filtered = df_filtered[df_filtered["客戶名稱"].isin(sel_client_name)]
            if sel_product_kw:
                df_filtered = df_filtered[df_filtered["推廣產品"].astype(str).str.contains(sel_product_kw, case=False)]
            
            # 【修改 4】模糊搜尋邏輯
            if sel_fuzzy_kw:
                # 定義要搜尋的欄位 (確保欄位存在)
                search_cols = ["客戶名稱", "工作內容", "依賴事項", "實際行程"]
                valid_cols = [c for c in search_cols if c in df_filtered.columns]
                
                if valid_cols:
                    # 建立一個全 False 的 mask
                    mask_fuzzy = pd.Series([False] * len(df_filtered), index=df_filtered.index)
                    for col in valid_cols:
                        # 使用 OR (|) 邏輯串接各欄位的搜尋結果
                        mask_fuzzy |= df_filtered[col].astype(str).str.contains(sel_fuzzy_kw, case=False)
                    
                    df_filtered = df_filtered[mask_fuzzy]

    # 4. 顯示統計指標
    st.markdown("---")
    if df_filtered.empty:
        st.info("🔍 此區間與條件下無資料。")
        return

    total_amount = df_filtered["總金額_數值"].sum()
    total_count = len(df_filtered)
    unique_clients = df_filtered["客戶名稱"].nunique()
    avg_amount = total_amount / total_count if total_count > 0 else 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("💰 預估商機總額 (萬)", f"{total_amount:,.1f}")
    k2.metric("📝 案件數量", total_count)
    k3.metric("🏢 涉及客戶數", unique_clients)
    k4.metric("📈 平均案件金額 (萬)", f"{avg_amount:,.1f}")

    # 5. 圖表分析
    st.subheader("📊 視覺化分析")
    chart1, chart2 = st.columns(2)
    
    with chart1:
        if "產業別" in df_filtered.columns:
            industry_counts = df_filtered["產業別"].value_counts().reset_index()
            industry_counts.columns = ["產業別", "數量"]
            if not industry_counts.empty:
                fig_ind = px.pie(industry_counts, values="數量", names="產業別", title="各產業案件分佈", hole=0.4)
                st.plotly_chart(fig_ind, use_container_width=True)
            else:
                st.caption("無產業資料可顯示")
            
    with chart2:
        if "推廣產品" in df_filtered.columns:
            products_series = df_filtered["推廣產品"].astype(str).str.split(r'[、,]\s*').explode()
            products_series = products_series[products_series != ""]
            
            if not products_series.empty:
                prod_counts = products_series.value_counts().reset_index()
                prod_counts.columns = ["推廣產品", "次數"]
                fig_prod = px.bar(prod_counts, x="次數", y="推廣產品", orientation='h', title="產品推廣熱度", text="次數")
                fig_prod.update_layout(yaxis={'categoryorder':'total ascending'})
                st.plotly_chart(fig_prod, use_container_width=True)
            else:
                st.caption("無產品資料可顯示")

    # 6. 詳細資料表
    st.subheader("📝 詳細列表")
    
    display_cols = [
        "拜訪日期", "填寫人", "客戶所屬", "客戶名稱", "產業別", 
        "推廣產品", "總金額", "行動方案", "實際行程", "依賴事項", "產出日期"
    ]
    final_cols = [c for c in display_cols if c in df_filtered.columns]
    
    ui_rename_map = {"實際行程": "目前狀況"}
    display_df = df_filtered.rename(columns=ui_rename_map)
    final_cols_ui = [ui_rename_map.get(c, c) for c in final_cols]
    
    display_df = display_df.sort_values(by="拜訪日期", ascending=False)

    st.dataframe(
        display_df[final_cols_ui],
        use_container_width=True,
        hide_index=True,
        column_config={
            "總金額": st.column_config.NumberColumn("預估金額(萬)", format="%.1f"),
            "拜訪日期": st.column_config.DateColumn("拜訪日期"),
        }
    )
    
    # 7. 匯出 CSV
    csv = df_filtered.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        label="📥 下載 CRM 報表 CSV",
        data=csv,
        file_name=f"CRM商機報表_{start_date}_{end_date}.csv",
        mime="text/csv"
    )

    if st.button("🔄 重新載入最新資料"):
        st.cache_data.clear()
        st.rerun()