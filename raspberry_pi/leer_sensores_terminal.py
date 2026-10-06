"""
leer_sensores_terminal.py

Script temporal para ejecutar en la Raspberry Pi.

Lee los sensores analogicos conectados al MCP3008 y muestra por terminal toda
la informacion util para calibracion:
  - valor normalizado del ADC (0.0 a 1.0),
  - voltaje calculado,
  - magnitud fisica estimada con las mismas formulas del proyecto,
  - resistencia/lux cuando se puede derivar.

No usa UDP, no lee el MPU6050 y no modifica el contador de pasos.
"""

from __future__ import annotations

import math
import time

from gpiozero import MCP3008  # type: ignore


ADC_REF_VOLTAGE = 3.3
CHANNEL_HUMIDITY = 0
CHANNEL_TEMPERATURE = 1
CHANNEL_LIGHT = 2
READ_PERIOD_S = 1.0


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def raw_to_voltage(raw_value: float) -> float:
    return raw_value * ADC_REF_VOLTAGE


def ntc_resistance_ohm(voltage: float, r_serie: float = 470.0) -> float:
    """Calcula R_NTC usando la misma topologia asumida en enviar_informacion.py."""
    if voltage <= 0.0 or voltage >= ADC_REF_VOLTAGE:
        return float("nan")
    return r_serie * voltage / (ADC_REF_VOLTAGE - voltage)


def voltage_to_temperature_c(voltage: float, r_serie: float = 470.0) -> float:
    """Misma conversion de temperatura usada en enviar_informacion.py."""
    r25 = 470.0
    beta = 3450.0
    t25 = 298.15

    r_ntc = ntc_resistance_ohm(voltage, r_serie)
    if not math.isfinite(r_ntc) or r_ntc <= 0.0:
        return float("nan")

    inv_t = (1.0 / t25) + (1.0 / beta) * math.log(r_ntc / r25)
    return (1.0 / inv_t) - 273.15


def voltage_to_humidity_percent(voltage: float) -> float:
    """Misma conversion de humedad usada en enviar_informacion.py."""
    rh = (voltage / ADC_REF_VOLTAGE - 0.1515) / 0.00636
    return clamp(rh, 0.0, 100.0)


def ldr_resistance_ohm(voltage: float, r_serie: float = 10_000.0) -> float:
    """Calcula R_LDR usando la misma topologia asumida en enviar_informacion.py."""
    if voltage <= 0.0 or voltage >= ADC_REF_VOLTAGE:
        return float("nan")
    return r_serie * voltage / (ADC_REF_VOLTAGE - voltage)


def ldr_lux(voltage: float) -> float:
    r_ldr = ldr_resistance_ohm(voltage)
    if not math.isfinite(r_ldr) or r_ldr <= 0.0:
        return 0.0 if voltage <= 0.0 else float("inf")
    return (70_000.0 / r_ldr) ** (1.0 / 0.7)


def voltage_to_light_percent(voltage: float) -> float:
    """Misma conversion de luz usada en enviar_informacion.py."""
    if voltage <= 0.0 or voltage >= ADC_REF_VOLTAGE:
        return 0.0 if voltage <= 0.0 else 100.0

    lux = ldr_lux(voltage)
    lux_min = 0.1
    lux_max = 10_000.0
    percent = (
        (math.log10(max(lux, lux_min)) - math.log10(lux_min))
        / (math.log10(lux_max) - math.log10(lux_min))
        * 100.0
    )
    return clamp(percent, 0.0, 100.0)


def fmt(value: float, unit: str = "", decimals: int = 3) -> str:
    if not math.isfinite(value):
        return f"{value}"
    return f"{value:.{decimals}f}{unit}"


def main() -> None:
    humidity = MCP3008(channel=CHANNEL_HUMIDITY)
    temperature = MCP3008(channel=CHANNEL_TEMPERATURE)
    light = MCP3008(channel=CHANNEL_LIGHT)

    print("Leyendo sensores analogicos MCP3008. Ctrl+C para salir.")
    print(f"Vref={ADC_REF_VOLTAGE:.2f} V | humedad=CH{CHANNEL_HUMIDITY} temp=CH{CHANNEL_TEMPERATURE} luz=CH{CHANNEL_LIGHT}")
    print()

    try:
        while True:
            hum_raw = humidity.value
            temp_raw = temperature.value
            light_raw = light.value

            hum_v = raw_to_voltage(hum_raw)
            temp_v = raw_to_voltage(temp_raw)
            light_v = raw_to_voltage(light_raw)

            hum_pct = voltage_to_humidity_percent(hum_v)
            temp_c = voltage_to_temperature_c(temp_v)
            r_ntc = ntc_resistance_ohm(temp_v)
            light_pct = voltage_to_light_percent(light_v)
            r_ldr = ldr_resistance_ohm(light_v)
            lux = ldr_lux(light_v)

            print("=" * 72)
            print(f"HUMEDAD     CH{CHANNEL_HUMIDITY}: raw={hum_raw:.4f} | V={hum_v:.4f} V | RH={hum_pct:.1f} %")
            print(f"TEMPERATURA CH{CHANNEL_TEMPERATURE}: raw={temp_raw:.4f} | V={temp_v:.4f} V | T={fmt(temp_c, ' C', 2)} | R_NTC={fmt(r_ntc, ' ohm', 1)}")
            print(f"LUZ         CH{CHANNEL_LIGHT}: raw={light_raw:.4f} | V={light_v:.4f} V | L={light_pct:.1f} % | lux={fmt(lux, '', 1)} | R_LDR={fmt(r_ldr, ' ohm', 1)}")
            time.sleep(READ_PERIOD_S)

    except KeyboardInterrupt:
        print("\nLectura detenida.")


if __name__ == "__main__":
    main()
