# ============================================================
#  버스 공기질 비교 시스템 - 웹앱 버전
#  라즈베리파이 피코 W + SCD30
#  당곡고등학교 환경 탐구 프로젝트
#  측정 주기: 5초
# ============================================================

import time
import utime
import json
import network
import socket
from machine import Pin, I2C

# ============================================================
# WiFi 설정 (본인 환경에 맞게 수정)
# ============================================================
WIFI_SSID     = "app"   # ← 수정
WIFI_PASSWORD = "20242024"   # ← 수정
MEASURE_INTERVAL = 5                     # 측정 주기 (초)

# ============================================================
# 문자열 헬퍼 (MicroPython 호환)
# ============================================================
def pad_right(s, width):
    s = str(s)
    return s + " " * (width - len(s)) if len(s) < width else s

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
            print("SCD30 읽기 오류:", e)
            return None, None, None

# ============================================================
# 통계 함수
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
    return round((sum((x-m)**2 for x in v) / len(v)) ** 0.5, 2)

def evaluate_co2(ppm):
    if ppm is None: return "알수없음", "#888888"
    if ppm < 450:   return "매우좋음", "#27ae60"
    if ppm < 700:   return "좋음",     "#2ecc71"
    if ppm < 1000:  return "보통",     "#f39c12"
    if ppm < 2000:  return "나쁨",     "#e67e22"
    if ppm < 5000:  return "매우나쁨", "#e74c3c"
    return           "위험",           "#8e44ad"

# ============================================================
# 경과 시간
# ============================================================
_boot = utime.ticks_ms()

