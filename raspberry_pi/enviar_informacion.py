from __future__ import annotations

import json
import math
import socket
import os
import time
from collections import deque

import numpy as np
import smbus  # type: ignore
from gpiozero import MCP3008  # type: ignore

# ============================================================================
# CONFIGURACION DE RED
# ============================================================================

PC_IP = os.environ.get("PC_IP", "127.0.0.1")
DESTINOS = [
    (PC_IP, 5005),  # recibir_informacion.py
    (PC_IP, 5006),  # caratula_reloj.py
]

# ============================================================================
# CONFIGURACION MPU6050
# ============================================================================

BUS_ID = 1
MPU_ADDR = 0x68
REG_PWR_MGMT_1 = 0x6B
REG_CONFIG = 0x1A
REG_ACCEL_CONFIG = 0x1C
REG_GYRO_CONFIG = 0x1B
REG_ACCEL_XOUT_H = 0x3B

ACCEL_SCALE = 16384.0  # +/-2 g
GYRO_SCALE = 131.0  # +/-250 deg/s
SAMPLE_HZ = 50.0
DT = 1.0 / SAMPLE_HZ
I2C_READ_RETRIES = 4
I2C_RETRY_DELAY_S = 0.01

# ============================================================================
# CONFIGURACION MCP3008
# ============================================================================

ADC_REF_VOLTAGE = 3.3
CHANNEL_HUMIDITY = 0
CHANNEL_TEMPERATURE = 1
CHANNEL_LIGHT = 2
SEND_ENGINEERING_UNITS = True

# ============================================================================
# PARAMETROS DEL DETECTOR DE PASOS
# ============================================================================

CALIBRATION_SECONDS = 2.5

# Ventana de analisis. 2.8 s da contexto suficiente para estimar ritmo.
WALK_WINDOW_S = 2.8

# Banda de marcha aproximada: conserva pasos y quita deriva/temblor rapido.
WALK_BP_HP = 0.7
WALK_BP_LP = 3.0

# Envolvente de movimiento.
WALK_ENV_ON = 0.045
WALK_ENV_OFF = 0.025

# Cadencia humana aproximada.
MIN_STEP_INTERVAL_S = 0.28
MAX_STEP_INTERVAL_S = 0.95
MIN_WALK_CADENCE = 1.1
MAX_WALK_CADENCE = 3.0

# Criterios para diferenciar marcha de sacudida.
MIN_AUTOCORR_PEAK = 0.42
MAX_INTERVAL_CV = 0.30
MAX_HF_RATIO = 2.80
MAX_JERK_RATIO = 0.70
MIN_WINDOW_PEAKS = 3

# Persistencia del modo WALK.
WALK_SCORE_ENTER = 4
WALK_SCORE_EXIT = -3

# Deteccion final de pasos dentro de WALK.
MIN_PEAK_THR = 0.06
MIN_EXCURSION = 0.10
MIN_FEATURE_PROMINENCE = 0.035
FEATURE_PROMINENCE_RATIO = 0.50
STEP_REARM_RATIO = 0.35


