# -*- coding: utf-8 -*-
"""
Stock Chart Local Web App
--------------------------
Đọc 3 file dữ liệu CafeF (HNX, HSX, UPCOM), gộp lại và phục vụ qua một
web app Flask + Plotly.js chạy local, cho phép xem biểu đồ nến (candlestick)
+ khối lượng cho từng mã (ticker) và chuyển đổi qua lại giữa các mã.

Chạy:
    python app.py
Sau đó mở trình duyệt: http://127.0.0.1:5000
"""

import os
import glob
import re
import time
import datetime
from zoneinfo import ZoneInfo
import requests
import pandas as pd
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# ----------------------------------------------------------------------
# CẤU HÌNH
# ----------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Map: tên file (glob pattern) -> tên sàn (Exchange)
# Có thể sửa lại pattern nếu bạn đổi tên file.
FILE_PATTERNS = {
    "HNX": "*HNX*.csv",
    "HSX": "*HSX*.csv",
    "UPCOM": "*UPCOM*.csv",
}

# Định dạng cột ngày trong file gốc là <DTYYYYMMDD> (vd: 20260807 = 2026-08-07)
DATE_FORMAT = "%Y%m%d"

# ----------------------------------------------------------------------
# NẠP DỮ LIỆU
# ----------------------------------------------------------------------

def _find_file(pattern: str) -> str | None:
    matches = glob.glob(os.path.join(DATA_DIR, pattern))
    return matches[0] if matches else None


def load_all_data() -> pd.DataFrame:
    """Đọc và gộp 3 file CSV (HNX, HSX, UPCOM) thành 1 DataFrame duy nhất."""
    frames = []
    for exchange, pattern in FILE_PATTERNS.items():
        path = _find_file(pattern)
        if not path:
            print(f"[WARN] Không tìm thấy file cho {exchange} (pattern={pattern}) trong {DATA_DIR}")
            continue
        df = pd.read_csv(path)
        # Chuẩn hoá tên cột: <Ticker>,<DTYYYYMMDD>,<Open>,<High>,<Low>,<Close>,<Volume>
        df.columns = [c.strip("<>") for c in df.columns]
        df = df.rename(columns={
            "Ticker": "Ticker",
            "DTYYYYMMDD": "Date",
            "Open": "Open",
            "High": "High",
            "Low": "Low",
            "Close": "Close",
            "Volume": "Volume",
        })
        df["Exchange"] = exchange
        frames.append(df)

    if not frames:
        raise FileNotFoundError(
            f"Không tìm thấy file dữ liệu nào trong {DATA_DIR}. "
            f"Hãy đặt 3 file CafeF_HNX*.csv, CafeF_HSX*.csv, CafeF_UPCOM*.csv vào thư mục 'data/'."
        )

    all_df = pd.concat(frames, ignore_index=True)
    all_df = all_df.dropna(subset=["Ticker"])
    all_df["Ticker"] = all_df["Ticker"].astype(str).str.strip().str.upper()
    all_df["Date"] = pd.to_datetime(all_df["Date"], format=DATE_FORMAT)

    # Loại bỏ các dòng mã CHỈ SỐ lẫn trong file gốc (không phải mã giao dịch
    # thật, CafeF vẫn để chung file: HN0-INDEX, VNX-ALL, VNXALL...)
    all_df = all_df[~all_df["Ticker"].str.contains("INDEX", na=False)]
    all_df = all_df[~all_df["Ticker"].isin(["VNX-ALL", "VNXALL"])]

    all_df["Type"] = all_df["Ticker"].apply(classify_ticker)

    all_df = all_df.sort_values(["Ticker", "Date"]).reset_index(drop=True)
    return all_df


# Mã chứng quyền có bảo đảm (CW) trên HOSE/HNX có dạng: 1 chữ cái đầu (thường
# là C) + 2-3 ký tự mã cơ sở + 4 số (kỳ đáo hạn), độ dài tổng luôn là 8.
# VD: CACB2101, CFPT2418...
_WARRANT_RE = re.compile(r"^C[A-Z0-9]{2,3}\d{4}$")


def classify_ticker(ticker: str) -> str:
    """Phân loại mã: STOCK (cổ phiếu) / WARRANT (chứng quyền) / FUND (quỹ, ETF)."""
    if _WARRANT_RE.match(ticker):
        return "WARRANT"
    if len(ticker) >= 6:
        # Các mã quỹ/ETF hiện tại đều có độ dài >=6 (E1VFVN30, FUEVFVND...)
        return "FUND"
    return "STOCK"


