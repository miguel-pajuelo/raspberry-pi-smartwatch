"""
caratula_reloj.py

Script para ejecutar en el ordenador.

Simula la pantalla de un reloj inteligente y escucha los paquetes JSON enviados
por enviar_informacion.py. Muestra:
  - hora local,
  - pasos,
  - temperatura,
  - humedad,
  - luz,
  - estado WALK/IDLE,
  - modo nocturno si la luz baja del 40%.

Los pasos se muestran relativos al primer paquete recibido por esta ventana.
Asi, si la Raspberry ya llevaba pasos acumulados, la caratula empieza en 0.

Usa el puerto 5006 para poder ejecutarse a la vez que recibir_informacion.py,
que escucha en el puerto 5005.
"""

from __future__ import annotations

import json
import math
import socket
import threading
import time
from datetime import datetime
import tkinter as tk
from tkinter import font as tkfont


class CasioSimulator:
    """Interfaz tipo reloj que se actualiza con los ultimos datos UDP."""

    BG_ROOT = "#202020"
    BG_LCD = "#C8D8A8"
    BG_NIGHT = "#000000"
    LCD_DARK = "#4A5A3A"
    TEXT_ON = "#1A2A0A"
    TEXT_GLOW = "#39FF14"
    ACCENT = "#DAA520"
    BLUE_LINE = "#0077CC"
    LOW_LIGHT_THRESHOLD = 15.0
    ADC_REF_VOLTAGE = 3.3
    NTC_R_SERIE = 470.0
    NTC_R25 = 470.0
    NTC_BETA = 3450.0
    NTC_T25_K = 298.15

    UDP_IP = "0.0.0.0"
    UDP_PORT = 5006
    STALE_TIMEOUT_S = 2.0
    STEP_DISPLAY_GAIN = 1.3

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Smartwatch - Simulador")
        self.root.geometry("640x500")
        self.root.configure(bg=self.BG_ROOT)
        self.root.resizable(False, False)

        # Hay dos hilos implicados:
        # 1) el principal de Tkinter, que pinta la interfaz
        # 2) el hilo UDP, que recibe paquetes
        # Este lock evita leer/escribir el estado compartido a la vez.
        self._lock = threading.Lock()

        self._last_packet_time = 0.0
        self._base_steps: int | None = None

        # Estado mínimo compartido entre el receptor UDP y la interfaz.
        # La UI no lee directamente del socket: trabaja contra esta copia estable.
        self._data = {"steps": 0, "temp": None, "hum": None, "light": None, "active": False}

        self._night_mode = False

        # Estas listas guardan referencias a widgets/elementos del canvas
        # para poder recolorearlos fácilmente al cambiar de tema.
        self._lcd_labels: list[tk.Label] = []
        self._lcd_text_items: list[int] = []
        self._lcd_line_items: list[int] = []

        self._setup_fonts()
        self._build_ui()
        self._start_udp()
        self._tick()

    def _setup_fonts(self) -> None:
        """Carga la fuente digital si esta instalada y usa fallback si no."""
        try:
            # Si el sistema tiene una fuente tipo reloj digital, se usa para
            # reforzar la estética de smartwatch/reloj Casio.
            self.f_main = tkfont.Font(family="Digital-7", size=88)
            self.f_side = tkfont.Font(family="Digital-7", size=28)
            self.f_small = tkfont.Font(family="Digital-7", size=20)
        except Exception:
            # Si la fuente no existe, se usa una monoespaciada robusta.
            self.f_main = tkfont.Font(family="Courier", size=72, weight="bold")
            self.f_side = tkfont.Font(family="Courier", size=22, weight="bold")
            self.f_small = tkfont.Font(family="Courier", size=16, weight="bold")

    def _build_ui(self) -> None:
        """Construye la caratula y todos los campos dinamicos."""
        width, height = 600, 420

        # El canvas actúa como “pantalla LCD” del reloj.
        self.canvas = tk.Canvas(
            self.root,
            bg=self.BG_LCD,
            width=width,
            height=height,
            highlightthickness=5,
            highlightbackground="#8A9A7A",
        )
        self.canvas.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        self._lcd_text_items.append(
            self.canvas.create_text(
                22,
                18,
                text="CASIO  SENSOR WATCH",
                font=("Helvetica", 11, "bold"),
                fill=self.ACCENT,
                anchor="nw",
            )
        )

        # Líneas decorativas/separadoras para reforzar el look de reloj digital.
        self._lcd_line_items.append(self.canvas.create_line(15, 70, width - 15, 70, fill=self.BLUE_LINE, width=2))
        self._lcd_line_items.append(self.canvas.create_line(15, 342, width - 15, 342, fill=self.BLUE_LINE, width=2))

        # Hora principal.
        self.lbl_time = tk.Label(self.canvas, text="00:00", font=self.f_main, bg=self.BG_LCD, fg=self.TEXT_ON)
        self.lbl_time.place(x=300, y=210, anchor=tk.CENTER)

        # Los segundos se separan visualmente para imitar un display segmentado.
        self._lcd_text_items.append(
            self.canvas.create_text(
                width - 36,
                155,
                text="ss",
                font=("Helvetica", 9, "bold"),
                fill=self.LCD_DARK,
                anchor="e",
            )
        )
        self.lbl_sec = tk.Label(self.canvas, text="00", font=self.f_side, bg=self.BG_LCD, fg=self.TEXT_ON)
        self.lbl_sec.place(x=width - 48, y=205, anchor=tk.CENTER)

        # Panel superior de temperatura y humedad.
        self._lcd_text_items.append(
            self.canvas.create_text(
                width // 4,
                84,
                text="TEMPERATURA",
                font=("Helvetica", 9, "bold"),
                fill=self.LCD_DARK,
                anchor="center",
            )
        )
        self.lbl_temp = tk.Label(self.canvas, text="--.-C", font=self.f_side, bg=self.BG_LCD, fg=self.TEXT_ON)
        self.lbl_temp.place(x=width // 4, y=118, anchor=tk.CENTER)

        self._lcd_text_items.append(
            self.canvas.create_text(
                3 * width // 4,
                84,
                text="HUMEDAD",
                font=("Helvetica", 9, "bold"),
                fill=self.LCD_DARK,
                anchor="center",
            )
        )
        self.lbl_hum = tk.Label(self.canvas, text="--%", font=self.f_side, bg=self.BG_LCD, fg=self.TEXT_ON)
        self.lbl_hum.place(x=3 * width // 4, y=118, anchor=tk.CENTER)

        self._lcd_line_items.append(self.canvas.create_line(width // 2, 75, width // 2, 145, fill=self.LCD_DARK, width=1))

        # Este campo queda reservado pero no se llega a materializar como label.
        # Ahora mismo la luz sí se usa para el modo nocturno, pero no se pinta explícitamente.
        self.lbl_light = None

        # Panel inferior: pasos, modo y estado de conexión.
        self._lcd_text_items.append(self.canvas.create_text(30, 350, text="STEPS", font=("Helvetica", 9, "bold"), fill=self.LCD_DARK, anchor="nw"))
        self.lbl_steps = tk.Label(self.canvas, text="00000", font=self.f_side, bg=self.BG_LCD, fg=self.TEXT_ON)
        self.lbl_steps.place(x=115, y=376, anchor=tk.CENTER)

        self._lcd_text_items.append(self.canvas.create_text(330, 350, text="MODE", font=("Helvetica", 9, "bold"), fill=self.LCD_DARK, anchor="nw"))
        self.lbl_mode = tk.Label(self.canvas, text="IDLE", font=("Helvetica", 14, "bold"), bg=self.BG_LCD, fg=self.LCD_DARK)
        self.lbl_mode.place(x=385, y=376, anchor=tk.CENTER)

        self.lbl_status = tk.Label(self.canvas, text="SIN DATOS", font=("Helvetica", 10, "bold"), bg=self.BG_LCD, fg=self.LCD_DARK)
        self.lbl_status.place(x=515, y=376, anchor=tk.CENTER)

        # Fecha compacta en esquina inferior.
        self.lbl_date = tk.Label(self.canvas, text="", font=("Helvetica", 11), bg=self.BG_LCD, fg=self.LCD_DARK)
        self.lbl_date.place(x=width - 20, y=height - 14, anchor="se")

        self._lcd_labels = [
            self.lbl_time,
            self.lbl_sec,
            self.lbl_temp,
            self.lbl_hum,
            self.lbl_steps,
            self.lbl_mode,
            self.lbl_status,
            self.lbl_date,
        ]

    @staticmethod
    def _format_number(value: object, suffix: str, decimals: int = 1) -> str:
        """Formatea sensores que pueden llegar como numero, None o '--'."""
        if value in (None, "--", "None", ""):
            return f"--{suffix}"
        try:
            return f"{float(value):.{decimals}f}{suffix}"
        except (TypeError, ValueError):
            # No se propaga el error: la interfaz siempre muestra algo estable.
            return f"--{suffix}"

    @staticmethod
    def _to_float(value: object, default: float | None = None) -> float | None:
        """Convierte campos UDP opcionales sin romper la interfaz."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _temperature_from_packet(self, packet: dict[str, object]) -> object:
        """Recalcula la temperatura desde el voltaje crudo igual que leer_sensores_terminal.py."""
        raw = packet.get("raw")
        if not isinstance(raw, dict) or "temperature_v" not in raw:
            return packet.get("temp", None)

        voltage = self._to_float(raw.get("temperature_v"))
        if voltage is None or voltage <= 0.0 or voltage >= self.ADC_REF_VOLTAGE:
            return packet.get("temp", None)

        # Misma ecuacion del NTC que en leer_sensores_terminal.py.
        r_ntc = self.NTC_R_SERIE * voltage / (self.ADC_REF_VOLTAGE - voltage)
        if r_ntc <= 0.0:
            return packet.get("temp", None)

        inv_t = (1.0 / self.NTC_T25_K) + (1.0 / self.NTC_BETA) * math.log(r_ntc / self.NTC_R25)
        return (1.0 / inv_t) - 273.15

    def _apply_theme(self, night_mode: bool) -> None:
        """Cambia entre LCD normal y modo fosforito con fondo negro."""
        if night_mode == self._night_mode:
            # Si ya está aplicado ese tema, no hace trabajo extra.
            return

        self._night_mode = night_mode

        bg = self.BG_NIGHT if night_mode else self.BG_LCD
        fg = self.TEXT_GLOW if night_mode else self.TEXT_ON
        muted = self.TEXT_GLOW if night_mode else self.LCD_DARK
        line = self.TEXT_GLOW if night_mode else self.BLUE_LINE
        border = self.TEXT_GLOW if night_mode else "#8A9A7A"

        # En modo nocturno cambia toda la paleta para simular display iluminado.
        self.root.configure(bg=bg if night_mode else self.BG_ROOT)
        self.canvas.configure(bg=bg, highlightbackground=border)

        for label in self._lcd_labels:
            label.configure(bg=bg, fg=fg)

        for item in self._lcd_text_items:
            self.canvas.itemconfigure(item, fill=muted)

        for item in self._lcd_line_items:
            self.canvas.itemconfigure(item, fill=line)

    def _tick(self) -> None:
        """Refresca la hora y pinta el ultimo paquete recibido."""
        now = datetime.now()
        self.lbl_time.config(text=now.strftime("%I:%M"))
        self.lbl_sec.config(text=now.strftime("%S"))
        self.lbl_date.config(text=now.strftime("%a %d %b"))

        with self._lock:
            # Se hace copia rápida del estado bajo lock y luego se libera.
            # Así Tkinter no se queda retenido mientras el hilo UDP trabaja.
            data = dict(self._data)
            last_packet_time = self._last_packet_time

        stale = (time.monotonic() - last_packet_time) > self.STALE_TIMEOUT_S
        active = bool(data["active"]) and not stale

        light_percent = self._to_float(data["light"])

        # El modo nocturno no depende solo de luz baja:
        # también exige que los datos sigan vivos.
        # Si no llegan paquetes, no tiene sentido mantener un estado visual heredado.
        night_mode = (
            light_percent is not None
            and light_percent < self.LOW_LIGHT_THRESHOLD
            and not stale
        )
        self._apply_theme(night_mode)

        self.lbl_steps.config(text=f"{int(data['steps']):05d}")
        self.lbl_temp.config(text=self._format_number(data["temp"], "C", 1))
        self.lbl_hum.config(text=self._format_number(data["hum"], "%", 1))

        if stale:
            self.lbl_status.config(text="SIN DATOS", fg="#8B0000")
        else:
            self.lbl_status.config(text="LIVE", fg=self.TEXT_GLOW if night_mode else "#1A7A1A")

        if active:
            self.lbl_mode.config(text="WALK", fg=self.TEXT_GLOW if night_mode else "#1A7A1A")
        else:
            self.lbl_mode.config(text="IDLE", fg=self.TEXT_GLOW if night_mode else self.LCD_DARK)

        # Tkinter trabaja con temporización cooperativa: se agenda el siguiente refresco.
        self.root.after(250, self._tick)

    def _start_udp(self) -> None:
        """Arranca el receptor UDP en un hilo para no bloquear Tkinter."""
        threading.Thread(target=self._udp_loop, daemon=True).start()

    def _udp_loop(self) -> None:
        """Recibe paquetes JSON del emisor y actualiza el estado compartido."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.UDP_IP, self.UDP_PORT))
        sock.settimeout(1.0)

        while True:
            try:
                raw, _ = sock.recvfrom(4096)
                message = raw.decode("utf-8").strip()

                # Esta carátula solo acepta el protocolo JSON actual.
                # A diferencia del visualizador grande, aquí no hay compatibilidad con CSV legado.
                if not message.startswith("{"):
                    continue

                packet = json.loads(message)
                remote_steps = int(packet.get("steps", 0))

                if self._base_steps is None:
                    # El primer valor recibido se toma como origen local.
                    self._base_steps = remote_steps

                # La interfaz muestra pasos relativos a la sesión actual, no el acumulado histórico.
                raw_display_steps = max(0, remote_steps - self._base_steps)

                # Igual que en el otro visualizador, aquí se mete una ganancia visual.
                # Ojo: esto altera la presentación, no el dato real recibido.
                display_steps = int(round(raw_display_steps * self.STEP_DISPLAY_GAIN))
                display_temp = round(self._temperature_from_packet(packet),1)
                

                with self._lock:
                    # El hilo UDP nunca toca widgets de Tkinter directamente.
                    # Solo actualiza el estado compartido; pintar corresponde a _tick().
                    self._data.update(
                        steps=display_steps,
                        temp=display_temp,
                        hum=packet.get("hum", None),
                        light=packet.get("light", None),
                        active=bool(packet.get("active", False)),
                    )
                    self._last_packet_time = time.monotonic()

            except socket.timeout:
                # Timeout corto para que el hilo siga vivo sin bloquearse indefinidamente.
                continue
            except Exception as exc:
                print(f"[UDP] Error: {exc}")


if __name__ == "__main__":
    root = tk.Tk()
    app = CasioSimulator(root)
    root.mainloop()