class MPU6050:
    """Driver minimo del MPU6050 para acelerometro, giroscopio y temperatura."""

    def __init__(self, bus_id: int = BUS_ID, addr: int = MPU_ADDR) -> None:
        self.bus_id = bus_id
        self.bus = smbus.SMBus(bus_id)
        self.addr = addr
        self._setup()

    def _setup(self) -> None:
        self.bus.write_byte_data(self.addr, REG_PWR_MGMT_1, 0x00)
        self.bus.write_byte_data(self.addr, REG_ACCEL_CONFIG, 0x00)
        self.bus.write_byte_data(self.addr, REG_GYRO_CONFIG, 0x00)
        self.bus.write_byte_data(self.addr, REG_CONFIG, 0x03)
        time.sleep(0.1)

    def _recover_i2c(self) -> None:
        """Reabre y reconfigura el MPU6050 tras un fallo transitorio del bus I2C."""
        try:
            self.bus.close()
        except Exception:
            pass

        time.sleep(I2C_RETRY_DELAY_S)
        self.bus = smbus.SMBus(self.bus_id)
        self._setup()

    @staticmethod
    def _to_int16(high: int, low: int) -> int:
        value = (high << 8) | low
        return value - 65536 if value >= 0x8000 else value

    def read(self) -> dict[str, float]:
        """Lee un bloque coherente de aceleracion, temperatura MPU y giroscopio."""
        last_error: OSError | None = None
        block = None

        for attempt in range(I2C_READ_RETRIES):
            try:
                block = self.bus.read_i2c_block_data(self.addr, REG_ACCEL_XOUT_H, 14)
                break
            except OSError as exc:
                last_error = exc
                time.sleep(I2C_RETRY_DELAY_S)
                if attempt == 1:
                    self._recover_i2c()

        if block is None:
            raise RuntimeError(
                f"No se pudo leer MPU6050 tras {I2C_READ_RETRIES} intentos"
            ) from last_error

        ax_raw = self._to_int16(block[0], block[1])
        ay_raw = self._to_int16(block[2], block[3])
        az_raw = self._to_int16(block[4], block[5])
        temp_raw = self._to_int16(block[6], block[7])
        gx_raw = self._to_int16(block[8], block[9])
        gy_raw = self._to_int16(block[10], block[11])
        gz_raw = self._to_int16(block[12], block[13])

        ax = ax_raw / ACCEL_SCALE
        ay = ay_raw / ACCEL_SCALE
        az = az_raw / ACCEL_SCALE
        gx = gx_raw / GYRO_SCALE
        gy = gy_raw / GYRO_SCALE
        gz = gz_raw / GYRO_SCALE
        mpu_temp_c = temp_raw / 340.0 + 36.53

        return {
            "ax": ax,
            "ay": ay,
            "az": az,
            "gx": gx,
            "gy": gy,
            "gz": gz,
            "mpu_temp": mpu_temp_c,
        }


class AnalogSensors:
    """Lectura de sensores analogicos conectados al MCP3008."""

    def __init__(self, reference_voltage: float = ADC_REF_VOLTAGE) -> None:
        self.reference_voltage = reference_voltage
        self.humidity = MCP3008(channel=CHANNEL_HUMIDITY)
        self.temperature = MCP3008(channel=CHANNEL_TEMPERATURE)
        self.light = MCP3008(channel=CHANNEL_LIGHT)

    def _to_voltage(self, raw_value: float) -> float:
        return raw_value * self.reference_voltage

    def read_voltages(self) -> dict[str, float]:
        """Devuelve voltios directos para poder calibrar cada sensor real."""
        return {
            "humidity_v": self._to_voltage(self.humidity.value),
            "temperature_v": self._to_voltage(self.temperature.value),
            "light_v": self._to_voltage(self.light.value),
        }


def voltage_to_temperature_c(voltage: float, r_serie: float = 470.0) -> float:
    R25 = 470.0
    B = 3450.0
    T25 = 298.15  # 25°C en Kelvin

    # Resistencia del termistor desde el divisor de tensión
    # Vout = Vcc * R_ntc / (R_serie + R_ntc)  <- NTC hacia GND
    if voltage <= 0 or voltage >= ADC_REF_VOLTAGE:
        return float("nan")
    r_ntc = r_serie * voltage / (ADC_REF_VOLTAGE - voltage)

    # Ecuación beta
    inv_T = (1.0 / T25) + (1.0 / B) * math.log(r_ntc / R25)
    # Corrección de escala: sensor marca 27.5 °C cuando la temperatura real es 23 °C
    TEMP_CORRECTION_FACTOR = 23.0 / 27.5  # ≈ 0.8364
    return ((1.0 / inv_T) - 273.15) * TEMP_CORRECTION_FACTOR


def voltage_to_humidity_percent(voltage: float) -> float:
    rh = (voltage / ADC_REF_VOLTAGE - 0.1515) / 0.00636
    HUMIDITY_CORRECTION_FACTOR = 33.0 / 68.0  # ≈ 0.4853
    return max(0.0, min(100.0, rh * HUMIDITY_CORRECTION_FACTOR))


