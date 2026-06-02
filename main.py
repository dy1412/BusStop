# ============================================================
#  버스 공기질 비교 시스템
#  라즈베리파이 피코 + SCD30 + SSD1306 OLED
#  당곡고등학교 환경 탐구 프로젝트
# ============================================================

import time
import machine
import utime
from machine import Pin, I2C, Timer

# ── SCD30 드라이버 (내장 없을 경우 아래 클래스 사용) ────────
try:
    from scd30 import SCD30
    print("SCD30 라이브러리 로드 성공")
except:
    # SCD30 드라이버가 없을 경우 직접 구현
    SCD30 = None

# ── OLED 드라이버 ────────────────────────────────────────────
try:
    import ssd1306
    OLED_AVAILABLE = True
except:
    OLED_AVAILABLE = False
    print("OLED 라이브러리 없음 - 시리얼 출력만 사용")


# ============================================================
# SCD30 드라이버 직접 구현 (라이브러리 없을 때 사용)
# ============================================================
class SCD30Driver:
    """
    SCD30 I2C 드라이버 (MicroPython용)
    """
    SCD30_ADDR          = 0x61
    CMD_START_MEASURE   = b'\x00\x10'
    CMD_DATA_READY      = b'\x02\x02'
    CMD_READ_MEASURE    = b'\x03\x00'
    CMD_SET_INTERVAL    = b'\x46\x00'

    def __init__(self, i2c):
        self.i2c = i2c
        self._start_measurement()
        time.sleep(2)

    def _crc8(self, data):
        """CRC-8 체크섬 계산"""
        crc = 0xFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = (crc << 1) ^ 0x31
                else:
                    crc <<= 1
                crc &= 0xFF
        return crc

    def _start_measurement(self, pressure=0):
        """연속 측정 시작"""
        cmd = self.CMD_START_MEASURE + bytes([
            (pressure >> 8) & 0xFF,
             pressure       & 0xFF
        ])
        crc = self._crc8(bytes([
            (pressure >> 8) & 0xFF,
             pressure       & 0xFF
        ]))
        self.i2c.writeto(self.SCD30_ADDR, cmd + bytes([crc]))

    def data_available(self):
        """데이터 준비 여부 확인"""
        try:
            self.i2c.writeto(self.SCD30_ADDR,
                             self.CMD_DATA_READY)
            time.sleep_ms(3)
            buf = self.i2c.readfrom(self.SCD30_ADDR, 3)
            return buf[1] == 1
        except:
            return False

    def read_measurement(self):
        """
        CO2(ppm), 온도(°C), 습도(%) 읽기
        반환: (co2, temp, humi) 또는 (None, None, None)
        """
        try:
            self.i2c.writeto(self.SCD30_ADDR,
                             self.CMD_READ_MEASURE)
            time.sleep_ms(3)
            buf = self.i2c.readfrom(self.SCD30_ADDR, 18)

            def parse_float(b):
                """4바이트 IEEE754 → float"""
                val = ((b[0] << 24) | (b[1] << 16) |
                       (b[3] << 8)  |  b[4])
                # 부호
                sign = -1 if (val >> 31) else 1
                # 지수
                exp  = ((val >> 23) & 0xFF) - 127
                # 가수
                mant = (val & 0x7FFFFF) | 0x800000
                result = sign * mant * (2 ** (exp - 23))
                return round(result, 2)

            co2  = parse_float(buf[0:5])
            temp = parse_float(buf[6:11])
            humi = parse_float(buf[12:17])
            return co2, temp, humi

        except Exception as e:
            print("SCD30 읽기 오류:", e)
            return None, None, None


# ============================================================
# CO2 등급 평가
# ============================================================
def evaluate_co2(ppm):
    """
    CO2 농도 등급 반환
    반환: (등급문자열, 단계숫자)
    """
    if ppm is None:
        return "알수없음", 0
    if ppm < 450:
        return "매우좋음", 1
    elif ppm < 700:
        return "좋음",     2
    elif ppm < 1000:
        return "보통",     3
    elif ppm < 2000:
        return "나쁨",     4
    elif ppm < 5000:
        return "매우나쁨", 5
    else:
        return "위험",     6


# ============================================================
# 간단한 통계 계산 (statistics 모듈 없음)
# ============================================================
def calc_mean(values):
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)

