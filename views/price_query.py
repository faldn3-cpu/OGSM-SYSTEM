import streamlit as st
import pandas as pd
import gspread
import re

# === 1. 輔助函式與快取 ===
@st.cache_data(ttl=3600, show_spinner="正在從雲端下載最新價格表...")
def fetch_price_data(db_name, _client):
    try:
        sh = _client.open(db_name)
        try:
            ws = sh.worksheet("經銷價(總)")
        except gspread.WorksheetNotFound:
            ws = sh.sheet1
            
        data = ws.get_all_records()
        if not data:
            return pd.DataFrame()
        
        df = pd.DataFrame(data)
        df = df.dropna(how='all')
        df = df.astype(str)
        return df
    except Exception as e:
        st.error(f"資料讀取錯誤: {e}")
        return pd.DataFrame()

def clean_currency(val):
    """
    將含有 $ , 或文字的價格字串轉為 float
    """
    if not val or pd.isna(val): return 0.0
    val_str = str(val)
    # 只保留數字和小數點
    clean_str = re.sub(r'[^\d.]', '', val_str)
    try:
        return float(clean_str)
    except ValueError:
        return 0.0

# === 2. 彈窗試算邏輯 (數值顯示優化) ===
@st.dialog("🧮 業務報價試算")
def show_calculator_dialog(spec, desc, base_price):
    # [顯示優化] 這裡使用 f"{value:,.0f}" 加上千分位符號
    st.markdown(f'<div class="dialog-text"><b>產品規格：</b>{spec}</div>', unsafe_allow_html=True)
    if desc:
        st.markdown(f'<div class="dialog-text"><b>產品說明：</b>{desc}</div>', unsafe_allow_html=True)
    
    # 顯示帶有千分位的底價
    st.markdown(f'<div class="dialog-text"><b>經銷底價：</b><span style="color:#d9534f">${base_price:,.0f}</span></div>', unsafe_allow_html=True)
    st.markdown("---")

    # 初始化計算機 Session
    if 'calc_discount' not in st.session_state: st.session_state.calc_discount = 100.00
    if 'calc_price' not in st.session_state: st.session_state.calc_price = int(base_price)
    if 'current_base_price' not in st.session_state: st.session_state.current_base_price = base_price

    # 若切換不同產品，重置數值
    if st.session_state.current_base_price != base_price:
        st.session_state.current_base_price = base_price
        st.session_state.calc_discount = 100.00
        st.session_state.calc_price = int(base_price)

    # Callback: 當折數改變 -> 重算價格
    def on_discount_change():
        if st.session_state.current_base_price > 0:
            new_price = st.session_state.current_base_price * (st.session_state.calc_discount / 100)
            st.session_state.calc_price = int(round(new_price))

    # Callback: 當價格改變 -> 重算折數
    def on_price_change():
        if st.session_state.current_base_price > 0:
            new_discount = (st.session_state.calc_price / st.session_state.current_base_price) * 100
            st.session_state.calc_discount = round(new_discount, 2)
    
    col1, col2 = st.columns(2)
    with col1:
        st.number_input(
            "販售折數 (%)", 
            min_value=0.0, max_value=300.0, step=0.5, format="%.2f", 
            key="calc_discount", 
            on_change=on_discount_change
        )
    with col2:
        # [顯示優化] 雖然輸入框內部很難加千分位，但我們可以標示單位
        st.number_input(
            "販售價格 ($)", 
            min_value=0, step=100, format="%d", 
            key="calc_price", 
            on_change=on_price_change
        )
    
    final_p = st.session_state.calc_price
    # [顯示優化] 結果顯示加上千分位
    st.markdown(f"<div class='dialog-price-highlight'>報價金額：${final_p:,.0f}</div>", unsafe_allow_html=True)

