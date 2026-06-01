import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import datetime
import json
import statistics
import board
import busio
import adafruit_scd30

# ============================================================
# SCD30 센서 초기화
# ============================================================
try:
    i2c = busio.I2C(board.SCL, board.SDA, frequency=50000)
    scd30 = adafruit_scd30.SCD30(i2c)
    scd30.measurement_interval = 2
    scd30.self_calibration_enabled = False
    SENSOR_AVAILABLE = True
except Exception as e:
    print(f"⚠️ 센서 초기화 실패 (테스트 모드): {e}")
    SENSOR_AVAILABLE = False

# ============================================================
# 센서 읽기 함수
# ============================================================
def read_sensor():
    """SCD30 센서 데이터 읽기 (실패 시 None 반환)"""
    if not SENSOR_AVAILABLE:
        # 테스트용 더미 데이터
        import random
        return (
            round(400 + random.uniform(0, 300), 2),
            round(22 + random.uniform(-2, 5), 2),
            round(50 + random.uniform(-10, 20), 2)
        )
    try:
        for _ in range(10):
            if scd30.data_available:
                co2  = round(scd30.CO2, 2)
                temp = round(scd30.temperature, 2)
                humi = round(scd30.relative_humidity, 2)
                if 400 <= co2 <= 5000:
                    return co2, temp, humi
            time.sleep(1)
    except Exception as e:
        print(f"센서 오류: {e}")
    return None, None, None

# ============================================================
# CO2 등급 평가
# ============================================================
def evaluate_co2(ppm):
    if ppm is None:
        return "알 수 없음", "#888888"
    if ppm < 450:
        return "🟢 매우 좋음", "#27ae60"
    elif ppm < 700:
        return "🟢 좋음", "#2ecc71"
    elif ppm < 1000:
        return "🟡 보통", "#f39c12"
    elif ppm < 2000:
        return "🟠 나쁨", "#e67e22"
    elif ppm < 5000:
        return "🔴 매우 나쁨", "#e74c3c"
    else:
        return "🚨 위험", "#8e44ad"