print("Đang nạp dữ liệu, vui lòng chờ...")
DF = load_all_data()
_type_counts = DF.drop_duplicates("Ticker")["Type"].value_counts().to_dict()
print(
    f"Đã nạp {len(DF):,} dòng dữ liệu, {DF['Ticker'].nunique():,} mã "
    f"(Cổ phiếu: {_type_counts.get('STOCK', 0):,} · "
    f"Quỹ/ETF: {_type_counts.get('FUND', 0):,} · "
    f"Chứng quyền: {_type_counts.get('WARRANT', 0):,})."
)

# Danh sách mã kèm sàn + thông tin phiên gần nhất (dùng cho ô tìm kiếm / sidebar)
_tail2 = DF.sort_values("Date").groupby("Ticker").tail(2)
_last_rows = _tail2.groupby("Ticker").tail(1).set_index("Ticker")
_prev_rows = _tail2.groupby("Ticker").head(1).set_index("Ticker")
# Với mã chỉ có 1 phiên, prev = last (change = 0)
_prev_close = _prev_rows["Close"].reindex(_last_rows.index).fillna(_last_rows["Close"])

_sidebar_df = _last_rows[["Exchange", "Close", "Date", "Volume", "Type"]].copy()
_sidebar_df["PrevClose"] = _prev_close
_sidebar_df["Change"] = _sidebar_df["Close"] - _sidebar_df["PrevClose"]
_sidebar_df["ChangePct"] = (_sidebar_df["Change"] / _sidebar_df["PrevClose"].replace(0, pd.NA) * 100).fillna(0)
_sidebar_df = _sidebar_df.reset_index().sort_values("Ticker")

TICKER_LIST = _sidebar_df.to_dict(orient="records")
for row in TICKER_LIST:
    row["Date"] = row["Date"].strftime("%Y-%m-%d")
    row["Change"] = round(row["Change"], 2)
    row["ChangePct"] = round(row["ChangePct"], 2)

# Gom dữ liệu theo từng mã để tra cứu nhanh khi đổi ticker
GROUPED = {ticker: g for ticker, g in DF.groupby("Ticker")}


# ----------------------------------------------------------------------
# ROUTES
# ----------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/tickers")
def api_tickers():
    """Trả về danh sách toàn bộ mã (dùng cho ô tìm kiếm / autocomplete)."""
    return jsonify(TICKER_LIST)


@app.route("/api/ohlc/<ticker>")
def api_ohlc(ticker: str):
    """Trả về dữ liệu OHLCV của 1 mã, có thể lọc theo khoảng thời gian.

    Query params (tuỳ chọn):
        range = 1M | 3M | 6M | 1Y | 5Y | ALL  (mặc định ALL)
    """
    ticker = ticker.strip().upper()
    if ticker not in GROUPED:
        return jsonify({"error": f"Không tìm thấy mã '{ticker}'"}), 404

    g = GROUPED[ticker]

    rng = request.args.get("range", "ALL").upper()
    if rng != "ALL" and len(g):
        last_date = g["Date"].max()
        offsets = {
            "1M": pd.DateOffset(months=1),
            "3M": pd.DateOffset(months=3),
            "6M": pd.DateOffset(months=6),
            "1Y": pd.DateOffset(years=1),
            "5Y": pd.DateOffset(years=5),
        }
        if rng in offsets:
            start = last_date - offsets[rng]
            g = g[g["Date"] >= start]

    data = {
        "ticker": ticker,
        "exchange": g["Exchange"].iloc[0] if len(g) else None,
        "date": g["Date"].dt.strftime("%Y-%m-%d").tolist(),
        "open": g["Open"].tolist(),
        "high": g["High"].tolist(),
        "low": g["Low"].tolist(),
        "close": g["Close"].tolist(),
        "volume": g["Volume"].tolist(),
    }
    return jsonify(data)


# ----------------------------------------------------------------------
# DỮ LIỆU TRỰC TUYẾN (real-time trong phiên)
# ----------------------------------------------------------------------
# LƯU Ý: CafeF không có API giá thời gian thực miễn phí công khai. Các endpoint
# dưới đây là API KHÔNG CHÍNH THỨC mà nhiều dashboard cá nhân hay dùng để lấy
# giá khớp lệnh trong phiên - có thể thay đổi hoặc ngừng hoạt động bất cứ lúc
# nào mà không báo trước. App sẽ thử lần lượt từng nguồn, nguồn nào lỗi thì bỏ
# qua và thử nguồn kế tiếp. Nếu cả 2 đều lỗi, cách ổn định lâu dài là đăng ký
# API chính thức từ 1 công ty chứng khoán (SSI FastConnect, VNDirect, DNSE...),
# thường miễn phí khi mở tài khoản giao dịch.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


