import streamlit as st
import yfinance as yf
import pandas as pd
import datetime
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
from github import Github # 新增套件

CONFIG_FILE = "strategy_config.json"

# ==========================================
# 設定檔讀寫函式
# ==========================================
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except: pass
    return {}

def save_config_to_github(new_config):
    # 1. 本機存一份
    with open(CONFIG_FILE, "w") as f:
        json.dump(new_config, f, indent=4)
        
    # 2. 嘗試同步到 GitHub
    token = st.secrets.get("GITHUB_TOKEN")
    repo_name = st.secrets.get("REPO_NAME")
    
    if not token or not repo_name:
        return False, "⚠️ 參數已暫存本機。若要同步至自動掃描系統，請在 Secrets 設定 GITHUB_TOKEN 與 REPO_NAME。"
        
    try:
        g = Github(token)
        repo = g.get_repo(repo_name)
        try:
            contents = repo.get_contents(CONFIG_FILE)
            repo.update_file(contents.path, "Streamlit 自動更新策略參數", json.dumps(new_config, indent=4), contents.sha)
        except:
            repo.create_file(CONFIG_FILE, "建立策略參數檔", json.dumps(new_config, indent=4))
        return True, "✅ 參數已成功同步至 GitHub！明日的自動掃描將套用新策略。"
    except Exception as e:
        return False, f"❌ GitHub 同步失敗: {e}"

# --- 讀取最新設定 ---
current_config = load_config()

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
# 寄發 Email 專用函式
# ==========================================
def send_results_email(results_list):
    """將掃描結果發送至設定的信箱"""
    end_date = datetime.date.today()
    
    if not results_list:
        html_content = "<h3>本日量化掃描完成</h3><p>本次手動測試掃描，無符合目前設定條件的標的。</p>"
    else:
        html_content = f"<h3>手動量化掃描測試完成，共 {len(results_list)} 檔符合條件：</h3><ul>"
        for res in results_list:
            html_content += f"<li><b>{res['代號']} {res['名稱']}</b> - 收盤: {res['收盤價']} | 短W%R: {res['短W%R']} | 長W%R: {res['長W%R']}</li>"
        html_content += "</ul><br><p>前往 Yahoo 股市查看：</p><ul>"
        
        for res in results_list:
            html_content += f'<li><a href="{res["Yahoo資訊"]}">{res["代號"]} 資訊</a></li>'
        html_content += "</ul>"

    # 讀取環境變數 (請確保您在終端機或雲端有設定好)
    sender_email = os.environ.get("SENDER_EMAIL")
    app_password = os.environ.get("APP_PASSWORD")
    receiver_email = os.environ.get("RECEIVER_EMAIL")

    if not sender_email or not app_password or not receiver_email:
        return False, "❌ 環境變數 (SENDER_EMAIL / APP_PASSWORD / RECEIVER_EMAIL) 未設定，請檢查設定檔或終端機變數。"

    receiver_list = [email.strip() for email in receiver_email.split(",")]

    msg = MIMEMultipart("alternative")
    msg['Subject'] = f"📈 台股量化掃描測試通報 ({end_date.strftime('%Y-%m-%d %H:%M')})"
    msg['From'] = sender_email
    msg['To'] = receiver_email 
    msg.attach(MIMEText(html_content, 'html'))

    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(sender_email, app_password)
        server.sendmail(sender_email, receiver_list, msg.as_string())
        server.quit()
        return True, f"✅ 掃描結果信件發送成功！已寄送至：\n{receiver_email}"
    except Exception as e:
        return False, f"❌ 寄信失敗: {e}"

# ==========================================
# 網頁介面與邏輯 (Streamlit)
# ==========================================
st.set_page_config(page_title="多指標量化掃描工具", layout="wide", page_icon="📈")
st.title("📈 多指標(W%R/RSI/KD)量化掃描工具by峰臣")
st.markdown("將您的 CSV 股票清單上傳，系統將自動套用您的策略進行雲端運算。若未上傳，將自動載入預設清單。")

# 用 session_state 保存目前的掃描結果，以便獨立寄信使用
if 'latest_results' not in st.session_state:
    st.session_state['latest_results'] = []
