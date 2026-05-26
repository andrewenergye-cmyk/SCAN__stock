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
# 1. 設定區（集中管理，方便修改）
# ==========================================
STOCK_CSV  = "default_stocks.csv"
ETF_CSV    = "default_etf.csv"
CONFIG_FILE = "strategy_config.json"
FETCH_DAYS  = 150   # 抓取歷史天數
MIN_ROWS    = 30    # 最少需要幾根 K 棒才進行計算

# ==========================================
# 2. 核心指標計算
# ==========================================
def calculate_williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """計算 Williams %R"""
    highest_high = high.rolling(window=period).max()
    lowest_low   = low.rolling(window=period).min()
    denom = highest_high - lowest_low
    denom = denom.replace(0, float('nan'))   # 避免除以零
    return ((highest_high - close) / denom) * -100

# ==========================================
# 3. 讀取股票/ETF 清單
# ==========================================
def load_targets(filepath: str) -> list[dict]:
    """
    讀取 CSV 股票/ETF 清單，支援 UTF-8-BOM 與 Big5 編碼。
    回傳: [{"clean": "0050", "yf": "0050.TW", "name": "元大台灣50"}, ...]
    """
    if not os.path.exists(filepath):
        print(f"⚠️  找不到清單檔案: {filepath}，略過。")
        return []

    try:
        df_csv = pd.read_csv(filepath, encoding='utf-8-sig', dtype=str)
    except Exception:
        df_csv = pd.read_csv(filepath, encoding='big5', dtype=str)

    targets = []
    for _, row in df_csv.iterrows():
        symbol = str(row.get('代號', '')).strip()
        if symbol.endswith('.0'):
            symbol = symbol[:-2]
        if not symbol or symbol.lower() == 'nan':
            continue

        # 純數字且位數不足時補前置零（台股常見 4 位代碼）
        if symbol.isdigit() and len(symbol) < 4:
            symbol = "00" + symbol

        market = str(row.get('市場', '')).strip()
        name   = str(row.get('名稱', '')).strip()
        yf_symbol = f"{symbol}.TWO" if '櫃' in market else f"{symbol}.TW"

        targets.append({"clean": symbol, "yf": yf_symbol, "name": name})

    print(f"📋 {filepath} 共讀取 {len(targets)} 檔")
    return targets

# ==========================================
# 4. 讀取策略參數（JSON）
# ==========================================
def load_config(filepath: str) -> dict:
    """
    讀取策略參數設定檔，若不存在則使用預設值。
    """
    defaults = {
        "oversold":  {"wr_s_d": 7,  "wr_s_t": -90.0, "wr_l_d": 30, "wr_l_t": -60.0},
        "overbought": {"wr_s_d": 7, "wr_s_t": -10.0, "wr_l_d": 30, "wr_l_t": -20.0},
    }
    if not os.path.exists(filepath):
        print(f"⚠️  找不到設定檔 {filepath}，使用預設參數。")
        return defaults

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            config = json.load(f)
        # 補齊缺少的 key
        for key in defaults:
            if key not in config:
                config[key] = defaults[key]
        return config
    except Exception as e:
        print(f"⚠️  讀取設定檔失敗: {e}，使用預設參數。")
        return defaults

# ==========================================
# 5. 下載單一標的 K 線資料（附備援邏輯）
# ==========================================
def fetch_ohlcv(stock: dict, start_date: datetime.date, end_date: datetime.date) -> pd.DataFrame:
    """
    下載 yfinance 歷史資料；若資料不足，嘗試補前置零後重試。
    回傳 DataFrame（可能為空）。
    """
    df = yf.Ticker(stock['yf']).history(start=start_date, end=end_date)

    # 備援：補前置零後重試（例如代碼 '91' → '0091'）
    if (df.empty or len(df) < MIN_ROWS) and len(stock['clean']) == 4 and stock['clean'].isdigit():
        alt_clean  = "00" + stock['clean']
        suffix     = ".TWO" if ".TWO" in stock['yf'] else ".TW"
        alt_yf     = f"{alt_clean}{suffix}"
        df_alt     = yf.Ticker(alt_yf).history(start=start_date, end=end_date)
        if not df_alt.empty and len(df_alt) >= MIN_ROWS:
            stock['clean'] = alt_clean
            stock['yf']    = alt_yf
            df = df_alt

    return df