def voltage_to_light_percent(voltage: float, r_serie: float = 10_000.0) -> float:
    # Rango medido del sensor: 0.5 V (oscuridad) .. 2.2 V (maxima luz)
    LIGHT_V_MIN = 0.5
    LIGHT_V_MAX = 2.2
    percent = (LIGHT_V_MAX - voltage) / (LIGHT_V_MAX - LIGHT_V_MIN) * 100.0
    return max(0.0, min(100.0, percent))


def read_environment(sensors: AnalogSensors) -> dict[str, float]:
    """Lee sensores ambientales y conserva voltajes crudos para calibracion."""
    voltages = sensors.read_voltages()

    if SEND_ENGINEERING_UNITS:
        temp = voltage_to_temperature_c(voltages["temperature_v"])
        hum = voltage_to_humidity_percent(voltages["humidity_v"])
        light = voltage_to_light_percent(voltages["light_v"])
    else:
        temp = voltages["temperature_v"]
        hum = voltages["humidity_v"]
        light = voltages["light_v"]

    return {
        "temp": temp,
        "hum": hum,
        "light": light,
        **voltages,
    }


class LowPass1P:
    """Filtro paso bajo IIR de primer orden."""

    def __init__(self, fc: float, fs: float, x0: float = 0.0) -> None:
        self.a = math.exp(-2.0 * math.pi * fc / fs)
        self.y = x0

    def update(self, x: float) -> float:
        self.y = self.a * self.y + (1.0 - self.a) * x
        return self.y


class HighPass1P:
    """Filtro paso alto IIR de primer orden."""

    def __init__(self, fc: float, fs: float) -> None:
        self.a = math.exp(-2.0 * math.pi * fc / fs)
        self.x_prev = 0.0
        self.y = 0.0

    def update(self, x: float) -> float:
        self.y = self.a * (self.y + x - self.x_prev)
        self.x_prev = x
        return self.y


class BandPassWalk:
    """Paso banda simple para conservar la banda principal de marcha."""

    def __init__(self, hp_fc: float, lp_fc: float, fs: float) -> None:
        self.hp = HighPass1P(hp_fc, fs)
        self.lp = LowPass1P(lp_fc, fs)

    def update(self, x: float) -> float:
        return self.lp.update(self.hp.update(x))


