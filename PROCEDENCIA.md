# Procedencia y selección de versiones

Trabajo académico compartido de Miguel Pajuelo Gómez y Jorge Ois de Pascual. Los cuatro scripts y la presentación proceden de `Entrega SE`.

El emisor de entrega incorpora factores de corrección para temperatura y humedad y cambios en la conversión de la luz respecto al emisor de trabajo. Se selecciona la entrega como conjunto coherente con sus receptores y se adapta únicamente la IP de destino a la variable `PC_IP`. Las conversiones, el detector y las calibraciones se mantienen.

Los requisitos se separan por dispositivo según los imports reales: el ordenador necesita Matplotlib y Tkinter; la Raspberry, NumPy, `gpiozero` y `smbus`. No se copia el entorno `.conda`. El archivo `digital-7.ttf` se conserva en el archivo local, y esta copia utiliza la fuente instalada o la sustitución disponible del sistema.

`leer_sensores_terminal.py` es una herramienta de diagnóstico: sus fórmulas de conversión no incorporan todos los factores de corrección del emisor de entrega. Las lecturas de esa herramienta y las transmitidas no deben interpretarse como una misma calibración.
