# emg_live

## Registro EMG — Arduino UNO R4 Minima

Aplicación local para registrar **actividad muscular**. Por defecto muestra y graba solo EMG: señal procesada, RMS móvil de 100 ms, valores originales del ADC y parámetros de la sesión. La ventana gráfica se abre al ejecutar Python desde VS Code. Puedes añadir etiquetas manuales de bloques.

El registro de botones del mouse se conserva como función opcional, **desactivada por defecto**. No se inicia ningún listener del mouse, no aparece el panel de clics y no se crea `clicks.csv` al ejecutar normalmente. Ningún modo usa micrófono ni depende del silencio ambiental.

## Inicio rápido en este equipo

1. Abre `EMG.code-workspace` en VS Code (o abre esta carpeta `emg_live`).
2. Ya hay un entorno `.venv` preparado. Desde la terminal integrada de esta carpeta:

   ```powershell
   .\.venv\Scripts\python.exe app.py --demo
   ```

3. Pulsa **Conectar**. La demo muestra señales EMG sintéticas. Pulsa **Grabar** para probar su guardado en CSV. Las carpetas de demo llevan prefijo `DEMO_`.
4. Para datos reales, carga primero el sketch indicado abajo. Ejecuta `app.py` sin `--demo`, selecciona el puerto COM, **2 canales**, y pulsa **Conectar**. Tras la calibración inicial se habilita **Grabar**.

También puedes usar F5 con las configuraciones de ejecución incluidas; requiere las extensiones Python/Python Debugger de VS Code. Si no están instaladas, usa el comando de terminal.

En otra instalación de Python, ejecuta `powershell -ExecutionPolicy Bypass -File .\setup.ps1`, o crea un entorno con `python -m venv .venv` e instala `requirements.txt` con el Python de ese entorno. No hace falta activar el entorno.

## Conexión de los dos canales

### Dos Arduinos, uno por canal

Carga `arduino/emg_capture/emg_capture.ino` en **cada UNO R4**. Conecta el sensor de cada uno a su entrada A0 según su montaje. En la aplicación selecciona **Arduino**, **2 canales** y marca **Un Arduino por canal**. Elige el puerto del canal 1 y el puerto del canal 2; deben ser distintos. Pulsa **Conectar** y espera a que ambos estén listos antes de grabar. El sketch recibe el comando de un canal en cada placa.

Cada Arduino tiene reloj, filtro, contador y calibración USB independientes. Las señales se ubican en un mismo reloj estimado del PC, pero **no están sincronizadas por hardware**: USB y la deriva entre relojes pueden producir desfases. No uses esta alineación para medir latencias precisas sin una referencia física común. Si falla uno de los puertos, se detiene la captura. La barra muestra pérdidas y tramas inválidas de cada placa.

En este modo `emg.csv` contiene el canal 1 y `emg_ch2.csv` el canal 2. Cada archivo usa las columnas `ch1_*` porque registra una entrada A0 independiente; cada fila conserva su propio tiempo y secuencia. `metadata.json` identifica los puertos, desfases y pérdidas de cada placa. La gráfica Plotly lee ambos archivos con sus tiempos sin forzar muestras simultáneas.

- **Arduino:** UNO R4 Minima con el Muscle BioAmp Shield instalado.
- **CH1 / A0:** canal incorporado en el shield, con su cable de electrodos.
- **CH2 / A2:** `OUT` de la BioAmp EXG Pill al terminal de señal del puerto STEMMA Analog (A2), `VCC` a 5 V y `GND` a GND. Verifica las etiquetas de los pines; no deduzcas el orden por el color del cable.
- Cada módulo recibe su propio par IN+/IN− sobre la zona muscular correspondiente y su referencia, siguiendo el montaje del fabricante. No conectes electrodos directamente a A0/A2.
- La Pill debe estar configurada apropiadamente para EMG; consulta el puente del filtro en su documentación.
- Como alternativa, también puedes usar un solo Arduino con dos entradas y dejar desmarcada la opción de un Arduino por canal.
- Para usar únicamente el shield, selecciona **1 canal**: el firmware deja de leer A2.

Los nombres iniciales «extensores» y «flexores» se pueden editar antes de conectar. Son etiquetas del montaje, no identificación automática de músculos ni de dedos.

## Cargar el Arduino

1. Abre `arduino/emg_capture/emg_capture.ino` en Arduino IDE.
2. Selecciona **Arduino UNO R4 Minima** y su puerto, y sube el sketch. Usa el paquete **Arduino Renesas UNO R4 Boards**.
3. Cierra el Monitor Serie, Serial Plotter y la conexión de Chords antes de conectar desde Python.

Este sketch reemplaza el firmware que esté cargado. Usa un protocolo binario propio con verificación CRC, **no es el protocolo Chords** y no es compatible con sus CSV previos. No requiere librerías externas en Arduino.

