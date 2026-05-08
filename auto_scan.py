import yfinance as yf
import pandas as pd
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import json

# ==========================================
# 1. 核心指標計算函式
# ==========================================
def calculate_williams_r(high, low, close, period):
    highest_high = high.rolling(window=period).max()
    lowest_low = low.rolling(window=period).min()
    return ((highest_high - close) / (highest_high - lowest_low)) * -100

def main():
    print("🤖 啟動每日自動量化掃描腳本 (超賣/超買雙向掃描)...")
    default_file = "default_stocks.csv"
    
    # 檢查名單是否存在
    if not os.path.exists(default_file):
        print(f"❌ 找不到預設股票清單 {default_file}，腳本結束。")
        return

    # 讀取 CSV 名單 (支援 UTF-8 與 BIG5 防呆)
    try:
        df_csv = pd.read_csv(default_file, encoding='utf-8-sig', dtype=str)
    except:
        df_csv = pd.read_csv(default_file, encoding='big5', dtype=str)
        
    targets = []
    for _, row in df_csv.iterrows():
        symbol = str(row.get('代號', '')).strip()
        if symbol.endswith('.0'): symbol = symbol[:-2]
        if not symbol or symbol.lower() == 'nan': continue
        
        # ETF 防呆補零
        if symbol.isdigit() and len(symbol) < 4:
            symbol = "00" + symbol
            
        market = str(row.get('市場', '')).strip()
        name = str(row.get('名稱', '')).strip()
        yf_symbol = f"{symbol}.TWO" if '櫃' in market else f"{symbol}.TW"
        targets.append({"clean": symbol, "yf": yf_symbol, "name": name})

    if not targets:
        print("⚠️ 股票名單為空，腳本結束。")
        return

    # ==========================================
    # 2. 讀取雲端連動的策略參數 (同時讀取雙模式)
    # ==========================================
    config_file = "strategy_config.json"
    full_config = {}
    try:
        if os.path.exists(config_file):
            with open(config_file, "r") as f:
                full_config = json.load(f)
            print("✅ 成功讀取 GitHub 雲端策略參數！")
    except Exception as e:
        print(f"⚠️ 讀取參數檔失敗 ({e})，將使用系統預設值。")

    # 超賣參數 (Oversold)
    os_cfg = full_config.get("oversold", {})
    os_wr_s_d, os_wr_s_t = os_cfg.get("wr_s_d", 7), os_cfg.get("wr_s_t", -90.0)
    os_wr_l_d, os_wr_l_t = os_cfg.get("wr_l_d", 30), os_cfg.get("wr_l_t", -60.0)

    # 超買參數 (Overbought)
    ob_cfg = full_config.get("overbought", {})
    ob_wr_s_d, ob_wr_s_t = ob_cfg.get("wr_s_d", 7), ob_cfg.get("wr_s_t", -10.0)
    ob_wr_l_d, ob_wr_l_t = ob_cfg.get("wr_l_d", 30), ob_cfg.get("wr_l_t", -20.0)
    
    print(f"📊 【超賣條件】短W%R({os_wr_s_d}天) < {os_wr_s_t} 且 長W%R({os_wr_l_d}天) < {os_wr_l_t}")
    print(f"📊 【超買條件】短W%R({ob_wr_s_d}天) > {ob_wr_s_t} 且 長W%R({ob_wr_l_d}天) > {ob_wr_l_t}")

    # ==========================================
    # 3. 開始執行掃描
    # ==========================================
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=150) 
    
    os_results = [] # 存放超賣名單
    ob_results = [] # 存放超買名單
    
    for stock in targets:
        try:
            print(f"掃描中: {stock['clean']} {stock['name']}")
            df = yf.Ticker(stock['yf']).history(start=start_date, end=end_date)
            
            # 智慧重試 (針對 ETF)
            if (df.empty or len(df) < 30) and len(stock['clean']) == 4 and stock['clean'].isdigit():
                alt_clean = "00" + stock['clean']
                alt_yf = f"{alt_clean}.TWO" if ".TWO" in stock['yf'] else f"{alt_clean}.TW"
                df_alt = yf.Ticker(alt_yf).history(start=start_date, end=end_date)
                if not df_alt.empty and len(df_alt) >= 30:
                    df = df_alt
                    stock['clean'] = alt_clean

            if df.empty or len(df) < 30: 
                continue
            
            # 計算當下收盤價
            current_price = float(df['Close'].iloc[-1])

            # --- 判斷超賣 (Oversold) ---
            v_wr_s_os = float(calculate_williams_r(df['High'], df['Low'], df['Close'], int(os_wr_s_d)).iloc[-1])
            v_wr_l_os = float(calculate_williams_r(df['High'], df['Low'], df['Close'], int(os_wr_l_d)).iloc[-1])
            if v_wr_s_os < float(os_wr_s_t) and v_wr_l_os < float(os_wr_l_t):
                os_results.append(f"<li><b>{stock['clean']} {stock['name']}</b> - 收盤: {current_price:.2f} | 短W%R: {v_wr_s_os:.2f} | 長W%R: {v_wr_l_os:.2f} <a href='https://tw.stock.yahoo.com/quote/{stock['clean']}'>[Yahoo資訊]</a></li>")
                print(f"  👉 [符合超賣] {stock['clean']}")

            # --- 判斷超買 (Overbought) ---
            v_wr_s_ob = float(calculate_williams_r(df['High'], df['Low'], df['Close'], int(ob_wr_s_d)).iloc[-1])
            v_wr_l_ob = float(calculate_williams_r(df['High'], df['Low'], df['Close'], int(ob_wr_l_d)).iloc[-1])
            if v_wr_s_ob > float(ob_wr_s_t) and v_wr_l_ob > float(ob_wr_l_t):
                ob_results.append(f"<li><b>{stock['clean']} {stock['name']}</b> - 收盤: {current_price:.2f} | 短W%R: {v_wr_s_ob:.2f} | 長W%R: {v_wr_l_ob:.2f} <a href='https://tw.stock.yahoo.com/quote/{stock['clean']}'>[Yahoo資訊]</a></li>")
                print(f"  👉 [符合超買] {stock['clean']}")

        except Exception as e:
            pass # 忽略異常標的

    # ==========================================
    # 4. 寄發 Email 邏輯 (整合雙模式)
    # ==========================================
    html_content = f"<h2>📈 每日台股量化掃描通報 ({end_date.strftime('%Y-%m-%d')})</h2>"
    
    # 綠色區塊：超賣 (買點)
    html_content += "<h3 style='color: green;'>🟢 超賣 (指標波段低點) 符合標的：</h3>"
    if not os_results:
        html_content += "<p>今日無符合條件標的。</p>"
    else:
        html_content += f"<ul>{''.join(os_results)}</ul>"
        
    html_content += "<hr>"

    # 紅色區塊：超買 (賣點)
    html_content += "<h3 style='color: red;'>🔴 超買 (指標波段高點) 符合標的：</h3>"
    if not ob_results:
        html_content += "<p>今日無符合條件標的。</p>"
    else:
        html_content += f"<ul>{''.join(ob_results)}</ul>"

    # 從環境變數取得金鑰
