# Modos de fallo

Generado por `agent/modos.py` el 20-09-2026 08:27 sobre **N = 528 llamadas** (cerebro v2) de `/tmp/nascalls`; fecha de los informes: 19-09-2026 21:36 → 20-09-2026 08:04. Descartados: 622 otro cerebro (v1), 121 no son informes de llamada.

Un evento de guardia **no es una llamada fallida**: es un defecto del modelo, del oído o de la conversación que el código vio y paró. La tasa es de llamadas afectadas por cada 100, con su intervalo de Wilson al 95 %. Lo que esta tabla no ve son los fallos que nadie caza: para eso están los arneses (`docs/rigor.md`).

Las llamadas son las de todo el desarrollo (arnés de voz de Prosper, rondas de práctica y pruebas propias), con versiones distintas del agente: las tasas describen el periodo, no la versión de hoy. Use `--desde` para acotar.

## Taxonomía: qué falla, qué lo para y cada cuánto

| familia | evento | modo de fallo | guardia | llamadas | por 100 (IC 95 %) | eventos | ejemplo |
|---|---|---|---|---|---|---|---|
| modelo inventa | `name_placeholder` | Identifica con un nombre de relleno («the patient», «caller», «unknown») en vez de preguntarlo. | identify_patient lo rechaza y pide nombre y apellidos. | 72/528 | 13,6 (11,0–16,8) | 72 | `071d92ca-292f-59e3-a2f8-800f06fb1a40` |
| modelo inventa | `identifier_invented` | El planificador pasa a identify_patient un DNI, teléfono o fecha de nacimiento que quien llama no ha dicho. | Solo valen las cifras que aparecen en lo dicho por quien llama; el dato se descarta y se busca sin él. | 55/528 | 10,4 (8,1–13,3) | 89 | `07c4e8f8-a674-54b9-be12-c90395d6b974` |
| modelo inventa | `dob_invented` | Al dar de alta, fecha de nacimiento que quien llama no ha dicho. | register_patient la rechaza y manda pedirla. | 12/528 | 2,3 (1,3–3,9) | 13 | `630f9419-27d0-50b8-b791-304c81976228` |
| modelo inventa | `address_invented` | Al dar de alta, dirección inventada. | register_patient la descarta. | 11/528 | 2,1 (1,2–3,7) | 12 | `26371b8e-ac70-5b70-8590-097625181bb2` |
| modelo inventa | `guard` | La respuesta repite en voz alta un dato protegido (fecha, DNI, teléfono) que quien llama no ha dicho. | Se tachan de la frase las cifras que no vienen de quien llama. | 8/528 | 1,5 (0,8–3,0) | 17 | `CA0cd340ec51e1416db892a93f24d49b8f` |
| modelo inventa | `specialty_invented` | Busca huecos de una especialidad que nadie ha pedido (la deduce de un síntoma o al azar). | find_slots se niega y manda preguntar para qué es la cita. | 6/528 | 1,1 (0,5–2,5) | 6 | `71432536-cd4d-5581-88ca-bb5a9731c742` |
| modelo inventa | `truth_guard` | La respuesta afirma algo que las herramientas no han hecho o dicho: «queda reservada» sin escritura, o una hora que no sale de ningún hueco. | Guardia de verdad: Jev juzga si la frase afirma una escritura y el código coteja las horas dichas con las de las herramientas; la frase se sustituye. | 5/528 | 0,9 (0,4–2,2) | 8 | `630f9419-27d0-50b8-b791-304c81976228` |
| modelo inventa | `nid_invented` | Al dar de alta, el planificador rellena un DNI/NIE que nadie ha dictado. | register_patient lo rechaza y manda pedirlo dígito a dígito. | 4/528 | 0,8 (0,3–1,9) | 9 | `8eede5f8-e240-55a6-837d-73f66857ce99` |
| modelo inventa | `email_invented` | Al dar de alta, correo electrónico inventado. | register_patient lo descarta. | 3/528 | 0,6 (0,2–1,7) | 8 | `3522f2b3-ffd8-53ad-90a1-6f7c4a40147a` |
| modelo inventa | `phone_invented` | Al dar de alta, teléfono inventado (o los dígitos del DNI puestos como teléfono). | register_patient lo rechaza y manda pedir el teléfono. | 3/528 | 0,6 (0,2–1,7) | 3 | `5815a22c-ec71-5a27-b76a-7c578f3f56db` |
| modelo inventa | `name_is_doctor` | Toma el nombre del médico («con la doctora Ortiz») por el del paciente. | identify_patient compara con el cuadro médico y pide el nombre del paciente. | 2/528 | 0,4 (0,1–1,4) | 2 | `17d6593a-5e21-5d42-bc49-88337fc8dfb7` |
| modelo restringe | `site_corrected` | Busca en una sede distinta de la que pidió quien llama (nombre mal oído o mal copiado). | La sede la fija Jev emparejando por sonido; find_slots impone esa. | 17/528 | 3,2 (2,0–5,1) | 25 | `08f8e670-5caf-59ff-9080-43d400c5d344` |
| modelo restringe | `site_ignored` | Filtra por una sede que nadie ha pedido y esconde huecos más tempranos en las otras. | find_slots quita el filtro de sede. | 6/528 | 1,1 (0,5–2,5) | 19 | `CA0a0ba5e6aecc47bcaa05879b3c8f4d82` |
| modelo restringe | `insurers_ignored` | Añade a la búsqueda seguros que quien llama no ha nombrado. | find_slots solo conserva los seguros nombrados. | 6/528 | 1,1 (0,5–2,5) | 6 | `CA72f9055e29d84060952c70fc61371b8d` |
| modelo restringe | `provider_ignored` | Elige un médico por su cuenta cuando solo se pidió la especialidad. | find_slots quita el filtro de médico. | 3/528 | 0,6 (0,2–1,7) | 4 | `CAa6af68e002054596b876aaa37ed53a6e` |
| modelo restringe | `constraints_ignored` | Inventa cuándo (fechas, franja, días) sin que quien llama haya dicho nada temporal. | find_slots busca lo primero disponible. | 2/528 | 0,4 (0,1–1,4) | 2 | `07c4e8f8-a674-54b9-be12-c90395d6b974` |
| motivo de la negativa | `must_check_first` | Va a negar (o a cerrar) sin haber mirado la agenda del paciente identificado. | Se bloquea una vez y se obliga a consultar antes. | 16/528 | 3,0 (1,9–4,9) | 16 | `03ce23a4-3473-5f0c-b9d7-067aa8fe495b` |
| motivo de la negativa | `decline_with_offer` | Da la llamada por denegada con una oferta todavía abierta sobre la mesa. | La negativa no se da por buena mientras haya oferta abierta. | 10/528 | 1,9 (1,0–3,5) | 10 | `3004265c-1ddb-5787-9167-71cd2fc0d6b2` |
| motivo de la negativa | `decline_coerced` | Declara un motivo de negativa que ninguna regla de la API ha dado en la llamada. | Se sustituye por el motivo que sí se ha visto en las herramientas. | 3/528 | 0,6 (0,2–1,7) | 3 | `CAa5ad3d6d8aa2439dbfa79b72b8c5681e` |
| motivo de la negativa | `decline_oos` | Pone una regla de cobertura como motivo cuando lo pedido estaba fuera de lo que se atiende por teléfono. | Se declara out_of_scope. | 1/528 | 0,2 (0,0–1,1) | 1 | `67e7ea45-e60d-5bef-b9e5-7fc5b292f712` |
| conversación | `offer_rejected` | Quien llama rechaza el hueco leído. | La oferta se retira y no se vuelve a proponer ese hueco. | 37/528 | 7,0 (5,1–9,5) | 59 | `0713461b-138a-52e4-a84c-997513f8a4df` |
| conversación | `otra_forma` | Se ha pedido dos veces el mismo dato sin conseguirlo. | La segunda vez se pide de otra forma (dígito a dígito, deletreado). | 20/528 | 3,8 (2,5–5,8) | 21 | `11b94171-be04-5e3c-b779-084c282b8d2e` |
| conversación | `dejar_de_pedir` | Tres peticiones del mismo dato sin éxito: la llamada se está atascando. | Se deja de pedir y se sigue por otra vía. | 10/528 | 1,9 (1,0–3,5) | 12 | `11b94171-be04-5e3c-b779-084c282b8d2e` |
| conversación | `leave_alternative` | El médico pedido está de baja y hay hueco antes con otro. | find_slots añade la alternativa. | 6/528 | 1,1 (0,5–2,5) | 6 | `CA2eeac782f2ac4d949ad41ee918b69457` |
| conversación | `commit_on_hangup` | Quien llama cuelga justo después de aceptar, antes de que la puerta escriba. | Al colgar se declara lo aceptado. | 1/528 | 0,2 (0,0–1,1) | 1 | `CA886bca217fe84ffd902fa64be8c8f2a0` |
| oído y turnos | `site_heard` | El transcriptor destroza un nombre de sede; se recupera por sonido. | Jev empareja por sonido contra las sedes reales. | 94/528 | 17,8 (14,8–21,3) | 96 | `0713461b-138a-52e4-a84c-997513f8a4df` |
| oído y turnos | `undo` | Se contestó a un turno que no había terminado: la persona siguió hablando. | Deshacer: se restaura el estado anterior y se vuelve a planificar con la frase entera. | 43/528 | 8,1 (6,1–10,8) | 46 | `5632aedd-bf69-56df-bc7c-e15bc6abe9df` |
| oído y turnos | `barge_in` | Quien llama habla encima del agente. | Jev decide si es una interrupción de verdad; si lo es, el agente calla. | 17/528 | 3,2 (2,0–5,1) | 18 | `3fbd6dd6-ebe0-5848-a1ce-4e0c049f1719` |
| infraestructura | `planner_error` | El planificador falla también al reintentar. | El turno se deshace y el agente pide que se lo repitan. | 18/528 | 3,4 (2,2–5,3) | 25 | `07c4e8f8-a674-54b9-be12-c90395d6b974` |
| infraestructura | `planner_retry` | Un paso del planificador falla o agota el tiempo. | Un reintento con más margen. | 1/528 | 0,2 (0,0–1,1) | 1 | `CAbf1d3e5a2fde4e0fa5c284a0cd060375` |
| derivado | `tool_error` | Una herramienta devuelve error al planificador (argumentos imposibles, paciente sin identificar, id desconocido). | El error vuelve al planificador como texto con la instrucción de qué hacer; no llega a quien llama. | 207/528 | 39,2 (35,1–43,4) | 318 | `03ce23a4-3473-5f0c-b9d7-067aa8fe495b` |
| derivado | `puerta_cerrada` | Se intenta escribir sin un «sí» claro a lo que se acaba de leer. | La PUERTA: sin lectura previa de eso mismo y sin «sí» de Jev, no se escribe. | 67/528 | 12,7 (10,1–15,8) | 137 | `07c4e8f8-a674-54b9-be12-c90395d6b974` |
| derivado | `mas_de_180_s` | La llamada pasa de los 180 s que admite el marcador. | Ninguna en código: se vigila con esta tabla y con la réplica. | 4/528 | 0,8 (0,3–1,9) | 4 | `5815a22c-ec71-5a27-b76a-7c578f3f56db` |

