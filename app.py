import streamlit as st
import yfinance as yf
import pandas as pd
import datetime

# ==========================================
# 核心指標計算函式
# ==========================================
def calculate_williams_r(high, low, close, period):
    highest_high = high.rolling(window=period).max()
    lowest_low = low.rolling(window=period).min()
    wr = ((highest_high - close) / (highest_high - lowest_low)) * -100
    return wr

def calculate_rsi(close, period):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_kd(high, low, close, rsv_period, smooth_period=3):
    highest_high = high.rolling(window=rsv_period).max()
    lowest_low = low.rolling(window=rsv_period).min()
    rsv = ((close - lowest_low) / (highest_high - lowest_low)) * 100
    k = rsv.ewm(com=smooth_period - 1, adjust=False).mean()
    d = k.ewm(com=smooth_period - 1, adjust=False).mean()
    return k, d

def yfinance_download_safe(symbol, start, end):
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start, end=end)
    return df

# ==========================================
# 網頁介面與邏輯 (Streamlit)
# ==========================================
st.set_page_config(page_title="多指標量化掃描工具", layout="wide", page_icon="📈")

st.title("📈 多指標(W%R/RSI/KD)量化掃描工具 by 峰臣")
st.markdown("將您的 CSV 股票清單上傳，系統將自動套用您的策略進行雲端運算。")

# --- 側邊欄：檔案上傳與指標設定 ---
with st.sidebar:
    st.header("📂 1. 資料來源")
    uploaded_files = st.file_uploader("請上傳 CSV 檔案 (可多選)", accept_multiple_files=True, type=['csv'])
    
    st.header("⚙️ 2. 條件組合")
    col1, col2 = st.columns(2)
    with col1:
        ind1 = st.selectbox("指標一", ["無選擇", "威廉", "RSI", "KD"])
    with col2:
        ind2 = st.selectbox("指標二", ["無選擇", "威廉", "RSI", "KD"])
        
    all_three = st.checkbox("✅ 三者同時符合 (勾選即忽略上方組合)", value=False)
    
    st.header("🎯 3. 掃描模式")
    scan_mode = st.radio("請選擇策略方向", ["超賣 (低於門檻)", "超買 (高於門檻)"])
    is_oversold = (scan_mode == "超賣 (低於門檻)")

# --- 主畫面：參數設定區 ---
st.subheader("📊 4. 參數設定")

if is_oversold:
    st.info("目前為 **模式 A (超賣)**：尋找指標 **小於(<)** 設定門檻的標的。")
    # 超賣預設值
    def_wr_s_t, def_wr_l_t = -90.0, -80.0
    def_rsi_s_t, def_rsi_l_t = 25.0, 50.0
    def_kd_s_k, def_kd_s_d, def_kd_l_k, def_kd_l_d = 20.0, 20.0, 30.0, 30.0
else:
    st.error("目前為 **模式 B (超買)**：尋找指標 **大於(>)** 設定門檻的標的。")
    # 超買預設值
    def_wr_s_t, def_wr_l_t = -10.0, -20.0
    def_rsi_s_t, def_rsi_l_t = 80.0, 50.0
    def_kd_s_k, def_kd_s_d, def_kd_l_k, def_kd_l_d = 80.0, 80.0, 70.0, 70.0

col_wr, col_rsi, col_kd = st.columns(3)

with col_wr:
    with st.expander("威廉指標 (W%R)", expanded=True):
        wr_s_d = st.number_input("短天期", value=7, step=1)
        wr_s_t = st.number_input("短天期門檻", value=def_wr_s_t, step=1.0)
        st.divider()
        wr_l_d = st.number_input("長天期", value=30, step=1)
        wr_l_t = st.number_input("長天期門檻", value=def_wr_l_t, step=1.0)

with col_rsi:
    with st.expander("相對強弱指標 (RSI)", expanded=True):
        rsi_s_d = st.number_input("RSI 短天期", value=4, step=1)
        rsi_s_t = st.number_input("RSI 短天期門檻", value=def_rsi_s_t, step=1.0)
        st.divider()
        rsi_l_d = st.number_input("RSI 長天期", value=15 if is_oversold else 30, step=1)
        rsi_l_t = st.number_input("RSI 長天期門檻", value=def_rsi_l_t, step=1.0)