def calc_max(values):
    if not values:
        return 0.0
    return round(max(values), 2)

def calc_min(values):
    if not values:
        return 0.0
    return round(min(values), 2)

def calc_stdev(values):
    if len(values) < 2:
        return 0.0
    mean = calc_mean(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return round(variance ** 0.5, 2)


# ============================================================
# 시간 문자열 (RTC 없으므로 경과 시간 사용)
# ============================================================
_boot_time = utime.ticks_ms()

def elapsed_str():
    """부팅 후 경과 시간 HH:MM:SS 형태로 반환"""
    ms      = utime.ticks_diff(utime.ticks_ms(), _boot_time)
    total_s = ms // 1000
    h       = total_s // 3600
    m       = (total_s % 3600) // 60
    s       = total_s % 60
    return "{:02d}:{:02d}:{:02d}".format(h, m, s)


# ============================================================
# 메인 시스템 클래스
# ============================================================
class BusAirMonitor:

    # 상태 상수
    STATE_IDLE         = 0   # 대기
    STATE_GAS_MEAS     = 1   # 가스버스 측정 중
    STATE_HYDRO_MEAS   = 2   # 수소버스 측정 중
    STATE_SHOW_RESULT  = 3   # 결과 표시

    def __init__(self):
        print("=" * 50)
        print("  버스 공기질 비교 시스템 초기화 중...")
        print("  당곡고등학교 환경 탐구 프로젝트")
        print("=" * 50)

        # ── I2C 초기화 ──────────────────────────────────────
        self.i2c = I2C(0,
                       sda=Pin(4),
                       scl=Pin(5),
                       freq=50000)
        print("I2C 초기화 완료")
        print("I2C 장치:", [hex(a) for a in self.i2c.scan()])

        # ── SCD30 초기화 ─────────────────────────────────────
        try:
            self.sensor = SCD30Driver(self.i2c)
            print("SCD30 센서 초기화 완료")
        except Exception as e:
            print("SCD30 오류:", e)
            self.sensor = None

        # ── OLED 초기화 ──────────────────────────────────────
        if OLED_AVAILABLE:
            try:
                self.oled = ssd1306.SSD1306_I2C(128, 64, self.i2c)
                self.oled.fill(0)
                self.oled.text("Bus Air Monitor", 0, 0)
                self.oled.text("Initializing...", 0, 16)
                self.oled.show()
                print("OLED 초기화 완료")
            except Exception as e:
                print("OLED 오류:", e)
                self.oled = None
        else:
            self.oled = None

        # ── 버튼 초기화 ──────────────────────────────────────
        # 버튼A: GP14 (측정 시작)
        # 버튼B: GP15 (측정 종료)
        self.btn_start = Pin(14, Pin.IN, Pin.PULL_UP)
        self.btn_stop  = Pin(15, Pin.IN, Pin.PULL_UP)

        # ── LED 초기화 (내장 LED GP25) ───────────────────────
        self.led = Pin(25, Pin.OUT)

        # ── 데이터 저장소 ────────────────────────────────────
        self.gas_sessions   = []   # 가스버스 세션 목록
        self.hydro_sessions = []   # 수소버스 세션 목록

        # 현재 세션 버퍼
        self.current_readings = []
        self.current_type     = None   # "gas" or "hydro"

        # ── 상태 변수 ────────────────────────────────────────
        self.state           = self.STATE_IDLE
        self.last_co2        = None
        self.last_temp       = None
        self.last_humi       = None

        # 버튼 디바운스 타이머
        self.last_btn_time   = 0
        self.DEBOUNCE_MS     = 300

        # LED 깜빡임 카운터
        self.led_tick        = 0

        print("초기화 완료!")
        print("-" * 50)
        self._print_serial_header()


    # ──────────────────────────────────────────────────────
    # 시리얼 출력 헬퍼
    # ──────────────────────────────────────────────────────
    def _print_serial_header(self):
        print()
        print("=" * 50)
        print("  조작 방법")
        print("  [버튼 A / GP14] : 측정 시작")
        print("  [버튼 B / GP15] : 측정 종료")
        print("  현재 모드 선택은 콘솔 메뉴로 안내됩니다.")
        print("=" * 50)
        print()
        self._print_menu()

    def _print_menu(self):
        print("┌─────────────────────────────────────────┐")
        print("│  1. 가스버스 탭 활성화 후 버튼A → 시작  │")
        print("│  2. 수소버스 탭 활성화 후 버튼A → 시작  │")
        print("│  버튼B : 측정 종료                       │")
        print("│  현재 상태:", self._state_str().ljust(20), "│")
        print("└─────────────────────────────────────────┘")

    def _state_str(self):
        mapping = {
            self.STATE_IDLE       : "대기 중",
            self.STATE_GAS_MEAS   : "가스버스 측정 중",
            self.STATE_HYDRO_MEAS : "수소버스 측정 중",
            self.STATE_SHOW_RESULT: "결과 표시 중",
        }
        return mapping.get(self.state, "알 수 없음")


    # ──────────────────────────────────────────────────────
    # OLED 출력
    # ──────────────────────────────────────────────────────
    def _oled_clear(self):
        if self.oled:
            self.oled.fill(0)

    def _oled_show(self):
        if self.oled:
            self.oled.show()

    def _oled_text(self, text, x, y):
        if self.oled:
            self.oled.text(text, x, y)

    def oled_idle_screen(self):
        """대기 화면"""
        self._oled_clear()
        self._oled_text("=Bus Air System=", 0, 0)
        if self.last_co2:
            self._oled_text(
                "CO2:" + str(int(self.last_co2)) + "ppm", 0, 16)
            self._oled_text(
                "T:" + str(self.last_temp) +
                " H:" + str(int(self.last_humi)) + "%", 0, 28)
            lvl, _ = evaluate_co2(self.last_co2)
            self._oled_text("Grade:" + lvl, 0, 40)
        else:
            self._oled_text("Warming up...", 0, 24)
        self._oled_text("A:Start  B:Stop", 0, 54)
        self._oled_show()

    def oled_measuring_screen(self, bus_label, count):
        """측정 중 화면"""
        self._oled_clear()
        self._oled_text(bus_label, 0, 0)
        self._oled_text("MEASURING...", 0, 10)
        if self.last_co2:
            self._oled_text(
                "CO2:" + str(int(self.last_co2)) + "ppm", 0, 24)
            lvl, _ = evaluate_co2(self.last_co2)
            self._oled_text("Lvl:" + lvl, 0, 36)
        self._oled_text(
            "N=" + str(count) + "  " + elapsed_str(), 0, 48)
        self._oled_text("B:Stop Measure", 0, 56)
        self._oled_show()

    def oled_result_screen(self, session):
        """세션 결과 화면"""
        avg = session["stats"]["co2"]["avg"]
        mx  = session["stats"]["co2"]["max"]
        lvl, _ = evaluate_co2(avg)
        btype   = "GAS" if session["type"] == "gas" else "H2"

        self._oled_clear()
        self._oled_text("[" + btype + "] Session #" +
                         str(session["session_no"]), 0, 0)
        self._oled_text(
            "Avg:" + str(avg) + "ppm", 0, 14)
        self._oled_text(
            "Max:" + str(mx)  + "ppm", 0, 26)
        self._oled_text("Grade:" + lvl, 0, 38)
        self._oled_text(
            "Count:" + str(session["count"]), 0, 50)
        self._oled_show()

    def oled_compare_screen(self):
        """비교 결과 화면"""
        self._oled_clear()
        self._oled_text("-COMPARE RESULT-", 0, 0)

        g_avg = self._get_overall_avg("gas")
        h_avg = self._get_overall_avg("hydro")

        if g_avg is not None:
            self._oled_text(
                "GAS :" + str(g_avg) + "ppm", 0, 14)
        else:
            self._oled_text("GAS : No data", 0, 14)

        if h_avg is not None:
            self._oled_text(
                "H2  :" + str(h_avg) + "ppm", 0, 26)
        else:
            self._oled_text("H2  : No data", 0, 26)

        if g_avg and h_avg:
            diff = round(g_avg - h_avg, 1)
            self._oled_text(
                "Diff:" + str(diff) + "ppm", 0, 38)
            if diff > 10:
                self._oled_text("H2 is cleaner!", 0, 50)
            elif diff < -10:
                self._oled_text("GAS is lower?", 0, 50)
            else:
                self._oled_text("Similar level", 0, 50)

        self._oled_show()


    # ──────────────────────────────────────────────────────
    # 센서 읽기
    # ──────────────────────────────────────────────────────
    def read_sensor(self):
        """SCD30에서 데이터 읽기"""
        if self.sensor is None:
            # 테스트 더미 데이터
            import urandom
            co2  = 400 + (urandom.getrandbits(8) % 300)
            temp = 20  + (urandom.getrandbits(5) % 10)
            humi = 45  + (urandom.getrandbits(5) % 30)
            return float(co2), float(temp), float(humi)

        try:
            for _ in range(8):
                if self.sensor.data_available():
                    co2, temp, humi = self.sensor.read_measurement()
                    if co2 and 300 <= co2 <= 5000:
                        return co2, temp, humi
                time.sleep_ms(500)
        except Exception as e:
            print("센서 읽기 오류:", e)
        return None, None, None


    # ──────────────────────────────────────────────────────
    # 버튼 처리
    # ──────────────────────────────────────────────────────
    def _debounce_ok(self):
        """디바운스 체크"""
        now = utime.ticks_ms()
        if utime.ticks_diff(now, self.last_btn_time) > self.DEBOUNCE_MS:
            self.last_btn_time = now
            return True
        return False

    def check_buttons(self):
        """
        버튼 상태 확인 및 처리
        버튼A(GP14) LOW = 누름
        버튼B(GP15) LOW = 누름
        """
        # 버튼 A : 측정 시작
        if self.btn_start.value() == 0 and self._debounce_ok():
            self._on_start_pressed()
            time.sleep_ms(50)

        # 버튼 B : 측정 종료
        if self.btn_stop.value() == 0 and self._debounce_ok():
            self._on_stop_pressed()
            time.sleep_ms(50)

    def _on_start_pressed(self):
        """버튼A 처리 - 측정 시작"""
        if self.state == self.STATE_IDLE:
            # 콘솔로 버스 종류 선택 안내
            print()
            print("어떤 버스를 측정하시겠습니까?")
            print("  1 → 가스버스")
            print("  2 → 수소전기버스")
            print("  (10초 안에 입력, 기본값=가스버스)")

            # 비차단 방식으로 입력 대기 (최대 10초)
            start = utime.ticks_ms()
            choice = ""
            while utime.ticks_diff(utime.ticks_ms(), start) < 10000:
                # stdin에서 한 글자 읽기 시도
                try:
                    import sys
                    ch = sys.stdin.read(1)
                    if ch in ("1", "2"):
                        choice = ch
                        break
                except:
                    pass
                time.sleep_ms(100)

            if choice == "2":
                self._start_measurement("hydro")
            else:
                self._start_measurement("gas")

        elif self.state == self.STATE_SHOW_RESULT:
            # 결과 화면에서 버튼A → 비교 결과 표시
            self._print_compare_result()
            self.oled_compare_screen()
            self.state = self.STATE_IDLE

    def _on_stop_pressed(self):
        """버튼B 처리 - 측정 종료"""
        if self.state in (self.STATE_GAS_MEAS,
                          self.STATE_HYDRO_MEAS):
            self._stop_measurement()


    # ──────────────────────────────────────────────────────
    # 측정 시작
    # ──────────────────────────────────────────────────────
    def _start_measurement(self, bus_type):
        """측정 시작"""
        self.current_readings = []
        self.current_type     = bus_type

        if bus_type == "gas":
            self.state = self.STATE_GAS_MEAS
            label = "[가스버스]"
        else:
            self.state = self.STATE_HYDRO_MEAS
            label = "[수소전기버스]"

        print()
        print("=" * 50)
        print(label + " 측정 시작!")
        print("  버스가 떠난 후 버튼B(GP15)를 누르세요.")
        print("=" * 50)

        self.oled_measuring_screen(label, 0)
        self.led.on()   # 측정 중 LED 켜기


    # ──────────────────────────────────────────────────────
    # 측정 종료
    # ──────────────────────────────────────────────────────
    def _stop_measurement(self):
        """측정 종료 및 세션 저장"""
        self.led.off()

        if not self.current_readings:
            print("경고: 측정 데이터가 없습니다.")
            self.state = self.STATE_IDLE
            return

        # 통계 계산
        co2_vals  = [r["co2"]  for r in self.current_readings]
        temp_vals = [r["temp"] for r in self.current_readings]
        humi_vals = [r["humi"] for r in self.current_readings]

        if self.current_type == "gas":
            sessions = self.gas_sessions
        else:
            sessions = self.hydro_sessions

        session = {
            "type"       : self.current_type,
            "session_no" : len(sessions) + 1,
            "count"      : len(self.current_readings),
            "start_time" : self.current_readings[0]["time"],
            "end_time"   : self.current_readings[-1]["time"],
            "readings"   : self.current_readings[:],
            "stats"      : {
                "co2": {
                    "avg"   : calc_mean(co2_vals),
                    "max"   : calc_max(co2_vals),
                    "min"   : calc_min(co2_vals),
                    "stdev" : calc_stdev(co2_vals),
                },
                "temp": {
                    "avg" : calc_mean(temp_vals),
                    "max" : calc_max(temp_vals),
                    "min" : calc_min(temp_vals),
                },
                "humi": {
                    "avg" : calc_mean(humi_vals),
                    "max" : calc_max(humi_vals),
                    "min" : calc_min(humi_vals),
                }
            }
        }

        sessions.append(session)

        # 결과 출력
        self._print_session_result(session)
        self.oled_result_screen(session)

        self.state        = self.STATE_SHOW_RESULT
        self.current_type = None


    # ──────────────────────────────────────────────────────
    # 시리얼 결과 출력
    # ──────────────────────────────────────────────────────
    def _print_session_result(self, session):
        btype = "가스버스" if session["type"] == "gas" else "수소전기버스"
        avg   = session["stats"]["co2"]["avg"]
        mx    = session["stats"]["co2"]["max"]
        mn    = session["stats"]["co2"]["min"]
        sd    = session["stats"]["co2"]["stdev"]
        lvl, _ = evaluate_co2(avg)

        print()
        print("=" * 50)
        print("  " + btype + " 세션 #" +
              str(session["session_no"]) + " 결과")
        print("=" * 50)
        print("  측정 횟수 : " + str(session["count"]) + "회")
        print("  시작 시각 : " + session["start_time"])
        print("  종료 시각 : " + session["end_time"])
        print("  ─ CO2 통계 ─────────────────")
        print("  평균    : " + str(avg)  + " ppm")
        print("  최대    : " + str(mx)   + " ppm")
        print("  최소    : " + str(mn)   + " ppm")
        print("  표준편차: " + str(sd)   + " ppm")
        print("  등급    : " + lvl)
        print("=" * 50)
        print()
        print("  ★ 버튼A를 누르면 전체 비교 결과를 봅니다.")
        print()

    def _print_compare_result(self):
        """가스버스 vs 수소버스 비교 출력"""
        g_avg = self._get_overall_avg("gas")
        h_avg = self._get_overall_avg("hydro")

        print()
        print("=" * 50)
        print("  [최종 비교 결과]")
        print("  가스버스 세션   : " +
              str(len(self.gas_sessions)) + "회")
        print("  수소전기버스 세션: " +
              str(len(self.hydro_sessions)) + "회")
        print("-" * 50)

        if g_avg is not None:
            lvl, _ = evaluate_co2(g_avg)
            print("  가스버스 평균 CO2    : " +
                  str(g_avg) + " ppm  [" + lvl + "]")
            # 세션별
            for s in self.gas_sessions:
                print("    #" + str(s["session_no"]) +
                      " avg=" + str(s["stats"]["co2"]["avg"]) +
                      " max=" + str(s["stats"]["co2"]["max"]) +
                      " ppm")
        else:
            print("  가스버스: 데이터 없음")

        print()

        if h_avg is not None:
            lvl, _ = evaluate_co2(h_avg)
            print("  수소전기버스 평균 CO2: " +
                  str(h_avg) + " ppm  [" + lvl + "]")
            for s in self.hydro_sessions:
                print("    #" + str(s["session_no"]) +
                      " avg=" + str(s["stats"]["co2"]["avg"]) +
                      " max=" + str(s["stats"]["co2"]["max"]) +
                      " ppm")
        else:
            print("  수소전기버스: 데이터 없음")

        print("-" * 50)

        if g_avg is not None and h_avg is not None:
            diff = round(g_avg - h_avg, 2)
            print("  차이 (가스 - 수소) : " + str(diff) + " ppm")
            print()
            if diff > 50:
                print("  ✅ 수소전기버스가 " + str(diff) +
                      " ppm 더 낮습니다!")
                print("  수소전기버스가 확연히 친환경적입니다.")
            elif diff > 10:
                print("  ✅ 수소전기버스가 " + str(diff) +
                      " ppm 더 낮습니다.")
                print("  수소전기버스가 친환경적입니다.")
            elif diff > 0:
                print("  수소전기버스가 약간 낮습니다. (" +
                      str(diff) + " ppm)")
            elif diff == 0:
                print("  두 버스의 CO2 수치가 동일합니다.")
            else:
                print("  이번 측정에서는 가스버스가 " +
                      str(abs(diff)) + " ppm 낮습니다.")
                print("  측정 횟수를 늘려 재확인하세요.")
        else:
            print("  ⚠  두 버스 모두 측정해야 비교 가능합니다.")

        print("=" * 50)
        print()


    # ──────────────────────────────────────────────────────
    # 전체 평균 계산
    # ──────────────────────────────────────────────────────
    def _get_overall_avg(self, bus_type):
        sessions = (self.gas_sessions if bus_type == "gas"
                    else self.hydro_sessions)
        if not sessions:
            return None
        all_vals = [r["co2"] for s in sessions
                              for r in s["readings"]]
        return calc_mean(all_vals) if all_vals else None


    # ──────────────────────────────────────────────────────
    # LED 깜빡임
    # ──────────────────────────────────────────────────────
    def _blink_led(self):
        """측정 중: 0.5초 간격 깜빡임"""
        if self.state in (self.STATE_GAS_MEAS,
                          self.STATE_HYDRO_MEAS):
            self.led_tick += 1
            if self.led_tick % 4 == 0:   # 2초마다
                self.led.toggle()
        else:
            self.led.off()


    # ──────────────────────────────────────────────────────
    # 메인 루프
    # ──────────────────────────────────────────────────────
    def run(self):
        """메인 실행 루프"""
        print("시스템 시작! 센서 워밍업 중 (5초)...")
        for i in range(5, 0, -1):
            print("  " + str(i) + "초 남음...")
            time.sleep(1)
        print("준비 완료!")
        print()

        loop_count = 0

        while True:
            loop_count += 1

            # ── 버튼 확인 ──────────────────────────────────
            self.check_buttons()

            # ── 센서 읽기 ──────────────────────────────────
            co2, temp, humi = self.read_sensor()

            if co2 is not None:
                self.last_co2  = co2
                self.last_temp = temp
                self.last_humi = humi

                # ── 측정 중이면 데이터 기록 ────────────────
                if self.state in (self.STATE_GAS_MEAS,
                                  self.STATE_HYDRO_MEAS):
                    record = {
                        "time" : elapsed_str(),
                        "co2"  : co2,
                        "temp" : temp,
                        "humi" : humi,
                    }
                    self.current_readings.append(record)
                    count = len(self.current_readings)

                    # 시리얼 출력 (매 측정마다)
                    lvl, _ = evaluate_co2(co2)
                    print(
                        "[" + elapsed_str() + "] "
                        "#" + str(count).zfill(3) + " | "
                        "CO2:" + str(co2) + "ppm | "
                        "T:" + str(temp) + "C | "
                        "H:" + str(humi) + "% | "
                        + lvl
                    )

                    # OLED 갱신 (3번에 1번)
                    if count % 3 == 1:
                        label = ("[가스버스]"
                                 if self.state == self.STATE_GAS_MEAS
                                 else "[수소버스]")
                        self.oled_measuring_screen(label, count)

                # ── 대기 중이면 OLED 주기적 갱신 ──────────
                elif self.state == self.STATE_IDLE:
                    if loop_count % 5 == 1:
                        self.oled_idle_screen()
                        # 시리얼 실시간 출력 (5회마다)
                        lvl, _ = evaluate_co2(co2)
                        print(
                            "[대기] " +
                            elapsed_str() + " | "
                            "CO2:" + str(co2) + "ppm | "
                            "T:" + str(temp) + "C | "
                            "H:" + str(humi) + "% | " +
                            lvl
                        )

            # LED 깜빡임
            self._blink_led()

            # 2초 대기
            time.sleep(2)


# ============================================================
# 프로그램 시작
# ============================================================
monitor = BusAirMonitor()
monitor.run()