La adquisición solicitada es de **2.000 muestras/s por canal**, resolución **14 bits**, rango **0–16383**, USB serie a 500000 baudios (en el R4 la interfaz USB nativa no depende de ese baud rate). La frecuencia se eligió para superar el doble de los 720 Hz del límite superior nominal del shield; no sustituye la caracterización del filtro analógico ni garantiza ausencia total de aliasing. El contador y `device_us` permiten medir la frecuencia efectiva y detectar saltos.

## Grabación

- **Grabar / Detener**, o **F8** con la aplicación enfocada: empieza o termina una sesión.
- **Tiempo grabado:** contador visible en horas, minutos, segundos y décimas. Comienza en cero al pulsar Grabar y se congela al detener. Cada nueva grabación vuelve a cero; el gráfico, los clics y los CSV usan el mismo inicio. Antes de grabar, el gráfico muestra una vista previa de los últimos 8 segundos respecto de ahora.
- Al detener, el gráfico conserva la última vista de la grabación y se habilita **Nombre al detener**: escribe un nombre y pulsa **Guardar nombre** (o Enter). La carpeta pasa a llamarse `nombre_fecha_hora` (incluye `DEMO_` en pruebas) y el nombre queda en `metadata.json`. Nombrarla es opcional: los datos ya están guardados con fecha y hora. Puedes corregir el nombre hasta empezar otra grabación.
- **Marcar bloque / F9**: agrega una etiqueta («reposo», «índice», «medio», o texto libre) con su instante.
- Azul: señal con eliminación de continua (20 Hz) y notch opcional 50/60 Hz. Naranja: RMS causal de 100 ms. El original permanece en el CSV.
- **Eje Y manual:** escribe el mínimo y el máximo y pulsa **Aplicar escala** (o Enter). Parte en −2000 a +2000 cuentas ADC para ambos canales; puedes ajustarlo durante la captura y se conserva al desconectar y volver a conectar mientras la aplicación siga abierta. No se reajusta automáticamente: si la señal sale de la vista, amplía los límites manualmente; los datos se siguen grabando completos.
- Azul y naranja comparten el mismo eje y el mismo cero. El RMS es la raíz del promedio de los cuadrados de la señal filtrada durante los últimos 100 ms: siempre es positivo o cero, resume la amplitud y **no es una envolvente de picos**. Puede estar por encima o por debajo del valor instantáneo azul y mantenerse elevado brevemente después de una ráfaga.
- El gráfico muestra aproximadamente los últimos 8 segundos. **Guardar gráfica** exporta esa vista, no toda la grabación.
- **Plotly** abre en el navegador una gráfica interactiva de la grabación completa después de detenerla. Si aún no hay una sesión en la aplicación, permite seleccionar una carpeta de grabación. Guarda `grafica_plotly.html` dentro de esa carpeta; el archivo funciona sin conexión y permite zoom y consultar valores. El gráfico en vivo sigue en la ventana principal. El botón **Cómo se captan clics** abre `CLICS.md` con la explicación del registro.
- F8 y F9 funcionan con la aplicación enfocada; no son atajos globales. Las marcas de bloques son anotaciones manuales, no detección de clics.
- «Saturadas» cuenta muestras que llegan al extremo del ADC. Las pérdidas o errores se muestran; ante fallo de adquisición o de disco se detiene la sesión y se intenta conservar lo recibido.

Prueba inicial sugerida: unos segundos de reposo; bloque con movimientos del índice; otro con movimientos del medio; misma postura y mano apoyada. Puedes etiquetar cada bloque manualmente. Los canales EMG no se asignan automáticamente a un dedo ni detectan por sí solos el momento exacto de un clic.

## Archivos de cada sesión

Se crean en `recordings/AAAAMMDD_HHMMSS_microsegundos/` sin sobrescribir sesiones anteriores.

| Archivo | Contenido |
|---|---|
| `emg.csv` | Contador, tiempo Arduino extendido, desfase CH2, tiempos PC, ADC originales, señal procesada, RMS y pérdidas. |
| `markers.csv` | Etiquetas de bloques y tiempos. |
| `metadata.json` | Configuración, origen temporal, conteos, modo de captura y si la sesión terminó correctamente. |

`ch2_*` queda vacío en modo de un canal. Las amplitudes son **cuentas del ADC**, no microvoltios musculares calibrados. Se hace flush aproximadamente cada segundo; un cierre forzado puede perder los últimos datos en búfer. `complete: false` indica una sesión que no terminó normalmente.

## Visualizar una grabación y análisis wavelet

Para generar una imagen con la señal filtrada, RMS de 100 ms, marcas de pulsación (si existen) y su scalograma wavelet:

```powershell
.\.venv\Scripts\python.exe analizar_emg.py "recordings\Prueba2 PG_20261126_161647_265169"
```

La imagen `analysis_wavelet.png` se guarda dentro de la sesión. También puedes pasar la ruta directa a `emg.csv`, elegir `--channel ch2_filtered` o cambiar el rango, por ejemplo `--low-hz 30 --high-hz 400`. El cálculo usa la wavelet continua Morlet compleja (CWT) y potencia relativa en dB, con bins temporales de 50 ms. Reinterpola la señal a la frecuencia nominal; interpola brechas menores de 10 ms y separa las mayores. Omite los bordes de cada tramo para reducir efectos de borde.