# === 3. 主頁面顯示 ===
def show(client, db_name, user_email, real_name, is_manager):
    st.title("💰 經銷牌價查詢")
    
    st.markdown("""
    <style>
    div.stButton > button:first-child {
        background-color: #0099ff;
        color: white;
        font-weight: bold;
    }
    /* 列表樣式優化 */
    .product-row {
        padding: 10px 0;
        border-bottom: 1px solid #eee;
    }
    .product-name { font-weight: bold; font-size: 1.05rem; color: #333; }
    .product-desc { font-size: 0.9rem; color: #666; }
    .product-price { font-weight: bold; color: #0071e3; font-size: 1.05rem; }
    
    /* Dialog 樣式優化 */
    .dialog-text { font-size: 1.1rem; color: #333; margin-bottom: 8px; }
    .dialog-price-highlight {
        font-size: 1.8rem; font-weight: 700; color: #0071e3;
        text-align: center; margin-top: 20px; padding: 20px;
        background-color: #f5f5f7; border-radius: 12px;
        border: 2px solid #e1e1e1;
    }
    </style>
    """, unsafe_allow_html=True)

    # === 搜尋區塊 ===
    col1, col2 = st.columns([3, 1])
    with col1:
        query = st.text_input(
            "🔍 請輸入關鍵字", 
            placeholder="輸入產品型號或關鍵字進行搜尋 (例如: SDE, SA3, 55KW)", 
            key="price_search_box"
        )
    with col2:
        st.write("") 
        st.write("")
        search_btn = st.button("搜尋", use_container_width=True)

    if search_btn or query:
        if not query:
            st.warning("⚠️ 請輸入關鍵字後再搜尋")
            return

        df = fetch_price_data(db_name, client)
        if df.empty:
            st.error("無法讀取價格表資料，請確認資料庫連線。")
            return

        # 執行搜尋
        mask = df.apply(lambda row: row.astype(str).str.contains(query, case=False).any(), axis=1)
        result_df = df[mask]

        st.markdown("---")
        
        if result_df.empty:
            st.info("找不到符合的資料，請嘗試其他關鍵字。")
        else:
            st.success(f"找到 {len(result_df)} 筆資料")
            
            # === [改版] 條列式顯示 + 試算按鈕 ===
            # 定義表頭
            h1, h2, h3, h4 = st.columns([3, 2, 2, 1.5])
            h1.markdown("**品名 / 規格**")
            h2.markdown("**型號 / 備註**")
            h3.markdown("**經銷牌價**")
            h4.markdown("**操作**")
            st.markdown("---")

            # 遍歷搜尋結果
            for idx, row in result_df.iterrows():
                # 1. 智慧判斷欄位 (Name)
                name_parts = []
                for col in ["產品名稱", "規格", "Item", "品名"]:
                    if col in row.index and str(row[col]).strip():
                        name_parts.append(str(row[col]))
                product_name = " | ".join(name_parts) if name_parts else str(row.values[0])
                
                # 2. 智慧判斷欄位 (Desc)
                desc_parts = []
                for col in ["型號", "備註", "說明"]:
                    if col in row.index and str(row[col]).strip():
                        desc_parts.append(str(row[col]))
                product_desc = " | ".join(desc_parts)

                # 3. 智慧判斷欄位 (Price)
                price_col = next((c for c in df.columns if '價' in c or 'Price' in c or 'MSRP' in c), None)
                base_price = 0
                price_display = "請洽詢"
                
                if price_col:
                    raw_price = row[price_col]
                    base_price = clean_currency(raw_price)
                    if base_price > 0:
                        # [顯示優化] 列表中的價格加上千分位
                        price_display = f"${base_price:,.0f}" 
                    else:
                        price_display = str(raw_price)

                # 4. 顯示該行資料
                c1, c2, c3, c4 = st.columns([3, 2, 2, 1.5])
                
                with c1:
                    st.write(product_name)
                with c2:
                    st.write(product_desc)
                with c3:
                    # 使用顏色標示價格
                    if base_price > 0:
                        st.markdown(f"<span style='color:#0071e3; font-weight:bold;'>{price_display}</span>", unsafe_allow_html=True)
                    else:
                        st.write(price_display)
                with c4:
                    # 只有價格有效時才顯示試算按鈕
                    if base_price > 0:
                        if st.button("試算 🧮", key=f"btn_calc_{idx}", use_container_width=True):
                            show_calculator_dialog(product_name, product_desc, base_price)
                    else:
                        st.write("-")
                
                st.markdown("<div style='border-bottom: 1px solid #f0f0f0; margin-bottom: 10px;'></div>", unsafe_allow_html=True)

    else:
        st.info("👈 請在上方輸入關鍵字開始查詢")
        with st.expander("ℹ️ 搜尋小撇步"):
            st.markdown("""
            - 輸入 **品名或規格** (如 FX5U、主機48點、光纖)
            - 搜尋完畢後，點擊右側 **「試算 🧮」** 按鈕即可進行報價試算。
            """)