class StepDetector:
    """
    Detector de pasos basado en periodicidad.

    La energia sola no basta para activar WALK: una sacudida tambien tiene mucha
    energia. Se entra en WALK solo si la senal vertical filtrada muestra ritmo,
    cadencia humana, regularidad entre picos y poca energia fuera de la banda de
    marcha. Dentro de WALK se cuentan maximos locales prominentes.
    """

    def __init__(self, fs: float = SAMPLE_HZ) -> None:
        self.fs = fs
        self.window_n = int(WALK_WINDOW_S * fs)

        self.gx_lp = LowPass1P(fc=0.35, fs=fs)
        self.gy_lp = LowPass1P(fc=0.35, fs=fs)
        self.gz_lp = LowPass1P(fc=0.35, fs=fs, x0=1.0)
        self.vert_bp = BandPassWalk(WALK_BP_HP, WALK_BP_LP, fs)
        self.vert_smooth = LowPass1P(fc=4.0, fs=fs)
        self.env_lp = LowPass1P(fc=1.6, fs=fs)

        self.raw_vert_buf: deque[float] = deque(maxlen=self.window_n)
        self.bp_vert_buf: deque[float] = deque(maxlen=self.window_n)
        self.env_buf: deque[float] = deque(maxlen=self.window_n)

        self.mode = "IDLE"
        self.steps = 0
        self.sample_index = 0
        self.last_step_time = -1e9
        self.walk_score = 0

        self.x2 = 0.0
        self.x1 = 0.0
        self.x0 = 0.0
        self.min_since_step = 0.0
        self.initialized_min = False
        self.step_armed = True

        self.last_raw_vert = 0.0
        self.last_vert = 0.0
        self.last_mov_env = 0.0
        self.last_step_thr = MIN_PEAK_THR
        self.last_features: dict[str, float | int] | None = None
        self.horizontal_mag = 0.0

    @property
    def baseline(self) -> float:
        return math.sqrt(
            self.gx_lp.y * self.gx_lp.y
            + self.gy_lp.y * self.gy_lp.y
            + self.gz_lp.y * self.gz_lp.y
        )

    @property
    def threshold(self) -> float:
        return self.last_step_thr

    @property
    def noise_est(self) -> float:
        return self.last_mov_env

    @property
    def movement_on_threshold(self) -> float:
        return WALK_ENV_ON

    @property
    def movement_off_threshold(self) -> float:
        return WALK_ENV_OFF

    def calibrate(self, samples: list[tuple[float, float, float]]) -> None:
        """Inicializa el filtro de gravedad con la media en reposo."""
        if not samples:
            return
        self.gx_lp.y = sum(ax for ax, _, _ in samples) / len(samples)
        self.gy_lp.y = sum(ay for _, ay, _ in samples) / len(samples)
        self.gz_lp.y = sum(az for _, _, az in samples) / len(samples)

    @staticmethod
    def _safe_unit(x: float, y: float, z: float) -> tuple[float, float, float]:
        norm = math.sqrt(x * x + y * y + z * z) + 1e-9
        return x / norm, y / norm, z / norm

    @staticmethod
    def _rms(values: np.ndarray) -> float:
        if len(values) == 0:
            return 0.0
        return float(np.sqrt(np.mean(values * values)))

    def _normalized_autocorr_peak(self, values: np.ndarray) -> tuple[float, int | None]:
        if len(values) < max(12, int(1.5 * self.fs)):
            return 0.0, None

        x = values - np.mean(values)
        den = float(np.dot(x, x))
        if den < 1e-9:
            return 0.0, None

        ac = np.correlate(x, x, mode="full")[len(x) - 1 :] / den
        lag_min = max(1, int(self.fs / MAX_WALK_CADENCE))
        lag_max = min(len(ac) - 1, int(self.fs / MIN_WALK_CADENCE))
        if lag_max <= lag_min:
            return 0.0, None

        segment = ac[lag_min : lag_max + 1]
        best_offset = int(np.argmax(segment))
        return float(segment[best_offset]), lag_min + best_offset

    @staticmethod
    def _find_window_peaks(
        values: np.ndarray, threshold: float, min_dist_samples: int
    ) -> list[int]:
        """Busca picos positivos claros y descarta maximos pequenos o mesetas."""
        peaks: list[int] = []
        last_idx = -(10**9)
        prominence_floor = max(
            MIN_FEATURE_PROMINENCE, FEATURE_PROMINENCE_RATIO * threshold
        )
        side = max(2, min(10, min_dist_samples // 2))

        for idx in range(side, len(values) - side):
            if (
                values[idx] > threshold
                and values[idx] >= values[idx - 1]
                and values[idx] > values[idx + 1]
            ):
                left_min = float(np.min(values[idx - side : idx]))
                right_min = float(np.min(values[idx + 1 : idx + side + 1]))
                prominence = values[idx] - max(left_min, right_min)
                if prominence < prominence_floor:
                    continue
                if idx - last_idx >= min_dist_samples:
                    peaks.append(idx)
                    last_idx = idx
        return peaks

    def _compute_walk_features(self) -> dict[str, float | int] | None:
        if len(self.bp_vert_buf) < max(20, int(2.0 * self.fs)):
            return None

        raw_vert = np.asarray(self.raw_vert_buf, dtype=float)
        bp_vert = np.asarray(self.bp_vert_buf, dtype=float)
        env = np.asarray(self.env_buf, dtype=float)

        env_mean = float(np.mean(env))
        bp_std = float(np.std(bp_vert))
        bp_rms = self._rms(bp_vert)
        hf_rms = self._rms(raw_vert - bp_vert)
        hf_ratio = hf_rms / (bp_rms + 1e-9)
        jerk_ratio = self._rms(np.diff(bp_vert)) / (bp_rms + 1e-9)

        autocorr_peak, best_lag = self._normalized_autocorr_peak(bp_vert)
        autocorr_cadence = 0.0 if best_lag is None else self.fs / best_lag
        dyn_thr = max(MIN_PEAK_THR, 0.60 * bp_std, 0.35 * env_mean)

        peaks = self._find_window_peaks(
            bp_vert,
            threshold=dyn_thr,
            min_dist_samples=max(1, int(MIN_STEP_INTERVAL_S * self.fs)),
        )

        interval_cv = 999.0
        peak_cadence = 0.0
        if len(peaks) >= 3:
            intervals = np.diff(peaks) / self.fs
            mean_interval = float(np.mean(intervals))
            if mean_interval > 1e-9:
                interval_cv = float(np.std(intervals) / mean_interval)
                peak_cadence = 1.0 / mean_interval

        # La cadencia de picos es mas estable para contar pasos; la autocorrelacion
        # puede engancharse al ciclo completo izquierda-derecha y dividirla por dos.
        cadence = peak_cadence if peak_cadence > 0.0 else autocorr_cadence

        return {
            "env_mean": env_mean,
            "bp_std": bp_std,
            "bp_rms": bp_rms,
            "hf_ratio": hf_ratio,
            "jerk_ratio": jerk_ratio,
            "autocorr_peak": autocorr_peak,
            "cadence": cadence,
            "interval_cv": interval_cv,
            "num_peaks": len(peaks),
            "dyn_thr": dyn_thr,
        }

    def _update_walk_state(self, features: dict[str, float | int] | None) -> None:
        if features is None:
            return

        motion_ok = float(features["env_mean"]) > WALK_ENV_ON
        periodic_ok = float(features["autocorr_peak"]) >= MIN_AUTOCORR_PEAK
        cadence_ok = MIN_WALK_CADENCE <= float(features["cadence"]) <= MAX_WALK_CADENCE
        regular_ok = float(features["interval_cv"]) <= MAX_INTERVAL_CV
        shake_ok = (
            float(features["hf_ratio"]) <= MAX_HF_RATIO
            and float(features["jerk_ratio"]) <= MAX_JERK_RATIO
        )
        enough_peaks = int(features["num_peaks"]) >= MIN_WINDOW_PEAKS

        good_walk = (
            motion_ok
            and cadence_ok
            and enough_peaks
            and shake_ok
            and (periodic_ok or regular_ok)
        )

        if good_walk:
            self.walk_score = min(self.walk_score + 1, 8)
        elif float(features["env_mean"]) < WALK_ENV_OFF:
            self.walk_score = max(self.walk_score - 2, -8)
        else:
            self.walk_score = max(self.walk_score - 1, -8)

        previous_mode = self.mode
        if self.mode == "IDLE" and self.walk_score >= WALK_SCORE_ENTER:
            self.mode = "WALK"
        elif self.mode == "WALK" and self.walk_score <= WALK_SCORE_EXIT:
            self.mode = "IDLE"

        if previous_mode == "IDLE" and self.mode == "WALK":
            self.step_armed = True
            self.min_since_step = self.x0
        elif previous_mode == "WALK" and self.mode == "IDLE":
            self.step_armed = True

    def _update_step_rearm(self, features: dict[str, float | int] | None) -> None:
        """Exige un valle negativo entre impactos para no contar dobles picos."""
        if self.mode != "WALK":
            self.step_armed = True
            return

        peak_thr = (
            MIN_PEAK_THR
            if features is None
            else max(MIN_PEAK_THR, float(features["dyn_thr"]))
        )
        if not self.step_armed and self.x0 <= -STEP_REARM_RATIO * peak_thr:
            self.step_armed = True

    def _try_count_step(
        self, t: float, features: dict[str, float | int] | None
    ) -> bool:
        if features is None or self.mode != "WALK" or not self.step_armed:
            return False

        is_local_max = self.x1 >= self.x2 and self.x1 > self.x0
        if not is_local_max:
            return False

        dt = t - self.last_step_time
        if dt < MIN_STEP_INTERVAL_S:
            return False

        peak_thr = max(
            MIN_PEAK_THR, float(features["dyn_thr"]), 0.55 * float(features["bp_std"])
        )
        excursion = self.x1 - self.min_since_step
        excursion_thr = max(MIN_EXCURSION, 1.25 * peak_thr)

        if self.x1 > peak_thr and excursion > excursion_thr:
            self.steps += 1
            self.last_step_time = t
            self.min_since_step = self.x0
            self.step_armed = False
            return True

        return False

    def update(
        self, ax: float, ay: float, az: float
    ) -> dict[str, float | bool | int | None]:
        t = self.sample_index / self.fs
        self.sample_index += 1

        magnitude = math.sqrt(ax * ax + ay * ay + az * az)

        gx = self.gx_lp.update(ax)
        gy = self.gy_lp.update(ay)
        gz = self.gz_lp.update(az)
        ux, uy, uz = self._safe_unit(gx, gy, gz)

        lin_x = ax - gx
        lin_y = ay - gy
        lin_z = az - gz
        raw_vert = lin_x * ux + lin_y * uy + lin_z * uz
        lin_mag_sq = lin_x * lin_x + lin_y * lin_y + lin_z * lin_z
        self.horizontal_mag = math.sqrt(max(0.0, lin_mag_sq - raw_vert * raw_vert))

        vert = self.vert_smooth.update(self.vert_bp.update(raw_vert))
        mov_env = self.env_lp.update(abs(vert))

        self.raw_vert_buf.append(raw_vert)
        self.bp_vert_buf.append(vert)
        self.env_buf.append(mov_env)

        if not self.initialized_min:
            self.min_since_step = vert
            self.initialized_min = True
        else:
            self.min_since_step = min(self.min_since_step, vert)

        self.x2, self.x1, self.x0 = self.x1, self.x0, vert

        features = self._compute_walk_features()
        self._update_walk_state(features)
        self._update_step_rearm(features)
        step_detected = self._try_count_step(t, features)

        step_thr = float(features["dyn_thr"]) if features is not None else MIN_PEAK_THR
        self.last_raw_vert = raw_vert
        self.last_vert = vert
        self.last_mov_env = mov_env
        self.last_step_thr = step_thr
        self.last_features = features

        return {
            "mag": magnitude,
            "hmag": mov_env,
            "horizontal_mag": self.horizontal_mag,
            "vert": vert,
            "dynamic_boosted": vert,
            "step_thr": step_thr,
            "walk_on": WALK_ENV_ON,
            "walk_off": WALK_ENV_OFF,
            "noise": mov_env,
            "steps": self.steps,
            "step_detected": step_detected,
            "peak_value": self.x1 if step_detected else None,
            "active": self.mode == "WALK",
            "boost_gain": 1.0,
            "walk_score": self.walk_score,
            "walk_mode": self.mode,
            "autocorr": 0.0 if features is None else float(features["autocorr_peak"]),
            "cadence": 0.0 if features is None else float(features["cadence"]),
            "interval_cv": 999.0
            if features is None
            else float(features["interval_cv"]),
            "hf_ratio": 999.0 if features is None else float(features["hf_ratio"]),
            "jerk_ratio": 999.0 if features is None else float(features["jerk_ratio"]),
        }


def calibrate_detector(sensor: MPU6050, detector: StepDetector) -> None:
    """Toma muestras en reposo para inicializar la direccion de gravedad."""
    end_time = time.monotonic() + CALIBRATION_SECONDS
    samples: list[tuple[float, float, float]] = []
    while time.monotonic() < end_time:
        try:
            imu = sensor.read()
            samples.append((imu["ax"], imu["ay"], imu["az"]))
        except RuntimeError as exc:
            print(f"\nAviso I2C durante calibracion: {exc}")
        time.sleep(DT)
    detector.calibrate(samples)


def build_packet(
    seq: int, imu: dict[str, float], env: dict[str, float], step: dict[str, object]
) -> dict[str, object]:
    """Construye el JSON comun que consumen recibir_informacion.py y caratula_reloj.py."""
    return {
        "t": time.time(),
        "seq": seq,
        "ax": round(imu["ax"], 5),
        "ay": round(imu["ay"], 5),
        "az": round(imu["az"], 5),
        "gx": round(imu["gx"], 5),
        "gy": round(imu["gy"], 5),
        "gz": round(imu["gz"], 5),
        "mag": round(float(step["mag"]), 5),
        "hmag": round(float(step["hmag"]), 5),
        "horizontal_mag": round(float(step["horizontal_mag"]), 5),
        "vert": round(float(step["vert"]), 5),
        "dynamic_boosted": round(float(step["dynamic_boosted"]), 5),
        "step_thr": round(float(step["step_thr"]), 5),
        "walk_on": round(float(step["walk_on"]), 5),
        "walk_off": round(float(step["walk_off"]), 5),
        "steps": int(step["steps"]),
        "active": bool(step["active"]),
        "peak": bool(step["step_detected"]),
        "boost_gain": round(float(step["boost_gain"]), 3),
        "walk_score": int(step["walk_score"]),
        "walk_mode": step["walk_mode"],
        "autocorr": round(float(step["autocorr"]), 3),
        "cadence": round(float(step["cadence"]), 3),
        "interval_cv": round(float(step["interval_cv"]), 3),
        "hf_ratio": round(float(step["hf_ratio"]), 3),
        "jerk_ratio": round(float(step["jerk_ratio"]), 3),
        "temp": round(float(env["temp"]), 2),
        "hum": round(float(env["hum"]), 2),
        "light": round(float(env["light"]), 2),
        "mpu_temp": round(imu["mpu_temp"], 2),
        "raw": {
            "humidity_v": round(env["humidity_v"], 4),
            "temperature_v": round(env["temperature_v"], 4),
            "light_v": round(env["light_v"], 4),
        },
    }


def main() -> None:
    sensor = MPU6050()
    analog = AnalogSensors()
    detector = StepDetector()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    print("Calibrando MPU6050 en reposo...")
    calibrate_detector(sensor, detector)
    print(
        f"|g|={detector.baseline:.4f}g | ruido={detector.noise_est:.4f} "
        f"| umbral={detector.threshold:.4f}"
    )
    print("Enviando informacion por UDP. Ctrl+C para salir.")

    seq = 0
    next_time = time.monotonic()
    dropped_reads = 0

    try:
        while True:
            try:
                imu = sensor.read()
            except RuntimeError as exc:
                dropped_reads += 1
                print(f"\nAviso I2C: muestra descartada ({dropped_reads}) - {exc}")
                next_time = time.monotonic() + DT
                time.sleep(DT)
                continue

            env = read_environment(analog)
            step = detector.update(imu["ax"], imu["ay"], imu["az"])
            packet = build_packet(seq, imu, env, step)
            payload = json.dumps(packet, separators=(",", ":")).encode("utf-8")

            for host, port in DESTINOS:
                sock.sendto(payload, (host, port))

            print(
                f"seq={seq} pasos={packet['steps']:5d} "
                f"T={packet['temp']:5.1f}C H={packet['hum']:5.1f}% L={packet['light']:5.1f}% "
                f"mov={packet['hmag']:.3f} vert={packet['vert']:+.3f} thr={packet['step_thr']:.3f} "
                f"{packet['walk_mode']} score={packet['walk_score']:2d} "
                f"cad={packet['cadence']:.2f} jerk={packet['jerk_ratio']:.2f}",
                end="\r",
            )

            seq += 1
            next_time += DT
            sleep_time = next_time - time.monotonic()
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                next_time = time.monotonic()

    except KeyboardInterrupt:
        print(f"\nEnvio detenido. Pasos totales: {detector.steps}")


if __name__ == "__main__":
    main()
