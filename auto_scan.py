import yfinance as yf
import pandas as pd
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import json
import gspread
from google.oauth2 import service_account

# ==========================================
# 1. 核心指標計算函式
# ==========================================
def calculate_williams_r(high, low, close, period):
    highest_high = high.rolling(window=period).max()
    lowest_low = low.rolling(window=period).min()
    return ((highest_high - close) / (highest_high - lowest_low)) * -100

def main():
    print("🤖 啟動每日自動量化掃描腳本 (雙向掃描 + Google Sheets 紀錄)...")
    default_file = "default_stocks.csv"
    
    if not os.path.exists(default_file):
        print(f"❌ 找不到預設股票清單 {default_file}，腳本結束。")
        return

    try:
        df_csv = pd.read_csv(default_file, encoding='utf-8-sig', dtype=str)
    except:
        df_csv = pd.read_csv(default_file, encoding='big5', dtype=str)
        
    targets = []
    for _, row in df_csv.iterrows():
        symbol = str(row.get('代號', '')).strip()
        if symbol.endswith('.0'): symbol = symbol[:-2]
        if not symbol or symbol.lower() == 'nan': continue
        if symbol.isdigit() and len(symbol) < 4: symbol = "00" + symbol
        market = str(row.get('市場', '')).strip()
        name = str(row.get('名稱', '')).strip()
        yf_symbol = f"{symbol}.TWO" if '櫃' in market else f"{symbol}.TW"
        targets.append({"clean": symbol, "yf": yf_symbol, "name": name})

    if not targets:
        return

    # ==========================================
    # 2. 讀取雲端連動的策略參數
    # ==========================================
    config_file = "strategy_config.json"
    full_config = {}
    try:
        if os.path.exists(config_file):
            with open(config_file, "r") as f:
                full_config = json.load(f)
    except: pass

    os_cfg = full_config.get("oversold", {})
    os_wr_s_d, os_wr_s_t = os_cfg.get("wr_s_d", 7), os_cfg.get("wr_s_t", -90.0)
    os_wr_l_d, os_wr_l_t = os_cfg.get("wr_l_d", 30), os_cfg.get("wr_l_t", -60.0)

    ob_cfg = full_config.get("overbought", {})
    ob_wr_s_d, ob_wr_s_t = ob_cfg.get("wr_s_d", 7), ob_cfg.get("wr_s_t", -10.0)
    ob_wr_l_d, ob_wr_l_t = ob_cfg.get("wr_l_d", 30), ob_cfg.get("wr_l_t", -20.0)

    # ==========================================
    # 3. 開始執行掃描
    # ==========================================
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=150) 
    date_str = end_date.strftime('%Y/%m/%d')
    
    os_results, ob_results = [], []
    sheet_data_to_append = [] # 💡 準備要塞進 Google 試算表的資料陣列
    
    for stock in targets:
        try:
            print(f"掃描中: {stock['clean']} {stock['name']}")
            df = yf.Ticker(stock['yf']).history(start=start_date, end=end_date)
            
            if (df.empty or len(df) < 30) and len(stock['clean']) == 4 and stock['clean'].isdigit():
                alt_clean = "00" + stock['clean']
                alt_yf = f"{alt_clean}.TWO" if ".TWO" in stock['yf'] else f"{alt_clean}.TW"
                df_alt = yf.Ticker(alt_yf).history(start=start_date, end=end_date)
                if not df_alt.empty and len(df_alt) >= 30:
                    df = df_alt; stock['clean'] = alt_clean

            if df.empty or len(df) < 30: continue
            current_price = float(df['Close'].iloc[-1])

            # --- 超賣 ---
            v_wr_s_os = float(calculate_williams_r(df['High'], df['Low'], df['Close'], int(os_wr_s_d)).iloc[-1])
            v_wr_l_os = float(calculate_williams_r(df['High'], df['Low'], df['Close'], int(os_wr_l_d)).iloc[-1])
            if v_wr_s_os < float(os_wr_s_t) and v_wr_l_os < float(os_wr_l_t):
                os_results.append(f"<li style='margin-bottom: 8px; font-size: 16px;'><b>{stock['clean']} {stock['name']}</b> ➔ 💰 <b style='color: blue;'>收盤價: {current_price:.2f}</b> | 短W%R: {v_wr_s_os:.2f} | 長W%R: {v_wr_l_os:.2f} <a href='https://tw.stock.yahoo.com/quote/{stock['clean']}'>[Yahoo資訊]</a></li>")
                sheet_data_to_append.append([date_str, "超賣", stock['clean'], stock['name'], current_price, round(v_wr_s_os, 2), round(v_wr_l_os, 2)])

            # --- 超買 ---
            v_wr_s_ob = float(calculate_williams_r(df['High'], df['Low'], df['Close'], int(ob_wr_s_d)).iloc[-1])
            v_wr_l_ob = float(calculate_williams_r(df['High'], df['Low'], df['Close'], int(ob_wr_l_d)).iloc[-1])
            if v_wr_s_ob > float(ob_wr_s_t) and v_wr_l_ob > float(ob_wr_l_t):
                ob_results.append(f"<li style='margin-bottom: 8px; font-size: 16px;'><b>{stock['clean']} {stock['name']}</b> ➔ 💰 <b style='color: blue;'>收盤價: {current_price:.2f}</b> | 短W%R: {v_wr_s_ob:.2f} | 長W%R: {v_wr_l_ob:.2f} <a href='https://tw.stock.yahoo.com/quote/{stock['clean']}'>[Yahoo資訊]</a></li>")
                sheet_data_to_append.append([date_str, "超買", stock['clean'], stock['name'], current_price, round(v_wr_s_ob, 2), round(v_wr_l_ob, 2)])

        except Exception as e: pass

    # ==========================================
    # 4. 寫入 Google 試算表 (💡 新增功能)
    # ==========================================
    gcp_creds_str = os.environ.get("GCP_CREDENTIALS")
    sheet_url = os.environ.get("SHEET_URL")

    if gcp_creds_str and sheet_url and sheet_data_to_append:
        print("📝 開始將結果寫入 Google 試算表...")
        try:
            creds_dict = json.loads(gcp_creds_str)
            credentials = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
            )
            gc = gspread.authorize(credentials)
            sheet = gc.open_by_url(sheet_url).sheet1
            
            # 將資料整批新增到最後一行
            sheet.append_rows(sheet_data_to_append, value_input_option='USER_ENTERED')
            print(f"✅ 成功寫入 {len(sheet_data_to_append)} 筆資料至 Google 試算表！")
        except Exception as e:
            print(f"❌ 寫入 Google 試算表失敗: {e}")
    else:
        print("⚠️ 未設定 Google 憑證/網址，或今日無資料，跳過試算表寫入。")

    # ==========================================
    # 5. 寄發 Email 邏輯
    # ==========================================
    html_content = f"<h2>📈 每日台股量化掃描通報 ({date_str})</h2>"
    
    html_content += "<h3 style='color: green;'>🟢 超賣 (逢低買進) 符合標的：</h3>"
    html_content += f"<ul>{''.join(os_results)}</ul>" if os_results else "<p>今日無符合條件標的。</p>"
    html_content += "<hr>"
    html_content += "<h3 style='color: red;'>🔴 超買 (逢高賣出) 符合標的：</h3>"
    html_content += f"<ul>{''.join(ob_results)}</ul>" if ob_results else "<p>今日無符合條件標的。</p>"

    sender_email = os.environ.get("SENDER_EMAIL")
    app_password = os.environ.get("APP_PASSWORD")
    receiver_email = os.environ.get("RECEIVER_EMAIL")

    if not sender_email or not app_password or not receiver_email: return

    clean_receivers = receiver_email.replace("，", ",").replace(";", ",")
    receiver_list = [e.strip() for e in clean_receivers.split(",") if e.strip()]

    msg = MIMEMultipart("alternative")
    msg['Subject'] = f"📈 每日雙向掃描報告：超賣 {len(os_results)} 檔 / 超買 {len(ob_results)} 檔"
    msg['From'] = sender_email
    msg['To'] = ", ".join(receiver_list) 
    msg.attach(MIMEText(html_content, 'html'))

    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(sender_email, app_password)
        server.sendmail(sender_email, receiver_list, msg.as_string())
        server.quit()
        print(f"✅ 信件發送成功！已寄送至：{receiver_email}")
    except Exception as e:
        print(f"❌ 寄信失敗: {e}")

if __name__ == "__main__":
    main()