if 'has_scanned' not in st.session_state:
    st.session_state['has_scanned'] = False

# --- 側邊欄：檔案上傳與指標設定 ---
with st.sidebar:
    st.header("📂 1. 資料來源")
    uploaded_files = st.file_uploader("請上傳 CSV 檔案 (可多選，若不選則載入預設檔)", accept_multiple_files=True, type=['csv'])
    
    st.header("⚙️ 2. 條件組合")
    col1, col2 = st.columns(2)
    with col1:
        ind1 = st.selectbox("指標一", ["無選擇", "威廉", "RSI", "KD"], index=1)
    with col2:
        ind2 = st.selectbox("指標二", ["無選擇", "威廉", "RSI", "KD"])
        
    all_three = st.checkbox("✅ 三者同時符合 (勾選即忽略上方組合)", value=False)
    
    st.header("🎯 3. 掃描模式")
    scan_mode = st.radio("請選擇策略方向", ["超賣 (低於門檻)", "超買 (高於門檻)"])
    is_oversold = (scan_mode == "超賣 (低於門檻)")
    
    st.divider()
    
    # 💡 新增區塊：發送 Email 按鈕
    st.header("✉️ 測試發送 Email")
    st.write("點擊下方按鈕，將目前畫面上的掃描結果發送至設定的信箱。")
    if st.button("發送掃描結果信件", use_container_width=True):
        if not st.session_state['has_scanned']:
            st.warning("請先點擊主畫面的「🚀 開始掃描」獲取結果後再寄信。")
        else:
            with st.spinner('正在連線 Gmail 伺服器發送信件...'):
                success, msg = send_results_email(st.session_state['latest_results'])
                if success:
                    st.success(msg)
                else:
                    st.error(msg)

# --- 主畫面：參數設定區 ---
st.subheader("📊 4. 參數設定")

col_wr, col_rsi, col_kd = st.columns(3)

with col_wr:
    with st.expander("威廉指標 (W%R)", expanded=True):
        wr_s_d = st.number_input("短天期", value=current_config.get("wr_s_d", 7), step=1)
        wr_s_t = st.number_input("短天期門檻", value=current_config.get("wr_s_t", -90.0), step=1.0)
        st.divider()
        wr_l_d = st.number_input("長天期", value=current_config.get("wr_l_d", 30), step=1)
        wr_l_t = st.number_input("長天期門檻", value=current_config.get("wr_l_t", -60.0), step=1.0)

with col_rsi:
    with st.expander("相對強弱指標 (RSI)", expanded=True):
        rsi_s_d = st.number_input("RSI 短天期", value=current_config.get("rsi_s_d", 4), step=1)
        rsi_s_t = st.number_input("RSI 短天期門檻", value=current_config.get("rsi_s_t", 25.0), step=1.0)
        st.divider()
        rsi_l_d = st.number_input("RSI 長天期", value=current_config.get("rsi_l_d", 15), step=1)
        rsi_l_t = st.number_input("RSI 長天期門檻", value=current_config.get("rsi_l_t", 50.0), step=1.0)

with col_kd:
    with st.expander("隨機指標 (KD)", expanded=True):
        kd_s_d = st.number_input("KD 短天期", value=current_config.get("kd_s_d", 9), step=1)
        k_col1, k_col2 = st.columns(2)
        kd_s_k = k_col1.number_input("短K門檻", value=current_config.get("kd_s_k", 20.0), step=1.0)
        kd_s_d_th = k_col2.number_input("短D門檻", value=current_config.get("kd_s_d_th", 20.0), step=1.0)
        st.divider()
        kd_l_d = st.number_input("KD 長天期", value=current_config.get("kd_l_d", 30), step=1)
        l_col1, l_col2 = st.columns(2)
        kd_l_k = l_col1.number_input("長K門檻", value=current_config.get("kd_l_k", 30.0), step=1.0)
        kd_l_d_th = l_col2.number_input("長D門檻", value=current_config.get("kd_l_d_th", 30.0), step=1.0)

st.divider()

