import streamlit as st
import pandas as pd
import gspread
import re
import logging
from datetime import datetime, timezone, timedelta

# ==========================================
#  1. 輔助函式與快取
# ==========================================
def get_tw_time():
    tw_tz = timezone(timedelta(hours=8))
    return datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M:%S")

def write_search_log(client, db_name, user_email, query, result_count):
    """記錄搜尋行為 (BI 商業分析用)"""
    try:
        sh = client.open(db_name)
        try: 
            ws = sh.worksheet("SearchLogs")
        except: 
            ws = sh.add_worksheet(title="SearchLogs", rows=1000, cols=4)
            ws.append_row(["時間", "使用者", "關鍵字", "結果數量"])
        
        ws.append_row([get_tw_time(), user_email, query, result_count])
    except Exception as e:
        logging.warning(f"Failed to write search log: {e}")

@st.cache_data(ttl=600, show_spinner=False)
def fetch_last_update_date(db_name, _client):
    """讀取 Users 頁面的 D1 儲存格作為更新日期"""
    try:
        sh = _client.open(db_name)
        ws = sh.worksheet("Users")
        val = ws.acell('D1').value
        return str(val) if val else "未知"
    except Exception as e:
        logging.warning(f"Failed to fetch update date: {e}")
        return "未知"

@st.cache_data(ttl=3600, show_spinner="正在從雲端下載最新價格表...")
def fetch_price_data(db_name, _client):
    try:
        sh = _client.open(db_name)
        try:
            ws = sh.worksheet("經銷價(總)")
        except gspread.WorksheetNotFound:
            ws = sh.sheet1
            
        data = ws.get_all_records()
        if not data: return pd.DataFrame()
        
        df = pd.DataFrame(data)
        df = df.dropna(how='all')
        df = df.astype(str)
        return df
    except Exception as e:
        st.error(f"資料讀取錯誤: {e}")
        logging.error(f"Price data fetch failed: {e}")
        return pd.DataFrame()

def clean_currency(val):
    """將含有 $ , 或文字的價格字串轉為 float"""
    if not val or pd.isna(val): return 0.0
    val_str = str(val)
    clean_str = re.sub(r'[^\d.]', '', val_str)
    try:
        return float(clean_str)
    except ValueError:
        return 0.0

# ==========================================
#  2. 輸入驗證
# ==========================================
MAX_SEARCH_LENGTH = 50

def sanitize_search_query(query):
    if not query: return ""
    query = str(query).strip()
    if len(query) > MAX_SEARCH_LENGTH:
        query = query[:MAX_SEARCH_LENGTH]
    query = re.sub(r'[^\w\s\-\.\(\)\/]', '', query)
    return query