# ==========================================
# 6. 格式化單筆結果（HTML li 與 Sheet 列）
# ==========================================
def _consecutive_tag(is_3: bool) -> str:
    if is_3:
        return ("<span style='background-color:#ffd700;color:#000;"
                "padding:2px 6px;border-radius:4px;font-size:11px;"
                "font-weight:bold;'>🏆 連3日</span> ")
    return ""

def build_html_row(stock: dict, price: float, wr_s: float, wr_l: float,
                   is_3: bool, direction: str) -> str:
    """產生 Email HTML 清單項目"""
    color   = "green" if direction == "oversold" else "red"
    tag_str = _consecutive_tag(is_3)
    return (
        f"<li style='margin-bottom:8px;font-size:16px;'>"
        f"<b>{stock['clean']} {stock['name']}</b> {tag_str}"
        f"➔ 💰 <b style='color:{color};'>收盤價: {price:.2f}</b> "
        f"| 短W%%R: {wr_s:.2f} | 長W%%R: {wr_l:.2f} "
        f"<a href='https://tw.stock.yahoo.com/quote/{stock['clean']}'>[Yahoo資訊]</a>"
        f"</li>"
    )

def build_sheet_row(date_str: str, mode: str, stock: dict,
                    price: float, wr_s: float, wr_l: float) -> list:
    """產生要寫入 Google Sheets 的一列"""
    return [date_str, mode, stock['clean'], stock['name'],
            round(price, 2), round(wr_s, 2), round(wr_l, 2)]

# ==========================================
# 7. 核心掃描函式（股票 & ETF 共用）
# ==========================================
def scan_targets(
    targets: list[dict],
    config: dict,
    start_date: datetime.date,
    end_date: datetime.date,
    date_str: str,
    label: str = ""          # 標籤，例如 "股票" / "ETF"
) -> tuple[list, list, list]:
    """
    掃描傳入的標的清單，判斷超賣/超買。

    回傳:
        os_html  : 超賣的 HTML 列表字串 list
        ob_html  : 超買的 HTML 列表字串 list
        sheet_rows: 要寫入試算表的列 list
    """
    os_cfg = config["oversold"]
    ob_cfg = config["overbought"]

    os_s_d, os_s_t = int(os_cfg["wr_s_d"]), float(os_cfg["wr_s_t"])
    os_l_d, os_l_t = int(os_cfg["wr_l_d"]), float(os_cfg["wr_l_t"])
    ob_s_d, ob_s_t = int(ob_cfg["wr_s_d"]), float(ob_cfg["wr_s_t"])
    ob_l_d, ob_l_t = int(ob_cfg["wr_l_d"]), float(ob_cfg["wr_l_t"])

    os_html, ob_html, sheet_rows = [], [], []

    for stock in targets:
        prefix = f"[{label}] " if label else ""
        print(f"  掃描中: {prefix}{stock['clean']} {stock['name']}")
        try:
            df = fetch_ohlcv(stock, start_date, end_date)
            if df.empty or len(df) < MIN_ROWS:
                print(f"    ⚠️  資料不足，略過。")
                continue

            price = float(df['Close'].iloc[-1])
            high, low, close = df['High'], df['Low'], df['Close']

            # --- 計算指標 ---
            wr_s_os = calculate_williams_r(high, low, close, os_s_d)
            wr_l_os = calculate_williams_r(high, low, close, os_l_d)
            # 若超賣與超買的週期相同，直接重用（避免重複運算）
            wr_s_ob = wr_s_os if ob_s_d == os_s_d else calculate_williams_r(high, low, close, ob_s_d)
            wr_l_ob = wr_l_os if ob_l_d == os_l_d else calculate_williams_r(high, low, close, ob_l_d)

            # --- 判斷超賣 ---
            today_s_os = float(wr_s_os.iloc[-1])
            today_l_os = float(wr_l_os.iloc[-1])

            if today_s_os < os_s_t and today_l_os < os_l_t:
                is_3 = (
                    len(wr_s_os) >= 3
                    and (wr_s_os.iloc[-3:] < os_s_t).all()
                    and (wr_l_os.iloc[-3:] < os_l_t).all()
                )
                mode_str = "超賣(連3)" if is_3 else "超賣"
                os_html.append(build_html_row(stock, price, today_s_os, today_l_os, is_3, "oversold"))
                sheet_rows.append(build_sheet_row(date_str, f"{label}-{mode_str}" if label else mode_str,
                                                  stock, price, today_s_os, today_l_os))
                print(f"    ✅ 超賣命中！{'(連3日)' if is_3 else ''}")

            # --- 判斷超買 ---
            today_s_ob = float(wr_s_ob.iloc[-1])
            today_l_ob = float(wr_l_ob.iloc[-1])

            if today_s_ob > ob_s_t and today_l_ob > ob_l_t:
                is_3 = (
                    len(wr_s_ob) >= 3
                    and (wr_s_ob.iloc[-3:] > ob_s_t).all()
                    and (wr_l_ob.iloc[-3:] > ob_l_t).all()
                )
                mode_str = "超買(連3)" if is_3 else "超買"
                ob_html.append(build_html_row(stock, price, today_s_ob, today_l_ob, is_3, "overbought"))
                sheet_rows.append(build_sheet_row(date_str, f"{label}-{mode_str}" if label else mode_str,
                                                  stock, price, today_s_ob, today_l_ob))
                print(f"    ✅ 超買命中！{'(連3日)' if is_3 else ''}")

        except Exception as e:
            print(f"    ❌ {stock['clean']} 掃描失敗: {type(e).__name__}: {e}")

    return os_html, ob_html, sheet_rows

