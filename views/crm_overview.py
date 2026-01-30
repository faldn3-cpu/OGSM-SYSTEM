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

# === 設定: 人員群組 (建議與 report_overview 保持一致或統一管理) ===
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
            
        data = ws.get_all_records()
        if not data:
            return pd.DataFrame()
            
        df = pd.DataFrame(data)
        
        # 【修正】處理欄位名稱不一致的問題
        # 自動搜尋包含 "客戶所屬" 的欄位，並將其標準化命名為 "客戶所屬"
        rename_map = {}
        for col in df.columns:
            if "客戶所屬" in str(col):
                rename_map[col] = "客戶所屬"
        
        if rename_map:
            df.rename(columns=rename_map, inplace=True)
        
        # 欄位名稱對照 (確保與 daily_report.py 寫入的一致)
        # 預期欄位: 時間戳記, 填寫人, 客戶名稱, 通路商, 競爭通路, 行動方案, 
        #           客戶性質, 流失取回, 產業別, 拜訪日期, 推廣產品, 工作內容, 
        #           產出日期, 總金額, 依賴事項, 實際行程, 競爭品牌, 客戶所屬
        
        # 1. 處理日期欄位
        if "拜訪日期" in df.columns:
            df["拜訪日期_dt"] = pd.to_datetime(df["拜訪日期"], errors='coerce').dt.date
        else:
            df["拜訪日期_dt"] = None
            
        # 2. 處理金額欄位
        if "總金額" in df.columns:
            df["總金額_數值"] = df["總金額"].apply(clean_currency)
        else:
            df["總金額_數值"] = 0.0

        # 3. 處理空值
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
        st.info("尚無 CRM 資料或無法讀取。")
        if st.button("🔄 重試"):
            st.cache_data.clear()
            st.rerun()
        return

    # 確保關鍵欄位存在 (防止其他欄位也缺漏)
    required_cols = ["填寫人", "客戶所屬", "產業別", "通路商", "推廣產品", "客戶名稱", "總金額"]
    missing_cols = [c for c in required_cols if c not in df_original.columns]
    if missing_cols:
        st.error(f"❌ 資料表缺少關鍵欄位，請檢查 Google Sheet 標題: {', '.join(missing_cols)}")
        st.dataframe(df_original.head(2)) # 顯示前兩筆讓使用者除錯
        return

    # 2. 側邊/上方篩選器
    with st.container(border=True):
        col1, col2 = st.columns([1, 2])
        
        # --- 日期篩選 ---
        with col1:
            today = date.today()
            # 預設顯示本月
            start_default = today.replace(day=1)
            end_default = today
            
            date_range = st.date_input(
                "📅 選擇拜訪日期區間", 
                (start_default, end_default),
                key="crm_date_range"
            )

        # --- 人員篩選 (權限控管) ---
        with col2:
            target_users = []
            
            # 取得所有相關人員清單 (排除空值)
            all_sales_in_data = sorted(list(set(
                df_original["填寫人"].dropna().unique().tolist() + 
                df_original["客戶所屬"].dropna().unique().tolist()
            )))
            all_sales_in_data = [x for x in all_sales_in_data if str(x).strip() != ""]

            if is_manager:
                # 管理員模式：可選多人
                menu_options = SPECIAL_OPTS + all_sales_in_data
                
                selected_opts = st.multiselect(
                    "👥 選擇業務員 (篩選 填寫人 或 客戶所屬)",
                    options=menu_options,
                    default=OPT_ALL
                )
                
                # 解析選項
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
                # 業務員模式：鎖定自己
                st.text_input("👤 查看對象", value=f"{real_name} (權限鎖定)", disabled=True)
                target_users = [real_name]

    # 3. 執行資料篩選
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        st.warning("請選擇完整的日期區間")
        return

    # 步驟 A: 日期過濾
    mask_date = (df_original["拜訪日期_dt"] >= start_date) & (df_original["拜訪日期_dt"] <= end_date)
    df_filtered = df_original.loc[mask_date].copy()

    # 步驟 B: 人員過濾 (邏輯：填寫人 IN target_users OR 客戶所屬 IN target_users)
    if not target_users:
        st.warning("請選擇至少一位業務員")
        return

    mask_user = (
        df_filtered["填寫人"].isin(target_users) | 
        df_filtered["客戶所屬"].isin(target_users)
    )
    df_filtered = df_filtered[mask_user]
    
    # 步驟 C: 進階屬性過濾 (產業 & 產品)
    if not df_filtered.empty:
        with st.expander("🔍 進階篩選 (產業、產品、通路)", expanded=False):
            c1, c2, c3 = st.columns(3)
            with c1:
                all_industries = sorted(list(set([x for x in df_filtered["產業別"].unique() if x])))
                sel_industry = st.multiselect("產業別", options=all_industries)
            with c2:
                # 產品可能包含多選字串 (e.g. "士林品, 三菱品")，這裡做簡單篩選
                sel_product_kw = st.text_input("產品關鍵字 (例如: 士林)", help="篩選推廣產品欄位")
            with c3:
                all_channels = sorted(list(set([x for x in df_filtered["通路商"].unique() if x])))
                sel_channel = st.multiselect("通路商", options=all_channels)
            
            if sel_industry:
                df_filtered = df_filtered[df_filtered["產業別"].isin(sel_industry)]
            if sel_product_kw:
                df_filtered = df_filtered[df_filtered["推廣產品"].astype(str).str.contains(sel_product_kw, case=False)]
            if sel_channel:
                df_filtered = df_filtered[df_filtered["通路商"].isin(sel_channel)]

    # 4. 顯示統計指標 (KPI Cards)
    st.markdown("---")
    if df_filtered.empty:
        st.info("🔍 此區間與條件下無資料。")
        return

    # 計算指標
    total_amount = df_filtered["總金額_數值"].sum()
    total_count = len(df_filtered)
    unique_clients = df_filtered["客戶名稱"].nunique()
    avg_amount = total_amount / total_count if total_count > 0 else 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("💰 預估商機總額 (萬)", f"{total_amount:,.1f}")
    k2.metric("📝 案件數量", total_count)
    k3.metric("🏢 涉及客戶數", unique_clients)
    k4.metric("📈 平均案件金額 (萬)", f"{avg_amount:,.1f}")

    # 5. 圖表分析 (使用 Plotly)
    st.subheader("📊 視覺化分析")
    
    chart1, chart2 = st.columns(2)
    
    with chart1:
        # 產業佔比 (圓餅圖)
        if "產業別" in df_filtered.columns:
            industry_counts = df_filtered["產業別"].value_counts().reset_index()
            industry_counts.columns = ["產業別", "數量"]
            if not industry_counts.empty:
                fig_ind = px.pie(industry_counts, values="數量", names="產業別", title="各產業案件分佈", hole=0.4)
                st.plotly_chart(fig_ind, use_container_width=True)
            else:
                st.caption("無產業資料可顯示")
            
    with chart2:
        # 產品推廣 (長條圖)
        if "推廣產品" in df_filtered.columns:
            # 將 "士林品, 三菱品" 拆開成多列
            products_series = df_filtered["推廣產品"].astype(str).str.split(r'[、,]\s*').explode()
            # 移除空字串
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
    
    # 選擇要顯示的欄位 (隱藏系統欄位)
    display_cols = [
        "拜訪日期", "填寫人", "客戶所屬", "客戶名稱", "產業別", 
        "推廣產品", "總金額", "行動方案", "目前狀況", "產出日期"
    ]
    # 確保欄位存在
    final_cols = [c for c in display_cols if c in df_filtered.columns]
    
    # 如果有「實際行程」，在顯示時重新命名為「目前狀況」比較直觀，若無則跳過
    rename_map = {"實際行程": "目前狀況"}
    display_df = df_filtered.rename(columns=rename_map)
    # 更新 final_cols 中的名稱
    final_cols = [rename_map.get(c, c) for c in final_cols]
    
    # 排序
    display_df = display_df.sort_values(by="拜訪日期", ascending=False)

    st.dataframe(
        display_df[final_cols],
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

    # 清除快取按鈕
    if st.button("🔄 重新載入最新資料"):
        st.cache_data.clear()
        st.rerun()