# 從環境變數取得金鑰
    sender_email = os.environ.get("SENDER_EMAIL")
    app_password = os.environ.get("APP_PASSWORD")
    receiver_email = os.environ.get("RECEIVER_EMAIL")

    if not sender_email or not app_password or not receiver_email:
        print("❌ 未設定 Email 環境變數 (Secrets)，跳過寄信階段。")
        return

    # 💡 防呆升級：強制替換全形逗號與分號，並自動過濾空白
    clean_receivers = receiver_email.replace("，", ",").replace(";", ",")
    receiver_list = [e.strip() for e in clean_receivers.split(",") if e.strip()]

    msg = MIMEMultipart("alternative")
    msg['Subject'] = f"📈 每日雙向掃描報告：超賣 {len(os_results)} 檔 / 超買 {len(ob_results)} 檔"
    msg['From'] = sender_email
    
    # 💡 使用重新組合好的乾淨字串作為標頭
    msg['To'] = ", ".join(receiver_list) 
    
    msg.attach(MIMEText(html_content, 'html'))

    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(sender_email, app_password)
        server.sendmail(sender_email, receiver_list, msg.as_string())
        server.quit()
        print(f"✅ 雙向掃描結果信件發送成功！已寄送至：{receiver_email}")
    except Exception as e:
        print(f"❌ 寄信失敗: {e}")

if __name__ == "__main__":
    main()
