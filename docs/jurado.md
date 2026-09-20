# Lo que ve el jurado, criterio por criterio

El marcador se congeló el 20-09-2026 a las 06:00 con el máximo (172 de 172, los 17 problemas con sus cuatro casos).
Seis equipos empatan a 172 y el desempate es la hora: Séneca llegó a las 04:05 y queda sexto. Lo que suma desde ahora
es la llamada del jurado y la demo. Este documento dice, para cada criterio, qué hace el agente, cómo enseñarlo en
directo y con qué cifra se defiende. Todas las cifras son medidas; donde no hay medida, se dice.

## 1. La experiencia del paciente

| Qué | Cómo se enseña |
|---|---|
| Arranca a hablar en ~0,7 s: mediana de 668 ms del fin de voz a la primera palabra, p90 de 1,3 s, medido con **diez llamadas de voz a la vez** (170 turnos, 20-09-2026) | Llamar y fijarse en que no hay silencios; en la consola, la barra de latencia de cada turno |
| Se le puede interrumpir: una negativa o una corrección cortan la voz, y también una sola palabra de alto («no», «espere», «wait»). Lo que no llegó a sonar no cuenta como dicho: una oferta cortada antes de la hora no está leída y un «sí» posterior no la reserva | Cortarle a mitad de una oferta y decir otra cosa; en la consola aparece «frase cortada: quien llama solo oyó…» |
| Cambiar de idea al tercer turno: las ofertas siguen sobre la mesa («mejor la primera que me dijo») y una escritura espera un turno por si la persona dice «eso no era» | Aceptar, arrepentirse y volver a una opción anterior |
| A un «no» a secas no ofrece huecos a ciegas: pregunta una vez qué no encaja (día, hora o médico) | Decir solo «no» a la primera oferta |
| Repara en vez de adivinar: la letra del DNI se comprueba contra los números, un dato pedido dos veces se pide de otra forma, y a la tercera se sigue con lo que haya | Dar un DNI con la letra cambiada |
| No inventa: todo hueco sale de la API; una hora que no sale de ninguna herramienta se tacha antes de sonar (`truth_guard`); un nombre, un DNI o una fecha que nadie ha dicho no se usan (`*_invented`) | Consola: las cajas «el núcleo corrigió al planificador» |
| Las horas suenan como las dice una persona («a las cuatro y media de la tarde», no «a las dieciséis treinta») | Oírlo en castellano o catalán |

## 2. Lo personal: la ficha antes de la pregunta

Las notas de las 2.900 fichas son una gramática cerrada: un resumen del historial y una pauta de trato (unas 45
distintas). `agent/ficha.py` las cubre **todas** (`python3 agent/ficha.py pacientes.json` lo comprueba: 2.900 de 2.900)
y convierte cada pauta en conducta:

- **Reconocer, no interrogar.** Si el número que llama está en una ficha, basta el nombre: «¿Con quién hablo, por
  favor?», no «nombre completo, DNI y fecha de nacimiento». Después, «Gracias, señora Sanz».
- **Su médico de siempre, pero elige quien llama.** Con más de la mitad de las visitas con un médico, se ofrece lo
  primero que haya *y* el primer hueco de ese médico: «Lo primero que tengo es… O, como le suele ver el Dr. Sáez,
  tengo…». Nunca se reserva el médico habitual por cuenta propia. Entre huecos empatados gana su médico y, si no, quien
  tiene la agenda más libre (reparto de carga).
- **La cita que ya tiene, antes de dar otra:** «Veo que ya tiene cita el martes 22 con el Dr. Iglesia. Si es para
  otra cosa: lo primero que tengo es…».
- **Nunca se pregunta si ha venido antes** a quien tiene visitas, ni se habla de primera visita.
- **El trato que pide la nota:** quien oye mal o llama con ruido recibe una voz más pausada (medido: el modelo v3 de
  ElevenLabs ignora la velocidad; se usa flash v2.5 a 0,85) y la fecha dos veces; «prefiere el apellido» → «Mr Baker»;
  «un pariente suele hablar por ella» → se aclara con quién se habla; «repasa la cita antes de colgar»; «se queda
  callado mirando la agenda» → no se da la línea por caída; etcétera.

Cómo se enseña: `agent/guion.py` hace cinco llamadas de guion fijo contra la clínica real (solo lectura) con fichas
reales de cada tipo. En la consola, el evento «la ficha pide un trato» dice qué pautas se han activado.

## 3. La plataforma

- **Consola en vivo** (`/` del agente): contador de llamadas a la vez y una fila por llamada activa (turno, lengua,
  última latencia, lo que se está oyendo); cada turno muestra lo que oyó Jev con sus confianzas, cada herramienta con
  argumentos, resultado y milisegundos, y las salvaguardas resaltadas.
- **«¿Por qué dijo eso?»**: clic en cualquier frase del agente → la cadena causal (qué oyó, qué juzgó Jev, qué
  herramientas, qué salvaguarda, qué carril escribió la frase y cuánto tardó cada paso). Enlace directo:
  `#llamada=<id>&frase=<n>`.
- **Llamadas pasadas** intercaladas por turno, con su latencia guardada en el informe.
- **Arena** (`https://prosper.joseluissaorin.com`): la llamada como materia en movimiento y un teléfono en el navegador.
- **Diez a la vez:** `PAR=10 python agent/voice_harness.py p1 p5 p13` → 12 llamadas de voz con ruido a 5 dB, 10
  simultáneas: 10 de 12 correctas (las dos restantes, el guion pregrabado no entiende una pregunta nueva del agente y
  cuelga), mediana de 668 ms, 104 intervenciones de voz con 2 huecos.

## 4. Seguridad y límites

Urgencias derivadas al 112 sin pasar por el planificador; nada de consejo médico, diagnósticos ni dosis; los datos de
otra persona no se leen; quien dice ser «el sistema» o un médico no cambia las reglas; la ficha de quien llama no es
el paciente cuando la cita es para otro; la nota de la ficha no se lee en voz alta ni se menciona.

## 5. Lengua

Castellano, catalán, gallego, euskera, inglés y francés, con trato de usted. Cambio de lengua a media llamada sin
reiniciar (arreglado el 20-09: «Hi. I'd like to see a GP…» contaba como «casi todo nombres propios» y se contestaba en
castellano). Un apellido español no cambia la lengua de una llamada en inglés. DNI leído agrupado o cifra a cifra según
la ficha.

## 6. Rigor de ingeniería

Ver [`rigor.md`](rigor.md) y [`modos-de-fallo.md`](modos-de-fallo.md): ocho arneses, varianza entre repeticiones con
intervalo de Wilson (`--rep N`), 528 llamadas de voz reales analizadas con sus modos de fallo por nombre, y **coste
por llamada medido** en el propio informe de cada llamada (bloque `cost`): una llamada de 63 s, 0,21 $ a precio de
lista (voz 0,16 $, planificador 0,036 $, oído 0,005 $, Jev 0,003 $), 22 peticiones a Jev, 20 pasos del planificador.
Regresión del 20-09 tras los cambios: arnés combinatorio 21/24 (semilla 202) y 22/24 (semilla 7).

## Pendiente que no depende del código

- **OpenRouter sin saldo** (402 desde la noche del 19 al 20): el planificador corre en Gemini (~0,6 s por paso) en vez
  de Groq (~0,35 s). Recargar devuelve ~250 ms por turno.
- **ElevenLabs:** 178.000 de 300.000 caracteres gastados. Una llamada sintetiza ~1.600; una tirada de voz de 12, ~4.600.
