# ============================================================
#  버스 공기질 비교 시스템 - 웹앱 버전 (안정화)
#  라즈베리파이 피코 W + SCD30
#  당곡고등학교 환경 탐구 프로젝트
# ============================================================

import time
import utime
import json
import network
import socket
import gc                          # ★ 가비지 컬렉터
from machine import Pin, I2C

# ============================================================
# 설정
# ============================================================
WIFI_SSID        = "여기에_와이파이_이름"
WIFI_PASSWORD    = "여기에_와이파이_비번"
IDLE_INTERVAL    = 15
MEASURE_INTERVAL = 1

# ============================================================
# 문자열 헬퍼
# ============================================================
def zero_pad(n, width):
    s = str(n)
    return "0" * (width - len(s)) + s if len(s) < width else s

# ============================================================
# SCD30 드라이버
# ============================================================
class SCD30Driver:
    SCD30_ADDR        = 0x61
    CMD_START_MEASURE = b'\x00\x10'
    CMD_DATA_READY    = b'\x02\x02'
    CMD_READ_MEASURE  = b'\x03\x00'

    def __init__(self, i2c):
        self.i2c = i2c
        self._start_measurement()
        time.sleep(2)

    def _crc8(self, data):
        crc = 0xFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                crc = ((crc << 1) ^ 0x31) if (crc & 0x80) else (crc << 1)
                crc &= 0xFF
        return crc

    def _start_measurement(self, pressure=0):
        pb  = bytes([(pressure >> 8) & 0xFF, pressure & 0xFF])
        cmd = self.CMD_START_MEASURE + pb + bytes([self._crc8(pb)])
        self.i2c.writeto(self.SCD30_ADDR, cmd)

    def data_available(self):
        try:
            self.i2c.writeto(self.SCD30_ADDR, self.CMD_DATA_READY)
            time.sleep_ms(3)
            return self.i2c.readfrom(self.SCD30_ADDR, 3)[1] == 1
        except:
            return False

    def _b2f(self, b0, b1, b2, b3):
        val  = (b0 << 24) | (b1 << 16) | (b2 << 8) | b3
        sign = -1 if (val >> 31) else 1
        exp  = ((val >> 23) & 0xFF) - 127
        mant = (val & 0x7FFFFF) | 0x800000
        return round(sign * mant * (2 ** (exp - 23)), 2)

    def read_measurement(self):
        try:
            self.i2c.writeto(self.SCD30_ADDR, self.CMD_READ_MEASURE)
            time.sleep_ms(3)
            b = self.i2c.readfrom(self.SCD30_ADDR, 18)
            return (
                self._b2f(b[0],  b[1],  b[3],  b[4]),
                self._b2f(b[6],  b[7],  b[9],  b[10]),
                self._b2f(b[12], b[13], b[15], b[16])
            )
        except Exception as e:
            print("SCD30 오류:", e)
            return None, None, None

# ============================================================
# 통계
# ============================================================
def calc_mean(v):
    return round(sum(v) / len(v), 2) if v else 0.0

def calc_max(v):
    return round(max(v), 2) if v else 0.0

def calc_min(v):
    return round(min(v), 2) if v else 0.0

def calc_stdev(v):
    if len(v) < 2:
        return 0.0
    m = calc_mean(v)
    return round((sum((x - m) ** 2 for x in v) / len(v)) ** 0.5, 2)

def evaluate_co2(ppm):
    if ppm is None: return "알수없음", "#888888"
    if ppm < 450:   return "매우좋음", "#27ae60"
    if ppm < 700:   return "좋음",     "#2ecc71"
    if ppm < 1000:  return "보통",     "#f39c12"
    if ppm < 2000:  return "나쁨",     "#e67e22"
    if ppm < 5000:  return "매우나쁨", "#e74c3c"
    return                 "위험",     "#8e44ad"

# ============================================================
# 경과 시간
# ============================================================
_boot = utime.ticks_ms()

