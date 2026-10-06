# Comprobaciones de preparación

Fecha: 6 de octubre de 2026.

- Los cuatro scripts Python se analizan sintácticamente sin errores.
- Se utiliza la función real `build_packet` para construir una telemetría sintética con 32 campos y las funciones reales de parseo del receptor para interpretarla. Se comprueban pasos, secuencia, actividad, variables ambientales y medidas crudas.
- La extracción de esas funciones por AST permite comprobar el protocolo sin abrir interfaces gráficas, puertos UDP, I2C ni SPI.

No se ha ejecutado el circuito, medido la frecuencia efectiva, contrastado la precisión del podómetro ni realizado una prueba UDP entre dos equipos. Los ajustes de calibración y visualización se conservan como parte de la entrega. Para una validación física, preparar el hardware y ejecutar emisor y receptores siguiendo el README y la presentación.
