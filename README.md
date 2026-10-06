# Reloj y podómetro con Raspberry Pi

Proyecto académico de **Miguel Pajuelo Gómez y Jorge Ois de Pascual** para Sistemas Electrónicos, ICAI, Universidad Pontificia Comillas.

Adquisición de señales inerciales y ambientales en una Raspberry Pi, detección de pasos y envío de telemetría JSON por UDP. El ordenador recibe los datos y muestra un visualizador técnico y una carátula de reloj.

```text
MPU6050 (I2C) + sensores analógicos (MCP3008 / SPI)
          ↓
Raspberry Pi: filtrado, detección de marcha y conteo de pasos
          ├── UDP 5005 → ordenador/recibir_informacion.py
          └── UDP 5006 → ordenador/caratula_reloj.py
```

## Contenido

| Carpeta | Componentes |
|---|---|
| [raspberry_pi/](raspberry_pi/) | `enviar_informacion.py`, `leer_sensores_terminal.py` y dependencias para Raspberry. |
| [ordenador/](ordenador/) | Recepción y gráficos con Matplotlib; interfaz Tkinter del reloj. |
| [documentacion/](documentacion/) | [Presentación original](documentacion/presentacion.pptx). |

Se elige el conjunto de la entrega para mantener juntos emisor y receptores de esa versión. El código de trabajo contiene otras calibraciones y se conserva en el archivo original. Consultar [PROCEDENCIA.md](PROCEDENCIA.md).

## Hardware y configuración

El código usa el bus I2C 1 y un MPU6050 en la dirección `0x68`. El MCP3008 recibe humedad en CH0, temperatura en CH1 y luz en CH2; las conversiones toman una referencia de 3,3 V. Es necesario habilitar I2C y SPI en Raspberry Pi OS y comprobar el montaje descrito en la presentación. El repositorio conserva el software; no incluye un esquema de cableado independiente que no existía en la entrega.

Crear un entorno Python en cada dispositivo. En la Raspberry, instalar las dependencias de `raspberry_pi/requirements.txt`; en el ordenador, las de `ordenador/requirements.txt`. Tkinter debe estar disponible en el Python del ordenador. En algunas distribuciones Linux se instala como paquete del sistema, por ejemplo `python3-tk`.

```sh
python -m venv .venv
# Activar el entorno del dispositivo antes de instalar:
# Windows: .venv\Scripts\Activate.ps1
# Linux:   . .venv/bin/activate
```

En la Raspberry:

```sh
python -m pip install -r raspberry_pi/requirements.txt
export PC_IP=IP_DEL_ORDENADOR
python raspberry_pi/enviar_informacion.py
```

Mantener el dispositivo en reposo durante la calibración inicial. El valor por defecto de `PC_IP` es `127.0.0.1`, de modo que hay que configurar la IP del ordenador para comunicar dos dispositivos. `.env.example` documenta la variable y no se carga automáticamente.

En el ordenador, abrir dos terminales si se quieren usar ambas ventanas:

```sh
python -m pip install -r ordenador/requirements.txt
python ordenador/recibir_informacion.py
```

```sh
python ordenador/caratula_reloj.py
```

Ambos equipos deben poder intercambiar UDP en los puertos 5005 y 5006. Para revisar solamente los sensores analógicos, ejecutar `python raspberry_pi/leer_sensores_terminal.py` en la Raspberry.

## Interpretación y límites

El emisor configura una adquisición nominal de 50 Hz y transmite aceleración, velocidad angular, magnitudes filtradas, pasos, estado WALK/IDLE y medidas ambientales. La entrega incluye correcciones empíricas de temperatura y humedad. Los receptores muestran pasos relativos a la sesión y aplican `STEP_DISPLAY_GAIN = 1.3`; la cifra visualizada no equivale directamente al contador bruto del emisor.

La carátula solicita la familia de fuente `Digital-7` si está instalada. La tipografía no se redistribuye aquí: la interfaz puede utilizar la sustitución del sistema. El cambio de modo nocturno depende de la luz; el umbral efectivo del código es 15 %, aunque parte de su comentario introductorio menciona 40 %.

La preparación de la carpeta no certifica precisión del podómetro, frecuencia efectiva de adquisición ni calibración del montaje. [VALIDACION.md](VALIDACION.md) recoge las comprobaciones de software y la ausencia de una prueba en hardware.