# ==========================================
#  3. 彈窗試算邏輯
# ==========================================
@st.dialog("🧮 業務報價試算")
def show_calculator_dialog(spec, desc, base_price):
    # 【修正 1】將 "經銷底價:" 修改為 "經銷價："
    st.markdown(f"""
    <div style="background-color:#f8f9fa; padding:10px; border-radius:8px; margin-bottom:15px;">
        <div style="font-weight:bold; font-size:1.1em; color:#333;">{spec}</div>
        <div style="font-size:0.9em; color:#666;">{desc}</div>
        <hr style="margin:8px 0;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <span>經銷價：</span>
            <span style="color:#d9534f; font-weight:bold; font-size:1.1em;">${base_price:,.0f}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if 'calc_discount' not in st.session_state: st.session_state.calc_discount = 100.00
    if 'calc_price' not in st.session_state: st.session_state.calc_price = int(base_price)
    if 'current_base_price' not in st.session_state: st.session_state.current_base_price = base_price

    if st.session_state.current_base_price != base_price:
        st.session_state.current_base_price = base_price
        st.session_state.calc_discount = 100.00
        st.session_state.calc_price = int(base_price)

    def on_discount_change():
        if st.session_state.current_base_price > 0:
            new_price = st.session_state.current_base_price * (st.session_state.calc_discount / 100)
            st.session_state.calc_price = int(round(new_price))

    def on_price_change():
        if st.session_state.current_base_price > 0:
            new_discount = (st.session_state.calc_price / st.session_state.current_base_price) * 100
            st.session_state.calc_discount = round(new_discount, 2)
    
    col1, col2 = st.columns(2)
    with col1:
        st.number_input("販售折數 (%)", min_value=0.0, max_value=300.0, step=0.5, format="%.2f", key="calc_discount", on_change=on_discount_change)
    with col2:
        # 【說明】Streamlit 的 st.number_input 不支援輸入時顯示千分位 (%,d)，維持 %d (整數) 是最穩定的做法
        st.number_input("販售價格 ($)", min_value=0, step=100, format="%d", key="calc_price", on_change=on_price_change)
    
    final_p = st.session_state.calc_price
    
    # 這裡的最終金額顯示已經包含千分位 (final_p:,.0f)
    st.markdown(f"""
    <div style="
        margin-top: 15px; padding: 15px;
        background: linear-gradient(135deg, #0071e3 0%, #00c6ff 100%);
        color: white; border-radius: 12px; text-align: center;
        box-shadow: 0 4px 15px rgba(0,113,227, 0.3);">
        <div style="font-size:0.9em; opacity:0.9;">最終報價金額</div>
        <div style="font-size:2em; font-weight:bold;">${final_p:,.0f}</div>
    </div>
    """, unsafe_allow_html=True)

# ==========================================
#  4. 主頁面顯示
# ==========================================
def show(client, db_name, user_email, real_name, is_manager):
    st.title("💰 經銷牌價查詢")
    
    update_date = fetch_last_update_date(db_name, client)
    st.caption(f"資料更新日期：{update_date}")
    
    # CSS 優化
    st.markdown("""
    <style>
    .search-card {
        background-color: white;
        border: 1px solid #e0e0e0;
        border-radius: 12px;
        padding: 16px;
        margin-bottom: 12px;
        transition: box-shadow 0.2s;
    }
    .search-card:hover {
        box-shadow: 0 4px 12px rgba(0,0,0,0.08);
    }
    .card-title { font-weight: bold; font-size: 1.1rem; color: #333; margin-bottom: 4px; }
    .card-desc { font-size: 0.9rem; color: #666; margin-bottom: 8px; line-height: 1.4; }
    .card-price { font-weight: bold; font-size: 1.2rem; color: #0071e3; }
    
    @media (prefers-color-scheme: dark) {
        .search-card { background-color: #262730; border-color: #444; }
        .card-title { color: #fff; }
        .card-desc { color: #bbb; }
        .card-price { color: #4da6ff; }
    }
    </style>
    """, unsafe_allow_html=True)

    # === 搜尋區塊 ===
    with st.container(border=True):
        col1, col2 = st.columns([4, 1])
        with col1:
            query = st.text_input("🔍 關鍵字搜尋", placeholder="例: SDE, 55KW, 變頻器...", max_chars=MAX_SEARCH_LENGTH, key="price_search_box", label_visibility="collapsed")
        with col2:
            search_btn = st.button("搜尋", use_container_width=True, type="primary")

    if search_btn or query:
        query = sanitize_search_query(query)
        
        if not query:
            st.warning("⚠️ 請輸入關鍵字")
            return

        df = fetch_price_data(db_name, client)
        if df.empty:
            st.error("無法讀取價格表，請聯繫管理員。")
            return

        try:
            mask = df.apply(lambda row: row.astype(str).str.contains(query, case=False, regex=False).any(), axis=1)
            result_df = df[mask]
            write_search_log(client, db_name, user_email, query, len(result_df))
        except Exception as e:
            st.error("搜尋發生錯誤")
            logging.error(f"Search error: {e}")
            return

        st.markdown(f"**搜尋結果：** `{query}` (共 {len(result_df)} 筆)")
        
        if result_df.empty:
            st.info("找不到符合的資料，請嘗試其他關鍵字。")
        else:
            MAX_RESULTS = 50
            if len(result_df) > MAX_RESULTS:
                st.caption(f"⚠️ 資料過多，僅顯示前 {MAX_RESULTS} 筆")
                result_df = result_df.head(MAX_RESULTS)
            
            for idx, row in result_df.iterrows():
                # 1. 產品名稱
                name_parts = []
                for col in ["產品名稱", "規格", "Item", "品名", "Name"]:
                    val = str(row.get(col, "")).strip()
                    if val: name_parts.append(val)
                product_name = " | ".join(name_parts) if name_parts else str(row.values[0])
                
                # 2. 產品描述
                desc_parts = []
                for col in ["型號", "備註", "說明", "Model", "Description"]:
                    val = str(row.get(col, "")).strip()
                    if val: desc_parts.append(val)
                product_desc = " | ".join(desc_parts)

                # 3. 嚴格經銷價判斷逻辑
                price_col = None
                
                # 策略 A: 找明確包含 "經銷" 且包含 "價" 的欄位
                dist_price_cols = [c for c in df.columns if '經銷' in c and '價' in c]
                
                # 策略 B: 找包含 "經銷" 的欄位
                if not dist_price_cols:
                    dist_price_cols = [c for c in df.columns if '經銷' in c]

                if dist_price_cols:
                    price_col = dist_price_cols[0]
                else:
                    price_col = None 

                base_price = 0
                price_display = "請洽詢"
                
                if price_col and price_col in row:
                    raw_price = row[price_col]
                    base_price = clean_currency(raw_price)
                    if base_price > 0:
                        price_display = f"${base_price:,.0f}" 
                    else:
                        price_display = str(raw_price)
                elif not price_col:
                    price_display = "⚠️ 無經銷價"

                # 4. 渲染卡片
                with st.container():
                    c1, c2 = st.columns([3, 1])
                    with c1:
                        st.markdown(f"""
                        <div class="card-title">{product_name}</div>
                        <div class="card-desc">{product_desc}</div>
                        <div class="card-price">{price_display}</div>
                        """, unsafe_allow_html=True)

                    with c2:
                        st.write("")
                        if base_price > 0:
                            if st.button("試算", key=f"btn_{idx}", use_container_width=True):
                                show_calculator_dialog(product_name, product_desc, base_price)
                        else:
                            st.caption("無法試算")
                    
                    st.divider()

    else:
        st.info("👈 請輸入產品型號或規格開始查詢")
        with st.expander("ℹ️ 搜尋小撇步"):
            st.markdown("""
            - 支援模糊搜尋，例如輸入 `SDE` 可找到相關系列。
            - 搜尋完畢後，點擊右側 **「試算」** 按鈕可進行折扣計算。
            """)