def elapsed_str():
    t = utime.ticks_diff(utime.ticks_ms(), _boot) // 1000
    return zero_pad(t//3600,2)+":"+zero_pad((t%3600)//60,2)+":"+zero_pad(t%60,2)

# ============================================================
# WiFi 연결
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
        print("\nWiFi 연결 성공! IP:", ip)
        return ip
    else:
        print("\nWiFi 연결 실패")
        return None

# ============================================================
# HTML 페이지 생성
# ============================================================
def build_html(monitor):
    co2  = monitor.last_co2  or 0
    temp = monitor.last_temp or 0
    humi = monitor.last_humi or 0
    lvl, color = evaluate_co2(co2)
    state_str  = monitor._state_str()

    g_avg = monitor._get_overall_avg("gas")
    h_avg = monitor._get_overall_avg("hydro")
    g_avg_str = str(g_avg) + " ppm" if g_avg else "데이터 없음"
    h_avg_str = str(h_avg) + " ppm" if h_avg else "데이터 없음"

    diff_str    = ""
    diff_color  = "#ecf0f1"
    result_msg  = ""
    if g_avg and h_avg:
        diff = round(g_avg - h_avg, 2)
        diff_str = ("+" if diff >= 0 else "") + str(diff) + " ppm"
        if diff > 10:
            diff_color = "#e74c3c"
            result_msg = "수소전기버스가 " + str(diff) + " ppm 더 낮습니다! 친환경적입니다."
        elif diff > 0:
            diff_color = "#f39c12"
            result_msg = "수소전기버스가 약간 낮습니다 (" + str(diff) + " ppm)"
        else:
            diff_color = "#2ecc71"
            result_msg = "이번 측정에서는 비슷하거나 가스버스가 낮습니다."

    # 가스버스 세션 행
    gas_rows = ""
    for s in monitor.gas_sessions:
        a = s["stats"]["co2"]["avg"]
        mx = s["stats"]["co2"]["max"]
        mn = s["stats"]["co2"]["min"]
        lv, cl = evaluate_co2(a)
        gas_rows += (
            "<tr>"
            "<td>#" + str(s["session_no"]) + "</td>"
            "<td>" + s["start_time"] + "</td>"
            "<td>" + s["end_time"]   + "</td>"
            "<td>" + str(s["count"]) + "회</td>"
            "<td style='color:" + cl + ";font-weight:bold'>" + str(a) + "</td>"
            "<td>" + str(mx) + "</td>"
            "<td>" + str(mn) + "</td>"
            "<td style='color:" + cl + "'>" + lv + "</td>"
            "</tr>"
        )
    if not gas_rows:
        gas_rows = "<tr><td colspan='8' style='text-align:center;color:#7f8c8d'>측정 데이터 없음</td></tr>"

    # 수소버스 세션 행
    hydro_rows = ""
    for s in monitor.hydro_sessions:
        a  = s["stats"]["co2"]["avg"]
        mx = s["stats"]["co2"]["max"]
        mn = s["stats"]["co2"]["min"]
        lv, cl = evaluate_co2(a)
        hydro_rows += (
            "<tr>"
            "<td>#" + str(s["session_no"]) + "</td>"
            "<td>" + s["start_time"] + "</td>"
            "<td>" + s["end_time"]   + "</td>"
            "<td>" + str(s["count"]) + "회</td>"
            "<td style='color:" + cl + ";font-weight:bold'>" + str(a) + "</td>"
            "<td>" + str(mx) + "</td>"
            "<td>" + str(mn) + "</td>"
            "<td style='color:" + cl + "'>" + lv + "</td>"
            "</tr>"
        )
    if not hydro_rows:
        hydro_rows = "<tr><td colspan='8' style='text-align:center;color:#7f8c8d'>측정 데이터 없음</td></tr>"

    # 최근 20개 측정 기록
    recent_rows = ""
    recent = monitor.all_recent[-20:]
    recent.reverse()
    for r in recent:
        lv, cl = evaluate_co2(r["co2"])
        recent_rows += (
            "<tr>"
            "<td>" + r["time"] + "</td>"
            "<td>" + r["bus_type"] + "</td>"
            "<td style='color:" + cl + ";font-weight:bold'>" + str(r["co2"]) + "</td>"
            "<td>" + str(r["temp"]) + "</td>"
            "<td>" + str(r["humi"]) + "</td>"
            "<td style='color:" + cl + "'>" + lv + "</td>"
            "</tr>"
        )
    if not recent_rows:
        recent_rows = "<tr><td colspan='6' style='text-align:center;color:#7f8c8d'>측정 기록 없음</td></tr>"

    html = """<!DOCTYPE html>
<html lang='ko'>
<head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width, initial-scale=1.0'>
<meta http-equiv='refresh' content='5'>
<title>버스 공기질 비교 | 당곡고</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:#1e272e;color:#ecf0f1;font-family:'Malgun Gothic',sans-serif;min-height:100vh}

  /* 헤더 */
  .header{background:#2c3e50;padding:18px 24px;border-bottom:3px solid #e74c3c}
  .header h1{font-size:22px;color:#ecf0f1}
  .header p{font-size:12px;color:#95a5a6;margin-top:4px}

  /* 상태 바 */
  .status-bar{background:#34495e;padding:10px 24px;display:flex;align-items:center;gap:24px;flex-wrap:wrap}
  .status-item{font-size:12px;color:#bdc3c7}
  .status-val{font-weight:bold;font-size:14px}
  .badge{display:inline-block;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:bold}

  /* 실시간 카드 */
  .live-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;padding:20px 24px}
  .card{background:#2c3e50;border-radius:12px;padding:20px;text-align:center;border:1px solid #34495e}
  .card-label{font-size:11px;color:#95a5a6;margin-bottom:8px;text-transform:uppercase}
  .card-value{font-size:32px;font-weight:bold;margin-bottom:4px}
  .card-unit{font-size:12px;color:#7f8c8d}

  /* 섹션 */
  .section{padding:0 24px 24px}
  .section-title{font-size:15px;font-weight:bold;color:#ecf0f1;
                 margin-bottom:12px;padding-bottom:8px;
                 border-bottom:2px solid #34495e;display:flex;align-items:center;gap:8px}

  /* 버튼 그룹 */
  .btn-group{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}
  .btn{padding:12px 24px;border:none;border-radius:8px;font-size:14px;
       font-weight:bold;cursor:pointer;text-decoration:none;display:inline-block;
       transition:opacity .2s}
  .btn:hover{opacity:.85}
  .btn-gas{background:#e74c3c;color:white}
  .btn-hydro{background:#27ae60;color:white}
  .btn-stop{background:#e67e22;color:white}
  .btn-compare{background:#8e44ad;color:white}
  .btn-reset{background:#2c3e50;color:#bdc3c7;border:1px solid #34495e}

  /* 비교 카드 */
  .compare-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-bottom:20px}
  .cmp-card{background:#2c3e50;border-radius:12px;padding:18px;text-align:center}
  .cmp-label{font-size:12px;color:#95a5a6;margin-bottom:6px}
  .cmp-value{font-size:24px;font-weight:bold}
  .cmp-sub{font-size:11px;color:#7f8c8d;margin-top:4px}
  .result-box{background:#2c3e50;border-radius:10px;padding:16px;
              text-align:center;font-size:14px;color:#f1c40f;border:1px solid #34495e}

  /* 테이블 */
  .tbl-wrap{overflow-x:auto;border-radius:10px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th{background:#34495e;color:#bdc3c7;padding:10px 12px;text-align:left;
     font-size:12px;white-space:nowrap}
  td{padding:9px 12px;border-bottom:1px solid #2c3e50;color:#ecf0f1;white-space:nowrap}
  tr:last-child td{border-bottom:none}
  tr:hover td{background:#2c3e50}

  /* 탭 */
  .tabs{display:flex;gap:0;margin-bottom:16px}
  .tab{padding:10px 20px;background:#2c3e50;color:#95a5a6;cursor:pointer;
       font-size:13px;font-weight:bold;border-radius:8px 8px 0 0;border:1px solid #34495e}
  .tab.active{background:#e74c3c;color:white;border-color:#e74c3c}
  .tab-hydro.active{background:#27ae60;border-color:#27ae60}
  .tab-content{display:none}
  .tab-content.active{display:block}

  /* 반응형 */
  @media(max-width:600px){
    .live-grid{grid-template-columns:repeat(2,1fr)}
    .compare-grid{grid-template-columns:1fr}
  }
</style>
</head>
<body>

<!-- 헤더 -->
<div class='header'>
  <h1>🌿 버스 공기질 비교 시스템</h1>
  <p>당곡고등학교 환경 탐구 프로젝트 | SCD30 센서 | Raspberry Pi Pico W | 5초 자동 갱신</p>
</div>

<!-- 상태 바 -->
<div class='status-bar'>
  <div class='status-item'>
    상태:&nbsp;
    <span class='status-val' style='color:#f1c40f'>""" + state_str + """</span>
  </div>
  <div class='status-item'>
    경과:&nbsp;<span class='status-val'>""" + elapsed_str() + """</span>
  </div>
  <div class='status-item'>
    가스버스 세션:&nbsp;
    <span class='badge' style='background:#e74c3c'>""" + str(len(monitor.gas_sessions)) + """회</span>
  </div>
  <div class='status-item'>
    수소버스 세션:&nbsp;
    <span class='badge' style='background:#27ae60'>""" + str(len(monitor.hydro_sessions)) + """회</span>
  </div>
  <div class='status-item'>
    측정 주기:&nbsp;<span class='status-val'>5초</span>
  </div>
</div>

<!-- 실시간 카드 -->
<div class='live-grid'>
  <div class='card'>
    <div class='card-label'>CO₂ 농도</div>
    <div class='card-value' style='color:""" + color + """'>""" + str(co2) + """</div>
    <div class='card-unit'>ppm</div>
  </div>
  <div class='card'>
    <div class='card-label'>온도</div>
    <div class='card-value' style='color:#3498db'>""" + str(temp) + """</div>
    <div class='card-unit'>°C</div>
  </div>
  <div class='card'>
    <div class='card-label'>습도</div>
    <div class='card-value' style='color:#2ecc71'>""" + str(humi) + """</div>
    <div class='card-unit'>%</div>
  </div>
  <div class='card'>
    <div class='card-label'>공기질 등급</div>
    <div class='card-value' style='color:""" + color + """;font-size:22px'>""" + lvl + """</div>
    <div class='card-unit'>현재 수준</div>
  </div>
</div>

<!-- 버튼 제어 -->
<div class='section'>
  <div class='section-title'>🎮 측정 제어</div>
  <div class='btn-group'>
    <a class='btn btn-gas'   href='/start_gas'>🚌 가스버스 측정 시작</a>
    <a class='btn btn-hydro' href='/start_hydro'>🚍 수소버스 측정 시작</a>
    <a class='btn btn-stop'  href='/stop'>⏹ 측정 종료</a>
    <a class='btn btn-compare' href='/compare'>📊 비교 결과 보기</a>
    <a class='btn btn-reset' href='/reset'>🗑 전체 초기화</a>
  </div>
</div>

<!-- 비교 결과 -->
<div class='section'>
  <div class='section-title'>📊 비교 요약</div>
  <div class='compare-grid'>
    <div class='cmp-card' style='border-top:3px solid #e74c3c'>
      <div class='cmp-label'>🚌 가스버스 평균 CO₂</div>
      <div class='cmp-value' style='color:#e74c3c'>""" + g_avg_str + """</div>
      <div class='cmp-sub'>""" + str(len(monitor.gas_sessions)) + """개 세션 평균</div>
    </div>
    <div class='cmp-card' style='border-top:3px solid #27ae60'>
      <div class='cmp-label'>🚍 수소버스 평균 CO₂</div>
      <div class='cmp-value' style='color:#27ae60'>""" + h_avg_str + """</div>
      <div class='cmp-sub'>""" + str(len(monitor.hydro_sessions)) + """개 세션 평균</div>
    </div>
    <div class='cmp-card' style='border-top:3px solid """ + diff_color + """'>
      <div class='cmp-label'>차이 (가스 - 수소)</div>
      <div class='cmp-value' style='color:""" + diff_color + """'>""" + (diff_str or "—") + """</div>
      <div class='cmp-sub'>양수 = 가스버스가 높음</div>
    </div>
  </div>
  """ + ("<div class='result-box'>" + result_msg + "</div>" if result_msg else "") + """
</div>

<!-- 세션 테이블 탭 -->
<div class='section'>
  <div class='section-title'>📋 세션별 측정 결과</div>
  <div class='tabs'>
    <div class='tab active'     onclick="showTab('gas')">🚌 가스버스</div>
    <div class='tab tab-hydro'  onclick="showTab('hydro')">🚍 수소버스</div>
    <div class='tab'            onclick="showTab('recent')">📡 최근 측정</div>
  </div>

  <div id='tab-gas' class='tab-content active'>
    <div class='tbl-wrap'>
    <table>
      <thead>
        <tr><th>#</th><th>시작</th><th>종료</th><th>횟수</th>
            <th>평균 CO₂</th><th>최대</th><th>최소</th><th>등급</th></tr>
      </thead>
      <tbody>""" + gas_rows + """</tbody>
    </table>
    </div>
  </div>

  <div id='tab-hydro' class='tab-content'>
    <div class='tbl-wrap'>
    <table>
      <thead>
        <tr><th>#</th><th>시작</th><th>종료</th><th>횟수</th>
            <th>평균 CO₂</th><th>최대</th><th>최소</th><th>등급</th></tr>
      </thead>
      <tbody>""" + hydro_rows + """</tbody>
    </table>
    </div>
  </div>

  <div id='tab-recent' class='tab-content'>
    <div class='tbl-wrap'>
    <table>
      <thead>
        <tr><th>시각</th><th>버스종류</th><th>CO₂(ppm)</th>
            <th>온도(°C)</th><th>습도(%)</th><th>등급</th></tr>
      </thead>
      <tbody>""" + recent_rows + """</tbody>
    </table>
    </div>
  </div>
</div>

<script>
function showTab(name){
  document.querySelectorAll('.tab-content').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  event.target.classList.add('active');
}
</script>
</body></html>"""
    return html

# ============================================================
# 메인 시스템 클래스
# ============================================================
class BusAirMonitor:

    STATE_IDLE        = 0
    STATE_GAS_MEAS    = 1
    STATE_HYDRO_MEAS  = 2
    STATE_SHOW_RESULT = 3

    def __init__(self):
        print("=" * 50)
        print("  버스 공기질 비교 시스템 (웹앱 버전)")
        print("  당곡고등학교 환경 탐구 프로젝트")
        print("=" * 50)

        # I2C
        self.i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=50000)
        print("I2C 장치:", [hex(a) for a in self.i2c.scan()])

        # SCD30
        try:
            self.sensor = SCD30Driver(self.i2c)
            print("SCD30 초기화 완료")
        except Exception as e:
            print("SCD30 오류:", e)
            self.sensor = None

        # 버튼 (물리 버튼도 유지)
        self.btn_start = Pin(14, Pin.IN, Pin.PULL_UP)
        self.btn_stop  = Pin(15, Pin.IN, Pin.PULL_UP)
        self.led       = Pin(25, Pin.OUT)

        # 데이터
        self.gas_sessions   = []
        self.hydro_sessions = []
        self.all_recent     = []   # 최근 측정 통합 기록 (최대 100개)

        self.current_readings = []
        self.current_type     = None

        # 상태
        self.state         = self.STATE_IDLE
        self.last_co2      = None
        self.last_temp     = None
        self.last_humi     = None
        self.last_btn_time = 0
        self.DEBOUNCE_MS   = 300
        self.led_tick      = 0

        # 웹 서버
        self.server_sock = None
        self.ip          = None

    # ──────────────────────────────────────────────────────
    # 상태 문자열
    # ──────────────────────────────────────────────────────
    def _state_str(self):
        if self.state == self.STATE_IDLE:        return "⏸ 대기중"
        if self.state == self.STATE_GAS_MEAS:    return "🔴 가스버스 측정중"
        if self.state == self.STATE_HYDRO_MEAS:  return "🟢 수소버스 측정중"
        if self.state == self.STATE_SHOW_RESULT: return "✅ 결과표시중"
        return "알수없음"

    # ──────────────────────────────────────────────────────
    # 센서 읽기
    # ──────────────────────────────────────────────────────
    def read_sensor(self):
        if self.sensor is None:
            import urandom
            return (
                float(400 + (urandom.getrandbits(8) % 300)),
                float(20  + (urandom.getrandbits(5) % 10)),
                float(45  + (urandom.getrandbits(5) % 30))
            )
        try:
            for _ in range(10):
                if self.sensor.data_available():
                    co2, temp, humi = self.sensor.read_measurement()
                    if co2 and 300 <= co2 <= 5000:
                        return co2, temp, humi
                time.sleep_ms(500)
        except Exception as e:
            print("센서 오류:", e)
        return None, None, None

    # ──────────────────────────────────────────────────────
    # 측정 제어
    # ──────────────────────────────────────────────────────
    def start_measurement(self, bus_type):
        self.current_readings = []
        self.current_type     = bus_type
        self.state = self.STATE_GAS_MEAS if bus_type == "gas" else self.STATE_HYDRO_MEAS
        label = "가스버스" if bus_type == "gas" else "수소전기버스"
        print("\n[" + label + "] 측정 시작!")
        self.led.on()

    def stop_measurement(self):
        self.led.off()
        if not self.current_readings:
            print("경고: 데이터 없음")
            self.state = self.STATE_IDLE
            return

        co2_v  = [r["co2"]  for r in self.current_readings]
        temp_v = [r["temp"] for r in self.current_readings]
        humi_v = [r["humi"] for r in self.current_readings]

        sessions = (self.gas_sessions if self.current_type == "gas"
                    else self.hydro_sessions)

        session = {
            "type"       : self.current_type,
            "session_no" : len(sessions) + 1,
            "count"      : len(self.current_readings),
            "start_time" : self.current_readings[0]["time"],
            "end_time"   : self.current_readings[-1]["time"],
            "readings"   : self.current_readings[:],
            "stats": {
                "co2" : {"avg":calc_mean(co2_v),"max":calc_max(co2_v),
                         "min":calc_min(co2_v),"stdev":calc_stdev(co2_v)},
                "temp": {"avg":calc_mean(temp_v),"max":calc_max(temp_v),
                         "min":calc_min(temp_v)},
                "humi": {"avg":calc_mean(humi_v),"max":calc_max(humi_v),
                         "min":calc_min(humi_v)},
            }
        }
        sessions.append(session)

        btype = "가스버스" if self.current_type == "gas" else "수소전기버스"
        avg = session["stats"]["co2"]["avg"]
        lvl, _ = evaluate_co2(avg)
        print("\n[" + btype + "] 세션#" + str(session["session_no"]) +
              " 종료 | 평균CO2:" + str(avg) + "ppm | " + lvl)

        self.state        = self.STATE_SHOW_RESULT
        self.current_type = None

    def reset_all(self):
        self.gas_sessions     = []
        self.hydro_sessions   = []
        self.all_recent       = []
        self.current_readings = []
        self.current_type     = None
        self.state            = self.STATE_IDLE
        print("전체 초기화 완료")

    # ──────────────────────────────────────────────────────
    # 전체 평균
    # ──────────────────────────────────────────────────────
    def _get_overall_avg(self, bus_type):
        s = self.gas_sessions if bus_type == "gas" else self.hydro_sessions
        if not s:
            return None
        vals = [r["co2"] for ss in s for r in ss["readings"]]
        return calc_mean(vals) if vals else None

    # ──────────────────────────────────────────────────────
    # 디바운스
    # ──────────────────────────────────────────────────────
    def _debounce_ok(self):
        now = utime.ticks_ms()
        if utime.ticks_diff(now, self.last_btn_time) > self.DEBOUNCE_MS:
            self.last_btn_time = now
            return True
        return False

    # ──────────────────────────────────────────────────────
    # 버튼 체크 (물리 버튼)
    # ──────────────────────────────────────────────────────
    def check_buttons(self):
        btn_a = self.btn_start.value() == 0
        btn_b = self.btn_stop.value()  == 0
        if (btn_a or btn_b) and self._debounce_ok():
            if self.state == self.STATE_IDLE:
                if btn_a:
                    self.start_measurement("gas")
                elif btn_b:
                    self.start_measurement("hydro")
            elif self.state in (self.STATE_GAS_MEAS, self.STATE_HYDRO_MEAS):
                if btn_b:
                    self.stop_measurement()
            elif self.state == self.STATE_SHOW_RESULT:
                self.state = self.STATE_IDLE
            time.sleep_ms(50)

    # ──────────────────────────────────────────────────────
    # LED 깜빡임
    # ──────────────────────────────────────────────────────
    def _blink_led(self):
        if self.state in (self.STATE_GAS_MEAS, self.STATE_HYDRO_MEAS):
            self.led_tick += 1
            if self.led_tick % 2 == 0:
                self.led.toggle()
        else:
            self.led.off()

    # ──────────────────────────────────────────────────────
    # 웹 서버 설정
    # ──────────────────────────────────────────────────────
    def setup_server(self):
        self.server_sock = socket.socket()
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(("0.0.0.0", 80))
        self.server_sock.listen(1)
        self.server_sock.setblocking(False)
        print("웹 서버 시작: http://" + str(self.ip))

    # ──────────────────────────────────────────────────────
    # HTTP 요청 처리
    # ──────────────────────────────────────────────────────
    def handle_request(self):
        try:
            conn, addr = self.server_sock.accept()
            conn.settimeout(2.0)
            try:
                req = conn.recv(512).decode("utf-8")
            except:
                conn.close()
                return

            # URL 파싱
            path = "/"
            if req:
                line = req.split("\r\n")[0]
                parts = line.split(" ")
                if len(parts) >= 2:
                    path = parts[1]

            print("요청:", path)

            # 라우팅
            if path == "/start_gas":
                self.start_measurement("gas")
                self._redirect(conn, "/")
            elif path == "/start_hydro":
                self.start_measurement("hydro")
                self._redirect(conn, "/")
            elif path == "/stop":
                self.stop_measurement()
                self._redirect(conn, "/")
            elif path == "/compare":
                self.state = self.STATE_SHOW_RESULT
                self._redirect(conn, "/")
            elif path == "/reset":
                self.reset_all()
                self._redirect(conn, "/")
            elif path == "/api":
                # JSON API
                lvl, clr = evaluate_co2(self.last_co2)
                data = {
                    "co2"  : self.last_co2,
                    "temp" : self.last_temp,
                    "humi" : self.last_humi,
                    "level": lvl,
                    "color": clr,
                    "state": self._state_str(),
                    "gas_sessions"  : len(self.gas_sessions),
                    "hydro_sessions": len(self.hydro_sessions),
                    "g_avg": self._get_overall_avg("gas"),
                    "h_avg": self._get_overall_avg("hydro"),
                }
                body = json.dumps(data)
                resp = ("HTTP/1.1 200 OK\r\n"
                        "Content-Type: application/json\r\n"
                        "Connection: close\r\n\r\n" + body)
                conn.send(resp.encode())
                conn.close()
            else:
                # 메인 페이지
                html = build_html(self)
                resp = ("HTTP/1.1 200 OK\r\n"
                        "Content-Type: text/html; charset=utf-8\r\n"
                        "Connection: close\r\n\r\n" + html)
                conn.send(resp.encode())
                conn.close()

        except OSError:
            pass  # 연결 없음 (비차단)
        except Exception as e:
            print("요청 처리 오류:", e)

    def _redirect(self, conn, url):
        resp = ("HTTP/1.1 302 Found\r\n"
                "Location: " + url + "\r\n"
                "Connection: close\r\n\r\n")
        conn.send(resp.encode())
        conn.close()

    # ──────────────────────────────────────────────────────
    # 메인 루프
    # ──────────────────────────────────────────────────────
    def run(self):
        # WiFi 연결
        self.ip = connect_wifi()
        if not self.ip:
            print("WiFi 실패. 오프라인 모드 (시리얼만 사용)")
        else:
            self.setup_server()

        # 워밍업
        print("센서 워밍업 (5초)...")
        for i in range(5, 0, -1):
            print("  " + str(i) + "초...")
            time.sleep(1)
        print("준비 완료!")
        if self.ip:
            print("브라우저에서 http://" + self.ip + " 접속하세요!")
        print()

        last_measure = utime.ticks_ms()
        loop_count   = 0

        while True:
            loop_count += 1

            # 물리 버튼 체크
            self.check_buttons()

            # 웹 요청 처리
            if self.server_sock:
                self.handle_request()

            # ── 5초마다 센서 측정 ────────────────────────
            now = utime.ticks_ms()
            if utime.ticks_diff(now, last_measure) >= MEASURE_INTERVAL * 1000:
                last_measure = now

                co2, temp, humi = self.read_sensor()

                if co2 is not None:
                    self.last_co2  = co2
                    self.last_temp = temp
                    self.last_humi = humi
                    lvl, _ = evaluate_co2(co2)

                    # 측정 중이면 기록
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

                        # 최근 기록에도 추가 (최대 100개)
                        self.all_recent.append(record)
                        if len(self.all_recent) > 100:
                            self.all_recent.pop(0)

                        count = len(self.current_readings)
                        print("[" + elapsed_str() + "] "
                              "#" + zero_pad(count, 3) +
                              " | CO2:" + str(co2) +
                              " | T:" + str(temp) +
                              " | H:" + str(humi) +
                              " | " + lvl)

                    # 대기 중 로그
                    elif self.state == self.STATE_IDLE:
                        if loop_count % 3 == 1:
                            print("[대기] " + elapsed_str() +
                                  " | CO2:" + str(co2) +
                                  " ppm | " + lvl)

                self._blink_led()

            time.sleep_ms(100)   # CPU 과부하 방지

# ============================================================
# 시작
# ============================================================
monitor = BusAirMonitor()
monitor.run()