# ==========================================
# 8. 寫入 Google Sheets
# ==========================================
def write_to_gsheet(sheet_rows: list) -> None:
    """將掃描結果批次寫入 Google 試算表（自動避免重複日期寫入）"""
    gcp_creds_str = os.environ.get("GCP_CREDENTIALS")
    sheet_url     = os.environ.get("SHEET_URL")

    if not gcp_creds_str or not sheet_url:
        print("⚠️  未設定 GCP_CREDENTIALS 或 SHEET_URL，跳過試算表寫入。")
        return

    if not sheet_rows:
        print("⚠️  今日無命中資料，跳過試算表寫入。")
        return

    print("📝 開始將結果寫入 Google 試算表...")
    try:
        creds_dict  = json.loads(gcp_creds_str)
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive"]
        )
        gc    = gspread.authorize(credentials)
        sheet = gc.open_by_url(sheet_url).sheet1

        # 取得最後一筆日期，防止重複寫入
        all_values  = sheet.get_all_values()
        today_str   = sheet_rows[0][0]
        existing    = {row[0] for row in all_values[1:] if row}  # 跳過標題列

        if today_str in existing:
            print(f"⚠️  {today_str} 資料已存在，跳過重複寫入。")
            return

        sheet.append_rows(sheet_rows, value_input_option='USER_ENTERED')
        print(f"✅ 成功寫入 {len(sheet_rows)} 筆資料！")

    except Exception as e:
        print(f"❌ 寫入 Google 試算表失敗: {type(e).__name__}: {e}")