with col_kd:
    with st.expander("隨機指標 (KD)", expanded=True):
        kd_s_d = st.number_input("KD 短天期", value=9, step=1)
        k_col1, k_col2 = st.columns(2)
        kd_s_k = k_col1.number_input("短K門檻", value=def_kd_s_k, step=1.0)
        kd_s_d_th = k_col2.number_input("短D門檻", value=def_kd_s_d, step=1.0)
        st.divider()
        kd_l_d = st.number_input("KD 長天期", value=30, step=1)
        l_col1, l_col2 = st.columns(2)
        kd_l_k = l_col1.number_input("長K門檻", value=def_kd_l_k, step=1.0)
        kd_l_d_th = l_col2.number_input("長D門檻", value=def_kd_l_d, step=1.0)

st.divider()

# ==========================================
# 掃描執行區
# ==========================================
if st.button("🚀 開始掃描", use_container_width=True, type="primary"):
    if not uploaded_files:
        st.warning("⚠️ 請先在左側欄位上傳至少一個 CSV 檔案！")
    else:
        # 解析上傳的 CSV 檔案
        parsed_data = []
        for file in uploaded_files:
            try:
                try:
                    df = pd.read_csv(file, encoding='utf-8-sig', dtype=str)
                except UnicodeDecodeError:
                    file.seek(0)
                    df = pd.read_csv(file, encoding='big5', dtype=str)
                
                df.columns = [str(col).strip() for col in df.columns]
                
                if '代號' in df.columns:
                    for index, row in df.iterrows():
                        symbol = str(row['代號']).strip()
                        if symbol.endswith('.0'): symbol = symbol[:-2]
                        if not symbol or symbol.lower() == 'nan': continue
                        
                        # ETF 防呆：自動補零
                        if symbol.isdigit() and len(symbol) < 4:
                            symbol = "00" + symbol
                            
                        market = str(row.get('市場', '')).strip()
                        name = str(row.get('名稱', '')).strip()
                        if name.lower() == 'nan': name = ""
                        
                        yf_symbol = f"{symbol}.TWO" if '櫃' in market else f"{symbol}.TW"
                        
                        parsed_data.append({
                            "clean_symbol": symbol,
                            "yf_symbol": yf_symbol,
                            "name": name
                        })
            except Exception as e:
                st.error(f"讀取檔案 {file.name} 失敗：{e}")

        # 去重複標的
        unique_targets = {d['yf_symbol']: d for d in parsed_data}
        targets_list = list(unique_targets.values())
        
        req_inds = set()
        if ind1 != "無選擇": req_inds.add(ind1)
        if ind2 != "無選擇": req_inds.add(ind2)
        
        if not all_three and len(req_inds) == 0:
            st.error("⚠️ 請至少在左側下拉選單選擇一個分析指標，或是勾選「三者同時符合」。")
        elif not targets_list:
             st.warning("⚠️ 讀取不到任何有效的股票代號，請檢查 CSV 格式。")
        else:
            end_date = datetime.date.today()
            max_days = max(wr_s_d, wr_l_d, rsi_s_d, rsi_l_d, kd_s_d, kd_l_d)
            start_date = end_date - datetime.timedelta(days=max_days * 4 + 15)
            
            results = []
            progress_bar = st.progress(0)
            status_text = st.empty()
            total = len(targets_list)
            
            for i, stock_info in enumerate(targets_list):
                symbol_yf = stock_info["yf_symbol"]
                symbol_clean = stock_info["clean_symbol"]
                stock_name = stock_info["name"]
                
                status_text.text(f"正在掃描 ({i+1}/{total}): {symbol_clean} {stock_name}")
                progress_bar.progress((i + 1) / total)
                
                try:
                    df = yfinance_download_safe(symbol_yf, start=start_date, end=end_date)
                    
                    # 智慧重試：長度4碼且純數字，找不到資料就當作被砍掉的ETF補00重試
                    if (df.empty or len(df) < max_days) and len(symbol_clean) == 4 and symbol_clean.isdigit():
                        alt_clean = "00" + symbol_clean
                        alt_yf = f"{alt_clean}.TWO" if ".TWO" in symbol_yf else f"{alt_clean}.TW"
                        df_alt = yfinance_download_safe(alt_yf, start=start_date, end=end_date)
                        if not df_alt.empty and len(df_alt) >= max_days:
                            df = df_alt
                            symbol_clean = alt_clean
                            symbol_yf = alt_yf
                            
                    if df.empty or len(df) < max_days:
                        continue
                        
                    df['WR_S'] = calculate_williams_r(df['High'], df['Low'], df['Close'], int(wr_s_d))
                    df['WR_L'] = calculate_williams_r(df['High'], df['Low'], df['Close'], int(wr_l_d))
                    df['RSI_S'] = calculate_rsi(df['Close'], int(rsi_s_d))
                    df['RSI_L'] = calculate_rsi(df['Close'], int(rsi_l_d))
                    
                    k_s, d_s = calculate_kd(df['High'], df['Low'], df['Close'], int(kd_s_d))
                    k_l, d_l = calculate_kd(df['High'], df['Low'], df['Close'], int(kd_l_d))
                    
                    latest = df.iloc[-1]
                    current_price = float(latest['Close'])
                    
                    v_wr_s, v_wr_l = float(latest['WR_S']), float(latest['WR_L'])
                    v_rsi_s, v_rsi_l = float(latest['RSI_S']), float(latest['RSI_L'])
                    v_k_s, v_d_s = float(k_s.iloc[-1]), float(d_s.iloc[-1])
                    v_k_l, v_d_l = float(k_l.iloc[-1]), float(d_l.iloc[-1])
                    
                    match_wr = (v_wr_s < wr_s_t and v_wr_l < wr_l_t) if is_oversold else (v_wr_s > wr_s_t and v_wr_l > wr_l_t)
                    match_rsi = (v_rsi_s < rsi_s_t and v_rsi_l < rsi_l_t) if is_oversold else (v_rsi_s > rsi_s_t and v_rsi_l > rsi_l_t)
                    match_kd = (v_k_s < kd_s_k and v_d_s < kd_s_d_th and v_k_l < kd_l_k and v_d_l < kd_l_d_th) if is_oversold else (v_k_s > kd_s_k and v_d_s > kd_s_d_th and v_k_l > kd_l_k and v_d_l > kd_l_d_th)
                    
                    is_match = False
                    if all_three:
                        is_match = match_wr and match_rsi and match_kd
                    else:
                        temp_match = True
                        if "威廉" in req_inds and not match_wr: temp_match = False
                        if "RSI" in req_inds and not match_rsi: temp_match = False
                        if "KD" in req_inds and not match_kd: temp_match = False
                        is_match = temp_match
                        
                    if is_match:
                        yahoo_url = f"https://tw.stock.yahoo.com/quote/{symbol_clean}"
                        results.append({
                            "代號": symbol_clean,
                            "名稱": stock_name,
                            "收盤價": f"{current_price:.2f}",
                            "估算資金(張)": f"{int(current_price * 1000):,}",
                            "短W%R": round(v_wr_s, 2),
                            "長W%R": round(v_wr_l, 2),
                            "短RSI": round(v_rsi_s, 2),
                            "長RSI": round(v_rsi_l, 2),
                            "短K": round(v_k_s, 2),
                            "短D": round(v_d_s, 2),
                            "長K": round(v_k_l, 2),
                            "長D": round(v_d_l, 2),
                            "Yahoo資訊": yahoo_url
                        })
                except Exception as e:
                    pass
            
            status_text.text("掃描完成！")
            
            if results:
                st.success(f"🎉 掃描完成！共找到 {len(results)} 檔符合條件的標的：")
                df_results = pd.DataFrame(results)
                
                # 使用 Streamlit 原生的 LinkColumn 將網址轉化為可點擊的按鈕
                st.dataframe(
                    df_results,
                    column_config={
                        "Yahoo資訊": st.column_config.LinkColumn(
                            "Yahoo 資訊", display_text="點擊查看 🌐"
                        )
                    },
                    hide_index=True,
                    use_container_width=True
                )
            else:
                st.info("💡 掃描完成，目前沒有符合此策略參數的標的。")