def _fetch_entrade(ticker, start_ts, end_ts):
    url = "https://services.entrade.com.vn/chart-api/v2/ohlc/stock"
    params = {"symbol": ticker, "resolution": "1", "from": start_ts, "to": end_ts}
    headers = {**_BROWSER_HEADERS, "Referer": "https://banggia.dnse.com.vn/"}
    resp = requests.get(url, params=params, headers=headers, timeout=5)
    resp.raise_for_status()
    raw = resp.json()
    return raw.get("t") or [], raw.get("o") or [], raw.get("h") or [], raw.get("l") or [], raw.get("c") or [], raw.get("v") or []


def _fetch_vndirect(ticker, start_ts, end_ts):
    url = "https://dchart-api.vndirect.com.vn/dchart/history"
    params = {"symbol": ticker, "resolution": "1", "from": start_ts, "to": end_ts}
    headers = {**_BROWSER_HEADERS, "Referer": "https://dstock.vndirect.com.vn/"}
    resp = requests.get(url, params=params, headers=headers, timeout=5)
    resp.raise_for_status()
    raw = resp.json()
    return raw.get("t") or [], raw.get("o") or [], raw.get("h") or [], raw.get("l") or [], raw.get("c") or [], raw.get("v") or []


LIVE_PROVIDERS = [
    ("Entrade/DNSE", _fetch_entrade),
    ("VNDirect", _fetch_vndirect),
]

# Giờ giao dịch HOSE/HNX/UPCOM (giờ Việt Nam), thứ 2 - thứ 6
MORNING_SESSION = (datetime.time(9, 0), datetime.time(11, 30))
AFTERNOON_SESSION = (datetime.time(13, 0), datetime.time(14, 45))


def is_market_open(now: datetime.datetime) -> bool:
    if now.weekday() >= 5:  # 5=Sat, 6=Sun
        return False
    t = now.time()
    in_morning = MORNING_SESSION[0] <= t <= MORNING_SESSION[1]
    in_afternoon = AFTERNOON_SESSION[0] <= t <= AFTERNOON_SESSION[1]
    return in_morning or in_afternoon


@app.route("/api/live/<ticker>")
def api_live(ticker: str):
    """Proxy lấy giá khớp lệnh trong phiên cho 1 mã (dữ liệu 1 phút, trong ngày).

    Thử lần lượt từng nguồn trong LIVE_PROVIDERS; nguồn nào lỗi thì ghi lại
    lỗi và thử nguồn kế tiếp. Nếu tất cả đều lỗi, trả về chi tiết lỗi của
    từng nguồn để dễ chẩn đoán.
    """
    ticker = ticker.strip().upper()
    if ticker not in GROUPED:
        return jsonify({"error": f"Không tìm thấy mã '{ticker}'"}), 404

    now = datetime.datetime.now(VN_TZ)
    if not is_market_open(now):
        return jsonify({
            "ticker": ticker,
            "market_open": False,
            "message": "Thị trường hiện đang đóng cửa (giờ giao dịch: 9:00-11:30 & 13:00-14:45, T2-T6).",
        })

    start_of_day = datetime.datetime.combine(now.date(), datetime.time(0, 0), tzinfo=VN_TZ)
    start_ts = int(start_of_day.timestamp())
    end_ts = int(now.timestamp())

    provider_errors = []
    for name, fetch_fn in LIVE_PROVIDERS:
        try:
            times, opens, highs, lows, closes, volumes = fetch_fn(ticker, start_ts, end_ts)
            if not times or not closes:
                provider_errors.append(f"{name}: không có dữ liệu cho mã này hôm nay")
                continue

            return jsonify({
                "ticker": ticker,
                "market_open": True,
                "source": name,
                "last_price": closes[-1],
                "open": opens[0],
                "high": max(highs),
                "low": min(lows),
                "volume": sum(volumes),
                "timestamp": times[-1],
                "intraday": {
                    "time": times, "open": opens, "high": highs,
                    "low": lows, "close": closes, "volume": volumes,
                },
            })
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            body = ""
            try:
                body = e.response.text[:200] if e.response is not None else ""
            except Exception:
                pass
            provider_errors.append(f"{name}: HTTP {status}" + (f" — {body}" if body else ""))
        except requests.exceptions.RequestException as e:
            provider_errors.append(f"{name}: lỗi kết nối ({e})")
        except (ValueError, KeyError) as e:
            provider_errors.append(f"{name}: dữ liệu trả về sai định dạng ({e})")

    # Tất cả nguồn đều lỗi
    return jsonify({
        "ticker": ticker,
        "market_open": True,
        "error": "Không lấy được dữ liệu trực tuyến từ nguồn nào. Chi tiết: " + " | ".join(provider_errors),
    }), 502


if __name__ == "__main__":
    # debug=False khi chạy dùng lâu dài để tránh nạp lại dữ liệu 2 lần (reloader)
    app.run(host="127.0.0.1", port=5000, debug=False)
