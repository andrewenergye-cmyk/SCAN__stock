import streamlit as st
import yfinance as yf
import pandas as pd
import datetime
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
from github import Github  # 確保 requirements.txt 有加入 PyGithub

# --- 設定檔名稱 ---
CONFIG_FILE = "strategy_config.json"

# ==========================================
# 1. 核心指標計算函式
# ==========================================
def calculate_williams_r(high, low, close, period):
    highest_high = high.rolling(window=period).max()
    lowest_low = low.rolling(window=period).min()
    return ((highest_high - close) / (highest_high - lowest_low)) * -100

def calculate_rsi(close, period):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_kd(high, low, close, rsv_period, smooth_period=3):
    highest_high = high.rolling(window=rsv_period).max()
    lowest_low = low.rolling(window=rsv_period).min()
    rsv = ((close - lowest_low) / (highest_high - lowest_low)) * 100
    k = rsv.ewm(com=smooth_period - 1, adjust=False).mean()
    d = k.ewm(com=smooth_period - 1, adjust=False).mean()
    return k, d

def yfinance_download_safe(symbol, start, end):
    return yf.Ticker(symbol).history(start=start, end=end)

# ==========================================
# 2. 設定檔與 GitHub 同步邏輯
# ==========================================
def load_config():
    """載入設定，包含獨立的超買與超賣參數"""
    defaults = {
        "oversold": {
            "wr_s_d": 7, "wr_s_t": -90.0, "wr_l_d": 30, "wr_l_t": -60.0,
            "rsi_s_d": 4, "rsi_s_t": 25.0, "rsi_l_d": 15, "rsi_l_t": 50.0,
            "kd_s_d": 9, "kd_s_k": 20.0, "kd_s_d_th": 20.0,
            "kd_l_d": 30, "kd_l_k": 30.0, "kd_l_d_th": 30.0
        },
        "overbought": {
            "wr_s_d": 7, "wr_s_t": -10.0, "wr_l_d": 30, "wr_l_t": -20.0,
            "rsi_s_d": 4, "rsi_s_t": 80.0, "rsi_l_d": 30, "rsi_l_t": 50.0,
            "kd_s_d": 9, "kd_s_k": 80.0, "kd_s_d_th": 80.0,
            "kd_l_d": 30, "kd_l_k": 70.0, "kd_l_d_th": 70.0
        }
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                saved = json.load(f)
                if "oversold" in saved: defaults["oversold"].update(saved["oversold"])
                if "overbought" in saved: defaults["overbought"].update(saved["overbought"])
        except: pass
    return defaults

def save_config_to_github(full_config):
    """將完整設定檔同步至 GitHub"""
    with open(CONFIG_FILE, "w") as f:
        json.dump(full_config, f, indent=4)
        
    token = st.secrets.get("GITHUB_TOKEN")
    repo_name = st.secrets.get("REPO_NAME")
    
    if not token or not repo_name:
        return False, "⚠️ 僅儲存於本機。若要連動自動掃描，請在 Secrets 設定 Token 與 Repo 名稱。"
        
    try:
        g = Github(token)
        repo = g.get_repo(repo_name)
        content_str = json.dumps(full_config, indent=4)
        try:
            contents = repo.get_contents(CONFIG_FILE)
            repo.update_file(contents.path, "Update strategy parameters via Streamlit", content_str, contents.sha)
        except:
            repo.create_file(CONFIG_FILE, "Initial strategy config", content_str)
        return True, "✅ 參數已成功同步至 GitHub！"
    except Exception as e:
        return False, f"❌ GitHub 同步失敗: {e}"

# ==========================================
# 3. 郵件發送邏輯
# ==========================================
def get_secret(key):
    if os.environ.get(key): return os.environ.get(key)
    try: return st.secrets[key]
    except: return None

def send_results_email(results_list):
    """將目前的掃描結果發送至設定的信箱"""
    sender_email = get_secret("SENDER_EMAIL")
    app_password = get_secret("APP_PASSWORD")
    receiver_email = get_secret("RECEIVER_EMAIL")

    if not sender_email or not app_password or not receiver_email:
        return False, "❌ Email 環境變數未設定。"

    end_date = datetime.date.today()
    if not results_list:
        html_content = "<h3>量化掃描完成</h3><p>本次掃描無符合條件標的。</p>"
    else:
        html_content = f"<h3>掃描完成，共 {len(results_list)} 檔符合條件：</h3><ul>"
        for res in results_list:
            html_content += f"<li><b>{res['代號']} {res['名稱']}</b> - 收盤: {res['收盤價']} | 短W%R: {res['短W%R']} | 長W%R: {res['長W%R']}</li>"
        html_content += "</ul><br><p>查看 Yahoo 股市：</p><ul>"
        for res in results_list:
            html_content += f'<li><a href="{res["Yahoo資訊"]}">{res["代號"]} 資訊</a></li>'
        html_content += "</ul>"

    receiver_list = [e.strip() for e in receiver_email.split(",")]
    msg = MIMEMultipart("alternative")
    msg['Subject'] = f"📈 策略掃描報告 ({end_date.strftime('%Y-%m-%d')})"
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg.attach(MIMEText(html_content, 'html'))

    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(sender_email, app_password)
        server.sendmail(sender_email, receiver_list, msg.as_string())
        server.quit()
        return True, "✅ 信件發送成功！"
    except Exception as e:
        return False, f"❌ 寄信失敗: {e}"

# ==========================================
# 4. Streamlit UI 介面
# ==========================================
st.set_page_config(page_title="多指標量化掃描工具", layout="wide", page_icon="📈")

# 初始化 Session State
if 'results' not in st.session_state: st.session_state['results'] = []
if 'scanned' not in st.session_state: st.session_state['scanned'] = False

current_config = load_config()

st.title("📈 多指標量化掃描工具")

with st.sidebar:
    st.header("📂 1. 資料來源")
    uploaded_files = st.file_uploader("上傳 CSV 檔案", accept_multiple_files=True, type=['csv'])
    
    st.header("⚙️ 2. 條件組合")
    c1, c2 = st.columns(2)
    with c1: ind1 = st.selectbox("指標一", ["無選擇", "威廉", "RSI", "KD"], index=1)
    with c2: ind2 = st.selectbox("指標二", ["無選擇", "威廉", "RSI", "KD"])
    all_three = st.checkbox("✅ 三者同時符合", value=False)
    
    st.header("🎯 3. 掃描模式")
    scan_mode = st.radio("策略方向", ["超賣 (低於門檻)", "超買 (高於門檻)"])
    is_oversold = (scan_mode == "超賣 (低於門檻)")
    
    st.divider()
    st.header("✉️ 測試通知")
    if st.button("發送掃描結果信件", use_container_width=True):
        if not st.session_state['scanned']: st.warning("請先執行掃描。")
        else:
            with st.spinner('發送中...'):
                ok, m = send_results_email(st.session_state['results'])
                if ok:
                    st.success(m)
                else:
                    st.error(m)

# 參數設定區
st.subheader("📊 4. 參數設定")
mode_key = "oversold" if is_oversold else "overbought"
cfg = current_config[mode_key]
prefix = "os_" if is_oversold else "ob_"

st.markdown(f"**模式：{'超賣 (A)' if is_oversold else '超買 (B)'}**")

col_wr, col_rsi, col_kd = st.columns(3)
with col_wr:
    with st.expander("威廉指標 (W%R)", expanded=True):
        wr_s_d = st.number_input("短天期", value=cfg['wr_s_d'], key=f"{prefix}wr_s_d")
        wr_s_t = st.number_input("短天期門檻", value=cfg['wr_s_t'], key=f"{prefix}wr_s_t")
        st.divider()
        wr_l_d = st.number_input("長天期", value=cfg['wr_l_d'], key=f"{prefix}wr_l_d")
        wr_l_t = st.number_input("長天期門檻", value=cfg['wr_l_t'], key=f"{prefix}wr_l_t")

with col_rsi:
    with st.expander("相對強弱指標 (RSI)", expanded=True):
        rsi_s_d = st.number_input("RSI 短天期", value=cfg['rsi_s_d'], key=f"{prefix}rsi_s_d")
        rsi_s_t = st.number_input("RSI 短天期門檻", value=cfg['rsi_s_t'], key=f"{prefix}rsi_s_t")
        st.divider()
        rsi_l_d = st.number_input("RSI 長天期", value=cfg['rsi_l_d'], key=f"{prefix}rsi_l_d")
        rsi_l_t = st.number_input("RSI 長天期門檻", value=cfg['rsi_l_t'], key=f"{prefix}rsi_l_t")

with col_kd:
    with st.expander("隨機指標 (KD)", expanded=True):
        kd_s_d = st.number_input("KD 短天期", value=cfg['kd_s_d'], key=f"{prefix}kd_s_d")
        k1, k2 = st.columns(2)
        kd_s_k = k1.number_input("短K門檻", value=cfg['kd_s_k'], key=f"{prefix}kd_s_k")
        kd_s_d_th = k2.number_input("短D門檻", value=cfg['kd_s_d_th'], key=f"{prefix}kd_s_d_th")
        st.divider()
        kd_l_d = st.number_input("KD 長天期", value=cfg['kd_l_d'], key=f"{prefix}kd_l_d")
        l1, l2 = st.columns(2)
        kd_l_k = l1.number_input("長K門檻", value=cfg['kd_l_k'], key=f"{prefix}kd_l_k")
        kd_l_d_th = l2.number_input("長D門檻", value=cfg['kd_l_d_th'], key=f"{prefix}kd_l_d_th")

if st.button(f"💾 儲存【{'超賣' if is_oversold else '超買'}】參數並同步至 GitHub", use_container_width=True):
    current_config[mode_key] = {
        "wr_s_d": int(wr_s_d), "wr_s_t": float(wr_s_t), "wr_l_d": int(wr_l_d), "wr_l_t": float(wr_l_t),
        "rsi_s_d": int(rsi_s_d), "rsi_s_t": float(rsi_s_t), "rsi_l_d": int(rsi_l_d), "rsi_l_t": float(rsi_l_t),
        "kd_s_d": int(kd_s_d), "kd_s_k": float(kd_s_k), "kd_s_d_th": float(kd_s_d_th),
        "kd_l_d": int(kd_l_d), "kd_l_k": float(kd_l_k), "kd_l_d_th": float(kd_l_d_th)
    }
with st.spinner("同步中..."):
        ok, msg = save_config_to_github(current_config)
        if ok:
            st.success(msg)
        else:
            st.warning(msg)

st.divider()

# ==========================================
# 5. 掃描執行邏輯
# ==========================================
if st.button("🚀 開始掃描", use_container_width=True, type="primary"):
    dfs = []
    if uploaded_files:
        for f in uploaded_files:
            try: dfs.append(pd.read_csv(f, encoding='utf-8-sig', dtype=str))
            except: 
                f.seek(0)
                dfs.append(pd.read_csv(f, encoding='big5', dtype=str))
    elif os.path.exists("default_stocks.csv"):
        try: dfs.append(pd.read_csv("default_stocks.csv", encoding='utf-8-sig', dtype=str))
        except: dfs.append(pd.read_csv("default_stocks.csv", encoding='big5', dtype=str))
    
    if not dfs: st.warning("⚠️ 請提供 CSV 檔案。")
    else:
        parsed = []
        for df in dfs:
            df.columns = [str(c).strip() for c in df.columns]
            if '代號' in df.columns:
                for _, row in df.iterrows():
                    s = str(row['代號']).strip().replace('.0', '')
                    if not s or s.lower() == 'nan': continue
                    if s.isdigit() and len(s) < 4: s = "00" + s
                    market = str(row.get('市場', '')).strip()
                    yf_s = f"{s}.TWO" if '櫃' in market else f"{s}.TW"
                    parsed.append({"clean": s, "yf": yf_s, "name": str(row.get('名稱', '')).replace('nan','')})

        targets = list({d['yf']: d for d in parsed}.values())
        req = {ind1, ind2} - {"無選擇"}
        
        if not all_three and not req: st.error("⚠️ 請選擇指標。")
        else:
            max_d = max(wr_s_d, wr_l_d, rsi_s_d, rsi_l_d, kd_s_d, kd_l_d)
            start = datetime.date.today() - datetime.timedelta(days=max_d * 4 + 15)
            
            results = []
            prog = st.progress(0)
            status = st.empty()
            
            for i, t in enumerate(targets):
                status.text(f"掃描中 ({i+1}/{len(targets)}): {t['clean']} {t['name']}")
                prog.progress((i + 1) / len(targets))
                try:
                    df = yfinance_download_safe(t['yf'], start, None)
                    if df.empty or len(df) < max_d: continue
                    
                    # 計算指標
                    df['WR_S'] = calculate_williams_r(df['High'], df['Low'], df['Close'], int(wr_s_d))
                    df['WR_L'] = calculate_williams_r(df['High'], df['Low'], df['Close'], int(wr_l_d))
                    df['RSI_S'] = calculate_rsi(df['Close'], int(rsi_s_d))
                    df['RSI_L'] = calculate_rsi(df['Close'], int(rsi_l_d))
                    k_s, d_s = calculate_kd(df['High'], df['Low'], df['Close'], int(kd_s_d))
                    k_l, d_l = calculate_kd(df['High'], df['Low'], df['Close'], int(kd_l_d))
                    
                    lt = df.iloc[-1]
                    cur_p = float(lt['Close'])
                    vws, vwl = float(lt['WR_S']), float(lt['WR_L'])
                    vrs, vrl = float(lt['RSI_S']), float(lt['RSI_L'])
                    vks, vds = float(k_s.iloc[-1]), float(d_s.iloc[-1])
                    vkl, vdl = float(k_l.iloc[-1]), float(d_l.iloc[-1])
                    
                    # 判斷條件
                    m_wr = (vws < wr_s_t and vwl < wr_l_t) if is_oversold else (vws > wr_s_t and vwl > wr_l_t)
                    m_rsi = (vrs < rsi_s_t and vrl < rsi_l_t) if is_oversold else (vrs > rsi_s_t and vrl > rsi_l_t)
                    m_kd = (vks < kd_s_k and vds < kd_s_d_th and vkl < kd_l_k and vdl < kd_l_d_th) if is_oversold else (vks > kd_s_k and vds > kd_s_d_th and vkl > kd_l_k and vdl > kd_l_d_th)
                    
                    is_m = (m_wr and m_rsi and m_kd) if all_three else all([ (ind not in req or m) for ind, m in [("威廉", m_wr), ("RSI", m_rsi), ("KD", m_kd)] ])
                    
                    if is_m:
                        results.append({
                            "代號": t['clean'], "名稱": t['name'], "收盤價": f"{cur_p:.2f}",
                            "估算資金": f"{int(cur_p * 1000):,}", "短W%R": round(vws, 2), "長W%R": round(vwl, 2),
                            "短RSI": round(vrs, 2), "長RSI": round(vrl, 2), "Yahoo資訊": f"https://tw.stock.yahoo.com/quote/{t['clean']}"
                        })
                except: pass
            
            st.session_state['results'] = results
            st.session_state['scanned'] = True
            status.text("掃描完成！")

# 顯示結果
if st.session_state['scanned']:
    if st.session_state['results']:
        st.success(f"共找到 {len(st.session_state['results'])} 檔符合條件標的：")
        st.dataframe(pd.DataFrame(st.session_state['results']), column_config={"Yahoo資訊": st.column_config.LinkColumn("Yahoo 資訊", display_text="🌐")}, hide_index=True, use_container_width=True)
    else: st.info("💡 目前無符合條件標的。")
