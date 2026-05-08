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
    print("🤖 啟動每日自動量化掃描腳本...")
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
    # 2. 讀取雲端連動的策略參數 (針對「超賣」模式)
    # ==========================================
    config_file = "strategy_config.json"
    try:
        with open(config_file, "r") as f:
            full_config = json.load(f)
            # 指定抓取 "oversold" (超賣) 的參數包
            config = full_config.get("oversold", {}) 
            print("✅ 成功讀取 GitHub 雲端策略參數 (超賣模式)！")
    except Exception as e:
        print(f"⚠️ 找不到自訂參數檔 ({e})，將使用系統預設值。")
        config = {}

    # 套用參數 (若無自訂值，則給予預設值)
    wr_s_d = config.get("wr_s_d", 7)
    wr_s_t = config.get("wr_s_t", -90.0)
    wr_l_d = config.get("wr_l_d", 30)
    wr_l_t = config.get("wr_l_t", -60.0)
    
    print(f"📊 今日掃描條件：短W%R({wr_s_d}天) < {wr_s_t} 且 長W%R({wr_l_d}天) < {wr_l_t}")

    # ==========================================
    # 3. 開始執行掃描
    # ==========================================
    end_date = datetime.date.today()
    # 往前多抓一些天數以利計算長天期指標
    start_date = end_date - datetime.timedelta(days=150) 
    
    results = []
    
    for stock in targets:
        try:
            print(f"掃描中: {stock['clean']} {stock['name']}")
            df = yf.Ticker(stock['yf']).history(start=start_date, end=end_date)
            
            # 智慧重試 (針對被砍掉 .TWO/.TW 尾碼的純數字 ETF)
            if (df.empty or len(df) < 30) and len(stock['clean']) == 4 and stock['clean'].isdigit():
                alt_clean = "00" + stock['clean']
                alt_yf = f"{alt_clean}.TWO" if ".TWO" in stock['yf'] else f"{alt_clean}.TW"
                df_alt = yf.Ticker(alt_yf).history(start=start_date, end=end_date)
                if not df_alt.empty and len(df_alt) >= 30:
                    df = df_alt
                    stock['clean'] = alt_clean

            if df.empty or len(df) < 30: 
                continue
            
            # 計算威廉指標
            df['WR_S'] = calculate_williams_r(df['High'], df['Low'], df['Close'], int(wr_s_d))
            df['WR_L'] = calculate_williams_r(df['High'], df['Low'], df['Close'], int(wr_l_d))
            
            latest = df.iloc[-1]
            v_wr_s, v_wr_l = float(latest['WR_S']), float(latest['WR_L'])
            current_price = float(latest['Close'])
            
            # 判斷是否符合條件
            if v_wr_s < float(wr_s_t) and v_wr_l < float(wr_l_t):
                results.append(f"<li><b>{stock['clean']} {stock['name']}</b> - 收盤: {current_price:.2f} | 短W%R: {v_wr_s:.2f} | 長W%R: {v_wr_l:.2f}</li>")
                print(f"  👉 [符合條件] {stock['clean']}")
        except Exception as e:
            pass # 忽略抓不到資料的廢棄股票

    # ==========================================
    # 4. 寄發 Email 邏輯
    # ==========================================
    if not results:
        html_content = "<h3>本日量化掃描完成</h3><p>今日無符合「威廉指標超賣」設定條件的標的。</p>"
    else:
        html_content = f"<h3>本日量化掃描完成，共 {len(results)} 檔符合條件：</h3><ul>" + "".join(results) + "</ul>"
        html_content += "<br><p>前往 Yahoo 股市查看：</p><ul>"
        for stock_html in results:
             symbol = stock_html.split("<b>")[1].split(" ")[0]
             html_content += f'<li><a href="https://tw.stock.yahoo.com/quote/{symbol}">{symbol} 資訊</a></li>'
        html_content += "</ul>"

    # 從環境變數 (GitHub Secrets) 取得金鑰
    sender_email = os.environ.get("SENDER_EMAIL")
    app_password = os.environ.get("APP_PASSWORD")
    receiver_email = os.environ.get("RECEIVER_EMAIL")

    if not sender_email or not app_password or not receiver_email:
        print("❌ 未設定 Email 環境變數 (Secrets)，跳過寄信階段。")
        return

    # 將逗號隔開的多個信箱轉換為 Python 陣列
    receiver_list = [email.strip() for email in receiver_email.split(",")]

    msg = MIMEMultipart("alternative")
    msg['Subject'] = f"📈 每日台股量化掃描通報 ({end_date.strftime('%Y-%m-%d')})"
    msg['From'] = sender_email
    msg['To'] = receiver_email  # 這裡放字串讓收件人欄位顯示所有人
    msg.attach(MIMEText(html_content, 'html'))

    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(sender_email, app_password)
        # 這裡放入 receiver_list 陣列，確保每個人都收到
        server.sendmail(sender_email, receiver_list, msg.as_string())
        server.quit()
        print(f"✅ 掃描結果信件發送成功！已寄送至：{receiver_email}")
    except Exception as e:
        print(f"❌ 寄信失敗: {e}")

if __name__ == "__main__":
    main()
