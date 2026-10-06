"""
recibir_informacion.py

Script para ejecutar en el ordenador.

Escucha paquetes JSON enviados por enviar_informacion.py y muestra una grafica
en tiempo real con:
  - envolvente de movimiento usada para activar WALK,
  - senal dinamica vertical usada por el podometro,
  - umbral dinamico de paso,
  - pasos, temperatura, humedad y luz.

Los pasos se muestran relativos al primer paquete recibido por esta ventana.
Asi, si la Raspberry ya llevaba pasos acumulados, la grafica empieza en 0.

Este script usa el puerto 5005. La caratula usa el puerto 5006 para que ambos
puedan ejecutarse a la vez en el mismo ordenador sin conflicto de socket.
"""

from __future__ import annotations

import json
import math
import socket
import time
from collections import deque

import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

UDP_IP = "0.0.0.0"
UDP_PORT = 5005
N_PUNTOS = 300
INTERVALO_MS = 25
MAX_PKT_POR_FRAME = 30
STALE_TIMEOUT_S = 2.0
STEP_DISPLAY_GAIN = 1.3


def safe_bool(value: object) -> bool:
    """Interpreta booleanos UDP sin convertir la cadena 'False' en True."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        # bool("False") en Python da True porque cualquier string no vacío es True.
        # Esta función evita ese error interpretando explícitamente textos booleanos.
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def safe_float(value: object, default: float = 0.0) -> float:
    """Convierte valores recibidos por UDP sin romper la animacion."""
    try:
        return float(value)
    except (TypeError, ValueError):
        # Si llega algo inválido o ausente, no se cae la animación:
        # se sustituye por un valor por defecto.
        return default


def is_finite_number(value: object) -> bool:
    # Se usa para excluir NaN e infinitos del autoscalado.
    return isinstance(value, (int, float)) and math.isfinite(value)


def parse_packet(message: str) -> dict[str, object]:
    """
    Parseador del protocolo final.

    Tambien acepta CSV legado para facilitar pruebas con scripts antiguos, pero
    el formato que debe mantenerse es JSON.
    """
    message = message.strip()

    if message.startswith("{"):
        packet = json.loads(message)

        # Aquí se hace compatibilidad hacia atrás:
        # si una versión antigua del emisor mandaba "mag" en vez de "hmag" o "vert",
        # la gráfica sigue funcionando sin depender de nombres exactos.
        return {
            "hmag": safe_float(packet.get("hmag", packet.get("mag", 0.0))),
            "vert": safe_float(packet.get("vert", packet.get("mag", 0.0))),
            "step_thr": safe_float(packet.get("step_thr", packet.get("thr", 0.0))),
            "walk_on": safe_float(packet.get("walk_on", 0.08)),
            "walk_off": safe_float(packet.get("walk_off", 0.04)),
            "steps": int(packet.get("steps", 0)),
            "temp": packet.get("temp", "--"),
            "hum": packet.get("hum", "--"),
            "light": packet.get("light", "--"),
            "active": safe_bool(packet.get("active", False)),
            "peak": safe_bool(packet.get("peak", False)),
            "seq": packet.get("seq", "--"),
            "walk_score": int(packet.get("walk_score", 0)),
            "walk_mode": packet.get("walk_mode", "IDLE"),
            "autocorr": safe_float(packet.get("autocorr", 0.0)),
            "cadence": safe_float(packet.get("cadence", 0.0)),
            "interval_cv": safe_float(packet.get("interval_cv", 999.0)),
            "hf_ratio": safe_float(packet.get("hf_ratio", 999.0)),
            "jerk_ratio": safe_float(packet.get("jerk_ratio", 999.0)),
            "raw": packet.get("raw", {}),
        }

    parts = message.split(",")
    if len(parts) < 5:
        raise ValueError("CSV legado incompleto")

    mag = safe_float(parts[1], 1.0)

    # Modo de compatibilidad con el protocolo CSV antiguo.
    # Como ese protocolo no traía toda la semántica nueva, se rellenan
    # algunos campos con valores fijos o marcadores.
    return {
        "hmag": mag,
        "vert": mag,
        "step_thr": 1.5,
        "walk_on": math.nan,
        "walk_off": math.nan,
        "steps": int(parts[2]),
        "temp": parts[3],
        "hum": parts[4],
        "light": "--",
        "active": True,
        "peak": False,
        "seq": "--",
        "walk_score": 0,
        "walk_mode": "CSV",
        "autocorr": 0.0,
        "cadence": 0.0,
        "interval_cv": 999.0,
        "hf_ratio": 999.0,
        "jerk_ratio": 999.0,
        "raw": {},
    }


def autoscale_axis(ax, series_list: list[deque[float]], min_span: float, pad_ratio: float = 0.18) -> None:
    """Ajusta limites verticales ignorando NaN para mantener la grafica legible."""
    values: list[float] = []

    for series in series_list:
        values.extend(v for v in series if is_finite_number(v))

    if not values:
        return

    y_min = min(values)
    y_max = max(values)
    span = y_max - y_min

    if span < min_span:
        # Si la señal está casi plana, matplotlib dejaría un eje muy colapsado
        # y visualmente feo. Aquí se fuerza una amplitud mínima útil.
        mid = 0.5 * (y_min + y_max)
        y_min = mid - min_span / 2
        y_max = mid + min_span / 2
        span = min_span

    pad = max(min_span * 0.35, span * pad_ratio)
    ax.set_ylim(y_min - pad, y_max + pad)


sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))
sock.setblocking(False)
# Socket no bloqueante: la GUI nunca se queda congelada esperando datos.
# Si no hay paquetes, update() sigue ejecutándose igualmente.

x_data = list(range(N_PUNTOS))

# Buffers deslizantes para representar las últimas N_PUNTOS muestras.
# deque(maxlen=...) hace que al añadir una nueva se descarte automáticamente la más vieja.
move_data: deque[float] = deque([0.0] * N_PUNTOS, maxlen=N_PUNTOS)
walk_on_data: deque[float] = deque([math.nan] * N_PUNTOS, maxlen=N_PUNTOS)
walk_off_data: deque[float] = deque([math.nan] * N_PUNTOS, maxlen=N_PUNTOS)
vert_data: deque[float] = deque([0.0] * N_PUNTOS, maxlen=N_PUNTOS)
thr_data: deque[float] = deque([0.0] * N_PUNTOS, maxlen=N_PUNTOS)
step_mark_data: deque[float] = deque([math.nan] * N_PUNTOS, maxlen=N_PUNTOS)

# Estado resumido del último paquete útil recibido.
# La GUI no consulta directamente el socket para pintar: pinta a partir de este estado.
state = {
    "steps": 0,
    "remote_steps": None,
    "base_steps": None,
    "temp": "--",
    "hum": "--",
    "light": "--",
    "active": False,
    "last_packet_time": 0.0,
    "seq": "--",
    "hmag": 0.0,
    "vert": 0.0,
    "step_thr": 0.0,
    "walk_score": 0,
    "walk_mode": "IDLE",
    "autocorr": 0.0,
    "cadence": 0.0,
    "interval_cv": 999.0,
    "hf_ratio": 999.0,
    "jerk_ratio": 999.0,
}

fig = plt.figure(figsize=(12, 7), facecolor="#101010")
fig.canvas.manager.set_window_title("Podometro - Visualizador de sensores")

# Se usa GridSpec para tener dos gráficas apiladas y una franja inferior de texto,
# con control explícito de alturas relativas.
gs = gridspec.GridSpec(3, 1, height_ratios=[2.2, 2.2, 1.0], hspace=0.08)

ax_move = fig.add_subplot(gs[0])
ax_step = fig.add_subplot(gs[1], sharex=ax_move)
ax_info = fig.add_subplot(gs[2])

for ax in (ax_move, ax_step):
    ax.set_facecolor("#181818")
    ax.set_xlim(0, N_PUNTOS - 1)
    ax.grid(True, color="#333333", alpha=0.65, linewidth=0.6)
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_color("#555555")

ax_move.tick_params(labelbottom=False)
ax_step.set_xlabel("Muestras recientes", color="white")

ax_move.set_title("Envolvente de movimiento / activacion WALK", color="white", fontsize=12, pad=8)
ax_move.set_ylabel("Envolvente (g)", color="white")
line_move, = ax_move.plot(x_data, list(move_data), color="#35c4ff", linewidth=2.0, label="mov_env")
line_walk_on, = ax_move.plot(x_data, list(walk_on_data), color="#00ff88", linewidth=1.2, linestyle="--", label="walk_on")
line_walk_off, = ax_move.plot(x_data, list(walk_off_data), color="#ffd166", linewidth=1.2, linestyle="--", label="walk_off")

ax_step.set_title("Senal de paso / umbral dinamico", color="white", fontsize=12, pad=8)
ax_step.set_ylabel("Senal dinamica (g)", color="white")
line_vert, = ax_step.plot(x_data, list(vert_data), color="#7aa2ff", linewidth=2.0, label="vert")
line_thr, = ax_step.plot(x_data, list(thr_data), color="#ff5c5c", linewidth=1.5, linestyle="--", label="step_thr")
line_marks, = ax_step.plot(
    x_data,
    list(step_mark_data),
    linestyle="None",
    marker="o",
    markersize=5,
    color="#00ff88",
    label="paso",
)
ax_step.axhline(y=0.0, color="#666666", linewidth=0.8, linestyle=":")
# Esta línea horizontal en y=0 ayuda a interpretar mejor picos y valles de la señal vertical.

for ax in (ax_move, ax_step):
    legend = ax.legend(loc="upper right", facecolor="#222222", edgecolor="#666666", framealpha=0.9)
    for text in legend.get_texts():
        text.set_color("white")

ax_info.set_facecolor("#111111")
ax_info.set_xlim(0, 1)
ax_info.set_ylim(0, 1)
ax_info.axis("off")
# El tercer panel no es una gráfica, sino una zona de HUD textual.

txt_steps = ax_info.text(0.04, 0.62, "Pasos: 0", color="#00ff88", fontsize=22, fontweight="bold")
txt_temp = ax_info.text(0.26, 0.62, "T: -- C", color="#ffb000", fontsize=19)
txt_hum = ax_info.text(0.43, 0.62, "H: -- %", color="#7cc7ff", fontsize=19)
txt_light = ax_info.text(0.60, 0.62, "L: -- %", color="#f7f48b", fontsize=19)
txt_status = ax_info.text(0.80, 0.62, "SIN DATOS", color="#888888", fontsize=18, fontweight="bold")
txt_values = ax_info.text(0.04, 0.18, "seq=-- | mov_env=-- | vert=-- | thr=--", color="#bbbbbb", fontsize=12)


def update(_frame):
    packets = 0

    while packets < MAX_PKT_POR_FRAME:
        try:
            # En cada frame de matplotlib se vacían varios paquetes pendientes del socket.
            # Eso evita que la GUI vaya con retraso si el emisor manda más rápido que el render.
            raw, _ = sock.recvfrom(4096)
            data = parse_packet(raw.decode("utf-8"))

            previous_remote_steps = state["remote_steps"]
            remote_steps = int(data["steps"])

            if state["base_steps"] is None:
                # El primer contador recibido se toma como referencia cero local.
                # Así la gráfica empieza siempre en 0 pasos aunque la Raspberry lleve acumulados.
                state["base_steps"] = remote_steps

            raw_display_steps = max(0, remote_steps - int(state["base_steps"]))

            # Ganancia visual aplicada solo a la interfaz, no al conteo real del emisor.
            # Esto es un truco de presentación, no una corrección física.
            display_steps = int(round(raw_display_steps * STEP_DISPLAY_GAIN))

            step_increased = previous_remote_steps is not None and remote_steps > int(previous_remote_steps)

            valid_peak = bool(data["peak"]) and safe_float(data["vert"]) > safe_float(data["step_thr"])

            # Se marca un paso en la gráfica por dos vías:
            # 1) porque el contador remoto aumentó
            # 2) porque el emisor señaló un pico y además supera el umbral
            # Esto hace la visualización más robusta frente a pequeñas desincronizaciones.
            marker = safe_float(data["vert"]) if step_increased or valid_peak else math.nan

            move_data.append(safe_float(data["hmag"]))
            walk_on_data.append(safe_float(data["walk_on"], math.nan))
            walk_off_data.append(safe_float(data["walk_off"], math.nan))
            vert_data.append(safe_float(data["vert"]))
            thr_data.append(safe_float(data["step_thr"]))
            step_mark_data.append(marker)

            state.update(
                steps=display_steps,
                remote_steps=remote_steps,
                temp=data["temp"],
                hum=data["hum"],
                light=data["light"],
                active=bool(data["active"]),
                last_packet_time=time.monotonic(),
                seq=data["seq"],
                hmag=safe_float(data["hmag"]),
                vert=safe_float(data["vert"]),
                step_thr=safe_float(data["step_thr"]),
                walk_score=int(data["walk_score"]),
                walk_mode=data["walk_mode"],
                autocorr=safe_float(data["autocorr"]),
                cadence=safe_float(data["cadence"]),
                interval_cv=safe_float(data["interval_cv"]),
                hf_ratio=safe_float(data["hf_ratio"]),
                jerk_ratio=safe_float(data["jerk_ratio"]),
            )
            packets += 1

        except BlockingIOError:
            # No hay más paquetes pendientes en este frame.
            break
        except Exception as exc:
            # Cualquier error de parseo o recepción se reporta pero no tumba la GUI.
            print(f"Error recibiendo paquete: {exc}")
            break

    stale = (time.monotonic() - float(state["last_packet_time"])) > STALE_TIMEOUT_S

    # Aunque el último paquete dijera active=True, si hace demasiado que no llega nada,
    # se considera que ya no hay datos válidos y se apaga visualmente el estado WALK.
    active = bool(state["active"]) and not stale

    line_move.set_ydata(list(move_data))
    line_walk_on.set_ydata(list(walk_on_data))
    line_walk_off.set_ydata(list(walk_off_data))
    line_vert.set_ydata(list(vert_data))
    line_thr.set_ydata(list(thr_data))
    line_marks.set_ydata(list(step_mark_data))

    autoscale_axis(ax_move, [move_data, walk_on_data, walk_off_data], min_span=0.03)
    autoscale_axis(ax_step, [vert_data, thr_data, step_mark_data], min_span=0.06)

    # Se usa el color de fondo como indicador contextual:
    # fondo verdoso = modo WALK activo; fondo oscuro neutro = IDLE o sin actividad.
    ax_move.set_facecolor("#132218" if active else "#181818")
    ax_step.set_facecolor("#151f18" if active else "#181818")

    txt_steps.set_text(f"Pasos: {state['steps']}")
    txt_temp.set_text(f"T: {safe_float(state['temp'], math.nan):.1f} C" if state["temp"] != "--" else "T: -- C")
    txt_hum.set_text(f"H: {safe_float(state['hum'], math.nan):.1f} %" if state["hum"] not in (None, "--") else "H: -- %")
    txt_light.set_text(f"L: {safe_float(state['light'], math.nan):.1f} %" if state["light"] not in (None, "--") else "L: -- %")

    if stale:
        txt_status.set_text("SIN DATOS")
        txt_status.set_color("#cc4444")
    elif active:
        txt_status.set_text("WALK")
        txt_status.set_color("#00ff88")
    else:
        txt_status.set_text("IDLE")
        txt_status.set_color("#888888")

    txt_values.set_text(
        f"seq={state['seq']} | mov_env={state['hmag']:.4f} | "
        f"vert={state['vert']:.4f} | thr={state['step_thr']:.4f} | "
        f"score={state['walk_score']} ac={state['autocorr']:.2f} "
        f"cad={state['cadence']:.2f} cv={state['interval_cv']:.2f} "
        f"hf={state['hf_ratio']:.2f} jerk={state['jerk_ratio']:.2f}"
    )
    # Esta línea inferior es básicamente telemetría de depuración en vivo:
    # sirve para entender por qué el detector está o no en WALK.

    return (
        line_move,
        line_walk_on,
        line_walk_off,
        line_vert,
        line_thr,
        line_marks,
        txt_steps,
        txt_temp,
        txt_hum,
        txt_light,
        txt_status,
        txt_values,
    )


ani = FuncAnimation(fig, update, interval=INTERVALO_MS, blit=False, cache_frame_data=False)
# FuncAnimation llama periódicamente a update().
# blit=False simplifica el refresco aunque sea algo menos eficiente.
# cache_frame_data=False evita acumular memoria en animaciones largas.

plt.tight_layout()
plt.show()