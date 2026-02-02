import streamlit as st
import pandas as pd
import re
import logging
from datetime import datetime
from utils import db  # 引入 Phase 1 的 DB 模組

# ==========================================
#  新增：讀取 G2 更新時間 (依需求恢復)
# ==========================================
@st.cache_data(ttl=600, show_spinner=False)
def fetch_last_update_date():
    """
    讀取 [經銷牌價表_資料庫] (Price_DB) -> 'PriceData' 頁面 -> G2 儲存格
    """
    sh, msg = db.get_db_connection("price")
    if not sh: return "未知"
    
    try:
        # 依指示讀取 PriceData 頁面
        try:
            ws = sh.worksheet("PriceData")
        except:
            # 若無 PriceData 頁面，回傳未知
            return "未知"
            
        val = ws.acell('G2').value
        return str(val) if val else "未知"
    except Exception as e:
        logging.warning(f"Failed to fetch update date: {e}")
        return "未知"

# ==========================================
#  輔助函式
# ==========================================
def write_search_log(user_email, query, result_count):
    """記錄搜尋行為至 Report_DB -> SearchLogs"""
    try:
        sh, msg = db.get_db_connection("report") # V6: Log 存於 Report_DB
        if not sh: return

        try: 
            ws = sh.worksheet("SearchLogs")
        except: 
            ws = sh.add_worksheet(title="SearchLogs", rows=1000, cols=4)
            ws.append_row(["時間", "使用者", "關鍵字", "結果數量"])
        
        ws.append_row([db.get_tw_time().strftime("%Y-%m-%d %H:%M:%S"), user_email, query, result_count])
    except Exception as e:
        logging.warning(f"Failed to write search log: {e}")

def clean_currency(val):
    """將金額字串轉換為 float"""
    if not val or pd.isna(val): return 0.0
    val_str = str(val)
    clean_str = re.sub(r'[^\d.]', '', val_str)
    try:
        return float(clean_str)
    except ValueError:
        return 0.0

@st.cache_data(ttl=3600, show_spinner="正在從雲端下載最新價格表...")
def fetch_price_data(_last_update_trigger):
    """
    讀取 Price_DB -> 經銷價(總)
    _last_update_trigger: 用於強制更新快取的 dummy 參數
    """
    sh, msg = db.get_db_connection("price")
    if not sh:
        st.error(f"資料庫連線失敗: {msg}")
        return pd.DataFrame()

    try:
        try:
            ws = sh.worksheet("經銷價(總)")
        except:
            # 相容性 fallback
            ws = sh.sheet1
            
        data = ws.get_all_records()
        if not data: return pd.DataFrame()
        
        df = pd.DataFrame(data)
        df = df.dropna(how='all')
        df = df.astype(str)
        return df
    except Exception as e:
        logging.error(f"Price data fetch failed: {e}")
        st.error(f"資料讀取錯誤: {e}")
        return pd.DataFrame()

# ==========================================
#  彈窗試算邏輯
# ==========================================
@st.dialog("🧮 業務報價試算")
def show_calculator_dialog(spec, desc, base_price):
    st.markdown(f"""
    <div style="background-color:var(--secondary-background-color); padding:10px; border-radius:8px; margin-bottom:15px; border:1px solid #ddd;">
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

    # 若切換商品，重置數值
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
        st.number_input("販售價格 ($)", min_value=0, step=100, format="%d", key="calc_price", on_change=on_price_change)
    
    final_p = st.session_state.calc_price
    
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
#  主顯示函式
# ==========================================
def show(user_info):
    user_email = user_info.get("Email", "guest")
    user_role = user_info.get("Role", "sales")
    
    # Header 區塊
    c1, c2 = st.columns([3, 1])
    with c1:
        st.title("💰 經銷牌價查詢")
    with c2:
        # 管理員專屬：強制更新按鈕
        if user_role in ["admin", "manager"]:
            if st.button("🔄 強制更新快取", help="若雲端價格有變動，點此立即更新"):
                st.cache_data.clear()
                st.rerun()

    # 【恢復】顯示更新日期 (來自 PriceData G2)
    update_date = fetch_last_update_date()
    st.caption(f"資料更新日期：{update_date}")

    # CSS 優化 (卡片樣式)
    st.markdown("""
    <style>
    .search-card {
        background-color: var(--secondary-background-color);
        border: 1px solid rgba(128,128,128,0.2);
        border-radius: 12px;
        padding: 16px;
        margin-bottom: 12px;
        transition: box-shadow 0.2s;
    }
    .search-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.08); }
    .card-title { font-weight: bold; font-size: 1.1rem; color: #333; margin-bottom: 4px; }
    .card-desc { font-size: 0.9rem; color: #666; margin-bottom: 8px; line-height: 1.4; }
    .card-price { font-weight: bold; font-size: 1.2rem; color: #0071e3; }
    </style>
    """, unsafe_allow_html=True)

    # 搜尋區塊
    with st.container(border=True):
        col1, col2 = st.columns([4, 1])
        with col1:
            query = st.text_input("🔍 關鍵字搜尋", placeholder="例: SDE, 55KW...", max_chars=50, key="price_search_box", label_visibility="collapsed")
        with col2:
            search_btn = st.button("搜尋", use_container_width=True, type="primary")

    if search_btn or query:
        query = str(query).strip()
        # 簡單過濾特殊字元
        query = re.sub(r'[^\w\s\-\.\(\)\/]', '', query)

        if not query:
            st.warning("⚠️ 請輸入關鍵字")
            return

        # 讀取資料 (傳入 dummy trigger 以便管理員強制刷新)
        df = fetch_price_data(st.session_state.get("price_cache_trigger", 0))
        
        if df.empty:
            st.error("無法讀取價格表")
            return

        try:
            # 模糊搜尋
            mask = df.apply(lambda row: row.astype(str).str.contains(query, case=False, regex=False).any(), axis=1)
            result_df = df[mask]
            
            # 寫入 Log
            write_search_log(user_email, query, len(result_df))
        except Exception as e:
            st.error("搜尋發生錯誤")
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
                # 組裝顯示資訊
                name_parts = [str(row.get(col, "")).strip() for col in ["產品名稱", "規格", "Item", "品名", "Name"] if str(row.get(col, "")).strip()]
                product_name = " | ".join(name_parts) if name_parts else "未知名稱"
                
                desc_parts = [str(row.get(col, "")).strip() for col in ["型號", "備註", "說明", "Model", "Description"] if str(row.get(col, "")).strip()]
                product_desc = " | ".join(desc_parts)

                # 價格判斷邏輯 (相容 V1)
                dist_price_cols = [c for c in df.columns if '經銷' in c and '價' in c]
                if not dist_price_cols: dist_price_cols = [c for c in df.columns if '經銷' in c]
                
                base_price = 0
                price_display = "請洽詢"
                
                if dist_price_cols:
                    price_col = dist_price_cols[0]
                    raw_price = row.get(price_col, 0)
                    base_price = clean_currency(raw_price)
                    if base_price > 0:
                        price_display = f"${base_price:,.0f}"
                    else:
                        price_display = str(raw_price)

                # 渲染卡片
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
                                # 點擊試算時 Log 產品名稱
                                write_search_log(user_email, product_name, "試算選取")
                                show_calculator_dialog(product_name, product_desc, base_price)
                        else:
                            st.caption("無法試算")
                    st.divider()