El wavelet es viable para explorar cuándo cambia la energía por frecuencia durante la actividad EMG. El resultado depende del filtrado previo: `ch1_filtered` ya tiene pasa-altos de 20 Hz y notch de 50 Hz, por lo que la energía alrededor de 50 Hz está atenuada y no sirve para evaluar esa componente. Las frecuencias son estimaciones, no una calibración clínica; esta grabación está expresada en cuentas ADC y usa una frecuencia nominal de 2 kHz.

## Tiempos y límites de sincronización

- `device_us` es el instante previo a la conversión de CH1 según el reloj del Arduino; se extiende al superar el rollover de `micros()` (~71,6 minutos).
- `ch2_delta_us` es el desfase medido antes de la conversión de CH2. Los dos canales se leen **secuencialmente**.
- `pc_rx_ns` conserva el reloj monotónico de recepción en el PC. Varios paquetes de una lectura USB comparten ese instante.
- Durante el primer segundo se estima el menor desfase PC−Arduino observado. Luego se fija y se calcula `pc_est_ns` y `time_s_est`, que se usan para el gráfico. No se guarda ese primer segundo de calibración.
- **La alineación con el PC es aproximada:** USB, el sistema operativo y la deriva entre relojes introducen retrasos no calibrados. Para estudiar latencias de pocos milisegundos respecto a un evento externo se necesita una referencia física capturada por el mismo reloj; este proyecto no la implementa.
- El RMS y los filtros causales introducen retardo y transitorios. Conserva la EMG original para análisis de latencias; no uses el pico de RMS como instante exacto de contracción. Se reinicia el filtro después de una pérdida.
- Un reinicio del Arduino interrumpe la captura: no se unen artificialmente dos relojes distintos.
- Para sesiones largas, conserva `device_us` y `pc_rx_ns` y evalúa la deriva antes de extraer conclusiones temporales.

## Registro de botones del mouse

**El lanzador `Iniciar_EMG.bat` abre la app con el registro del mouse habilitado.** Aparece el panel de clics; la captura global de botones comienza al pulsar **Grabar** y termina al detener la grabación. Para abrir solo EMG, ejecuta `app.py` directamente sin `--mouse-events`.

También puedes activar el registro desde la terminal:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-mouse.txt
.\.venv\Scripts\python.exe app.py --mouse-events
```

Las configuraciones F5 incluidas y el comando normal `app.py` **no activan esta función**. No necesitas cambiar ni volver a cargar el sketch del Arduino para alternar modos.

Al activar esta opción aparece el panel de clics y la asignación botón→dedo. Durante la grabación se capturan pasivamente pulsaciones/liberaciones de botones izquierdo/derecho en cualquier ventana y se guardan en `clicks.csv`. No se mueve el cursor, no se generan clics y no se usa audio. La asignación de dedos es manual y puede invertirse o dejarse sin asignar.

`pc_ns` es el instante de entrada al callback del sistema operativo; `time_s` usa el mismo origen PC que la EMG estimada. USB y el mouse añaden latencias no calibradas: estas marcas no representan contacto físico exacto. Los clics sobre los controles también se registran; F8 permite comenzar/terminar sin pulsarlos. Ante fallo del listener se detiene la sesión. `metadata.json` indica `capture_mouse_events: true`; en el modo normal indica `false`.

Para volver a solo EMG, cierra la aplicación y ejecútala sin `--mouse-events`.

## Pruebas sin hardware

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_core.py
.\.venv\Scripts\python.exe -m unittest -v test_app.py
.\.venv\Scripts\python.exe app.py --demo --headless --seconds 6 --output test_outputs
```

La prueba sin ventana genera **solo EMG sintética**, escribe los archivos y produce `preview.png`. Añade `--mouse-events` únicamente para probar también eventos sintéticos; la prueba sin ventana nunca escucha tu mouse. Se prueban CRC, recuperación de tramas, rollovers, reinicios, pérdidas, filtros, marcas temporales, grabaciones interrumpidas y que el modo predeterminado omita los eventos del mouse. Las pruebas sintéticas no validan la calidad de los electrodos ni la frecuencia efectiva del hardware.

## Fuentes del montaje y APIs

- [Muscle BioAmp Shield: 1 canal, puerto analógico A2 y ampliación con Pill](https://docs.upsidedownlabs.tech/hardware/bioamp/muscle-bioamp-shield/index.html).
- [BioAmp EXG Pill: conexión y configuración para EMG](https://docs.upsidedownlabs.tech/hardware/bioamp/bioamp-exg-pill/index.html).
- [Resolución ADC del UNO R4 Minima](https://docs.arduino.cc/tutorials/uno-r4-minima/adc-resolution/).
- [pySerial](https://pyserial.readthedocs.io/en/latest/pyserial_api.html), [pynput: eventos del mouse](https://pynput.readthedocs.io/en/latest/mouse.html), [Matplotlib con Tk](https://matplotlib.org/stable/gallery/user_interfaces/embedding_in_tk_sgskip.html).