def elapsed_str():
    t = utime.ticks_diff(utime.ticks_ms(), _boot) // 1000
    return (zero_pad(t // 3600, 2) + ":" +
            zero_pad((t % 3600) // 60, 2) + ":" +
            zero_pad(t % 60, 2))

# ============================================================
# WiFi
# ============================================================
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    print("WiFi 연결 중", end="")
    for _ in range(20):
        if wlan.isconnected():
            break
        print(".", end="")
        time.sleep(1)
    if wlan.isconnected():
        ip = wlan.ifconfig()[0]
        print("\nIP:", ip)
        return ip
    print("\nWiFi 실패")
    return None

# ============================================================
# ★ 핵심: HTML을 청크로 나눠서 전송하는 함수
# ============================================================
def send_chunk(conn, text):
    """문자열을 512바이트씩 나눠서 전송 (메모리 절약)"""
    data = text.encode("utf-8")
    pos  = 0
    while pos < len(data):
        chunk = data[pos:pos + 512]
        conn.send(chunk)
        pos += 512
        time.sleep_ms(5)           # ★ 전송 사이 짧은 대기

# ============================================================
# ★ HTML을 여러 조각으로 나눠서 전송 (메모리 부족 방지)
# ============================================================
def send_html(conn, monitor):
    """HTML을 한 번에 만들지 않고 조각씩 전송"""

    gc.collect()                   # ★ 전송 전 메모리 정리

    co2  = monitor.last_co2  or 0
    temp = monitor.last_temp or 0
    humi = monitor.last_humi or 0
    lvl, color = evaluate_co2(co2)

    is_measuring = monitor.state in (
        monitor.STATE_GAS_MEAS,
        monitor.STATE_HYDRO_MEAS
    )
    current_count = len(monitor.current_readings)

    # 조건부 값 변수화
    interval_info     = "1초 (측정중)" if is_measuring else "15초 (대기중)"
    interval_bg       = "#c0392b"     if is_measuring else "#2c3e50"
    start_gas_href    = ""            if is_measuring else "/start_gas"
    start_hydro_href  = ""            if is_measuring else "/start_hydro"
    stop_href         = "/stop"       if is_measuring else ""
    start_gas_style   = "opacity:0.4;pointer-events:none" if is_measuring else ""
    start_hydro_style = "opacity:0.4;pointer-events:none" if is_measuring else ""
    stop_style        = ""            if is_measuring else "opacity:0.4;pointer-events:none"
    state_str         = monitor._state_str()

    g_avg = monitor._get_overall_avg("gas")
    h_avg = monitor._get_overall_avg("hydro")
    g_avg_str = str(g_avg) + " ppm" if g_avg is not None else "-"
    h_avg_str = str(h_avg) + " ppm" if h_avg is not None else "-"

    diff_str   = "-"
    diff_color = "#ecf0f1"
    result_msg = ""
    if g_avg is not None and h_avg is not None:
        diff = round(g_avg - h_avg, 2)
        diff_str   = ("+" if diff >= 0 else "") + str(diff) + " ppm"
        diff_color = "#e74c3c" if diff > 10 else ("#f39c12" if diff > 0 else "#2ecc71")
        if diff > 10:
            result_msg = "수소버스가 " + str(diff) + " ppm 낮습니다! 친환경적입니다."
        elif diff > 0:
            result_msg = "수소버스가 약간 낮습니다 (" + str(diff) + " ppm)"
        else:
            result_msg = "이번 측정에서는 비슷하거나 가스버스가 낮습니다."

    # 배너
    if is_measuring:
        if monitor.state == monitor.STATE_GAS_MEAS:
            banner_bg   = "#c0392b"
            banner_name = "가스버스"
        else:
            banner_bg   = "#1e8449"
            banner_name = "수소전기버스"
        banner = (
            "<div style='background:" + banner_bg +
            ";padding:12px;text-align:center;"
            "font-weight:bold;color:white;font-size:14px'>"
            + banner_name + " 측정중 | 수집: " +
            str(current_count) + "회 | 1초마다 수집</div>"
        )
    else:
        banner = ""

    # ── HTTP 헤더 먼저 전송 ──────────────────────────────
    conn.send(
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"Connection: close\r\n\r\n"
    )

    # ── CSS (1번째 청크) ─────────────────────────────────
    send_chunk(conn,
        "<!DOCTYPE html><html lang='ko'><head>"
        "<meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>버스 공기질 | 당곡고</title>"
        "<style>"
        "*{margin:0;padding:0;box-sizing:border-box}"
        "body{background:#1a1a2e;color:#eee;"
        "font-family:sans-serif;font-size:14px}"
        "h1{font-size:17px}"
        ".hd{background:#16213e;padding:14px 18px;"
        "border-bottom:3px solid #e74c3c}"
        ".hd p{font-size:11px;color:#aaa;margin-top:3px}"
        ".sb{background:#0f3460;padding:8px 18px;"
        "display:flex;flex-wrap:wrap;gap:14px;align-items:center}"
        ".si{font-size:12px;color:#ccc}"
        ".sv{font-weight:bold;font-size:13px}"
        ".bw{display:inline-block;padding:2px 8px;"
        "border-radius:8px;font-size:11px;font-weight:bold}"
        ".grid{display:grid;"
        "grid-template-columns:repeat(4,1fr);"
        "gap:10px;padding:14px 18px}"
        ".card{background:#16213e;border-radius:10px;"
        "padding:16px;text-align:center}"
        ".cl{font-size:10px;color:#aaa;margin-bottom:5px}"
        ".cv{font-size:26px;font-weight:bold;margin-bottom:2px}"
        ".cu{font-size:10px;color:#777}"
        ".sec{padding:0 18px 16px}"
        ".st{font-size:13px;font-weight:bold;margin-bottom:8px;"
        "padding-bottom:5px;border-bottom:1px solid #333}"
        ".bg{display:flex;gap:8px;flex-wrap:wrap}"
        ".btn{padding:10px 18px;border:none;border-radius:7px;"
        "font-size:12px;font-weight:bold;cursor:pointer;"
        "text-decoration:none;color:white;display:inline-block}"
        ".bg1{background:#c0392b}"
        ".bg2{background:#1e8449}"
        ".bg3{background:#d35400}"
        ".bg4{background:#6c3483}"
        ".bg5{background:#2c3e50;color:#bbb;border:1px solid #555}"
        ".cg{display:grid;grid-template-columns:1fr 1fr 1fr;"
        "gap:10px;margin-bottom:12px}"
        ".cc{background:#16213e;border-radius:10px;"
        "padding:14px;text-align:center}"
        ".ccl{font-size:10px;color:#aaa;margin-bottom:4px}"
        ".ccv{font-size:20px;font-weight:bold}"
        ".ccs{font-size:10px;color:#777;margin-top:2px}"
        ".rb{background:#16213e;border-radius:8px;padding:12px;"
        "text-align:center;font-size:12px;color:#f1c40f;"
        "border:1px solid #333;margin-top:8px}"
        ".tw{overflow-x:auto;border-radius:8px}"
        "table{width:100%;border-collapse:collapse;font-size:11px}"
        "th{background:#0f3460;color:#aaa;padding:8px 8px;"
        "text-align:left;white-space:nowrap}"
        "td{padding:7px 8px;border-bottom:1px solid #16213e;"
        "white-space:nowrap}"
        "tr:hover td{background:#16213e}"
        ".tabs{display:flex;gap:3px;margin-bottom:0}"
        ".tab{padding:7px 14px;background:#16213e;color:#888;"
        "cursor:pointer;font-size:11px;font-weight:bold;"
        "border-radius:6px 6px 0 0}"
        ".tab.on{color:white}"
        ".tc{display:none;border:1px solid #333;"
        "border-radius:0 6px 6px 6px}"
        ".tc.on{display:block}"
        "@media(max-width:500px){"
        ".grid{grid-template-columns:repeat(2,1fr)}"
        ".cg{grid-template-columns:1fr}}"
        "</style></head><body>"
    )

    # ── 헤더 + 배너 (2번째 청크) ────────────────────────
    send_chunk(conn,
        "<div class='hd'>"
        "<h1>버스 공기질 비교 시스템</h1>"
        "<p>당곡고등학교 환경탐구 | SCD30 | Pico W</p>"
        "</div>"
        + banner +
        "<div class='sb'>"
        "<div class='si'>상태: <span class='sv' style='color:#f1c40f'>"
        + state_str + "</span></div>"
        "<div class='si'>경과: <span class='sv'>" + elapsed_str() + "</span></div>"
        "<div class='si'>주기: <span class='bw' style='background:"
        + interval_bg + "'>" + interval_info + "</span></div>"
        "<div class='si'>가스: <span class='bw' style='background:#c0392b'>"
        + str(len(monitor.gas_sessions)) + "회</span></div>"
        "<div class='si'>수소: <span class='bw' style='background:#1e8449'>"
        + str(len(monitor.hydro_sessions)) + "회</span>"
        + (" &nbsp; 수집: <span class='sv' style='color:#e74c3c'>"
           + str(current_count) + "회</span>" if is_measuring else "") +
        "</div>"
        "</div>"
    )

    # ── 실시간 카드 (3번째 청크) ────────────────────────
    send_chunk(conn,
        "<div class='grid'>"
        "<div class='card'><div class='cl'>CO2 농도</div>"
        "<div class='cv' style='color:" + color + "'>" + str(co2) + "</div>"
        "<div class='cu'>ppm</div></div>"

        "<div class='card'><div class='cl'>온도</div>"
        "<div class='cv' style='color:#3498db'>" + str(temp) + "</div>"
        "<div class='cu'>°C</div></div>"

        "<div class='card'><div class='cl'>습도</div>"
        "<div class='cv' style='color:#2ecc71'>" + str(humi) + "</div>"
        "<div class='cu'>%</div></div>"

        "<div class='card'><div class='cl'>등급</div>"
        "<div class='cv' style='color:" + color + ";font-size:17px'>"
        + lvl + "</div>"
        "<div class='cu'>현재 수준</div></div>"
        "</div>"
    )

    # ── 제어 버튼 (4번째 청크) ──────────────────────────
    send_chunk(conn,
        "<div class='sec'><div class='st'>측정 제어</div>"
        "<div class='bg'>"
        "<a class='btn bg1' style='" + start_gas_style + "'"
        " href='" + start_gas_href + "'>가스버스 시작</a>"
        "<a class='btn bg2' style='" + start_hydro_style + "'"
        " href='" + start_hydro_href + "'>수소버스 시작</a>"
        "<a class='btn bg3' style='" + stop_style + "'"
        " href='" + stop_href + "'>측정 종료</a>"
        "<a class='btn bg4' href='/'>새로고침</a>"
        "<a class='btn bg5' href='/reset'"
        " onclick=\"return confirm('초기화?\')\">"
        "초기화</a>"
        "</div></div>"
    )

    # ── 비교 요약 (5번째 청크) ──────────────────────────
    send_chunk(conn,
        "<div class='sec'><div class='st'>비교 요약</div>"
        "<div class='cg'>"

        "<div class='cc' style='border-top:3px solid #c0392b'>"
        "<div class='ccl'>가스버스 평균 CO2</div>"
        "<div class='ccv' style='color:#e74c3c'>" + g_avg_str + "</div>"
        "<div class='ccs'>" + str(len(monitor.gas_sessions)) + "개 세션</div>"
        "</div>"

        "<div class='cc' style='border-top:3px solid #1e8449'>"
        "<div class='ccl'>수소버스 평균 CO2</div>"
        "<div class='ccv' style='color:#2ecc71'>" + h_avg_str + "</div>"
        "<div class='ccs'>" + str(len(monitor.hydro_sessions)) + "개 세션</div>"
        "</div>"

        "<div class='cc' style='border-top:3px solid " + diff_color + "'>"
        "<div class='ccl'>차이 (가스-수소)</div>"
        "<div class='ccv' style='color:" + diff_color + "'>" + diff_str + "</div>"
        "<div class='ccs'>양수=가스버스 높음</div>"
        "</div>"
        "</div>"
        + ("<div class='rb'>" + result_msg + "</div>" if result_msg else "") +
        "</div>"
    )

    # ── 가스버스 세션 테이블 (6번째 청크) ───────────────
    gas_rows = ""
    for s in monitor.gas_sessions:
        a      = s["stats"]["co2"]["avg"]
        mx     = s["stats"]["co2"]["max"]
        mn     = s["stats"]["co2"]["min"]
        lv, cl = evaluate_co2(a)
        gas_rows += (
            "<tr><td>#" + str(s["session_no"]) + "</td>"
            "<td>" + s["start_time"] + "</td>"
            "<td>" + s["end_time"]   + "</td>"
            "<td>" + str(s["count"]) + "회</td>"
            "<td style='color:" + cl + ";font-weight:bold'>" + str(a) + "</td>"
            "<td>" + str(mx) + "</td>"
            "<td>" + str(mn) + "</td>"
            "<td style='color:" + cl + "'>" + lv + "</td></tr>"
        )
    if not gas_rows:
        gas_rows = "<tr><td colspan='8' style='text-align:center;color:#777'>데이터 없음</td></tr>"

    # ── 수소버스 세션 테이블 (7번째 청크) ───────────────
    hydro_rows = ""
    for s in monitor.hydro_sessions:
        a      = s["stats"]["co2"]["avg"]
        mx     = s["stats"]["co2"]["max"]
        mn     = s["stats"]["co2"]["min"]
        lv, cl = evaluate_co2(a)
        hydro_rows += (
            "<tr><td>#" + str(s["session_no"]) + "</td>"
            "<td>" + s["start_time"] + "</td>"
            "<td>" + s["end_time"]   + "</td>"
            "<td>" + str(s["count"]) + "회</td>"
            "<td style='color:" + cl + ";font-weight:bold'>" + str(a) + "</td>"
            "<td>" + str(mx) + "</td>"
            "<td>" + str(mn) + "</td>"
            "<td style='color:" + cl + "'>" + lv + "</td></tr>"
        )
    if not hydro_rows:
        hydro_rows = "<tr><td colspan='8' style='text-align:center;color:#777'>데이터 없음</td></tr>"

    # ── 최근 기록 테이블 (8번째 청크) ───────────────────
    recent_rows = ""
    recent = monitor.all_recent[-20:]   # ★ 20개로 줄여서 메모리 절약
    for r in recent[::-1]:
        lv, cl = evaluate_co2(r["co2"])
        recent_rows += (
            "<tr><td>" + r["time"]     + "</td>"
            "<td>" + r["bus_type"]     + "</td>"
            "<td style='color:" + cl + ";font-weight:bold'>"
            + str(r["co2"])  + "</td>"
            "<td>" + str(r["temp"])    + "</td>"
            "<td>" + str(r["humi"])    + "</td>"
            "<td style='color:" + cl + "'>" + lv + "</td></tr>"
        )
    if not recent_rows:
        recent_rows = "<tr><td colspan='6' style='text-align:center;color:#777'>기록 없음</td></tr>"

    # ── 탭 + 테이블 (9번째 청크) ────────────────────────
    send_chunk(conn,
        "<div class='sec'><div class='st'>세션별 결과</div>"
        "<div class='tabs'>"
        "<div class='tab on' id='tg' style='background:#c0392b'"
        " onclick=\"st('g')\">가스버스</div>"
        "<div class='tab' id='th'"
        " onclick=\"st('h')\">수소버스</div>"
        "<div class='tab' id='tr'"
        " onclick=\"st('r')\">최근측정</div>"
        "</div>"
    )

    send_chunk(conn,
        "<div id='tc-g' class='tc on'><div class='tw'>"
        "<table><thead><tr>"
        "<th>#</th><th>시작</th><th>종료</th><th>횟수</th>"
        "<th>평균CO2</th><th>최대</th><th>최소</th><th>등급</th>"
        "</tr></thead><tbody>" + gas_rows +
        "</tbody></table></div></div>"
    )

    send_chunk(conn,
        "<div id='tc-h' class='tc'><div class='tw'>"
        "<table><thead><tr>"
        "<th>#</th><th>시작</th><th>종료</th><th>횟수</th>"
        "<th>평균CO2</th><th>최대</th><th>최소</th><th>등급</th>"
        "</tr></thead><tbody>" + hydro_rows +
        "</tbody></table></div></div>"
    )

    send_chunk(conn,
        "<div id='tc-r' class='tc'><div class='tw'>"
        "<table><thead><tr>"
        "<th>시각</th><th>버스</th>"
        "<th>CO2</th><th>온도</th><th>습도</th><th>등급</th>"
        "</tr></thead><tbody>" + recent_rows +
        "</tbody></table></div></div>"
        "</div>"
    )

    # ── JavaScript (10번째 청크) ─────────────────────────
    auto_js = "setTimeout(function(){location.reload()},3000);" if is_measuring else ""
    send_chunk(conn,
        "<script>"
        "function st(n){"
        "['g','h','r'].forEach(function(x){"
        "document.getElementById('tc-'+x).classList.remove('on');"
        "var t=document.getElementById('t'+x);"
        "t.classList.remove('on');"
        "t.style.background='';"
        "});"
        "document.getElementById('tc-'+n).classList.add('on');"
        "var el=document.getElementById('t'+n);"
        "el.classList.add('on');"
        "var c={'g':'#c0392b','h':'#1e8449','r':'#2980b9'};"
        "el.style.background=c[n];}"
        + auto_js +
        "</script></body></html>"
    )

    gc.collect()                   # ★ 전송 후 메모리 정리


# ============================================================
# 메인 시스템
# ============================================================
class BusAirMonitor:

    STATE_IDLE        = 0
    STATE_GAS_MEAS    = 1
    STATE_HYDRO_MEAS  = 2
    STATE_SHOW_RESULT = 3

    def __init__(self):
        print("=" * 45)
        print("  버스 공기질 비교 시스템")
        print("  당곡고등학교 환경 탐구 프로젝트")
        print("=" * 45)

        self.i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=50000)
        print("I2C:", [hex(a) for a in self.i2c.scan()])

        try:
            self.sensor = SCD30Driver(self.i2c)
            print("SCD30 OK")
        except Exception as e:
            print("SCD30 오류:", e)
            self.sensor = None

        self.btn_start = Pin(14, Pin.IN, Pin.PULL_UP)
        self.btn_stop  = Pin(15, Pin.IN, Pin.PULL_UP)
        self.led       = Pin(25, Pin.OUT)

        self.gas_sessions   = []
        self.hydro_sessions = []
        self.all_recent     = []       # ★ 최대 100개로 제한

        self.current_readings = []
        self.current_type     = None

        self.state           = self.STATE_IDLE
        self.last_co2        = None
        self.last_temp       = None
        self.last_humi       = None
        self.last_btn_time   = 0
        self.DEBOUNCE_MS     = 300
        self.led_tick        = 0
        self.last_measure_ms = utime.ticks_ms()

        self.server_sock = None
        self.ip          = None

    def _state_str(self):
        if self.state == self.STATE_IDLE:        return "대기중"
        if self.state == self.STATE_GAS_MEAS:    return "가스버스 측정중"
        if self.state == self.STATE_HYDRO_MEAS:  return "수소버스 측정중"
        if self.state == self.STATE_SHOW_RESULT: return "결과표시중"
        return "알수없음"

    def _current_interval_ms(self):
        if self.state in (self.STATE_GAS_MEAS, self.STATE_HYDRO_MEAS):
            return MEASURE_INTERVAL * 1000
        return IDLE_INTERVAL * 1000

    def read_sensor(self):
        if self.sensor is None:
            import urandom
            return (
                float(400 + (urandom.getrandbits(8) % 300)),
                float(20  + (urandom.getrandbits(5) % 10)),
                float(45  + (urandom.getrandbits(5) % 30))
            )
        try:
            for _ in range(5):              # ★ 반복 횟수 줄임
                if self.sensor.data_available():
                    co2, temp, humi = self.sensor.read_measurement()
                    if co2 and 300 <= co2 <= 5000:
                        return co2, temp, humi
                time.sleep_ms(200)
        except Exception as e:
            print("센서 오류:", e)
        return None, None, None

    def start_measurement(self, bus_type):
        self.current_readings = []
        self.current_type     = bus_type
        self.state = (self.STATE_GAS_MEAS
                      if bus_type == "gas"
                      else self.STATE_HYDRO_MEAS)
        self.last_measure_ms = (utime.ticks_ms()
                                - self._current_interval_ms())
        label = "가스버스" if bus_type == "gas" else "수소전기버스"
        print("[" + label + "] 측정 시작!")
        self.led.on()

    def stop_measurement(self):
        self.led.off()
        if not self.current_readings:
            print("데이터 없음")
            self.state = self.STATE_IDLE
            return

        co2_v  = [r["co2"]  for r in self.current_readings]
        temp_v = [r["temp"] for r in self.current_readings]
        humi_v = [r["humi"] for r in self.current_readings]

        sessions = (self.gas_sessions
                    if self.current_type == "gas"
                    else self.hydro_sessions)

        session = {
            "type"       : self.current_type,
            "session_no" : len(sessions) + 1,
            "count"      : len(self.current_readings),
            "start_time" : self.current_readings[0]["time"],
            "end_time"   : self.current_readings[-1]["time"],
            "readings"   : self.current_readings[:],
            "stats": {
                "co2" : {"avg"  : calc_mean(co2_v),
                         "max"  : calc_max(co2_v),
                         "min"  : calc_min(co2_v),
                         "stdev": calc_stdev(co2_v)},
                "temp": {"avg"  : calc_mean(temp_v),
                         "max"  : calc_max(temp_v),
                         "min"  : calc_min(temp_v)},
                "humi": {"avg"  : calc_mean(humi_v),
                         "max"  : calc_max(humi_v),
                         "min"  : calc_min(humi_v)}
            }
        }
        sessions.append(session)

        avg    = session["stats"]["co2"]["avg"]
        lvl, _ = evaluate_co2(avg)
        btype  = "가스버스" if self.current_type == "gas" else "수소버스"
        print("[" + btype + "] #" + str(session["session_no"]) +
              " 종료 | " + str(session["count"]) + "회 | " +
              str(avg) + "ppm | " + lvl)

        self.state        = self.STATE_SHOW_RESULT
        self.current_type = None
        self.last_measure_ms = utime.ticks_ms()
        gc.collect()               # ★ 종료 후 메모리 정리

    def reset_all(self):
        self.gas_sessions     = []
        self.hydro_sessions   = []
        self.all_recent       = []
        self.current_readings = []
        self.current_type     = None
        self.state            = self.STATE_IDLE
        self.last_measure_ms  = utime.ticks_ms()
        gc.collect()               # ★ 초기화 후 메모리 정리
        print("초기화 완료")

    def _get_overall_avg(self, bus_type):
        s = (self.gas_sessions if bus_type == "gas"
             else self.hydro_sessions)
        if not s:
            return None
        vals = [r["co2"] for ss in s for r in ss["readings"]]
        return calc_mean(vals) if vals else None

    def _debounce_ok(self):
        now = utime.ticks_ms()
        if utime.ticks_diff(now, self.last_btn_time) > self.DEBOUNCE_MS:
            self.last_btn_time = now
            return True
        return False

    def check_buttons(self):
        btn_a = self.btn_start.value() == 0
        btn_b = self.btn_stop.value()  == 0
        if (btn_a or btn_b) and self._debounce_ok():
            if self.state == self.STATE_IDLE:
                if btn_a:
                    self.start_measurement("gas")
                elif btn_b:
                    self.start_measurement("hydro")
            elif self.state in (self.STATE_GAS_MEAS,
                                self.STATE_HYDRO_MEAS):
                if btn_b:
                    self.stop_measurement()
            elif self.state == self.STATE_SHOW_RESULT:
                self.state = self.STATE_IDLE
            time.sleep_ms(50)

    def _blink_led(self):
        if self.state in (self.STATE_GAS_MEAS, self.STATE_HYDRO_MEAS):
            self.led_tick += 1
            if self.led_tick % 2 == 0:
                self.led.toggle()
        else:
            self.led.off()

    def setup_server(self):
        self.server_sock = socket.socket()
        self.server_sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(("0.0.0.0", 80))
        self.server_sock.listen(1)
        self.server_sock.setblocking(False)
        print("서버: http://" + str(self.ip))

    def handle_request(self):
        try:
            conn, addr = self.server_sock.accept()
        except OSError:
            return                 # 요청 없음

        conn.settimeout(3.0)
        try:
            req = conn.recv(256).decode("utf-8")  # ★ 256으로 축소
        except:
            conn.close()
            return

        path = "/"
        try:
            line  = req.split("\r\n")[0]
            parts = line.split(" ")
            if len(parts) >= 2:
                path = parts[1]
        except:
            pass

        print("요청:", path)
        gc.collect()               # ★ 요청 처리 전 메모리 정리

        try:
            if path == "/start_gas":
                self.start_measurement("gas")
                conn.send(b"HTTP/1.1 302 Found\r\nLocation: /\r\n"
                          b"Connection: close\r\n\r\n")
                conn.close()
            elif path == "/start_hydro":
                self.start_measurement("hydro")
                conn.send(b"HTTP/1.1 302 Found\r\nLocation: /\r\n"
                          b"Connection: close\r\n\r\n")
                conn.close()
            elif path == "/stop":
                self.stop_measurement()
                conn.send(b"HTTP/1.1 302 Found\r\nLocation: /\r\n"
                          b"Connection: close\r\n\r\n")
                conn.close()
            elif path == "/reset":
                self.reset_all()
                conn.send(b"HTTP/1.1 302 Found\r\nLocation: /\r\n"
                          b"Connection: close\r\n\r\n")
                conn.close()
            elif path == "/api":
                # ★ JSON API (가벼운 데이터만)
                lvl, clr = evaluate_co2(self.last_co2)
                data = {
                    "co2"    : self.last_co2,
                    "temp"   : self.last_temp,
                    "humi"   : self.last_humi,
                    "level"  : lvl,
                    "state"  : self._state_str(),
                    "count"  : len(self.current_readings),
                    "g_sess" : len(self.gas_sessions),
                    "h_sess" : len(self.hydro_sessions),
                    "g_avg"  : self._get_overall_avg("gas"),
                    "h_avg"  : self._get_overall_avg("hydro"),
                }
                body = json.dumps(data)
                conn.send(("HTTP/1.1 200 OK\r\n"
                           "Content-Type: application/json\r\n"
                           "Connection: close\r\n\r\n"
                           + body).encode())
                conn.close()
            else:
                # ★ HTML을 청크로 나눠 전송
                send_html(conn, self)
                conn.close()

        except Exception as e:
            print("응답 오류:", e)
            try:
                conn.close()
            except:
                pass

        gc.collect()               # ★ 요청 완료 후 메모리 정리

    def run(self):
        self.ip = connect_wifi()
        if self.ip:
            self.setup_server()
        else:
            print("오프라인 모드")

        print("워밍업 (5초)...")
        for i in range(5, 0, -1):
            print(" " + str(i) + "초...")
            time.sleep(1)
        print("준비 완료!")
        if self.ip:
            print("접속: http://" + self.ip)
        print()

        while True:
            # 버튼
            self.check_buttons()

            # 웹 요청
            if self.server_sock:
                self.handle_request()

            # 센서 수집 (주기 체크)
            now      = utime.ticks_ms()
            interval = self._current_interval_ms()
            if utime.ticks_diff(now, self.last_measure_ms) >= interval:
                self.last_measure_ms = now
                co2, temp, humi = self.read_sensor()

                if co2 is not None:
                    self.last_co2  = co2
                    self.last_temp = temp
                    self.last_humi = humi
                    lvl, _ = evaluate_co2(co2)

                    if self.state in (self.STATE_GAS_MEAS,
                                      self.STATE_HYDRO_MEAS):
                        bus_label = ("가스버스"
                                     if self.state == self.STATE_GAS_MEAS
                                     else "수소버스")
                        record = {
                            "time"    : elapsed_str(),
                            "bus_type": bus_label,
                            "co2"     : co2,
                            "temp"    : temp,
                            "humi"    : humi,
                        }
                        self.current_readings.append(record)
                        self.all_recent.append(record)

                        # ★ 메모리 관리: 최대 100개
                        if len(self.all_recent) > 100:
                            self.all_recent.pop(0)

                        count = len(self.current_readings)
                        print("#" + zero_pad(count, 4) +
                              " CO2:" + str(co2) +
                              " T:"   + str(temp) +
                              " H:"   + str(humi) +
                              " " + lvl)

                        # ★ 50회마다 메모리 정리
                        if count % 50 == 0:
                            gc.collect()
                            print("메모리 정리:", gc.mem_free(), "bytes")

                    else:
                        print("[대기] CO2:" + str(co2) +
                              " " + lvl)

                self._blink_led()

            time.sleep_ms(100)

# ============================================================
# 시작
# ============================================================
gc.collect()
monitor = BusAirMonitor()
monitor.run()