Llamadas con al menos una guardia sobre el planificador (inventa, restringe o motivo): 185/528 (35,0 %).
Modos catalogados que no aparecen en estas llamadas: `circuit_closed`, `circuit_open`, `colgar_pronto`, `date_from_ignored`, `decline_kept`, `escritura_retirada`, `jev_down`, `specialty_corrected`.

## Rechazos de las herramientas: lo que el planificador intentó y el código no dejó

Desglose de `tool_error`. El mensaje es el que recibe el planificador (en inglés, como su prompt); quien llama no lo oye.

| herramienta | rechazo | llamadas | por 100 | eventos | ejemplo |
|---|---|---|---|---|---|
| find_slots | identify the patient first (identify_patient) | 80/528 | 15,2 | 80 | `0b1e7727-59a4-5d41-be0e-35cb9fbf0b00` |
| confirm_booking | not read back yet: read it back to the caller now and ask for a clear yes; confirm next  | 40/528 | 7,6 | 56 | `07c4e8f8-a674-54b9-be12-c90395d6b974` |
| end_call | the caller has not said goodbye: ask if there is anything else instead of ending | 29/528 | 5,5 | 31 | `03ce23a4-3473-5f0c-b9d7-067aa8fe495b` |
| confirm_booking | the caller has not clearly chosen this option: ask them | 21/528 | 4,0 | 43 | `07c4e8f8-a674-54b9-be12-c90395d6b974` |
| nearest_site | the caller has not given an address and has not asked which site is nearest: do NOT use  | 11/528 | 2,1 | 12 | `26371b8e-ac70-5b70-8590-097625181bb2` |
| decline | you have an appointment on the table for an identified patient and the caller has not as | 10/528 | 1,9 | 10 | `3004265c-1ddb-5787-9167-71cd2fc0d6b2` |
| clinic_info | missing specialty / provider_id / location_id / language for that topic | 9/528 | 1,7 | 14 | `5b8f5c1b-da67-5da2-acc5-e5e294bc9350` |
| end_call | you have not looked anyone up in this call, so you cannot know whether that appointment  | 8/528 | 1,5 | 8 | `CA1252d22f77b846b1b5695339b035c926` |
| find_slots | the caller has not said what the appointment is for: ask them briefly what they need (wh | 6/528 | 1,1 | 6 | `71432536-cd4d-5581-88ca-bb5a9731c742` |
| decline | you have not looked anyone up in this call, so you cannot know whether that appointment  | 4/528 | 0,8 | 4 | `11b94171-be04-5e3c-b779-084c282b8d2e` |
| prepare_registration | the caller has not given that DNI/NIE: ask them to say it again, digit by digit with the | 4/528 | 0,8 | 9 | `8eede5f8-e240-55a6-837d-73f66857ce99` |
| list_appointments | identify the patient first | 4/528 | 0,8 | 4 | `CA3d4598fdb21d495e9f1dd3fd189132f5` |
| find_slots | need the specialty (or a doctor) | 3/528 | 0,6 | 3 | `3004265c-1ddb-5787-9167-71cd2fc0d6b2` |
| prepare_registration | the caller has not given that email: ask them for it, spelling the part after the at sig | 3/528 | 0,6 | 8 | `3522f2b3-ffd8-53ad-90a1-6f7c4a40147a` |
| confirm_registration | the caller has not clearly said yes to the readback: ask them | 3/528 | 0,6 | 3 | `3522f2b3-ffd8-53ad-90a1-6f7c4a40147a` |

… y otros 14 rechazos distintos, con 27 eventos en total.

## Cómo acaban las llamadas

**Resultado declarado (outcome):** BOOK 283 (53,6 %) · NO_ACTION 192 (36,4 %) · RESCHEDULE 13 (2,5 %) · REGISTER 13 (2,5 %) · CANCEL 10 (1,9 %) · ESCALATE 8 (1,5 %) · CANCEL, CANCEL 6 (1,1 %) · BOOK, BOOK 2 (0,4 %) · BOOK, RESCHEDULE 1 (0,2 %)

**Motivo (reason):** (sin dato) 409 (77,5 %) · out_of_scope 38 (7,2 %) · no_availability 27 (5,1 %) · specialty_not_covered 13 (2,5 %) · allowance_exhausted 8 (1,5 %) · patient_not_found 7 (1,3 %) · referral_required 7 (1,3 %) · location_not_covered 6 (1,1 %) · not_eligible_age 5 (0,9 %) · provider_not_found 5 (0,9 %) · provider_not_in_network 2 (0,4 %) · clinic_closed 1 (0,2 %)

**Quién cierra (ended_by):** goodbye 318 (60,2 %) · stop 197 (37,3 %) · disconnect 13 (2,5 %)

**Idioma:** en 499 (94,5 %) · es 16 (3,0 %) · ca 13 (2,5 %)


## Duración y latencia por paso

Duración de la llamada (n=528): mediana 54,5 s · p90 108,0 s · p99 171,9 s · máximo 211,6 s.

| paso | n | mediana (ms) | p90 (ms) | p99 (ms) |
|---|---|---|---|---|
| Jev (percepción) | 2406 | 350 | 708 | 819 |
| planificador (paso que redacta) | 1267 | 585 | 741 | 1233 |
| herramienta (incluye la API de la clínica) | 1426 | 576 | 719 | 1188 |
| planificador (paso con herramientas, redactado por el código) | 536 | 602 | 740 | 1089 |

Herramientas más usadas: find_slots 451 · confirm_booking 401 · identify_patient 308 · end_call 147 · clinic_info 125 · decline 107 · prepare_registration 47 · nearest_site 43.

## Especulación

Turnos (eventos de percepción): 2406 · respondidos con la especulación ya hecha: 1534 (63,8 %, IC 95 % 61,8–65,7).
Ventaja al reutilizarla (head_start_ms, n=1534): mediana 676 ms · p90 1899 ms.
Por qué se dio por buena: idéntico 1296 · solo cortesía de más 129 · Jev: no pide nada nuevo 108 · (sin dato) 1.

## Lo que gasta una llamada, contado en las trazas

| por llamada | mediana | p90 | media |
|---|---|---|---|
| juicios de Jev registrados (eventos perception) | 4 | 7 | 4,6 |
| pasos del planificador registrados (planner + herramientas pedidas por él) | 4 | 10 | 5,1 |
| llamadas a herramientas | 3 | 6 | 3,2 |
| caracteres dichos por el agente (transcripción) | 432 | 713 | 453,3 |

**Estimación en USD** (no es una medida: recuentos de la traza × supuestos de tokens × precios de lista de `agent/coste.py`). Supuestos: 1800 tokens por juicio de Jev, 4700 de entrada y 60 de salida por paso del planificador (gpt-oss-120b en Groq), 2 sesiones de oído durante toda la llamada.

|  | Jev | planificador | voz (cota superior, sin caché) | oído | total |
|---|---|---|---|---|---|
| llamada media | 0,00034 | 0,00378 | 0,04533 | 0,01056 | 0,06002 |
| llamada p90 (cada recuento en su p90) | 0,00053 | 0,00741 | 0,07129 | 0,01800 | 0,09723 |

Informes con bloque `cost` medido: 0/528. En los demás solo hay recuentos de la traza, que son una COTA INFERIOR: los juicios de Jev sobre parciales y los pasos de especulaciones descartadas no dejan evento. La fórmula y la estimación en dinero, en docs/rigor.md.

