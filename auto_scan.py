import yfinance as yf
import pandas as pd
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os

# --- 核心指標計算函式 ---
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

def main():
    print("啟動自動掃描腳本...")
    default_file = "default_stocks.csv"
    
    if not os.path.exists(default_file):
        print(f"找不到 {default_file}，腳本結束。")
        return

    # 讀取名單
    try:
        df_csv = pd.read_csv(default_file, encoding='utf-8-sig', dtype=str)
    except:
        df_csv = pd.read_csv(default_file, encoding='big5', dtype=str)
        
    targets = []
    for _, row in df_csv.iterrows():
        symbol = str(row.get('代號', '')).strip()
        if symbol.endswith('.0'): symbol = symbol[:-2]
        if not symbol or symbol.lower() == 'nan': continue
        if symbol.isdigit() and len(symbol) < 4:
            symbol = "00" + symbol
            
        market = str(row.get('市場', '')).strip()
        name = str(row.get('名稱', '')).strip()
        yf_symbol = f"{symbol}.TWO" if '櫃' in market else f"{symbol}.TW"
        targets.append({"clean": symbol, "yf": yf_symbol, "name": name})

    # --- 💡 新增：讀取雲端連動的策略參數 ---
    try:
        with open("strategy_config.json", "r") as f:
            config = json.load(f)
            print("✅ 成功讀取自訂策略參數檔！")
    except:
        print("⚠️ 找不到自訂參數檔，使用系統預設值。")
        config = {}

    # 套用參數 (如果 JSON 裡沒有，就退回後面的預設值)
    wr_s_d = config.get("wr_s_d", 7)
    wr_s_t = config.get("wr_s_t", -90.0)
    wr_l_d = config.get("wr_l_d", 30)
    wr_l_t = config.get("wr_l_t", -60.0)
    
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=150)
    
    results = []
    
    # --- 掃描參數設定 (此處預設為超賣模式，威廉小於-60) ---
    wr_s_d, wr_s_t = 7, -90.0
    wr_l_d, wr_l_t = 30, -60.0
    
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=150)
    
    results = []
    
    for stock in targets:
        try:
            print(f"掃描中: {stock['clean']}")
            df = yf.Ticker(stock['yf']).history(start=start_date, end=end_date)
            
            # 智慧重試 (針對ETF)
            if (df.empty or len(df) < 30) and len(stock['clean']) == 4 and stock['clean'].isdigit():
                alt_clean = "00" + stock['clean']
                alt_yf = f"{alt_clean}.TWO" if ".TWO" in stock['yf'] else f"{alt_clean}.TW"
                df_alt = yf.Ticker(alt_yf).history(start=start_date, end=end_date)
                if not df_alt.empty and len(df_alt) >= 30:
                    df = df_alt
                    stock['clean'] = alt_clean

            if df.empty or len(df) < 30: continue
            
            df['WR_S'] = calculate_williams_r(df['High'], df['Low'], df['Close'], wr_s_d)
            df['WR_L'] = calculate_williams_r(df['High'], df['Low'], df['Close'], wr_l_d)
            
            latest = df.iloc[-1]
            v_wr_s, v_wr_l = float(latest['WR_S']), float(latest['WR_L'])
            current_price = float(latest['Close'])
            
            # 條件：短天期與長天期威廉皆符合超賣
            if v_wr_s < wr_s_t and v_wr_l < wr_l_t:
                results.append(f"<li><b>{stock['clean']} {stock['name']}</b> - 收盤: {current_price:.2f} | 短W%R: {v_wr_s:.2f} | 長W%R: {v_wr_l:.2f}</li>")
        except Exception as e:
            pass

# --- 寄發 Email 邏輯 ---
    if not results:
        html_content = "<h3>本日量化掃描完成</h3><p>今日無符合「威廉指標超賣」條件的標的。</p>"
    else:
        html_content = f"<h3>本日量化掃描完成，共 {len(results)} 檔符合條件：</h3><ul>" + "".join(results) + "</ul>"
        html_content += "<br><p>前往 Yahoo 股市查看：</p><ul>"
        for stock in results:
             symbol = stock.split("<b>")[1].split(" ")[0]
             html_content += f'<li><a href="https://tw.stock.yahoo.com/quote/{symbol}">{symbol} 資訊</a></li>'
        html_content += "</ul>"

    sender_email = os.environ.get("SENDER_EMAIL")
    app_password = os.environ.get("APP_PASSWORD")
    receiver_email = os.environ.get("RECEIVER_EMAIL")

    if not sender_email or not app_password or not receiver_email:
        print("未設定 Email 環境變數，跳過寄信階段。")
        print(html_content)
        return

    # 💡 新增邏輯：將用逗號隔開的多個信箱，轉換成 Python 陣列名單
    receiver_list = [email.strip() for email in receiver_email.split(",")]

    msg = MIMEMultipart("alternative")
    msg['Subject'] = f"📈 每日台股量化掃描通報 ({end_date.strftime('%Y-%m-%d')})"
    msg['From'] = sender_email
    
    # 信件標頭的收件者顯示 (多個人可以用逗號連在一起顯示)
    msg['To'] = receiver_email 
    
    msg.attach(MIMEText(html_content, 'html'))

    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(sender_email, app_password)
        
        # 寄信指令：這裡必須傳入 receiver_list (名單陣列)，才能正確派發給所有人
        server.sendmail(sender_email, receiver_list, msg.as_string())
        
        server.quit()
        print(f"✅ 掃描結果信件發送成功！已寄送至：{receiver_email}")
    except Exception as e:
        print(f"❌ 寄信失敗: {e}")

if __name__ == "__main__":
    main()