# ==========================================
# 9. 組合 Email HTML 內容
# ==========================================
def build_email_html(
    date_str: str,
    os_stock: list, ob_stock: list,
    os_etf:   list, ob_etf:   list
) -> str:
    """組合完整的 Email HTML 字串"""

    def section(title: str, color: str, items: list) -> str:
        rows = f"<ul>{''.join(items)}</ul>" if items else "<p>今日無符合條件標的。</p>"
        return f"<h3 style='color:{color};'>{title}</h3>{rows}<hr>"

    html = f"<h2>📈 每日台股自動掃描通報 ({date_str})</h2>"
    html += section("🟢 股票 — 超賣 (波段低)", "green",    os_stock)
    html += section("🔴 股票 — 超買 (波段高)", "red",      ob_stock)
    html += section("🔵 ETF  — 超賣 (波段低)", "#1a7abf",  os_etf)
    html += section("🟠 ETF  — 超買 (波段高)", "#e67e22",  ob_etf)
    return html

# ==========================================
# 10. 寄發 Email
# ==========================================
def send_email(html_content: str, os_stock: list, ob_stock: list,
               os_etf: list, ob_etf: list) -> None:
    """透過 Gmail SMTP SSL 寄發 HTML Email"""
    sender_email   = os.environ.get("SENDER_EMAIL")
    app_password   = os.environ.get("APP_PASSWORD")
    receiver_email = os.environ.get("RECEIVER_EMAIL")

    if not all([sender_email, app_password, receiver_email]):
        print("⚠️  Email 環境變數未完整設定，跳過寄信。")
        return

    clean_receivers = receiver_email.replace("，", ",").replace(";", ",")
    receiver_list   = [e.strip() for e in clean_receivers.split(",") if e.strip()]

    total_os = len(os_stock) + len(os_etf)
    total_ob = len(ob_stock) + len(ob_etf)
    subject  = (
        f"📈 每日雙向掃描報告｜"
        f"股票 超賣{len(os_stock)}/超買{len(ob_stock)} | "
        f"ETF 超賣{len(os_etf)}/超買{len(ob_etf)} | "
        f"共 {total_os + total_ob} 檔"
    )

    msg = MIMEMultipart("alternative")
    msg['Subject'] = subject
    msg['From']    = sender_email
    msg['To']      = ", ".join(receiver_list)
    msg.attach(MIMEText(html_content, 'html'))

    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(sender_email, app_password)
        server.sendmail(sender_email, receiver_list, msg.as_string())
        server.quit()
        print(f"✅ 信件發送成功！已寄送至：{receiver_email}")
    except Exception as e:
        print(f"❌ 寄信失敗: {type(e).__name__}: {e}")

# ==========================================
# 11. 主程式入口
# ==========================================
def main():
    print("🤖 啟動每日自動量化掃描腳本（股票 + ETF 雙向掃描）...")
    print("=" * 60)

    # 日期
    end_date   = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=FETCH_DAYS)
    date_str   = end_date.strftime('%Y/%m/%d')

    # 讀取清單 & 設定
    stock_targets = load_targets(STOCK_CSV)
    etf_targets   = load_targets(ETF_CSV)
    config        = load_config(CONFIG_FILE)

    if not stock_targets and not etf_targets:
        print("❌ 股票與 ETF 清單皆為空，腳本結束。")
        return

    # 掃描
    print("\n📊 開始掃描股票...")
    os_stock, ob_stock, sheet_stock = scan_targets(
        stock_targets, config, start_date, end_date, date_str, label="股票"
    )

    print("\n📊 開始掃描 ETF...")
    os_etf, ob_etf, sheet_etf = scan_targets(
        etf_targets, config, start_date, end_date, date_str, label="ETF"
    )

    # 彙總結果
    all_sheet_rows = sheet_stock + sheet_etf
    print(f"\n📌 掃描完成 | 股票超賣:{len(os_stock)} 超買:{len(ob_stock)} "
          f"| ETF超賣:{len(os_etf)} 超買:{len(ob_etf)}")

    # 寫入 Google Sheets
    print("\n" + "=" * 60)
    write_to_gsheet(all_sheet_rows)

    # 組合並寄出 Email
    print("\n" + "=" * 60)
    html = build_email_html(date_str, os_stock, ob_stock, os_etf, ob_etf)
    send_email(html, os_stock, ob_stock, os_etf, ob_etf)

    print("\n✅ 全部流程完成！")


if __name__ == "__main__":
    main()
