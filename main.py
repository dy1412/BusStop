# ============================================================
#  버스 공기질 비교 시스템 - 밝은 테마 + 실시간 그래프
#  라즈베리파이 피코 W + SCD30
#  당곡고등학교 환경 탐구 프로젝트
# ============================================================

import time
import utime
import json
import network
import socket
import gc
from machine import Pin, I2C

# ============================================================
# 설정
# ============================================================
WIFI_SSID        = "app"
WIFI_PASSWORD    = "20242024"
IDLE_INTERVAL    = 15
MEASURE_INTERVAL = 1

# ============================================================
# 헬퍼
# ============================================================
def zero_pad(n, w):
    s = str(n)
    return "0" * (w - len(s)) + s if len(s) < w else s

def elapsed_str():
    t = utime.ticks_diff(utime.ticks_ms(), _boot) // 1000
    return (zero_pad(t//3600,2)+":"+
            zero_pad((t%3600)//60,2)+":"+
            zero_pad(t%60,2))

_boot = utime.ticks_ms()

# ============================================================
# SCD30
# ============================================================
class SCD30Driver:
    ADDR = 0x61
    CMD_START = b'\x00\x10'
    CMD_READY = b'\x02\x02'
    CMD_READ  = b'\x03\x00'

    def __init__(self, i2c):
        self.i2c = i2c
        pb  = bytes([0,0])
        crc = self._crc8(pb)
        self.i2c.writeto(self.ADDR, self.CMD_START + pb + bytes([crc]))
        time.sleep(2)

    def _crc8(self, data):
        crc = 0xFF
        for b in data:
            crc ^= b
            for _ in range(8):
                crc = ((crc<<1)^0x31) if (crc&0x80) else (crc<<1)
                crc &= 0xFF
        return crc

    def ready(self):
        try:
            self.i2c.writeto(self.ADDR, self.CMD_READY)
            time.sleep_ms(3)
            return self.i2c.readfrom(self.ADDR, 3)[1] == 1
        except:
            return False

    def _b2f(self, a,b,c,d):
        v = (a<<24)|(b<<16)|(c<<8)|d
        s = -1 if (v>>31) else 1
        e = ((v>>23)&0xFF)-127
        m = (v&0x7FFFFF)|0x800000
        return round(s*m*(2**(e-23)),1)

    def read(self):
        try:
            self.i2c.writeto(self.ADDR, self.CMD_READ)
            time.sleep_ms(3)
            b = self.i2c.readfrom(self.ADDR, 18)
            return (self._b2f(b[0],b[1],b[3],b[4]),
                    self._b2f(b[6],b[7],b[9],b[10]),
                    self._b2f(b[12],b[13],b[15],b[16]))
        except:
            return None,None,None

# ============================================================
# 통계
# ============================================================
def mean(v): return round(sum(v)/len(v),1) if v else 0.0
def vmax(v): return round(max(v),1) if v else 0.0
def vmin(v): return round(min(v),1) if v else 0.0
def stdev(v):
    if len(v)<2: return 0.0
    m=mean(v)
    return round((sum((x-m)**2 for x in v)/len(v))**0.5,1)

def grade(ppm):
    if ppm is None: return "알수없음","#888"
    if ppm<450:  return "매우좋음","#27ae60"
    if ppm<700:  return "좋음",    "#2ecc71"
    if ppm<1000: return "보통",    "#f39c12"
    if ppm<2000: return "나쁨",    "#e67e22"
    if ppm<5000: return "매우나쁨","#e74c3c"
    return            "위험",      "#8e44ad"

# ============================================================
# WiFi
# ============================================================
def connect_wifi():
    w = network.WLAN(network.STA_IF)
    w.active(True)
    w.connect(WIFI_SSID, WIFI_PASSWORD)
    print("WiFi", end="")
    for _ in range(20):
        if w.isconnected(): break
        print(".",end=""); time.sleep(1)
    if w.isconnected():
        ip = w.ifconfig()[0]
        print("\nIP:", ip)
        return ip
    print("\n실패")
    return None

# ============================================================
# 청크 전송
# ============================================================
def send_chunk(conn, text):
    try:
        data = text.encode("utf-8") if isinstance(text,str) else text
        pos  = 0
        while pos < len(data):
            conn.send(data[pos:pos+512])
            pos += 512
            time.sleep_ms(5)
    except:
        pass

# ============================================================
# ★ 정적 HTML (한 번만 전송, JS가 API 폴링)
# ============================================================
HTML_PAGE = """<!DOCTYPE html>
<html lang='ko'>
<head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>버스 공기질 비교 | 당곡고등학교</title>
<script src='https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js'></script>
<style>
:root{
  --bg:#f0f4f8;--card:#fff;--border:#e2e8f0;
  --text:#1a202c;--sub:#718096;
  --gas:#e53e3e;--hydro:#38a169;
  --blue:#3182ce;--yellow:#d69e2e;
  --shadow:0 2px 12px rgba(0,0,0,.08);
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);
     font-family:'Segoe UI',sans-serif;min-height:100vh}

/* 헤더 */
.hd{background:linear-gradient(135deg,#667eea,#764ba2);
    padding:20px 28px;color:white;
    box-shadow:0 4px 20px rgba(102,126,234,.4)}
.hd h1{font-size:22px;font-weight:700;letter-spacing:-.3px}
.hd p{font-size:12px;opacity:.85;margin-top:4px}
.hd-row{display:flex;justify-content:space-between;
        align-items:flex-end;flex-wrap:wrap;gap:8px}
.hd-badge{background:rgba(255,255,255,.2);
          padding:4px 12px;border-radius:20px;
          font-size:12px;font-weight:600}

/* 상태 바 */
.sb{background:#fff;padding:10px 28px;
    display:flex;flex-wrap:wrap;gap:16px;align-items:center;
    border-bottom:1px solid var(--border);
    box-shadow:var(--shadow)}
.si{font-size:12px;color:var(--sub);display:flex;
    align-items:center;gap:5px}
.sv{font-weight:700;font-size:13px;color:var(--text)}
.dot{width:8px;height:8px;border-radius:50%;
     display:inline-block}

/* 측정 배너 */
.banner{padding:12px 28px;text-align:center;
        font-weight:700;font-size:14px;color:white;
        display:none}
.banner.show{display:block}

/* 카드 그리드 */
.cg{display:grid;grid-template-columns:repeat(4,1fr);
    gap:16px;padding:20px 28px 0}
.card{background:var(--card);border-radius:16px;
      padding:20px;box-shadow:var(--shadow);
      border:1px solid var(--border);
      transition:transform .2s}
.card:hover{transform:translateY(-2px)}
.cl{font-size:11px;color:var(--sub);font-weight:600;
    text-transform:uppercase;letter-spacing:.5px;
    margin-bottom:8px}
.cv{font-size:32px;font-weight:800;line-height:1}
.cu{font-size:11px;color:var(--sub);margin-top:4px}
.grade-badge{display:inline-block;padding:4px 12px;
             border-radius:20px;font-size:13px;
             font-weight:700;color:white;margin-top:4px}

/* 버튼 */
.sec{padding:20px 28px}
.sec-title{font-size:15px;font-weight:700;
           color:var(--text);margin-bottom:12px;
           display:flex;align-items:center;gap:8px}
.sec-title::before{content:'';display:inline-block;
                   width:4px;height:16px;
                   background:linear-gradient(135deg,#667eea,#764ba2);
                   border-radius:2px}
.bg{display:flex;gap:10px;flex-wrap:wrap}
.btn{padding:11px 22px;border:none;border-radius:10px;
     font-size:13px;font-weight:700;cursor:pointer;
     text-decoration:none;display:inline-flex;
     align-items:center;gap:6px;
     transition:all .2s;box-shadow:0 2px 8px rgba(0,0,0,.12)}
.btn:hover{transform:translateY(-1px);
           box-shadow:0 4px 16px rgba(0,0,0,.2)}
.btn:active{transform:translateY(0)}
.b-gas{background:linear-gradient(135deg,#e53e3e,#c53030);
       color:white}
.b-hyd{background:linear-gradient(135deg,#38a169,#276749);
       color:white}
.b-stp{background:linear-gradient(135deg,#ed8936,#c05621);
       color:white}
.b-ref{background:linear-gradient(135deg,#667eea,#764ba2);
       color:white}
.b-rst{background:#fff;color:var(--sub);
       border:1px solid var(--border)}
.b-dis{opacity:.4;pointer-events:none;cursor:not-allowed}

/* 그래프 */
.chart-grid{display:grid;grid-template-columns:1fr 1fr;
            gap:16px;padding:0 28px 20px}
.chart-card{background:var(--card);border-radius:16px;
            padding:20px;box-shadow:var(--shadow);
            border:1px solid var(--border)}
.chart-title{font-size:13px;font-weight:700;
             color:var(--text);margin-bottom:14px;
             display:flex;align-items:center;
             justify-content:space-between}
.chart-wrap{position:relative;height:200px}

/* 비교 카드 */
.cmp-grid{display:grid;grid-template-columns:1fr 1fr 1fr;
          gap:16px}
.cmp-card{background:var(--card);border-radius:16px;
          padding:18px;box-shadow:var(--shadow);
          border:1px solid var(--border);text-align:center}
.cmp-label{font-size:11px;color:var(--sub);font-weight:600;
           text-transform:uppercase;margin-bottom:8px}
.cmp-val{font-size:24px;font-weight:800}
.cmp-sub{font-size:11px;color:var(--sub);margin-top:4px}
.result-box{background:linear-gradient(135deg,#ebf8ff,#bee3f8);
            border:1px solid #90cdf4;border-radius:12px;
            padding:14px;text-align:center;
            font-size:13px;font-weight:600;
            color:#2b6cb0;margin-top:12px}

/* 탭 테이블 */
.tabs{display:flex;gap:4px;margin-bottom:-1px}
.tab{padding:9px 18px;background:var(--bg);
     color:var(--sub);cursor:pointer;
     font-size:12px;font-weight:700;
     border-radius:10px 10px 0 0;
     border:1px solid var(--border);
     border-bottom:none;transition:all .2s}
.tab.on{background:var(--card);color:var(--text)}
.tc{display:none;background:var(--card);
    border:1px solid var(--border);
    border-radius:0 10px 10px 10px;
    overflow:hidden;box-shadow:var(--shadow)}
.tc.on{display:block}
.tw{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:#f7fafc;color:var(--sub);padding:10px 12px;
   text-align:left;font-size:11px;font-weight:700;
   text-transform:uppercase;white-space:nowrap;
   border-bottom:1px solid var(--border)}
td{padding:9px 12px;border-bottom:1px solid #f0f4f8;
   color:var(--text);white-space:nowrap}
tr:last-child td{border-bottom:none}
tr:hover td{background:#f7fafc}

/* 반응형 */
@media(max-width:768px){
  .cg{grid-template-columns:repeat(2,1fr)}
  .chart-grid{grid-template-columns:1fr}
  .cmp-grid{grid-template-columns:1fr}
  .hd h1{font-size:17px}
  .sec,.cg,.chart-grid{padding-left:16px;padding-right:16px}
}
@media(max-width:480px){
  .cg{grid-template-columns:repeat(2,1fr)}
}
</style>
</head>
<body>

<!-- 헤더 -->
<div class='hd'>
  <div class='hd-row'>
    <div>
      <h1>🌿 버스 공기질 비교 시스템</h1>
      <p>당곡고등학교 환경 탐구 프로젝트 | SCD30 센서 | Raspberry Pi Pico W</p>
    </div>
    <div class='hd-badge' id='hd-state'>대기중</div>
  </div>
</div>

<!-- 측정 배너 -->
<div class='banner' id='banner'>측정중...</div>

<!-- 상태 바 -->
<div class='sb'>
  <div class='si'>
    <span class='dot' id='dot' style='background:#48bb78'></span>
    <span>상태: <span class='sv' id='sb-state'>대기중</span></span>
  </div>
  <div class='si'>경과: <span class='sv' id='sb-time'>00:00:00</span></div>
  <div class='si'>주기: <span class='sv' id='sb-interval'>15초</span></div>
  <div class='si'>
    <span class='dot' style='background:#e53e3e'></span>
    가스버스: <span class='sv' id='sb-gsess'>0회</span>
  </div>
  <div class='si'>
    <span class='dot' style='background:#38a169'></span>
    수소버스: <span class='sv' id='sb-hsess'>0회</span>
  </div>
  <div class='si' id='sb-count-wrap' style='display:none'>
    수집: <span class='sv' id='sb-count' style='color:#e53e3e'>0회</span>
  </div>
</div>

<!-- 실시간 카드 -->
<div class='cg'>
  <div class='card'>
    <div class='cl'>CO₂ 농도</div>
    <div class='cv' id='c-co2' style='color:#e53e3e'>---</div>
    <div class='cu'>ppm</div>
  </div>
  <div class='card'>
    <div class='cl'>온도</div>
    <div class='cv' id='c-temp' style='color:#3182ce'>---</div>
    <div class='cu'>°C</div>
  </div>
  <div class='card'>
    <div class='cl'>습도</div>
    <div class='cv' id='c-humi' style='color:#38a169'>---</div>
    <div class='cu'>%</div>
  </div>
  <div class='card'>
    <div class='cl'>공기질 등급</div>
    <div id='c-grade'>
      <span class='grade-badge' id='c-grade-badge'
            style='background:#48bb78'>---</span>
    </div>
    <div class='cu' style='margin-top:6px' id='c-grade-sub'></div>
  </div>
</div>

<!-- 제어 버튼 -->
<div class='sec'>
  <div class='sec-title'>측정 제어</div>
  <div class='bg'>
    <a class='btn b-gas' id='btn-gas'  href='/start_gas'>
      🚌 가스버스 측정 시작
    </a>
    <a class='btn b-hyd' id='btn-hyd'  href='/start_hydro'>
      🚍 수소버스 측정 시작
    </a>
    <a class='btn b-stp b-dis' id='btn-stp' href='/stop'>
      ⏹ 측정 종료
    </a>
    <a class='btn b-ref' href='javascript:void(0)'
       onclick='fetchData()'>🔄 새로고침</a>
    <a class='btn b-rst' href='/reset'
       onclick="return confirm('전체 데이터를 초기화하시겠습니까?')">
      🗑 초기화
    </a>
  </div>
</div>

<!-- ★ 실시간 그래프 -->
<div class='sec' style='padding-bottom:8px'>
  <div class='sec-title'>실시간 CO₂ 변화 그래프</div>
</div>
<div class='chart-grid'>

  <div class='chart-card'>
    <div class='chart-title'>
      <span>🚌 가스버스 CO₂ 변화</span>
      <span id='gas-chart-info'
            style='font-size:11px;color:#718096;font-weight:400'>
        데이터 없음
      </span>
    </div>
    <div class='chart-wrap'>
      <canvas id='gasChart'></canvas>
    </div>
  </div>

  <div class='chart-card'>
    <div class='chart-title'>
      <span>🚍 수소버스 CO₂ 변화</span>
      <span id='hyd-chart-info'
            style='font-size:11px;color:#718096;font-weight:400'>
        데이터 없음
      </span>
    </div>
    <div class='chart-wrap'>
      <canvas id='hydChart'></canvas>
    </div>
  </div>

  <div class='chart-card' style='grid-column:1/-1'>
    <div class='chart-title'>
      <span>📊 가스버스 vs 수소버스 비교</span>
      <span id='cmp-chart-info'
            style='font-size:11px;color:#718096;font-weight:400'>
        두 버스 모두 측정하면 비교됩니다
      </span>
    </div>
    <div class='chart-wrap' style='height:220px'>
      <canvas id='cmpChart'></canvas>
    </div>
  </div>

</div>

<!-- 비교 요약 -->
<div class='sec'>
  <div class='sec-title'>비교 요약</div>
  <div class='cmp-grid'>
    <div class='cmp-card' style='border-top:4px solid #e53e3e'>
      <div class='cmp-label'>🚌 가스버스 평균 CO₂</div>
      <div class='cmp-val' id='cmp-gas' style='color:#e53e3e'>-</div>
      <div class='cmp-sub' id='cmp-gas-sub'>세션 없음</div>
    </div>
    <div class='cmp-card' style='border-top:4px solid #38a169'>
      <div class='cmp-label'>🚍 수소버스 평균 CO₂</div>
      <div class='cmp-val' id='cmp-hyd' style='color:#38a169'>-</div>
      <div class='cmp-sub' id='cmp-hyd-sub'>세션 없음</div>
    </div>
    <div class='cmp-card' id='cmp-diff-card'
         style='border-top:4px solid #718096'>
      <div class='cmp-label'>차이 (가스 - 수소)</div>
      <div class='cmp-val' id='cmp-diff'>-</div>
      <div class='cmp-sub'>양수 = 가스버스가 높음</div>
    </div>
  </div>
  <div class='result-box' id='result-box' style='display:none'></div>
</div>

<!-- 세션 테이블 -->
<div class='sec'>
  <div class='sec-title'>세션별 측정 결과</div>
  <div class='tabs'>
    <div class='tab on' id='tg' onclick="showTab('g')"
         style='border-top:3px solid #e53e3e'>🚌 가스버스</div>
    <div class='tab'    id='th' onclick="showTab('h')"
         style=''>🚍 수소버스</div>
    <div class='tab'    id='tr' onclick="showTab('r')"
         style=''>📡 최근측정</div>
  </div>
  <div id='tc-g' class='tc on'>
    <div class='tw'><table id='tbl-gas'>
      <thead><tr>
        <th>#</th><th>시작</th><th>종료</th><th>횟수</th>
        <th>평균CO₂</th><th>최대</th><th>최소</th><th>등급</th>
      </tr></thead>
      <tbody id='tbody-gas'>
        <tr><td colspan='8' style='text-align:center;color:#718096;padding:20px'>
          데이터 없음</td></tr>
      </tbody>
    </table></div>
  </div>
  <div id='tc-h' class='tc'>
    <div class='tw'><table id='tbl-hyd'>
      <thead><tr>
        <th>#</th><th>시작</th><th>종료</th><th>횟수</th>
        <th>평균CO₂</th><th>최대</th><th>최소</th><th>등급</th>
      </tr></thead>
      <tbody id='tbody-hyd'>
        <tr><td colspan='8' style='text-align:center;color:#718096;padding:20px'>
          데이터 없음</td></tr>
      </tbody>
    </table></div>
  </div>
  <div id='tc-r' class='tc'>
    <div class='tw'><table>
      <thead><tr>
        <th>시각</th><th>버스</th>
        <th>CO₂(ppm)</th><th>온도</th><th>습도</th><th>등급</th>
      </tr></thead>
      <tbody id='tbody-rec'>
        <tr><td colspan='6' style='text-align:center;color:#718096;padding:20px'>
          데이터 없음</td></tr>
      </tbody>
    </table></div>
  </div>
</div>

<div style='height:40px'></div>

<script>
// ============================================================
// Chart.js 초기화
// ============================================================
const chartOpts = (label, color) => ({
  type: 'line',
  data: {
    labels: [],
    datasets: [{
      label: label,
      data: [],
      borderColor: color,
      backgroundColor: color + '18',
      borderWidth: 2.5,
      pointRadius: 3,
      pointBackgroundColor: color,
      tension: 0.35,
      fill: true
    }]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 300 },
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: ctx => ' CO₂: ' + ctx.parsed.y + ' ppm'
        }
      }
    },
    scales: {
      x: {
        ticks: { maxTicksLimit: 8, font: { size: 10 },
                 color: '#718096' },
        grid:  { color: '#f0f4f8' }
      },
      y: {
        ticks: { font: { size: 10 }, color: '#718096',
                 callback: v => v + ' ppm' },
        grid:  { color: '#f0f4f8' }
      }
    }
  }
});

// 비교 그래프 (두 데이터셋)
const cmpOpts = {
  type: 'line',
  data: {
    labels: [],
    datasets: [
      {
        label: '가스버스',
        data: [],
        borderColor: '#e53e3e',
        backgroundColor: '#e53e3e18',
        borderWidth: 2.5,
        pointRadius: 2,
        tension: 0.35,
        fill: true
      },
      {
        label: '수소버스',
        data: [],
        borderColor: '#38a169',
        backgroundColor: '#38a16918',
        borderWidth: 2.5,
        pointRadius: 2,
        tension: 0.35,
        fill: true
      }
    ]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 300 },
    plugins: {
      legend: {
        display: true,
        position: 'top',
        labels: { font: { size: 11 }, color: '#1a202c',
                  usePointStyle: true }
      },
      tooltip: {
        callbacks: {
          label: ctx => ' ' + ctx.dataset.label +
                        ': ' + ctx.parsed.y + ' ppm'
        }
      }
    },
    scales: {
      x: {
        ticks: { maxTicksLimit: 10, font: { size: 10 },
                 color: '#718096' },
        grid:  { color: '#f0f4f8' }
      },
      y: {
        ticks: { font: { size: 10 }, color: '#718096',
                 callback: v => v + ' ppm' },
        grid:  { color: '#f0f4f8' }
      }
    }
  }
};

const gasChart = new Chart(
  document.getElementById('gasChart'), chartOpts('가스버스 CO₂','#e53e3e'));
const hydChart = new Chart(
  document.getElementById('hydChart'), chartOpts('수소버스 CO₂','#38a169'));
const cmpChart = new Chart(
  document.getElementById('cmpChart'), cmpOpts);

// 그래프에 최대 몇 개 포인트 표시
const MAX_PTS = 60;

function addPoint(chart, label, value) {
  chart.data.labels.push(label);
  chart.data.datasets[0].data.push(value);
  if (chart.data.labels.length > MAX_PTS) {
    chart.data.labels.shift();
    chart.data.datasets[0].data.shift();
  }
  chart.update('none');
}

// ============================================================
// API 폴링
// ============================================================
let lastState = '';

function fetchData() {
  fetch('/api')
    .then(r => r.json())
    .then(d => updateUI(d))
    .catch(e => console.log('API 오류:', e));
}

function updateUI(d) {
  // 실시간 수치
  document.getElementById('c-co2').textContent =
    d.co2 !== null ? d.co2 : '---';
  document.getElementById('c-temp').textContent =
    d.temp !== null ? d.temp : '---';
  document.getElementById('c-humi').textContent =
    d.humi !== null ? d.humi : '---';

  // 등급 뱃지
  const gb = document.getElementById('c-grade-badge');
  gb.textContent    = d.level;
  gb.style.background = d.color;

  // 상태 바
  document.getElementById('sb-state').textContent  = d.state;
  document.getElementById('hd-state').textContent  = d.state;
  document.getElementById('sb-time').textContent   = d.elapsed;
  document.getElementById('sb-interval').textContent =
    d.measuring ? '1초 (측정중)' : '15초 (대기중)';
  document.getElementById('sb-gsess').textContent  = d.g_sess + '회';
  document.getElementById('sb-hsess').textContent  = d.h_sess + '회';

  // 수집 횟수
  const cw = document.getElementById('sb-count-wrap');
  if (d.measuring) {
    cw.style.display = '';
    document.getElementById('sb-count').textContent = d.count + '회';
  } else {
    cw.style.display = 'none';
  }

  // 배너
  const banner = document.getElementById('banner');
  if (d.measuring) {
    banner.classList.add('show');
    banner.style.background = d.bus_type === 'gas'
      ? 'linear-gradient(135deg,#e53e3e,#c53030)'
      : 'linear-gradient(135deg,#38a169,#276749)';
    banner.textContent =
      (d.bus_type === 'gas' ? '🚌 가스버스' : '🚍 수소전기버스') +
      ' 측정중 | 수집 ' + d.count + '회 | 1초마다 수집 | 종료하려면 [측정 종료] 클릭';
  } else {
    banner.classList.remove('show');
  }

  // 버튼 활성/비활성
  const bg  = document.getElementById('btn-gas');
  const bh  = document.getElementById('btn-hyd');
  const bs  = document.getElementById('btn-stp');
  if (d.measuring) {
    bg.classList.add('b-dis');
    bh.classList.add('b-dis');
    bs.classList.remove('b-dis');
  } else {
    bg.classList.remove('b-dis');
    bh.classList.remove('b-dis');
    bs.classList.add('b-dis');
  }

  // 상태 dot 색
  const dot = document.getElementById('dot');
  if (d.measuring) {
    dot.style.background = d.bus_type === 'gas' ? '#e53e3e' : '#38a169';
  } else {
    dot.style.background = '#48bb78';
  }

  // ── 그래프 업데이트 ──────────────────────────────────
  if (d.co2 !== null && d.measuring) {
    const t = d.elapsed;
    if (d.bus_type === 'gas') {
      addPoint(gasChart, t, d.co2);
    } else if (d.bus_type === 'hydro') {
      addPoint(hydChart, t, d.co2);
    }
  }

  // 비교 그래프 (세션 평균값)
  updateCmpChart(d);

  // 비교 수치
  if (d.g_avg !== null) {
    document.getElementById('cmp-gas').textContent = d.g_avg + ' ppm';
    document.getElementById('cmp-gas-sub').textContent =
      d.g_sess + '개 세션';
  }
  if (d.h_avg !== null) {
    document.getElementById('cmp-hyd').textContent = d.h_avg + ' ppm';
    document.getElementById('cmp-hyd-sub').textContent =
      d.h_sess + '개 세션';
  }

  if (d.g_avg !== null && d.h_avg !== null) {
    const diff = Math.round((d.g_avg - d.h_avg) * 10) / 10;
    const dc   = document.getElementById('cmp-diff-card');
    const dv   = document.getElementById('cmp-diff');
    const rb   = document.getElementById('result-box');

    dv.textContent = (diff >= 0 ? '+' : '') + diff + ' ppm';
    if (diff > 10) {
      dv.style.color = '#e53e3e';
      dc.style.borderTopColor = '#e53e3e';
      rb.style.display = '';
      rb.textContent =
        '✅ 수소전기버스가 ' + diff + ' ppm 더 낮습니다! 친환경적입니다.';
    } else if (diff > 0) {
      dv.style.color = '#d69e2e';
      dc.style.borderTopColor = '#d69e2e';
      rb.style.display = '';
      rb.textContent = '🔄 수소버스가 약간 낮습니다 (' + diff + ' ppm)';
    } else {
      dv.style.color = '#38a169';
      dc.style.borderTopColor = '#38a169';
      rb.style.display = 'none';
    }
  }

  // 세션 테이블
  if (d.sessions_updated) {
    updateTables(d);
  }

  lastState = d.state;
}

// 비교 그래프: 세션별 평균값 표시
function updateCmpChart(d) {
  const gc = cmpChart.data.datasets[0];
  const hc = cmpChart.data.datasets[1];

  if (d.gas_sessions && d.gas_sessions.length > 0) {
    gc.data = d.gas_sessions.map(s => s.avg);
    const labels = d.gas_sessions.map(s => '#' + s.no);
    cmpChart.data.labels = labels;
    document.getElementById('cmp-chart-info').textContent =
      '최대: ' + Math.max(...gc.data) + ' ppm';
  }
  if (d.hydro_sessions && d.hydro_sessions.length > 0) {
    hc.data = d.hydro_sessions.map(s => s.avg);
    const labels = d.hydro_sessions.map(s => '#' + s.no);
    if (cmpChart.data.labels.length < labels.length) {
      cmpChart.data.labels = labels;
    }
  }
  if (d.gas_sessions && d.hydro_sessions &&
      (d.gas_sessions.length > 0 || d.hydro_sessions.length > 0)) {
    cmpChart.update('none');
  }
}

// 세션 테이블 업데이트
function updateTables(d) {
  const gradeColor = {
    '매우좋음':'#27ae60','좋음':'#2ecc71','보통':'#d69e2e',
    '나쁨':'#e67e22','매우나쁨':'#e53e3e','위험':'#8e44ad'
  };

  // 가스버스 테이블
  if (d.gas_sessions) {
    const tb = document.getElementById('tbody-gas');
    if (d.gas_sessions.length === 0) {
      tb.innerHTML = "<tr><td colspan='8' style='text-align:center;" +
        "color:#718096;padding:20px'>데이터 없음</td></tr>";
    } else {
      tb.innerHTML = d.gas_sessions.map(s => {
        const c = gradeColor[s.grade] || '#718096';
        return "<tr>" +
          "<td>#" + s.no + "</td>" +
          "<td>" + s.start + "</td>" +
          "<td>" + s.end + "</td>" +
          "<td>" + s.count + "회</td>" +
          "<td style='color:" + c + ";font-weight:700'>" + s.avg + "</td>" +
          "<td>" + s.max + "</td>" +
          "<td>" + s.min + "</td>" +
          "<td><span style='background:" + c + ";color:white;" +
          "padding:2px 8px;border-radius:10px;font-size:11px'>" +
          s.grade + "</span></td></tr>";
      }).join('');
    }
  }

  // 수소버스 테이블
  if (d.hydro_sessions) {
    const tb = document.getElementById('tbody-hyd');
    if (d.hydro_sessions.length === 0) {
      tb.innerHTML = "<tr><td colspan='8' style='text-align:center;" +
        "color:#718096;padding:20px'>데이터 없음</td></tr>";
    } else {
      tb.innerHTML = d.hydro_sessions.map(s => {
        const c = gradeColor[s.grade] || '#718096';
        return "<tr>" +
          "<td>#" + s.no + "</td>" +
          "<td>" + s.start + "</td>" +
          "<td>" + s.end + "</td>" +
          "<td>" + s.count + "회</td>" +
          "<td style='color:" + c + ";font-weight:700'>" + s.avg + "</td>" +
          "<td>" + s.max + "</td>" +
          "<td>" + s.min + "</td>" +
          "<td><span style='background:" + c + ";color:white;" +
          "padding:2px 8px;border-radius:10px;font-size:11px'>" +
          s.grade + "</span></td></tr>";
      }).join('');
    }
  }

  // 최근 기록
  if (d.recent) {
    const tb = document.getElementById('tbody-rec');
    if (d.recent.length === 0) {
      tb.innerHTML = "<tr><td colspan='6' style='text-align:center;" +
        "color:#718096;padding:20px'>기록 없음</td></tr>";
    } else {
      tb.innerHTML = d.recent.map(r => {
        const c = gradeColor[r.grade] || '#718096';
        return "<tr>" +
          "<td>" + r.time + "</td>" +
          "<td>" + r.bus + "</td>" +
          "<td style='color:" + c + ";font-weight:700'>" + r.co2 + "</td>" +
          "<td>" + r.temp + "</td>" +
          "<td>" + r.humi + "</td>" +
          "<td><span style='background:" + c + ";color:white;" +
          "padding:2px 8px;border-radius:10px;font-size:11px'>" +
          r.grade + "</span></td></tr>";
      }).join('');
    }
  }
}

// 탭 전환
function showTab(n) {
  ['g','h','r'].forEach(x => {
    document.getElementById('tc-'+x).classList.remove('on');
    document.getElementById('t'+x).classList.remove('on');
  });
  document.getElementById('tc-'+n).classList.add('on');
  document.getElementById('t'+n).classList.add('on');
}

// ============================================================
// 폴링 시작 (측정 중 2초, 대기 중 5초)
// ============================================================
let pollTimer = null;

function startPolling() {
  fetchData();
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    fetchData();
  }, 2000);
}

startPolling();
</script>
</body></html>"""

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
        self.all_recent     = []

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

        # 세션/테이블 변경 플래그
        self.sessions_updated = False

        self.server_sock = None
        self.ip          = None

    def _state_str(self):
        if self.state == self.STATE_IDLE:        return "대기중"
        if self.state == self.STATE_GAS_MEAS:    return "가스버스 측정중"
        if self.state == self.STATE_HYDRO_MEAS:  return "수소버스 측정중"
        if self.state == self.STATE_SHOW_RESULT: return "결과표시중"
        return "알수없음"

    def _interval_ms(self):
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
            for _ in range(5):
                if self.sensor.ready():
                    co2, temp, humi = self.sensor.read()
                    if co2 and 300 <= co2 <= 5000:
                        return co2, temp, humi
                time.sleep_ms(200)
        except Exception as e:
            print("센서 오류:", e)
        return None, None, None

    def start_measurement(self, bus_type):
        self.current_readings = []
        self.current_type     = bus_type
        self.state = (self.STATE_GAS_MEAS if bus_type == "gas"
                      else self.STATE_HYDRO_MEAS)
        self.last_measure_ms = utime.ticks_ms() - self._interval_ms()
        label = "가스버스" if bus_type == "gas" else "수소전기버스"
        print("[" + label + "] 측정 시작!")
        self.led.on()

    def stop_measurement(self):
        self.led.off()
        if not self.current_readings:
            self.state = self.STATE_IDLE
            return

        co2_v  = [r["co2"]  for r in self.current_readings]
        temp_v = [r["temp"] for r in self.current_readings]
        humi_v = [r["humi"] for r in self.current_readings]

        sessions = (self.gas_sessions if self.current_type == "gas"
                    else self.hydro_sessions)
        avg_co2  = mean(co2_v)
        lv, _    = grade(avg_co2)

        session = {
            "type"       : self.current_type,
            "session_no" : len(sessions) + 1,
            "count"      : len(self.current_readings),
            "start_time" : self.current_readings[0]["time"],
            "end_time"   : self.current_readings[-1]["time"],
            "readings"   : self.current_readings[:],
            "stats": {
                "co2" : {"avg":avg_co2,
                         "max":vmax(co2_v),
                         "min":vmin(co2_v),
                         "stdev":stdev(co2_v)},
                "temp": {"avg":mean(temp_v)},
                "humi": {"avg":mean(humi_v)}
            }
        }
        sessions.append(session)
        self.sessions_updated = True

        btype = "가스버스" if self.current_type == "gas" else "수소버스"
        print("[" + btype + "] 종료 | " +
              str(session["count"]) + "회 | " +
              str(avg_co2) + "ppm | " + lv)

        self.state        = self.STATE_IDLE
        self.current_type = None
        self.last_measure_ms = utime.ticks_ms()
        gc.collect()

    def reset_all(self):
        self.gas_sessions     = []
        self.hydro_sessions   = []
        self.all_recent       = []
        self.current_readings = []
        self.current_type     = None
        self.state            = self.STATE_IDLE
        self.last_measure_ms  = utime.ticks_ms()
        self.sessions_updated = True
        gc.collect()
        print("초기화 완료")

    def _overall_avg(self, bus_type):
        s = self.gas_sessions if bus_type == "gas" else self.hydro_sessions
        if not s: return None
        vals = [r["co2"] for ss in s for r in ss["readings"]]
        return mean(vals) if vals else None

    def _debounce_ok(self):
        now = utime.ticks_ms()
        if utime.ticks_diff(now, self.last_btn_time) > self.DEBOUNCE_MS:
            self.last_btn_time = now
            return True
        return False

    def check_buttons(self):
        a = self.btn_start.value() == 0
        b = self.btn_stop.value()  == 0
        if (a or b) and self._debounce_ok():
            if self.state == self.STATE_IDLE:
                if a: self.start_measurement("gas")
                elif b: self.start_measurement("hydro")
            elif self.state in (self.STATE_GAS_MEAS,
                                self.STATE_HYDRO_MEAS):
                if b: self.stop_measurement()
            elif self.state == self.STATE_SHOW_RESULT:
                self.state = self.STATE_IDLE
            time.sleep_ms(50)

    def _blink_led(self):
        if self.state in (self.STATE_GAS_MEAS, self.STATE_HYDRO_MEAS):
            self.led_tick += 1
            if self.led_tick % 2 == 0: self.led.toggle()
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

    # ── ★ API JSON 생성 (가볍게 유지) ───────────────────
    def build_api_json(self):
        is_meas  = self.state in (self.STATE_GAS_MEAS,
                                  self.STATE_HYDRO_MEAS)
        lv, clr  = grade(self.last_co2)
        g_avg    = self._overall_avg("gas")
        h_avg    = self._overall_avg("hydro")

        # 세션 요약 (가벼운 데이터만)
        def sess_summary(sessions):
            return [{"no"   : s["session_no"],
                     "start": s["start_time"],
                     "end"  : s["end_time"],
                     "count": s["count"],
                     "avg"  : s["stats"]["co2"]["avg"],
                     "max"  : s["stats"]["co2"]["max"],
                     "min"  : s["stats"]["co2"]["min"],
                     "grade": grade(s["stats"]["co2"]["avg"])[0]}
                    for s in sessions]

        # 최근 20개
        recent = [{"time" : r["time"],
                   "bus"  : r["bus_type"],
                   "co2"  : r["co2"],
                   "temp" : r["temp"],
                   "humi" : r["humi"],
                   "grade": grade(r["co2"])[0]}
                  for r in self.all_recent[-20:]]
        recent.reverse()

        data = {
            "co2"             : self.last_co2,
            "temp"            : self.last_temp,
            "humi"            : self.last_humi,
            "level"           : lv,
            "color"           : clr,
            "state"           : self._state_str(),
            "elapsed"         : elapsed_str(),
            "measuring"       : is_meas,
            "bus_type"        : self.current_type or "",
            "count"           : len(self.current_readings),
            "g_sess"          : len(self.gas_sessions),
            "h_sess"          : len(self.hydro_sessions),
            "g_avg"           : g_avg,
            "h_avg"           : h_avg,
            "gas_sessions"    : sess_summary(self.gas_sessions),
            "hydro_sessions"  : sess_summary(self.hydro_sessions),
            "recent"          : recent,
            "sessions_updated": self.sessions_updated,
        }
        self.sessions_updated = False
        return json.dumps(data)

    def handle_request(self):
        try:
            conn, addr = self.server_sock.accept()
        except OSError:
            return

        conn.settimeout(3.0)
        try:
            req = conn.recv(256).decode("utf-8")
        except:
            conn.close()
            return

        path = "/"
        try:
            parts = req.split("\r\n")[0].split(" ")
            if len(parts) >= 2:
                path = parts[1]
        except:
            pass

        gc.collect()

        try:
            if path == "/start_gas":
                self.start_measurement("gas")
                conn.send(b"HTTP/1.1 302 Found\r\n"
                          b"Location: /\r\nConnection: close\r\n\r\n")
                conn.close()

            elif path == "/start_hydro":
                self.start_measurement("hydro")
                conn.send(b"HTTP/1.1 302 Found\r\n"
                          b"Location: /\r\nConnection: close\r\n\r\n")
                conn.close()

            elif path == "/stop":
                self.stop_measurement()
                conn.send(b"HTTP/1.1 302 Found\r\n"
                          b"Location: /\r\nConnection: close\r\n\r\n")
                conn.close()

            elif path == "/reset":
                self.reset_all()
                conn.send(b"HTTP/1.1 302 Found\r\n"
                          b"Location: /\r\nConnection: close\r\n\r\n")
                conn.close()

            elif path == "/api":
                # ★ 가장 자주 호출 → 최대한 가볍게
                body = self.build_api_json()
                header = ("HTTP/1.1 200 OK\r\n"
                          "Content-Type: application/json\r\n"
                          "Content-Length: " + str(len(body)) + "\r\n"
                          "Connection: close\r\n\r\n")
                send_chunk(conn, header + body)
                conn.close()

            else:
                # ★ 정적 HTML 1회 전송
                header = ("HTTP/1.1 200 OK\r\n"
                          "Content-Type: text/html; charset=utf-8\r\n"
                          "Connection: close\r\n\r\n")
                conn.send(header.encode())
                send_chunk(conn, HTML_PAGE)
                conn.close()

        except Exception as e:
            print("응답 오류:", e)
            try: conn.close()
            except: pass

        gc.collect()

    def run(self):
        self.ip = connect_wifi()
        if self.ip:
            self.setup_server()
        else:
            print("오프라인 모드")

        print("워밍업 5초...")
        for i in range(5, 0, -1):
            print(" " + str(i) + "초"); time.sleep(1)
        print("준비 완료!")
        if self.ip:
            print("접속: http://" + self.ip)

        while True:
            self.check_buttons()

            if self.server_sock:
                self.handle_request()

            now = utime.ticks_ms()
            if utime.ticks_diff(now, self.last_measure_ms) >= self._interval_ms():
                self.last_measure_ms = now
                co2, temp, humi = self.read_sensor()

                if co2 is not None:
                    self.last_co2  = co2
                    self.last_temp = temp
                    self.last_humi = humi
                    lv, _ = grade(co2)

                    if self.state in (self.STATE_GAS_MEAS,
                                      self.STATE_HYDRO_MEAS):
                        bl = ("가스버스" if self.state == self.STATE_GAS_MEAS
                              else "수소버스")
                        rec = {"time"    : elapsed_str(),
                               "bus_type": bl,
                               "co2"     : co2,
                               "temp"    : temp,
                               "humi"    : humi}
                        self.current_readings.append(rec)
                        self.all_recent.append(rec)
                        if len(self.all_recent) > 100:
                            self.all_recent.pop(0)

                        cnt = len(self.current_readings)
                        print("#" + zero_pad(cnt,4) +
                              " CO2:" + str(co2) + " " + lv)
                        if cnt % 60 == 0:
                            gc.collect()
                    else:
                        print("[대기] CO2:" + str(co2) + " " + lv)

                self._blink_led()

            time.sleep_ms(100)

# ============================================================
# 시작
# ============================================================
gc.collect()
monitor = BusAirMonitor()
monitor.run()
