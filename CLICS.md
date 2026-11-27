# Cómo se registra cada clic

Al abrir la aplicación con `Iniciar_EMG.bat`, se habilita el registro de botones. Al pulsar **Grabar**, `pynput` inicia un listener que recibe desde el sistema operativo los eventos de los botones izquierdo y derecho, incluso cuando otra ventana está activa. Se registran por separado la pulsación (`press`) y la liberación (`release`); los demás botones se ignoran. Al detener, el listener se detiene.

Cada evento recibe un instante del reloj monotónico del PC (`time.perf_counter_ns()`) cuando llega al callback. El callback lo coloca en una cola; la aplicación lo recoge y escribe en `clicks.csv`: instante `pc_ns`, segundos desde el inicio `time_s`, botón, acción y dedo asignado. La asignación de dedo se elige manualmente y no identifica automáticamente el dedo.

La EMG procede del Arduino y sus tiempos se estiman en el reloj del PC mediante el desfase USB mínimo observado durante el primer segundo de conexión. Así, EMG y clics usan un origen común al pulsar Grabar, pero su alineación es **aproximada**: el USB, el sistema operativo, el mouse y la deriva de relojes pueden introducir desfases. El instante de callback no es el instante físico exacto del interruptor. Para medir latencias de pocos milisegundos se necesita una referencia física capturada por el mismo reloj.

Si se usa un Arduino por canal, cada placa tiene su propio reloj y su propio desfase USB estimado. Los dos canales y los clics comparten el origen temporal del PC, pero no están sincronizados físicamente entre sí.

Se incluyen los clics realizados sobre la propia aplicación mientras se graba. F8 permite comenzar y detener sin clicar los controles. La aplicación no mueve el cursor ni genera clics. Si falla el listener o se llena la cola, la sesión se interrumpe para evitar pérdidas silenciosas. Los eventos de la demo sin ventana son sintéticos.