# ============================================================
# 메인 앱 클래스
# ============================================================
class BusAirQualityApp:
    def __init__(self, root):
        self.root = root
        self.root.title("🚌 버스 공기질 비교 시스템 | 당곡고등학교")
        self.root.geometry("900x750")
        self.root.configure(bg="#1e272e")
        self.root.resizable(True, True)

        # ── 공유 데이터 ──────────────────────────────────────
        self.gas_bus_sessions    = []   # 가스버스 측정 세션 목록
        self.hydro_bus_sessions  = []   # 수소전기버스 측정 세션 목록

        # 측정 상태 플래그
        self.gas_measuring   = False
        self.hydro_measuring = False

        # 현재 세션 버퍼
        self.gas_current_readings   = []
        self.hydro_current_readings = []

        # 측정 스레드
        self.gas_thread   = None
        self.hydro_thread = None

        self._build_ui()
        self._start_live_monitor()

    # ────────────────────────────────────────────────────────
    # UI 빌드
    # ────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── 상단 헤더 ────────────────────────────────────────
        header = tk.Frame(self.root, bg="#2c3e50", pady=12)
        header.pack(fill="x")

        tk.Label(
            header,
            text="🌿 버스 공기질 비교 시스템",
            font=("Malgun Gothic", 20, "bold"),
            bg="#2c3e50", fg="#ecf0f1"
        ).pack()
        tk.Label(
            header,
            text="당곡고등학교 환경 탐구 프로젝트  |  SCD30 센서  |  Raspberry Pi Zero 2W",
            font=("Malgun Gothic", 10),
            bg="#2c3e50", fg="#95a5a6"
        ).pack()

        # ── 실시간 센서 표시 바 ──────────────────────────────
        live_bar = tk.Frame(self.root, bg="#34495e", pady=6)
        live_bar.pack(fill="x")

        tk.Label(live_bar, text="📡 실시간 센서",
                 font=("Malgun Gothic", 10, "bold"),
                 bg="#34495e", fg="#bdc3c7").pack(side="left", padx=15)

        self.live_co2_var  = tk.StringVar(value="CO₂: --- ppm")
        self.live_temp_var = tk.StringVar(value="온도: ---°C")
        self.live_humi_var = tk.StringVar(value="습도: ---%")
        self.live_lvl_var  = tk.StringVar(value="등급: ---")
        self.live_time_var = tk.StringVar(value="")

        for var, color in [
            (self.live_co2_var,  "#e74c3c"),
            (self.live_temp_var, "#3498db"),
            (self.live_humi_var, "#2ecc71"),
            (self.live_lvl_var,  "#f1c40f"),
        ]:
            tk.Label(live_bar, textvariable=var,
                     font=("Malgun Gothic", 10, "bold"),
                     bg="#34495e", fg=color).pack(side="left", padx=18)

        tk.Label(live_bar, textvariable=self.live_time_var,
                 font=("Malgun Gothic", 9),
                 bg="#34495e", fg="#7f8c8d").pack(side="right", padx=15)

        # ── 탭 노트북 ────────────────────────────────────────
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook",
                        background="#1e272e", borderwidth=0)
        style.configure("TNotebook.Tab",
                        background="#2c3e50", foreground="#ecf0f1",
                        font=("Malgun Gothic", 12, "bold"),
                        padding=[20, 8])
        style.map("TNotebook.Tab",
                  background=[("selected", "#e74c3c")],
                  foreground=[("selected", "white")])

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        # 탭 생성
        self.tab_gas   = tk.Frame(self.notebook, bg="#1e272e")
        self.tab_hydro = tk.Frame(self.notebook, bg="#1e272e")
        self.tab_compare = tk.Frame(self.notebook, bg="#1e272e")

        self.notebook.add(self.tab_gas,     text="🚌  가스버스 측정")
        self.notebook.add(self.tab_hydro,   text="🚍  수소전기버스 측정")
        self.notebook.add(self.tab_compare, text="📊  비교 결과")

        self._build_gas_tab()
        self._build_hydro_tab()
        self._build_compare_tab()

        # 탭 전환 시 비교 탭 자동 갱신
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_change)

    # ────────────────────────────────────────────────────────
    # 가스버스 탭
    # ────────────────────────────────────────────────────────
    def _build_gas_tab(self):
        self._build_measurement_tab(
            parent        = self.tab_gas,
            title         = "🚌 가스버스 측정",
            color_accent  = "#e74c3c",
            bus_type      = "gas"
        )

    # ────────────────────────────────────────────────────────
    # 수소전기버스 탭
    # ────────────────────────────────────────────────────────
    def _build_hydro_tab(self):
        self._build_measurement_tab(
            parent        = self.tab_hydro,
            title         = "🚍 수소전기버스 측정",
            color_accent  = "#27ae60",
            bus_type      = "hydro"
        )

    # ────────────────────────────────────────────────────────
    # 공통 측정 탭 빌드 (가스/수소 공용)
    # ────────────────────────────────────────────────────────
    def _build_measurement_tab(self, parent, title, color_accent, bus_type):
        """
        bus_type : "gas" | "hydro"
        """
        # 탭 제목
        tk.Label(parent, text=title,
                 font=("Malgun Gothic", 16, "bold"),
                 bg="#1e272e", fg=color_accent).pack(pady=(18, 4))

        tk.Label(parent,
                 text="버스가 오기 전 [측정 시작]을 누르고, 버스가 떠난 후 [측정 종료]를 누르세요.",
                 font=("Malgun Gothic", 10),
                 bg="#1e272e", fg="#bdc3c7").pack()

        # ── 상태 표시 ────────────────────────────────────────
        status_frame = tk.Frame(parent, bg="#2c3e50",
                                bd=0, relief="flat", pady=10)
        status_frame.pack(fill="x", padx=20, pady=(14, 0))

        status_var = tk.StringVar(value="⏸  대기 중 — [측정 시작] 버튼을 누르세요")
        status_lbl = tk.Label(status_frame, textvariable=status_var,
                              font=("Malgun Gothic", 11, "bold"),
                              bg="#2c3e50", fg="#f1c40f")
        status_lbl.pack()

        # ── 실시간 수치 카드 ─────────────────────────────────
        cards_frame = tk.Frame(parent, bg="#1e272e")
        cards_frame.pack(fill="x", padx=20, pady=10)

        co2_var  = tk.StringVar(value="---")
        temp_var = tk.StringVar(value="---")
        humi_var = tk.StringVar(value="---")
        lvl_var  = tk.StringVar(value="---")

        card_defs = [
            ("CO₂",  "ppm",  co2_var,  "#e74c3c"),
            ("온도",  "°C",   temp_var, "#3498db"),
            ("습도",  "%",    humi_var, "#2ecc71"),
            ("등급",  "",     lvl_var,  "#f1c40f"),
        ]
        for label, unit, var, clr in card_defs:
            card = tk.Frame(cards_frame, bg="#2c3e50",
                            bd=2, relief="groove")
            card.pack(side="left", expand=True, fill="both", padx=6, pady=4)
            tk.Label(card, text=label,
                     font=("Malgun Gothic", 9),
                     bg="#2c3e50", fg="#95a5a6").pack(pady=(8, 0))
            tk.Label(card, textvariable=var,
                     font=("Malgun Gothic", 18, "bold"),
                     bg="#2c3e50", fg=clr).pack()
            tk.Label(card, text=unit,
                     font=("Malgun Gothic", 9),
                     bg="#2c3e50", fg="#7f8c8d").pack(pady=(0, 8))

        # ── 버튼 영역 ────────────────────────────────────────
        btn_frame = tk.Frame(parent, bg="#1e272e")
        btn_frame.pack(pady=10)

        start_btn = tk.Button(
            btn_frame,
            text="▶  측정 시작",
            font=("Malgun Gothic", 13, "bold"),
            bg=color_accent, fg="white",
            activebackground="#c0392b",
            width=14, height=2,
            relief="flat", cursor="hand2",
            command=lambda: self._start_measurement(bus_type)
        )
        start_btn.pack(side="left", padx=10)

        stop_btn = tk.Button(
            btn_frame,
            text="⏹  측정 종료",
            font=("Malgun Gothic", 13, "bold"),
            bg="#7f8c8d", fg="white",
            activebackground="#636e72",
            width=14, height=2,
            relief="flat", cursor="hand2",
            state="disabled",
            command=lambda: self._stop_measurement(bus_type)
        )
        stop_btn.pack(side="left", padx=10)

        clear_btn = tk.Button(
            btn_frame,
            text="🗑  초기화",
            font=("Malgun Gothic", 11),
            bg="#2c3e50", fg="#ecf0f1",
            activebackground="#34495e",
            width=10, height=2,
            relief="flat", cursor="hand2",
            command=lambda: self._clear_sessions(bus_type)
        )
        clear_btn.pack(side="left", padx=10)

        # ── 측정 로그 ────────────────────────────────────────
        tk.Label(parent,
                 text="📋 측정 로그",
                 font=("Malgun Gothic", 11, "bold"),
                 bg="#1e272e", fg="#ecf0f1").pack(anchor="w", padx=22)

        log_frame = tk.Frame(parent, bg="#1e272e")
        log_frame.pack(fill="both", expand=True, padx=20, pady=(4, 6))

        log_box = tk.Text(
            log_frame,
            font=("Consolas", 9),
            bg="#0d1117", fg="#58d68d",
            insertbackground="white",
            state="disabled",
            relief="flat", bd=0,
            wrap="none"
        )
        scrollbar_y = tk.Scrollbar(log_frame, command=log_box.yview)
        scrollbar_x = tk.Scrollbar(log_frame, orient="horizontal",
                                   command=log_box.xview)
        log_box.configure(yscrollcommand=scrollbar_y.set,
                          xscrollcommand=scrollbar_x.set)

        scrollbar_y.pack(side="right",  fill="y")
        scrollbar_x.pack(side="bottom", fill="x")
        log_box.pack(side="left", fill="both", expand=True)

        # ── 세션 요약 라벨 ───────────────────────────────────
        session_var = tk.StringVar(value="측정 세션: 0회")
        tk.Label(parent, textvariable=session_var,
                 font=("Malgun Gothic", 9),
                 bg="#1e272e", fg="#7f8c8d").pack(anchor="e", padx=22, pady=2)

        # ── 위젯 참조 저장 ───────────────────────────────────
        if bus_type == "gas":
            self.gas_status_var  = status_var
            self.gas_co2_var     = co2_var
            self.gas_temp_var    = temp_var
            self.gas_humi_var    = humi_var
            self.gas_lvl_var     = lvl_var
            self.gas_log_box     = log_box
            self.gas_start_btn   = start_btn
            self.gas_stop_btn    = stop_btn
            self.gas_session_var = session_var
        else:
            self.hydro_status_var  = status_var
            self.hydro_co2_var     = co2_var
            self.hydro_temp_var    = temp_var
            self.hydro_humi_var    = humi_var
            self.hydro_lvl_var     = lvl_var
            self.hydro_log_box     = log_box
            self.hydro_start_btn   = start_btn
            self.hydro_stop_btn    = stop_btn
            self.hydro_session_var = session_var

    # ────────────────────────────────────────────────────────
    # 비교 결과 탭
    # ────────────────────────────────────────────────────────
    def _build_compare_tab(self):
        parent = self.tab_compare

        tk.Label(parent,
                 text="📊 가스버스 vs 수소전기버스 비교",
                 font=("Malgun Gothic", 16, "bold"),
                 bg="#1e272e", fg="#ecf0f1").pack(pady=(18, 4))

        # 갱신 버튼
        tk.Button(parent,
                  text="🔄 결과 갱신",
                  font=("Malgun Gothic", 11, "bold"),
                  bg="#8e44ad", fg="white",
                  relief="flat", cursor="hand2",
                  padx=20, pady=6,
                  command=self._update_compare_tab
                  ).pack(pady=(0, 10))

        # 결과 텍스트 박스
        result_frame = tk.Frame(parent, bg="#1e272e")
        result_frame.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        self.compare_box = tk.Text(
            result_frame,
            font=("Malgun Gothic", 11),
            bg="#0d1117", fg="#ecf0f1",
            state="disabled",
            relief="flat", bd=0,
            wrap="word",
            padx=16, pady=12
        )
        sb = tk.Scrollbar(result_frame, command=self.compare_box.yview)
        self.compare_box.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.compare_box.pack(side="left", fill="both", expand=True)

        # 저장 버튼
        tk.Button(parent,
                  text="💾  결과 JSON 저장",
                  font=("Malgun Gothic", 10),
                  bg="#2c3e50", fg="#ecf0f1",
                  relief="flat", cursor="hand2",
                  padx=14, pady=4,
                  command=self._save_results
                  ).pack(pady=(0, 12))

    # ────────────────────────────────────────────────────────
    # 실시간 센서 모니터 (백그라운드 스레드)
    # ────────────────────────────────────────────────────────
    def _start_live_monitor(self):
        def _loop():
            while True:
                co2, temp, humi = read_sensor()
                if co2 is not None:
                    lvl, _ = evaluate_co2(co2)
                    now = datetime.datetime.now().strftime("%H:%M:%S")
                    self.live_co2_var.set(f"CO₂: {co2:.1f} ppm")
                    self.live_temp_var.set(f"온도: {temp:.1f}°C")
                    self.live_humi_var.set(f"습도: {humi:.1f}%")
                    self.live_lvl_var.set(f"등급: {lvl}")
                    self.live_time_var.set(f"🕐 {now}")
                time.sleep(2)

        t = threading.Thread(target=_loop, daemon=True)
        t.start()

    # ────────────────────────────────────────────────────────
    # 측정 시작
    # ────────────────────────────────────────────────────────
    def _start_measurement(self, bus_type):
        if bus_type == "gas":
            if self.gas_measuring:
                return
            self.gas_measuring = True
            self.gas_current_readings = []
            self.gas_start_btn.config(state="disabled", bg="#7f8c8d")
            self.gas_stop_btn.config(state="normal",   bg="#e74c3c")
            self.gas_status_var.set("🔴 측정 중... 버스가 떠난 후 [측정 종료]를 누르세요")
            self._log(self.gas_log_box,
                      "▶ 측정 시작", header=True, color="start")
            self.gas_thread = threading.Thread(
                target=self._measure_loop, args=("gas",), daemon=True)
            self.gas_thread.start()

        else:
            if self.hydro_measuring:
                return
            self.hydro_measuring = True
            self.hydro_current_readings = []
            self.hydro_start_btn.config(state="disabled", bg="#7f8c8d")
            self.hydro_stop_btn.config(state="normal",   bg="#27ae60")
            self.hydro_status_var.set("🟢 측정 중... 버스가 떠난 후 [측정 종료]를 누르세요")
            self._log(self.hydro_log_box,
                      "▶ 측정 시작", header=True, color="start")
            self.hydro_thread = threading.Thread(
                target=self._measure_loop, args=("hydro",), daemon=True)
            self.hydro_thread.start()

    # ────────────────────────────────────────────────────────
    # 측정 루프 (스레드 실행)
    # ────────────────────────────────────────────────────────
    def _measure_loop(self, bus_type):
        count = 0
        while True:
            # 측정 중지 확인
            measuring = (self.gas_measuring   if bus_type == "gas"
                         else self.hydro_measuring)
            if not measuring:
                break

            co2, temp, humi = read_sensor()
            now = datetime.datetime.now().strftime("%H:%M:%S")

            if co2 is not None:
                count += 1
                record = {
                    "timestamp": now,
                    "co2": co2, "temperature": temp, "humidity": humi
                }

                if bus_type == "gas":
                    self.gas_current_readings.append(record)
                    self.root.after(0, lambda r=record, c=count:
                        self._update_live_cards("gas", r, c))
                    self.root.after(0, lambda r=record:
                        self._log(self.gas_log_box,
                                  f"[{r['timestamp']}] #{count:3d} | "
                                  f"CO₂: {r['co2']:6.1f} ppm | "
                                  f"온도: {r['temperature']:5.1f}°C | "
                                  f"습도: {r['humidity']:5.1f}%"))
                else:
                    self.hydro_current_readings.append(record)
                    self.root.after(0, lambda r=record, c=count:
                        self._update_live_cards("hydro", r, c))
                    self.root.after(0, lambda r=record:
                        self._log(self.hydro_log_box,
                                  f"[{r['timestamp']}] #{count:3d} | "
                                  f"CO₂: {r['co2']:6.1f} ppm | "
                                  f"온도: {r['temperature']:5.1f}°C | "
                                  f"습도: {r['humidity']:5.1f}%"))
            time.sleep(2)

    # ────────────────────────────────────────────────────────
    # 실시간 카드 업데이트
    # ────────────────────────────────────────────────────────
    def _update_live_cards(self, bus_type, record, count):
        co2  = record["co2"]
        temp = record["temperature"]
        humi = record["humidity"]
        lvl, _ = evaluate_co2(co2)

        if bus_type == "gas":
            self.gas_co2_var.set(f"{co2:.1f}")
            self.gas_temp_var.set(f"{temp:.1f}")
            self.gas_humi_var.set(f"{humi:.1f}")
            self.gas_lvl_var.set(lvl)
        else:
            self.hydro_co2_var.set(f"{co2:.1f}")
            self.hydro_temp_var.set(f"{temp:.1f}")
            self.hydro_humi_var.set(f"{humi:.1f}")
            self.hydro_lvl_var.set(lvl)

    # ────────────────────────────────────────────────────────
    # 측정 종료
    # ────────────────────────────────────────────────────────
    def _stop_measurement(self, bus_type):
        if bus_type == "gas":
            if not self.gas_measuring:
                return
            self.gas_measuring = False
            readings = self.gas_current_readings[:]
            sessions = self.gas_bus_sessions
            log_box  = self.gas_log_box
            self.gas_start_btn.config(state="normal",   bg="#e74c3c")
            self.gas_stop_btn.config(state="disabled",  bg="#7f8c8d")
            self.gas_status_var.set("✅ 측정 완료 — 다시 측정하려면 [측정 시작]을 누르세요")
            session_var = self.gas_session_var
        else:
            if not self.hydro_measuring:
                return
            self.hydro_measuring = False
            readings = self.hydro_current_readings[:]
            sessions = self.hydro_bus_sessions
            log_box  = self.hydro_log_box
            self.hydro_start_btn.config(state="normal",  bg="#27ae60")
            self.hydro_stop_btn.config(state="disabled", bg="#7f8c8d")
            self.hydro_status_var.set("✅ 측정 완료 — 다시 측정하려면 [측정 시작]을 누르세요")
            session_var = self.hydro_session_var

        if not readings:
            self._log(log_box, "⚠️ 측정된 데이터가 없습니다.", color="warn")
            return

        # 세션 통계 계산
        co2_values  = [r["co2"]         for r in readings]
        temp_values = [r["temperature"] for r in readings]
        humi_values = [r["humidity"]    for r in readings]

        session = {
            "session_no"  : len(sessions) + 1,
            "start_time"  : readings[0]["timestamp"],
            "end_time"    : readings[-1]["timestamp"],
            "count"       : len(readings),
            "readings"    : readings,
            "stats": {
                "co2" : {
                    "평균": round(statistics.mean(co2_values),  2),
                    "최대": round(max(co2_values),              2),
                    "최소": round(min(co2_values),              2),
                    "표준편차": round(
                        statistics.stdev(co2_values) if len(co2_values)>1 else 0, 2)
                },
                "temperature": {
                    "평균": round(statistics.mean(temp_values), 2),
                    "최대": round(max(temp_values),             2),
                    "최소": round(min(temp_values),             2),
                },
                "humidity": {
                    "평균": round(statistics.mean(humi_values), 2),
                    "최대": round(max(humi_values),             2),
                    "최소": round(min(humi_values),             2),
                }
            }
        }
        sessions.append(session)
        session_var.set(f"측정 세션: {len(sessions)}회 완료")

        # 세션 요약 로그
        avg_co2 = session["stats"]["co2"]["평균"]
        max_co2 = session["stats"]["co2"]["최대"]
        lvl, _  = evaluate_co2(avg_co2)

        self._log(log_box, "─" * 52, color="sep")
        self._log(log_box,
                  f"⏹ 세션 #{session['session_no']} 종료  "
                  f"({session['count']}회 측정 | "
                  f"{session['start_time']} ~ {session['end_time']})",
                  color="stop")
        self._log(log_box,
                  f"   평균 CO₂: {avg_co2:.1f} ppm  |  "
                  f"최대: {max_co2:.1f} ppm  |  등급: {lvl}",
                  color="result")
        self._log(log_box, "─" * 52, color="sep")

    # ────────────────────────────────────────────────────────
    # 초기화
    # ────────────────────────────────────────────────────────
    def _clear_sessions(self, bus_type):
        answer = messagebox.askyesno(
            "초기화 확인",
            "모든 측정 데이터를 삭제하시겠습니까?"
        )
        if not answer:
            return

        if bus_type == "gas":
            self.gas_bus_sessions = []
            self.gas_current_readings = []
            self.gas_session_var.set("측정 세션: 0회")
            self.gas_status_var.set("⏸  대기 중 — [측정 시작] 버튼을 누르세요")
            self._clear_log(self.gas_log_box)
            self.gas_co2_var.set("---")
            self.gas_temp_var.set("---")
            self.gas_humi_var.set("---")
            self.gas_lvl_var.set("---")
        else:
            self.hydro_bus_sessions = []
            self.hydro_current_readings = []
            self.hydro_session_var.set("측정 세션: 0회")
            self.hydro_status_var.set("⏸  대기 중 — [측정 시작] 버튼을 누르세요")
            self._clear_log(self.hydro_log_box)
            self.hydro_co2_var.set("---")
            self.hydro_temp_var.set("---")
            self.hydro_humi_var.set("---")
            self.hydro_lvl_var.set("---")

    # ────────────────────────────────────────────────────────
    # 비교 탭 갱신
    # ────────────────────────────────────────────────────────
    def _on_tab_change(self, event):
        idx = self.notebook.index(self.notebook.select())
        if idx == 2:
            self._update_compare_tab()

    def _update_compare_tab(self):
        box = self.compare_box
        box.config(state="normal")
        box.delete("1.0", "end")

        gas_s   = self.gas_bus_sessions
        hydro_s = self.hydro_bus_sessions

        lines = []
        lines.append("=" * 58)
        lines.append("  📊 비교 결과 요약")
        lines.append(f"  생성 시각: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 58)

        # ── 가스버스 요약 ─────────────────────────────────
        lines.append("\n🚌 가스버스")
        lines.append(f"  측정 세션: {len(gas_s)}회")
        if gas_s:
            all_co2 = [r["co2"] for s in gas_s for r in s["readings"]]
            g_avg = round(statistics.mean(all_co2), 2)
            g_max = round(max(all_co2), 2)
            g_min = round(min(all_co2), 2)
            lvl, _ = evaluate_co2(g_avg)
            lines.append(f"  전체 측정 횟수: {len(all_co2)}회")
            lines.append(f"  CO₂ 평균: {g_avg} ppm   {lvl}")
            lines.append(f"  CO₂ 최대: {g_max} ppm")
            lines.append(f"  CO₂ 최소: {g_min} ppm")

            lines.append("\n  세션별 상세:")
            for s in gas_s:
                avg = s["stats"]["co2"]["평균"]
                mx  = s["stats"]["co2"]["최대"]
                lv, _ = evaluate_co2(avg)
                lines.append(
                    f"    세션 #{s['session_no']} | "
                    f"{s['start_time']}~{s['end_time']} | "
                    f"평균 {avg:.1f} ppm | 최대 {mx:.1f} ppm | {lv}"
                )
        else:
            lines.append("  (데이터 없음)")
            g_avg = None

        # ── 수소전기버스 요약 ─────────────────────────────
        lines.append("\n🚍 수소전기버스")
        lines.append(f"  측정 세션: {len(hydro_s)}회")
        if hydro_s:
            all_co2_h = [r["co2"] for s in hydro_s for r in s["readings"]]
            h_avg = round(statistics.mean(all_co2_h), 2)
            h_max = round(max(all_co2_h), 2)
            h_min = round(min(all_co2_h), 2)
            lvl_h, _ = evaluate_co2(h_avg)
            lines.append(f"  전체 측정 횟수: {len(all_co2_h)}회")
            lines.append(f"  CO₂ 평균: {h_avg} ppm   {lvl_h}")
            lines.append(f"  CO₂ 최대: {h_max} ppm")
            lines.append(f"  CO₂ 최소: {h_min} ppm")

            lines.append("\n  세션별 상세:")
            for s in hydro_s:
                avg = s["stats"]["co2"]["평균"]
                mx  = s["stats"]["co2"]["최대"]
                lv, _ = evaluate_co2(avg)
                lines.append(
                    f"    세션 #{s['session_no']} | "
                    f"{s['start_time']}~{s['end_time']} | "
                    f"평균 {avg:.1f} ppm | 최대 {mx:.1f} ppm | {lv}"
                )
        else:
            lines.append("  (데이터 없음)")
            h_avg = None

        # ── 최종 비교 ────────────────────────────────────
        lines.append("\n" + "=" * 58)
        lines.append("  🔍 최종 비교")
        lines.append("=" * 58)

        if g_avg is not None and h_avg is not None:
            diff = round(g_avg - h_avg, 2)
            lines.append(f"  가스버스 평균 CO₂    : {g_avg:.1f} ppm")
            lines.append(f"  수소전기버스 평균 CO₂: {h_avg:.1f} ppm")
            lines.append(f"  차이 (가스 - 수소)   : {diff:+.1f} ppm")
            lines.append("")
            if diff > 50:
                lines.append("  ✅ 수소전기버스가 가스버스보다")
                lines.append(f"     CO₂를 {diff:.1f} ppm 낮게 유지합니다!")
                lines.append("  🌱 수소전기버스가 확연히 친환경적입니다.")
            elif diff > 10:
                lines.append(f"  ✅ 수소전기버스가 {diff:.1f} ppm 더 낮습니다.")
                lines.append("  🌱 수소전기버스가 친환경적입니다.")
            elif diff > 0:
                lines.append(f"  🔄 수소전기버스가 약간 낮습니다. ({diff:.1f} ppm)")
                lines.append("  📊 추가 측정을 권장합니다.")
            elif diff == 0:
                lines.append("  🔄 두 버스의 CO₂ 수치가 동일합니다.")
            else:
                lines.append(f"  ❓ 이번 측정에서는 가스버스가 {abs(diff):.1f} ppm 낮습니다.")
                lines.append("  📊 측정 횟수를 늘려 재확인하세요.")
        else:
            lines.append("  ⚠️  두 버스 모두 측정 데이터가 필요합니다.")

        lines.append("=" * 58)

        box.insert("end", "\n".join(lines))
        box.config(state="disabled")

    # ────────────────────────────────────────────────────────
    # 결과 저장
    # ────────────────────────────────────────────────────────
    def _save_results(self):
        filename = (f"result_"
                    f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        data = {
            "저장시각"       : datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "가스버스_세션"   : self.gas_bus_sessions,
            "수소전기버스_세션": self.hydro_bus_sessions
        }
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            messagebox.showinfo("저장 완료", f"결과가 저장되었습니다:\n{filename}")
        except Exception as e:
            messagebox.showerror("저장 실패", str(e))

    # ────────────────────────────────────────────────────────
    # 로그 헬퍼
    # ────────────────────────────────────────────────────────
    def _log(self, log_box, text, header=False, color="normal"):
        color_map = {
            "normal" : "#58d68d",
            "start"  : "#f1c40f",
            "stop"   : "#e74c3c",
            "warn"   : "#e67e22",
            "result" : "#3498db",
            "sep"    : "#555555",
        }
        tag = color
        log_box.config(state="normal")
        log_box.tag_configure(tag, foreground=color_map.get(color, "#58d68d"),
                              font=("Consolas", 9,
                                    "bold" if header else "normal"))
        log_box.insert("end", text + "\n", tag)
        log_box.see("end")
        log_box.config(state="disabled")

    def _clear_log(self, log_box):
        log_box.config(state="normal")
        log_box.delete("1.0", "end")
        log_box.config(state="disabled")


# ============================================================
# 실행
# ============================================================
if __name__ == "__main__":
    root = tk.Tk()
    app  = BusAirQualityApp(root)
    root.mainloop()