# --- 新增：儲存參數按鈕 (放在掃描按鈕上方) ---
if st.button("💾 將上方參數記憶並設為「自動掃描」預設值", use_container_width=True):
    new_config = {
        "wr_s_d": int(wr_s_d), "wr_s_t": float(wr_s_t),
        "wr_l_d": int(wr_l_d), "wr_l_t": float(wr_l_t),
        "rsi_s_d": int(rsi_s_d), "rsi_s_t": float(rsi_s_t),
        "rsi_l_d": int(rsi_l_d), "rsi_l_t": float(rsi_l_t),
        "kd_s_d": int(kd_s_d), "kd_s_k": float(kd_s_k), "kd_s_d_th": float(kd_s_d_th),
        "kd_l_d": int(kd_l_d), "kd_l_k": float(kd_l_k), "kd_l_d_th": float(kd_l_d_th)
    }
    with st.spinner("正在將參數同步至 GitHub..."):
        success, msg = save_config_to_github(new_config)
        if success:
            st.success(msg)
        else:
            st.warning(msg)

# ==========================================
# 掃描執行區
# ==========================================
if st.button("🚀 開始掃描", use_container_width=True, type="primary"):
    
    dataframes = [] 
    
    if uploaded_files:
        for file in uploaded_files:
            try:
                try:
                    df = pd.read_csv(file, encoding='utf-8-sig', dtype=str)
                except UnicodeDecodeError:
                    file.seek(0)
                    df = pd.read_csv(file, encoding='big5', dtype=str)
                dataframes.append(df)
            except Exception as e:
                st.error(f"讀取檔案 {file.name} 失敗：{e}")
    else:
        default_file = "default_stocks.csv"
        if os.path.exists(default_file):
            st.info(f"📂 未偵測到手動上傳，已自動載入預設清單：`{default_file}`")
            try:
                try:
                    df = pd.read_csv(default_file, encoding='utf-8-sig', dtype=str)
                except UnicodeDecodeError:
                    df = pd.read_csv(default_file, encoding='big5', dtype=str)
                dataframes.append(df)
            except Exception as e:
                st.error(f"預設檔案讀取失敗：{e}")
        else:
            st.warning("⚠️ 請先在左側欄位上傳 CSV 檔案，或在程式資料夾放入 `default_stocks.csv` 作為預設檔。")

    if dataframes:
        parsed_data = []
        for df in dataframes:
            df.columns = [str(col).strip() for col in df.columns]
            if '代號' in df.columns:
                for index, row in df.iterrows():
                    symbol = str(row['代號']).strip()
                    if symbol.endswith('.0'): symbol = symbol[:-2]
                    if not symbol or symbol.lower() == 'nan': continue
                    
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

        unique_targets = {d['yf_symbol']: d for d in parsed_data}
        targets_list = list(unique_targets.values())
        
        req_inds = set()
        if ind1 != "無選擇": req_inds.add(ind1)
        if ind2 != "無選擇": req_inds.add(ind2)
        
        if not all_three and len(req_inds) == 0:
            st.error("⚠️ 請至少在左側下拉選單選擇一個分析指標，或是勾選「三者同時符合」。")
        elif not targets_list:
             st.warning("⚠️ 讀取不到任何有效的股票代號，請檢查 CSV 格式是否有「代號」欄位。")
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
            
            # 將結果存入 session_state 以供側邊欄寄信用
            st.session_state['latest_results'] = results
            st.session_state['has_scanned'] = True
            
            if results:
                st.success(f"🎉 掃描完成！共找到 {len(results)} 檔符合條件的標的：")
                df_results = pd.DataFrame(results)
                
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

# --- 顯示舊有掃描結果 (當重新渲染側邊欄或寄信時) ---
elif st.session_state['has_scanned']:
    if st.session_state['latest_results']:
        st.success(f"ℹ️ 畫面保留上次掃描結果，共找到 {len(st.session_state['latest_results'])} 檔：")
        df_results = pd.DataFrame(st.session_state['latest_results'])
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
        st.info("💡 上次掃描結果為空，沒有符合條件的標的。")
